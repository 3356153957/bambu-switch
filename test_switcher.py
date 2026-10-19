import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from switcher import Manager, SwitchError, atomic_json, shortcut, studio_pids, find_studio


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bambu-switch-test-")
        self.addCleanup(self.temp.cleanup)
        self.manager = Manager(self.temp.name, running=lambda: [])
        self.manager.active.mkdir()
        (self.manager.active / "session.txt").write_text("fake-home-session", encoding="utf-8")
        self.home = self.manager.initialize("家里")
        self.club = self.manager.add("社团")

    def test_backup_and_roundtrip_keep_independent_sessions(self):
        backups = list((self.manager.root / "backups").iterdir())
        self.assertEqual((backups[0] / "session.txt").read_text(), "fake-home-session")
        self.manager.switch(self.club)
        self.assertFalse((self.manager.active / "session.txt").exists())
        (self.manager.active / "session.txt").write_text("fake-club-session")
        self.manager.switch(self.home)
        self.assertEqual((self.manager.active / "session.txt").read_text(), "fake-home-session")
        self.manager.switch(self.club)
        self.assertEqual((self.manager.active / "session.txt").read_text(), "fake-club-session")

    def test_running_studio_blocks_all_moves(self):
        self.manager.running = lambda: [123]
        with self.assertRaises(SwitchError):
            self.manager.switch(self.club)
        self.assertEqual(self.manager.state()["active"], self.home)
        self.assertFalse(self.manager.journal.exists())

    def test_invalid_names_and_duplicates(self):
        for name in ("", "../x", "a\\b", "x\x00", "家里", "a" * 33):
            with self.subTest(name=repr(name)), self.assertRaises(SwitchError):
                self.manager.add(name)

    def test_unknown_profile_leaves_data(self):
        with patch("switcher.close_studio") as close:
            with self.assertRaises(SwitchError):
                self.manager.switch("../something", close=True)
            close.assert_not_called()
        self.assertEqual(self.manager.status()["active"], self.home)

    def test_recover_before_first_move(self):
        atomic_json(self.manager.journal, {"from": self.home, "to": self.club})
        self.assertEqual(self.manager.status()["active"], self.home)
        self.assertFalse(self.manager.journal.exists())

    def test_recover_after_first_move_rolls_back(self):
        atomic_json(self.manager.journal, {"from": self.home, "to": self.club})
        self.manager.active.rename(self.manager.profiles / self.home)
        self.assertEqual(self.manager.status()["active"], self.home)
        self.assertTrue((self.manager.active / "session.txt").is_file())

    def test_recover_after_second_move_finishes_commit(self):
        atomic_json(self.manager.journal, {"from": self.home, "to": self.club})
        self.manager.active.rename(self.manager.profiles / self.home)
        (self.manager.profiles / self.club).rename(self.manager.active)
        self.assertEqual(self.manager.status()["active"], self.club)
        self.assertFalse(self.manager.journal.exists())

    def test_recover_after_committed_state(self):
        self.manager.switch(self.club)
        atomic_json(self.manager.journal, {"from": self.home, "to": self.club})
        self.assertEqual(self.manager.status()["active"], self.club)

    def test_directory_collision_fails_without_overwrite(self):
        duplicate = self.manager.profiles / self.home
        duplicate.mkdir()
        (duplicate / "keep.txt").write_text("keep")
        with self.assertRaises(SwitchError):
            self.manager.switch(self.club)
        self.assertEqual((duplicate / "keep.txt").read_text(), "keep")
        self.assertTrue((self.manager.active / "session.txt").exists())

    def test_rename_error_rolls_back(self):
        original = Path.rename
        target = self.manager.profiles / self.club
        def fail_second(path, destination):
            if path == target:
                raise PermissionError("simulated file lock")
            return original(path, destination)
        with patch.object(Path, "rename", fail_second), self.assertRaises(SwitchError):
            self.manager.switch(self.club)
        self.assertEqual(self.manager.status()["active"], self.home)
        self.assertTrue((self.manager.active / "session.txt").exists())

    def test_lock_blocks_second_writer(self):
        with self.manager.locked():
            with self.assertRaises(SwitchError):
                Manager(self.temp.name, running=lambda: []).add("另外一个")

    def test_missing_executable_prevents_switch(self):
        state = self.manager.state()
        state["studio"] = str(Path(self.temp.name) / "missing.exe")
        atomic_json(self.manager.state_file, state)
        with self.assertRaises(SwitchError):
            self.manager.switch(self.club, launch=True)
        self.assertEqual(self.manager.status()["active"], self.home)

    def test_detach_keeps_all_profiles_and_backup(self):
        self.manager.detach()
        self.assertIsNone(self.manager.status())
        self.assertTrue((self.manager.active / "session.txt").is_file())
        self.assertTrue((self.manager.profiles / self.club).is_dir())
        self.assertTrue(list(self.manager.root.glob("state-detached-*.json")))

    def test_corrupt_state_preserves_data(self):
        self.manager.state_file.write_text('{"version":42}')
        with self.assertRaises(SwitchError):
            self.manager.status()
        self.assertTrue((self.manager.active / "session.txt").is_file())

    def test_missing_profile_does_not_initialize_silently(self):
        (self.manager.profiles / self.club).rmdir()
        with self.assertRaises(SwitchError):
            self.manager.switch(self.club)
        self.assertEqual(self.manager.state()["active"], self.home)

    def test_shortcut_unicode_quotes_spaces_and_collision(self):
        desktop = Path(self.temp.name) / "desktop's 中文 空格"
        desktop.mkdir()
        result = shortcut(self.club, "社团", desktop=desktop)
        self.assertTrue(result.is_file())
        self.assertGreater(result.stat().st_size, 0)
        with self.assertRaises(SwitchError):
            shortcut(self.club, "社团", desktop=desktop)

    def test_native_detection_returns_safe_types(self):
        self.assertIsInstance(studio_pids(), list)
        self.assertIsInstance(find_studio(), str)



if __name__ == "__main__":
    unittest.main(verbosity=2)
