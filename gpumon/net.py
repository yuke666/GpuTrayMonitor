"""全网卡上下行总速率。

数据源：PDH 计数器 ``\\Network Interface(*)\\Bytes Received/sec`` 与
``\\Network Interface(*)\\Bytes Sent/sec``。Windows 自己已经算好了每秒字节数，
这里只需要把所有接口实例的值相加，因此不需要维护两次采样的差值。

回环（Loopback）不计入，否则本机内部流量会让数字虚高。
接口断开时其计数器为 0，不影响求和。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging

from .gpu import PdhError, PdhSampler, pdh

log = logging.getLogger("gpumon.net")

LOOPBACK_HINTS = ("loopback", "环回", "回环")


class NetSampler:
    """常驻的 PDH 查询；每次 sample() 采一次所有接口的速率。"""

    PATHS = (
        r"\Network Interface(*)\Bytes Received/sec",
        r"\Network Interface(*)\Bytes Sent/sec",
    )

    def __init__(self) -> None:
        self.ok = False
        self.error = ""
        self._query = wt.HANDLE()
        self._counters: list = []
        try:
            if pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) != 0:
                raise PdhError("PdhOpenQuery 失败")
            for path in self.PATHS:
                handle = wt.HANDLE()
                if pdh.PdhAddEnglishCounterW(self._query, path, 0, ctypes.byref(handle)) != 0:
                    continue
                self._counters.append(handle)
            if len(self._counters) < 2:
                raise PdhError("系统不支持 Network Interface 计数器")
            # 速率计数器需要两次采集才有值，这里先预热一次
            pdh.PdhCollectQueryData(self._query)
            self.ok = True
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self.close()

    def close(self) -> None:
        self.ok = False
        if self._query:
            try:
                pdh.PdhCloseQuery(self._query)
            except Exception:
                pass
        self._query = wt.HANDLE()
        self._counters = []

    @staticmethod
    def _sum(counter) -> float:
        """把某个 wildcard 计数器的所有实例加总（跳过回环）。"""
        try:
            data = PdhSampler._array(counter)
        except Exception:
            log.debug("读取网卡计数器失败", exc_info=True)
            return 0.0
        total = 0.0
        for name, value in data.items():
            lowered = name.lower()
            if any(hint in lowered for hint in LOOPBACK_HINTS):
                continue
            if value > 0:
                total += float(value)
        return total

    def sample(self) -> tuple[float, float]:
        """返回 (上传 B/s, 下载 B/s)。"""
        if not self.ok or len(self._counters) < 2:
            return 0.0, 0.0
        try:
            if pdh.PdhCollectQueryData(self._query) != 0:
                return 0.0, 0.0
            down = self._sum(self._counters[0])
            up = self._sum(self._counters[1])
            return max(0.0, up), max(0.0, down)
        except Exception:
            log.debug("采样网速失败", exc_info=True)
            return 0.0, 0.0
