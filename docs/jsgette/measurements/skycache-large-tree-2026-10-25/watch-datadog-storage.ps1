$ErrorActionPreference = 'Stop'
$taskSnapshot = 'C:\Users\jgett\Documents\Codex\2026-10-24\ex\work\datadog-storage-live.json'
$taskStop = 'C:\Users\jgett\Documents\Codex\2026-10-24\ex\work\datadog-storage.stop'
Remove-Item -LiteralPath $taskStop -ErrorAction SilentlyContinue
while (-not (Test-Path -LiteralPath $taskStop)) {
    $taskDrives = @([IO.DriveInfo]::GetDrives() | Where-Object { $_.DriveType -eq [IO.DriveType]::Fixed -and $_.IsReady } | ForEach-Object { @{root=$_.Name; free_bytes=$_.AvailableFreeSpace} })
    $taskState = @{
        schema_version=1
        observed_unix=([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0)
        system_drive='C:\'
        system_free_bytes=([IO.DriveInfo]::new('C:\').AvailableFreeSpace)
        distros=@(@{name='Ubuntu';path='E:\WSL\Ubuntu';free_bytes=([IO.DriveInfo]::new('E:\').AvailableFreeSpace)})
        drives=$taskDrives
    }
    [IO.File]::WriteAllText($taskSnapshot+'.tmp', ($taskState | ConvertTo-Json -Depth 5 -Compress), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath ($taskSnapshot+'.tmp') -Destination $taskSnapshot -Force
    Start-Sleep -Seconds 3
}
