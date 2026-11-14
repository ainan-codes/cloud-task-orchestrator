# scripts/restart_frontend.ps1
# Automates detached restart and health verification of Vite dev server (port 5173).

$ErrorActionPreference = "Stop"

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$frontendDir = Join-Path $workspaceRoot "frontend"
$port = 5173
$healthUrl = "http://localhost:$port"
$timeoutSeconds = 25

Write-Host "=== Frontend Dev Server Lifecycle Manager ==="

# 1. Stop existing process on port 5173
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
    Start-Sleep -Milliseconds 750
} else {
    Write-Host "   Port $port is clear."
}

# Also clean up any lingering node processes running vite
$viteProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | 
    Where-Object { $_.CommandLine -like "*vite*" -and $_.ProcessId -ne $PID }
if ($viteProcs) {
    foreach ($vp in $viteProcs) {
        Write-Host "   Terminating lingering Vite process (PID: $($vp.ProcessId))..."
        Stop-Process -Id $vp.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 500
}

# 2. Start npm run dev as detached/background process
# 3. Do not wait for Vite to exit
Write-Host "2. Starting detached Vite process in: $frontendDir..."
$logFile = Join-Path $frontendDir "vite_dev.log"

$cmd = "cmd.exe /c npm run dev > `"$logFile`" 2>&1"
$res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $cmd
    CurrentDirectory = $frontendDir
}

if ($res.ReturnValue -ne 0) {
    Write-Error "Failed to spawn detached Vite process (Win32_Process ReturnValue: $($res.ReturnValue))."
    exit 1
}

$spawnedPid = $res.ProcessId
Write-Host "   Detached process spawned (PID: $spawnedPid). Log output: $logFile"

# 4. Wait briefly for startup & 5. Perform HTTP health check
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

# 6. Return exit code 0 when reachable
# 7. Return error only if startup failed
# 8. Vite keeps running independently
if ($isReachable) {
    $elapsed = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 1)
    Write-Host "SUCCESS: Vite dev server is running and healthy at $healthUrl (responded in ${elapsed}s)."
    exit 0
} else {
    Write-Error "ERROR: Vite dev server failed to respond at $healthUrl within $timeoutSeconds seconds. Check log: $logFile"
    if (Test-Path $logFile) {
        Write-Host "Recent log output:"
        Get-Content $logFile -Tail 20 | Write-Host
    }
    exit 1
}
