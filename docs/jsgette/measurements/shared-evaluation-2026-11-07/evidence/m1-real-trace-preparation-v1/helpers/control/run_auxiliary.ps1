#Requires -Version 7.0
# One explicitly requested post-matrix diagnostic/build/cohort. No gate decisions or retries.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$CommandJson,
    [Parameter(Mandatory)][ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9-]{0,47}$')][string]$RunId,
    [ValidateRange(1,14400)][int]$TimeoutSeconds = 3600,
    [switch]$DryRun
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Save-Json($Path, $Value) {
    [IO.File]::WriteAllText($Path,($Value | ConvertTo-Json -Depth 20) + "`n")
}
function Sha($Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Byte-Hash([byte[]]$Bytes) { [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($Bytes)).ToLowerInvariant() }
function Verified-Bytes($Path, $Expected) {
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ((Byte-Hash $bytes) -ne $Expected) { throw "Control helper identity changed: $Path" }
    return ,$bytes
}
$evidence = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../evidence'))
$destination = Join-Path $evidence "auxiliary/$RunId"
$commandPath = (Resolve-Path -LiteralPath $CommandJson).Path
$commandBytes = [IO.File]::ReadAllBytes($commandPath)
$commandHash = Byte-Hash $commandBytes
$literal = [Text.Encoding]::UTF8.GetString($commandBytes) | ConvertFrom-Json -NoEnumerate
if ($literal -isnot [array] -or $literal.Count -eq 0 -or
    @($literal | Where-Object { $_ -isnot [string] }).Count -ne 0) {
    throw 'CommandJson must be a nonempty array of literal argv strings.'
}
if (Test-Path -LiteralPath $destination) { throw 'Auxiliary evidence destination already exists.' }
$progressPath = Join-Path $evidence 'm0-v7b-01/matrix-progress.json'
$progress = Get-Content -Raw -LiteralPath $progressPath | ConvertFrom-Json
$matrixComplete = $progress.status -eq 'completed_no_gate_verdict' -and
    $progress.rows.Count -eq 29 -and
    @($progress.rows | Where-Object status -ne 'checked_and_archived').Count -eq 0
$reset = Join-Path $PSScriptRoot 'reset_task_mount.py'
$guard = Join-Path $PSScriptRoot 'run_guarded.ps1'
$guardModule = Join-Path $PSScriptRoot 'host_memory_guard.psm1'
$helperBytes = @{
    'reset_task_mount.py' = (Verified-Bytes $reset 'da301d14b2e9916b03e106be86edd233488dbce1a935a160ce91e4451158382f')
    'run_guarded.ps1' = (Verified-Bytes $guard '392087d82b47dbb1fdf40e6814ede576cd371a861e828222c123fa621d2fe313')
    'host_memory_guard.psm1' = (Verified-Bytes $guardModule '1a90a0cf8bdc0b9390475733b8915447eb6e774e3de88428bc45aaed105a6b3c')
}
if ($DryRun) {
    @{status='planned_only'; matrix_complete=$matrixComplete; destination=$destination;
      command_sha256=$commandHash; memory_max_mib=10240; cpu_quota_percent=600;
      timeout_seconds=$TimeoutSeconds} | ConvertTo-Json
    exit 0
}
if (-not $matrixComplete) { throw 'The frozen M0 matrix is still incomplete; refusing a competing workload.' }
[IO.Directory]::CreateDirectory((Join-Path $evidence 'auxiliary')) | Out-Null
$lease = [IO.File]::Open((Join-Path $evidence 'auxiliary/cohort.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
try {
    # The file lock alone cannot survive wrapper termination while its child guard runs.
    # An unresolved durable owner blocks reset/admission until separately assessed.
    $ownerPath = Join-Path $evidence 'auxiliary/active-owner.json'
    if (Test-Path -LiteralPath $ownerPath) {
        $prior = Get-Content -Raw -LiteralPath $ownerPath | ConvertFrom-Json
        if ($prior.status -ne 'completed_no_gate_verdict') {
            throw 'A prior auxiliary owner is unresolved. Verify its guard/scope cleanup and record assessment before retiring its owner record.'
        }
        $priorGuard = Get-Content -Raw -LiteralPath (Join-Path $prior.evidence 'guard/result.json') | ConvertFrom-Json
        if ($priorGuard.status -ne 'completed' -or $priorGuard.exit_code -ne 0 -or
            $priorGuard.final_scope_cleanup.verified -ne $true) {
            throw 'Prior owner lacks verified successful scope cleanup.'
        }
    }
    if (Test-Path -LiteralPath $destination) { throw 'Auxiliary evidence destination appeared while waiting for the lease.' }
    [IO.Directory]::CreateDirectory($destination) | Out-Null
    [IO.File]::WriteAllBytes((Join-Path $destination 'command.json'),$commandBytes)
    $controlTools = Join-Path $destination 'control-tools'
    [IO.Directory]::CreateDirectory($controlTools) | Out-Null
    foreach ($name in $helperBytes.Keys) { [IO.File]::WriteAllBytes((Join-Path $controlTools $name),$helperBytes[$name]) }
    $guard = Join-Path $controlTools 'run_guarded.ps1'
    $guardModule = Join-Path $controlTools 'host_memory_guard.psm1'
    $record = [ordered]@{schema_version=1; status='starting'; run_id=$RunId;
        command_sha256=$commandHash; wrapper_sha256=(Sha $PSCommandPath);
        matrix_progress_sha256=(Sha $progressPath); memory_max_mib=10240;
        cpu_quota_percent=600; timeout_seconds=$TimeoutSeconds; milestone_verdict='not_assessed';
        helper_sha256=@{}; evidence=$destination; windows_wrapper_pid=$PID}
    foreach ($name in $helperBytes.Keys) { $record.helper_sha256[$name] = Byte-Hash $helperBytes[$name] }
    Save-Json (Join-Path $destination 'control.json') $record
    Save-Json $ownerPath $record
    try {
        # The helper verifies the exact task image, UUID, mount and absence of task workloads.
        $linuxTools = (& wsl.exe -d Ubuntu --exec wslpath -u $controlTools).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Control path conversion failed.' }
        $linuxDestination = (& wsl.exe -d Ubuntu --exec wslpath -u $destination).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Evidence path conversion failed.' }
        & wsl.exe -d Ubuntu -u root --exec python3 -B "$linuxTools/reset_task_mount.py" --apply --report "$linuxDestination/task-reset.json"
        if ($LASTEXITCODE -ne 0) { throw 'Task cache reset failed.' }
        Import-Module $guardModule -Force
        $preflight = [ordered]@{schema_version=1; status='waiting'; policy=[ordered]@{
            memory_max_mib=10240; minimum_physical_bytes=14495514624L;
            minimum_commit_bytes=15569256448L; physical_reserve_mib=3072;
            commit_reserve_mib=4096; margin_bytes=536870912L;
            sample_interval_ms=2000; timeout_seconds=120; consecutive_samples_required=2
        }; samples=@(); consecutive_ready_samples=0}
        $timer = [Diagnostics.Stopwatch]::StartNew()
        try {
            while ($true) {
                $memory = Get-GuardHostMemory
                $preflight.samples += $memory
                if ($memory.available_physical_bytes -ge $preflight.policy.minimum_physical_bytes -and
                    $memory.available_commit_bytes -ge $preflight.policy.minimum_commit_bytes) {
                    $preflight.consecutive_ready_samples++
                } else { $preflight.consecutive_ready_samples=0 }
                $preflight.elapsed_seconds = $timer.Elapsed.TotalSeconds
                if ($preflight.consecutive_ready_samples -ge 2) { $preflight.status='ready'; break }
                $remaining = 120000 - $timer.Elapsed.TotalMilliseconds
                if ($remaining -le 0) { $preflight.status='timeout'; break }
                Save-Json (Join-Path $destination 'host-budget-preflight.json') $preflight
                Start-Sleep -Milliseconds ([int][Math]::Min(2000,[Math]::Ceiling($remaining)))
            }
        } catch { $preflight.status='error'; $preflight.error=$_.Exception.Message; throw }
        finally {
            $preflight.elapsed_seconds = $timer.Elapsed.TotalSeconds
            Save-Json (Join-Path $destination 'host-budget-preflight.json') $preflight
        }
        if ($preflight.status -ne 'ready') { throw 'Host budget preflight timed out; workload not launched.' }
        & (Join-Path $PSHOME 'pwsh.exe') -NoProfile -File $guard -CommandJson (Join-Path $destination 'command.json') -EvidenceDirectory (Join-Path $destination 'guard') -RunId $RunId -MemoryMaxMiB 10240 -CpuQuotaPercent 600 -TimeoutSeconds $TimeoutSeconds *> (Join-Path $destination 'launcher.log')
        $record.launcher_exit_code = $LASTEXITCODE
        $result = Get-Content -Raw -LiteralPath (Join-Path $destination 'guard/result.json') | ConvertFrom-Json
        $policy = Get-Content -Raw -LiteralPath (Join-Path $destination 'guard/policy.json') | ConvertFrom-Json
        if ($policy.command_sha256 -ne $commandHash) { throw 'Guard executed a different command snapshot.' }
        if ($record.launcher_exit_code -ne 0 -or $result.status -ne 'completed' -or $result.exit_code -ne 0) {
            throw 'Guard/workload failed; preserve evidence and assess before another attempt.'
        }
        if ($result.final_scope_cleanup.verified -ne $true) { throw 'Final scope cleanup is not verified.' }
        $record.status='completed_no_gate_verdict'
    } catch { $record.status='stopped_for_assessment'; $record.error=$_.Exception.Message; throw }
    finally {
        Save-Json (Join-Path $destination 'control.json') $record
        Save-Json $ownerPath $record
    }
} finally { $lease.Dispose() }
Write-Output "Auxiliary $RunId completed; evidence $destination"
