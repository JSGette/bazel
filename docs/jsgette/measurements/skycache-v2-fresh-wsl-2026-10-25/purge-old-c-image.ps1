$ErrorActionPreference='Stop'
$taskReceipt=Get-Content -LiteralPath 'D:\Codex\wsl-reset-2026-10-25\old-C-evidence-verified.json' -Raw | ConvertFrom-Json
if(-not $taskReceipt.verified){throw 'Evidence backup is unverified'}
$taskImage=(Resolve-Path -LiteralPath 'C:\Users\jgett\Documents\Codex\2026-10-24\ex\work\skycache-v2-data.ext4').ProviderPath
$taskExpected='C:\Users\jgett\Documents\Codex\2026-10-24\ex\work\skycache-v2-data.ext4'
if($taskImage -cne $taskExpected){throw 'Unexpected image path'}
$taskItem=Get-Item -LiteralPath $taskImage
if($taskItem.Length -ne 68719476736 -or ($taskItem.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Unexpected image metadata'}
& wsl.exe -d Ubuntu -u root -- umount /mnt/skycache-v2-archive
if($LASTEXITCODE -ne 0){throw 'Image could not be unmounted'}
$taskBefore=[IO.DriveInfo]::new('C:\').AvailableFreeSpace
Remove-Item -LiteralPath $taskImage
if(Test-Path -LiteralPath $taskImage){throw 'Image remains'}
@{removed=$taskImage;bytes=68719476736;verified_evidence_backup='D:\Codex\wsl-reset-2026-10-25\old-C-benchmark-evidence.tar';free_bytes_before=$taskBefore;free_bytes_after=([IO.DriveInfo]::new('C:\').AvailableFreeSpace);finished_at=[DateTimeOffset]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath '.\work\old-c-purge-protocol.json' -Encoding utf8
