"""项目回溯引擎：把 python.exe / 普通进程 映射回"它属于哪个项目"。

回溯依据（按可信度从高到低）
--------------------------
1. 命令行中的脚本路径（``python train.py`` / ``-m pkg.mod``）→ 脚本所在目录
2. 进程真实工作目录（PEB CurrentDirectory），对 ``python -c``、Jupyter 内核、
   ``python -m uvicorn`` 这类没有脚本路径的进程是关键线索
3. 虚拟环境 / conda 环境变量（VIRTUAL_ENV、CONDA_PREFIX）
4. 项目标记文件逐级向上搜索（.git、pyproject.toml、requirements.txt …）
5. 父进程链（pwsh / cmd / 终端 / IDE），用于展示"是谁启动的"
6. 用户自定义覆盖规则（config.json 的 overrides）
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .procs import ProcDetail, looks_python

_GENERIC_DIRS = {
    "windows", "system32", "syswow64", "program files", "program files (x86)",
    "programdata", "appdata", "local", "roaming", "users", "desktop", "downloads",
    "documents", "pictures", "videos", "music", "temp", "tmp", "public", "default",
    "windowsapps", "common files", "packages", "node_modules", "site-packages",
    "dist-packages", "lib", "libs", "scripts", "bin", "include", "libexec",
    "start menu", "startup", "microsoft", ".git", ".venv", "venv",
    # 中文系统常见目录名
    "下载", "桌面", "文档", "图片", "视频", "音乐", "公共", "我的文档", "应用程序",
}

_MARKERS: tuple[tuple[str, int, str], ...] = (
    (".git", 100, "Git 仓库"),
    ("pyproject.toml", 80, "pyproject.toml"),
    ("uv.lock", 55, "uv.lock"),
    ("poetry.lock", 55, "poetry.lock"),
    ("Pipfile", 50, "Pipfile"),
    ("setup.py", 50, "setup.py"),
    ("setup.cfg", 40, "setup.cfg"),
    ("environment.yml", 45, "environment.yml"),
    ("requirements.txt", 40, "requirements.txt"),
    ("requirements-dev.txt", 30, "requirements-dev.txt"),
    (".venv", 30, "虚拟环境目录"),
    ("venv", 25, "虚拟环境目录"),
    ("conda.yaml", 35, "conda.yaml"),
    (".idea", 20, ".idea"),
    (".vscode", 20, ".vscode"),
    (".gitignore", 12, ".gitignore"),
    ("README.md", 8, "README.md"),
    ("src", 6, "src/"),
    ("tests", 4, "tests/"),
    ("notebooks", 4, "notebooks/"),
)

_STEAM = re.compile(r"\\steamapps\\common\\([^\\]+)", re.I)
_EPIC = re.compile(r"\\epic games\\([^\\]+)", re.I)

_FLAG_WITH_VALUE = {
    "-m", "-c", "-x", "-u", "-O", "-OO", "-B", "-d", "-E", "-i", "-I", "-q", "-s",
    "-S", "-v", "-W", "-X", "--check-hash-based-pycs", "-Q", "-t", "-R",
}


@dataclass
class Attribution:
    project: str = ""
    root: str = ""
    kind: str = "Python 项目"
    confidence: str = "低"
    score: int = 0
    markers: list[str] = field(default_factory=list)
    script: str = ""
    module: str = ""
    venv: str = ""
    conda: str = ""
    branch: str = ""
    evidence: list[str] = field(default_factory=list)
    parent_chain: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return (self.root or f"~{self.project}").casefold()

    @property
    def tagline(self) -> str:
        bits = []
        if self.conda:
            bits.append(f"conda: {self.conda}")
        elif self.venv:
            bits.append(f"venv: {self.venv}")
        if self.branch:
            bits.append(f"git: {self.branch}")
        if self.kind and self.kind != "Python 项目":
            bits.append(self.kind)
        return " · ".join(bits)


def _norm(path: str) -> str:
    try:
        return os.path.normpath(path)
    except Exception:
        return path


def _basename(path: str) -> str:
    return os.path.basename(path.rstrip("\\/")) or path


def _is_generic(path: str) -> bool:
    low = _basename(path).casefold()
    if low in _GENERIC_DIRS:
        return True
    drive, tail = os.path.splitdrive(path)
    return not tail.strip("\\/")


def _score_dir(path: str) -> tuple[int, list[str]]:
    score = 0
    hits: list[str] = []
    try:
        entries = {name.casefold(): name for name in os.listdir(path)}
    except OSError:
        return 0, []
    for marker, weight, label in _MARKERS:
        if marker.casefold() in entries:
            score += weight
            hits.append(label)
    return score, hits


def _git_branch(root: str) -> str:
    head = os.path.join(root, ".git", "HEAD")
    try:
        with open(head, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(200).strip()
    except OSError:
        return ""
    if text.startswith("ref:"):
        ref = text[4:].strip()
        if "heads/" in ref:
            return ref.split("heads/", 1)[1]
        return ref.split("/")[-1]
    return text[:8]


class Attributor:
    def __init__(self, overrides: list[dict] | None = None) -> None:
        self.overrides = [o for o in (overrides or []) if o.get("match")]
        self._root_cache: dict[str, tuple[int, str, list[str]]] = {}
        self._branch_cache: dict[str, str] = {}

    # -- 公共入口 ----------------------------------------------------------
    def attribute(self, detail: ProcDetail) -> Attribution:
        override = self._match_override(detail)
        if override:
            attr = self._base_attr(detail)
            attr.project = override.get("project") or attr.project
            attr.root = _norm(override.get("root") or "") or attr.root
            attr.confidence = "高"
            attr.score = 999
            attr.evidence.insert(0, "用户规则匹配")
            attr.kind = "自定义"
            return attr
        if looks_python(detail.exe, detail.name):
            return self._attribute_python(detail)
        return self._attribute_app(detail)

    # -- 内部 --------------------------------------------------------------
    def _match_override(self, detail: ProcDetail) -> dict | None:
        blob = f"{detail.exe}\n{detail.cmdline}".casefold()
        for rule in self.overrides:
            pattern = str(rule.get("match", "")).casefold()
            if pattern and pattern in blob:
                return rule
        return None

    def _base_attr(self, detail: ProcDetail) -> Attribution:
        return Attribution(
            project=_basename(detail.exe) or f"PID {detail.pid}",
            kind="应用",
            evidence=[],
        )

    # -- python ------------------------------------------------------------
    def _attribute_python(self, detail: ProcDetail) -> Attribution:
        attr = Attribution(kind="Python 项目")
        script, module = self._parse_script(detail)
        attr.script = script
        attr.module = module

        cwd = _norm(detail.cwd) if detail.cwd and os.path.isdir(detail.cwd) else ""
        venv = detail.env.get("VIRTUAL_ENV", "")
        conda = detail.env.get("CONDA_PREFIX", "") or (
            detail.env.get("CONDA_DEFAULT_ENV", "") if detail.env.get("CONDA_DEFAULT_ENV") not in ("", "base") else ""
        )
        attr.venv = _basename(venv) if venv else self._venv_from_exe(detail.exe)
        attr.conda = _basename(conda) if conda else ""

        # 1) 脚本目录优先，其次工作目录
        anchor = ""
        if script and os.path.exists(script):
            anchor = os.path.dirname(script)
        if not anchor and cwd:
            anchor = cwd
        if not anchor and venv:
            anchor = venv

        # 已安装包（site-packages）或虚拟环境自带脚本，不应视作项目根
        parts = script.replace("/", "\\").split("\\") if script else []
        inside_pkg = any(p.casefold() in ("site-packages", "dist-packages") for p in parts)
        if not inside_pkg and venv:
            low_script = script.casefold()
            low_venv = _norm(venv).casefold()
            inside_pkg = bool(low_script) and low_script.startswith(low_venv + "\\")

        root, score, markers = ("", 0, [])
        if anchor and not inside_pkg:
            root, score, markers = self._find_root(anchor)

        if inside_pkg:
            attr.kind = "第三方包 / 解释器"
            if venv:
                base = _norm(venv)
            elif any(p.casefold() in ("site-packages", "dist-packages") for p in parts):
                idx = next(i for i, p in enumerate(parts) if p.casefold() in ("site-packages", "dist-packages"))
                base = "\\".join(parts[:idx]) or ""
            else:
                base = os.path.dirname(script)
            attr.project = _basename(base) if base else "第三方包"
            attr.root = base
            attr.confidence = "中"
            attr.score = 60
            attr.markers = ["site-packages"]
            attr.evidence.append("脚本来自已安装的包/工具")
            if cwd:
                alt_root, alt_score, alt_markers = self._find_root(cwd)
                if alt_score >= 100:
                    attr.root, attr.project = alt_root, _basename(alt_root)
                    attr.markers = alt_markers
                    attr.confidence = "高"
                    attr.score = alt_score
                    attr.evidence = ["工作目录命中项目标记（工具在项目内启动）"]
        elif root:
            attr.root = root
            attr.project = _basename(root)
            attr.markers = markers
            attr.score = score
            attr.confidence = "高" if score >= 100 else ("中" if score >= 35 else "低")
            if score >= 100:
                attr.evidence.append("命中 .git")
            elif markers:
                attr.evidence.append("命中 " + "、".join(markers[:3]))
            if cwd and _norm(cwd).casefold() != _norm(root).casefold():
                attr.evidence.append(f"工作目录 {cwd}")
        else:
            attr.root = anchor or ""
            attr.project = _basename(anchor) if anchor else (module or "未知 Python 进程")
            attr.confidence = "低"
            attr.evidence.append("未找到项目标记，回退到" + ("脚本目录" if script else "工作目录"))

        if attr.root and not attr.branch:
            attr.branch = self._branch_cache.get(attr.root) or ""
            if not attr.branch:
                attr.branch = _git_branch(attr.root)
                self._branch_cache[attr.root] = attr.branch

        if module:
            attr.evidence.append(f"模块 -m {module}")
        elif script and os.path.exists(script):
            attr.evidence.append(f"脚本 {os.path.basename(script)}")
        if attr.venv:
            attr.evidence.append(f"虚拟环境 {attr.venv}")
        if attr.conda:
            attr.evidence.append(f"conda 环境 {attr.conda}")
        if not detail.readable:
            attr.evidence.append("进程受保护，命令行/目录不可读")
        if not attr.project:
            attr.project = "未知 Python 进程"
        return attr

    def _venv_from_exe(self, exe: str) -> str:
        low = exe.casefold()
        for part in ("\\scripts\\", "\\bin\\"):
            idx = low.find(part)
            if idx > 0:
                return _basename(exe[:idx])
        return ""

    def _parse_script(self, detail: ProcDetail) -> tuple[str, str]:
        """从 argv 中找到脚本路径或 -m 模块名。"""
        args = detail.argv[1:] if detail.argv else []
        if not args:
            return "", ""
        module = ""
        for i, token in enumerate(args):
            if token == "-m" and i + 1 < len(args):
                module = args[i + 1]
                break
        script = ""
        skip_next = False
        for i, token in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            if token in _FLAG_WITH_VALUE:
                skip_next = True
                continue
            if token.startswith("-"):
                continue
            low = token.casefold()
            if low.endswith((".py", ".pyw")):
                candidate = token
                if not os.path.isabs(candidate) and detail.cwd:
                    candidate = os.path.join(detail.cwd, candidate)
                candidate = _norm(candidate)
                if os.path.exists(candidate):
                    script = candidate
                    break
                if not script:
                    script = candidate
            elif os.path.sep in token and os.path.exists(token) and script == "":
                if os.path.isdir(token):
                    continue
        if not script and module:
            found = self._module_path(module, detail.cwd)
            if found:
                script = found
        return script, module

    def _module_path(self, module: str, cwd: str) -> str:
        """把 -m a.b 解析成 cwd 下的包路径（仅用于定位项目）。"""
        if not cwd or not os.path.isdir(cwd):
            return ""
        parts = module.split(".")
        base = os.path.join(cwd, *parts)
        for candidate in (base + ".py", os.path.join(base, "__init__.py"), base):
            if os.path.exists(candidate):
                return _norm(candidate)
        return ""

    def _find_root(self, anchor: str) -> tuple[str, int, list[str]]:
        anchor = _norm(anchor)
        cached = self._root_cache.get(anchor.casefold())
        if cached:
            return cached
        best_score, best_dir, best_markers = 0, "", []
        current = anchor
        depth = 0
        while depth < 7 and current:
            score, markers = _score_dir(current)
            if score > best_score:
                best_score, best_dir, best_markers = score, current, markers
            if ".git" in markers:
                break
            parent = os.path.dirname(current)
            if not parent or parent == current or _is_generic(parent):
                break
            current = parent
            depth += 1
        if best_score == 0:
            result = (anchor, 0, [])
        elif (best_dir.casefold() != anchor.casefold() and best_score < 35):
            # 更上层只有弱标记（README / tests 之类）时，仍以脚本或工作目录为准
            result = (anchor, 0, [])
        else:
            result = (best_dir, best_score, best_markers)
        if len(self._root_cache) > 4096:
            self._root_cache.clear()
        self._root_cache[anchor.casefold()] = result
        return result

    # -- 普通应用 ----------------------------------------------------------
    def _attribute_app(self, detail: ProcDetail) -> Attribution:
        attr = self._base_attr(detail)
        exe = _norm(detail.exe)
        name = os.path.splitext(_basename(exe))[0]
        attr.project = name
        attr.kind = "应用"

        match = _STEAM.search(exe)
        if match:
            attr.project = match.group(1)
            attr.root = exe[: match.end()]
            attr.kind = "游戏 (Steam)"
            attr.confidence = "中"
            attr.score = 60
            return attr
        match = _EPIC.search(exe)
        if match:
            attr.project = match.group(1)
            attr.root = exe[: match.end()]
            attr.kind = "游戏 (Epic)"
            attr.confidence = "中"
            attr.score = 60
            return attr

        folder = os.path.dirname(exe)
        if folder and not _is_generic(folder):
            attr.root = folder
            attr.confidence = "低"
            attr.score = 20
        elif folder:
            attr.root = folder
        return attr
