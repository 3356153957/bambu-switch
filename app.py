"""Windows desktop entry point; use --demo for a completely isolated preview."""
import argparse
import ctypes
import os
from pathlib import Path
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog

from switcher import Manager, VERSION, shortcut

BG, WHITE, INK, MUTED, GREEN = "#F5F7F6", "#FFFFFF", "#192B22", "#66756C", "#008B46"
ASSETS = Path(__file__).resolve().parent / "assets"


class AccountCards(tk.Frame):
    def __init__(self, parent, scale, activate):
        super().__init__(parent, bg=BG)
        self.scale, self.activate = scale, activate
        self.rows, self.active, self.selection_id = {}, None, None
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.inner = tk.Frame(self.canvas, bg=BG)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self.empty = tk.Label(self.inner, text="还没有保存的账号\n\n先保存当前账号，再添加其他账号。", bg=BG,
                              fg=MUTED, font=("Microsoft YaHei UI", 11), pady=int(58 * scale))
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<Return>", lambda _: activate())

    def wheel(self, event):
        self.canvas.yview_scroll(-int(event.delta / 120), "units")
        return "break"

    def set_profiles(self, state):
        previous = self.selection_id
        for child in self.inner.winfo_children():
            child.destroy()
        self.rows = {}
        if not state:
            self.selection_id = None
            tk.Label(self.inner, text="还没有保存的账号\n\n先保存当前账号，再添加其他账号。", bg=BG,
                     fg=MUTED, font=("Microsoft YaHei UI", 11), pady=int(60*self.scale)).pack(fill="x")
            return
        self.active = state["active"]
        u = lambda n: int(n * self.scale)
        for index, (key, name) in enumerate(state["profiles"].items()):
            current = key == self.active
            card = tk.Frame(self.inner, bg=WHITE, highlightbackground="#DEE6E0", highlightthickness=2,
                            padx=u(18), pady=u(17), cursor="hand2")
            card.pack(fill="x", pady=(0, u(12)), padx=(0, u(6)))
            avatar = tk.Label(card, text=(name[0] if name else str(index+1)), bg="#E7F6ED" if current else "#EEF1F6",
                              fg=GREEN if current else "#65748C", font=("Microsoft YaHei UI", 16, "bold"),
                              width=3, height=2)
            avatar.pack(side="left", padx=(0, u(16)))
            details = tk.Frame(card, bg=WHITE)
            details.pack(side="left", fill="x", expand=True)
            title = tk.Label(details, text=name, bg=WHITE, fg=INK, font=("Microsoft YaHei UI", 12, "bold"),
                             anchor="w", justify="left", wraplength=u(300))
            title.pack(fill="x")
            subtitle = tk.Label(details, text="正在使用此配置" if current else "独立保存登录状态与设置", bg=WHITE, fg=MUTED,
                                font=("Microsoft YaHei UI", 9), anchor="w")
            subtitle.pack(fill="x", pady=(u(5), 0))
            tag = tk.Label(card, text="●  当前使用" if current else "选择  ›", bg="#EAF7EF" if current else WHITE,
                           fg=GREEN if current else MUTED, font=("Microsoft YaHei UI", 9), padx=u(10), pady=u(5))
            tag.pack(side="right", padx=(u(8), 0))
            self.rows[key] = card
            for widget in (card, avatar, details, title, subtitle, tag):
                widget.bind("<Button-1>", lambda _, k=key: self.select(k))
                widget.bind("<Double-1>", lambda _, k=key: self.open(k))
                widget.bind("<MouseWheel>", self.wheel)
        self.select(previous if previous in self.rows else self.active)

    def select(self, key):
        self.selection_id = key
        for row_key, card in self.rows.items():
            card.configure(highlightbackground=GREEN if row_key == key else "#DEE6E0")
        self.canvas.focus_set()

    def open(self, key):
        self.select(key)
        self.activate()

    def selected(self):
        return self.selection_id


class App:
    def __init__(self, root, manager, demo=False):
        self.root, self.manager, self.demo = root, manager, demo
        self.busy = False
        self.events = queue.Queue()
        self.state = None
        root.title("Bambu 账号切换器" + (" · 演示" if demo else ""))
        scale = max(1.0, float(root.tk.call("tk", "scaling")) * 0.75)
        u = lambda n: int(n * scale)
        root.geometry(f"{u(980)}x{u(680)}")
        root.minsize(u(920), u(640))
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.iconbitmap(str(ASSETS / "BambuStudio.ico"))
        self.logo = tk.PhotoImage(file=str(ASSETS / "bambu-logo.png"))
        root.iconphoto(True, self.logo)
        self.small_logo = self.logo.subsample(max(1, round(154 / u(42))))
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(u(14), u(10)),
                        background=WHITE, foreground=INK, bordercolor="#DDE5DF", relief="flat")
        style.map("TButton", background=[("active", "#EDF3EF"), ("disabled", "#F0F2F1")],
                  foreground=[("disabled", "#A4AEA8")])
        style.configure("Primary.TButton", background=GREEN, foreground=WHITE, bordercolor=GREEN)
        style.map("Primary.TButton", background=[("active", "#00753B"), ("disabled", "#BADBC8")],
                  foreground=[("disabled", WHITE)])
        style.configure("Quiet.TButton", background=WHITE, bordercolor=WHITE, foreground=MUTED,
                        anchor="w", padding=(u(12), u(10)))
        style.configure("Vertical.TScrollbar", background="#DBE4DD", troughcolor=BG, borderwidth=0,
                        arrowsize=u(10))
        self.controls = []
        sidebar = tk.Frame(root, bg=WHITE, width=u(206), padx=u(20), pady=u(28))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, image=self.small_logo, bg=WHITE).pack(anchor="w")
        tk.Label(sidebar, text="Bambu", bg=WHITE, fg=INK,
                 font=("Segoe UI", 20, "bold")).pack(anchor="w", pady=(u(16), 0))
        tk.Label(sidebar, text="账号切换器", bg=WHITE, fg=MUTED,
                 font=("Microsoft YaHei UI", 11)).pack(anchor="w", pady=(u(2), u(32)))
        tk.Label(sidebar, text="●    账号管理", bg="#E8F6ED", fg=GREEN, anchor="w",
                 font=("Microsoft YaHei UI", 10, "bold"), padx=u(14), pady=u(12)).pack(fill="x")
        bottom = tk.Frame(sidebar, bg=WHITE)
        bottom.pack(side="bottom", fill="x")
        self.button(bottom, "程序位置", self.choose_studio, quiet=True)
        self.button(bottom, "打开数据目录", self.open_data, quiet=True)
        self.button(bottom, "恢复普通使用", self.detach, quiet=True)
        tk.Frame(bottom, height=1, bg="#E8EDE9").pack(fill="x", pady=u(17))
        tk.Label(bottom, text=f"v{VERSION}  ·  本地保存", bg=WHITE, fg="#88958D",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=u(12))
        tk.Frame(root, width=1, bg="#E5EBE7").pack(side="left", fill="y")
        content = tk.Frame(root, bg=BG, padx=u(32), pady=u(28))
        content.pack(side="left", fill="both", expand=True)
        header = tk.Frame(content, bg=BG)
        header.pack(fill="x")
        headings = tk.Frame(header, bg=BG)
        headings.pack(side="left", fill="x", expand=True)
        tk.Label(headings, text="账号管理", bg=BG, fg=INK, font=("Microsoft YaHei UI", 22, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(headings, text="多个账号，各自保留登录与设置。", bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 10), anchor="w").pack(fill="x", pady=(u(7), 0))
        self.add_button = self.button(header, "+  添加账号", self.add, primary=True, right=True)
        status_box = tk.Frame(content, bg="#EAF4ED", padx=u(18), pady=u(16))
        status_box.pack(fill="x", pady=(u(26), u(26)))
        self.current = tk.StringVar(value="正在读取配置…")
        tk.Label(status_box, textvariable=self.current, bg="#EAF4ED", fg=INK,
                 font=("Microsoft YaHei UI", 11, "bold"), anchor="w").pack(fill="x")
        self.helper = tk.StringVar()
        tk.Label(status_box, textvariable=self.helper, bg="#EAF4ED", fg=MUTED,
                 font=("Microsoft YaHei UI", 9), anchor="w", wraplength=u(570), justify="left").pack(fill="x", pady=(u(7), 0))
        section = tk.Frame(content, bg=BG)
        section.pack(fill="x", pady=(0, u(12)))
        self.count = tk.StringVar(value="已保存账号")
        tk.Label(section, textvariable=self.count, bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 10), anchor="w").pack(side="left")
        tk.Label(section, text="双击账号即可启动", bg=BG, fg="#8B978F",
                 font=("Microsoft YaHei UI", 9)).pack(side="right")
        self.cards = AccountCards(content, scale, self.switch_selected)
        self.cards.pack(fill="both", expand=True)
        actions = tk.Frame(content, bg=BG)
        actions.pack(fill="x", pady=(u(18), u(12)))
        self.switch_button = self.button(actions, "切换并启动 Studio", self.switch_selected, primary=True)
        self.shortcut_button = self.button(actions, "创建桌面入口", self.make_shortcut)
        self.init_button = self.button(actions, "保存当前账号", self.initialize)
        tk.Label(content, text="如 Studio 有未保存项目，请先处理保存提示。", bg=BG, fg="#819087",
                 font=("Microsoft YaHei UI", 9), anchor="w").pack(fill="x", pady=(u(4), u(9)))
        self.message = tk.StringVar(value="就绪")
        tk.Label(content, textvariable=self.message, bg=BG, fg=GREEN, anchor="w", wraplength=u(600),
                 justify="left", font=("Microsoft YaHei UI", 9)).pack(fill="x")
        self.refresh()
        root.after(100, self.poll)

    def button(self, parent, text, command, primary=False, quiet=False, right=False):
        style = "Primary.TButton" if primary else "Quiet.TButton" if quiet else "TButton"
        button = ttk.Button(parent, text=text, command=command, style=style)
        if quiet:
            button.pack(fill="x", pady=2)
        else:
            button.pack(side="right" if right else "left", padx=(0, 8))
        self.controls.append(button)
        return button

    def refresh(self):
        try:
            self.state = self.manager.status()
        except Exception as exc:
            self.message.set(str(exc))
            self.state = None
            self.current.set("配置需要检查")
            self.helper.set("请保留数据目录中的所有内容。中断操作可在 Studio 退出后重新打开本工具恢复。")
            self.switch_button.state(["disabled"])
            self.init_button.state(["disabled"])
            self.add_button.state(["disabled"])
            self.shortcut_button.state(["disabled"])
            return
        self.cards.set_profiles(self.state)
        self.count.set(f"已保存账号  ·  {len(self.state['profiles']) if self.state else 0}")
        if self.state:
            active = self.state["active"]
            self.current.set("当前配置  ·  " + self.state["profiles"][active])
            self.helper.set("选择账号后点击「切换并启动 Studio」，也可以创建专属桌面入口。")
        else:
            self.current.set("首次使用  ·  先保存当前账号")
            self.helper.set("为当前账号设置一个名称。保存前会完整备份已有 Studio 配置。")
        for button in (self.switch_button, self.add_button, self.shortcut_button):
            button.state(["!disabled"] if self.state else ["disabled"])
        self.init_button.state(["disabled"] if self.state else ["!disabled"])

    def run(self, work, success="操作完成"):
        if self.busy:
            return
        self.busy = True
        self.message.set("正在处理，请稍候… 如 Studio 出现保存提示，请先处理。")
        for control in self.controls:
            control.state(["disabled"])
        def worker():
            try:
                work()
                self.events.put((True, success))
            except Exception as exc:
                self.events.put((False, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            ok, message = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            for control in self.controls:
                control.state(["!disabled"])
            self.refresh()
            self.message.set(message)
            if not ok:
                messagebox.showerror("操作未完成", message, parent=self.root)
        self.root.after(100, self.poll)

    def initialize(self):
        name = simpledialog.askstring("保存当前账号", "为当前 Studio 账号设置一个便于识别的名称。\n请先保存并退出 Studio。", initialvalue="账号 1", parent=self.root)
        if name:
            self.run(lambda: self.manager.initialize(name), "当前账号已保存，原始配置已备份。接下来可以添加另一个账号。")

    def add(self):
        if self.busy:
            return
        name = simpledialog.askstring("添加账号", "为新账号设置一个名称。\n创建后可选中账号并启动 Studio，完成设置与登录。", parent=self.root)
        if name:
            self.run(lambda: self.manager.add(name), "账号已添加。选中后启动 Studio，完成设置与登录。")

    def selected(self):
        return self.cards.selected()

    def switch_selected(self):
        key = self.selected()
        if self.busy or not key:
            return
        if self.demo:
            self.run(lambda: self.manager.switch(key), "演示配置已切换，未启动真实 Studio。")
        else:
            self.run(lambda: self.manager.switch(key, launch=True, close=True), "配置已切换，已请求启动 Studio。登录结果请在 Studio 中确认。")

    def make_shortcut(self):
        key = self.selected()
        if key and self.state:
            if self.demo:
                self.message.set("演示模式不创建真实桌面入口。")
                return
            self.run(lambda: shortcut(key, self.state["profiles"][key]), "桌面入口已创建。保持本工具所在位置不变，双击入口即可使用。")

    def choose_studio(self):
        if not self.state:
            messagebox.showinfo("程序位置", "请先保存当前账号；默认会自动查找本机 Bambu Studio。", parent=self.root)
            return
        path = filedialog.askopenfilename(title="选择正式版 bambu-studio.exe", filetypes=[("Bambu Studio", "bambu-studio.exe")])
        if path:
            self.run(lambda: self.manager.set_studio(path), "Studio 程序位置已保存。")

    def open_data(self):
        os.startfile(self.manager.root)

    def detach(self):
        if self.demo:
            return
        if self.state and messagebox.askyesno("恢复普通使用", "请先退出 Studio。\n\n将当前账号留在 Studio 默认配置目录，并停止配置管理。\n其他账号及备份全部保留在数据目录，原桌面账号入口将不再可用。\n\n继续？", parent=self.root):
            self.run(self.manager.detach, "已恢复普通使用。其他账号与备份保留在数据目录。")

    def quit(self):
        if self.busy:
            messagebox.showinfo("正在处理", "请等待当前配置操作完成后关闭窗口。", parent=self.root)
        else:
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", help="Switch to profile ID and launch Bambu Studio")
    parser.add_argument("--demo", action="store_true", help="Preview with isolated temporary data")
    args = parser.parse_args()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    temporary = tempfile.TemporaryDirectory(prefix="bambu-switch-demo-") if args.demo else None
    manager = Manager(temporary.name, running=lambda: []) if temporary else Manager()
    if temporary:
        manager.initialize("账号 1")
        manager.add("账号 2")
    if args.profile:
        root.withdraw()
        try:
            manager.switch(args.profile, launch=True, close=True)
        except Exception as exc:
            messagebox.showerror("Bambu 账号切换未完成", str(exc), parent=root)
            root.destroy()
            raise SystemExit(1)
        root.destroy()
    else:
        App(root, manager, demo=args.demo)
        root.mainloop()
    if temporary:
        temporary.cleanup()


if __name__ == "__main__":
    main()
