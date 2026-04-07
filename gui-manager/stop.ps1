
Write-Host "============================================================"
Write-Host "  gui-manager - Stopping servers"
Write-Host "============================================================"
Write-Host ""

$venv = $env:LOCALAPPDATA + "\upstitch-venvs\gui-manager"
$rundir = $env:LOCALAPPDATA + "\upstitch-tools\gui-manager"

# Kill backend: python.exe from our venv running uvicorn
$backend = Get-WmiObject Win32_Process | Where-Object {
    $_.ExecutablePath -like "$venv\Scripts\python.exe"
}
if ($backend) {
    $backend | ForEach-Object {
        $id = $_.ProcessId
        taskkill /F /T /PID $id 2>&1 | Out-Null
        Write-Host ("Stopped backend  (PID " + $id + ").")
    }
} else {
    Write-Host "Backend  -- not running."
}

# Kill frontend: node.exe with vite in commandline
$frontend = Get-WmiObject Win32_Process | Where-Object {
    $_.Name -eq "node.exe" -and $_.CommandLine -like "*vite*"
}
if ($frontend) {
    $frontend | ForEach-Object {
        $id = $_.ProcessId
        taskkill /F /T /PID $id 2>&1 | Out-Null
        Write-Host ("Stopped frontend (PID " + $id + ").")
    }
} else {
    Write-Host "Frontend -- not running."
}

Remove-Item "$rundir\backend.pid"  -EA SilentlyContinue
Remove-Item "$rundir\frontend.pid" -EA SilentlyContinue

Write-Host ""
Write-Host "Done."
Start-Sleep 2
