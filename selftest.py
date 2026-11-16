"""自检：逐层验证采集、回溯与聚合是否正常。用法：

    python selftest.py

不依赖第三方库；会临时创建项目目录、拉起测试进程，结束后自动清理。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gpumon import gpu as gpu_mod, icons, win32
from gpumon.attrib import Attributor
from gpumon.monitor import Monitor
from gpumon.procs import ProcTable

PASS, FAIL = "[PASS]", "[FAIL]"
failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{PASS if condition else FAIL} {name}" + (f"  — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def make_project(root: str, kind: str) -> str:
    os.makedirs(root, exist_ok=True)
    if kind == "git":
        os.makedirs(os.path.join(root, ".git"), exist_ok=True)
        with open(os.path.join(root, ".git", "HEAD"), "w", encoding="utf-8") as fh:
            fh.write("ref: refs/heads/feature/vram\n")
        with open(os.path.join(root, "pyproject.toml"), "w", encoding="utf-8") as fh:
            fh.write("[project]\nname='demo'\n")
        script = os.path.join(root, "train.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("import time\nprint('train')\ntime.sleep(30)\n")
        return script
    if kind == "reqs":
        os.makedirs(os.path.join(root, "src"), exist_ok=True)
        with open(os.path.join(root, "requirements.txt"), "w", encoding="utf-8") as fh:
            fh.write("torch\n")
        return os.path.join(root, "src")
    os.makedirs(os.path.join(root, "pkg"), exist_ok=True)
    with open(os.path.join(root, "pkg", "__init__.py"), "w", encoding="utf-8") as fh:
        fh.write("")
    return root


def test_gpu() -> None:
    section("1. GPU 采集")
    nvml = gpu_mod.Nvml()
    devices = nvml.devices()
    check("NVML 可用", nvml.ok, nvml.error or f"驱动 {nvml.driver}")
    check("枚举到 NVIDIA 显卡", bool(devices),
          "; ".join(f"{d.name} {d.used_mib}/{d.total_mib} MiB {d.util}% {d.temp}°C"
                    for d in devices))

    adapters = gpu_mod.dxgi_adapters()
    check("DXGI 适配器枚举", bool(adapters),
          "; ".join(f"{a.desc}({a.vendor}, {a.vram_mib}MiB)" for a in adapters[:4]))

    pdh = gpu_mod.PdhSampler()
    procs_mem, _ = pdh.collect()
    check("性能计数器可用", pdh.ok, pdh.error)
    top = sorted(procs_mem.values(), key=lambda p: -p.dedicated)[:6]
    check("读到每进程显存", bool(procs_mem),
          "; ".join(f"pid {p.pid}: {p.dedicated / 1048576:.0f}MiB" for p in top))
    totals = pdh.adapter_totals()
    check("读到适配器总量", bool(totals),
          "; ".join(f"luid {k:#x}: {v[0] / 1048576:.0f}MiB" for k, v in totals.items()))

    collector = gpu_mod.GpuCollector()
    gpus, pmem, smi, errors = collector.sample()
    check("GpuCollector 汇总", bool(gpus),
          "; ".join(f"#{g.index} {g.name} luid={g.luid:#x} 已用 {g.used_mib}MiB" for g in gpus))
    check("LUID→GPU 映射", bool(collector.luid_index),
          str({f"{k:#x}": v for k, v in collector.luid_index.items()}))
    for gpu in gpus:
        if gpu.kind == "核显":
            print(f"    核显：{gpu.short_name} {gpu.used_mib}/{gpu.total_mib} MiB "
                  f"({gpu.used_pct:.1f}%) 利用率 {gpu.util}% 专用 {gpu.dedicated_total_mib} MB")
    if errors:
        print("    （采集告警：" + " | ".join(errors) + "）")


def test_attribution(base_tmp: str) -> None:
    section("2. 进程内省与项目回溯")
    py = sys.executable
    proj_a = os.path.join(base_tmp, "vision-lab")
    proj_b = os.path.join(base_tmp, "数据清洗")
    proj_c = os.path.join(base_tmp, "web-service")
    script_a = make_project(proj_a, "git")
    dir_b = make_project(proj_b, "reqs")
    dir_c = make_project(proj_c, "bare")

    env = dict(os.environ)
    env["VIRTUAL_ENV"] = os.path.join(proj_a, ".venv")
    procs = [
        subprocess.Popen([py, script_a], cwd=proj_a, env=env),
        subprocess.Popen([py, "-c", "import time; time.sleep(30)"], cwd=dir_b),
        subprocess.Popen([py, "-m", "http.server", "18765"], cwd=dir_c,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
    ]
    try:
        time.sleep(1.0)
        table = ProcTable()
        details = table.scan({p.pid for p in procs})
        attributor = Attributor()
        expectations = (
            (procs[0], proj_a, "python train.py（.git + pyproject）"),
            (procs[1], proj_b, "python -c（工作目录推断）"),
            (procs[2], proj_c, "python -m（无标记目录）"),
        )
        for proc, expected, label in expectations:
            detail = details.get(proc.pid)
            check(f"能读取 PID {proc.pid} 的命令行", bool(detail and detail.cmdline),
                  ((detail.cmdline if detail else "进程未被枚举") or "读取失败")[:100])
            if not detail:
                continue
            attr = attributor.attribute(detail)
            same = os.path.normcase(attr.root).rstrip("\\") == os.path.normcase(expected).rstrip("\\")
            check(f"项目回溯正确：{label}", same,
                  f"→ {attr.project} | root={attr.root} | 置信度 {attr.confidence} | "
                  f"依据 {'、'.join(attr.evidence)}")
            if label.startswith("python train.py"):
                check("识别到 git 分支", attr.branch == "feature/vram", attr.branch)
                check("识别到虚拟环境", attr.venv == ".venv", attr.venv)
    finally:
        for proc in procs:
            proc.terminate()


def test_monitor(base_tmp: str) -> None:
    section("3. 采样与聚合")
    py = sys.executable
    proj = os.path.join(base_tmp, "vision-lab")
    child = subprocess.Popen([py, os.path.join(proj, "train.py")], cwd=proj)

    class _Cfg:
        interval = 2.0
        show_all_python = True
        overrides: list = []
        paused = False

    try:
        time.sleep(1.2)
        monitor = Monitor(_Cfg())
        snap = monitor.sample()
        check("生成快照", snap is not None,
              f"{snap.elapsed_ms:.0f} ms / {len(snap.rows)} 进程 / {len(snap.groups)} 项目")
        for group in snap.groups[:8]:
            print(f"    {group.total_mib:8.1f} MiB  {group.name:<26} {group.confidence:<2}  "
                  f"{group.root}")
            for row in group.rows[:3]:
                print(f"             └─ {row.name} pid={row.pid} {row.vram_mib:.1f} MiB  "
                      f"{row.script_name}")
        check("聚合结果有序",
              all(snap.groups[i].total_mib >= snap.groups[i + 1].total_mib
                  for i in range(len(snap.groups) - 1)))
        row = next((r for r in snap.rows if r.pid == child.pid), None)
        check("子进程出现在快照中", row is not None, row.script_name if row else "未找到")
        if row:
            check("子进程归属正确项目",
                  os.path.normcase(row.attribution.root).rstrip("\\")
                  == os.path.normcase(proj).rstrip("\\"), row.attribution.root)
            check("命令行摘要可读", bool(row.summary), row.summary[:80])
        check("tooltip 长度合规", len(snap.tooltip()) <= 127, f"{len(snap.tooltip())} 字符")
    finally:
        child.terminate()


def test_icons(base_tmp: str) -> None:
    section("4. 托盘图标")
    ico = os.path.join(base_tmp, "gpumon-test.ico")
    icons.write_window_icon(ico)
    check("生成 .ico", os.path.getsize(ico) > 1000, f"{os.path.getsize(ico)} 字节")
    hicon = win32.make_hicon(icons.render(32, "#35d399", 45), 32, 32)
    check("生成 HICON", bool(hicon), hex(hicon or 0))
    win32.destroy_icon(hicon)


def main() -> int:
    base_tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_selftest_tmp")
    shutil.rmtree(base_tmp, ignore_errors=True)
    os.makedirs(base_tmp, exist_ok=True)
    try:
        test_gpu()
        test_attribution(base_tmp)
        test_monitor(base_tmp)
        test_icons(base_tmp)
    finally:
        shutil.rmtree(base_tmp, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} 项失败：")
        for name in failures:
            print("  -", name)
        return 1
    print("全部检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
