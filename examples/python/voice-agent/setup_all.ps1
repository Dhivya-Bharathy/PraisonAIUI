# One command: start app + tunnel + configure voice provider automatically.
Set-Location $PSScriptRoot
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env — add VOICE_* keys" ; exit 1 }

.\start_dev.ps1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Configuring voice provider via API..." -ForegroundColor Cyan
py -3.13 setup_voice_provider.py
exit $LASTEXITCODE
