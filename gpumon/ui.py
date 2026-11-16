"""主窗口：GPU 仪表卡 + 按项目聚合的进程表 + 详情面板。"""
from __future__ import annotations

import os
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from .monitor import ProcRow, ProjectGroup, Snapshot
from . import __version__
from .util import (
    AMBER, GREEN, RED,
    fmt_clock, fmt_datetime, fmt_duration, fmt_mib, pressure_color, series_color,
    shorten_path,
)

BG = "#0f1116"
PANEL = "#171a22"
PANEL2 = "#1e222c"
PANEL3 = "#1b1f29"
BORDER = "#262c3a"
FG = "#e8ebf2"
MUTED = "#8b93a7"
DIM = "#5f6779"
SEL = "#24304a"
ACCENT = "#4f8cff"
AMBER_BG = "#2a2113"

UI_FONT = "Microsoft YaHei UI"
MONO_FONT = "Consolas"


def _round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kwargs):
    r = max(0, min(r, abs(x2 - x1) / 2, abs(y2 - y1) / 2))
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def _mix(color: str, base: str, weight: float) -> str:
    """把 color 与 base 按 weight 混合，用于画曲线下的浅色填充。"""
    c = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(base[i:i + 2], 16) for i in (1, 3, 5))
    out = tuple(int(c[i] * weight + b[i] * (1 - weight)) for i in range(3))
    return "#%02x%02x%02x" % out


class GpuCard:
    """GPU 仪表卡：左侧独显、中间核显、右侧双系列显存曲线。"""

    def __init__(self, parent: tk.Misc, scale, measure) -> None:
        self.scale = scale
        self.measure = measure
        self._gpus: list = []
        self._history: dict[int, list[float]] = {}
        self._fonts: dict[tuple, tkfont.Font] = {}
        self.canvas = tk.Canvas(parent, bg=BG, highlightthickness=0, bd=0,
                                height=self._s(124))
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.redraw())

    def _s(self, value) -> int:
        return int(round(value * self.scale))

    def _text_width(self, text: str, size: int, bold: bool = False,
                    mono: bool = False) -> int:
        key = (size, bold, mono)
        font = self._fonts.get(key)
        if font is None:
            font = tkfont.Font(family=MONO_FONT if mono else UI_FONT, size=-self._s(size),
                               weight="bold" if bold else "normal")
            self._fonts[key] = font
        return font.measure(text)

    def update(self, gpus, history) -> None:
        self._gpus = list(gpus)[:2]
        self._history = dict(history or {})
        self.redraw()

    # -- 绘制 --------------------------------------------------------------
    def redraw(self) -> None:
        gpus = self._gpus
        if not gpus:
            return
        c = self.canvas
        s = self._s
        w = max(c.winfo_width(), s(300))
        h = max(c.winfo_height(), s(112))
        c.delete("all")
        _round_rect(c, s(4), s(4), w - s(4), h - s(4), s(14), fill=PANEL, outline=BORDER,
                    width=1)

        pad, gap = s(20), s(26)
        chart_w = s(300) if w >= s(820) else 0
        count = len(gpus)
        block_w = (w - 2 * pad - chart_w - gap * count) / count

        for i, gpu in enumerate(gpus):
            x0 = pad + i * (block_w + gap)
            self._draw_block(c, x0, block_w, gpu, series_color(gpu.index), h)

        if chart_w:
            self._draw_chart(c, w - pad - chart_w, w - pad, gpus)

    def _draw_block(self, c: tk.Canvas, x0: float, width: float, gpu, colour: str,
                    h: float) -> None:
        s = self._s
        pressure = pressure_color(gpu.used_pct)
        _round_rect(c, x0 - s(12), s(14), x0 - s(8), h - s(14), s(2), fill=colour,
                    outline=colour)

        badge = gpu.kind
        if gpu.kind == "独显" and gpu.driver:
            badge = f"独显 · 驱动 {gpu.driver}"
        elif gpu.kind == "核显":
            badge = "核显 · 共享显存"
        c.create_text(x0 + width - s(8), s(16), text=badge, anchor="ne", fill=DIM,
                      font=(UI_FONT, -s(9)))

        name = gpu.short_name
        name_size = 13 if width >= s(300) else 12
        limit = width - s(8)
        while len(name) > 4 and self._text_width(name, name_size, True) > limit:
            name = name[:-1]
        c.create_text(x0, s(15), text=name, anchor="nw", fill=FG,
                      font=(UI_FONT, -s(name_size), "bold"))

        value = f"{gpu.used_mib / 1024:.2f} / {gpu.total_mib / 1024:.1f} GB"
        value_size = 20 if width >= s(300) else 17
        c.create_text(x0, s(37), text=value, anchor="nw", fill=FG,
                      font=(MONO_FONT, -s(value_size), "bold"))
        c.create_text(x0 + width - s(8), s(40), text=f"{gpu.used_pct:.1f}%", anchor="ne",
                      fill=pressure, font=(UI_FONT, -s(12), "bold"))

        c.create_text(x0, s(70), text=self._meta_text(gpu), anchor="nw", fill=MUTED,
                      font=(UI_FONT, -s(10)))

        bar_top, bar_h = s(97), s(7)
        bar_w = width - s(8)
        _round_rect(c, x0, bar_top, x0 + bar_w, bar_top + bar_h, bar_h / 2, fill=PANEL2,
                    outline="")
        ratio = min(1.0, (gpu.used_mib / gpu.total_mib) if gpu.total_mib else 0.0)
        fill_w = bar_w * ratio
        if fill_w > bar_h:
            _round_rect(c, x0, bar_top, x0 + fill_w, bar_top + bar_h, bar_h / 2,
                        fill=pressure, outline="")

    def _meta_text(self, gpu) -> str:
        parts = [f"GPU {gpu.util}%"]
        if gpu.temp > 0:
            parts.append(f"{gpu.temp}°C")
        if gpu.power_w > 0:
            parts.append(f"{gpu.power_w:.0f} / {gpu.power_limit_w:.0f} W"
                         if gpu.power_limit_w > 0 else f"{gpu.power_w:.0f} W")
        if gpu.fan >= 0:
            parts.append(f"风扇 {gpu.fan}%")
        if gpu.kind == "核显" and gpu.dedicated_total_mib:
            parts.append(f"专用 {gpu.dedicated_total_mib} MB")
        return "  ·  ".join(parts)

    def _draw_chart(self, c: tk.Canvas, x1: float, x2: float, gpus: list) -> None:
        s = self._s
        y1, y2 = s(46), s(104)
        c.create_text(x1, s(15), text="近 5 分钟 · 显存占比", anchor="nw", fill=DIM,
                      font=(UI_FONT, -s(9)))

        kinds = {g.kind for g in gpus}
        lx = x1
        for gpu in gpus:
            colour = series_color(gpu.index)
            c.create_oval(lx, s(32), lx + s(6), s(32) + s(6), fill=colour, outline="")
            label = gpu.kind if len(kinds) > 1 else f"GPU{gpu.index}"
            text = f"{label} {gpu.used_pct:.0f}%"
            c.create_text(lx + s(10), s(31), text=text, anchor="nw", fill=MUTED,
                          font=(UI_FONT, -s(9)))
            lx += s(10) + self._text_width(text, 9) + s(14)

        c.create_line(x1, y2, x2, y2, fill=BORDER, width=1)
        c.create_line(x1, (y1 + y2) / 2, x2, (y1 + y2) / 2, fill=PANEL2, width=1)

        series = []
        for gpu in gpus:
            pts = list(self._history.get(gpu.index, []))[-150:]
            if len(pts) < 2:
                continue
            step = (x2 - x1) / max(1, len(pts) - 1)
            coords = []
            for i, pct in enumerate(pts):
                coords.extend([x1 + i * step,
                               y2 - (y2 - y1) * max(0.0, min(100.0, pct)) / 100.0])
            series.append((gpu, coords))

        # 先铺面积，再画曲线，避免后画的填充遮住前一条线
        for gpu, coords in series:
            colour = series_color(gpu.index)
            c.create_polygon(*coords, x2, y2, x1, y2, fill=_mix(colour, PANEL, 0.16),
                             outline="")
        for gpu, coords in series:
            colour = series_color(gpu.index)
            c.create_line(*coords, fill=colour, width=max(1.0, s(1.6)), smooth=True)
            c.create_oval(coords[-2] - s(2.6), coords[-1] - s(2.6), coords[-2] + s(2.6),
                          coords[-1] + s(2.6), fill=colour, outline="")


class MainWindow:
    def __init__(self, app) -> None:
        self.app = app
        self.cfg = app.config
        self.root: tk.Tk = app.root
        self.scale = app.scale
        self.snap: Snapshot | None = None
        self._sig = None
        self._row_by_pid: dict[int, ProcRow] = {}
        self._group_by_key: dict[str, ProjectGroup] = {}
        self._sel_row: ProcRow | None = None
        self._sel_group: ProjectGroup | None = None
        self._first_build = True
        self._query = ""
        self._measure_cache: dict[tuple, tkfont.Font] = {}
        self._cards: list[GpuCard] = []

        self._build_styles()
        self._build_layout()
        self._bind_keys()
        self.root.protocol("WM_DELETE_WINDOW", self.app.hide_window)

    # -- 尺寸 / 字体 -------------------------------------------------------
    def S(self, value) -> int:
        return int(round(value * self.scale))

    def font(self, size: int, bold: bool = False, mono: bool = False) -> tuple:
        family = MONO_FONT if mono else UI_FONT
        return (family, -self.S(size), "bold") if bold else (family, -self.S(size))

    def measure(self, text: str, size: int, bold: bool = False) -> int:
        key = (size, bold)
        font = self._measure_cache.get(key)
        if font is None:
            font = tkfont.Font(family=MONO_FONT, size=-self.S(size),
                               weight="bold" if bold else "normal")
            self._measure_cache[key] = font
        return font.measure(text)

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                        borderwidth=0, relief="flat", rowheight=self.S(31),
                        font=self.font(12))
        style.configure("Treeview.Heading", background=PANEL2, foreground=MUTED,
                        borderwidth=0, relief="flat", font=self.font(10, bold=True),
                        padding=(self.S(8), self.S(6)))
        style.map("Treeview.Heading", background=[("active", "#232936")])
        style.map("Treeview", background=[("selected", SEL)],
                  foreground=[("selected", "#ffffff")])
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Vertical.TScrollbar", gripcount=0, background=PANEL2,
                        darkcolor=BG, lightcolor=PANEL2, troughcolor=BG, bordercolor=BG,
                        arrowcolor=MUTED, arrowsize=self.S(10), width=self.S(10))
        style.map("Vertical.TScrollbar", background=[("active", "#2c3342")])

    # -- 布局 --------------------------------------------------------------
    def _build_layout(self) -> None:
        s = self.S
        root = self.root
        root.configure(bg=BG)

        header = tk.Frame(root, bg=BG, height=s(56))
        header.pack(fill="x", padx=s(16), pady=(s(8), 0))
        header.pack_propagate(False)
        tk.Label(header, text="显存监视器", bg=BG, fg=FG,
                 font=self.font(18, bold=True)).pack(side="left", anchor="s", pady=(0, s(4)))
        tk.Label(header, text="GPU Tray Monitor · 按项目回溯显存占用", bg=BG, fg=DIM,
                 font=self.font(10)).pack(side="left", anchor="s", padx=(s(10), 0),
                                           pady=(0, s(5)))

        self.status_dot = tk.Label(header, text="●", bg=BG, fg=GREEN, font=self.font(11))
        self.status_dot.pack(side="right", anchor="s", pady=(0, s(5)))
        self.status_label = tk.Label(header, text="正在采样…", bg=BG, fg=MUTED,
                                     font=self.font(10))
        self.status_label.pack(side="right", anchor="s", padx=(0, s(3)), pady=(0, s(6)))
        self.settings_btn = tk.Menubutton(
            header, text="设置 ▾", bg=PANEL, fg=MUTED, activebackground=PANEL2,
            activeforeground=FG, relief="flat", bd=0, font=self.font(10),
            padx=s(10), pady=s(4), cursor="hand2",
        )
        self.settings_btn.pack(side="right", anchor="s", padx=(s(12), s(14)), pady=(0, s(5)))
        self._build_settings_menu()

        self.banner = tk.Frame(root, bg=AMBER_BG)
        self.banner_label = tk.Label(self.banner, text="", bg=AMBER_BG, fg=AMBER,
                                     font=self.font(10), anchor="w")
        self.banner_label.pack(side="left", padx=s(4))

        self.gpu_area = tk.Frame(root, bg=BG)
        self.gpu_area.pack(fill="x", padx=s(12), pady=(s(6), 0))

        statusbar = tk.Frame(root, bg=BG, height=s(24))
        statusbar.pack(fill="x", side="bottom", padx=s(16), pady=(0, s(3)))
        statusbar.pack_propagate(False)
        self.left_status = tk.Label(statusbar, text="", bg=BG, fg=DIM, font=self.font(9),
                                    anchor="w")
        self.left_status.pack(side="left")
        self.right_status = tk.Label(statusbar, text="", bg=BG, fg=DIM, font=self.font(9),
                                     anchor="e")
        self.right_status.pack(side="right")

        mid = tk.Frame(root, bg=BG)
        mid.pack(fill="both", expand=True, padx=s(12), pady=(s(8), 0))

        toolbar = tk.Frame(mid, bg=BG)
        toolbar.pack(fill="x", pady=(0, s(6)))

        entry_wrap = tk.Frame(toolbar, bg=PANEL, highlightbackground=BORDER,
                              highlightthickness=1, width=s(308), height=s(31))
        entry_wrap.pack(side="left")
        entry_wrap.pack_propagate(False)
        tk.Label(entry_wrap, text="⌕", bg=PANEL, fg=DIM, font=self.font(12)).pack(
            side="left", padx=(s(8), s(4)))
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(entry_wrap, textvariable=self.search_var, bg=PANEL,
                                     fg=FG, relief="flat", bd=0, insertbackground=FG,
                                     font=self.font(11), width=1)
        self.search_entry.pack(side="left", fill="both", expand=True, padx=(0, s(8)),
                               pady=s(3))
        self._hint = "搜索项目 / 进程 / 路径"
        self._hint_active = False
        self.search_entry.insert(0, self._hint)
        self.search_entry.configure(fg=DIM)
        self._hint_active = True
        self.search_entry.bind("<FocusIn>", self._search_focus_in)
        self.search_entry.bind("<FocusOut>", self._search_focus_out)
        self.search_var.trace_add("write", lambda *_a: self._apply_search())

        self.chk_python = self._toggle_button(toolbar, "显示全部 Python 进程",
                                              self.cfg.show_all_python, self._toggle_show_all)
        self.chk_python.pack(side="left", padx=(s(10), 0))
        self.chk_conly = self._toggle_button(toolbar, "仅 GPU 进程", False,
                                             self._toggle_only_gpu)
        self.chk_conly.pack(side="left", padx=(s(8), 0))

        self.refresh_btn = self._text_button(toolbar, "立即刷新", self.app.refresh)
        self.refresh_btn.pack(side="right")
        self.export_btn = self._text_button(toolbar, "导出 CSV", self._export)
        self.export_btn.pack(side="right", padx=(0, s(8)))
        self.interval_btn = self._text_button(toolbar, f"间隔 {self.cfg.interval:g}s",
                                              self._cycle_interval)
        self.interval_btn.pack(side="right", padx=(0, s(8)))

        body = tk.Frame(mid, bg=BG)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0, minsize=s(356))
        body.grid_rowconfigure(0, weight=1)

        table_frame = tk.Frame(body, bg=BG, highlightbackground=BORDER, highlightthickness=1)
        table_frame.grid(row=0, column=0, sticky="nsew")
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        columns = ("vram", "pct", "pid", "gpu", "cmd")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="tree headings",
                                 selectmode="browse")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.heading("#0", text="项目 / 进程", anchor="w")
        self.tree.heading("vram", text="显存", anchor="e")
        self.tree.heading("pct", text="占显存", anchor="e")
        self.tree.heading("pid", text="PID", anchor="e")
        self.tree.heading("gpu", text="GPU", anchor="w")
        self.tree.heading("cmd", text="路径 / 命令行", anchor="w")
        self.tree.column("#0", width=s(258), minwidth=s(180), stretch=False)
        self.tree.column("vram", width=s(84), minwidth=s(66), anchor="e", stretch=False)
        self.tree.column("pct", width=s(58), minwidth=s(46), anchor="e", stretch=False)
        self.tree.column("pid", width=s(58), minwidth=s(46), anchor="e", stretch=False)
        self.tree.column("gpu", width=s(112), minwidth=s(64), anchor="w", stretch=False)
        self.tree.column("cmd", width=s(300), minwidth=s(140), anchor="w", stretch=True)

        self.tree.tag_configure("proj", font=self.font(12, bold=True), foreground=FG,
                                background=PANEL3)
        self.tree.tag_configure("proc", font=self.font(11), foreground="#c6ccdb")
        self.tree.tag_configure("py", font=self.font(11), foreground="#a9c7ff")
        self.tree.tag_configure("idle", font=self.font(11), foreground=DIM)

        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview,
                               style="Vertical.TScrollbar")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)

        self.detail = tk.Frame(body, bg=PANEL, highlightbackground=BORDER,
                               highlightthickness=1)
        self.detail.grid(row=0, column=1, sticky="nsew", padx=(s(10), 0))
        self.detail.pack_propagate(False)
        self.detail.grid_propagate(False)

        actions = tk.Frame(self.detail, bg=PANEL, height=s(38))
        actions.pack(side="bottom", fill="x", padx=s(10), pady=(0, s(10)))
        actions.pack_propagate(False)
        self.btn_open = self._text_button(actions, "打开项目目录", self._open_project, small=True)
        self.btn_open.pack(side="left", pady=s(2))
        self.btn_copy = self._text_button(actions, "复制命令行", self._copy_cmdline, small=True)
        self.btn_copy.pack(side="left", padx=(s(6), 0), pady=s(2))
        self.btn_kill = self._text_button(actions, "结束进程", self._kill_process, small=True,
                                          danger=True)
        self.btn_kill.pack(side="right", pady=s(2))

        self.detail_text = tk.Text(
            self.detail, bg=PANEL, fg=FG, bd=0, relief="flat", wrap="word",
            font=self.font(11), padx=s(14), pady=s(12), insertwidth=0,
            highlightthickness=0, spacing1=self.S(1), spacing3=self.S(3),
            selectbackground=SEL, cursor="arrow", width=1, height=1,
        )
        self.detail_scroll = ttk.Scrollbar(self.detail, orient="vertical",
                                           command=self.detail_text.yview,
                                           style="Vertical.TScrollbar")
        self.detail_scroll.pack(side="right", fill="y")
        self.detail_text.pack(side="left", fill="both", expand=True)
        self.detail_text.configure(yscrollcommand=self.detail_scroll.set)
        self.detail_text.bind("<Control-c>", lambda _e: self._copy_selection())
        self._detail_tags()

    def _build_settings_menu(self) -> None:
        menu = tk.Menu(self.settings_btn, tearoff=0, bg=PANEL2, fg=FG,
                       activebackground=SEL, activeforeground="#ffffff", bd=0,
                       font=self.font(10))
        self._menu_state: list[tuple[int, str, object]] = []

        def toggle_item(text: str, getter, command) -> None:
            index = menu.index("end")
            index = 0 if index is None else index + 1
            menu.add_command(label=text, command=command)
            self._menu_state.append((index, text, getter))

        toggle_item("开机自动启动", lambda: bool(self.cfg.autostart),
                    self.app.toggle_autostart)
        toggle_item("启动时进托盘（不弹窗口）", lambda: bool(self.cfg.start_hidden),
                    self.app.toggle_start_hidden)
        toggle_item("在任务栏显示关键指标", lambda: bool(self.cfg.taskbar_hud),
                    self.app.toggle_hud)
        toggle_item("显示全部 Python 进程", lambda: bool(self.cfg.show_all_python),
                    self.app.toggle_show_all)
        menu.add_separator()
        menu.add_command(label="打开数据目录", command=self.app.open_data_dir)
        menu.add_command(label="打开日志文件", command=self.app.open_log)
        menu.add_separator()
        menu.add_command(label="关于 / 使用说明", command=self._about)
        menu.add_command(label="退出显存监视器", command=self.app.quit)
        self.settings_btn.configure(menu=menu)
        self.settings_menu = menu
        self._refresh_settings_menu()

    def _refresh_settings_menu(self) -> None:
        """勾选状态用前缀表示，避免自定义深色菜单里指示器看不清。"""
        menu = getattr(self, "settings_menu", None)
        if menu is None:
            return
        for index, text, getter in self._menu_state:
            try:
                on = bool(getter())
                menu.entryconfigure(index, label=("✓  " if on else "     ") + text)
            except Exception:
                pass

    def _toggle_button(self, parent, text, value, command) -> tk.Checkbutton:
        var = tk.BooleanVar(value=value)
        btn = tk.Checkbutton(
            parent, text=text, variable=var, command=command, indicatoron=False,
            bg=PANEL, fg=MUTED, activebackground=PANEL2, activeforeground=FG,
            selectcolor=SEL, relief="flat", bd=0, highlightthickness=0,
            font=self.font(10), padx=self.S(10), pady=self.S(5), cursor="hand2",
        )
        btn.var = var
        return btn

    def _text_button(self, parent, text, command, small: bool = False,
                     danger: bool = False) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command, bg=(PANEL2 if small else PANEL),
            fg=(RED if danger else "#cfd6e4"), activebackground=("#33262b" if danger else PANEL2),
            activeforeground=FG, relief="flat", bd=0, highlightthickness=0,
            font=self.font(10 if not small else 9),
            padx=self.S(10 if not small else 8), pady=self.S(5 if not small else 3),
            cursor="hand2",
        )

    def _detail_tags(self) -> None:
        t = self.detail_text
        t.tag_configure("h1", font=self.font(16, bold=True), foreground=FG,
                        spacing1=self.S(3), spacing3=self.S(4))
        t.tag_configure("path", font=self.font(9), foreground=MUTED, spacing3=self.S(6))
        t.tag_configure("label", font=self.font(9, bold=True), foreground=DIM,
                        spacing1=self.S(10), spacing3=self.S(3))
        t.tag_configure("value", font=self.font(11), foreground=FG)
        t.tag_configure("muted", font=self.font(10), foreground=MUTED, spacing3=self.S(2))
        t.tag_configure("mono", font=self.font(10, mono=True), foreground="#cfd6e4",
                        lmargin1=self.S(2), lmargin2=self.S(2))
        t.tag_configure("pill", font=self.font(9), foreground="#cfe0ff", background="#22304a")
        t.tag_configure("warn", font=self.font(10), foreground=AMBER)
        t.tag_configure("good", font=self.font(10), foreground=GREEN)
        t.tag_configure("big", font=(MONO_FONT, -self.S(17), "bold"), foreground=FG,
                        spacing1=self.S(2), spacing3=self.S(2))
        t.tag_configure("row", font=self.font(10), foreground="#c6ccdb",
                        lmargin1=self.S(2), lmargin2=self.S(10), spacing3=self.S(1))

    def _bind_keys(self) -> None:
        self.root.bind("<Escape>", lambda _e: self.app.hide_window())
        self.root.bind("<F5>", lambda _e: self.app.refresh())
        self.root.bind("<Control-f>", lambda _e: self.search_entry.focus_set())

    # -- 搜索 --------------------------------------------------------------
    def _search_focus_in(self, _event=None) -> None:
        if self._hint_active:
            self.search_entry.delete(0, "end")
            self.search_entry.configure(fg=FG)
            self._hint_active = False

    def _search_focus_out(self, _event=None) -> None:
        if not self.search_entry.get().strip():
            self._hint_active = True
            self.search_entry.insert(0, self._hint)
            self.search_entry.configure(fg=DIM)
            self._query = ""
            if self.snap:
                self.render(self.snap, force=True)

    def _apply_search(self) -> None:
        if self._hint_active:
            return
        self._query = self.search_var.get().strip().casefold()
        if self.snap:
            self.render(self.snap, force=True)

    # -- 数据渲染 ----------------------------------------------------------
    def render(self, snap: Snapshot, force: bool = False) -> None:
        self.snap = snap
        self._row_by_pid = {r.pid: r for r in snap.rows}
        self._group_by_key = {g.key: g for g in snap.groups}
        self._update_cards(snap)
        self._update_header(snap)

        groups = self._filtered_groups(snap)
        struct = (
            tuple(sorted(g.iid for g in groups)),
            tuple(sorted(r.iid for g in groups for r in g.rows)),
        )
        if force or struct != self._sig:
            self._rebuild(groups)
            self._sig = struct
        else:
            self._update_values(groups)
            self._reorder(groups)
        self._update_status(snap, groups)
        self._render_detail()

    def _filtered_groups(self, snap: Snapshot) -> list[ProjectGroup]:
        query = self._query
        only_gpu = bool(self.chk_conly.var.get())
        out: list[ProjectGroup] = []
        for group in snap.groups:
            rows = [r for r in group.rows if r.vram_mib > 0] if only_gpu else list(group.rows)
            if not rows:
                continue
            if query:
                hay = " ".join(
                    [group.name, group.root, group.tagline]
                    + [f"{r.name} {r.cmdline} {r.exe} {r.pid} {r.script_name}" for r in rows]
                ).casefold()
                if query not in hay:
                    continue
            clone = ProjectGroup(**{**group.__dict__, "rows": rows})
            clone.total_mib = round(sum(r.vram_mib for r in rows), 1)
            clone.max_util = max((r.util for r in rows), default=0.0)
            out.append(clone)
        out.sort(key=lambda g: (-g.total_mib, g.name.casefold()))
        return out

    def _pct_text(self, row: ProcRow) -> str:
        if row.gpu_index is None or not self.snap:
            return ""
        gpu = next((g for g in self.snap.gpus if g.index == row.gpu_index), None)
        if not gpu or not gpu.total_mib:
            return ""
        return f"{row.vram_mib / gpu.total_mib * 100:.1f}%"

    def _group_pct_text(self, group: ProjectGroup) -> str:
        total = sum(g.total_mib for g in (self.snap.gpus if self.snap else []))
        if not total:
            return ""
        return f"{group.total_mib / total * 100:.1f}%"

    def _rebuild(self, groups: list[ProjectGroup]) -> None:
        tree = self.tree
        open_state = {iid for iid in tree.get_children("") if tree.item(iid, "open")}
        selection = tree.selection()
        tree.delete(*tree.get_children(""))
        for group in groups:
            label = group.name if len(group.rows) == 1 else f"{group.name}   ×{len(group.rows)}"
            tree.insert(
                "", "end", iid=group.iid, text=label,
                values=(fmt_mib(group.total_mib), self._group_pct_text(group), "", "",
                        shorten_path(group.root, 76) if group.root else group.tagline),
                tags=("proj",),
                open=(self._first_build or group.iid in open_state),
            )
            for row in group.rows:
                label = f"  {row.name}" + (f"   {row.script_name}" if row.is_python else "")
                tag = "idle" if row.vram_mib <= 0 else ("py" if row.is_python else "proc")
                tree.insert(
                    group.iid, "end", iid=row.iid, text=label,
                    values=(fmt_mib(row.vram_mib), self._pct_text(row), str(row.pid),
                            row.gpu_label, row.summary),
                    tags=(tag,),
                )
        self._first_build = False
        if selection:
            alive = [iid for iid in selection if tree.exists(iid)]
            if alive:
                tree.selection_set(alive)
                tree.see(alive[0])
        elif groups:
            tree.selection_set(groups[0].iid)
        self._on_select()

    def _update_values(self, groups: list[ProjectGroup]) -> None:
        tree = self.tree
        for group in groups:
            if not tree.exists(group.iid):
                continue
            tree.item(group.iid, values=(
                fmt_mib(group.total_mib), self._group_pct_text(group), "", "",
                shorten_path(group.root, 76) if group.root else group.tagline,
            ))
            for row in group.rows:
                if not tree.exists(row.iid):
                    continue
                tree.item(row.iid, values=(fmt_mib(row.vram_mib), self._pct_text(row),
                                           str(row.pid), row.gpu_label, row.summary))
                tag = "idle" if row.vram_mib <= 0 else ("py" if row.is_python else "proc")
                tree.item(row.iid, tags=(tag,))

    def _reorder(self, groups: list[ProjectGroup]) -> None:
        tree = self.tree
        for index, group in enumerate(groups):
            if tree.index(group.iid) != index:
                tree.move(group.iid, "", index)
            for sub, row in enumerate(group.rows):
                try:
                    if tree.index(row.iid) != sub:
                        tree.move(row.iid, group.iid, sub)
                except tk.TclError:
                    pass

    def _update_cards(self, snap: Snapshot) -> None:
        if not self._cards:
            self._cards.append(GpuCard(self.gpu_area, self.scale, self.measure))
        self._cards[0].update(snap.gpus, snap.history)

    def _update_header(self, snap: Snapshot) -> None:
        if self.app.monitor.paused:
            self.status_dot.configure(fg=AMBER)
            self.status_label.configure(text="已暂停采样")
        else:
            self.status_dot.configure(fg=GREEN)
            self.status_label.configure(text=f"实时监测 · {self.cfg.interval:g}s")

        warnings = list(snap.errors)
        tray = getattr(self.app, "tray", None)
        if tray is not None and tray.failed_since and time.monotonic() - tray.failed_since > 12:
            code = getattr(tray, "last_error", 0)
            hint = "（管理员身份启动时无法放置托盘图标，请用普通身份运行）" if code == 5 else ""
            warnings.append(
                f"系统未允许托盘图标{('，错误码 ' + str(code)) if code else ''}{hint}，"
                "关闭窗口会最小化到任务栏（退出请用「设置 ▾ → 退出」）")

        if warnings:
            self.banner_label.configure(text="⚠   " + "   |   ".join(warnings))
            if not self.banner.winfo_ismapped():
                self.banner.pack(fill="x", padx=self.S(12), pady=(self.S(6), 0),
                                 before=self.gpu_area)
        elif self.banner.winfo_ismapped():
            self.banner.pack_forget()

    def _update_status(self, snap: Snapshot, groups: list[ProjectGroup]) -> None:
        self.left_status.configure(text=(
            f"{len(groups)} 个项目 · {len(snap.rows)} 个进程 · Python {snap.python_count}"
            f" · 合计 {fmt_mib(snap.total_used_mib)} / {fmt_mib(snap.total_vram_mib)}"
        ))
        self.right_status.configure(text=(
            f"采样 {fmt_clock(snap.ts)} · 耗时 {snap.elapsed_ms:.0f} ms · "
            f"间隔 {self.cfg.interval:g}s"
        ))

    # -- 交互 --------------------------------------------------------------
    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        self._sel_row = None
        self._sel_group = None
        if selection:
            iid = selection[0]
            if iid.startswith("g:"):
                self._sel_group = self._group_by_key.get(iid[2:])
            elif iid.startswith("p:"):
                try:
                    pid = int(iid[2:])
                except ValueError:
                    pid = -1
                self._sel_row = self._row_by_pid.get(pid)
        self._render_detail()

    def _current_row(self) -> ProcRow | None:
        if self._sel_row:
            return self._sel_row
        if self._sel_group and self._sel_group.rows:
            return self._sel_group.rows[0]
        return None

    def _render_detail(self) -> None:
        t = self.detail_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        group = self._sel_group
        row = self._sel_row
        if row is None and group is None:
            if self.snap and self.snap.groups:
                group = next((g for g in self.snap.groups if g.key == self.snap.groups[0].key), None)
            if group is None:
                t.insert("end", "暂无占用 GPU 显存的进程\n", "h1")
                t.insert("end", "启动一个 Python 训练 / 推理任务后，这里会显示它属于哪个项目及其依据。\n",
                         "muted")
                t.configure(state="disabled")
                self._update_action_states(None, None)
                return
        if row is None and group is not None:
            row = group.rows[0] if group.rows else None
            self._render_group_detail(t, group)
        elif row is not None:
            self._render_row_detail(t, row)
        t.configure(state="disabled")
        self._update_action_states(group, row)

    def _render_group_detail(self, t: tk.Text, group: ProjectGroup) -> None:
        t.insert("end", group.name + "\n", "h1")
        if group.root:
            t.insert("end", group.root + "\n", "path")
        pills = []
        if group.branch:
            pills.append(f"git:{group.branch}")
        if group.conda:
            pills.append(f"conda:{group.conda}")
        if group.venv:
            pills.append(f"venv:{group.venv}")
        if group.confidence:
            pills.append("置信度 " + group.confidence)
        for pill in pills:
            t.insert("end", f" {pill} ", "pill")
            t.insert("end", "  ")
        if pills:
            t.insert("end", "\n")
        t.insert("end", "合计占用\n", "label")
        t.insert("end", fmt_mib(group.total_mib) + "\n", "big")
        t.insert("end", f"{len(group.rows)} 个进程 · {group.kind or 'Python 项目'}\n", "muted")
        t.insert("end", "进程明细\n", "label")
        for row in group.rows[:12]:
            t.insert("end", f"{row.name}  ", "row")
            t.insert("end", f"PID {row.pid}   {fmt_mib(row.vram_mib)}   {row.script_name}\n",
                     "row")
        if len(group.rows) > 12:
            t.insert("end", f"… 另有 {len(group.rows) - 12} 个进程\n", "muted")
        if group.markers:
            t.insert("end", "识别依据\n", "label")
            t.insert("end", "、".join(group.markers) + "\n", "muted")

    def _render_row_detail(self, t: tk.Text, row: ProcRow) -> None:
        attr = row.attribution
        t.insert("end", (attr.project or row.name) + "\n", "h1")
        if attr.root:
            t.insert("end", attr.root + "\n", "path")
        pills = []
        if attr.conda:
            pills.append(f"conda:{attr.conda}")
        if attr.venv:
            pills.append(f"venv:{attr.venv}")
        if attr.branch:
            pills.append(f"git:{attr.branch}")
        if attr.kind and attr.kind != "Python 项目":
            pills.append(attr.kind)
        pills.append("置信度 " + (attr.confidence or "低"))
        for pill in pills:
            t.insert("end", f" {pill} ", "pill")
            t.insert("end", "  ")
        t.insert("end", "\n")

        t.insert("end", "显存占用\n", "label")
        t.insert("end", fmt_mib(row.vram_mib) + "\n", "big")
        extras = []
        if row.shared_mib > 0.5:
            extras.append(f"共享 {fmt_mib(row.shared_mib)}")
        if row.util > 0:
            extras.append(f"引擎 {row.util:.0f}%")
        if row.gpu_label:
            extras.append(row.gpu_label)
        if extras:
            t.insert("end", "  ·  ".join(extras) + "\n", "muted")

        t.insert("end", "进程\n", "label")
        t.insert("end", f"{row.name}   PID {row.pid}\n", "value")
        started = fmt_clock(row.created) if row.created else "未知"
        runtime = fmt_duration(time.time() - row.created) if row.created else "—"
        t.insert("end", f"启动 {started} · 已运行 {runtime}\n", "muted")

        t.insert("end", "命令行\n", "label")
        t.insert("end", (row.cmdline or "（不可读，可能受系统保护）") + "\n", "mono")
        t.insert("end", "工作目录\n", "label")
        t.insert("end", (row.cwd or "（不可读）") + "\n", "mono")
        if attr.script:
            t.insert("end", "入口脚本\n", "label")
            t.insert("end", attr.script + "\n", "mono")
        if attr.parent_chain:
            t.insert("end", "父进程链\n", "label")
            t.insert("end", " ← ".join(attr.parent_chain) + "\n", "muted")
        if attr.evidence:
            t.insert("end", "识别依据\n", "label")
            t.insert("end", "、".join(attr.evidence) + "\n", "muted")
        if row.source == "nvidia-smi":
            t.insert("end", "数据来自 nvidia-smi（性能计数器不可用时的兜底）\n", "warn")

    def _update_action_states(self, group: ProjectGroup | None, row: ProcRow | None) -> None:
        root = ""
        if group is not None:
            root = group.root
        elif row is not None:
            root = row.attribution.root
        self.btn_open.configure(state="normal" if root and os.path.isdir(root) else "disabled",
                                fg=("#cfd6e4" if root and os.path.isdir(root) else DIM))
        self.btn_copy.configure(state="normal" if row and row.cmdline else "disabled",
                                fg=("#cfd6e4" if row and row.cmdline else DIM))
        can_kill = bool(self._sel_row)
        self.btn_kill.configure(state="normal" if can_kill else "disabled",
                                fg=(RED if can_kill else DIM))

    def _on_double_click(self, _event=None) -> None:
        self._open_project()

    def _on_right_click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            self._on_select()
        row = self._current_row()
        root = self._sel_group.root if self._sel_group else (
            row.attribution.root if row else "")
        menu = tk.Menu(self.root, tearoff=0, bg=PANEL2, fg=FG, activebackground=SEL,
                       activeforeground="#ffffff", bd=0, font=self.font(10))
        menu.add_command(label="打开项目目录", command=self._open_project,
                         state="normal" if root and os.path.isdir(root) else "disabled")
        menu.add_command(
            label="打开进程所在目录",
            command=lambda: self._open_dir(os.path.dirname(row.exe)) if row else None,
            state="normal" if row and os.path.isdir(os.path.dirname(row.exe)) else "disabled")
        menu.add_separator()
        menu.add_command(label="复制命令行", command=self._copy_cmdline,
                         state="normal" if row and row.cmdline else "disabled")
        menu.add_command(label="复制项目路径", command=self._copy_root,
                         state="normal" if root else "disabled")
        menu.add_command(label="复制进程信息", command=self._copy_info,
                         state="normal" if row else "disabled")
        menu.add_separator()
        menu.add_command(label="结束进程", command=self._kill_process,
                         state="normal" if row else "disabled")
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_selection(self) -> None:
        try:
            text = self.detail_text.get("sel.first", "sel.last")
        except tk.TclError:
            text = ""
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)

    def _copy_text(self, text: str, note: str) -> None:
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.app.flash_status(note)

    def _copy_cmdline(self) -> None:
        row = self._current_row()
        if row:
            self._copy_text(row.cmdline, "命令行已复制")

    def _copy_root(self) -> None:
        root = self._sel_group.root if self._sel_group else (
            self._current_row().attribution.root if self._current_row() else "")
        self._copy_text(root, "项目路径已复制")

    def _copy_info(self) -> None:
        row = self._current_row()
        if not row:
            return
        self._copy_text(
            f"项目: {row.attribution.project}\n根目录: {row.attribution.root}\n"
            f"进程: {row.name} (PID {row.pid})\n显存: {fmt_mib(row.vram_mib)}\n"
            f"命令行: {row.cmdline}\n工作目录: {row.cwd}", "进程信息已复制")

    def _open_project(self) -> None:
        root = self._sel_group.root if self._sel_group else (
            self._current_row().attribution.root if self._current_row() else "")
        self._open_dir(root)

    def _open_dir(self, path: str) -> None:
        if path and os.path.isdir(path):
            os.startfile(path)  # noqa: S606
        else:
            self.app.flash_status("目录不存在", warn=True)

    def _kill_process(self) -> None:
        row = self._sel_row
        if not row:
            return
        if not messagebox.askyesno(
            "结束进程",
            f"确定结束 {row.name}（PID {row.pid}）吗？\n\n"
            f"项目：{row.attribution.project}\n显存：{fmt_mib(row.vram_mib)}",
            parent=self.root,
        ):
            return
        if self.app.monitor.table.terminate(row.pid):
            self.app.flash_status(f"已结束 PID {row.pid}")
            self.app.refresh()
        else:
            self.app.flash_status("结束失败：权限不足", warn=True)

    def _cycle_interval(self) -> None:
        options = [1, 2, 3, 5, 10]
        try:
            idx = options.index(int(self.cfg.interval))
        except ValueError:
            idx = 1
        self.app.set_interval(float(options[(idx + 1) % len(options)]))

    def _toggle_show_all(self) -> None:
        value = bool(self.chk_python.var.get())
        self.cfg.show_all_python = value
        self.cfg.save()
        self.app.tray.set_menu_state(show_all=value)
        self.app.refresh()

    def _toggle_only_gpu(self) -> None:
        if self.snap:
            self.render(self.snap, force=True)

    def _export(self) -> None:
        if not self.snap:
            return
        default = time.strftime("gpu-monitor-%Y%m%d-%H%M%S.csv")
        path = filedialog.asksaveasfilename(
            parent=self.root, title="导出当前快照", defaultextension=".csv",
            initialfile=default, filetypes=[("CSV 文件", "*.csv")])
        if not path:
            return
        import csv

        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["采样时间", "项目", "项目根目录", "进程", "PID", "显存MiB",
                                 "共享MiB", "引擎%", "GPU", "命令行"])
                stamp = fmt_datetime(self.snap.ts)
                for group in self.snap.groups:
                    for row in group.rows:
                        writer.writerow([stamp, group.name, group.root, row.name, row.pid,
                                         f"{row.vram_mib:.1f}", f"{row.shared_mib:.1f}",
                                         f"{row.util:.1f}", row.gpu_label, row.cmdline])
            self.app.flash_status(f"已导出 {os.path.basename(path)}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", str(exc), parent=self.root)

    def _about(self) -> None:
        messagebox.showinfo(
            "关于",
            f"显存监视器 GPU Tray Monitor  v{__version__}\n\n"
            "· 托盘常驻显示显存压力，左键开关窗口，右键菜单\n"
            "· 按项目聚合，可回溯每个 python.exe 属于哪个项目\n"
            "· 数据来源：NVML（显卡指标）、GPU Process Memory 性能计数器（每进程显存）、"
            "DXGI（适配器识别）、进程 PEB（命令行 / 工作目录 / 环境变量）\n\n"
            f"配置目录：{self.app.config_dir}\n"
            "退出：托盘右键 → 退出显存监视器",
            parent=self.root)

    # -- 供 app 调用 -------------------------------------------------------
    def set_interval_label(self) -> None:
        self.interval_btn.configure(text=f"间隔 {self.cfg.interval:g}s")

    def update_toggles(self) -> None:
        self.chk_python.var.set(self.cfg.show_all_python)
        self._refresh_settings_menu()




