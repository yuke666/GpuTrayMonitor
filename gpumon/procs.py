"""进程枚举与内省：可执行路径、启动时间、父进程、命令行、工作目录、环境变量。"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

from . import win32

_PY_RE = re.compile(r"^(python|pythonw|python[0-9.]+|python[0-9.]+w|ipython[0-9.]*)$", re.I)
_ENV_KEYS = (
    "VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "PYTHONPATH", "PWD", "OLDPWD",
    "CUDA_VISIBLE_DEVICES", "PYTHONSTARTUP", "VIRTUAL_ENV_PROMPT", "JUPYTER_CONFIG_DIR",
    "HF_HOME", "TRANSFORMERS_CACHE", "PIPENV_ACTIVE", "POETRY_ACTIVE", "UV_PROJECT_ENVIRONMENT",
)


@dataclass
class ProcDetail:
    pid: int
    name: str = ""
    exe: str = ""
    cmdline: str = ""
    argv: list[str] = field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    ppid: int = 0
    created: float = 0.0
    is_python: bool = False
    readable: bool = True

    @property
    def age(self) -> float:
        return max(0.0, time.time() - self.created) if self.created else 0.0


def looks_python(exe: str, name: str = "") -> bool:
    base = (name or os.path.basename(exe)).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    if _PY_RE.match(base):
        return True
    low = exe.lower()
    # 虚拟环境里的 console script（jupyter-lab.exe / streamlit.exe / torchrun.exe …）
    if "\\scripts\\" in low and base not in ("pip", "activate"):
        return True
    return False


class ProcTable:
    """带缓存的进程表；只对"感兴趣的"进程读取 PEB，控制开销。"""

    def __init__(self) -> None:
        self._base: dict[int, tuple[str, str, int, float]] = {}   # pid -> (name, exe, ppid, created)
        self._detail_cache: dict[tuple[int, float], ProcDetail] = {}
        self._self_pid = os.getpid()
        self.errors: list[str] = []

    # -- 基础信息 ----------------------------------------------------------
    def scan(self, deep_pids: set[int] | None = None, deep_python: bool = True) -> dict[int, ProcDetail]:
        deep_pids = set(deep_pids or ())
        now = time.monotonic()
        base: dict[int, tuple[str, str, int, float]] = {}
        for pid in win32.enum_pids():
            if pid == 0 or pid == 4:
                continue
            if pid == self._self_pid:
                continue
            handle = win32.open_process(pid)
            limited = False
            if not handle:
                handle = win32.open_process(pid, win32.PROCESS_QUERY_LIMITED_INFORMATION)
                limited = True
            if not handle:
                continue
            try:
                exe = win32.query_image_name(handle)
                if not exe:
                    continue
                name = os.path.basename(exe)
                created = win32.get_create_time(handle)
                ppid = 0 if limited else win32.parent_pid(handle)
                base[pid] = (name, exe, ppid, created)
            finally:
                win32.close_handle(handle)

        self._base = base
        results: dict[int, ProcDetail] = {}
        # 清理过期缓存
        if len(self._detail_cache) > 512:
            self._detail_cache.clear()

        for pid, (name, exe, ppid, created) in base.items():
            is_py = looks_python(exe, name)
            if not (pid in deep_pids or (deep_python and is_py)):
                continue
            key = (pid, created)
            detail = self._detail_cache.get(key)
            if detail is None:
                detail = self._inspect(pid, name, exe, ppid, created, is_py)
                self._detail_cache[key] = detail
            results[pid] = detail
        # 丢弃已退出进程的缓存
        alive = {(pid, b[3]) for pid, b in base.items()}
        for key in [k for k in self._detail_cache if k not in alive]:
            self._detail_cache.pop(key, None)
        return results

    def _inspect(self, pid: int, name: str, exe: str, ppid: int, created: float,
                 is_py: bool) -> ProcDetail:
        detail = ProcDetail(pid=pid, name=name, exe=exe, ppid=ppid, created=created, is_python=is_py)
        handle = win32.open_process(pid)
        if not handle:
            detail.readable = False
            return detail
        try:
            info = win32.read_process_details(handle)
            detail.cmdline = info.get("cmdline") or ""
            detail.cwd = (info.get("cwd") or "").rstrip("\\/") or ""
            env = info.get("env") or {}
            detail.env = {k: env[k] for k in _ENV_KEYS if k in env}
            detail.argv = win32.split_cmdline(detail.cmdline, exe)
        except Exception:
            detail.readable = False
        finally:
            win32.close_handle(handle)
        return detail

    # -- 辅助 --------------------------------------------------------------
    def name_of(self, pid: int) -> str:
        return self._base.get(pid, ("", "", 0, 0.0))[0]

    def parent_chain(self, pid: int, limit: int = 5) -> list[str]:
        chain: list[str] = []
        seen = set()
        current = pid
        for _ in range(limit):
            entry = self._base.get(current)
            if not entry:
                break
            ppid = entry[2]
            if ppid <= 0 or ppid in seen:
                break
            seen.add(ppid)
            parent = self._base.get(ppid)
            if not parent:
                break
            chain.append(parent[0])
            current = ppid
        return chain

    def terminate(self, pid: int) -> bool:
        handle = win32.open_process(pid, win32.PROCESS_TERMINATE)
        if not handle:
            return False
        try:
            return bool(win32.kernel32.TerminateProcess(handle, 1))
        finally:
            win32.close_handle(handle)
