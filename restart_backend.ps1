# Root PowerShell runner for detached backend restart
& "$PSScriptRoot\scripts\restart_backend.ps1"
exit $LASTEXITCODE
