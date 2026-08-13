# Starts Sunshine (backend :8000 + frontend :5173) as detached processes.
# Safe to re-run: skips anything already listening. Logs in backend\storage\temp\.
$root = $PSScriptRoot
$logs = Join-Path $root "backend\storage\temp"
New-Item -ItemType Directory -Force $logs | Out-Null

function Test-Port($port) {
    (Test-NetConnection -ComputerName "127.0.0.1" -Port $port -WarningAction SilentlyContinue).TcpTestSucceeded
}

if (Test-Port 8000) {
    Write-Host "Backend already running on :8000"
} else {
    Start-Process -FilePath "python" -ArgumentList "run.py" -WorkingDirectory (Join-Path $root "backend") -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs "backend.out.log") -RedirectStandardError (Join-Path $logs "backend.err.log")
    Write-Host "Backend starting on :8000 (logs: backend\storage\temp\backend.err.log)"
}

if (Test-Port 5173) {
    Write-Host "Frontend already running on :5173"
} else {
    Start-Process -FilePath "node" -ArgumentList "dev.js" -WorkingDirectory (Join-Path $root "frontend") -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs "frontend.out.log") -RedirectStandardError (Join-Path $logs "frontend.err.log")
    Write-Host "Frontend starting on :5173 (logs: backend\storage\temp\frontend.err.log)"
}

# Backend imports take ~15-30s cold; wait until the API answers.
foreach ($i in 1..45) {
    try {
        Invoke-WebRequest -Uri "http://localhost:8000/api/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
        Write-Host "Backend is up."
        break
    } catch { Start-Sleep -Seconds 1 }
}

Write-Host "Sunshine ready: http://localhost:5173"
