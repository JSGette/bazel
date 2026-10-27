$ErrorActionPreference = 'Stop'
$taskLog = Join-Path $PSScriptRoot 'all-host-memory.jsonl'
$taskRoot = '\\wsl.localhost\Ubuntu\home\jgett\REPOS\.work\skycache-all-targets-2026-10-27'
$taskDeadline = [DateTime]::UtcNow.AddHours(6)
while ([DateTime]::UtcNow -lt $taskDeadline) {
    $taskMemory = Get-CimInstance Win32_OperatingSystem
    $taskVm = Get-Process -Name vmmemWSL -ErrorAction SilentlyContinue
    $taskRecord = [ordered]@{
        epoch_ms = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        total_physical_kib = $taskMemory.TotalVisibleMemorySize
        free_physical_kib = $taskMemory.FreePhysicalMemory
        total_virtual_kib = $taskMemory.TotalVirtualMemorySize
        free_virtual_kib = $taskMemory.FreeVirtualMemory
        wsl_working_set_bytes = $taskVm.WorkingSet64
        wsl_private_bytes = $taskVm.PrivateMemorySize64
    }
    $taskRecord | ConvertTo-Json -Compress | Add-Content -LiteralPath $taskLog -Encoding UTF8
    $taskPipeline = Join-Path $taskRoot 'pipeline-status.json'
    if (Test-Path -LiteralPath $taskPipeline) {
        $taskState = Get-Content -Raw -LiteralPath $taskPipeline | ConvertFrom-Json
        if ($taskState.status -in @('passed', 'failed')) { break }
    }
    Start-Sleep -Seconds 30
}
