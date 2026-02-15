$conns = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
if ($conns) {
    $pids = $conns.OwningProcess | Where-Object { $_ -gt 0 } | Sort-Object -Unique
    foreach ($p in $pids) {
        Write-Host "Killing PID $p"
        taskkill /F /PID $p
    }
} else {
    Write-Host "No process on port 8000"
}
