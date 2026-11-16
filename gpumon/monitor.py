"""采样循环与数据聚合：把 GPU 数据 + 进程内省 + 项目回溯 拼成一份快照。"""
from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass, field

from . import gpu as gpu_mod
from .attrib import Attribution, Attributor
from .procs import ProcDetail, ProcTable
from .util import fmt_mib

MIB = 1024 * 1024


@dataclass
class ProcRow:
    pid: int
    name: str = ""
    exe: str = ""
    cmdline: str = ""
    cwd: str = ""
    vram_mib: float = 0.0
    shared_mib: float = 0.0
    util: float = 0.0
    gpu_index: int | None = None
    gpu_label: str = ""
    created: float = 0.0
    is_python: bool = False
    source: str = "pdh"
    attribution: Attribution = field(default_factory=Attribution)
    detail: ProcDetail | None = None

    @property
    def iid(self) -> str:
        return f"p:{self.pid}"

    @property
    def script_name(self) -> str:
        if self.attribution.script:
            import os

            return os.path.basename(self.attribution.script)
        if self.attribution.module:
            return f"-m {self.attribution.module}"
        if self.is_python and self.cmdline:
            return "python"
        return self.name

    @property
    def summary(self) -> str:
        """命令行摘要：优先显示脚本 + 关键参数。"""
        import os

        attr = self.attribution
        if attr.script:
            args = []
            argv = (self.detail.argv[1:] if self.detail and self.detail.argv else [])
            try:
                idx = next(i for i, a in enumerate(argv) if a and a.lower().endswith((".py", ".pyw")))
                args = argv[idx + 1:]
            except StopIteration:
                args = []
            text = os.path.basename(attr.script) + (" " + " ".join(args) if args else "")
        elif attr.module:
            argv = (self.detail.argv[1:] if self.detail and self.detail.argv else [])
            text = " ".join(argv[:4]) if argv else f"-m {attr.module}"
        else:
            text = self.cmdline or self.exe
        text = " ".join(text.split())
        return text if len(text) <= 96 else text[:95] + "…"


@dataclass
class ProjectGroup:
    key: str
    name: str
    root: str = ""
    kind: str = ""
    tagline: str = ""
    confidence: str = ""
    markers: list[str] = field(default_factory=list)
    branch: str = ""
    venv: str = ""
    conda: str = ""
    rows: list[ProcRow] = field(default_factory=list)
    total_mib: float = 0.0
    max_util: float = 0.0
    is_python: bool = False

    @property
    def iid(self) -> str:
        return f"g:{self.key}"


@dataclass
class Snapshot:
    ts: float = 0.0
    gpus: list[gpu_mod.GpuStat] = field(default_factory=list)
    rows: list[ProcRow] = field(default_factory=list)
    groups: list[ProjectGroup] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    history: dict[int, list[float]] = field(default_factory=dict)

    @property
    def total_used_mib(self) -> float:
        return sum(g.used_mib for g in self.gpus)

    @property
    def total_vram_mib(self) -> float:
        return sum(g.total_mib for g in self.gpus)

    @property
    def max_pressure(self) -> float:
        return max((g.used_pct for g in self.gpus), default=0.0)

    @property
    def python_count(self) -> int:
        return sum(1 for r in self.rows if r.is_python)

    def tooltip(self) -> str:
        if not self.gpus:
            return "显存监视器 · 未检测到 GPU"
        parts = []
        for gpu in self.gpus[:2]:
            name = gpu.short_name
            if len(name) > 16:
                name = name[:15] + "…"
            text = f"{name} {gpu.used_mib / 1024:.1f}/{gpu.total_mib / 1024:.1f}G {gpu.used_pct:.0f}%"
            if len(self.gpus) == 1:
                text += f" · GPU {gpu.util}%"
            parts.append(text)
        if self.groups:
            top = self.groups[0]
            parts.append(f"最大 {top.name[:12]} {top.total_mib / 1024:.1f}G")
        text = "  ".join(parts)
        return text if len(text) <= 127 else text[:126] + "…"


class Monitor(threading.Thread):
    """后台采样线程；结果通过回调推送。"""

    def __init__(self, config, on_snapshot=None) -> None:
        super().__init__(daemon=True, name="gpumon-sampler")
        self.config = config
        self.on_snapshot = on_snapshot
        self.collector = gpu_mod.GpuCollector()
        self.table = ProcTable()
        self.attributor = Attributor(config.overrides)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._attr_cache: dict[tuple[int, float], tuple[str, Attribution]] = {}
        self._history: dict[int, collections.deque] = {}
        self.latest: Snapshot | None = None
        self.paused = False
        self.last_error = ""

    # -- 控制 --------------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self._wake.set()

    # -- 主循环 ------------------------------------------------------------
    def run(self) -> None:
        while not self._stop.is_set():
            start = time.perf_counter()
            if not self.paused:
                try:
                    snap = self.sample()
                    self.latest = snap
                    if self.on_snapshot:
                        self.on_snapshot(snap)
                except Exception as exc:  # noqa: BLE001
                    self.last_error = f"{type(exc).__name__}: {exc}"
            elapsed = time.perf_counter() - start
            interval = max(0.5, float(self.config.interval))
            self._wake.wait(max(0.2, interval - elapsed))
            self._wake.clear()

    # -- 采样 --------------------------------------------------------------
    def sample(self) -> Snapshot:
        t0 = time.perf_counter()
        gpus, proc_vram, smi, errors = self.collector.sample()
        deep = set(proc_vram) | set(smi)
        details = self.table.scan(deep, deep_python=self.config.show_all_python)

        rows: list[ProcRow] = []
        for pid, detail in details.items():
            pv = proc_vram.get(pid)
            vram = (pv.dedicated / MIB) if pv else 0.0
            shared = (pv.shared / MIB) if pv else 0.0
            util = pv.util if pv else 0.0
            source = "pdh"
            if vram <= 0 and pid in smi:
                vram = float(smi[pid])
                source = "nvidia-smi"
            if vram < 0.5 and not (detail.is_python and self.config.show_all_python):
                continue
            if not detail.is_python and vram < 0.5:
                continue

            attr = self._attribute_cached(detail)
            gpu_index, gpu_label = self._gpu_of(pv.luid if pv else 0, gpus)
            row = ProcRow(
                pid=pid,
                name=detail.name,
                exe=detail.exe,
                cmdline=detail.cmdline,
                cwd=detail.cwd,
                vram_mib=round(vram, 1),
                shared_mib=round(shared, 1),
                util=round(util, 1),
                gpu_index=gpu_index,
                gpu_label=gpu_label,
                created=detail.created,
                is_python=detail.is_python,
                source=source,
                attribution=attr,
                detail=detail,
            )
            rows.append(row)

        # GPU 进程但无法打开的（其它用户 / 受保护进程）
        known = {r.pid for r in rows}
        for pid, pv in proc_vram.items():
            if pid in known or pid not in self.table._base:
                continue
            if pv.dedicated / MIB < 1:
                continue
            name, exe, ppid, created = self.table._base[pid]
            detail = ProcDetail(pid=pid, name=name, exe=exe, ppid=ppid, created=created,
                                is_python=False, readable=False)
            attr = self.attributor.attribute(detail)
            gpu_index, gpu_label = self._gpu_of(pv.luid, gpus)
            rows.append(
                ProcRow(
                    pid=pid, name=name, exe=exe, vram_mib=round(pv.dedicated / MIB, 1),
                    shared_mib=round(pv.shared / MIB, 1), util=round(pv.util, 1),
                    gpu_index=gpu_index, gpu_label=gpu_label, created=created,
                    is_python=False, attribution=attr, detail=detail,
                )
            )

        groups = self._group(rows)
        self._update_history(gpus)
        snap = Snapshot(
            ts=time.time(),
            gpus=gpus,
            rows=rows,
            groups=groups,
            errors=list(dict.fromkeys(errors + ([self.last_error] if self.last_error else [])))[:5],
            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
            history={idx: list(buf) for idx, buf in self._history.items()},
        )
        return snap

    def _attribute_cached(self, detail: ProcDetail) -> Attribution:
        key = (detail.pid, detail.created)
        cached = self._attr_cache.get(key)
        if cached and cached[0] == detail.cmdline and cached[1].root:
            return cached[1]
        attr = self.attributor.attribute(detail)
        attr.parent_chain = self.table.parent_chain(detail.pid)
        if len(self._attr_cache) > 1024:
            self._attr_cache.clear()
        self._attr_cache[key] = (detail.cmdline, attr)
        return attr

    def _gpu_of(self, luid: int, gpus: list[gpu_mod.GpuStat]) -> tuple[int | None, str]:
        if not luid:
            return (gpus[0].index, f"#{gpus[0].index}") if len(gpus) == 1 else (None, "")
        idx = self.collector.luid_index.get(luid)
        if idx is not None:
            for g in gpus:
                if g.index == idx:
                    return idx, f"#{idx} {g.short_name}"
            return idx, f"#{idx}"
        for adapter in self.collector.adapters:
            if adapter.luid == luid:
                if adapter.vendor_id == 0x8086:
                    label = "核显"
                elif adapter.vendor_id == 0x1414:
                    label = "软件渲染"
                else:
                    label = adapter.vendor
                return None, label
        return None, ""

    def _group(self, rows: list[ProcRow]) -> list[ProjectGroup]:
        groups: dict[str, ProjectGroup] = {}
        for row in rows:
            attr = row.attribution
            key = attr.key or f"pid:{row.pid}"
            group = groups.get(key)
            if group is None:
                group = ProjectGroup(
                    key=key,
                    name=attr.project or "未知",
                    root=attr.root,
                    kind=attr.kind,
                    tagline=attr.tagline,
                    confidence=attr.confidence,
                    markers=list(attr.markers),
                    branch=attr.branch,
                    venv=attr.venv,
                    conda=attr.conda,
                )
                groups[key] = group
            group.rows.append(row)
            group.total_mib += row.vram_mib
            group.max_util = max(group.max_util, row.util)
            group.is_python = group.is_python or row.is_python
        out = list(groups.values())
        for group in out:
            group.rows.sort(key=lambda r: (-r.vram_mib, r.name.lower(), r.pid))
            group.total_mib = round(group.total_mib, 1)
        out.sort(key=lambda g: (-g.total_mib, g.name.casefold()))
        return out

    def _update_history(self, gpus: list[gpu_mod.GpuStat]) -> None:
        for g in gpus:
            buf = self._history.get(g.index)
            if buf is None:
                buf = collections.deque(maxlen=150)
                self._history[g.index] = buf
            buf.append(round(g.used_pct, 1))
        for idx in list(self._history):
            if all(g.index != idx for g in gpus):
                self._history.pop(idx, None)
