"""结束正在运行的显存监视器实例（测试/脚本用）。

    python tools/kill_instance.py
"""
import ctypes
import ctypes.wintypes as wt

u = ctypes.WinDLL("user32", use_last_error=True)
k = ctypes.WinDLL("kernel32", use_last_error=True)
u.FindWindowW.restype = wt.HWND
u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
k.OpenProcess.restype = wt.HANDLE


def kill(pid: int) -> bool:
    handle = k.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if not handle:
        return False
    try:
        return bool(k.TerminateProcess(handle, 0))
    finally:
        k.CloseHandle(handle)


def main() -> int:
    pids = set()
    for title in ("GpuMonHud", None):
        for cls in ("TkTopLevel", "GpuMonTrayWindow"):
            hwnd = u.FindWindowW(cls, title)
            if not hwnd:
                continue
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
            if pid.value:
                pids.add(pid.value)
    for pid in sorted(pids):
        print("kill", pid, kill(pid))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
