"""Account archive tests use synthetic sessions and isolated temporary folders."""
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from switcher import Manager, SwitchError


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="bambu-transfer-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.manager = Manager(self.base / "local", running=lambda: [])
        self.manager.active.mkdir(parents=True)
        (self.manager.active / "BambuStudio.conf").write_text("synthetic-login-session", encoding="utf-8")
        (self.manager.active / "user" / "12345").mkdir(parents=True)
        (self.manager.active / "user" / "12345" / "preset.json").write_text('{"synthetic": true}')
        self.home = self.manager.initialize("家里")
        self.account_file = self.base / "家里.bambu-account"

    def exported(self):
        self.manager.export_profile(self.home, self.account_file)
        return self.account_file

    def make_archive(self, entries=None, *, name="共享账号", manifest_change=None):
        entries = entries if entries is not None else [("profile/config.txt", b"synthetic-shared-session")]
        records = [{"path": filename[len("profile/"):], "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest()}
                   for filename, content in entries if filename.startswith("profile/") and not filename.endswith("/")]
        manifest = {"format": "bambu-account", "version": 1, "name": name, "files": records}
        if manifest_change:
            manifest_change(manifest)
        source = self.base / "incoming.bambu-account"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            for filename, content in entries:
                archive.writestr(filename, content)
        return source

    def assert_rejected_without_changes(self, source):
        before = self.manager.state_file.read_bytes()
        with self.assertRaises(SwitchError):
            self.manager.inspect_profile(source)
        with self.assertRaises(SwitchError):
            self.manager.import_profile(source)
        self.assertEqual(self.manager.state_file.read_bytes(), before)
        self.assertEqual((self.manager.active / "BambuStudio.conf").read_text(), "synthetic-login-session")
        self.assertEqual(list(self.manager.profiles.iterdir()), [])

    def test_roundtrip_keeps_local_account_and_can_activate_import(self):
        source = self.exported()
        self.assertEqual(self.manager.inspect_profile(source),
                         {"name": "家里", "format_version": 1, "files": 2,
                          "bytes": len(b"synthetic-login-session") + len(b'{"synthetic": true}')})
        key = self.manager.import_profile(source)
        state = self.manager.status()
        self.assertEqual(state["active"], self.home)
        self.assertEqual(state["profiles"][key], "家里 (2)")
        self.assertEqual((self.manager.profiles / key / "BambuStudio.conf").read_text(), "synthetic-login-session")
        self.manager.switch(key)
        self.assertTrue((self.manager.active / "user" / "12345" / "preset.json").is_file())
        self.assertTrue((self.manager.profiles / self.home / "BambuStudio.conf").is_file())

    def test_export_inactive_profile_uses_selected_account(self):
        self.manager.switch(self.manager.add("另外"))
        (self.manager.active / "BambuStudio.conf").write_text("synthetic-other-session")
        self.exported()
        with zipfile.ZipFile(self.account_file) as archive:
            self.assertEqual(archive.read("profile/BambuStudio.conf"), b"synthetic-login-session")

    def test_export_omits_logs_caches_and_local_state_but_preserves_plugins(self):
        for directory in ("logs", "Cache", "tmp"):
            (self.manager.active / directory).mkdir()
            (self.manager.active / directory / "private.txt").write_text("omit")
        (self.manager.active / "session.log").write_text("omit")
        (self.manager.active / "plugins").mkdir()
        (self.manager.active / "plugins" / "bambu_networking.dll").write_bytes(b"synthetic-plugin")
        with zipfile.ZipFile(self.exported()) as archive:
            names = archive.namelist()
            self.assertIn("profile/plugins/bambu_networking.dll", names)
            self.assertFalse(any("private.txt" in name or name.endswith(".log") for name in names))
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(set(manifest), {"format", "version", "name", "files"})
            self.assertNotIn(str(self.manager.parent), json.dumps(manifest))
            self.assertNotIn("state.json", names)

    def test_empty_profile_and_empty_directories_roundtrip(self):
        key = self.manager.add("空账号")
        (self.manager.profiles / key / "empty").mkdir()
        self.manager.export_profile(key, self.account_file)
        imported = self.manager.import_profile(self.account_file)
        self.assertEqual(self.manager.inspect_profile(self.account_file)["files"], 0)
        self.assertTrue((self.manager.profiles / imported / "empty").is_dir())

    def test_import_before_initialization_preserves_and_backs_up_local_account(self):
        other = Manager(self.base / "new-machine", running=lambda: [])
        other.active.mkdir(parents=True)
        (other.active / "local.conf").write_text("synthetic-original-session")
        imported = other.import_profile(self.exported())
        state = other.status()
        self.assertEqual(state["profiles"][state["active"]], "本机账号")
        self.assertNotEqual(state["active"], imported)
        self.assertEqual((other.active / "local.conf").read_text(), "synthetic-original-session")
        backups = list((other.root / "backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "local.conf").read_text(), "synthetic-original-session")

    def test_import_before_initialization_without_studio_config(self):
        other = Manager(self.base / "fresh-machine", running=lambda: [])
        imported = other.import_profile(self.exported())
        self.assertTrue(other.active.is_dir())
        self.assertTrue((other.active / "BambuStudio.conf").is_file())
        state = other.status()
        self.assertEqual(state["profiles"], {imported: "家里"})
        self.assertEqual(state["active"], imported)
        self.assertFalse((other.profiles / imported).exists())

    def test_fresh_import_commit_failure_removes_only_new_active_profile(self):
        source = self.exported()
        other = Manager(self.base / "fresh-failure-machine", running=lambda: [])
        with patch("switcher.atomic_json", side_effect=OSError("synthetic-full-disk")), self.assertRaises(SwitchError):
            other.import_profile(source)
        self.assertFalse(other.state_file.exists())
        self.assertFalse(other.active.exists())
        self.assertEqual(list(other.profiles.iterdir()), [])

    def test_fresh_import_directory_collision_keeps_external_config(self):
        source = self.exported()
        other = Manager(self.base / "fresh-collision-machine", running=lambda: [])
        original = Path.rename
        def collide(path, destination):
            if destination == other.active:
                other.active.mkdir()
                (other.active / "external.conf").write_text("synthetic-external-session")
                raise FileExistsError("synthetic-external-config-created")
            return original(path, destination)
        with patch.object(Path, "rename", collide), self.assertRaises(SwitchError):
            other.import_profile(source)
        self.assertEqual((other.active / "external.conf").read_text(), "synthetic-external-session")
        self.assertFalse(other.state_file.exists())
        self.assertEqual(list(other.profiles.iterdir()), [])

    def test_requested_duplicate_name_is_rejected(self):
        with self.assertRaises(SwitchError):
            self.manager.import_profile(self.exported(), name="家里")
        self.assertEqual(list(self.manager.profiles.iterdir()), [])
        key = self.manager.import_profile(self.account_file, name="外出")
        self.assertEqual(self.manager.status()["profiles"][key], "外出")

    def test_automatic_duplicate_names_are_unique_and_at_most_32_characters(self):
        source = self.make_archive(name="长" * 32)
        names = []
        for _ in range(3):
            key = self.manager.import_profile(source)
            names.append(self.manager.status()["profiles"][key])
        self.assertEqual(len(set(names)), 3)
        self.assertTrue(all(len(name) <= 32 for name in names))
        self.assertTrue(names[-1].endswith(" (3)"))

    def test_running_studio_blocks_export_and_import(self):
        source = self.make_archive()
        self.manager.running = lambda: [123]
        self.assertEqual(self.manager.inspect_profile(source)["name"], "共享账号")
        with self.assertRaises(SwitchError):
            self.manager.export_profile(self.home, self.account_file)
        with self.assertRaises(SwitchError):
            self.manager.import_profile(source)
        self.assertFalse(self.account_file.exists())

    def test_export_rejects_destination_inside_active_and_manager_data(self):
        for destination in (self.manager.active / "account.bambu-account", self.manager.root / "account.bambu-account"):
            with self.subTest(destination=destination), self.assertRaises(SwitchError):
                self.manager.export_profile(self.home, destination)
            self.assertFalse(destination.exists())

    def test_malformed_paths_are_rejected_without_writing(self):
        for filename in ("../escape", "/absolute", "C:/drive", "profile/../escape", "profile/a\\b",
                         "profile/CON.txt", "profile/Nul", "profile/COM1.log", "profile/LPT¹", "profile/a:b",
                         "profile/a.", "profile/a ", "profile/a//b", "profile/./b", "profile/a\x01b"):
            with self.subTest(filename=repr(filename)):
                self.assert_rejected_without_changes(self.make_archive([(filename, b"synthetic")]))
        self.assertFalse((self.manager.root / "escape").exists())

    def test_duplicate_case_and_file_directory_conflicts_are_rejected(self):
        for entries in (
                [("profile/a", b"one"), ("profile/a", b"two")],
                [("profile/a", b"one"), ("profile/A", b"two")],
                [("profile/Folder/a", b"one"), ("profile/folder/b", b"two")],
                [("profile/a", b"one"), ("profile/a/b", b"two")],
                [("profile/a/b", b"one"), ("profile/a", b"two")]):
            with self.subTest(entries=entries):
                self.assert_rejected_without_changes(self.make_archive(entries))

    def test_symlink_and_special_file_entries_are_rejected(self):
        for mode in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK):
            with self.subTest(mode=mode):
                source = self.make_archive()
                with zipfile.ZipFile(source, "a") as archive:
                    info = zipfile.ZipInfo("profile/link")
                    info.create_system = 3
                    info.external_attr = (mode | 0o777) << 16
                    archive.writestr(info, b"../../outside")
                self.assert_rejected_without_changes(source)

    def test_changed_payload_fails_sha256_even_with_valid_zip_crc(self):
        source = self.make_archive(manifest_change=lambda manifest: manifest["files"][0].update(
            sha256=hashlib.sha256(b"different-synthetic-session").hexdigest()))
        self.assert_rejected_without_changes(source)

    def test_unknown_version_and_missing_manifest_payload_are_rejected(self):
        for change in (lambda data: data.update(version=999), lambda data: data.update(files=[]),
                       lambda data: data["files"][0].update(size=999), lambda data: data.update(name="../bad")):
            self.assert_rejected_without_changes(self.make_archive(manifest_change=change))

    def test_non_zip_and_truncated_archive_are_rejected(self):
        source = self.base / "invalid.bambu-account"
        source.write_bytes(b"not an account")
        self.assert_rejected_without_changes(source)
        source = self.exported()
        source.write_bytes(source.read_bytes()[:40])
        self.assert_rejected_without_changes(source)

    def test_archive_resource_limits_reject_before_import(self):
        source = self.make_archive()
        with patch.object(self.manager, "TRANSFER_MAX_FILE_BYTES", 1):
            self.assert_rejected_without_changes(source)
        with patch.object(self.manager, "TRANSFER_MAX_BYTES", 1):
            self.assert_rejected_without_changes(source)
        with patch.object(self.manager, "TRANSFER_MAX_MANIFEST_BYTES", 1):
            self.assert_rejected_without_changes(source)
        source = self.make_archive([(f"profile/{index}.txt", b"x") for index in range(5)])
        with patch.object(self.manager, "TRANSFER_MAX_FILES", 1):
            self.assert_rejected_without_changes(source)

    def test_import_state_write_failure_rolls_back_staged_profile(self):
        source = self.exported()
        before = self.manager.state_file.read_bytes()
        with patch("switcher.atomic_json", side_effect=OSError("synthetic-full-disk")), self.assertRaises(SwitchError):
            self.manager.import_profile(source)
        self.assertEqual(self.manager.state_file.read_bytes(), before)
        self.assertEqual(list(self.manager.profiles.iterdir()), [])
        self.assertEqual(self.manager.status()["active"], self.home)

    def test_first_import_failure_keeps_original_and_removes_incomplete_backup(self):
        source = self.exported()
        other = Manager(self.base / "failure-machine", running=lambda: [])
        other.active.mkdir(parents=True)
        (other.active / "local.conf").write_text("synthetic-original-session")
        with patch("switcher.atomic_json", side_effect=OSError("synthetic-full-disk")), self.assertRaises(SwitchError):
            other.import_profile(source)
        self.assertFalse(other.state_file.exists())
        self.assertEqual((other.active / "local.conf").read_text(), "synthetic-original-session")
        self.assertEqual(list(other.profiles.iterdir()), [])
        self.assertEqual(list((other.root / "backups").iterdir()), [])

    def test_export_failure_does_not_replace_existing_destination(self):
        self.account_file.write_bytes(b"existing-export")
        with patch("switcher.os.replace", side_effect=OSError("synthetic-full-disk")), self.assertRaises(SwitchError):
            self.manager.export_profile(self.home, self.account_file)
        self.assertEqual(self.account_file.read_bytes(), b"existing-export")
        self.assertEqual(list(self.base.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
