$ErrorActionPreference='Stop'
$taskBackup='D:\Codex\wsl-reset-2026-10-25'
$taskReceipt=Get-Content -LiteralPath ($taskBackup+'\backup-verified.json') -Raw | ConvertFrom-Json
if(-not $taskReceipt.verified){throw 'Backup is not verified'}
foreach($taskArchive in $taskReceipt.archives.PSObject.Properties){
 $taskFile=Get-Item -LiteralPath (Join-Path $taskBackup $taskArchive.Name)
 if($taskFile.Length -ne $taskArchive.Value.bytes){throw 'Backup length changed'}
}
$taskDistro=@(Get-ChildItem -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' | Get-ItemProperty | Where-Object DistributionName -eq 'Ubuntu')
if($taskDistro.Count -ne 1){throw 'Expected exactly one Ubuntu distribution'}
$taskTarget=[IO.Path]::GetFullPath($taskDistro[0].BasePath)
if($taskTarget.TrimEnd('\') -cne 'E:\WSL\Ubuntu'){throw ('Unexpected Ubuntu root: '+$taskTarget)}
$taskProtocol=[ordered]@{started_at=[DateTimeOffset]::UtcNow.ToString('o');distribution='Ubuntu';verified_target=$taskTarget;backup_receipt=($taskBackup+'\backup-verified.json');free_bytes_before=([IO.DriveInfo]::new('E:\').AvailableFreeSpace)}
$taskProtocol | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath '.\work\wsl-reset-protocol.json' -Encoding utf8
& wsl.exe --terminate Ubuntu
if($LASTEXITCODE -ne 0){throw 'Could not stop Ubuntu'}
& wsl.exe --unregister Ubuntu
if($LASTEXITCODE -ne 0){throw 'Unregister failed'}
if(Test-Path -LiteralPath 'E:\WSL\Ubuntu\ext4.vhdx'){throw 'Old VHD still exists'}
if(Test-Path -LiteralPath $taskTarget){
 if(@(Get-ChildItem -LiteralPath $taskTarget -Force).Count -ne 0){throw 'Unexpected files remain in distro directory'}
 Remove-Item -LiteralPath $taskTarget
}
$taskProtocol.unregistered_at=[DateTimeOffset]::UtcNow.ToString('o')
$taskProtocol.free_bytes_after_purge=[IO.DriveInfo]::new('E:\').AvailableFreeSpace
$taskProtocol | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath '.\work\wsl-reset-protocol.json' -Encoding utf8
& wsl.exe --install Ubuntu-24.04 --name Ubuntu --location 'E:\WSL\Ubuntu' --no-launch --web-download
if($LASTEXITCODE -ne 0){throw 'Fresh Ubuntu installation failed; old environment is backed up on D:'}
$taskProtocol.installed_at=[DateTimeOffset]::UtcNow.ToString('o')
$taskProtocol.free_bytes_after_install=[IO.DriveInfo]::new('E:\').AvailableFreeSpace
$taskProtocol | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath '.\work\wsl-reset-protocol.json' -Encoding utf8
'Fresh Ubuntu installed on E:'
