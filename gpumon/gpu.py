"""GPU 数据采集：NVML（NVIDIA 指标）+ PDH（每进程显存/引擎占用）+ DXGI（适配器识别）。

设计要点
--------
* 显存总量/利用率/温度/功耗：NVML，一次调用几十微秒，适合高频采样。
* 每进程显存：Windows 性能计数器 ``GPU Process Memory``，WDDM 会统计所有
  图形与计算上下文（CUDA、DirectML、游戏都包含），这是任务管理器"专用 GPU
  内存"的同一数据源，因此 python.exe 的 CUDA 占用也能准确归属。
* 适配器与 LUID 的对应关系来自 DXGI，用于把计数器实例映射到具体显卡。
* 若 NVML 不可用（无 NVIDIA 驱动），仍可通过 DXGI + PDH 展示总量与进程。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass

MIB = 1024 * 1024

_instance_re = re.compile(
    r"pid_(\d+)_luid_0x([0-9A-Fa-f]+)_0x([0-9A-Fa-f]+)_phys_(\d+)", re.I
)
_engine_re = re.compile(r"_eng_(\d+)_engtype_", re.I)


@dataclass
class GpuStat:
    index: int
    name: str
    vendor: str
    kind: str = "独显"          # 独显 / 核显
    luid: int = 0
    total_mib: int = 0          # 展示口径：独显=专用显存，核显=共享显存上限
    used_mib: int = 0
    free_mib: int = 0
    dedicated_total_mib: int = 0
    dedicated_used_mib: int = 0
    shared_total_mib: int = 0
    shared_used_mib: int = 0
    util: int = 0
    mem_util: int = 0
    temp: int = -1
    power_w: float = -1.0
    power_limit_w: float = -1.0
    fan: int = -1
    driver: str = ""
    source: str = "nvml"
    pci: str = ""

    @property
    def used_pct(self) -> float:
        return (self.used_mib / self.total_mib * 100.0) if self.total_mib else 0.0

    @property
    def short_name(self) -> str:
        name = self.name.replace("NVIDIA GeForce ", "").replace("NVIDIA ", "")
        name = name.replace("(R)", "").replace("(TM)", "").strip()
        return " ".join(name.split())


@dataclass
class ProcVram:
    pid: int
    dedicated: int = 0      # 专用显存（字节）
    shared: int = 0         # 共享内存（字节）
    util: float = 0.0       # 引擎占用百分比（0-100）
    luid: int = 0


@dataclass
class Adapter:
    luid: int
    desc: str
    vendor_id: int
    device_id: int
    vram_mib: int
    shared_mib: int
    order: int

    @property
    def vendor(self) -> str:
        return {0x10DE: "NVIDIA", 0x8086: "Intel", 0x1002: "AMD", 0x1414: "Microsoft"}.get(
            self.vendor_id, "其它"
        )


# --------------------------------------------------------------------------- #
# NVML
# --------------------------------------------------------------------------- #
class _NvmlMemory(ctypes.Structure):
    _fields_ = [
        ("total", ctypes.c_ulonglong),
        ("free", ctypes.c_ulonglong),
        ("used", ctypes.c_ulonglong),
    ]


class _NvmlMemoryV2(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint),
        ("total", ctypes.c_ulonglong),
        ("reserved", ctypes.c_ulonglong),
        ("free", ctypes.c_ulonglong),
        ("used", ctypes.c_ulonglong),
    ]


class _NvmlUtil(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


def _nvml_version(struct_cls, ver: int) -> int:
    return ctypes.sizeof(struct_cls) | (ver << 24)


class Nvml:
    """最小化 NVML 封装（只覆盖监视器需要的接口）。"""

    def __init__(self) -> None:
        self.ok = False
        self.error = ""
        self.driver = ""
        self._lib = None
        self._handles: list[ctypes.c_void_p] = []
        try:
            lib = None
            for path in (
                os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvml.dll"),
                "nvml.dll",
            ):
                try:
                    lib = ctypes.WinDLL(path)
                    break
                except OSError:
                    continue
            if lib is None:
                raise OSError("nvml.dll 未找到")
            self._lib = lib
            lib.nvmlInit_v2.restype = ctypes.c_int
            if lib.nvmlInit_v2() != 0:
                raise OSError("nvmlInit 失败（驱动过旧或无 NVIDIA 显卡）")
            self.ok = True
            self._setup()
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self.ok = False

    # -- 初始化 ------------------------------------------------------------
    def _setup(self) -> None:
        lib = self._lib
        count = ctypes.c_uint(0)
        if lib.nvmlDeviceGetCount_v2(ctypes.byref(count)) != 0:
            raise OSError("nvmlDeviceGetCount 失败")
        self._handles = []
        for i in range(count.value):
            handle = ctypes.c_void_p()
            if lib.nvmlDeviceGetHandleByIndex_v2(ctypes.c_uint(i), ctypes.byref(handle)) == 0:
                self._handles.append(handle)
        buf = ctypes.create_string_buffer(80)
        if lib.nvmlSystemGetDriverVersion(buf, len(buf)) == 0:
            self.driver = buf.value.decode("ascii", "replace")

    # -- 读取 --------------------------------------------------------------
    def _memory(self, handle) -> tuple[int, int, int]:
        lib = self._lib
        mem = _NvmlMemoryV2(version=_nvml_version(_NvmlMemoryV2, 2))
        try:
            if lib.nvmlDeviceGetMemoryInfo_v2(handle, ctypes.byref(mem)) == 0:
                return mem.total, mem.free, mem.used
        except AttributeError:
            pass
        mem1 = _NvmlMemory()
        if lib.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem1)) == 0:
            return mem1.total, mem1.free, mem1.used
        return 0, 0, 0

    def devices(self) -> list[GpuStat]:
        if not self.ok:
            return []
        lib = self._lib
        out: list[GpuStat] = []
        for index, handle in enumerate(self._handles):
            total, free, used = self._memory(handle)
            name = ctypes.create_string_buffer(128)
            lib.nvmlDeviceGetName(handle, name, len(name))
            util = _NvmlUtil()
            if lib.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(util)) != 0:
                util = _NvmlUtil()
            temp = ctypes.c_int(0)
            lib.nvmlDeviceGetTemperature(handle, 0, ctypes.byref(temp))
            power = ctypes.c_uint(0)
            lib.nvmlDeviceGetPowerUsage(handle, ctypes.byref(power))
            limit = ctypes.c_uint(0)
            try:
                lib.nvmlDeviceGetEnforcedPowerLimit(handle, ctypes.byref(limit))
            except Exception:
                pass
            fan = ctypes.c_uint(0)
            lib.nvmlDeviceGetFanSpeed(handle, ctypes.byref(fan))
            pci = ""
            try:
                class _Pci(ctypes.Structure):
                    _fields_ = [
                        ("busIdLegacy", ctypes.c_char * 16),
                        ("domain", ctypes.c_uint),
                        ("bus", ctypes.c_uint),
                        ("device", ctypes.c_uint),
                        ("pciDeviceId", ctypes.c_uint),
                        ("pciSubSystemId", ctypes.c_uint),
                        ("busId", ctypes.c_char * 32),
                    ]
                info = _Pci()
                if lib.nvmlDeviceGetPciInfo_v3(handle, ctypes.byref(info)) == 0:
                    pci = info.busId.decode("ascii", "replace")
            except Exception:
                pass
            out.append(
                GpuStat(
                    index=index,
                    name=name.value.decode("utf-8", "replace") or f"NVIDIA GPU {index}",
                    vendor="NVIDIA",
                    total_mib=int(total // MIB),
                    used_mib=int(used // MIB),
                    free_mib=int(free // MIB),
                    dedicated_total_mib=int(total // MIB),
                    dedicated_used_mib=int(used // MIB),
                    util=int(util.gpu),
                    mem_util=int(util.memory),
                    temp=int(temp.value) if temp.value else -1,
                    power_w=round(power.value / 1000.0, 1) if power.value else -1.0,
                    power_limit_w=round(limit.value / 1000.0, 1) if limit.value else -1.0,
                    fan=int(fan.value) if fan.value else -1,
                    driver=self.driver,
                    pci=pci,
                )
            )
        return out


# --------------------------------------------------------------------------- #
# DXGI：适配器枚举
# --------------------------------------------------------------------------- #
class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, text: str | None = None):
        super().__init__()
        if text:
            u = uuid.UUID(text)
            self.Data1 = u.time_low
            self.Data2 = u.time_mid
            self.Data3 = u.time_hi_version
            for i, byte in enumerate(u.bytes[8:]):
                self.Data4[i] = byte


class _Luid(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]

    @property
    def value(self) -> int:
        return ((self.HighPart & 0xFFFFFFFF) << 32) | self.LowPart


class _AdapterDesc1(ctypes.Structure):
    _fields_ = [
        ("Description", wt.WCHAR * 128),
        ("VendorId", wt.UINT), ("DeviceId", wt.UINT),
        ("SubSysId", wt.UINT), ("Revision", wt.UINT),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", _Luid),
        ("Flags", wt.UINT),
    ]


def dxgi_adapters() -> list[Adapter]:
    """通过 DXGI 枚举图形适配器（LUID 用于对齐 PDH 计数器实例）。"""
    try:
        dxgi = ctypes.WinDLL("dxgi")
        factory = ctypes.c_void_p()
        iid = GUID("{770aae78-f26f-4dba-a829-253c83d1b387}")  # IDXGIFactory1
        if dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)) != 0:
            return []

        def method(obj, index, restype, *argtypes):
            vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            return ctypes.WINFUNCTYPE(restype, *argtypes)(vtable[index])

        out: list[Adapter] = []
        enum = method(factory, 12, ctypes.c_long, ctypes.c_void_p, ctypes.c_uint,
                      ctypes.POINTER(ctypes.c_void_p))
        desc_fn = None
        release = method(factory, 2, ctypes.c_ulong, ctypes.c_void_p)
        for i in range(16):
            adapter = ctypes.c_void_p()
            if enum(factory, i, ctypes.byref(adapter)) != 0 or not adapter:
                break
            desc = _AdapterDesc1()
            desc_fn = method(adapter, 10, ctypes.c_long, ctypes.c_void_p,
                             ctypes.POINTER(_AdapterDesc1))
            if desc_fn(adapter, ctypes.byref(desc)) == 0:
                out.append(
                    Adapter(
                        luid=desc.AdapterLuid.value,
                        desc=desc.Description,
                        vendor_id=desc.VendorId,
                        device_id=desc.DeviceId,
                        vram_mib=int(desc.DedicatedVideoMemory // MIB),
                        shared_mib=int(desc.SharedSystemMemory // MIB),
                        order=i,
                    )
                )
            method(adapter, 2, ctypes.c_ulong, ctypes.c_void_p)(adapter)
        release(factory)
        return out
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# PDH：每进程显存 / 引擎占用
# --------------------------------------------------------------------------- #
pdh = ctypes.WinDLL("pdh.dll")
PDH_FMT_DOUBLE = 0x00000200
PDH_MORE_DATA = 0x800007D2
PDH_INVALID_DATA = 0xC0000BC6
PDH_CSTATUS_VALID_DATA = 0x00000000


class _FmtCounterValue(ctypes.Structure):
    _fields_ = [("CStatus", wt.DWORD), ("doubleValue", ctypes.c_double)]


class _CounterItem(ctypes.Structure):
    _fields_ = [("szName", wt.LPWSTR), ("FmtValue", _FmtCounterValue)]


class PdhError(RuntimeError):
    pass


class PdhSampler:
    """一次性把三类计数器收集完；每次采样新建查询以保证新进程能被发现。"""

    PATHS = (
        r"\GPU Process Memory(*)\Local Usage",
        r"\GPU Process Memory(*)\Non Local Usage",
        r"\GPU Engine(*)\Utilization Percentage",
    )

    def __init__(self) -> None:
        self.ok = False
        self.error = ""
        self._verify()

    def _verify(self) -> None:
        try:
            self.collect(gap=0.01)
            self.ok = True
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self.ok = False

    @staticmethod
    def _array(counter) -> dict[str, float]:
        for _ in range(3):
            size = wt.DWORD(0)
            count = wt.DWORD(0)
            rc = pdh.PdhGetFormattedCounterArrayW(
                counter, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None
            )
            if (rc & 0xFFFFFFFF) not in (PDH_MORE_DATA, 0):
                raise PdhError(f"取缓冲失败 rc={rc & 0xFFFFFFFF:#x}")
            if size.value == 0:
                return {}
            buf = ctypes.create_string_buffer(size.value)
            rc = pdh.PdhGetFormattedCounterArrayW(
                counter, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buf
            )
            if (rc & 0xFFFFFFFF) == PDH_INVALID_DATA:
                return {}
            if (rc & 0xFFFFFFFF) != 0:
                raise PdhError(f"读数据失败 rc={rc & 0xFFFFFFFF:#x}")
            items = ctypes.cast(buf, ctypes.POINTER(_CounterItem))
            out: dict[str, float] = {}
            for i in range(count.value):
                item = items[i]
                if item.FmtValue.CStatus != PDH_CSTATUS_VALID_DATA:
                    continue
                out[item.szName] = item.FmtValue.doubleValue
            return out
        return {}

    def collect(self, gap: float = 0.09) -> tuple[dict[int, ProcVram], dict[int, float]]:
        """返回 (进程显存字典, 适配器总占用字典)。"""
        query = wt.HANDLE()
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)) != 0:
            raise PdhError("PdhOpenQuery 失败")
        counters = []
        try:
            for path in self.PATHS:
                handle = wt.HANDLE()
                if pdh.PdhAddEnglishCounterW(query, path, 0, ctypes.byref(handle)) != 0:
                    continue  # 某类计数器缺失不影响其余
                counters.append((path, handle))
            if not counters:
                raise PdhError("系统不支持 GPU 性能计数器")
            pdh.PdhCollectQueryData(query)
            if gap > 0:
                time.sleep(gap)
            pdh.PdhCollectQueryData(query)

            procs: dict[int, ProcVram] = {}
            adapters: dict[int, float] = {}
            for path, handle in counters:
                data = self._array(handle)
                if "Process Memory" in path:
                    is_shared = "Non Local" in path
                    for name, value in data.items():
                        match = _instance_re.search(name)
                        if not match:
                            continue
                        pid = int(match.group(1))
                        luid = (int(match.group(2), 16) << 32) | int(match.group(3), 16)
                        entry = procs.setdefault(pid, ProcVram(pid=pid, luid=luid))
                        if is_shared:
                            entry.shared += int(value)
                        else:
                            entry.dedicated += int(value)
                            entry.luid = luid
                elif "GPU Engine" in path:
                    for name, value in data.items():
                        match = _instance_re.search(name)
                        if not match:
                            continue
                        pid = int(match.group(1))
                        entry = procs.setdefault(pid, ProcVram(pid=pid))
                        entry.util += max(0.0, float(value))
                # 适配器级总数不在此处采集
            for entry in procs.values():
                entry.util = min(100.0, entry.util)
            return procs, adapters
        finally:
            pdh.PdhCloseQuery(query)

    def adapter_totals(self) -> dict[int, tuple[float, float]]:
        """{luid: (专用已用, 共享已用)} 字节。"""
        query = wt.HANDLE()
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)) != 0:
            return {}
        try:
            handles = []
            for path in (
                r"\GPU Adapter Memory(*)\Dedicated Usage",
                r"\GPU Adapter Memory(*)\Shared Usage",
            ):
                handle = wt.HANDLE()
                if pdh.PdhAddEnglishCounterW(query, path, 0, ctypes.byref(handle)) == 0:
                    handles.append((path, handle))
            if not handles:
                return {}
            pdh.PdhCollectQueryData(query)
            time.sleep(0.03)
            pdh.PdhCollectQueryData(query)
            out: dict[int, list[float]] = {}
            for path, handle in handles:
                data = self._array(handle)
                for name, value in data.items():
                    match = re.search(r"luid_0x([0-9A-Fa-f]+)_0x([0-9A-Fa-f]+)_", name, re.I)
                    if not match:
                        continue
                    luid = (int(match.group(1), 16) << 32) | int(match.group(2), 16)
                    pair = out.setdefault(luid, [0.0, 0.0])
                    pair[1 if "Shared" in path else 0] = float(value)
            return {k: (v[0], v[1]) for k, v in out.items()}
        finally:
            pdh.PdhCloseQuery(query)


# --------------------------------------------------------------------------- #
# nvidia-smi 兜底
# --------------------------------------------------------------------------- #
def nvidia_smi_compute_apps() -> dict[int, int]:
    """{pid: MiB}，仅在 PDH 拿不到数据时兜底（WDDM 下只覆盖 CUDA 上下文）。"""
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")
    if not os.path.exists(exe):
        return {}
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        out = subprocess.run(
            [exe, "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6, startupinfo=si,
            creationflags=0x08000000,
        ).stdout
    except Exception:
        return {}
    result: dict[int, int] = {}
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        try:
            result[int(parts[0])] = int(float(parts[1]))
        except ValueError:
            continue
    return result


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #
class GpuCollector:
    """把 NVML / DXGI / PDH 组合成一次采样结果。"""

    def __init__(self) -> None:
        self.nvml = Nvml()
        self.pdh = PdhSampler()
        self.adapters: list[Adapter] = dxgi_adapters()
        self.errors: list[str] = []
        if not self.nvml.ok:
            self.errors.append(f"NVML 不可用：{self.nvml.error}")
        if not self.pdh.ok:
            self.errors.append(f"性能计数器不可用：{self.pdh.error}")
        self._fallback_until = 0.0
        self._last_adapters = 0.0
        self._adapter_totals: dict[int, tuple[float, float]] = {}
        self.luid_index: dict[int, int] = {}

    # -- 适配器 ------------------------------------------------------------
    def refresh_adapters(self) -> None:
        if time.monotonic() - self._last_adapters > 30:
            self.adapters = dxgi_adapters()
            self._last_adapters = time.monotonic()

    def display_adapters(self) -> list[Adapter]:
        """需要在界面上展示的适配器：独显在前、核显在后，同型号去重。"""
        seen: set[tuple[int, int]] = set()
        picked: list[Adapter] = []
        for adapter in self.adapters:
            if adapter.vendor_id == 0x1414:      # Microsoft Basic Render Driver（软件渲染）
                continue
            key = (adapter.vendor_id, adapter.device_id)
            if key in seen:
                continue
            if adapter.vram_mib < 64 and adapter.shared_mib < 1024:
                continue
            seen.add(key)
            picked.append(adapter)
        picked.sort(key=lambda a: (0 if a.vram_mib >= 2048 else 1, a.order))
        return picked

    def _luid_to_index(self, gpus: list[GpuStat], adapters: list[Adapter] | None = None) -> dict[int, int]:
        """把 DXGI 适配器 LUID 映射到 GpuStat 下标。"""
        adapters = adapters if adapters is not None else self.adapters
        mapping: dict[int, int] = {}
        used = set()
        for gpu in gpus:
            if not gpu.luid:
                continue
            # NVML 不直接给 LUID，用显存总量 + 厂商 + 顺序做匹配
            best = None
            best_diff = None
            for adapter in adapters:
                if adapter.luid in mapping:
                    continue
                if adapter.vendor != "NVIDIA":
                    continue
                diff = abs(adapter.vram_mib - gpu.total_mib)
                if best is None or diff < best_diff:
                    best, best_diff = adapter, diff
            if best and best_diff is not None and best_diff < max(1024, gpu.total_mib * 0.25):
                mapping[best.luid] = gpu.index
                used.add(best.luid)
        # 再按顺序给剩余 NVIDIA 显卡兜底
        rest = [a for a in adapters if a.vendor == "NVIDIA" and a.luid not in used]
        for gpu in gpus:
            if gpu.index in mapping.values():
                continue
            if rest:
                mapping[rest.pop(0).luid] = gpu.index
        return mapping

    # -- 采样 --------------------------------------------------------------
    def sample(self) -> tuple[list[GpuStat], dict[int, ProcVram], dict[int, int], list[str]]:
        """返回 (GPU 列表, 进程显存, nvidia-smi 兜底数据, 错误列表)。"""
        errors: list[str] = []
        self.refresh_adapters()
        gpus = self.nvml.devices()
        adapters = self.display_adapters()

        procs: dict[int, ProcVram] = {}
        try:
            procs, _ = self.pdh.collect()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"进程显存采集失败：{exc}")

        needs_adapter_totals = bool(gpus) and any(a.vendor != "NVIDIA" for a in adapters)
        if not gpus or needs_adapter_totals:
            try:
                self._adapter_totals = self.pdh.adapter_totals()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"适配器计数失败：{exc}")
                self._adapter_totals = {}

        if gpus:
            mapping = self._luid_to_index(gpus, adapters)
            for gpu in gpus:
                for luid, idx in mapping.items():
                    if idx == gpu.index:
                        gpu.luid = luid
                        break

        # 补上 NVML 覆盖不到的适配器（核显 / 非 NVIDIA 独显 / 无 NVIDIA 驱动）
        occupied = {g.luid for g in gpus if g.luid}
        for adapter in adapters:
            if adapter.luid in occupied:
                continue
            if adapter.vendor == "NVIDIA" and gpus:
                continue  # 已由 NVML 表示
            dedicated_used, shared_used = self._adapter_totals.get(adapter.luid, (0.0, 0.0))
            integrated = adapter.vram_mib < 2048
            total = adapter.shared_mib if integrated else adapter.vram_mib
            used = int((shared_used if integrated else dedicated_used) // MIB)
            util = min(100, int(round(sum(p.util for p in procs.values()
                                          if p.luid == adapter.luid))))
            gpus.append(
                GpuStat(
                    index=len(gpus),
                    name=adapter.desc,
                    vendor=adapter.vendor,
                    kind="核显" if integrated else "独显",
                    luid=adapter.luid,
                    total_mib=total,
                    used_mib=used,
                    free_mib=max(0, total - used),
                    dedicated_total_mib=adapter.vram_mib,
                    dedicated_used_mib=int(dedicated_used // MIB),
                    shared_total_mib=adapter.shared_mib,
                    shared_used_mib=int(shared_used // MIB),
                    util=util,
                    source="pdh",
                )
            )

        for index, gpu in enumerate(gpus):
            gpu.index = index
        self.luid_index = {g.luid: g.index for g in gpus if g.luid}

        smi: dict[int, int] = {}
        if gpus and (not procs or sum(p.dedicated for p in procs.values()) < MIB):
            if time.monotonic() >= self._fallback_until:
                smi = nvidia_smi_compute_apps()
                self._fallback_until = time.monotonic() + 10
        return gpus, procs, smi, errors
