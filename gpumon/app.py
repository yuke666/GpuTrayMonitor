"""程序入口：装配配置、托盘、采样线程与主窗口。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import os
import queue
import sys
import time
import tkinter as tk
import traceback

from . import icons, tray as tray_mod, win32
from .config import Config, data_dir, setup_logging
from .monitor import Monitor, Snapshot
from .taskbar_hud import TaskbarHud
from .ui import MainWindow

APP_MUTEX = "Local\\GpuTrayMonitor_SingleInstance"


def _selftest_tray() -> int:
    """Create just the tray icon and report what Windows really did.

    Useful when the UI warns that the tray icon was refused: it prints the
    Win32 error code and the icon rectangle instead of guessing.
    """
    win32.set_dpi_awareness()
    ctypes.set_last_error(0)
    icon = tray_mod.TrayIcon(on_command=lambda _cmd: None, tooltip="显存监视器 自检")
    icon.start()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not icon.registered and not icon.failed_since:
        time.sleep(0.05)

    ok = bool(icon.registered)
    rect = icon.notification_rect()
    err = ctypes.get_last_error()
    print(f"registered   : {ok}")
    print(f"icon rect    : {rect}")
    print(f"hwnd         : {icon._hwnd}")
    print(f"icon size    : {icon._icon_size}")
    print(f"add failures : {icon._add_failures}")
    print(f"last error   : {err}")
    if not ok:
        print()
        print("hint: 0x5 (5) = access denied, usually because the program runs")
        print("      elevated while the taskbar runs at normal integrity.")
    icon.stop()
    return 0 if ok else 2


class App:
    def __init__(self, start_hidden: bool = False, selftest_hud: bool = False) -> None:
        self.config = Config.load()
        self.config_dir = data_dir()
        self.scale = 1.0
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("显存监视器 · GPU Tray Monitor")
        self._apply_icon()
        dpi = win32.get_dpi_for_window(self.root.winfo_id()) or win32.get_system_dpi()
        self.scale = max(0.8, min(2.5, dpi / 96.0))
        self._restore_geometry()

        self.commands: queue.Queue[str] = queue.Queue()
        self.monitor = Monitor(self.config, on_snapshot=self._on_snapshot)
        self.tray = tray_mod.TrayIcon(self._tray_command)
        self.window = MainWindow(self)
        self._latest: Snapshot | None = None
        self._rendered: Snapshot | None = None
        self._hud_snap: Snapshot | None = None
        self._shown_once = False
        self._flash_until = 0.0
        self.hud = TaskbarHud(
            self.root, self.scale, on_open=self.show_window,
            on_toggle=self.toggle_hud, on_quit=self.quit,
            on_move=self._hud_moved,
            offset=int(getattr(self.config, "taskbar_hud_offset", 10) or 10),
            offset_y=getattr(self.config, "taskbar_hud_y", None),
        )

        self.tray.set_menu_state(interval=self.config.interval,
                                 show_all=self.config.show_all_python,
                                 autostart=self.config.autostart,
                                 paused=self.config.paused,
                                 hud=bool(self.config.taskbar_hud),
                                 start_hidden=bool(self.config.start_hidden))
        self.tray.start()
        self.monitor.paused = bool(self.config.paused)
        self.monitor.start()
        if self.config.taskbar_hud:
            self.hud.set_enabled(True)

        if start_hidden:
            self.tray.notify("显存监视器", "已在托盘后台运行，左键图标打开窗口。")
        else:
            self.show_window()
        if selftest_hud:
            self.root.after(2500, self._selftest_hud)
        self.root.after(150, self._tick)

    def _hud_moved(self, x: int, y: int) -> None:
        self.config.taskbar_hud_offset = int(x)
        self.config.taskbar_hud_y = int(y)
        self.config.save()

    def _selftest_hud(self) -> None:
        """自动化验证：任务栏指标条的点击是否能召回主窗口。"""
        canvas = self.hud._canvas
        log = logging.getLogger("gpumon")
        if canvas is None:
            log.warning("HUD 自检：指标条未创建")
            return
        canvas.event_generate("<ButtonPress-1>", x=6, y=6, when="now")
        canvas.event_generate("<ButtonRelease-1>", x=6, y=6, when="now")

        def report():
            log.info("HUD 自检：点击后主窗口可见=%s",
                     self.root.state() != "withdrawn")

        self.root.after(800, report)

    def _apply_icon(self) -> None:
        path = os.path.join(data_dir(), "app.ico")
        try:
            if not os.path.exists(path):
                icons.write_window_icon(path)
            self.root.iconbitmap(path)
        except Exception:
            pass

    def _restore_geometry(self) -> None:
        s = self.scale
        win = self.config.window or {}
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = int(win.get("w") or int(1260 * s))
        height = int(win.get("h") or int(820 * s))
        width = max(int(900 * s), min(width, screen_w - 40))
        height = max(int(600 * s), min(height, screen_h - 80))
        x = win.get("x")
        y = win.get("y")
        if x is None or y is None:
            x = (screen_w - width) // 2
            y = max(20, (screen_h - height) // 2 - 20)
        x = max(0, min(int(x), max(0, screen_w - width)))
        y = max(0, min(int(y), max(0, screen_h - height)))
        self._geom = (width, height, x, y)
        self.root.minsize(int(900 * s), int(600 * s))
        logging.getLogger("gpumon").info(
            "geom=%s screen=%sx%s scale=%.2f cfg=%s",
            self._geom, screen_w, screen_h, s, self.config.window)

    def _apply_geometry(self) -> None:
        width, height, x, y = self._geom
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _save_geometry(self) -> None:
        try:
            if self.root.state() == "zoomed" or not self._shown_once:
                return
            geo = self.root.geometry()
            size, _, pos = geo.partition("+")
            width, height = (int(v) for v in size.split("x"))
            x, y = (int(v) for v in pos.split("+")) if pos else (None, None)
            self.config.window = {"w": width, "h": height, "x": x, "y": y}
            self.config.save()
        except Exception:
            pass

    def _apply_dark_frame(self) -> None:
        try:
            win32.user32.GetParent.argtypes = [wt.HWND]
            win32.user32.GetParent.restype = wt.HWND
            child = self.root.winfo_id()
            parent = win32.user32.GetParent(wt.HWND(child))
            win32.enable_dark_titlebar(int(parent or child))
        except Exception:
            pass

    def show_window(self) -> None:
        try:
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        self.root.deiconify()
        self._apply_geometry()
        self.root.update_idletasks()
        self._shown_once = True
        self._apply_dark_frame()
        try:
            self.root.attributes("-alpha", 1.0)
        except tk.TclError:
            pass
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(60, lambda: self.root.attributes("-topmost", False))
        except tk.TclError:
            pass
        if self._latest:
            self.window.render(self._latest, force=True)
            self._rendered = self._latest
        self.root.focus_force()

    def hide_window(self) -> None:
        self._save_geometry()
        if not self.tray.registered:
            self.root.iconify()
            self.flash_status("托盘图标不可用（可能被系统策略阻止），已最小化窗口", warn=True)
            return
        self.root.withdraw()
        if self.config.first_hide_hint:
            self.tray.notify("已最小化到托盘", "显存监视器仍在后台监测：左键托盘图标即可再次打开。")
            self.config.first_hide_hint = False
            self.config.save()

    # -- 采样回调（采样线程） ---------------------------------------------
    def _on_snapshot(self, snap: Snapshot) -> None:
        self._latest = snap
        self.tray.set_status(snap.max_pressure, tray_mod.tooltip_for(snap), self.monitor.paused)

    # -- 主线程 tick -------------------------------------------------------
    def _tick(self) -> None:
        try:
            while True:
                command = self.commands.get_nowait()
                self._handle_command(command)
        except queue.Empty:
            pass
        snap = self._latest
        if snap is not None and snap is not self._rendered and self.root.state() != "withdrawn":
            self.window.render(snap)
            self._rendered = snap
        if snap is not None and snap is not self._hud_snap:
            self.hud.update(snap)
            self._hud_snap = snap
        if self._flash_until and self._flash_until < _now():
            self._flash_until = 0.0
            self.window.right_status.configure(fg="#5f6779")
            if snap is not None:
                self.window.render(snap)
        self.root.after(180, self._tick)

    # -- 托盘命令 ----------------------------------------------------------
    def _tray_command(self, command: str) -> None:
        self.commands.put(command)

    def _handle_command(self, command: str) -> None:
        if command == tray_mod.CMD_SHOW:
            self.show_window()
        elif command == tray_mod.CMD_REFRESH:
            self.refresh()
        elif command == tray_mod.CMD_TOGGLE_PAUSE:
            self.toggle_pause()
        elif command.startswith(tray_mod.CMD_INTERVAL):
            try:
                self.set_interval(float(command.split(":", 1)[1]))
            except ValueError:
                pass
        elif command == tray_mod.CMD_TOGGLE_ALLPY:
            self.toggle_show_all()
        elif command == tray_mod.CMD_TOGGLE_HUD:
            self.toggle_hud()
        elif command == tray_mod.CMD_TOGGLE_AUTOSTART:
            self.toggle_autostart()
        elif command == tray_mod.CMD_TOGGLE_START_HIDDEN:
            self.toggle_start_hidden()
        elif command == tray_mod.CMD_QUIT:
            self.quit()

    # -- 动作 --------------------------------------------------------------
    def refresh(self) -> None:
        self.monitor.wake()

    def set_interval(self, seconds: float) -> None:
        self.config.interval = seconds
        self.config.save()
        self.monitor.wake()
        self.window.set_interval_label()
        self.tray.set_menu_state(interval=seconds)
        self.flash_status(f"采样间隔已设为 {seconds:g} 秒")

    def toggle_pause(self) -> None:
        paused = not self.monitor.paused
        self.monitor.set_paused(paused)
        self.config.paused = paused
        self.config.save()
        self.tray.set_menu_state(paused=paused)
        if paused:
            self.tray.set_status(None, "显存监视器 · 已暂停", True)
        self.flash_status("已暂停采样" if paused else "已恢复采样")

    def toggle_show_all(self) -> None:
        self.config.show_all_python = not self.config.show_all_python
        self.config.save()
        self.window.update_toggles()
        self.tray.set_menu_state(show_all=self.config.show_all_python)
        self.refresh()
        self.flash_status("已显示全部 Python 进程" if self.config.show_all_python
                          else "仅显示占用显存的进程")

    def toggle_hud(self) -> None:
        enabled = not bool(self.config.taskbar_hud)
        self.config.taskbar_hud = enabled
        self.config.save()
        self.hud.set_enabled(enabled)
        self.tray.set_menu_state(hud=enabled)
        self.window.update_toggles()
        if enabled and self._latest is not None:
            self.hud.update(self._latest)
            self._hud_snap = self._latest
        self.flash_status("已在任务栏显示关键指标" if enabled else "已隐藏任务栏关键指标")

    def toggle_autostart(self) -> None:
        want = not self.config.autostart
        ok = self.config.set_autostart(want)
        self.tray.set_menu_state(autostart=self.config.autostart)
        self.window.update_toggles()
        self.flash_status("已开启开机自启" if self.config.autostart else "已关闭开机自启",
                          warn=not ok)

    def toggle_start_hidden(self) -> None:
        self.config.start_hidden = not bool(self.config.start_hidden)
        self.config.save()
        self.tray.set_menu_state(start_hidden=self.config.start_hidden)
        self.window.update_toggles()
        self.flash_status("下次启动直接进托盘（不弹窗口）" if self.config.start_hidden
                          else "下次启动会打开主窗口")

    def open_data_dir(self) -> None:
        os.startfile(self.config_dir)  # noqa: S606

    def open_log(self) -> None:
        path = os.path.join(self.config_dir, "monitor.log")
        if os.path.exists(path):
            os.startfile(path)  # noqa: S606
        else:
            self.flash_status("暂无日志", warn=True)

    def flash_status(self, text: str, warn: bool = False) -> None:
        self._flash_until = _now() + 3.0
        self.window.right_status.configure(text=("⚠ " if warn else "✓ ") + text,
                                           fg=("#f2b63c" if warn else "#35d399"))

    def quit(self) -> None:
        self._save_geometry()
        try:
            self.monitor.stop()
            self.tray.stop()
        finally:
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def _now() -> float:
    import time
    return time.time()


def _acquire_single_instance():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wt.HANDLE
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    # CreateMutexW 只在"已存在"时设置 ERROR_ALREADY_EXISTS，成功时不会清零，
    # 因此必须先把线程错误码置 0，否则可能误判为已有实例。
    ctypes.windll.kernel32.SetLastError(0)
    handle = kernel32.CreateMutexW(None, False, APP_MUTEX)
    if not handle:
        return None
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(wt.HANDLE(handle))
        return None
    return handle


def _activate_existing_instance() -> bool:
    """让已在运行的实例把窗口显示出来（第二个实例只做这件事然后退出）。"""
    try:
        user32 = win32.user32
        user32.FindWindowW.restype = wt.HWND
        user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
        user32.RegisterWindowMessageW.restype = wt.UINT
        user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
        user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        hwnd = user32.FindWindowW(tray_mod.TrayIcon.CLASS_NAME, None)
        if not hwnd:
            return False
        message = user32.RegisterWindowMessageW("GpuMonActivateWindow")
        user32.PostMessageW(wt.HWND(hwnd), message, 0, 0)
        return True
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    selftest_hud = "--selftest-hud" in argv
    selftest_tray = "--selftest-tray" in argv
    verbose = "--verbose" in argv or "-v" in argv

    setup_logging(verbose)
    if selftest_tray:
        try:
            return _selftest_tray()
        except Exception:
            traceback.print_exc()
            return 1

    _cfg = Config.load()
    force_show = "--show" in argv or "--window" in argv
    start_hidden = ("--hidden" in argv or "--tray" in argv
                    or (bool(getattr(_cfg, "start_hidden", False)) and not force_show))

    log = logging.getLogger("gpumon")
    log.info("启动：hidden=%s verbose=%s python=%s", start_hidden, verbose, sys.executable)
    mutex = _acquire_single_instance()
    if mutex is None:
        if _activate_existing_instance():
            log.info("已有实例在运行，已请求其显示窗口")
            return 0
        log.info("已有实例在运行，本次退出")
        try:
            import tkinter.messagebox as mb

            root = tk.Tk()
            root.withdraw()
            mb.showinfo("显存监视器", "程序已在运行（可查看系统托盘图标）。")
            root.destroy()
        except Exception:
            pass
        return 0

    win32.set_dpi_awareness()
    try:
        app = App(start_hidden=start_hidden, selftest_hud=selftest_hud)
        log.info("窗口已创建，scale=%.2f", app.scale)
    except Exception:
        log.exception("启动失败")
        try:
            tk.messagebox.showerror("显存监视器", "启动失败：\n" + traceback.format_exc())
        except Exception:
            pass
        return 1
    try:
        app.run()
    finally:
        log.info("主循环结束")
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.CloseHandle(wt.HANDLE(mutex))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

