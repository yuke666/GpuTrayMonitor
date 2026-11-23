"""任务栏关键指标条：贴着 Windows 任务栏左侧显示显存 / 利用率。

实现方式：一个没有标题栏、不抢焦点、始终置顶的 Tk 浮层，按任务栏
``Shell_TrayWnd`` 的实时矩形定位，因此任务栏换位置、改高度、换缩放都能跟随。
不是真正的 DeskBand（那需要 C++ COM 注册），但视觉与交互上等价。

外观：背景完全透明（只留文字），所以底色天然与任务栏一致；文字颜色按
Windows 的浅色/深色任务栏主题自动切换。

置顶策略（任务栏本身也是 topmost，容易被它盖住）：
1. 创建后给可见窗口加 ``WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE``；
2. 每次刷新与每秒一次 ``SetWindowPos(HWND_TOPMOST)``，把浮层重新压到最上层；
3. 若样式设置失败，仅依赖第 2 步也能正常显示，不会「窗口存在但看不见」。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import time
import tkinter as tk
import tkinter.font as tkfont
import winreg

from .util import fmt_speed

BG_KEY = "#010203"      # 透明色键（避免与主题色冲突）

UI_FONT = "Microsoft YaHei UI"
MONO_FONT = "Consolas"

PALETTE_DARK = {
    "fg": "#f2f4f8", "muted": "#c3cad6", "sep": "#5a6478",
    "green": "#35d399", "amber": "#f2b63c", "red": "#f2555a", "violet": "#a78bfa",
}
PALETTE_LIGHT = {
    "fg": "#141414", "muted": "#3a3f45", "sep": "#9aa0a6",
    "green": "#0b7d52", "amber": "#96590a", "red": "#b3261e", "violet": "#4f3ec0",
}

WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
GWL_EXSTYLE = -20
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
GA_ROOT = 2

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetAncestor.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
user32.GetWindow.restype = wt.HWND
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]

log = logging.getLogger("gpumon.hud")


def _as_int(handle) -> int:
    if handle is None:
        return 0
    return int(getattr(handle, "value", handle) or 0)


class TaskbarHud:
    """任务栏左侧的关键指标条（可拖动微调位置）。"""

    def __init__(self, root: tk.Tk, scale: float, on_open, on_toggle, on_quit,
                 on_move=None, offset: int = 10,
                 offset_y: int | None = None) -> None:
        self.root = root
        self.scale = scale
        self.on_open = on_open
        self.on_toggle = on_toggle
        self.on_quit = on_quit
        self.on_move = on_move
        self.offset = offset
        self.offset_y = offset_y
        self.enabled = False
        self._win: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._fonts: dict[tuple, tkfont.Font] = {}
        self._snap = None
        self._last_geometry = None
        self._hwnd = 0
        self._drag = None
        self._dragging = False
        self._announced = False
        self._light = None
        self._theme_checked = 0.0
        self._key = ""

    # -- 尺寸/字体 ---------------------------------------------------------
    def _s(self, value) -> int:
        return int(round(value * self.scale))

    def _font(self, size: int, bold: bool = False, mono: bool = False) -> tkfont.Font:
        key = (size, bold, mono)
        font = self._fonts.get(key)
        if font is None:
            font = tkfont.Font(family=MONO_FONT if mono else UI_FONT,
                               size=-self._s(size), weight="bold" if bold else "normal")
            self._fonts[key] = font
        return font

    # -- 开关 --------------------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if self.enabled:
            self._ensure_window()
            self._refresh()
        else:
            self._destroy()

    def update(self, snap) -> None:
        self._snap = snap
        if self.enabled:
            self._refresh()

    # -- 窗口 --------------------------------------------------------------
    def _ensure_window(self) -> None:
        if self._win is not None:
            return
        key = self._key_color()
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.configure(bg=key)
        try:
            win.attributes("-topmost", True)
        except tk.TclError:
            pass
        try:
            win.attributes("-transparentcolor", key)
        except tk.TclError:
            pass
        canvas = tk.Canvas(win, bg=key, highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        canvas.bind("<ButtonPress-1>", self._drag_start)
        canvas.bind("<B1-Motion>", self._drag_move)
        canvas.bind("<ButtonRelease-1>", self._drag_end)
        canvas.bind("<Button-3>", self._popup_menu)
        self._win = win
        self._canvas = canvas
        self._key = key
        win.deiconify()
        self._announced = False
        self._apply_styles()
        # 无标题栏窗口的标题对用户不可见，仅用于调试/自动化定位
        try:
            win.title("GpuMonHud")
        except tk.TclError:
            pass
        self._keep_on_top()

    def _handles(self) -> tuple[int, int]:
        """返回 (可见的 TkTopLevel, 内容子窗口)。Tk 在 Windows 上两者是不同 HWND。"""
        if self._win is None:
            return 0, 0
        try:
            child = int(self._win.winfo_id())
        except tk.TclError:
            return 0, 0
        root = _as_int(user32.GetAncestor(wt.HWND(child), GA_ROOT))
        return (root or child), child

    def _apply_styles(self) -> None:
        """给可见窗口与内容子窗口都加上 置顶 / 工具窗 / 不激活 样式。

        Tk 在窗口首次映射时会重置 toplevel 的扩展样式，所以调用方需要
        定期重试（见 _keep_on_top）。
        """
        top, child = self._handles()
        if not top:
            return
        for handle in {top, child}:
            try:
                ex = user32.GetWindowLongW(wt.HWND(handle), GWL_EXSTYLE) & 0xFFFFFFFF
                wanted = ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST
                if wanted != ex:
                    user32.SetWindowLongW(wt.HWND(handle), GWL_EXSTYLE,
                                          ctypes.c_long(wanted))
            except Exception:
                log.exception("设置任务栏指标条样式失败 hwnd=%s", handle)
        self._hwnd = top
        top_ex = user32.GetWindowLongW(wt.HWND(top), GWL_EXSTYLE) & 0xFFFFFFFF
        log.debug("HUD 句柄 top=%s child=%s ex=%#x", top, child, top_ex)

    def raise_topmost(self) -> None:
        """把指标条重新压到任务栏之上（任务栏自己也是 topmost）。"""
        if not self.enabled or self._win is None:
            return
        top, _child = self._handles()
        if not top:
            return
        if top != self._hwnd:
            self._hwnd = top
            self._apply_styles()
        try:
            ok = user32.SetWindowPos(
                wt.HWND(top), wt.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
            if not ok:
                log.debug("SetWindowPos 失败 err=%s", ctypes.get_last_error())
        except Exception:
            log.exception("提升任务栏指标条失败")

    def _keep_on_top(self) -> None:
        if not self.enabled or self._win is None:
            return
        self._sync_theme()
        self.raise_topmost()
        # Tk 首次映射后可能重置样式，这里补一次（每秒，成本可忽略）
        try:
            top, _child = self._handles()
            ex = user32.GetWindowLongW(wt.HWND(top), GWL_EXSTYLE) & 0xFFFFFFFF
            if not (ex & WS_EX_NOACTIVATE):
                self._apply_styles()
        except Exception:
            pass
        try:
            self._win.after(1000, self._keep_on_top)
        except tk.TclError:
            pass

    def _destroy(self) -> None:
        if self._win is not None:
            try:
                self._win.destroy()
            except tk.TclError:
                pass
        self._win = None
        self._canvas = None
        self._last_geometry = None
        self._hwnd = 0

    # -- 交互 --------------------------------------------------------------
    def _on_click(self, _event=None) -> None:
        log.debug("任务栏指标被点击")
        try:
            self.on_open()
        except Exception:
            log.exception("打开主窗口失败")

    def _drag_start(self, event) -> None:
        self._drag = (event.x_root, event.y_root)
        self._dragging = False

    def _drag_move(self, event) -> None:
        if not self._drag or not self._win:
            return
        x0, y0 = self._drag
        dx, dy = event.x_root - x0, event.y_root - y0
        if abs(dx) + abs(dy) > 4:
            self._dragging = True
        if not self._dragging:
            return
        pos = self._win.geometry().split("+")
        width, height = pos[0].split("x")
        self._win.geometry(f"{width}x{height}+{int(pos[1]) + dx}+{int(pos[2]) + dy}")
        self._drag = (event.x_root, event.y_root)

    def _drag_end(self, _event=None) -> None:
        self._drag = None
        if not self._dragging:
            self._on_click()
            return
        self._dragging = False
        rect = self._taskbar_rect()
        if rect and self._win and self.on_move:
            pos = self._win.geometry().split("+")
            left = round((int(pos[1]) - rect.left) / self.scale)
            top = round((int(pos[2]) - rect.top) / self.scale)
            self.offset = left
            self.offset_y = top
            try:
                self.on_move(left, top)
            except Exception:
                pass

    def _popup_menu(self, event) -> None:
        menu = tk.Menu(self.root, tearoff=0, bg="#1b1f29", fg="#e8ebf2",
                       activebackground="#24304a", activeforeground="#ffffff",
                       bd=0, font=(UI_FONT, -self._s(10)))
        menu.add_command(label="打开主窗口", command=self.on_open)
        menu.add_command(label="隐藏任务栏指标", command=self.on_toggle)
        menu.add_separator()
        menu.add_command(label="退出显存监视器", command=self.on_quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # -- 任务栏几何 --------------------------------------------------------
    def _taskbar_rect(self):
        hwnd = user32.FindWindowW("Shell_TrayWnd", None)
        if not hwnd:
            return None
        rect = wt.RECT()
        if not user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect)):
            return None
        if rect.right - rect.left < rect.bottom - rect.top:
            return None  # 竖向任务栏：暂不处理
        return rect

    # -- 主题 --------------------------------------------------------------
    def _key_color(self) -> str:
        """透明色键取接近任务栏的浅/深中性色：背景全透明，文字抗锯齿边缘也贴近任务栏。"""
        return "#f3f3f3" if self._is_light() else "#202020"

    def _is_light(self) -> bool:
        now = time.monotonic()
        if self._light is None or now - self._theme_checked > 30:
            self._light = self._theme_is_light()
            self._theme_checked = now
        return bool(self._light)

    def _sync_theme(self) -> None:
        """主题切换后同步透明色键与画布底色。"""
        if self._win is None or self._canvas is None:
            return
        key = self._key_color()
        if key == self._key:
            return
        self._key = key
        try:
            self._win.attributes("-transparentcolor", key)
        except tk.TclError:
            pass
        try:
            self._win.configure(bg=key)
            self._canvas.configure(bg=key)
        except tk.TclError:
            pass

    def _palette(self) -> dict:
        return PALETTE_LIGHT if self._is_light() else PALETTE_DARK

    @staticmethod
    def _theme_is_light() -> bool:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                return bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
        except OSError:
            return False

    @staticmethod
    def _pressure(pct: float, pal: dict) -> str:
        if pct >= 88:
            return pal["red"]
        if pct >= 65:
            return pal["amber"]
        return pal["green"]

    # -- 绘制 --------------------------------------------------------------
    def _segments(self, snap) -> list[tuple[str, tkfont.Font, str, int]]:
        """(文字, 字体, 颜色, 之前的间距)。重要度：已用/最大 → 占比 → 利用率 → 网速。"""
        pal = self._palette()
        if not snap:
            return [("显存监视器 初始化中", self._font(13), pal["muted"], 0)]
        row: list[tuple[str, tkfont.Font, str, int]] = []
        for index, gpu in enumerate(snap.gpus[:2]):
            ident = pal["green"] if index == 0 else pal["violet"]
            if index:
                row.append(("│", self._font(13), pal["sep"], self._s(10)))
            row.append(("●", self._font(12), ident, 0 if index == 0 else self._s(8)))
            row.append(("GPU" if gpu.kind == "独显" else "核显",
                        self._font(13), pal["muted"], self._s(6)))
            row.append((f"{gpu.used_mib / 1024:.1f}/{gpu.total_mib / 1024:.1f}G",
                        self._font(18, mono=True), pal["fg"], self._s(8)))
            row.append((f"{gpu.used_pct:.0f}%", self._font(18, bold=True),
                        self._pressure(gpu.used_pct, pal), self._s(7)))
            row.append((f"({gpu.util}%)", self._font(14, mono=True), pal["muted"],
                        self._s(7)))

        if getattr(snap, "net_ok", True):
            up = fmt_speed(getattr(snap, "net_up_bps", 0.0))
            down = fmt_speed(getattr(snap, "net_down_bps", 0.0))
            row.append(("│", self._font(13), pal["sep"], self._s(10) if row else 0))
            row.append(("↑", self._font(14), pal["muted"], self._s(6)))
            row.append((f"{up}/s", self._font(14, mono=True), pal["fg"], self._s(4)))
            row.append(("↓", self._font(14), pal["muted"], self._s(8)))
            row.append((f"{down}/s", self._font(14, mono=True), pal["fg"], self._s(4)))

        if not row:
            return [("显存监视器 初始化中", self._font(13), pal["muted"], 0)]
        return row

    def _refresh(self) -> None:
        if not self.enabled:
            return
        self._ensure_window()
        canvas = self._canvas
        if canvas is None:
            return
        self._sync_theme()
        rect = self._taskbar_rect()
        bar_h = (rect.bottom - rect.top) if rect else self._s(48)
        # 字号加大后按任务栏实际高度收一下，避免超出任务栏
        height = min(self._s(50), max(self._s(30), bar_h - self._s(4)))
        segs = self._segments(self._snap)
        pad = self._s(4)
        width = pad * 2
        for text, font, _colour, gap in segs:
            width += gap + font.measure(text)
        width = max(width, self._s(80))

        canvas.delete("all")
        x = pad
        mid = height / 2
        for text, font, colour, gap in segs:
            x += gap
            canvas.create_text(x, mid, text=text, anchor="w", fill=colour, font=font)
            x += font.measure(text)

        # 定位到任务栏左侧（拖动时保持用户位置）
        if self._dragging:
            return
        if rect is None:
            try:
                self._win.withdraw()
            except tk.TclError:
                pass
            return
        if not self._win.winfo_ismapped():
            self._win.deiconify()
        x_pos = rect.left + self._s(self.offset)
        if self.offset_y is None:
            y_pos = rect.top + max(0, (bar_h - height) // 2)
        else:
            y_pos = rect.top + self._s(self.offset_y)
        geometry = f"{width}x{height}+{int(x_pos)}+{int(y_pos)}"
        if geometry != self._last_geometry:
            self._win.geometry(geometry)
            self._last_geometry = geometry
        self.raise_topmost()
        if not self._announced:
            self._announced = True
            log.info("任务栏指标条已显示 hwnd=%s rect=%s 尺寸=%sx%s",
                     self._hwnd, (int(x_pos), int(y_pos)), width, height)

