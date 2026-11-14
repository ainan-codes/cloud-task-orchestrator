# scripts/restart_backend.ps1
# Automates detached restart and health verification of FastAPI backend server (port 8000).

$ErrorActionPreference = "Stop"

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $workspaceRoot "backend"
$port = 8000
$healthUrl = "http://127.0.0.1:$port/api/health"
$timeoutSeconds = 45

Write-Host "=== Backend Server Lifecycle Manager ==="

# 1. Stop existing process on port 8000
Write-Host "1. Checking for existing process on port $port..."
$connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
if ($connections) {
    $procIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($pidToKill in $procIds) {
        if ($pidToKill -gt 0) {
            Write-Host "   Terminating existing process on port $port (PID: $pidToKill)..."
            Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
        }
    }
}
$uvicornProcs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*uvicorn*app:app*" }
foreach ($p in $uvicornProcs) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    } catch { }
}
Start-Sleep -Milliseconds 1000

# 2. Start uvicorn as detached/background process using WMI Win32_Process
Write-Host "2. Starting detached Uvicorn process in: $backendDir..."
$logFile = Join-Path $workspaceRoot "uvicorn.log"

$cmd = "cmd.exe /c python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload > `"$logFile`" 2>&1"
$res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $cmd
    CurrentDirectory = $backendDir
}

if ($res.ReturnValue -ne 0) {
    Write-Error "Failed to spawn detached Uvicorn process (Win32_Process ReturnValue: $($res.ReturnValue))."
    exit 1
}

$spawnedPid = $res.ProcessId
Write-Host "   Detached process spawned (PID: $spawnedPid). Log output: $logFile"

# 3. Wait briefly for startup & perform HTTP health check
Write-Host "3. Verifying HTTP health at $healthUrl (Timeout: ${timeoutSeconds}s)..."
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$isReachable = $false

while ($stopwatch.Elapsed.TotalSeconds -lt $timeoutSeconds) {
    Start-Sleep -Milliseconds 600
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($response -and $response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
            $isReachable = $true
            break
        }
    } catch {
        # Retry until timeout
    }
}

# 4. Return exit code 0 when reachable
if ($isReachable) {
    $elapsed = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 1)
    Write-Host "SUCCESS: Backend server is running and healthy at $healthUrl (responded in ${elapsed}s)."
    exit 0
} else {
    Write-Error "ERROR: Backend server failed to respond at $healthUrl within $timeoutSeconds seconds. Check log: $logFile"
    if (Test-Path $logFile) {
        Write-Host "Recent log output:"
        Get-Content $logFile -Tail 20 | Write-Host
    }
    exit 1
}
