# Sign the portable exe with a locally generated self-signed code signing
# certificate, and (optionally) trust it on this machine.
#
#   # sign (creates a 3-year cert in CurrentUser\My, signs, trusts locally)
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\sign_exe.ps1 -Exe ".\portable\GpuMon.exe"
#
#   # remove everything it created
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\sign_exe.ps1 -Exe ".\portable\GpuMon.exe" -Uninstall
#
# Notes
#   * All certificate stores used are CurrentUser, so no administrator rights
#     are needed.
#   * Importing into CurrentUser\Root shows a Windows security prompt; that is
#     expected -- the prompt is what makes the signature "valid" locally.
#   * A self-signed certificate is only trusted on machines that imported it.
#     It does NOT give the executable SmartScreen reputation on other machines.
#   * The signature is bound to the file: re-sign after every rebuild.

param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [string]$Subject = "CN=GpuTrayMonitor Self-Signed Code Signing",
    [int]$Years = 3,
    [string]$TimestampServer = "http://timestamp.digicert.com",
    [switch]$Uninstall,
    [switch]$NoTrust,
    [switch]$TrustOnly,
    [string]$CerPath = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $root "build\signing"
$pfxPath = Join-Path $outDir "GpuTrayMonitor-selfsigned.pfx"
$cerPath = Join-Path $outDir "GpuTrayMonitor-selfsigned.cer"
$pfxPassword = "gputraymonitor"

function Import-Trust([string]$path) {
    if (-not (Test-Path $path)) { throw "certificate not found: $path" }
    $cert = Get-PfxCertificate -FilePath $path -ErrorAction SilentlyContinue
    if (-not $cert) {
        $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 $path
    }
    foreach ($store in @("TrustedPublisher", "Root")) {
        $existing = Get-ChildItem "Cert:\CurrentUser\$store" -ErrorAction SilentlyContinue |
                    Where-Object { $_.Thumbprint -eq $cert.Thumbprint }
        if ($existing) {
            Write-Host "already trusted in CurrentUser\$store : $($cert.Thumbprint)"
            continue
        }
        Import-Certificate -FilePath $path -CertStoreLocation "Cert:\CurrentUser\$store" | Out-Null
        Write-Host "imported into CurrentUser\$store : $($cert.Thumbprint)"
    }
}

# --- trust-only mode: run this on another machine, no signing involved ------
if ($TrustOnly) {
    $source = if ($CerPath) { $CerPath } else { $cerPath }
    Import-Trust (Resolve-Path $source).Path
    Write-Host ""
    $check = Get-AuthenticodeSignature $Exe
    Write-Host "exe    : $Exe"
    Write-Host "status : $($check.Status)"
    Write-Host "signer : $($check.SignerCertificate.Subject)"
    exit 0
}

function Remove-SelfSigned {
    foreach ($store in @("My", "TrustedPublisher", "Root")) {
        $path = "Cert:\CurrentUser\$store"
        Get-ChildItem $path -ErrorAction SilentlyContinue |
            Where-Object { $_.Subject -eq $Subject -and $_.HasPrivateKey -eq ($store -eq "My") } |
            ForEach-Object {
                Write-Host "remove $store : $($_.Thumbprint)"
                Remove-Item ("$path\" + $_.Thumbprint) -Force -ErrorAction SilentlyContinue
            }
    }
}

if ($Uninstall) {
    Remove-SelfSigned
    if (Test-Path $pfxPath) { Remove-Item $pfxPath -Force }
    if (Test-Path $cerPath) { Remove-Item $cerPath -Force }
    Write-Host "done: certificate and signing artefacts removed."
    exit 0
}

if (-not (Test-Path $Exe)) {
    throw "exe not found: $Exe"
}
$Exe = (Resolve-Path $Exe).Path
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

# 1) certificate (CurrentUser\My, code signing EKU, 3 years)
$cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
        Where-Object { $_.Subject -eq $Subject } | Select-Object -First 1
if (-not $cert) {
    $cert = New-SelfSignedCertificate `
        -Type CodeSigningCert `
        -Subject $Subject `
        -CertStoreLocation Cert:\CurrentUser\My `
        -KeyExportPolicy Exportable `
        -KeyUsage DigitalSignature `
        -NotAfter (Get-Date).AddYears($Years)
    Write-Host "created certificate: $($cert.Thumbprint)"
} else {
    Write-Host "reusing certificate: $($cert.Thumbprint)"
}

# 2) export .pfx / .cer for reuse on other machines
$secure = ConvertTo-SecureString -String $pfxPassword -Force -AsPlainText
Export-PfxCertificate -Cert $cert -FilePath $pfxPath -Password $secure | Out-Null
Export-Certificate -Cert $cert -FilePath $cerPath | Out-Null
Write-Host "exported: $pfxPath"

# 3) sign (falls back to a signature without timestamp when the server is unreachable)
$signed = $false
if ($TimestampServer) {
    try {
        $sig = Set-AuthenticodeSignature -FilePath $Exe -Certificate $cert `
               -HashAlgorithm SHA256 -TimestampServer $TimestampServer
        $signed = ($sig.Status -eq "Valid")
        if (-not $signed) { Write-Host "timestamp attempt: $($sig.Status) $($sig.StatusMessage)" }
    } catch {
        Write-Host "timestamp failed: $($_.Exception.Message)"
    }
}
if (-not $signed) {
    $sig = Set-AuthenticodeSignature -FilePath $Exe -Certificate $cert -HashAlgorithm SHA256
    Write-Host "signed without timestamp: $($sig.Status)"
}

# 4) trust locally (CurrentUser stores)
if (-not $NoTrust) {
    Import-Trust $cerPath
}

# 5) report
$check = Get-AuthenticodeSignature $Exe
Write-Host ""
Write-Host "exe      : $Exe"
Write-Host "status   : $($check.Status)"
Write-Host "signer   : $($check.SignerCertificate.Subject)"
Write-Host "valid to : $($cert.NotAfter)"
