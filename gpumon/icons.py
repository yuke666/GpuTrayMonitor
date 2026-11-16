"""程序化生成托盘 / 窗口图标（环形仪表盘，无第三方依赖）。

图标表达的信息：圆环 = 显存占用比例，颜色 = 压力等级。
"""
from __future__ import annotations

import math
from functools import lru_cache

from .util import AMBER, GREEN, GREY, RED

_RING_BG = (62, 70, 88)
_CHIP = (116, 124, 145)


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _paint(size: int, pct: float | None, color: str, ss: int) -> bytearray:
    n = size * ss
    cx = cy = n / 2.0
    r_out = n * 0.47
    thickness = max(1.55 * ss, r_out * 0.36)
    r_in = r_out - thickness
    circle = _hex_to_rgb(color)
    chip_w = n * 0.115
    chip_h = max(1.0 * ss, n * 0.05)
    buf = bytearray(n * n * 4)
    two_pi = 2.0 * math.pi
    limit = (pct or 0.0) / 100.0
    for y in range(n):
        dy = y + 0.5 - cy
        for x in range(n):
            dx = x + 0.5 - cx
            dist = math.hypot(dx, dy)
            rgb = None
            if r_in <= dist <= r_out:
                angle = math.atan2(dx, -dy) % two_pi
                filled = pct is not None and (angle / two_pi) <= limit
                rgb = circle if filled else _RING_BG
            elif abs(dx) <= chip_w and abs(dy) <= chip_h:
                rgb = _CHIP
            if rgb is None:
                continue
            idx = (y * n + x) * 4
            buf[idx] = rgb[2]
            buf[idx + 1] = rgb[1]
            buf[idx + 2] = rgb[0]
            buf[idx + 3] = 255
    return buf


def _downsample(buf: bytearray, n: int, ss: int) -> bytes:
    size = n // ss
    out = bytearray(size * size * 4)
    samples = ss * ss
    for y in range(size):
        for x in range(size):
            r = g = b = a = 0
            base_y = y * ss
            base_x = x * ss
            for sy in range(ss):
                row = ((base_y + sy) * n + base_x) * 4
                for sx in range(ss):
                    idx = row + sx * 4
                    alpha = buf[idx + 3]
                    if alpha:
                        r += buf[idx + 2] * alpha
                        g += buf[idx + 1] * alpha
                        b += buf[idx] * alpha
                        a += alpha
            o = (y * size + x) * 4
            if a:
                out[o] = min(255, b // a)
                out[o + 1] = min(255, g // a)
                out[o + 2] = min(255, r // a)
            out[o + 3] = min(255, a // samples)
    return bytes(out)


@lru_cache(maxsize=64)
def render(size: int, color: str, pct_bucket: int | None) -> bytes:
    """返回 BGRA（自上而下、非预乘 alpha）像素。pct_bucket 为 None 表示无数据。"""
    ss = 4 if size <= 32 else 3
    pct = None if pct_bucket is None else float(pct_bucket)
    return _downsample(_paint(size, pct, color, ss), size * ss, ss)


def color_for(pct: float | None) -> str:
    if pct is None:
        return GREY
    if pct >= 88:
        return RED
    if pct >= 65:
        return AMBER
    return GREEN


def bucket(pct: float | None) -> int | None:
    if pct is None:
        return None
    return int(max(0.0, min(100.0, pct)) // 5) * 5


def write_window_icon(path: str) -> None:
    from .win32 import write_ico

    images = {size: render(size, "#4f8cff", 70) for size in (16, 24, 32, 48)}
    write_ico(path, images)
