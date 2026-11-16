"""演示 / 验收辅助：生成三个仿真项目，并各拉起一个占用显存的 python 进程。

    python tests/demo.py          # 创建项目 + 启动进程
    python tests/demo.py clean    # 结束演示进程并删除目录

用来直观验证「多个 python.exe 分别回溯到不同项目」以及显存聚合是否正确。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_ROOT = os.path.join(ROOT, "demo")
PY = sys.executable

# 每个演示项目里的脚本：申请显存后常驻，模拟训练 / 推理进程
SCRIPT = '''"""演示用脚本：申请 {vram} MiB 显存后常驻，验证显存归属。"""
import ctypes
import time


def allocate(mb: int) -> str:
    try:
        cuda = ctypes.WinDLL("nvcuda.dll")
    except OSError:
        return "无 NVIDIA 驱动，跳过显存申请"
    if cuda.cuInit(0) != 0:
        return "cuInit 失败"
    device = ctypes.c_int()
    cuda.cuDeviceGet(ctypes.byref(device), 0)
    context = ctypes.c_void_p()
    cuda.cuCtxCreate_v2(ctypes.byref(context), 0, device)
    pointer = ctypes.c_void_p()
    code = cuda.cuMemAlloc_v2(ctypes.byref(pointer), ctypes.c_size_t(mb * 1024 * 1024))
    return f"已申请 {{mb}} MiB 显存" if code == 0 else f"申请失败 code={{code}}"


if __name__ == "__main__":
    print("{name}: " + allocate({vram}), flush=True)
    time.sleep(86400)
'''

PROJECTS = [
    {
        "name": "Qwen-Finetune",
        "vram": 3584,
        "script": "src/train_lora.py",
        "args": ["--model", "Qwen3-14B", "--lora-r", "16", "--output", "outputs/qwen3-lora"],
        "env": {"VIRTUAL_ENV": os.path.join(DEMO_ROOT, "Qwen-Finetune", ".venv")},
        "files": {
            ".git/HEAD": "ref: refs/heads/feat/lora-merge\n",
            "pyproject.toml": "[project]\nname = \"qwen-finetune\"\nversion = \"0.3.1\"\n",
            "requirements.txt": "torch==2.6.0\ntransformers==4.49.0\npeft==0.14.0\n",
            "README.md": "# Qwen-Finetune\n\nLoRA 微调实验。\n",
            "configs/lora.yaml": "r: 16\nalpha: 32\n",
        },
        "dirs": [".venv/Lib/site-packages", "outputs", "data"],
    },
    {
        "name": "ComfyUI-API",
        "vram": 2048,
        "script": "main.py",
        "args": ["--listen", "0.0.0.0", "--port", "8188"],
        "env": {},
        "files": {
            ".git/HEAD": "ref: refs/heads/main\n",
            "requirements.txt": "torch\ndiffusers\nfastapi\nuvicorn\n",
            "custom_nodes/README.md": "custom nodes\n",
        },
        "dirs": ["models/checkpoints", "custom_nodes"],
    },
    {
        "name": "data-pipeline",
        "vram": 768,
        "script": "dags/etl_embeddings.py",
        "args": ["--batch-size", "256"],
        "env": {},
        "files": {"requirements.txt": "sentence-transformers\npandas\n"},
        "dirs": ["dags", "notebooks"],
    },
]

PID_FILE = os.path.join(DEMO_ROOT, ".pids.json")


def make_project(spec: dict) -> str:
    base = os.path.join(DEMO_ROOT, spec["name"])
    for rel in spec.get("dirs", []):
        os.makedirs(os.path.join(base, rel), exist_ok=True)
    files = dict(spec.get("files", {}))
    files[spec["script"]] = SCRIPT.format(name=spec["name"], vram=spec["vram"])
    for rel, content in files.items():
        path = os.path.join(base, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    return base


def start() -> None:
    os.makedirs(DEMO_ROOT, exist_ok=True)
    pids = {}
    for spec in PROJECTS:
        base = make_project(spec)
        env = dict(os.environ)
        env.update(spec.get("env", {}))
        log = open(os.path.join(DEMO_ROOT, f"{spec['name']}.log"), "w", encoding="utf-8")
        proc = subprocess.Popen(
            [PY, spec["script"]] + spec["args"], cwd=base, env=env,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=0x00000008,  # DETACHED_PROCESS
        )
        pids[spec["name"]] = proc.pid
        print(f"启动 {spec['name']:<16} pid={proc.pid:<7} 显存 {spec['vram']} MiB  {base}")
    with open(PID_FILE, "w", encoding="utf-8") as fh:
        json.dump(pids, fh, ensure_ascii=False, indent=2)
    time.sleep(0.8)
    print("演示进程已启动，打开监视器即可看到按项目聚合的结果。")


def clean() -> None:
    try:
        with open(PID_FILE, "r", encoding="utf-8") as fh:
            pids = json.load(fh)
    except Exception:
        pids = {}
    for name, pid in pids.items():
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        print("结束", name, pid)
    shutil.rmtree(DEMO_ROOT, ignore_errors=True)
    print("已清理演示目录")


if __name__ == "__main__":
    clean() if len(sys.argv) > 1 and sys.argv[1] == "clean" else start()
