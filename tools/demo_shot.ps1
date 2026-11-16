param(
    [string]$Out = "",
    [int]$WaitSeconds = 9
)
$ErrorActionPreference = "Continue"
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class ShotApi {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  public struct RECT { public int Left, Top, Right, Bottom; }
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr c);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr FindWindow(string c, string t);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  public static IntPtr FindMainWindow(uint target) {
    IntPtr best = IntPtr.Zero; long bestArea = 0;
    EnumWindows((h, l) => {
      uint pid; GetWindowThreadProcessId(h, out pid);
      if (pid != target || !IsWindowVisible(h)) return true;
      RECT r; GetWindowRect(h, out r);
      long area = (long)(r.Right - r.Left) * (r.Bottom - r.Top);
      if (area > bestArea) { bestArea = area; best = h; }
      return true;
    }, IntPtr.Zero);
    return best;
  }
}
"@
[ShotApi]::SetProcessDpiAwarenessContext([IntPtr](-4)) | Out-Null
[ShotApi]::SetCursorPos(4, 4) | Out-Null
Add-Type -AssemblyName System.Windows.Forms, System.Drawing

$root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrEmpty($Out)) {
    $Out = Join-Path (Split-Path -Parent $root) "_shot.png"
}
$python = "C:\Users\cferi\AppData\Local\Programs\Python\Python313\python.exe"
$pythonw = "C:\Users\cferi\AppData\Local\Programs\Python\Python313\pythonw.exe"
$env:PYTHONIOENCODING = "utf-8"

Push-Location $root
try {
    # 清掉上一次遗留的实例（沙箱里 Get-Process 看不到它们，只能靠窗口句柄找）
    $stale = [ShotApi]::FindWindow("GpuMonHud", $null)
    if ($stale -and $stale -ne [IntPtr]::Zero) {
        [uint32]$stalePid = 0
        [ShotApi]::GetWindowThreadProcessId($stale, [ref]$stalePid) | Out-Null
        try { [System.Diagnostics.Process]::GetProcessById([int]$stalePid).Kill() } catch {}
        Start-Sleep -Milliseconds 600
    }
    & $python "tests\demo.py" | Out-Host
    $app = Start-Process -FilePath $pythonw -ArgumentList "gpumon.pyw" -WorkingDirectory $root -PassThru
    Start-Sleep -Seconds $WaitSeconds
    $app.Refresh()
    if (-not $app.HasExited) {
        $hwnd = [ShotApi]::FindMainWindow([uint32]$app.Id)
        if ($hwnd -ne [IntPtr]::Zero) {
            [ShotApi]::ShowWindow($hwnd, 9) | Out-Null
            [ShotApi]::SetForegroundWindow($hwnd) | Out-Null
            Start-Sleep -Milliseconds 1200
            $r = New-Object ShotApi+RECT
            [ShotApi]::GetWindowRect($hwnd, [ref]$r) | Out-Null
            "window: $($r.Left),$($r.Top) $($r.Right - $r.Left)x$($r.Bottom - $r.Top)"
        } else {
            "main window not found for pid $($app.Id)"
        }
    } else {
        "app exited with $($app.ExitCode)"
    }
    Start-Sleep -Milliseconds 800
    $vs = [System.Windows.Forms.SystemInformation]::VirtualScreen
    $bmp = New-Object System.Drawing.Bitmap $vs.Width, $vs.Height
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($vs.X, $vs.Y, 0, 0, $bmp.Size)
    $bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
    "saved $Out"
}
finally {
    if ($app -and -not $app.HasExited) { Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue }
    & $python "tests\demo.py" clean | Out-Host
    Pop-Location
}
