"""配置、日志、开机自启。"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field

APP_NAME = "GpuTrayMonitor"
DISPLAY_NAME = "显存监视器"


_DATA_DIR_CACHE: str | None = None


def data_dir() -> str:
    """配置/日志目录；%LOCALAPPDATA% 不可写时依次回退到程序目录、临时目录。"""
    global _DATA_DIR_CACHE
    if _DATA_DIR_CACHE:
        return _DATA_DIR_CACHE
    candidates = []
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    candidates.append(os.path.join(local, APP_NAME))
    # 打包后的 exe 要落在 exe 旁边，而不是 _internal 里面
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.path.join(base, ".data"))
    candidates.append(os.path.join(os.environ.get("TEMP") or "/tmp", APP_NAME))
    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".write-test")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
            _DATA_DIR_CACHE = path
            return path
        except OSError:
            continue
    _DATA_DIR_CACHE = os.path.dirname(os.path.abspath(__file__))
    return _DATA_DIR_CACHE


def app_root() -> str:
    return os.path.dirname(os.path.abspath(sys.argv[0] if sys.argv and sys.argv[0] else __file__))


def startup_shortcut() -> str:
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(
        appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
        f"{DISPLAY_NAME}.vbs",
    )


@dataclass
class Config:
    interval: float = 2.0
    show_all_python: bool = True
    paused: bool = False
    autostart: bool = False
    start_hidden: bool = True
    first_hide_hint: bool = True
    taskbar_hud: bool = True
    taskbar_hud_offset: int = 10
    taskbar_hud_y: int | None = None
    overrides: list[dict] = field(default_factory=list)
    ignores: list[str] = field(default_factory=list)
    window: dict = field(default_factory=lambda: {"w": None, "h": None, "x": None, "y": None})
    sort_mode: str = "vram"

    path: str = ""

    # -- 读写 --------------------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        path = os.path.join(data_dir(), "config.json")
        cfg = cls(path=path)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            for key, value in raw.items():
                if hasattr(cfg, key) and key not in ("path",):
                    setattr(cfg, key, value)
        except FileNotFoundError:
            pass
        except Exception:
            pass
        if not isinstance(cfg.overrides, list):
            cfg.overrides = []
        return cfg

    def save(self) -> None:
        try:
            raw = {k: v for k, v in asdict(self).items() if k != "path"}
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            pass

    # -- 开机自启 ----------------------------------------------------------
    def set_autostart(self, enabled: bool) -> bool:
        target = startup_shortcut()
        try:
            if enabled:
                pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                if not os.path.exists(pythonw):
                    pythonw = sys.executable
                script = os.path.join(app_root(), "gpumon.pyw")
                content = (
                    'Set sh = CreateObject("WScript.Shell")\r\n'
                    f'sh.Run """{pythonw}"" ""{script}""", 0, False\r\n'
                )
                with open(target, "w", encoding="utf-8") as fh:
                    fh.write(content)
            else:
                if os.path.exists(target):
                    os.remove(target)
            self.autostart = enabled
            self.save()
            return True
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning("设置开机自启失败: %s", exc)
            return False


def setup_logging(verbose: bool = False) -> None:
    path = os.path.join(data_dir(), "monitor.log")
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=512 * 1024, backupCount=2,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(handler)
    if verbose and sys.stderr:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(stream)


def copy_launcher(dest_dir: str) -> None:
    """把 .pyw 入口复制到目标目录（预留）。"""
    src = os.path.join(app_root(), "gpumon.pyw")
    if os.path.exists(src) and os.path.abspath(dest_dir) != os.path.abspath(app_root()):
        shutil.copy2(src, os.path.join(dest_dir, "gpumon.pyw"))
