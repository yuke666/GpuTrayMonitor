# 更新记录 · Changelog

## v1.0.0 — 2026-11-17

首个公开版本。

### 功能

- 托盘常驻的 GPU 显存监控：托盘图标按压力变色（环形仪表），悬停显示紧凑摘要。
- 任务栏关键指标条：`已用/最大显存 · 占用比 · 使用率`，可沿任务栏拖动，跟随系统浅色/深色主题。
- 主窗口按 **项目** 聚合进程，能把每个 `python.exe` 回溯到所属工程目录，并识别 venv / conda 与 git 分支。
- 进程详情面板：命令行、工作目录、环境变量、解释器、虚拟环境、git 分支；支持导出 CSV。
- 独显 / 核显双卡片 + 双序列曲线（独显绿色、核显蓝紫）。
- 双形态交付：免安装 exe（PyInstaller onedir，内置 Python 3.13）与脚本（Python 3.10+）。
- 启动即进托盘：主窗口「设置 ▾」与托盘菜单共享四项开关，状态实时同步。
- 开机自启（启动文件夹写入 `.vbs`）。
- 托盘图标自检：`--selftest-tray` 打印注册结果与 Win32 错误码。

### 数据来源

- NVML（`nvml.dll`）：显存总量/已用、使用率、温度。
- PDH 性能计数器 `\GPU Process Memory(*)\Local Usage` / `Non Local Usage`：每进程显存。
- PDH `\GPU Engine(*)\Utilization Percentage`：每进程 GPU 使用率（核显）。
- DXGI `IDXGIFactory1::EnumAdapters1`：适配器识别与 LUID 映射。
- PEB（`NtQueryInformationProcess`）：命令行、工作目录、环境变量。

### 打包与签名

- `--onedir` 目录版（24 MB），不向 `%TEMP%` 解压，避免 "Could not create temporary directory!"。
- 自签名代码签名证书（`CN=GpuTrayMonitor Self-Signed Code Signing`，有效期至 2029-11-16），
  配套 `install-cert.bat` / `uninstall-cert.bat`。
- `setup-this-pc.bat`：换机一键配置（信任证书、清下载标记、关闭旧式附件提示、检查 SmartScreen、建桌面快捷方式），
  全部为当前用户级操作，不需要管理员权限。

### 已知限制

- 仅支持 Windows。
- 自签名证书无法获得 SmartScreen 云端声誉：直接把 exe 拷到别的机器双击仍可能被蓝色拦截，
  推荐用桌面快捷方式（启动 `pythonw.exe`）或购买 CA 证书。
- 显存数值来自 Windows 计数器，与 `nvidia-smi` 可能略有差异。
- 项目回溯为启发式规则，特殊目录结构可通过 `overrides` 配置修正。
