# Offline install of DICOM_discovery from the wheelhouse bundle — Windows counterpart of
# install-offline.sh, for a workstation or server with NO internet access. Nothing is
# downloaded: every wheel, including a recent pip, ships in .\wheelhouse next to this script.
#
#   powershell -ExecutionPolicy Bypass -File install-offline.ps1
#   powershell -ExecutionPolicy Bypass -File install-offline.ps1 -Dest C:\Tools\dicom-discovery -Python py -3.12
#
# The bundle must match the target: same Python minor version (bundle name *-py3.X-*) on
# 64-bit Windows.
param(
    [string]$Dest = "$env:LOCALAPPDATA\dicom-discovery",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$wheels = Join-Path $here "wheelhouse"

if (-not (Test-Path $wheels)) { throw "wheelhouse\ not found next to $PSCommandPath" }

& $Python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Python >= 3.9 required ('$Python' is $(& $Python -V))" }

& $Python -m venv (Join-Path $Dest "venv")
$venvPython = Join-Path $Dest "venv\Scripts\python.exe"

# Old pips cannot install manylinux/win wheels built with recent metadata: upgrade pip first,
# from the bundle.
& $venvPython -m pip install --no-index --find-links $wheels --upgrade pip
& $venvPython -m pip install --no-index --find-links $wheels DICOM_discovery

$protocol = Join-Path $here "protocol.brain_rt_followup.yaml"
if (Test-Path $protocol) { Copy-Item $protocol $Dest }

$cli = Join-Path $Dest "venv\Scripts\dicom-discovery.exe"
& $cli --version
Write-Host "Installed: $cli"
Write-Host "Check a share without writing anything:"
Write-Host "  & '$cli' doctor --root D:\DICOM --output-dir D:\qc"
