"""Local Bambu Studio profile manager. No credentials are parsed or sent anywhere."""
from __future__ import annotations

import contextlib
import csv
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
import zipfile
import zlib

VERSION = "1.2.0"
HIDDEN = 0x08000000 if os.name == "nt" else 0


class SwitchError(RuntimeError):
    pass


def atomic_json(path: Path, data):
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def studio_pids():
    if os.name != "nt":
        return []
    result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq bambu-studio.exe", "/FO", "CSV", "/NH"],
                            capture_output=True, creationflags=HIDDEN, check=True)
    rows = csv.reader(io.StringIO(result.stdout.decode("mbcs", errors="replace")))
    return [int(row[1]) for row in rows if len(row) > 1 and row[0].lower() == "bambu-studio.exe"]


def close_studio():
    """Ask Studio to close normally; never terminate it or bypass save dialogs."""
    pids = set(studio_pids())
    if not pids:
        return
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]

    @callback_type
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        return True

    user32.EnumWindows(visit, 0)
    for _ in range(30):
        if not studio_pids():
            return
        time.sleep(0.5)
    raise SwitchError("Bambu Studio 仍在运行。请处理保存提示并退出，然后重试；配置尚未切换。")


def find_studio():
    candidates = []
    if os.name == "nt":
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for key in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",):
                try:
                    with winreg.OpenKey(hive, key) as parent:
                        for index in range(winreg.QueryInfoKey(parent)[0]):
                            try:
                                with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as entry:
                                    name = winreg.QueryValueEx(entry, "DisplayName")[0]
                                    if "bambu studio" in name.lower() and "beta" not in name.lower():
                                        candidates.append(Path(winreg.QueryValueEx(entry, "InstallLocation")[0]) / "bambu-studio.exe")
                            except OSError:
                                pass
                except OSError:
                    pass
    candidates += [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Bambu Studio/bambu-studio.exe",
                   Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Bambu Studio/bambu-studio.exe"]
    return next((str(p) for p in candidates if p.is_file()), "")


class Manager:
    TRANSFER_FORMAT = "bambu-account"
    TRANSFER_MAX_FILES = 10000
    TRANSFER_MAX_BYTES = 512 * 1024 * 1024
    TRANSFER_MAX_FILE_BYTES = 128 * 1024 * 1024
    TRANSFER_MAX_MANIFEST_BYTES = 4 * 1024 * 1024

    def __init__(self, parent=None, running=studio_pids):
        self.parent = Path(parent or os.environ["APPDATA"]).absolute()
        self.active = self.parent / "BambuStudio"
        self.root = self.parent / "BambuStudioSwitch"
        self.profiles = self.root / "profiles"
        self.state_file = self.root / "state.json"
        self.journal = self.root / "pending.json"
        self.running = running

    @contextlib.contextmanager
    def locked(self):
        self.regular(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.regular(self.profiles)
        with (self.root / ".lock").open("a+b") as lock:
            try:
                import msvcrt
                if os.fstat(lock.fileno()).st_size == 0:
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise SwitchError("另一个切换器正在操作，请稍后重试。") from exc
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)

    def idle(self):
        if self.running():
            raise SwitchError("请先保存并退出 Bambu Studio，再进行配置操作。")

    @staticmethod
    def regular(path):
        if path.is_symlink() or path.is_junction():
            raise SwitchError(f"检测到目录链接，请先还原其他配置切换工具：{path}")
        if path.exists() and not path.is_dir():
            raise SwitchError(f"预期为目录，但发现文件：{path}")

    def state(self):
        if not self.state_file.exists():
            return None
        try:
            data = read_json(self.state_file)
            if not isinstance(data, dict):
                raise ValueError()
            if data.get("version") != 1 or not isinstance(data.get("profiles"), dict):
                raise ValueError()
            if data["active"] not in data["profiles"]:
                raise ValueError()
            for key, name in data["profiles"].items():
                if not re.fullmatch(r"[0-9a-f]{32}", key) or not isinstance(name, str):
                    raise ValueError()
            return data
        except (ValueError, KeyError, TypeError) as exc:
            raise SwitchError("配置清单损坏或版本不兼容。数据目录已保留，请勿手动覆盖。") from exc

    def validate(self, state):
        self.regular(self.active)
        if not self.active.is_dir():
            raise SwitchError("当前配置目录丢失，已停止操作。请检查备份或中断恢复。")
        for key in state["profiles"]:
            path = self.profiles / key
            self.regular(path)
            if key == state["active"]:
                if path.exists():
                    raise SwitchError("当前配置出现重复目录，已停止操作，所有数据均保留。")
            elif not path.is_dir():
                raise SwitchError("某个账号配置目录丢失，已停止操作，所有数据均保留。")

    def recover(self):
        if not self.journal.exists():
            return
        self.idle()
        state = self.state()
        try:
            pending = read_json(self.journal)
            old, new = pending["from"], pending["to"]
        except (ValueError, KeyError, TypeError) as exc:
            # Keep the journal: it is the only record of which move was interrupted.
            raise SwitchError("中断记录损坏，已停止操作；数据目录已保留，请检查 pending.json。") from exc
        if (not state or not isinstance(old, str) or not isinstance(new, str) or old == new
                or old not in state["profiles"] or new not in state["profiles"]):
            raise SwitchError("中断记录异常，已停止操作；数据目录已保留。")
        source, target = self.profiles / old, self.profiles / new
        for path in (self.active, source, target):
            self.regular(path)
        layout = (self.active.exists(), source.exists(), target.exists())
        if layout == (False, True, True):
            # The old directory was parked, but the new one was not activated. Roll back.
            source.rename(self.active)
            state["active"] = old
        elif layout == (True, True, False):
            state["active"] = new
        elif layout == (True, False, True):
            state["active"] = old
        else:
            raise SwitchError("中断后的目录状态不明确，已停止操作；数据均保留，请检查数据目录。")
        self.validate(state)
        atomic_json(self.state_file, state)
        self.journal.unlink()

    def status(self):
        with self.locked():
            self.recover()
            state = self.state()
            if state:
                self.validate(state)
            return state

    def name(self, value, state=None):
        value = value.strip()
        if not value or len(value) > 32 or re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value.endswith("."):
            raise SwitchError("请输入 1–32 字的名称，不含路径符号或文件名特殊字符。")
        if state and value.casefold() in [n.casefold() for n in state["profiles"].values()]:
            raise SwitchError("这个名称已经存在，请换一个名称。")
        return value

    def initialize(self, name):
        with self.locked():
            self.idle()
            self.recover()
            if self.state_file.exists():
                raise SwitchError("已经初始化，无需重复操作。")
            name = self.name(name)
            self.regular(self.active)
            self.profiles.mkdir(exist_ok=True)
            if self.active.exists():
                self.regular(self.root / "backups")
                # Refuse nested reparse points; copytree must not follow links into other data.
                for base, dirs, files in os.walk(self.active):
                    for item in dirs + files:
                        path = Path(base) / item
                        if path.is_symlink() or path.is_junction():
                            raise SwitchError("配置内含链接，无法安全备份。请检查后重试。")
                backup = self.root / "backups" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
                shutil.copytree(self.active, backup)
            else:
                self.active.mkdir(parents=True)
            key = uuid.uuid4().hex
            atomic_json(self.state_file, {"version": 1, "active": key, "profiles": {key: name}, "studio": find_studio()})
            return key

    def add(self, name):
        with self.locked():
            self.recover()
            state = self.state()
            if not state:
                raise SwitchError("请先保存当前账号。")
            self.validate(state)
            name = self.name(name, state)
            key = uuid.uuid4().hex
            (self.profiles / key).mkdir()
            state["profiles"][key] = name
            atomic_json(self.state_file, state)
            return key

    def switch(self, key, launch=False, close=False):
        with self.locked():
            state = self.state()
            if not state or key not in state["profiles"]:
                raise SwitchError("找不到所选账号配置，请刷新后重试。")
            executable = state.get("studio") or find_studio()
            if launch and not Path(executable).is_file():
                raise SwitchError("找不到 Bambu Studio，请先在界面中选择程序位置。")
            if close:
                close_studio()
            self.idle()
            self.recover()
            state = self.state()
            if not state or key not in state["profiles"]:
                raise SwitchError("找不到所选账号配置，请刷新后重试。")
            self.validate(state)
            self._activate(state, key)
            if launch:
                self._launch(executable)

    def _activate(self, state, key):
        """Caller holds lock and has verified that Studio has exited."""
        if key == state["active"]:
            return
        old = state["active"]
        atomic_json(self.journal, {"from": old, "to": key})
        try:
            self.active.rename(self.profiles / old)
            (self.profiles / key).rename(self.active)
            state["active"] = key
            self.validate(state)
            atomic_json(self.state_file, state)
            self.journal.unlink()
        except OSError as exc:
            self.recover()
            raise SwitchError("切换未完成，已恢复到一致状态。请检查目录占用或磁盘空间后重试。") from exc

    @staticmethod
    def _launch(executable):
        try:
            subprocess.Popen([executable], cwd=str(Path(executable).parent))
        except OSError as exc:
            raise SwitchError("账号配置已保留，但 Studio 启动失败。请检查程序位置后重试。") from exc

    def set_studio(self, executable):
        path = Path(executable).absolute()
        if path.name.lower() != "bambu-studio.exe" or not path.is_file():
            raise SwitchError("请选择正式版 Bambu Studio 的 bambu-studio.exe。")
        with self.locked():
            self.recover()
            state = self.state()
            if not state:
                raise SwitchError("请先保存当前账号。")
            state["studio"] = str(path)
            atomic_json(self.state_file, state)

    def detach(self):
        with self.locked():
            self.idle()
            self.recover()
            state = self.state()
            if state:
                self.validate(state)
            marker = self.root / "auto-import-disabled"
            created_marker = False
            try:
                if not marker.exists():
                    atomic_json(marker, {"version": 1})
                    created_marker = True
                if state:
                    destination = self.root / ("state-detached-" + uuid.uuid4().hex + ".json")
                    self.state_file.rename(destination)
            except OSError as exc:
                if created_marker:
                    marker.unlink()
                raise SwitchError("恢复普通使用未完成，账号管理仍保留。请检查数据目录权限后重试。") from exc
            return "当前账号仍保留在 BambuStudio，其他账号和备份保留在数据目录。"

    @staticmethod
    def _transfer_path(value):
        """Validate portable relative names before touching the filesystem."""
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise SwitchError("账号文件包含无效路径。")
        if "\\" in value or value.startswith("/"):
            raise SwitchError("账号文件包含不安全路径。")
        parts = value.split("/")
        for part in parts:
            stem = part.split(".", 1)[0].rstrip(" .").upper()
            reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
            reserved.update(prefix + number for prefix in ("COM", "LPT") for number in "123456789¹²³")
            if (not part or part in (".", "..") or len(part) > 255 or part.endswith((".", " "))
                    or re.search(r'[<>:"\\|?*\x00-\x1f\x7f]', part) or stem in reserved):
                raise SwitchError("账号文件包含不安全或不兼容的文件名。")
        return parts

    @staticmethod
    def _transfer_excluded(name, directory=False):
        lowered = name.casefold()
        if directory:
            return lowered in {"log", "logs", "cache", "caches", "temp", "tmp", "__pycache__"}
        return lowered.endswith((".log", ".tmp", ".temp", ".lock")) or lowered in {".ds_store", "thumbs.db"}

    def _transfer_entries(self, source):
        directories, files, seen = [], [], {}
        total = 0
        def walk_error(error):
            raise error
        for base, dirs, names in os.walk(source, followlinks=False, onerror=walk_error):
            dirs[:] = sorted(d for d in dirs if not self._transfer_excluded(d, directory=True))
            for filename in dirs + sorted(names):
                path = Path(base) / filename
                is_directory = filename in dirs
                if not is_directory and self._transfer_excluded(filename):
                    continue
                if path.is_symlink() or path.is_junction():
                    raise SwitchError("配置内含链接，无法安全导出。")
                relative = path.relative_to(source).as_posix()
                self._transfer_path(relative)
                canonical = relative.casefold()
                if canonical in seen:
                    raise SwitchError("配置内含大小写冲突的路径，无法导出。")
                seen[canonical] = is_directory
                if len(seen) > self.TRANSFER_MAX_FILES:
                    raise SwitchError("配置文件数量过多，无法导出。")
                if is_directory:
                    directories.append(relative)
                else:
                    details = path.stat()
                    if not stat.S_ISREG(details.st_mode):
                        raise SwitchError("配置内含非普通文件，无法导出。")
                    total += details.st_size
                    if details.st_size > self.TRANSFER_MAX_FILE_BYTES or total > self.TRANSFER_MAX_BYTES:
                        raise SwitchError("账号配置过大，无法导出。")
                    files.append((relative, path, details.st_size))
        return directories, files

    def export_profile(self, key, destination):
        """Write one credential-bearing profile atomically; Studio must be closed."""
        destination = Path(destination).absolute()
        with self.locked():
            self.idle()
            self.recover()
            state = self.state()
            if not state or key not in state["profiles"]:
                raise SwitchError("找不到所选账号配置，请刷新后重试。")
            self.validate(state)
            source = self.active if key == state["active"] else self.profiles / key
            resolved = destination.resolve()
            if any(resolved.is_relative_to(path.resolve()) for path in (self.root, self.active, source)):
                raise SwitchError("请将账号文件保存在配置目录和软件数据目录之外。")
            if destination.is_symlink() or destination.is_junction() or destination.is_dir():
                raise SwitchError("导出目标不能是目录或链接。")
            temporary = destination.with_name("." + destination.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                directories, files = self._transfer_entries(source)
                records = []
                with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                    archive.writestr("profile/", b"")
                    for relative in directories:
                        archive.writestr("profile/" + relative + "/", b"")
                    for relative, path, expected_size in files:
                        digest, size = hashlib.sha256(), 0
                        with path.open("rb") as incoming, archive.open("profile/" + relative, "w") as outgoing:
                            while chunk := incoming.read(1024 * 1024):
                                size += len(chunk)
                                if size > expected_size:
                                    raise SwitchError("配置在导出时发生变化，请退出 Studio 后重试。")
                                digest.update(chunk)
                                outgoing.write(chunk)
                        if size != expected_size:
                            raise SwitchError("配置在导出时发生变化，请重试。")
                        records.append({"path": relative, "size": size, "sha256": digest.hexdigest()})
                    manifest = {"format": self.TRANSFER_FORMAT, "version": 1,
                                "name": state["profiles"][key], "files": records}
                    encoded = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
                    if len(encoded) > self.TRANSFER_MAX_MANIFEST_BYTES:
                        raise SwitchError("账号配置清单过大，无法导出。")
                    archive.writestr("manifest.json", encoded)
                with temporary.open("r+b") as stream:
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, destination)
                return destination
            except SwitchError:
                raise
            except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
                raise SwitchError("账号导出失败，请检查目标目录权限和可用空间。") from exc
            finally:
                if temporary.exists():
                    temporary.unlink()

    def _inspect_transfer(self, archive):
        infos = archive.infolist()
        if len(infos) > self.TRANSFER_MAX_FILES + 2:
            raise SwitchError("账号文件包含过多文件。")
        paths, implicit, payload, spellings = {}, set(), {}, {}
        manifest_info = None
        total = 0
        for info in infos:
            raw = info.filename
            # zipfile truncates names containing a NUL; inspect the original as well.
            if info.orig_filename != raw:
                raise SwitchError("账号文件包含不安全路径。")
            directory = info.is_dir()
            value = raw[:-1] if directory else raw
            parts = self._transfer_path(value)
            canonical = value.casefold()
            for index in range(1, len(parts) + 1):
                spelling = "/".join(parts[:index])
                folded = spelling.casefold()
                if folded in spellings and spellings[folded] != spelling:
                    raise SwitchError("账号文件包含大小写冲突的路径。")
                spellings[folded] = spelling
            mode = (info.external_attr >> 16) & 0xffff
            kind = stat.S_IFMT(mode)
            if (kind not in (0, stat.S_IFREG, stat.S_IFDIR) or (kind == stat.S_IFDIR) != directory and kind != 0
                    or info.external_attr & 0x400 or info.flag_bits & 1):
                raise SwitchError("账号文件包含链接、特殊文件或加密条目。")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise SwitchError("账号文件使用了不支持的压缩方式。")
            if canonical in paths or (canonical in implicit and not directory):
                raise SwitchError("账号文件包含重复路径或文件与目录冲突。")
            for index in range(1, len(parts)):
                parent = "/".join(parts[:index]).casefold()
                if parent in paths and not paths[parent]:
                    raise SwitchError("账号文件包含文件与目录冲突。")
                implicit.add(parent)
            paths[canonical] = directory
            if directory:
                if parts[0] != "profile" or info.file_size != 0:
                    raise SwitchError("账号文件目录结构不正确。")
                continue
            if raw == "manifest.json":
                manifest_info = info
                if info.file_size > self.TRANSFER_MAX_MANIFEST_BYTES:
                    raise SwitchError("账号文件清单过大。")
                continue
            if parts[0] != "profile" or len(parts) < 2:
                raise SwitchError("账号文件目录结构不正确。")
            if info.file_size > self.TRANSFER_MAX_FILE_BYTES:
                raise SwitchError("账号文件中的单个配置过大。")
            total += info.file_size
            if total > self.TRANSFER_MAX_BYTES:
                raise SwitchError("账号文件解压后过大。")
            payload["/".join(parts[1:])] = info
        if manifest_info is None:
            raise SwitchError("这不是有效的 Bambu 账号文件。")
        manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
        if (not isinstance(manifest, dict) or set(manifest) != {"format", "version", "name", "files"}
                or manifest["format"] != self.TRANSFER_FORMAT or type(manifest["version"]) is not int
                or manifest["version"] != 1 or not isinstance(manifest["name"], str)
                or not isinstance(manifest["files"], list)):
            raise SwitchError("账号文件格式不正确或版本不兼容。")
        self.name(manifest["name"])
        if len(manifest["files"]) > self.TRANSFER_MAX_FILES:
            raise SwitchError("账号文件校验清单包含过多文件。")
        records = {}
        for record in manifest["files"]:
            if (not isinstance(record, dict) or set(record) != {"path", "size", "sha256"}
                    or not isinstance(record["path"], str) or type(record["size"]) is not int
                    or record["size"] < 0 or not isinstance(record["sha256"], str)
                    or not re.fullmatch("[0-9a-f]{64}", record["sha256"])):
                raise SwitchError("账号文件校验清单不正确。")
            self._transfer_path(record["path"])
            if record["path"] in records:
                raise SwitchError("账号文件校验清单包含重复路径。")
            records[record["path"]] = record
        if set(records) != set(payload):
            raise SwitchError("账号文件内容与校验清单不一致。")
        for relative, info in payload.items():
            record = records[relative]
            if record["size"] != info.file_size:
                raise SwitchError("账号文件已损坏，文件大小校验失败。")
            digest, size = hashlib.sha256(), 0
            with archive.open(info) as stream:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > record["size"]:
                        raise SwitchError("账号文件解压后超出声明大小。")
                    digest.update(chunk)
            if size != record["size"] or digest.hexdigest() != record["sha256"]:
                raise SwitchError("账号文件已损坏，内容校验失败。")
        return manifest, payload, [p for p in infos if p.is_dir()]

    def inspect_profile(self, source):
        """Read and verify an account file without changing any local profile."""
        try:
            with self._open_transfer(source) as archive:
                manifest, payload, _ = self._inspect_transfer(archive)
                return {"name": manifest["name"], "format_version": 1, "files": len(payload),
                        "bytes": sum(info.file_size for info in payload.values())}
        except SwitchError:
            raise
        except (OSError, ValueError, TypeError, KeyError, RuntimeError, NotImplementedError,
                EOFError, zipfile.BadZipFile, UnicodeError, zlib.error) as exc:
            raise SwitchError("无法读取账号文件：文件已损坏或格式不受支持。") from exc

    @contextlib.contextmanager
    def _open_transfer(self, source):
        with Path(source).open("rb") as stream:
            if os.fstat(stream.fileno()).st_size > self.TRANSFER_MAX_BYTES + 16 * 1024 * 1024:
                raise SwitchError("账号文件过大，无法导入。")
            with zipfile.ZipFile(stream, "r") as archive:
                yield archive

    def _available_transfer_name(self, name, state):
        name = self.name(name)
        existing = {value.casefold() for value in state["profiles"].values()}
        candidate, index = name, 2
        while candidate.casefold() in existing:
            suffix = f" ({index})"
            candidate = name[:32 - len(suffix)] + suffix
            index += 1
        return candidate

    def import_profile(self, source, name=None):
        """Add a verified profile; only a machine with no local config activates it."""
        with self.locked():
            self.idle()
            self.recover()
            state = self.state()
            if state:
                self.validate(state)
            staging = self.profiles / (".import-" + uuid.uuid4().hex)
            final = backup = None
            fresh_machine = preserve_local = installed = False
            committed = False
            try:
                with self._open_transfer(source) as archive:
                    manifest, payload, directories = self._inspect_transfer(archive)
                    if not state:
                        self.regular(self.active)
                        preserve_local = self.active.exists()
                        fresh_machine = not preserve_local
                        local_key = uuid.uuid4().hex if preserve_local else None
                        state = {"version": 1, "active": local_key,
                                 "profiles": {local_key: "本机账号"} if preserve_local else {},
                                 "studio": find_studio()}
                    chosen = (self.name(name, state) if name is not None
                              else self._available_transfer_name(manifest["name"], state))
                    self.profiles.mkdir(exist_ok=True)
                    staging.mkdir()
                    for directory in directories:
                        parts = directory.filename.rstrip("/").split("/")[1:]
                        staging.joinpath(*parts).mkdir(parents=True, exist_ok=True)
                    records = {record["path"]: record for record in manifest["files"]}
                    for relative, info in payload.items():
                        target = staging.joinpath(*relative.split("/"))
                        target.parent.mkdir(parents=True, exist_ok=True)
                        digest, size = hashlib.sha256(), 0
                        with archive.open(info) as incoming, target.open("xb") as outgoing:
                            while chunk := incoming.read(1024 * 1024):
                                size += len(chunk)
                                if size > records[relative]["size"]:
                                    raise SwitchError("账号文件在导入时发生变化，请重新获取文件。")
                                digest.update(chunk)
                                outgoing.write(chunk)
                        if size != records[relative]["size"] or digest.hexdigest() != records[relative]["sha256"]:
                            raise SwitchError("账号文件在导入时发生变化，请重新获取文件。")
                if preserve_local:
                    self.regular(self.root / "backups")
                    for base, dirs, files in os.walk(self.active):
                        for item in dirs + files:
                            path = Path(base) / item
                            if path.is_symlink() or path.is_junction():
                                raise SwitchError("本机配置内含链接，无法安全备份。")
                    backup = self.root / "backups" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
                    shutil.copytree(self.active, backup)
                key = uuid.uuid4().hex
                final = self.active if fresh_machine else self.profiles / key
                staging.rename(final)
                installed = True
                state["profiles"][key] = chosen
                if fresh_machine:
                    state["active"] = key
                self.validate(state)
                atomic_json(self.state_file, state)
                committed = True
                return key
            except SwitchError:
                raise
            except (OSError, ValueError, TypeError, KeyError, RuntimeError, NotImplementedError,
                    EOFError, zipfile.BadZipFile, UnicodeError, zlib.error) as exc:
                raise SwitchError("账号导入失败，原有账号已保留。请检查文件、权限和可用空间。") from exc
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
                if not committed:
                    if installed and final is not None and final.exists():
                        shutil.rmtree(final)
                    if backup is not None and backup.exists():
                        shutil.rmtree(backup)


def desktop_path():
    from ctypes import wintypes
    buffer = ctypes.create_unicode_buffer(260)
    shell = ctypes.WinDLL("shell32")
    shell.SHGetFolderPathW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR]
    if shell.SHGetFolderPathW(None, 0x10, None, 0, buffer) != 0:
        raise SwitchError("无法找到桌面目录。")
    return Path(buffer.value)


def shortcut(key, name, desktop=None):
    desktop = Path(desktop) if desktop else desktop_path()
    target = Path(sys.executable)
    args = subprocess.list2cmdline(["--profile", key])
    if not getattr(sys, "frozen", False):
        pythonw = target.with_name("pythonw.exe")
        target = pythonw if pythonw.exists() else target
        args = subprocess.list2cmdline([str(Path(__file__).with_name("app.py")), "--profile", key])
    path = desktop / f"Bambu - {name}.lnk"
    if path.exists():
        raise SwitchError(f"桌面已存在同名快捷方式，请先重命名或移走：{path.name}")
    from windows_shortcuts import create_shortcut
    icon = str(target) if getattr(sys, "frozen", False) else str(Path(__file__).parent / "assets/BambuStudio.ico")
    try:
        create_shortcut(path, target, args, target.parent, icon)
    except OSError as exc:
        raise SwitchError(f"无法创建桌面入口：{exc}") from exc
    if not path.is_file():
        raise SwitchError("快捷方式未生成，请检查桌面权限。")
    return path
