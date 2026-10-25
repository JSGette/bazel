$ErrorActionPreference='Stop'
$taskTarget=(Resolve-Path -LiteralPath 'E:\QWEN').ProviderPath
if($taskTarget -cne 'E:\QWEN'){throw 'Unexpected Qwen target'}
$taskInfo=Get-Item -LiteralPath $taskTarget -Force
if($taskInfo.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Refuse reparse-point target'}
$taskFiles=@(Get-ChildItem -LiteralPath $taskTarget -Force)
if(@($taskFiles | Where-Object { $_.PSIsContainer -or $_.Extension -ne '.gguf' -or ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) }).Count){throw 'Unexpected contents: inspect before purging'}
$taskProtocol=[ordered]@{user_request='Purge WSL and Qwen files then, start from scratch with datadog agent in wsl';started_at=[DateTimeOffset]::UtcNow.ToString('o');target=$taskTarget;files=@($taskFiles | Select-Object Name,Length);free_bytes_before=([IO.DriveInfo]::new('E:\').AvailableFreeSpace)}
$taskProtocol | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath '.\work\qwen-purge-protocol.json' -Encoding utf8
Remove-Item -LiteralPath $taskTarget -Recurse -Force
if(Test-Path -LiteralPath $taskTarget){throw 'Qwen directory still exists'}
$taskProtocol.finished_at=[DateTimeOffset]::UtcNow.ToString('o')
$taskProtocol.free_bytes_after=[IO.DriveInfo]::new('E:\').AvailableFreeSpace
$taskProtocol.verified_removed=$true
$taskProtocol | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath '.\work\qwen-purge-protocol.json' -Encoding utf8
[pscustomobject]@{removed=$taskTarget;reclaimed_gib=($taskProtocol.free_bytes_after-$taskProtocol.free_bytes_before)/1GB;free_gib=$taskProtocol.free_bytes_after/1GB}
