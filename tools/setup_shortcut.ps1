param(
    [Parameter(Mandatory = $true)][string]$Root
)

$ErrorActionPreference = 'Stop'
$pyw = $null

$venv = Join-Path $Root '.venv\Scripts\pythonw.exe'
if (Test-Path $venv) { $pyw = (Resolve-Path $venv).Path }

if (-not $pyw) {
    foreach ($base in @((Join-Path $env:LOCALAPPDATA 'Programs\Python'), $env:ProgramFiles)) {
        if ($pyw) { break }
        if (-not (Test-Path $base)) { continue }
        $hit = Get-ChildItem $base -Filter pythonw.exe -Recurse -Depth 2 -ErrorAction SilentlyContinue |
               Sort-Object FullName -Descending | Select-Object -First 1
        if ($hit) { $pyw = $hit.FullName }
    }
}

if (-not $pyw) {
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($cmd) { $pyw = $cmd.Source }
}

if (-not $pyw) {
    Write-Host '      [SKIP] pythonw.exe not found, desktop shortcut not created'
    exit 0
}

$entry = Join-Path $Root 'gpumon.pyw'
$icon = Join-Path $Root 'portable\GpuMon.exe'
if (-not (Test-Path $icon)) { $icon = $pyw }

$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop 'GPU Monitor.lnk'
$q = [char]34

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($link)
$sc.TargetPath = $pyw
$sc.Arguments = $q + $entry + $q + ' --hidden'
$sc.WorkingDirectory = $Root
$sc.IconLocation = $icon + ',0'
$sc.WindowStyle = 1
$sc.Description = 'GPU Tray Monitor'
$sc.Save()

Write-Host ('      shortcut: ' + $link)
Write-Host ('      target  : ' + $pyw)
Write-Host ('      args    : ' + $sc.Arguments)
