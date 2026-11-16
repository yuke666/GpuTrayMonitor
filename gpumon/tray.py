"""系统托盘图标与菜单（Shell_NotifyIcon + 独立消息循环线程）。

独立线程的好处：托盘窗口的消息循环不会和 Tk 的主循环互相干扰；所有对
图标/菜单的修改都通过 PostMessage 序列化到托盘线程内执行。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import threading
import time

from . import icons, win32
from .util import fmt_mib

user32 = win32.user32
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = win32.kernel32

NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_STATE = 0x08
NIF_INFO = 0x10

NIM_ADD = 0x00
NIM_MODIFY = 0x01
NIM_DELETE = 0x02
NIM_SETVERSION = 0x04

MF_STRING = 0x0000
MF_GRAYED = 0x0001
MF_CHECKED = 0x0008
MF_SEPARATOR = 0x0800
MF_POPUP = 0x0010

TPM_RIGHTBUTTON = 0x0002
TPM_LEFTALIGN = 0x0000
TPM_BOTTOMALIGN = 0x0020
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080

WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_UPDATE = WM_APP + 2
WM_SHUTDOWN = WM_APP + 3
WM_TASKBARCREATED = None  # 运行时注册
WM_ACTIVATE = None        # 运行时注册：第二个实例请求置前时投递

NIIF_INFO = 0x01

CMD_SHOW = "show"
CMD_REFRESH = "refresh"
CMD_TOGGLE_PAUSE = "toggle_pause"
CMD_INTERVAL = "interval:"
CMD_TOGGLE_ALLPY = "toggle_allpython"
CMD_TOGGLE_HUD = "toggle_hud"
CMD_TOGGLE_AUTOSTART = "toggle_autostart"
CMD_TOGGLE_START_HIDDEN = "toggle_start_hidden"
CMD_QUIT = "quit"


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uTimeout", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wt.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hWnd", wt.HWND),
        ("message", wt.UINT),
        ("wParam", wt.WPARAM),
        ("lParam", wt.LPARAM),
        ("time", wt.DWORD),
        ("pt", wt.POINT),
        ("lPrivate", wt.DWORD),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

class NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("guidItem", ctypes.c_byte * 16),
    ]


class RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG), ("right", wt.LONG), ("bottom", wt.LONG)]


user32.CreatePopupMenu.restype = wt.HMENU
user32.CreatePopupMenu.argtypes = []
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.TrackPopupMenuEx.restype = wt.UINT
user32.TrackPopupMenuEx.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                                    wt.HWND, ctypes.c_void_p]
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p,
]
user32.RegisterClassW.restype = wt.ATOM
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]


class TrayIcon:
    """托盘图标；start() 后在自己的线程里跑消息循环。"""

    CLASS_NAME = "GpuMonTrayWindow"

    def __init__(self, on_command, tooltip: str = "显存监视器") -> None:
        self.on_command = on_command
        self._hwnd: int = 0
        self._hicon = 0
        self._icon_key: tuple | None = None
        self._tip = tooltip
        self._pct: float | None = None
        self._paused = False
        self._show_all = True
        self._autostart = False
        self._start_hidden = True
        self._hud = True
        self._interval = 2.0
        self._counts = (0, 0)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._wndproc = WNDPROC(self._wnd_proc)
        self._hicon_cache: dict[tuple, int] = {}
        self._icon_size = win32.small_icon_size()
        self._taskbar_msg = 0
        self._activate_msg = 0
        self.registered = False
        self.failed_since = 0.0
        self.last_error = 0
        self._add_failures = 0

    # -- 生命周期 ----------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="gpumon-tray", daemon=True)
        self._thread.start()
        self._ready.wait(3.0)

    def stop(self) -> None:
        if self._hwnd:
            user32.PostMessageW(wt.HWND(self._hwnd), WM_SHUTDOWN, 0, 0)
        if self._thread:
            self._thread.join(2.0)

    # -- 外部更新（任意线程） ----------------------------------------------
    def set_status(self, pct: float | None, tooltip: str, paused: bool) -> None:
        with self._lock:
            self._pct = pct
            self._tip = tooltip[:120]
            self._paused = paused
        self._post(WM_UPDATE)

    def set_menu_state(self, *, interval: float | None = None, show_all: bool | None = None,
                       autostart: bool | None = None, paused: bool | None = None,
                       hud: bool | None = None, start_hidden: bool | None = None) -> None:
        with self._lock:
            if interval is not None:
                self._interval = interval
            if show_all is not None:
                self._show_all = show_all
            if autostart is not None:
                self._autostart = autostart
            if paused is not None:
                self._paused = paused
            if hud is not None:
                self._hud = hud
            if start_hidden is not None:
                self._start_hidden = start_hidden
        self._post(WM_UPDATE)

    def notify(self, title: str, text: str) -> None:
        """气泡提示（用于首次最小化到托盘）。"""
        data = self._data()
        data.uFlags = NIF_INFO
        data.szInfo = text[:255]
        data.szInfoTitle = title[:63]
        data.uTimeout = 4000
        data.dwInfoFlags = NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))

    def _post(self, message: int) -> None:
        if self._hwnd:
            user32.PostMessageW(wt.HWND(self._hwnd), message, 0, 0)

    # -- 内部 --------------------------------------------------------------
    def _run(self) -> None:
        self._taskbar_msg = user32.RegisterWindowMessageW("TaskbarCreated")
        self._activate_msg = user32.RegisterWindowMessageW("GpuMonActivateWindow")
        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = self.CLASS_NAME
        user32.RegisterClassW(ctypes.byref(wc))
        self._hwnd = user32.CreateWindowExW(
            0, self.CLASS_NAME, "GpuMonTray", 0, 0, 0, 0, 0, None, None, wc.hInstance, None
        )
        if not self._hwnd:
            self._ready.set()
            return
        self._add_icon()
        self._ready.set()
        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _data(self) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = wt.HWND(self._hwnd)
        data.uID = 1
        return data

    def _icon_for(self, pct: float | None) -> int:
        key = (self._icon_size, icons.color_for(pct), icons.bucket(pct))
        cached = self._hicon_cache.get(key)
        if cached:
            return cached
        bgra = icons.render(key[0], key[1], key[2])
        hicon = win32.make_hicon(bgra, key[0], key[0])
        self._hicon_cache[key] = hicon
        return hicon

    def _add_icon(self) -> None:
        with self._lock:
            pct, tip = self._pct, self._tip
        self._hicon = self._icon_for(pct)
        data = self._data()
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAY
        data.hIcon = self._hicon
        data.szTip = tip
        ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)))
        self.registered = ok
        log = logging.getLogger("gpumon.tray")
        if ok:
            self._add_failures = 0
            self.failed_since = 0.0
            self.last_error = 0
            log.info("托盘图标已注册 rect=%s size=%s", self.notification_rect(), self._icon_size)
        else:
            self.last_error = ctypes.get_last_error()
            self._add_failures += 1
            if not self.failed_since:
                self.failed_since = time.monotonic()
            if self._add_failures in (1, 10):
                log.warning("托盘图标注册失败（第 %d 次，错误码 %s）；将以窗口模式继续运行",
                            self._add_failures, self.last_error)

    def notification_rect(self) -> tuple[int, int, int, int] | None:
        """向系统查询本图标在通知区域的真实位置，用于自检。"""
        identifier = NOTIFYICONIDENTIFIER()
        identifier.cbSize = ctypes.sizeof(NOTIFYICONIDENTIFIER)
        identifier.hWnd = wt.HWND(self._hwnd)
        identifier.uID = 1
        rect = RECT()
        try:
            shell32.Shell_NotifyIconGetRect.argtypes = [
                ctypes.POINTER(NOTIFYICONIDENTIFIER), ctypes.POINTER(RECT)]
            shell32.Shell_NotifyIconGetRect.restype = ctypes.c_long
            hr = shell32.Shell_NotifyIconGetRect(ctypes.byref(identifier), ctypes.byref(rect))
            if hr != 0:
                return None
            return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            return None

    def _apply(self) -> None:
        if not self.registered:
            # 资源管理器可能尚未就绪（开机自启场景），每轮采样重试一次
            self._add_icon()
            if not self.registered:
                return
        with self._lock:
            pct, tip = self._pct, self._tip
        hicon = self._icon_for(pct)
        data = self._data()
        data.uFlags = NIF_ICON | NIF_TIP
        data.hIcon = hicon
        data.szTip = tip
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))
        if hicon != self._hicon:
            self._hicon = hicon

    def _wnd_proc(self, hwnd, message, wparam, lparam):
        try:
            if message == WM_TRAY:
                if lparam in (0x0202, 0x0201, 0x0405):  # WM_LBUTTONUP / DOWN / DBLCLK
                    self._emit(CMD_SHOW)
                elif lparam in (0x0205, 0x0204):        # WM_RBUTTONUP / DOWN
                    self._show_menu()
                return 0
            if message == WM_UPDATE:
                self._apply()
                return 0
            if message == WM_SHUTDOWN:
                data = self._data()
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
                user32.DestroyWindow(wt.HWND(hwnd))
                user32.PostQuitMessage(0)
                return 0
            if self._taskbar_msg and message == self._taskbar_msg:
                self._add_icon()
                return 0
            if self._activate_msg and message == self._activate_msg:
                self._emit(CMD_SHOW)
                return 0
        except Exception:
            pass
        return user32.DefWindowProcW(wt.HWND(hwnd), message, wparam, lparam)

    def _emit(self, command: str) -> None:
        try:
            self.on_command(command)
        except Exception:
            pass

    def _show_menu(self) -> None:
        with self._lock:
            paused, interval, show_all, autostart, hud = (
                self._paused, self._interval, self._show_all, self._autostart, self._hud,
            )
            start_hidden = self._start_hidden
        menu = user32.CreatePopupMenu()
        sub = user32.CreatePopupMenu()
        for seconds in (1, 2, 3, 5, 10):
            flags = MF_STRING | (MF_CHECKED if abs(interval - seconds) < 0.01 else 0)
            user32.AppendMenuW(sub, flags, 100 + seconds, f"{seconds} 秒")
        user32.AppendMenuW(menu, MF_STRING, 1, "显示主窗口")
        user32.AppendMenuW(menu, MF_STRING, 2, "立即刷新")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if paused else 0), 3,
                           "暂停监测" if not paused else "继续监测")
        user32.AppendMenuW(menu, MF_POPUP, sub, "采样间隔")
        user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if show_all else 0), 4,
                           "显示全部 Python 进程")
        user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if hud else 0), 6,
                           "在任务栏显示关键指标")
        user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if autostart else 0), 5, "开机自动启动")
        user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if start_hidden else 0), 7,
                           "启动时进托盘（不弹窗口）")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, 9, "退出显存监视器")

        point = wt.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(wt.HWND(self._hwnd))
        command = user32.TrackPopupMenuEx(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
            point.x, point.y, wt.HWND(self._hwnd), None
        )
        user32.PostMessageW(wt.HWND(self._hwnd), 0, 0, 0)
        user32.DestroyMenu(sub)
        user32.DestroyMenu(menu)

        if command == 1:
            self._emit(CMD_SHOW)
        elif command == 2:
            self._emit(CMD_REFRESH)
        elif command == 3:
            self._emit(CMD_TOGGLE_PAUSE)
        elif 100 <= command <= 200:
            self._emit(CMD_INTERVAL + str(command - 100))
        elif command == 4:
            self._emit(CMD_TOGGLE_ALLPY)
        elif command == 5:
            self._emit(CMD_TOGGLE_AUTOSTART)
        elif command == 6:
            self._emit(CMD_TOGGLE_HUD)
        elif command == 7:
            self._emit(CMD_TOGGLE_START_HIDDEN)
        elif command == 9:
            self._emit(CMD_QUIT)


def tooltip_for(snapshot) -> str:
    """从快照生成紧凑提示文字（<127 字符）。"""
    if snapshot is None or not snapshot.gpus:
        return "显存监视器 · 正在初始化"
    bits = []
    for g in snapshot.gpus[:2]:
        name = g.name.replace("NVIDIA GeForce ", "").replace("NVIDIA ", "")
        bits.append(f"{name} {fmt_mib(g.used_mib)}/{fmt_mib(g.total_mib)} {g.used_pct:.0f}%")
    text = " | ".join(bits)
    if snapshot.groups:
        top = snapshot.groups[0]
        text += f" · 最高 {top.name[:14]} {fmt_mib(top.total_mib)}"
    return text
