# Start voice-agent + Cloudflare tunnel for provider webhook URL.
# Usage: .\start_dev.ps1
#        .\start_dev.ps1 -Restart   # kill old app and reload code

param(
    [int]$Port = 8001,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Load-DotEnv {
    $envFile = Join-Path $Root ".env"
    if (-not (Test-Path $envFile)) { return }
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or $line -notmatch "=") { return }
        $key, $value = $line -split "=", 2
        if ($key -and -not [string]::IsNullOrWhiteSpace($key) -and -not (Get-Item "Env:$key" -ErrorAction SilentlyContinue)) {
            Set-Item -Path "Env:$key" -Value $value.Trim()
        }
    }
}

function Test-PortListening([int]$p) {
    foreach ($path in @("/health/live", "/health", "/api/voice/config")) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$p$path" -UseBasicParsing -TimeoutSec 15
            if ($r.StatusCode -eq 200) { return $true }
        } catch { continue }
    }
    return $false
}

function Stop-PortProcess([int]$p) {
    try {
        $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
        foreach ($conn in $conns) {
            if ($conn.OwningProcess) {
                Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
            }
        }
        if ($conns) {
            Start-Sleep -Seconds 2
            Write-Host "Stopped previous process on port $p"
        }
    } catch { }
}

function Clear-LogFile([string]$path) {
    if (-not (Test-Path $path)) { return }
    try {
        Remove-Item $path -Force -ErrorAction Stop
    } catch {
        try { Clear-Content $path -ErrorAction SilentlyContinue } catch { }
    }
}

function Start-VoiceApp([int]$p) {
    Write-Host "Starting voice-agent on port $p..."
    Stop-PortProcess $p
    $appLog = Join-Path $Root ".app.log"
    $appErr = Join-Path $Root ".app.err"
    Clear-LogFile $appLog
    Clear-LogFile $appErr
    Start-Process -FilePath "py" -ArgumentList "-3.13", "app.py" -WorkingDirectory $Root -WindowStyle Minimized `
        -RedirectStandardOutput $appLog -RedirectStandardError $appErr
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortListening $p) { break }
        Start-Sleep -Seconds 3
    }
    if (-not (Test-PortListening $p)) {
        $errText = ""
        if (Test-Path $appErr) { $errText = Get-Content $appErr -Raw -ErrorAction SilentlyContinue }
        if ($errText -match "Uvicorn running") {
            Write-Host "Server process up — waiting for HTTP endpoints..."
            Start-Sleep -Seconds 10
        }
    }
    if (-not (Test-PortListening $p)) {
        if (Test-Path $appErr) {
            Write-Host "App startup log:" -ForegroundColor Red
            Get-Content $appErr -Tail 20 | ForEach-Object { Write-Host $_ }
        }
        throw "App did not become ready on http://127.0.0.1:$p"
    }
    Write-Host "App ready: http://127.0.0.1:$p"
}

function Update-PublicUrl([string]$baseUrl) {
    $envFile = Join-Path $Root ".env"
    $content = Get-Content $envFile -Raw
    if ($content -match "PUBLIC_API_BASE_URL=.*") {
        $newContent = $content -replace "PUBLIC_API_BASE_URL=.*", "PUBLIC_API_BASE_URL=$baseUrl"
    } else {
        $newContent = $content.TrimEnd() + "`nPUBLIC_API_BASE_URL=$baseUrl`n"
    }
    Set-Content -Path $envFile -Value $newContent -NoNewline
    $env:PUBLIC_API_BASE_URL = $baseUrl
    Write-Host "Updated .env PUBLIC_API_BASE_URL=$baseUrl"
}

function Ensure-ServerSecret {
    $envFile = Join-Path $Root ".env"
    if (-not (Test-Path $envFile)) { return }
    $lines = Get-Content $envFile
    $secret = $env:VOICE_SERVER_URL_SECRET
    foreach ($line in $lines) {
        if ($line -match '^\s*VOICE_SERVER_URL_SECRET=(.+)$') {
            $secret = $Matches[1].Trim()
            break
        }
    }
    if (-not $secret) {
        $secret = [guid]::NewGuid().ToString()
        $updated = $false
        $newLines = foreach ($line in $lines) {
            if ($line -match '^\s*VOICE_SERVER_URL_SECRET=') {
                $updated = $true
                "VOICE_SERVER_URL_SECRET=$secret"
            } else { $line }
        }
        if (-not $updated) { $newLines += "VOICE_SERVER_URL_SECRET=$secret" }
        Set-Content -Path $envFile -Value ($newLines -join "`n")
        Write-Host "Generated VOICE_SERVER_URL_SECRET in .env"
    }
    $env:VOICE_SERVER_URL_SECRET = $secret
}

function Show-ProviderSetupSteps([string]$webhookUrl, [string]$secret) {
    Write-Host ""
    Write-Host "=== Provider webhook setup (Assistant -> Advanced -> Webhook Server) ===" -ForegroundColor Cyan
    Write-Host "Webhook URL:"
    Write-Host "  $webhookUrl"
    Write-Host ""
    Write-Host "Secret header:"
    Write-Host "  Name: X-Voice-Webhook-Secret"
    Write-Host "  Value: $secret"
    Write-Host "================================================================" -ForegroundColor Cyan
}

Load-DotEnv
Ensure-ServerSecret
$env:VOICE_AGENT_PORT = "$Port"

$needsRestart = $Restart.IsPresent
if (Test-PortListening $Port) {
    if ($needsRestart) {
        Stop-PortProcess $Port
    } else {
        try {
            $cfg = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/voice/config" -TimeoutSec 3
            if (-not $cfg.webhook_url -and $env:PUBLIC_API_BASE_URL) {
                Write-Host "App running but missing webhook URL — restarting to reload .env"
                $needsRestart = $true
                Stop-PortProcess $Port
            } else {
                Write-Host "App already running on port $Port"
            }
        } catch {
            Write-Host "App already running on port $Port"
        }
    }
}

if ($needsRestart -or -not (Test-PortListening $Port)) {
    Start-VoiceApp $Port
}

$public = $env:PUBLIC_API_BASE_URL
try {
    $null = Invoke-WebRequest -Uri "$public/api/voice/config" -UseBasicParsing -TimeoutSec 5
    Write-Host "Tunnel OK: $public"
} catch {
    Write-Host "Starting Cloudflare tunnel..."
    $log = Join-Path $Root ".tunnel.log"
    $logErr = "${log}.err"
    if (Test-Path $log) { Remove-Item $log -Force }
    if (Test-Path $logErr) { Remove-Item $logErr -Force }
    Start-Process -FilePath "cloudflared" -ArgumentList "tunnel", "--url", "http://127.0.0.1:$Port" `
        -RedirectStandardOutput $log -RedirectStandardError $logErr -WindowStyle Minimized
    $deadline = (Get-Date).AddSeconds(90)
    $newUrl = $null
    while ((Get-Date) -lt $deadline -and -not $newUrl) {
        Start-Sleep -Seconds 2
        foreach ($path in @($logErr, $log)) {
            if (-not (Test-Path $path)) { continue }
            $match = Select-String -Path $path -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
            if ($match) {
                $newUrl = $match.Matches[0].Value
                break
            }
        }
    }
    if (-not $newUrl) { throw "Could not read tunnel URL from $logErr" }
    Update-PublicUrl $newUrl
    Write-Host "Tunnel ready: $newUrl"
    if ($needsRestart -or -not (Test-PortListening $Port)) {
        Stop-PortProcess $Port
        Start-VoiceApp $Port
    }
}

$config = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/voice/config"
Write-Host "Voice configured: $($config.configured)"
Write-Host "Webhook URL: $($config.webhook_url)"
$webhook = if ($config.webhook_url) { $config.webhook_url } else { "$($env:PUBLIC_API_BASE_URL)/webhooks/voice" }

Write-Host ""
Write-Host "Configuring voice provider via API..." -ForegroundColor Cyan
py -3.13 setup_voice_provider.py
if ($LASTEXITCODE -eq 0) {
    Write-Host "Voice provider auto-configured (tools + webhook URL + auth)." -ForegroundColor Green
} else {
    Show-ProviderSetupSteps -webhookUrl $webhook -secret $env:VOICE_SERVER_URL_SECRET
}

Write-Host "Done. Dashboard: http://127.0.0.1:$Port"
