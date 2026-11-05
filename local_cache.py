"""Discover the current Studio cache without decrypting its account session.

Studio's open source GUI writes the current account's ID to
``app.preset_folder`` and clears it on logout.  The per-user directories only
contain slicer presets: they are not independent saved login sessions.
The networking plugin owns its opaque ``BambuNetworkEngine.conf`` file.
Its presence cannot prove that a server will still accept the saved session.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from switcher import Manager


MAX_CONFIG_BYTES = 8 * 1024 * 1024
DEFAULT_NAME = "本机账号"


@dataclass(frozen=True)
class CacheInfo:
    directory: Path
    available: bool
    suggested_name: str
    reason: str


@dataclass(frozen=True)
class AutoImportResult:
    status: str  # imported, managed, running, unavailable, disabled
    key: str | None
    cache: CacheInfo | None
    message: str


def _linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def inspect_cache(directory: str | Path) -> CacheInfo:
    """Return an advisory result for one known Studio configuration directory.

    No account ID, access code, cookie, or token is returned.  This only reads
    the public app configuration and stats the opaque network cache.  It never
    searches other drives, opens browser storage, or modifies the directory.
    """
    directory = Path(directory).absolute()

    def result(available: bool, reason: str) -> CacheInfo:
        return CacheInfo(directory, available, DEFAULT_NAME, reason)

    try:
        if _linked(directory) or not directory.is_dir():
            return result(False, "没有找到可读取的本机 Bambu Studio 配置。")
        config = directory / "BambuStudio.conf"
        network = directory / "BambuNetworkEngine.conf"
        if _linked(config) or _linked(network):
            return result(False, "本机缓存包含目录链接，无法自动导入。")
        if not config.is_file() or config.stat().st_size > MAX_CONFIG_BYTES:
            return result(False, "本机 Bambu Studio 配置不存在或格式不受支持。")
        # Studio appends an MD5 comment after the JSON document in recent
        # versions.  json.loads() rejects those otherwise valid config files.
        raw = config.read_text(encoding="utf-8-sig")
        data, end = json.JSONDecoder().raw_decode(raw.lstrip())
        tail = raw.lstrip()[end:].strip()
        if tail and not re.fullmatch(r"# MD5 checksum [0-9a-fA-F]{32}", tail):
            return result(False, "本机 Bambu Studio 配置格式不受支持。")
        app = data.get("app", {}) if isinstance(data, dict) else {}
        user_id = app.get("preset_folder", "") if isinstance(app, dict) else ""
        # Never treat historical user/<id> directories as saved sessions.
        # Require the current-account marker as well as the plugin cache.
        if not isinstance(user_id, str) or not user_id or user_id == "default":
            return result(False, "没有发现当前账号的登录缓存；可先在 Bambu Studio 登录。")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", user_id):
            return result(False, "本机账号缓存格式不受支持。")
        users = directory / "user"
        presets = users / user_id
        if _linked(users) or _linked(presets):
            return result(False, "本机缓存包含目录链接，无法自动导入。")
        if not presets.is_dir() or not network.is_file() or not network.stat().st_size:
            return result(False, "本机账号缓存不完整；可先在 Bambu Studio 登录并正常退出。")
        return result(True, "发现当前账号的本地登录缓存，登录是否仍有效由 Bambu Studio 确认。")
    except (OSError, UnicodeError, ValueError, RecursionError):
        return result(False, "暂时无法读取本机缓存；可在 Bambu Studio 正常退出后重试。")


def auto_import_current(manager: Manager, *, automatic: bool = True) -> AutoImportResult:
    """Back up and adopt the current cache once, after Studio has exited.

    Existing managed profiles are left alone.  ``Manager.initialize`` performs
    the usual complete backup and link checks before registering this profile;
    the active Studio directory stays in place.  Other user preset folders are
    deliberately not offered as accounts because they have no separate session.
    Detaching disables future automatic adoption across app restarts.  A manual
    cache import (``automatic=False``) can restore management and clear that flag
    after it succeeds; failed or deferred imports preserve the user's choice.
    """
    disabled = manager.root / "auto-import-disabled"
    if automatic and disabled.exists():
        return AutoImportResult("disabled", None, None,
                                "已停用自动导入；可通过“读取本机缓存”重新管理当前账号。")
    state = manager.state()
    if state and not automatic:
        with manager.locked():
            # Do not clear a new detach marker if another process detached
            # this profile between reading state and taking the lock.
            state = manager.state()
            if state:
                disabled.unlink(missing_ok=True)
    if state:
        return AutoImportResult("managed", state["active"], None, "本机账号已在列表中。")
    cache = inspect_cache(manager.active)
    if not cache.available:
        return AutoImportResult("unavailable", None, cache, cache.reason)
    if manager.running():
        return AutoImportResult("running", None, cache,
                                "已发现本机登录缓存，退出 Bambu Studio 后会自动导入。" if automatic
                                else "已发现本机登录缓存，请退出 Bambu Studio 后再次读取。")
    key = manager.initialize(cache.suggested_name)
    if not automatic:
        with manager.locked():
            if manager.state():
                disabled.unlink(missing_ok=True)
    return AutoImportResult("imported", key, cache,
                            "已自动导入本机账号，并保留首次备份。" if automatic
                            else "已导入本机账号，并保留备份。")
