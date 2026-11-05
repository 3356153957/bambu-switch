"""UI regressions with isolated storage; never launch the real slicer."""
import json
import tempfile
import time
import tkinter as tk
from tkinter import ttk
from pathlib import Path
import unittest
from unittest.mock import patch

from app import App, show_dialog
from local_cache import inspect_cache
from switcher import Manager


class AccountUITests(unittest.TestCase):
    @staticmethod
    def settle(root):
        # Configure callbacks measure wrapped labels after the first layout pass.
        for _ in range(3):
            root.update_idletasks()
            root.update()

    @staticmethod
    def destroy_root(root):
        for callback in root.tk.call("after", "info"):
            root.after_cancel(callback)
        root.destroy()

    def wait_for_idle(self, root, app):
        deadline = time.monotonic() + 5
        while app.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        self.assertFalse(app.busy, "UI worker did not finish")
        self.settle(root)

    def assert_inside(self, widget, container):
        left = widget.winfo_rootx() - container.winfo_rootx()
        top = widget.winfo_rooty() - container.winfo_rooty()
        bounds = (left, top, left + widget.winfo_width(), top + widget.winfo_height())
        self.assertTrue(widget.winfo_ismapped(), (widget, "not visible"))
        self.assertGreaterEqual(bounds[0], 0, (widget, bounds))
        self.assertGreaterEqual(bounds[1], 0, (widget, bounds))
        self.assertLessEqual(bounds[2], container.winfo_width(), (widget, bounds))
        self.assertLessEqual(bounds[3], container.winfo_height(), (widget, bounds))

    @staticmethod
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from AccountUITests.descendants(child)

    def test_add_only_creates_profile_without_switching_or_launching(self):
        with tempfile.TemporaryDirectory(prefix="bambu-ui-test-") as temp:
            manager = Manager(temp, running=lambda: [])
            original = manager.initialize("账号 1")
            root = tk.Tk()
            root.withdraw()
            try:
                app = App(root, manager, demo=True)
                with patch.object(app, "ask_name", return_value="新增账号"), \
                     patch("switcher.subprocess.Popen") as launch, \
                     patch.object(app, "notify") as error:
                    app.add_button.invoke()
                    self.wait_for_idle(root, app)
                    launch.assert_not_called()
                    error.assert_not_called()
                state = manager.status()
                self.assertEqual(state["active"], original)
                self.assertIn("新增账号", state["profiles"].values())
                self.assertEqual(len(app.cards.rows), 2)
                new = next(key for key, name in state["profiles"].items() if name == "新增账号")
                app.cards.select(new)
                self.assertEqual(app.selected(), new)
            finally:
                self.destroy_root(root)

    def test_minimum_window_keeps_footer_and_long_account_names_visible_at_high_dpi(self):
        long_name = "长账号名称" * 6 + "演示"
        self.assertEqual(len(long_name), 32)
        for scaling in (4 / 3, 2, 8 / 3):
            with self.subTest(tk_scaling=scaling), tempfile.TemporaryDirectory(prefix="bambu-ui-layout-") as temp:
                manager = Manager(temp, running=lambda: [])
                manager.initialize(long_name)
                for index in range(8):
                    manager.add(f"{long_name[:28]}{index:04}")
                root = tk.Tk()
                root.withdraw()
                root.tk.call("tk", "scaling", scaling)
                try:
                    app = App(root, manager, demo=True)
                    width, height = root.minsize()
                    root.geometry(f"{width}x{height}+10000+10000")
                    root.deiconify()
                    app.message.set("账号已导入。点击「切换并启动」，在 Studio 中确认登录状态。切换前请保存未完成的项目。")
                    self.settle(root)
                    self.assertEqual((root.winfo_width(), root.winfo_height()), (width, height))
                    for widget in (app.switch_button, app.export_button, app.more, app.message_label):
                        self.assert_inside(widget, root)
                        self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight())
                    self.assertLessEqual(app.cards.winfo_rooty() + app.cards.winfo_height(),
                                         app.switch_button.winfo_rooty())
                    for key, card in app.cards.rows.items():
                        name = app.state["profiles"][key]
                        title = next(child for child in card.winfo_children()
                                     if child.cget("text") == name)
                        tag = next(child for child in card.winfo_children()
                                   if child.cget("text") in ("● 当前使用", "双击启动"))
                        self.assertGreaterEqual(tag.winfo_x(), title.winfo_x() + title.winfo_width())
                        self.assertLessEqual(tag.winfo_x() + tag.winfo_width(), card.winfo_width())
                        self.assertGreaterEqual(title.winfo_height(), title.winfo_reqheight())
                finally:
                    self.destroy_root(root)

    def test_themed_input_dialog_fits_long_prompt_and_chinese_actions(self):
        prompt = "为新账号设置名称，添加后选中它启动 Studio，再完成登录。" * 8
        for scaling in (4 / 3, 2, 8 / 3):
            with self.subTest(tk_scaling=scaling), tempfile.TemporaryDirectory(prefix="bambu-ui-dialog-") as temp:
                manager = Manager(temp, running=lambda: [])
                manager.initialize("测试账号")
                root = tk.Tk()
                root.tk.call("tk", "scaling", scaling)
                root.withdraw()
                try:
                    App(root, manager, demo=True)
                    root.geometry("780x570+10000+10000")
                    root.deiconify()
                    self.settle(root)
                    errors = []
                    inspected = []

                    def inspect_and_accept():
                        window = next((child for child in root.winfo_children()
                                       if isinstance(child, tk.Toplevel)), None)
                        try:
                            self.assertIsNotNone(window)
                            self.settle(window)
                            widgets = list(self.descendants(window))
                            entry = next(child for child in widgets if isinstance(child, ttk.Entry))
                            labels = [child for child in widgets if isinstance(child, tk.Label)]
                            prompt_label = next(child for child in labels if child.cget("text") == prompt)
                            buttons = {child.cget("text"): child for child in widgets
                                       if isinstance(child, ttk.Button)}
                            self.assertEqual(set(buttons), {"确定", "取消"})
                            for widget in (entry, prompt_label, *buttons.values()):
                                self.assert_inside(widget, window)
                                self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight())
                            entry.insert(0, "弹窗测试账号")
                            inspected.append(True)
                            buttons["确定"].invoke()
                        except Exception as exc:
                            errors.append(exc)
                            if window is not None and window.winfo_exists():
                                window.destroy()

                    root.after(100, inspect_and_accept)
                    result = show_dialog(root, "添加账号", prompt, input_value="")
                    if errors:
                        raise errors[0]
                    self.assertTrue(inspected)
                    self.assertEqual(result, "弹窗测试账号")
                finally:
                    self.destroy_root(root)

    def test_import_selects_new_profile_without_changing_active_or_launching_studio(self):
        with tempfile.TemporaryDirectory(prefix="bambu-ui-import-") as temp:
            destination = Manager(Path(temp) / "destination", running=lambda: [])
            active = destination.initialize("本机账号")
            (destination.active / "local-settings.txt").write_text("keep-local", encoding="utf-8")
            source = Manager(Path(temp) / "source", running=lambda: [])
            shared = source.initialize("分享账号")
            (source.active / "shared-settings.txt").write_text("shared-fixture", encoding="utf-8")
            archive = Path(temp) / "shared.bambu-account"
            source.export_profile(shared, archive)
            root = tk.Tk()
            root.withdraw()
            try:
                app = App(root, destination, demo=True)
                with patch("app.filedialog.askopenfilename", return_value=str(archive)) as choose, \
                     patch.object(app, "confirm", return_value=True) as confirm, \
                     patch.object(app, "notify") as notify, \
                     patch.object(destination, "import_profile", wraps=destination.import_profile) as import_profile, \
                     patch("switcher.subprocess.Popen") as launch:
                    app.import_button.invoke()
                    self.wait_for_idle(root, app)
                    choose.assert_called_once()
                    confirm.assert_called_once()
                    import_profile.assert_called_once_with(str(archive))
                    notify.assert_not_called()
                    launch.assert_not_called()
                state = destination.status()
                self.assertEqual(state["active"], active)
                self.assertEqual((destination.active / "local-settings.txt").read_text(encoding="utf-8"), "keep-local")
                imported = next(key for key, name in state["profiles"].items() if name == "分享账号")
                self.assertNotEqual(imported, active)
                self.assertEqual(app.selected(), imported)
                self.assertEqual((destination.profiles / imported / "shared-settings.txt").read_text(encoding="utf-8"),
                                 "shared-fixture")
            finally:
                self.destroy_root(root)

    def test_demo_read_cache_only_explains_without_reading_or_importing(self):
        with tempfile.TemporaryDirectory(prefix="bambu-ui-demo-cache-") as temp:
            manager = Manager(temp, running=lambda: [])
            manager.initialize("虚构演示账号")
            original_state = manager.state_file.read_bytes()
            root = tk.Tk()
            root.withdraw()
            try:
                app = App(root, manager, demo=True)
                with patch("app.inspect_cache") as inspect, \
                     patch("app.auto_import_current") as auto_import, \
                     patch.object(app, "notify") as notify, \
                     patch("switcher.subprocess.Popen") as launch:
                    app.cache_button.invoke()
                    self.settle(root)
                    inspect.assert_not_called()
                    auto_import.assert_not_called()
                    launch.assert_not_called()
                    notify.assert_called_once()
                    title, text = notify.call_args.args
                    self.assertEqual(title, "演示模式")
                    self.assertIn("不读取真实的 Studio 缓存", text)
                    self.assertIn("正常模式", text)
                    self.assertFalse(app.busy)
                self.assertEqual(manager.state_file.read_bytes(), original_state)
            finally:
                self.destroy_root(root)

    def test_managed_profile_read_cache_inspects_real_fixture_without_reimport_or_launch(self):
        with tempfile.TemporaryDirectory(prefix="bambu-ui-managed-cache-") as temp:
            manager = Manager(temp, running=lambda: [])
            manager.initialize("本机测试账号")
            (manager.active / "BambuStudio.conf").write_text(
                json.dumps({"app": {"preset_folder": "test_user_123"}}), encoding="utf-8")
            (manager.active / "user" / "test_user_123").mkdir(parents=True)
            (manager.active / "BambuNetworkEngine.conf").write_bytes(b"opaque-test-session")
            original_state = manager.state_file.read_bytes()
            original_profiles = manager.status()["profiles"]
            original_files = {file.relative_to(manager.active): file.read_bytes()
                              for file in manager.active.rglob("*") if file.is_file()}
            root = tk.Tk()
            root.withdraw()
            try:
                app = App(root, manager, demo=False)
                with patch("app.inspect_cache", wraps=inspect_cache) as inspect, \
                     patch("app.auto_import_current") as auto_import, \
                     patch.object(app, "notify") as notify, \
                     patch("switcher.subprocess.Popen") as launch:
                    app.cache_button.invoke()
                    self.wait_for_idle(root, app)
                    inspect.assert_called_once_with(manager.active)
                    auto_import.assert_not_called()
                    notify.assert_not_called()
                    launch.assert_not_called()
                self.assertIn("已读取「本机测试账号」的本机配置", app.message.get())
                self.assertIn("发现当前账号的本地登录缓存", app.message.get())
                self.assertIn("无需重复导入", app.message.get())
                self.assertEqual(manager.state_file.read_bytes(), original_state)
                self.assertEqual(manager.status()["profiles"], original_profiles)
                self.assertEqual({file.relative_to(manager.active): file.read_bytes()
                                  for file in manager.active.rglob("*") if file.is_file()}, original_files)
            finally:
                self.destroy_root(root)


if __name__ == "__main__":
    unittest.main()
