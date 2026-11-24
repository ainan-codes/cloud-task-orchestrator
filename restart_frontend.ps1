# Root PowerShell runner for detached frontend restart
& "$PSScriptRoot\scripts\restart_frontend.ps1"
exit $LASTEXITCODE
