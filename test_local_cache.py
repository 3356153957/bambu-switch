import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_cache import auto_import_current, inspect_cache
from switcher import Manager, SwitchError


class LocalCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bambu-local-cache-test-")
        self.addCleanup(self.temp.cleanup)
        self.manager = Manager(self.temp.name, running=lambda: [])
        self.root = self.manager.active
        self.root.mkdir()
        self.write_cache()

    def write_cache(self, user_id="123456789", checksum=False):
        raw = json.dumps({"app": {"preset_folder": user_id}}, ensure_ascii=False)
        if checksum:
            raw += "\n# MD5 checksum " + hashlib.md5(raw.encode()).hexdigest() + "\n"
        (self.root / "BambuStudio.conf").write_text(raw, encoding="utf-8")
        if user_id and user_id != "../escape":
            (self.root / "user" / user_id).mkdir(parents=True, exist_ok=True)
        (self.root / "BambuNetworkEngine.conf").write_bytes(b"\x87opaque-test-session")

    def test_current_cache_detected_without_exposing_identity_or_session(self):
        info = inspect_cache(self.root)
        self.assertTrue(info.available)
        self.assertEqual(info.suggested_name, "本机账号")
        self.assertNotIn("123456789", repr(info))
        self.assertNotIn("opaque", repr(info))
        self.assertIn("Bambu Studio 确认", info.reason)

    def test_current_studio_checksum_trailer_is_supported(self):
        self.write_cache(checksum=True)
        self.assertTrue(inspect_cache(self.root).available)

    def test_historical_preset_folder_is_not_an_account(self):
        self.write_cache(user_id="")
        self.assertTrue((self.root / "user" / "123456789").is_dir())
        self.assertFalse(inspect_cache(self.root).available)

    def test_default_user_is_not_a_logged_in_account(self):
        self.write_cache(user_id="default")
        self.assertFalse(inspect_cache(self.root).available)

    def test_incomplete_session_is_not_importable(self):
        network = self.root / "BambuNetworkEngine.conf"
        network.write_bytes(b"")
        self.assertFalse(inspect_cache(self.root).available)
        network.unlink()
        self.assertFalse(inspect_cache(self.root).available)

    def test_missing_current_user_presets_are_not_importable(self):
        (self.root / "user" / "123456789").rmdir()
        self.assertFalse(inspect_cache(self.root).available)

    def test_invalid_config_does_not_raise_or_import(self):
        for raw in ("{broken", '[]', '{"app": []}',
                    '{"app":{"preset_folder":"123456789"}} {}'):
            with self.subTest(raw=raw):
                (self.root / "BambuStudio.conf").write_text(raw)
                self.assertFalse(inspect_cache(self.root).available)

    def test_user_id_cannot_escape_cache(self):
        self.write_cache(user_id="../escape")
        self.assertFalse(inspect_cache(self.root).available)

    def test_links_are_not_followed(self):
        original = Path.is_symlink
        config = self.root / "BambuStudio.conf"
        with patch.object(Path, "is_symlink", lambda p: p == config or original(p)):
            self.assertFalse(inspect_cache(self.root).available)

    @patch("switcher.find_studio", return_value="")
    def test_auto_import_backs_up_once_and_keeps_active_cache(self, _find):
        before = {p.relative_to(self.root): p.read_bytes()
                  for p in self.root.rglob("*") if p.is_file()}
        result = auto_import_current(self.manager)
        self.assertEqual(result.status, "imported")
        self.assertEqual(self.manager.state()["profiles"][result.key], "本机账号")
        backup, = (self.manager.root / "backups").iterdir()
        for relative, content in before.items():
            self.assertEqual((self.root / relative).read_bytes(), content)
            self.assertEqual((backup / relative).read_bytes(), content)
        repeat = auto_import_current(self.manager)
        self.assertEqual(repeat.status, "managed")
        self.assertEqual(repeat.key, result.key)
        self.assertEqual(len(list((self.manager.root / "backups").iterdir())), 1)

    def test_running_studio_waits_without_creating_state_or_backup(self):
        self.manager.running = lambda: [123]
        result = auto_import_current(self.manager)
        self.assertEqual(result.status, "running")
        self.assertFalse(self.manager.root.exists())

    def test_no_session_leaves_cache_untouched(self):
        self.write_cache(user_id="")
        result = auto_import_current(self.manager)
        self.assertEqual(result.status, "unavailable")
        self.assertFalse(self.manager.root.exists())

    @patch("switcher.find_studio", return_value="")
    def test_corrupt_existing_state_is_never_reinitialized(self, _find):
        self.manager.initialize("我的账号")
        self.manager.state_file.write_text('{"version":42}')
        with self.assertRaises(SwitchError):
            auto_import_current(self.manager)
        self.assertEqual(self.manager.state_file.read_text(), '{"version":42}')

    @patch("switcher.find_studio", return_value="")
    def test_detach_prevents_auto_import_after_restart(self, _find):
        auto_import_current(self.manager)
        self.manager.detach()
        archived = list(self.manager.root.glob("state-detached-*.json"))
        restarted = Manager(self.temp.name, running=lambda: [])
        result = auto_import_current(restarted)
        self.assertEqual(result.status, "disabled")
        self.assertIsNone(restarted.state())
        self.assertEqual(list(self.manager.root.glob("state-detached-*.json")), archived)
        self.assertEqual(len(list((self.manager.root / "backups").iterdir())), 1)

    @patch("switcher.find_studio", return_value="")
    def test_manual_import_restores_management_and_keeps_archived_state(self, _find):
        auto_import_current(self.manager)
        self.manager.detach()
        archived = list(self.manager.root.glob("state-detached-*.json"))
        restarted = Manager(self.temp.name, running=lambda: [])
        result = auto_import_current(restarted, automatic=False)
        self.assertEqual(result.status, "imported")
        self.assertFalse((self.manager.root / "auto-import-disabled").exists())
        self.assertEqual(restarted.state()["active"], result.key)
        self.assertEqual(list(self.manager.root.glob("state-detached-*.json")), archived)
        self.assertEqual(auto_import_current(restarted).status, "managed")

    @patch("switcher.find_studio", return_value="")
    def test_manual_read_of_managed_profile_clears_disabled_marker(self, _find):
        key = self.manager.initialize("我的账号")
        disabled = self.manager.root / "auto-import-disabled"
        disabled.touch()
        self.assertEqual(auto_import_current(self.manager).status, "disabled")
        result = auto_import_current(self.manager, automatic=False)
        self.assertEqual(result.status, "managed")
        self.assertEqual(result.key, key)
        self.assertFalse(disabled.exists())

    def test_manual_read_waiting_for_studio_preserves_disabled_marker(self):
        self.manager.root.mkdir()
        disabled = self.manager.root / "auto-import-disabled"
        disabled.touch()
        self.manager.running = lambda: [123]
        result = auto_import_current(self.manager, automatic=False)
        self.assertEqual(result.status, "running")
        self.assertTrue(disabled.exists())
        self.assertFalse(self.manager.state_file.exists())

    def test_unsuccessful_manual_read_preserves_disabled_marker(self):
        self.manager.root.mkdir()
        disabled = self.manager.root / "auto-import-disabled"
        disabled.touch()
        self.write_cache(user_id="")
        result = auto_import_current(self.manager, automatic=False)
        self.assertEqual(result.status, "unavailable")
        self.assertTrue(disabled.exists())
        self.assertFalse(self.manager.state_file.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
