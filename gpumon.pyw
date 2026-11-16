"""pythonw.exe 入口：无控制台窗口启动显存监视器。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gpumon.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
