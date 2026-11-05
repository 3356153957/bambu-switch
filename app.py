"""Compact Windows account manager; --demo never touches real Studio data."""
import argparse
import ctypes
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, filedialog

from switcher import Manager, VERSION, shortcut, find_studio
from local_cache import auto_import_current, inspect_cache, AutoImportResult

BG, WHITE, INK, MUTED, GREEN = "#F5F7F6", "#FFFFFF", "#192B22", "#66756C", "#008B46"
ASSETS = Path(__file__).resolve().parent / "assets"


def show_dialog(parent, title, text, *, input_value=None, confirm=False):
    """One measured, themed dialog for account forms, notices and confirmations."""
    window = tk.Toplevel(parent)
    window.withdraw()
    window.title(title)
    window.configure(bg=BG)
    window.transient(parent)
    window.resizable(False, False)
    scale = max(1.0, float(parent.tk.call("tk", "scaling")) * 0.75)
    u = lambda n: int(n * scale)
    content = tk.Frame(window, bg=BG, padx=u(26), pady=u(24))
    content.pack(fill="both", expand=True)
    tk.Label(content, text=title, bg=BG, fg=INK, font=("Microsoft YaHei UI", 16, "bold"),
             anchor="w").pack(fill="x", pady=(0, u(12)))
    tk.Label(content, text=text, bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 10),
             wraplength=u(470), anchor="w", justify="left").pack(fill="x")
    result = [None if input_value is not None else False]
    entry = None
    error = tk.StringVar()
    if input_value is not None:
        entry = ttk.Entry(content, font=("Microsoft YaHei UI", 11), width=32)
        entry.insert(0, input_value)
        entry.pack(fill="x", ipady=u(7), pady=(u(18), 0))
        tk.Label(content, textvariable=error, bg=BG, fg="#B33C30",
                 font=("Microsoft YaHei UI", 9), anchor="w").pack(fill="x", pady=(u(5), 0))

    def accept(_=None):
        if entry is not None:
            value = entry.get().strip()
            if not value or len(value) > 32:
                error.set("请输入 1–32 字的账号名称。")
                entry.focus_set()
                return
            result[0] = value
        else:
            result[0] = True
        window.destroy()

    actions = tk.Frame(content, bg=BG)
    actions.pack(fill="x", pady=(u(22), 0))
    ttk.Button(actions, text="确定", style="Primary.TButton", command=accept, width=9).pack(side="right")
    if confirm or entry is not None:
        ttk.Button(actions, text="取消", command=window.destroy, width=9).pack(side="right", padx=(0, u(10)))
    window.bind("<Return>", accept)
    window.bind("<Escape>", lambda _: window.destroy())
    window.update_idletasks()
    width, height = max(u(490), window.winfo_reqwidth()), window.winfo_reqheight()
    x = max(0, min(parent.winfo_rootx() + (parent.winfo_width()-width)//2, window.winfo_screenwidth()-width))
    y = max(0, min(parent.winfo_rooty() + (parent.winfo_height()-height)//2, window.winfo_screenheight()-height-50))
    window.geometry(f"{width}x{height}+{x}+{y}")
    window.deiconify()
    window.grab_set()
    if entry is not None:
        entry.focus_set()
    else:
        window.focus_set()
    parent.wait_window(window)
    return result[0]


class AccountCards(tk.Frame):
    def __init__(self, parent, scale, activate, changed=None):
        super().__init__(parent, bg=BG)
        self.scale, self.activate, self.changed = scale, activate, changed
        self.rows, self.active, self.selection_id = {}, None, None
        self.enabled = True
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0, takefocus=True, height=1)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.inner = tk.Frame(self.canvas, bg=BG)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self.update_scroll)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<Return>", lambda _: self.activate() if self.enabled else None)
        self.canvas.bind("<Up>", lambda _: self.move(-1))
        self.canvas.bind("<Down>", lambda _: self.move(1))

    def wheel(self, event):
        if self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(-int(event.delta / 120), "units")
        return "break"

    def update_scroll(self, _=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        if self.inner.winfo_reqheight() <= self.canvas.winfo_height():
            self.canvas.yview_moveto(0)

    def move(self, direction):
        keys = list(self.rows)
        if keys and self.enabled:
            index = keys.index(self.selection_id) if self.selection_id in keys else 0
            index = max(0, min(len(keys) - 1, index + direction))
            self.select(keys[index])
            if self.inner.winfo_reqheight() > self.canvas.winfo_height():
                self.canvas.yview_moveto(index / len(keys))
        return "break"

    def set_profiles(self, state):
        previous = self.selection_id
        for child in self.inner.winfo_children():
            child.destroy()
        self.rows = {}
        self.active = state["active"] if state else None
        if not state:
            self.selection_id = None
            tk.Label(self.inner, text="还没有账号", bg=BG, fg=INK,
                     font=("Microsoft YaHei UI", 13, "bold")).pack(pady=(int(48*self.scale), 10))
            tk.Label(self.inner, text="读取本机已保存的登录，或导入别人分享的账号文件。", bg=BG,
                     fg=MUTED, font=("Microsoft YaHei UI", 10)).pack()
            return
        u = lambda n: int(n * self.scale)
        for index, (key, name) in enumerate(state["profiles"].items()):
            current = key == self.active
            card = tk.Frame(self.inner, bg=WHITE, highlightbackground="#DEE6E0", highlightthickness=1,
                            padx=u(15), pady=u(12), cursor="hand2")
            card.pack(fill="x", pady=(0, u(8)), padx=(0, u(6)))
            avatar = tk.Label(card, text=name[0] if name else str(index+1), bg="#E7F6ED" if current else "#EEF1F3",
                              fg=GREEN if current else MUTED, font=("Microsoft YaHei UI", 12, "bold"), width=3, pady=u(5))
            card.columnconfigure(1, weight=1)
            avatar.grid(row=0, column=0, padx=(0, u(13)))
            tag = tk.Label(card, text="● 当前使用" if current else "双击启动", bg=WHITE,
                           fg=GREEN if current else MUTED, font=("Microsoft YaHei UI", 9))
            tag.grid(row=0, column=2, padx=(u(8), 0))
            title = tk.Label(card, text=name, bg=WHITE, fg=INK, font=("Microsoft YaHei UI", 11, "bold"),
                             anchor="w", justify="left", wraplength=u(380))
            title.grid(row=0, column=1, sticky="ew")
            title.bind("<Configure>", lambda e, label=title: label.configure(wraplength=max(30, e.width)))
            self.rows[key] = card
            for widget in (card, avatar, title, tag):
                widget.bind("<Button-1>", lambda _, k=key: self.select(k))
                widget.bind("<Double-1>", lambda _, k=key: self.open(k))
                widget.bind("<MouseWheel>", self.wheel)
        self.select(previous if previous in self.rows else self.active, focus=False)

    def select(self, key, focus=True):
        if not self.enabled:
            return
        self.selection_id = key
        for row_key, card in self.rows.items():
            card.configure(highlightbackground=GREEN if row_key == key else "#DEE6E0")
        if focus:
            self.canvas.focus_set()
        if self.changed:
            self.changed()

    def open(self, key):
        if self.enabled:
            self.select(key)
            self.activate()

    def selected(self):
        return self.selection_id


class App:
    def __init__(self, root, manager, demo=False):
        self.root, self.manager, self.demo = root, manager, demo
        self.busy, self.state, self.settings_window = False, None, None
        self.more_window = None
        self.data_error = False
        self.events = queue.Queue()
        root.title(f"Bambu Switch {VERSION} · 账号管理" + (" · 演示" if demo else ""))
        self.scale = max(1.0, float(root.tk.call("tk", "scaling")) * 0.75)
        u = self.u
        width = min(u(780), max(480, root.winfo_screenwidth()-u(40)))
        height = min(u(570), max(400, root.winfo_screenheight()-u(70)))
        root.geometry(f"{width}x{height}")
        root.minsize(min(u(720), width), min(u(540), height))
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.iconbitmap(str(ASSETS / "BambuStudio.ico"))
        self.logo = tk.PhotoImage(file=str(ASSETS / "bambu-logo.png"))
        root.iconphoto(True, self.logo)
        self.small_logo = self.logo.subsample(max(1, round(154 / u(34))))
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(u(13), u(8)),
                        background=WHITE, foreground=INK, bordercolor="#DDE5DF", relief="flat")
        style.map("TButton", background=[("active", "#EDF3EF"), ("disabled", "#F0F2F1")],
                  foreground=[("disabled", "#A4AEA8")])
        style.configure("Primary.TButton", background=GREEN, foreground=WHITE, bordercolor=GREEN)
        style.map("Primary.TButton", background=[("active", "#00753B"), ("disabled", "#BADBC8")],
                  foreground=[("disabled", WHITE)])
        style.configure("Vertical.TScrollbar", background="#DBE4DD", troughcolor=BG, borderwidth=0, arrowsize=u(10))
        style.configure("TEntry", fieldbackground=WHITE, foreground=INK, bordercolor="#DDE5DF", padding=u(6))
        style.map("TEntry", bordercolor=[("focus", GREEN)])
        self.controls = []
        content = tk.Frame(root, bg=BG, padx=u(26), pady=u(22))
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(3, weight=1)
        header = tk.Frame(content, bg=BG)
        header.grid(row=0, column=0, sticky="ew")
        tk.Label(header, image=self.small_logo, bg=BG).pack(side="left", padx=(0, u(12)))
        tk.Label(header, text="Bambu Switch", bg=BG, fg=INK, font=("Segoe UI", 19, "bold")).pack(side="left")
        self.button(header, "设置", self.show_settings, right=True)
        self.current = tk.StringVar(value="正在读取账号…")
        current_label = tk.Label(content, textvariable=self.current, bg=BG, fg=MUTED,
                                font=("Microsoft YaHei UI", 10), anchor="w", justify="left")
        current_label.grid(row=1, column=0, sticky="ew", pady=(u(13), u(20)))
        current_label.bind("<Configure>", lambda e: current_label.configure(wraplength=max(30, e.width)))
        toolbar = tk.Frame(content, bg=BG)
        toolbar.grid(row=2, column=0, sticky="ew", pady=(0, u(17)))
        self.add_button = self.button(toolbar, "+ 添加账号", self.add)
        self.cache_button = self.button(toolbar, "读取本机", self.read_cache)
        self.import_button = self.button(toolbar, "导入文件", self.import_account)
        self.init_button = self.cache_button
        self.count = tk.StringVar(value="账号")
        tk.Label(toolbar, textvariable=self.count, bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="right")
        self.cards = AccountCards(content, self.scale, self.switch_selected, self.selection_changed)
        self.cards.grid(row=3, column=0, sticky="nsew")
        actions = tk.Frame(content, bg=BG)
        actions.grid(row=4, column=0, sticky="ew", pady=(u(16), u(12)))
        self.switch_button = self.button(actions, "切换并启动", self.switch_selected, primary=True)
        self.export_button = self.button(actions, "导出账号", self.export_account)
        self.more = self.button(actions, "更多 ···", self.open_more)
        self.message = tk.StringVar(value="双击账号也可启动。切换前请保存 Studio 项目。")
        self.message_label = tk.Label(content, textvariable=self.message, bg=BG, fg=MUTED, anchor="w",
                                      justify="left", font=("Microsoft YaHei UI", 9))
        self.message_label.grid(row=5, column=0, sticky="ew")
        self.message_label.bind("<Configure>", lambda e: self.message_label.configure(wraplength=max(30, e.width)))
        self.refresh()
        root.after(100, self.poll)
        if not demo:
            root.after(250, self.auto_read_cache)

    def u(self, value):
        return int(value * self.scale)

    def button(self, parent, text, command, primary=False, right=False):
        button = ttk.Button(parent, text=text, command=command, style="Primary.TButton" if primary else "TButton")
        button.pack(side="right" if right else "left", padx=(0, 8))
        self.controls.append(button)
        return button

    def selection_changed(self):
        if not hasattr(self, "switch_button"):
            return
        available = self.state is not None and bool(self.selected()) and not self.busy
        for button in (self.switch_button, self.export_button, self.more):
            button.state(["!disabled"] if available else ["disabled"])

    def refresh(self):
        try:
            self.state = self.manager.status()
        except Exception as exc:
            self.data_error = True
            self.state = None
            self.current.set("账号数据需要检查，请在设置中打开数据目录。")
            self.message.set(str(exc))
            for control in (self.add_button, self.cache_button, self.import_button, self.switch_button, self.export_button, self.more):
                control.state(["disabled"])
            return
        self.data_error = False
        self.cards.set_profiles(self.state)
        self.count.set(f"{len(self.state['profiles']) if self.state else 0} 个账号")
        self.current.set("当前使用  ·  " + self.state["profiles"][self.state["active"]] if self.state else "自动查找本机已保存的 Studio 登录")
        for control in (self.add_button, self.cache_button, self.import_button):
            control.state(["!disabled"] if not self.busy else ["disabled"])
        self.selection_changed()

    def run(self, work, success="操作完成"):
        if self.busy:
            return
        self.busy = True
        self.cards.enabled = False
        self.message.set("正在处理，请稍候…")
        for control in self.controls:
            control.state(["disabled"])
        def worker():
            try:
                result = work()
                self.events.put((True, result if success is None else success))
            except Exception as exc:
                self.events.put((False, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        select_key = None
        try:
            ok, message = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            if isinstance(message, dict):
                select_key = message.get("select")
                message = message.get("message", "操作完成")
            if isinstance(message, AutoImportResult):
                if message.status == "running":
                    self.root.after(5000, self.auto_read_cache)
                message = message.message
            self.busy = False
            self.cards.enabled = True
            for control in self.controls:
                control.state(["!disabled"])
            self.refresh()
            if select_key in self.cards.rows:
                self.cards.select(select_key)
            self.message.set(message or "操作完成")
            if not ok:
                self.notify("操作未完成", message)
        self.root.after(100, self.poll)

    def auto_read_cache(self):
        if not self.busy and not self.data_error and self.state is None:
            self.read_cache(automatic=True)

    def read_cache(self, automatic=False):
        if self.busy:
            return
        if self.demo:
            self.notify("演示模式", "当前仅使用虚构账号和临时数据，不读取真实的 Studio 缓存。\n\n请启动正常模式，保存并退出 Studio 后，点击「读取本机」。")
            return
        if self.state:
            if not automatic:
                name = self.state["profiles"][self.state["active"]]
                def inspect_managed():
                    cache = inspect_cache(self.manager.active)
                    return f"已读取「{name}」的本机配置。{cache.reason} 当前配置已受管理，无需重复导入。"
                self.run(inspect_managed, success=None)
            return
        self.run(lambda: auto_import_current(self.manager, automatic=automatic), success=None)

    def initialize(self):
        self.read_cache()

    def add(self):
        if self.busy:
            return
        first = not self.state
        prompt = ("为本机当前配置设置名称。请先保存并退出 Studio。" if first else
                  "为新账号设置名称。添加后选中它启动 Studio，再完成登录。")
        name = self.ask_name("添加账号", prompt)
        if name:
            def work():
                key = self.manager.initialize(name) if first else self.manager.add(name)
                return {"select": key, "message": "账号已添加，点击「切换并启动」即可打开 Studio。"}
            self.run(work, success=None)

    def selected(self):
        return self.cards.selected()

    def switch_selected(self):
        key = self.selected()
        if self.busy or not key:
            return
        if self.demo:
            self.run(lambda: self.manager.switch(key), "演示账号已切换，未启动真实 Studio。")
        else:
            self.run(lambda: self.manager.switch(key, launch=True, close=True), "已请求启动 Studio，请在 Studio 中确认登录状态。")

    def export_account(self):
        key = self.selected()
        if self.busy or not key or not self.state:
            return
        name = self.state["profiles"][key]
        if not self.confirm("导出账号", "文件会包含此账号的登录状态、打印机信息和设置，且未加密。\n请只交给你信任并允许使用该账号的人。\n\n请先保存并退出 Studio；跨电脑使用可能仍需重新登录。"):
            return
        path = filedialog.asksaveasfilename(title="导出账号文件", initialfile=name + ".bambu-account",
                                           defaultextension=".bambu-account", filetypes=[("Bambu 账号文件", "*.bambu-account")], parent=self.root)
        if path:
            self.run(lambda: self.manager.export_profile(key, path), "账号文件已导出，可以将该文件交给对方导入。")

    def import_account(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(title="导入账号文件", filetypes=[("Bambu 账号文件", "*.bambu-account")], parent=self.root)
        if not path:
            return
        if self.confirm("导入账号", "请只导入可信来源的文件，其中可能包含登录状态、设置和 Studio 插件。\n\n导入会新增账号，保留已有账号。同名账号会自动编号。\n请先保存并退出 Studio。"):
            def work():
                key = self.manager.import_profile(path)
                return {"select": key, "message": "账号已导入。点击「切换并启动」，在 Studio 中确认登录状态。"}
            self.run(work, success=None)

    def ask_name(self, title, text):
        return show_dialog(self.root, title, text, input_value="")

    def confirm(self, title, text, parent=None):
        return show_dialog(parent or self.root, title, text, confirm=True)

    def notify(self, title, text):
        show_dialog(self.root, title, text)

    def open_more(self):
        if self.busy or not self.selected():
            return
        if self.more_window and self.more_window.winfo_exists():
            self.more_window.destroy()
            return
        pop = self.more_window = tk.Toplevel(self.root)
        pop.withdraw()
        pop.overrideredirect(True)
        pop.transient(self.root)
        frame = tk.Frame(pop, bg=WHITE, padx=self.u(7), pady=self.u(7),
                         highlightbackground="#DDE5DF", highlightthickness=1)
        frame.pack(fill="both", expand=True)
        def perform(command):
            pop.destroy()
            command()
        for text, command in (("创建桌面入口", self.make_shortcut), ("打开所选账号目录", self.open_selected_data)):
            ttk.Button(frame, text=text, command=lambda cmd=command: perform(cmd), width=22).pack(fill="x", pady=self.u(3))
        pop.update_idletasks()
        width, height = pop.winfo_reqwidth(), pop.winfo_reqheight()
        x = max(0, min(self.more.winfo_rootx(), pop.winfo_screenwidth()-width))
        y = max(0, self.more.winfo_rooty()-height-self.u(6))
        pop.geometry(f"{width}x{height}+{x}+{y}")
        pop.bind("<Escape>", lambda _: pop.destroy())
        def outside(event):
            if not (x <= event.x_root < x+width and y <= event.y_root < y+height):
                pop.destroy()
        pop.bind("<Button-1>", outside)
        pop.deiconify()
        pop.grab_set()
        pop.focus_set()

    def make_shortcut(self):
        key = self.selected()
        if not self.busy and key and self.state:
            if self.demo:
                self.message.set("演示模式不创建真实桌面入口。")
                return
            name = self.state["profiles"][key]
            self.run(lambda: shortcut(key, name), "桌面入口已创建。移动软件后需重新创建入口。")

    def choose_studio(self):
        if self.busy:
            return
        if not self.state:
            self.notify("Studio 位置", "请先读取本机账号或添加一个账号。默认会自动查找 Studio。")
            return
        path = filedialog.askopenfilename(title="选择正式版 bambu-studio.exe", filetypes=[("Bambu Studio", "bambu-studio.exe")], parent=self.root)
        if path:
            self.run(lambda: self.manager.set_studio(path), "Studio 程序位置已保存。重新打开设置可查看。")

    def open_path(self, path):
        try:
            if not Path(path).is_dir():
                raise FileNotFoundError("此目录尚未创建。完成首次保存后会自动创建。")
            os.startfile(path)
        except OSError as exc:
            self.notify("打开目录", str(exc))

    def open_data(self):
        self.open_path(self.manager.root)

    def open_selected_data(self):
        key = self.selected()
        if not self.busy and self.state and key:
            self.open_path(self.manager.active if key == self.state["active"] else self.manager.profiles / key)

    def show_settings(self):
        if self.busy:
            return
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        window = self.settings_window = tk.Toplevel(self.root)
        window.title("设置与目录")
        window.configure(bg=WHITE)
        window.transient(self.root)
        window.geometry(f"{self.u(720)}x{self.u(600)}")
        window.minsize(self.u(680), self.u(570))
        content = tk.Frame(window, bg=WHITE, padx=self.u(24), pady=self.u(20))
        content.pack(fill="both", expand=True)
        tk.Label(content, text="设置与目录", bg=WHITE, fg=INK,
                 font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        tk.Label(content, text=f"Bambu Switch {VERSION}  ·  账号数据独立保存在 Windows 用户目录", bg=WHITE,
                 fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(8, 14))
        executable = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__).resolve()
        studio = (self.state or {}).get("studio") or find_studio()
        rows = [("本软件位置", executable.parent, "打开", lambda: self.open_path(executable.parent)),
                ("Studio 程序", studio or "未找到，请选择 bambu-studio.exe", "更改", self.choose_studio),
                ("当前 Studio 配置与登录缓存", self.manager.active, "打开", lambda: self.open_path(self.manager.active)),
                ("全部账号数据", self.manager.root, "打开", self.open_data),
                ("原始配置备份", self.manager.root / "backups", "打开", lambda: self.open_path(self.manager.root / "backups"))]
        for label, path, action, command in rows:
            row = tk.Frame(content, bg=WHITE)
            row.pack(fill="x", pady=(0, self.u(13)))
            tk.Label(row, text=label, bg=WHITE, fg=INK, font=("Microsoft YaHei UI", 10)).pack(anchor="w")
            line = tk.Frame(row, bg=WHITE)
            line.pack(fill="x", pady=(4, 0))
            entry = ttk.Entry(line, font=("Segoe UI", 9))
            entry.insert(0, str(path))
            entry.configure(state="readonly")
            entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
            ttk.Button(line, text=action, width=6, command=command).pack(side="right")
        tk.Label(content, text="查看具体账号目录，请用「更多 → 打开所选账号目录」。", bg=WHITE, fg=MUTED,
                 wraplength=self.u(650), justify="left", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(0, 12))
        ttk.Button(content, text="恢复普通使用…", command=lambda: self.detach(window)).pack(anchor="w")
        window.update_idletasks()
        height = max(self.u(600), window.winfo_reqheight())
        window.geometry(f"{self.u(720)}x{height}")
        window.minsize(self.u(680), height)

    def detach(self, window=None):
        if self.demo or self.busy:
            return
        if self.state and self.confirm("恢复普通使用", "请先退出 Studio。\n\n当前账号将留在 Studio 默认目录，并停止账号管理。\n其他账号及备份保留，原账号桌面入口将失效。\n\n继续？", parent=window):
            if window:
                window.destroy()
            self.run(self.manager.detach, "已恢复普通使用。其他账号与备份保留在数据目录。")

    def quit(self):
        if self.busy:
            self.notify("正在处理", "请等待当前操作完成后关闭窗口。")
        else:
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", help="Switch to profile ID and launch Bambu Studio")
    parser.add_argument("--demo", action="store_true", help="Preview with isolated temporary data")
    args = parser.parse_args()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BambuSwitch.Desktop")
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    temporary = tempfile.TemporaryDirectory(prefix="bambu-switch-demo-") if args.demo else None
    manager = Manager(temporary.name, running=lambda: []) if temporary else Manager()
    if temporary:
        manager.initialize("日常打印")
        manager.add("工作室")
    if args.profile:
        root.withdraw()
        try:
            manager.switch(args.profile, launch=True, close=True)
        except Exception as exc:
            show_dialog(root, "Bambu 账号切换未完成", str(exc))
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
