"""Watch for the "Open File - Security Warning" dialog and record what fired it.

Polls visible #32770 windows whose title contains 安全警告, extracts the file
path shown in the dialog, then records the file's alternate data streams,
signature state and timestamps into a log file.

Usage:  pythonw tools/watch_dialog.py --log <path> --seconds 900
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.FindWindowW.restype = wt.HWND
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.EnumChildWindows.argtypes = [wt.HWND, ctypes.c_void_p, wt.LPARAM]

ENUM_PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


class FIND_STREAM_DATA(ctypes.Structure):
    _fields_ = [("StreamSize", ctypes.c_longlong), ("StreamName", wt.WCHAR * 296)]


kernel32.FindFirstStreamW.restype = wt.HANDLE
kernel32.FindFirstStreamW.argtypes = [
    wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(FIND_STREAM_DATA), wt.DWORD,
]
kernel32.FindNextStreamW.argtypes = [wt.HANDLE, ctypes.POINTER(FIND_STREAM_DATA)]
kernel32.FindClose.argtypes = [wt.HANDLE]


def streams(path: str) -> list[str]:
    out: list[str] = []
    data = FIND_STREAM_DATA()
    handle = kernel32.FindFirstStreamW(path, 0, ctypes.byref(data), 0)
    if not handle or handle == ctypes.c_void_p(-1).value:
        return out
    try:
        while True:
            out.append(data.StreamName)
            if not kernel32.FindNextStreamW(handle, ctypes.byref(data)):
                break
    finally:
        kernel32.FindClose(handle)
    return out


def window_text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def child_texts(hwnd: int) -> list[str]:
    items: list[str] = []

    def _cb(child, _lparam):
        text = window_text(child).strip()
        if text:
            items.append(text)
        return True

    user32.EnumChildWindows(wt.HWND(hwnd), ENUM_PROC(_cb), 0)
    return items


def security_dialogs() -> list[tuple[int, int, list[str]]]:
    found = []

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if class_name(hwnd) != "#32770":
            return True
        title = window_text(hwnd)
        if "安全警告" not in title and "Security Warning" not in title:
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append((hwnd, pid.value, child_texts(hwnd)))
        return True

    user32.EnumWindows(ENUM_PROC(_cb), 0)
    return found


def probe(path: str) -> list[str]:
    lines = [f"    path      : {path}"]
    if not os.path.exists(path):
        lines.append("    exists    : NO")
        return lines
    lines.append(f"    exists    : yes")
    lines.append(f"    streams   : {', '.join(streams(path))}")
    try:
        st = os.stat(path)
        lines.append(f"    size      : {st.st_size}")
        lines.append(f"    modified  : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))}")
    except OSError as exc:
        lines.append(f"    stat      : {exc}")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"$s = Get-AuthenticodeSignature -LiteralPath '{path}'; "
             "Write-Output ($s.Status.ToString() + ' | ' + ($(if ($s.SignerCertificate) { $s.SignerCertificate.Subject } else { 'none' })))"],
            capture_output=True, text=True, timeout=20,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        lines.append(f"    signature : {result.stdout.strip()}")
    except Exception as exc:  # pragma: no cover - best effort
        lines.append(f"    signature : <error {exc}>")
    for extra in ("Zone.Identifier",):
        target = path + ":" + extra
        try:
            if os.path.exists(target):
                with open(target, "r", encoding="utf-8", errors="replace") as fh:
                    lines.append(f"    {extra}:\n" + "".join("      " + ln for ln in fh.read().splitlines(True)))
        except OSError:
            pass
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    parser.add_argument("--seconds", type=int, default=900)
    args = parser.parse_args()

    deadline = time.time() + args.seconds
    seen: set[int] = set()

    with open(args.log, "a", encoding="utf-8") as log:
        log.write(f"\n=== watcher started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        log.flush()
        while time.time() < deadline:
            for hwnd, pid, items in security_dialogs():
                if hwnd in seen:
                    continue
                seen.add(hwnd)
                log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] dialog hwnd={hwnd} pid={pid}\n")
                for item in items:
                    log.write(f"    text      : {item}\n")
                candidates = [
                    item for item in items
                    if item.lower().endswith(".exe") or item.lower().endswith(".vbs")
                    or item.lower().endswith(".cmd") or item.lower().endswith(".bat")
                ]
                for cand in candidates:
                    for line in probe(cand):
                        log.write(line + "\n")
                log.flush()
            time.sleep(0.6)

        log.write("=== watcher stopped ===\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
