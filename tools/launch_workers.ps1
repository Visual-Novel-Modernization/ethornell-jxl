param([int]$Workers=6, [string]$GameDir='.')
$root = if ([System.IO.Path]::IsPathRooted($GameDir)) { $GameDir } else { (Resolve-Path $GameDir).Path }
$script = Join-Path $PSScriptRoot 'batch_jxl.ps1'

$processes = @()
for ($i = 0; $i -lt $Workers; $i++) {
    $log = "$root\batch\work\worker_$i.log"
    $err = "$root\batch\work\worker_${i}_err.log"
    $p = Start-Process -FilePath "pwsh" -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Worker $i -Workers $Workers -GameDir `"$root`"" -PassThru -NoNewWindow -RedirectStandardOutput $log -RedirectStandardError $err
    $processes += $p
    Write-Output "Started worker $i (PID: $($p.Id))"
}

Write-Output "Waiting for workers to complete..."
$lastCount = 0
while ($true) {
    Start-Sleep -Seconds 5
    $running = $processes | Where-Object { !$_.HasExited }
    $statusFiles = (Get-ChildItem "$root\batch\status\*.json" -ErrorAction SilentlyContinue).Count
    $delta = $statusFiles - $lastCount
    $lastCount = $statusFiles
    Write-Output "Workers alive: $($running.Count), Status files: $statusFiles (+$delta in 5s)"
    if ($running.Count -eq 0) {
        break
    }
}

Write-Output "All workers finished."
