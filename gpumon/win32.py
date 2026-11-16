"""Win32 底层封装：DPI、暗色标题栏、图标生成、进程句柄与 PEB 读取。

全部使用标准库 ctypes，不依赖 pywin32。仅供 gpumon 内部使用。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import struct

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001

DIB_RGB_COLORS = 0
BI_RGB = 0

# --------------------------------------------------------------------------- #
# 结构体
# --------------------------------------------------------------------------- #


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wt.BOOL),
        ("xHotspot", wt.DWORD),
        ("yHotspot", wt.DWORD),
        ("hbmMask", wt.HBITMAP),
        ("hbmColor", wt.HBITMAP),
    ]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("MaximumLength", ctypes.c_ushort),
        ("Buffer", ctypes.c_void_p),
    ]


class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Reserved1", ctypes.c_void_p),
        ("PebBaseAddress", ctypes.c_void_p),
        ("Reserved2_0", ctypes.c_void_p),
        ("Reserved2_1", ctypes.c_void_p),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
    ]


# --------------------------------------------------------------------------- #
# 函数签名
# --------------------------------------------------------------------------- #
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = [
    wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
    ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD,
]
gdi32.CreateBitmap.restype = wt.HBITMAP
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wt.UINT, wt.UINT, ctypes.c_void_p]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
user32.CreateIconIndirect.restype = wt.HICON
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.DestroyIcon.argtypes = [wt.HICON]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.GetDpiForWindow.argtypes = [wt.HWND]
user32.GetDpiForWindow.restype = wt.UINT
user32.RegisterWindowMessageW.restype = wt.UINT
user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
user32.PostQuitMessage.argtypes = [ctypes.c_int]

kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)
]
kernel32.GetProcessTimes.restype = wt.BOOL
kernel32.GetProcessTimes.argtypes = [
    wt.HANDLE, ctypes.POINTER(wt.FILETIME), ctypes.POINTER(wt.FILETIME),
    ctypes.POINTER(wt.FILETIME), ctypes.POINTER(wt.FILETIME),
]
kernel32.ReadProcessMemory.restype = wt.BOOL
kernel32.ReadProcessMemory.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
psapi = ctypes.WinDLL("psapi", use_last_error=True)
psapi.EnumProcesses.restype = wt.BOOL
psapi.EnumProcesses.argtypes = [
    ctypes.POINTER(wt.DWORD), wt.DWORD, ctypes.POINTER(wt.DWORD)
]
ntdll.NtQueryInformationProcess.restype = ctypes.c_long
ntdll.NtQueryInformationProcess.argtypes = [
    wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.ULONG, ctypes.POINTER(wt.ULONG)
]

# CommandLineToArgvW：按 Windows 规则切分命令行
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shell32.CommandLineToArgvW.restype = ctypes.POINTER(wt.LPWSTR)
shell32.CommandLineToArgvW.argtypes = [wt.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
kernel32.LocalFree.argtypes = [wt.HLOCAL]


# --------------------------------------------------------------------------- #
# DPI / 窗口
# --------------------------------------------------------------------------- #
def set_dpi_awareness() -> None:
    """在创建任何窗口前调用，避免高分屏模糊。"""
    try:
        ctx = ctypes.c_void_p(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if user32.SetProcessDpiAwarenessContext(ctx):
            return
    except Exception:
        pass
    try:
        shcore = ctypes.WinDLL("shcore")
        shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def enable_dark_titlebar(hwnd: int) -> None:
    """Win10 1809+ / Win11 暗色标题栏，失败静默。"""
    try:
        value = ctypes.c_int(1)
        dwm = ctypes.WinDLL("dwmapi")
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE
            if dwm.DwmSetWindowAttribute(
                wt.HWND(hwnd), attr, ctypes.byref(value), ctypes.sizeof(value)
            ) == 0:
                return
    except Exception:
        pass


def get_dpi_for_window(hwnd: int) -> int:
    try:
        dpi = user32.GetDpiForWindow(wt.HWND(hwnd))
        return dpi or 96
    except Exception:
        return 96


SM_CXSMICON = 49


def get_system_dpi() -> int:
    try:
        user32.GetDpiForSystem.restype = wt.UINT
        return int(user32.GetDpiForSystem()) or 96
    except Exception:
        return 96


def small_icon_size(dpi: int | None = None) -> int:
    """任务栏小图标实际像素尺寸（随 DPI 变化）。"""
    dpi = dpi or get_system_dpi()
    try:
        user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wt.UINT]
        user32.GetSystemMetricsForDpi.restype = ctypes.c_int
        size = user32.GetSystemMetricsForDpi(SM_CXSMICON, dpi)
        if size:
            return int(size)
    except Exception:
        pass
    size = user32.GetSystemMetrics(SM_CXSMICON)
    return int(size) or 16


# --------------------------------------------------------------------------- #
# 图标：由 BGRA 像素生成 HICON
# --------------------------------------------------------------------------- #
def make_hicon(bgra: bytes, width: int, height: int) -> int:
    if len(bgra) != width * height * 4:
        raise ValueError("bgra size mismatch")

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height  # 自上而下
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB

    bits = ctypes.c_void_p()
    hbm_color = gdi32.CreateDIBSection(
        None, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
    )
    if not hbm_color:
        raise ctypes.WinError(ctypes.get_last_error())
    ctypes.memmove(bits, bgra, len(bgra))

    stride = (width + 7) // 8
    mask = b"\x00" * (stride * height)
    hbm_mask = gdi32.CreateBitmap(width, height, 1, 1, mask)
    if not hbm_mask:
        gdi32.DeleteObject(hbm_color)
        raise ctypes.WinError(ctypes.get_last_error())

    info = ICONINFO()
    info.fIcon = True
    info.hbmMask = hbm_mask
    info.hbmColor = hbm_color
    hicon = user32.CreateIconIndirect(ctypes.byref(info))
    gdi32.DeleteObject(hbm_mask)
    gdi32.DeleteObject(hbm_color)
    if not hicon:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(hicon)


def destroy_icon(hicon: int) -> None:
    if hicon:
        user32.DestroyIcon(wt.HICON(hicon))


def write_ico(path: str, images: dict[int, bytes]) -> None:
    """把 {size: bgra} 写成 .ico（32bpp BMP 条目）。"""
    sizes = sorted(images)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    entries = b""
    payload = b""
    offset = 6 + 16 * len(sizes)
    for size in sizes:
        data = images[size]
        row = size * 4
        flipped = b"".join(data[y * row:(y + 1) * row] for y in range(size - 1, -1, -1))
        mask_stride = ((size + 31) // 32) * 4
        mask = b"\x00" * (mask_stride * size)
        bmp_header = struct.pack(
            "<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, len(flipped) + len(mask), 0, 0, 0, 0
        )
        blob = bmp_header + flipped + mask
        entries += struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        payload += blob
    with open(path, "wb") as fh:
        fh.write(header + entries + payload)


# --------------------------------------------------------------------------- #
# 进程
# --------------------------------------------------------------------------- #
def enum_pids() -> list[int]:
    size = 4096
    while True:
        buf = (wt.DWORD * size)()
        needed = wt.DWORD()
        if not psapi.EnumProcesses(buf, ctypes.sizeof(buf), ctypes.byref(needed)):
            return []
        count = needed.value // ctypes.sizeof(wt.DWORD)
        if count < size:
            return [buf[i] for i in range(count) if buf[i]]
        size *= 2


def open_process(pid: int,
                 access: int = PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_QUERY_INFORMATION
                 | PROCESS_VM_READ):
    """默认带上 PROCESS_QUERY_INFORMATION：读取 PEB 必需（仅 LIMITED 不够）。"""
    return kernel32.OpenProcess(access, False, pid)


def close_handle(handle) -> None:
    if handle:
        kernel32.CloseHandle(handle)


def query_image_name(handle) -> str:
    size = wt.DWORD(1024)
    buf = ctypes.create_unicode_buffer(size.value)
    if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
        return buf.value
    return ""


def get_create_time(handle) -> float:
    created, exited, kernel, user = (wt.FILETIME() for _ in range(4))
    if not kernel32.GetProcessTimes(
        handle, ctypes.byref(created), ctypes.byref(exited),
        ctypes.byref(kernel), ctypes.byref(user)
    ):
        return 0.0
    ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
    return ticks / 1e7 - 11644473600.0  # FILETIME -> unix


def read_memory(handle, address: int, size: int) -> bytes | None:
    if not address or size <= 0:
        return None
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    if not kernel32.ReadProcessMemory(
        wt.HANDLE(handle), ctypes.c_void_p(address), buf, size, ctypes.byref(got)
    ):
        return None
    return buf.raw[: got.value]


def basic_info(handle) -> PROCESS_BASIC_INFORMATION | None:
    pbi = PROCESS_BASIC_INFORMATION()
    ret = wt.ULONG(0)
    rc = ntdll.NtQueryInformationProcess(
        wt.HANDLE(handle), 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(ret)
    )
    if rc != 0:
        return None
    return pbi


def is_wow64(handle) -> bool:
    """目标进程是否为 32 位（WOW64）。"""
    try:
        fn = kernel32.IsWow64Process2
        fn.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_ushort), ctypes.POINTER(ctypes.c_ushort)]
        machine = ctypes.c_ushort(0)
        native = ctypes.c_ushort(0)
        if fn(wt.HANDLE(handle), ctypes.byref(machine), ctypes.byref(native)):
            return machine.value != 0
    except Exception:
        pass
    try:
        wow = wt.BOOL()
        if kernel32.IsWow64Process(wt.HANDLE(handle), ctypes.byref(wow)):
            return bool(wow.value)
    except Exception:
        pass
    return False


def parent_pid(handle) -> int:
    pbi = basic_info(handle)
    if not pbi:
        return 0
    return int(pbi.InheritedFromUniqueProcessId or 0)


# --------------------------------------------------------------------------- #
# PEB：命令行 / 工作目录 / 环境变量
# --------------------------------------------------------------------------- #
# RTL_USER_PROCESS_PARAMETERS 偏移
_LAYOUT = {
    64: {  # x64
        "params": 0x20,
        "image": 0x60,
        "cmdline": 0x70,
        "cwd": 0x38,
        "env": 0x80,
        "env_size": 0x3F0,
        "ptr": 8,
    },
    32: {  # WOW64 进程
        "params": 0x10,
        "image": 0x38,
        "cmdline": 0x40,
        "cwd": 0x24,
        "env": 0x48,
        "env_size": 0x290,
        "ptr": 4,
    },
}


def _read_ustr(handle, address: int, ptr_size: int) -> str:
    # UNICODE_STRING: USHORT Length, USHORT MaximumLength, WCHAR* Buffer
    # x64 有 4 字节填充，Buffer 在 +8；x86 在 +4
    buffer_offset = 8 if ptr_size == 8 else 4
    raw = read_memory(handle, address, buffer_offset + ptr_size)
    if not raw or len(raw) < buffer_offset + ptr_size:
        return ""
    length = struct.unpack_from("<H", raw, 0)[0]
    if not length or length > 0x10000:
        return ""
    buf_ptr = struct.unpack_from("<Q" if ptr_size == 8 else "<I", raw, buffer_offset)[0]
    data = read_memory(handle, buf_ptr, length)
    if not data:
        return ""
    return data.decode("utf-16-le", "replace")


def read_process_details(handle) -> dict:
    """读取命令行、工作目录、环境变量（尽力而为，失败返回空值）。"""
    out: dict = {"cmdline": "", "cwd": "", "env": {}, "env_count": 0}
    pbi = basic_info(handle)
    if not pbi or not pbi.PebBaseAddress:
        return out
    ptr_size = 4 if is_wow64(handle) else 8
    layout = _LAYOUT[ptr_size * 8]

    raw = read_memory(handle, pbi.PebBaseAddress + layout["params"], ptr_size)
    if not raw:
        return out
    params = struct.unpack_from("<I" if ptr_size == 4 else "<Q", raw, 0)[0]
    if not params:
        return out

    out["cmdline"] = _read_ustr(handle, params + layout["cmdline"], ptr_size)
    out["cwd"] = _read_ustr(handle, params + layout["cwd"], ptr_size)

    raw = read_memory(handle, params + layout["env"], ptr_size)
    if raw:
        env_ptr = struct.unpack_from("<I" if ptr_size == 4 else "<Q", raw, 0)[0]
        raw = read_memory(handle, params + layout["env_size"], ptr_size)
        env_size = struct.unpack_from("<I" if ptr_size == 4 else "<Q", raw, 0)[0] if raw else 0
        if env_ptr and 0 < env_size <= 1 << 21:
            blob = read_memory(handle, env_ptr, env_size)
            if blob:
                text = blob.decode("utf-16-le", "replace")
                env = {}
                for part in text.split("\0"):
                    if "=" in part:
                        key, _, value = part.partition("=")
                        if key:
                            env[key] = value
                out["env"] = env
                out["env_count"] = len(env)
    return out


def split_cmdline(cmdline: str, exe: str = "") -> list[str]:
    """按 Windows 规则切分命令行；若 exe 未加引号带空格则先剥离。"""
    if not cmdline:
        return []
    text = cmdline.strip()
    if exe and len(text) > len(exe) and text.lower().startswith(exe.lower()):
        rest = text[len(exe):].strip()
        return [exe] + _argv(rest)
    args = _argv(text)
    if args and exe and os.path.basename(args[0]).lower() != os.path.basename(exe).lower():
        return [exe] + args
    return args


def _argv(s: str) -> list[str]:
    if not s:
        return []
    argc = ctypes.c_int(0)
    ptr = shell32.CommandLineToArgvW(s, ctypes.byref(argc))
    if not ptr:
        return s.split()
    try:
        return [ptr[i] for i in range(argc.value)]
    finally:
        kernel32.LocalFree(ctypes.cast(ptr, wt.HLOCAL))
