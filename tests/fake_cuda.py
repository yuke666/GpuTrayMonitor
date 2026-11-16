"""演示用：在指定项目中占用一部分显存的 python 进程。

    python tests/fake_cuda.py 3072      # 申请 3 GiB 显存并常驻

用于验证「同一个 python.exe 各自回溯到不同项目」以及显存统计是否准确。
没有 NVIDIA 显卡时会退化为纯 sleep 进程。
"""
from __future__ import annotations

import ctypes
import sys
import time


def allocate(mb: int) -> str:
    try:
        cuda = ctypes.WinDLL("nvcuda.dll")
    except OSError:
        return "未找到 nvcuda.dll，跳过显存申请"
    if cuda.cuInit(0) != 0:
        return "cuInit 失败"
    device = ctypes.c_int()
    if cuda.cuDeviceGet(ctypes.byref(device), 0) != 0:
        return "cuDeviceGet 失败"
    context = ctypes.c_void_p()
    if cuda.cuCtxCreate_v2(ctypes.byref(context), 0, device) != 0:
        return "cuCtxCreate 失败"
    pointer = ctypes.c_void_p()
    code = cuda.cuMemAlloc_v2(ctypes.byref(pointer), ctypes.c_size_t(mb * 1024 * 1024))
    if code != 0:
        return f"cuMemAlloc {mb} MiB 失败（code={code}）"
    return f"已申请 {mb} MiB 显存 @ 0x{pointer.value:x}"


if __name__ == "__main__":
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 2048
    print(allocate(size), flush=True)
    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        pass
