$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create virtual environment.' }
}
& '.venv\Scripts\python.exe' -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Build dependency installation failed.' }
& '.venv\Scripts\python.exe' package_release.py
if ($LASTEXITCODE -ne 0) { throw 'Tests, build, or packaging failed.' }
Write-Output 'Built dist\<version>\BambuAccountSwitcher.exe and output\*.zip'
