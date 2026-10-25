$ErrorActionPreference='Stop'
$taskStarted=[DateTimeOffset]::UtcNow.ToString('o')
$taskTotals=@{}
$taskErrors=[System.Collections.Generic.List[string]]::new()
$taskQueue=[System.Collections.Generic.Stack[string]]::new()
$taskQueue.Push('E:\')
$taskFiles=0
while($taskQueue.Count -gt 0) {
  $taskDir=$taskQueue.Pop()
  try {
    foreach($taskEntry in [IO.DirectoryInfo]::new($taskDir).EnumerateFileSystemInfos()) {
      if($taskEntry.Attributes -band [IO.FileAttributes]::ReparsePoint) {continue}
      if($taskEntry.Attributes -band [IO.FileAttributes]::Directory) {
        if($taskEntry.Name -notin @('System Volume Information','$RECYCLE.BIN')) {$taskQueue.Push($taskEntry.FullName)}
      } else {
        $taskRelative=$taskEntry.FullName.Substring(3)
        $taskParts=$taskRelative.Split('\')
        $taskKeys=@(foreach($taskDepth in @(1,2,[Math]::Min(4,$taskParts.Length))) { ($taskParts | Select-Object -First $taskDepth) -join '\' })
        foreach($taskKey in @($taskKeys | Select-Object -Unique)) {
          if(-not $taskTotals.ContainsKey($taskKey)) {$taskTotals[$taskKey]=[long]0}
          $taskTotals[$taskKey]+=$taskEntry.Length
        }
        $taskFiles++
      }
    }
  } catch {$taskErrors.Add($taskDir+': '+$_.Exception.Message)}
}
$taskReport=@{started_at=$taskStarted;finished_at=[DateTimeOffset]::UtcNow.ToString('o');files=$taskFiles;errors=$taskErrors.ToArray();totals=@($taskTotals.GetEnumerator() | ForEach-Object { [pscustomobject]@{path=('E:\'+$_.Key);bytes=$_.Value;gib=[math]::Round($_.Value/1GB,2)} } | Sort-Object bytes -Descending);volume=@(Get-Volume -DriveLetter E | Select-Object Size,SizeRemaining)}
$taskReport | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath 'C:\Users\jgett\Documents\Codex\2026-10-24\ex\work\e-storage-analysis.json' -Encoding utf8
$taskReport.totals | Select-Object -First 30 | Format-Table -AutoSize
