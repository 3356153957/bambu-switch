"""Local Bambu Studio profile manager. No credentials are parsed or sent anywhere."""
from __future__ import annotations

import contextlib
import csv
import ctypes
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

VERSION = "1.1.1"
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
        pending = read_json(self.journal)
        old, new = pending["from"], pending["to"]
        if not state or old == new or old not in state["profiles"] or new not in state["profiles"]:
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
                destination = self.root / ("state-detached-" + uuid.uuid4().hex + ".json")
                self.state_file.rename(destination)
            return "当前账号仍保留在 BambuStudio，其他账号和备份保留在数据目录。"


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
