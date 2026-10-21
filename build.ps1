<#
    Build Gantz.exe for Windows.

    Usage (from the repository root, in PowerShell):
        .\build.ps1                 # single-file Gantz.exe
        .\build.ps1 -Directory      # folder build (starts faster, fewer AV issues)
        .\build.ps1 -SkipTests

    The result is written to .\dist\
#>
[CmdletBinding()]
param(
    [switch]$Directory,
    [switch]$SkipTests,
    [switch]$NoVenv
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($text) {
    Write-Host ""
    Write-Host "==> $text" -ForegroundColor Cyan
}

Write-Step "Checking Python"
$python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
& $python --version
if ($LASTEXITCODE -ne 0) {
    throw "Python was not found. Install Python 3.10+ from https://www.python.org/downloads/ and tick 'Add python.exe to PATH'."
}

if (-not $NoVenv) {
    Write-Step "Preparing a build environment (.venv)"
    if (-not (Test-Path ".venv")) {
        & $python -m venv .venv
    }
    $python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
}

Write-Step "Installing PyInstaller"
& $python -m pip install --upgrade pip --quiet
& $python -m pip install -r requirements-build.txt --quiet

Write-Step "Generating the application icon"
& $python tools\make_icon.py

if (-not $SkipTests) {
    Write-Step "Running the test suite"
    & $python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw "Tests failed - build stopped." }
}

Write-Step "Building Gantz.exe"
$env:GANTZ_ONEDIR = if ($Directory) { "1" } else { "0" }
& $python -m PyInstaller --noconfirm --clean gantz.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$exe = Join-Path $PSScriptRoot "dist\Gantz.exe"
if ($Directory -and (Test-Path "dist\Gantz")) {
    $exe = Join-Path $PSScriptRoot "dist\Gantz\Gantz.exe"
}

if (-not (Test-Path $exe)) { throw "Build finished but $exe was not found." }

$size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Step "Done"
Write-Host "  $exe  ($size MB)" -ForegroundColor Green
Write-Host ""
Write-Host "Copy Gantz.exe anywhere - it runs offline, with no installer." -ForegroundColor DarkGray
