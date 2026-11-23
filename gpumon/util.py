"""格式化小工具。"""
from __future__ import annotations

import os
import time

GREEN = "#35d399"
IGPU = "#a78bfa"     # 核显主题色（蓝紫，与独显绿、告警琥珀/红均不冲突）
AMBER = "#f2b63c"
RED = "#f2555a"
BLUE = "#4f8cff"
GREY = "#6b7385"

SERIES_COLORS = (GREEN, IGPU, BLUE, AMBER)


def series_color(index: int) -> str:
    """按显卡序号取固定的系列颜色（用于曲线、左侧色条）。"""
    return SERIES_COLORS[index % len(SERIES_COLORS)]


def fmt_mib(mib: float) -> str:
    if mib is None or mib <= 0:
        return "0 MB"
    if mib >= 1024:
        return f"{mib / 1024:.2f} GB"
    if mib >= 100:
        return f"{mib:.0f} MB"
    return f"{mib:.1f} MB"


def fmt_speed(bps: float) -> str:
    """每秒字节数 → 便于塞进任务栏的短字符串（不带空格，单位 K/M/G）。"""
    value = float(bps or 0.0)
    if value < 1024:
        return f"{value:.0f}B"
    if value < 1024 ** 2:
        return f"{value / 1024:.0f}K"
    if value < 1024 ** 3:
        text = f"{value / 1024 ** 2:.1f}"
        return (text[:-2] if text.endswith(".0") else text) + "M"
    text = f"{value / 1024 ** 3:.1f}"
    return (text[:-2] if text.endswith(".0") else text) + "G"


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {sec:02d} 秒"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} 小时 {minutes:02d} 分"
    days, hours = divmod(hours, 24)
    return f"{days} 天 {hours} 小时"


def fmt_clock(ts: float) -> str:
    try:
        return time.strftime("%H:%M:%S", time.localtime(ts))
    except Exception:
        return "--:--:--"


def fmt_datetime(ts: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:
        return "-"


def shorten_path(path: str, limit: int = 60) -> str:
    if not path or len(path) <= limit:
        return path
    parts = [p for p in path.replace("/", "\\").split("\\") if p]
    if len(parts) <= 2:
        return path[: limit - 1] + "…"
    tail = "\\".join(parts[-2:])
    drive = parts[0]
    head = f"{drive}\\…\\"
    if len(head) + len(tail) > limit:
        return "…\\" + parts[-1][: max(8, limit - 2)]
    return head + tail


def pressure_color(pct: float) -> str:
    if pct >= 88:
        return RED
    if pct >= 65:
        return AMBER
    return GREEN


def pressure_icon_color(pct: float | None) -> str:
    if pct is None:
        return GREY
    return pressure_color(pct)


def tail_name(path: str) -> str:
    return os.path.basename(path.rstrip("\\/")) or path
