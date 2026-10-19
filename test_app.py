"""UI regressions with isolated storage; never launch the real slicer."""
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from app import App
from switcher import Manager


class AccountUITests(unittest.TestCase):
    def test_add_only_creates_profile_without_switching_or_launching(self):
        with tempfile.TemporaryDirectory(prefix="bambu-ui-test-") as temp:
            manager = Manager(temp, running=lambda: [])
            original = manager.initialize("账号 1")
            root = tk.Tk()
            root.withdraw()
            try:
                app = App(root, manager, demo=True)
                with patch("app.simpledialog.askstring", return_value="新增账号"), \
                     patch("switcher.subprocess.Popen") as launch, \
                     patch("app.messagebox.showerror") as error:
                    app.add_button.invoke()
                    deadline = time.monotonic() + 5
                    while app.busy and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                    self.assertFalse(app.busy)
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
                root.destroy()


if __name__ == "__main__":
    unittest.main()
