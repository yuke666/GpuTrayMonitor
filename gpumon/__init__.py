"""显存监视器 —— 托盘常驻的 GPU 显存监控，并按项目回溯 python 进程。

模块划分
--------
win32    Win32 / PEB 底层封装
gpu      NVML + PDH + DXGI 数据采集
procs    进程枚举与内省
attrib   项目回溯引擎
monitor  采样循环与快照聚合
tray     托盘图标与菜单
ui       tkinter 主界面
config   配置 / 日志 / 开机自启
app      入口装配
"""

__version__ = "1.1.0"
__all__ = ["__version__"]
