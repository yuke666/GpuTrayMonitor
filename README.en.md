# GPU Tray Monitor · 显存监视器

[中文](README.md) · **English**

A GPU VRAM monitor that lives in the Windows system tray: the tray icon shows VRAM pressure, a compact strip
on the taskbar shows the key numbers, and the main window groups processes by **project** — every `python.exe`
is traced back to the project folder it belongs to (including the conda/venv and the git branch).

![Main window](docs/screenshot.png)

![Taskbar strip](docs/taskbar.png)

## Features

- **No third-party dependencies** — Python standard library plus built-in Windows APIs
  (NVML, PDH performance counters, DXGI, process PEB). No torch, no psutil, no pynvml.
- **Per-project attribution** — reads each process's command line, working directory and environment from its
  PEB, then walks up the directory tree looking for `.git`, `pyproject.toml`, `requirements.txt`.
  Virtualenvs and git branches are detected and shown.
- **Tray first** — double-click the launcher and it goes straight to the tray without opening a window.
- **Taskbar strip** — `used / total VRAM · pressure % · GPU utilization`, positioned on the taskbar,
  draggable, follows light/dark theme.
- **Two builds** — a self-contained `GpuMon.exe` (bundled Python, no install) or the scripts (needs Python 3.10+).
- **Windows 10/11 x64.** Tested on RTX 5090 D v2 24 GB + Intel iGPU, Windows 11 26H2.

## Install

### Option A — portable exe (no Python needed)

1. Copy the whole `portable` folder (the exe needs the `_internal` folder next to it).
2. Double-click `setup-this-pc.bat` once — it trusts the bundled certificate, clears the "downloaded" mark
   on every file, disables the legacy attachment prompt (per-user registry), checks SmartScreen and creates a
   desktop shortcut.
3. Double-click **GPU Monitor** on the desktop. The app goes straight to the tray.

Everything `setup-this-pc.bat` changes is per-user (HKCU), no administrator rights, and reversible:

| Undo | Run |
| --- | --- |
| Restore the "ask before running downloaded files" prompt | `portable\restore-launch-prompt.bat` |
| Stop trusting the certificate | `portable\uninstall-cert.bat` |
| Restore SmartScreen defaults | `portable\restore-smartscreen.bat` (admin) |

### Option B — scripts (Python 3.10+ with tkinter)

1. Keep `gpumon.pyw`, the `gpumon\` folder and `start-tray.vbs` together.
2. Double-click `start-tray.vbs` — it finds `pythonw.exe` in `.venv`, `%LOCALAPPDATA%\Programs\Python`,
   `%ProgramFiles%`, the `py` launcher or `PATH`, and shows a dialog if none is found.

| Launcher | Behaviour |
| --- | --- |
| `start-tray.vbs` | start hidden in the tray (`--hidden`) |
| `start-gpumon.vbs` | start with the main window (`--show`) |
| `debug-start.cmd` | console launcher, shows the log |

Command line: `python gpumon.pyw [--hidden | --show] [--verbose] [--selftest-tray]`.

## Usage

| Action | Result |
| --- | --- |
| Left-click tray icon | open the main window |
| Right-click tray icon | menu: show window, refresh, pause, sampling interval, all-Python toggle, taskbar strip, autostart, start-in-tray, quit |
| Click the taskbar strip | open the main window |
| Drag the taskbar strip | move it along the taskbar, position is remembered |
| Close the window | hide to the tray (it keeps monitoring) |

The main window lists one row per project plus its processes: VRAM, share of total, PID, adapter, command line.
Expand a project to see its processes. The right panel shows the full trace (argv, cwd, env, interpreter,
virtualenv, git branch) and lets you export the current snapshot to CSV.

### Settings

The main window's **设置 ▾** menu and the tray menu expose the same four switches, and either side updates the
other immediately:

| Switch | Effect |
| --- | --- |
| 开机自动启动 / Start with Windows | writes a `.vbs` into the Startup folder |
| 启动时进托盘（不弹窗口） / Start in tray | no main window on launch |
| 在任务栏显示关键指标 / Taskbar strip | the VRAM strip on the taskbar |
| 显示全部 Python 进程 / Show all Python processes | list processes without GPU memory too |

## How project attribution works

1. Enumerate processes, keep the ones whose image name looks like a Python interpreter.
2. Read the PEB (`NtQueryInformationProcess` + `RTL_USER_PROCESS_PARAMETERS`) for the command line,
   current directory and environment (including `VIRTUAL_ENV`, `CONDA_PREFIX`).
3. Pick the script path from the command line; fall back to the process working directory.
4. Walk up from the script/cwd, scoring markers: `.git` = 100, `pyproject.toml` = 80,
   `requirements.txt` = 40; below 35 the walk continues upward, and if nothing is found the anchor
   directory (script or cwd) is used.
5. Extract the git branch from `.git/HEAD`, detect the virtualenv/conda prefix, and present
   `project · branch · env`.

## Data sources

| Data | Source |
| --- | --- |
| Total / used VRAM, utilization, temperature | NVML (`nvml.dll`, no CUDA toolkit needed) |
| Per-process VRAM | PDH counters `\GPU Process Memory(*)\Local Usage` / `Non Local Usage` |
| Per-process GPU utilization | PDH `\GPU Engine(*)\Utilization Percentage` |
| Adapter identity / LUID mapping | DXGI `IDXGIFactory1::EnumAdapters1` |
| Per-process command line, cwd, env | PEB via `NtQueryInformationProcess` |

If NVML is unavailable (non-NVIDIA GPU) the app falls back to the PDH counters and still shows per-process data.

## Configuration

`%LOCALAPPDATA%\GpuTrayMonitor\config.json` (falls back to `<app>\data` when not writable).
Log: `monitor.log` in the same folder.

| Key | Default | Meaning |
| --- | --- | --- |
| `interval` | `2.0` | sampling interval, seconds |
| `show_all_python` | `true` | list Python processes without GPU memory |
| `paused` | `false` | start paused |
| `autostart` | `false` | start with Windows |
| `start_hidden` | `true` | start in the tray without a window |
| `taskbar_hud` | `true` | taskbar strip |
| `taskbar_hud_offset` / `taskbar_hud_y` | `10` / `null` | strip position (updated when dragged) |
| `overrides` | `[]` | custom attribution rules (`match` is a case-insensitive substring test) |

## Troubleshooting

**The exe shows "Open File - Security Warning"** — the file carries a "downloaded from the Internet" mark,
or the shell falls back to the legacy attachment prompt. Run `portable\unblock.bat`, and if it persists run
`portable\allow-launch.bat` (per-user; it also stops warnings for other downloaded apps).

**SmartScreen blocks the exe (blue screen)** — the signature is self-signed; SmartScreen relies on cloud
reputation and ignores locally trusted roots. Options: launch through the desktop shortcut (it starts
`pythonw.exe`, so no exe reputation check), or buy a CA-issued code signing certificate.

**No tray icon, banner says the tray was refused** — run `portable\GpuMon.exe --selftest-tray`
(or `python gpumon.pyw --selftest-tray`). `last error : 5` means access denied: the program is running
elevated, or the files carry a Low integrity label (`icacls "portable\GpuMon.exe" | findstr Mandatory`;
fix with `icacls "GpuTrayMonitor" /setintegritylevel (OI)(CI)M /T`).

**No tray icon after Explorer restarts** — the app re-registers on the `TaskbarCreated` message automatically.

## Build

```powershell
python tools\fetch_wheels.py                 # download PyInstaller and friends from PyPI
python -c "import zipfile,glob,os; d=r'.build\libs'; os.makedirs(d,exist_ok=True); [zipfile.ZipFile(w).extractall(d) for w in glob.glob(r'.build\wheels\*.whl')]"
$env:PYTHONPATH="$PWD\.build\libs"
python -m PyInstaller --noconfirm --clean --onedir --windowed --name GpuMon `
  --icon build\app.ico --version-file build\version.txt `
  --distpath dist --workpath .build\work --specpath .build gpumon.pyw
```

Then sign it (optional, self-signed):

```powershell
powershell -ExecutionPolicy Bypass -File tools\sign_exe.ps1 -Exe .\portable\GpuMon.exe
```

Use `--onedir`, not `--onefile`: a onefile build has to unpack into `%TEMP%`, which is blocked on machines
with strict policy or overly aggressive antivirus ("Could not create temporary directory!").

## Limitations

- Windows only (the whole data path is Windows-specific).
- VRAM figures come from Windows counters; they can differ slightly from `nvidia-smi` and are granular at
  the driver's own sampling rate.
- Attribution is heuristic. Configure `overrides` for exotic setups.
- Microsoft Store / MSIX Python installs may not expose a usable PEB.

## License

No license file yet — add one before distributing publicly.
