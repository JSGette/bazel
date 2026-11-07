#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateRange(256, 1048576)][int]$MemoryMaxMiB,
    [Parameter(Mandatory)][ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9-]{0,15}$')][string]$BatchId,
    [string]$Manifest = (Join-Path $PSScriptRoot 'matrix-v6.json'),
    [string]$EvidenceDirectory = (Join-Path $PSScriptRoot '../evidence'),
    [string]$Distribution = 'Ubuntu',
    [switch]$DryRun
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Write-Json($Path, $Value) { [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 30) + "`n") }
function Hash($Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Wait-HostBudgetHeadroom([string]$Path, [int]$MemoryMaxMiB, [int]$TimeoutSeconds = 120) {
    if (Test-Path -LiteralPath $Path) { throw 'Host-budget preflight evidence already exists.' }
    $report = [ordered]@{schema_version=1; status='waiting'; policy=[ordered]@{
        memory_max_mib=$MemoryMaxMiB; minimum_physical_bytes=([long]$MemoryMaxMiB + 3072 + 512) * 1MB
        minimum_commit_bytes=([long]$MemoryMaxMiB + 4096 + 512) * 1MB
        physical_reserve_mib=3072; commit_reserve_mib=4096; margin_bytes=512MB
        sample_interval_ms=2000; timeout_seconds=$TimeoutSeconds; consecutive_samples_required=2
    }; samples=@(); consecutive_ready_samples=0}
    $watch = [Diagnostics.Stopwatch]::StartNew()
    try {
        Write-Json $Path $report
        while ($true) {
            $memory = Get-GuardHostMemory
            $report.samples += $memory
            if ($memory.available_physical_bytes -ge $report.policy.minimum_physical_bytes -and
                $memory.available_commit_bytes -ge $report.policy.minimum_commit_bytes) {
                $report.consecutive_ready_samples++
            } else { $report.consecutive_ready_samples = 0 }
            $report.elapsed_seconds = $watch.Elapsed.TotalSeconds
            if ($report.consecutive_ready_samples -ge 2) { $report.status = 'ready'; break }
            $remaining = $TimeoutSeconds * 1000 - $watch.Elapsed.TotalMilliseconds
            if ($remaining -le 0) { $report.status = 'timeout'; break }
            Write-Json $Path $report
            Start-Sleep -Milliseconds ([int][Math]::Min(2000, [Math]::Ceiling($remaining)))
        }
    } catch {
        $report.status = 'error'; $report.error = $_.Exception.Message
    } finally {
        $report.elapsed_seconds = $watch.Elapsed.TotalSeconds
        Write-Json $Path $report
    }
    return $report
}
function Linux-File([string]$Path) { "\\wsl.localhost\$Distribution" + $Path.Replace('/', '\') }
function Invoke-Checked([string]$Exe, [string[]]$Argv, [switch]$AllowFailure) {
    $start = [Diagnostics.ProcessStartInfo]::new($Exe)
    $start.UseShellExecute = $false; $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true; $start.RedirectStandardError = $true
    foreach ($arg in $Argv) { $start.ArgumentList.Add($arg) }
    $process = [Diagnostics.Process]::Start($start)
    try {
        $stdout = $process.StandardOutput.ReadToEndAsync(); $stderr = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $answer = @{ exit_code=$process.ExitCode; stdout=$stdout.GetAwaiter().GetResult(); stderr=$stderr.GetAwaiter().GetResult() }
        if ($answer.exit_code -ne 0 -and -not $AllowFailure) { throw "Control command failed ($($answer.exit_code)): $($answer.stderr)" }
        return $answer
    } finally { $process.Dispose() }
}
function To-Linux([string]$Path) { (Invoke-Checked $wsl @('-d',$Distribution,'--exec','wslpath','-u',[IO.Path]::GetFullPath($Path))).stdout.Trim() }
function Archive-Guard([string]$Directory, [string]$Destination) {
    $files = @(Get-ChildItem -LiteralPath $Directory -File)
    Compress-Archive -LiteralPath $files.FullName -DestinationPath $Destination
    $members = @($files | ForEach-Object { @{path=$_.Name; bytes=$_.Length; sha256=(Hash $_.FullName)} })
    $zip = [IO.Compression.ZipFile]::OpenRead($Destination)
    try {
        if ($zip.Entries.Count -ne $members.Count) { throw 'Guard archive member count differs.' }
        foreach ($member in $members) {
            $entry = $zip.GetEntry($member.path); $stream = $entry.Open()
            try { $actual = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($stream)).ToLowerInvariant() }
            finally { $stream.Dispose() }
            if ($actual -ne $member.sha256 -or $entry.Length -ne $member.bytes) { throw 'Guard archive verification failed.' }
        }
    } finally { $zip.Dispose() }
    Write-Json "$Destination.json" @{schema_version=1; kind='host_guard'; archive_sha256=(Hash $Destination); members=$members; milestone_verdict='not_assessed'}
}
$verifyArchive = @'
import hashlib,json,sys,tarfile
from pathlib import Path
p=Path(sys.argv[1]); m=json.loads(Path(str(p)+'.json').read_text())
assert hashlib.file_digest(p.open('rb'),'sha256').hexdigest()==m['archive_sha256']
with tarfile.open(p) as t:
    assert sorted(t.getnames())==sorted(x['path'] for x in m['members'])
    for x in m['members']:
        f=t.extractfile(x['path']); data=f.read()
        assert len(data)==x['bytes'] and hashlib.sha256(data).hexdigest()==x['sha256']
print('verified')
'@
$config = Get-Content -LiteralPath $Manifest -Raw | ConvertFrom-Json -AsHashtable
$batch = Join-Path ([IO.Path]::GetFullPath($EvidenceDirectory)) $BatchId
if (Test-Path -LiteralPath $batch) { throw 'Batch evidence already exists; never resume or retry implicitly.' }
[void][IO.Directory]::CreateDirectory($batch)
Copy-Item -LiteralPath $Manifest -Destination (Join-Path $batch 'matrix.json')
$progress = @{schema_version=1; batch=$BatchId; manifest_sha256=(Hash $Manifest); runner_sha256=(Hash $PSCommandPath); memory_max_mib=$MemoryMaxMiB; cpu_quota_percent=600; dry_run=[bool]$DryRun; status='validating'; milestone_verdict='not_assessed'; rows=@()}
$progressPath = Join-Path $batch 'matrix-progress.json'
$reports = @{}; $current = $null
try {
    if ($config.schema_version -ne 1) { throw 'Unsupported manifest schema.' }
    $requireBudgetHeadroom = if ($config.ContainsKey('require_budget_headroom')) { $config.require_budget_headroom } else { $false }
    if ($requireBudgetHeadroom -isnot [bool]) { throw 'require_budget_headroom must be a JSON boolean.' }
    if ($requireBudgetHeadroom -and -not $config.local_sha256.ContainsKey('host_memory_guard.psm1')) { throw 'Budget preflight requires a pinned host memory guard module.' }
    $progress.require_budget_headroom = $requireBudgetHeadroom
    $poolNamespace = if ($config.ContainsKey('pool_namespace')) { $config.pool_namespace } else { 'v6' }
    if ($poolNamespace -isnot [string] -or $poolNamespace -notmatch '^[a-zA-Z0-9][a-zA-Z0-9-]{0,30}$') { throw 'Invalid pool namespace.' }
    $temporaryRoot = if ($config.ContainsKey('temporary_root')) { $config.temporary_root } else { $null }
    if ($null -ne $temporaryRoot -and ($temporaryRoot -isnot [string] -or -not $temporaryRoot.StartsWith($config.runtime_root.TrimEnd('/') + '/') -or $temporaryRoot -match '/\.\.?(/|$)')) { throw 'Temporary root must be an absolute descendant of the task runtime.' }
    $progress.pool_namespace = $poolNamespace; $progress.temporary_root = $temporaryRoot
    foreach ($name in $config.local_sha256.Keys) {
        if ((Hash (Join-Path $PSScriptRoot $name)) -ne $config.local_sha256[$name]) { throw "Local tool hash mismatch: $name" }
    }
    $seen = @{}
    foreach ($row in $config.rows) {
        if ($row.id -notmatch '^[a-zA-Z0-9][a-zA-Z0-9-]{0,30}$' -or $seen.ContainsKey($row.id)) { throw 'Invalid or duplicate matrix row ID.' }
        if (-not $config.fixtures.ContainsKey($row.fixture) -or $row.mode -notin @('vanilla','candidate-off','skycache') -or $row.state -notin @('restart','warm','cold')) { throw "Invalid matrix row: $($row.id)" }
        if ($row.reference -and ($row.mode -ne 'vanilla' -or $row.oracle)) { throw 'References must be independent vanilla runs.' }
        if (-not $row.reference -and (-not $row.oracle -or -not $seen.ContainsKey($row.oracle) -or -not $seen[$row.oracle].reference)) { throw 'Each measured row requires a preceding explicit reference.' }
        if ($row.ContainsKey('external_report') -and (-not $row.reference -or -not $row.ContainsKey('external_guard_directory'))) { throw 'Imported correctness references require an explicit guard directory.' }
        $seen[$row.id] = $row
    }
    if (-not $DryRun) {
        $wsl = (Get-Command wsl.exe -CommandType Application | Select-Object -First 1).Source
        foreach ($name in $config.harness_sha256.Keys) {
            if ((Hash (Linux-File "$($config.harness_directory)/$name")) -ne $config.harness_sha256[$name]) { throw "Frozen harness hash mismatch: $name" }
        }
        foreach ($binary in $config.binary.Values) { if ((Hash (Linux-File $binary.path)) -ne $binary.sha256) { throw 'Pinned binary hash mismatch.' } }
        $linuxTools = To-Linux $PSScriptRoot; $linuxBatch = To-Linux $batch
    }
    foreach ($row in $config.rows) {
        $fixture = $config.fixtures[$row.fixture]; $root = "$($config.runtime_root)/$($fixture.directory)"
        $runId = "$BatchId-$($row.id)"; $run = "$root/runs/$runId"
        $external = $row.ContainsKey('external_report')
        $reportPath = if ($external) { $row.external_report } else { "$run/report.json" }
        $recordedRunId = if ($external) { ($reportPath -split '/')[-2] } else { $runId }
        $current = @{id=$row.id; run_id=$recordedRunId; report=$reportPath; reference=[bool]$row.reference; external_reference=$external; status='planned'}
        $progress.rows += $current; Write-Json $progressPath $progress
        $binary = $config.binary[$(if ($row.mode -eq 'vanilla') {'vanilla'} else {'candidate'})]
        $argv = @('python3','-B',"$($config.harness_directory)/baseline.py",'--root',$root,'--clients',[string]$fixture.clients,'--scenario',$fixture.scenario,'--mode',$row.mode,'--binary',$binary.path,'--run-id',$runId,'--cache-state',$row.state,'--require-cgroup','--install-root',"$($config.runtime_root)/install")
        if ($null -ne $temporaryRoot) { $argv += @('--temporary-root',$temporaryRoot) }
        if ($row.state -eq 'cold') { $argv += '--fresh-caches' }
        else { $argv += @('--repository-cache',$config.repository_cache,'--disk-cache',"$($config.runtime_root)/actions",'--skycache-root',"$($config.runtime_root)/skycache-matrix/$($fixture.pool)-$($row.mode)-$poolNamespace",'--output-base-root',"$($config.runtime_root)/pools/$($fixture.pool)-$($row.mode)-$poolNamespace") }
        if ($row.state -eq 'restart') { $argv += '--serial-restart-preparation' }
        if ($row.oracle) { $argv += @('--oracle-report',$reports[$row.oracle]) }
        $reports[$row.id] = $reportPath
        $command = Join-Path $batch "$($row.id)-command.json"
        if (-not $external) { Write-Json $command $argv }
        if ($DryRun) { continue }
        $current.status = 'running'; Write-Json $progressPath $progress
        if (-not $external) {
            # The helper and report stay on C:, outside the mount it resets. Prior row evidence is already archived.
            $resetFile = "$($row.id)-task-reset.json"
            $current.task_reset = $resetFile; Write-Json $progressPath $progress
            $null = Invoke-Checked $wsl @('-d',$Distribution,'-u','root','--exec','python3','-B',"$linuxTools/reset_task_mount.py",'--apply','--report',"$linuxBatch/$resetFile")
            $reset = Get-Content -LiteralPath (Join-Path $batch $resetFile) -Raw | ConvertFrom-Json
            if ($reset.status -ne 'completed') { throw 'Task-only cache reset did not complete; no cohort launched.' }
            if ($requireBudgetHeadroom) {
                Import-Module (Join-Path $PSScriptRoot 'host_memory_guard.psm1') -Force
                $preflightPath = Join-Path $batch "$($row.id)-host-budget-preflight.json"
                $current.host_budget_preflight = [IO.Path]::GetFileName($preflightPath)
                Write-Json $progressPath $progress
                try { $preflight = Wait-HostBudgetHeadroom $preflightPath $MemoryMaxMiB }
                finally {
                    if (Test-Path -LiteralPath $preflightPath) { $current.host_budget_preflight_sha256 = Hash $preflightPath }
                    Write-Json $progressPath $progress
                }
                if ($preflight.status -ne 'ready') { throw "Host-budget preflight $($preflight.status); no cohort launched." }
            }
            $guard = Join-Path $batch "$($row.id)-guard"
            $result = Invoke-Checked (Join-Path $PSHOME 'pwsh.exe') @('-NoProfile','-File',(Join-Path $PSScriptRoot 'run_guarded.ps1'),'-CommandJson',$command,'-EvidenceDirectory',$guard,'-RunId',$runId,'-MemoryMaxMiB',[string]$MemoryMaxMiB,'-CpuQuotaPercent','600','-Distribution',$Distribution) -AllowFailure
            Write-Json (Join-Path $batch "$($row.id)-launcher.json") $result
        } else { $guard = $row.external_guard_directory }
        if (Test-Path -LiteralPath $guard) { Archive-Guard $guard (Join-Path $batch "$($row.id)-guard.zip") }
        $guardResult = Get-Content -LiteralPath (Join-Path $guard 'result.json') -Raw | ConvertFrom-Json
        $guardPolicy = Get-Content -LiteralPath (Join-Path $guard 'policy.json') -Raw | ConvertFrom-Json
        if ((-not $external -and $result.exit_code -ne 0) -or $guardResult.status -ne 'completed' -or $guardResult.exit_code -ne 0) { throw "Guard or command failed for $($row.id); preserve evidence and assess before continuing." }
        if ($guardPolicy.memory_max_mib -ne $MemoryMaxMiB -or $guardPolicy.cpu_quota_percent -ne 600) {
            throw 'Guard resource limits differ from this matrix; use an explicitly matched reference.'
        }
        $report = Get-Content -LiteralPath (Linux-File $reportPath) -Raw | ConvertFrom-Json -AsHashtable
        $current.report_sha256 = Hash (Linux-File $reportPath)
        if ($report.passed -ne $true) { throw "Report failed: $($row.id)" }
        $sourceRun = $reportPath.Substring(0, $reportPath.LastIndexOf('/'))
        $resources = Get-Content -LiteralPath (Linux-File "$sourceRun/measured-resources.json") -Raw | ConvertFrom-Json
        if (@($resources.samples | Where-Object { $_.engine_pids_sampled -eq $fixture.clients -and $_.engine_pss_bytes -gt 0 }).Count -eq 0) {
            throw 'No positive PSS sample covers every expected engine; memory evidence is incomplete.'
        }
        if ($report.mode -ne $row.mode -or $report.binary_sha256 -ne $binary.sha256 -or $report.cache_state -ne $row.state -or $report.fixture.clients -ne $fixture.clients -or $report.fixture.scenario -ne $fixture.scenario) { throw 'Report identity differs from the declared row.' }
        foreach ($name in $config.harness_sha256.Keys) { if ($report.harness_sha256[$name] -ne $config.harness_sha256[$name]) { throw 'Report uses a different harness.' } }
        $summaryBase = "$linuxBatch/$($row.id)-summary"
        $null = Invoke-Checked $wsl @('-d',$Distribution,'--exec','python3','-B',"$linuxTools/summarize.py",'--runs',$reportPath,'--skip-profiles','--output-json',"$summaryBase.json",'--output-md',"$summaryBase.md")
        $summary = Get-Content -LiteralPath (Join-Path $batch "$($row.id)-summary.json") -Raw | ConvertFrom-Json
        $reasons = @($summary.runs[0].ineligibility_reasons)
        if ($row.reference) { $reasons = @($reasons | Where-Object { $_ -notmatch '^digest_oracle_missing_or_failed:[0-9]+$' }) }
        $current.performance_eligible = $summary.runs[0].performance_eligible
        $current.ineligibility_reasons = @($summary.runs[0].ineligibility_reasons)
        if ($reasons.Count -gt 0) { throw "Evidence checks failed: $($reasons -join ', ')" }
        $archive = "$linuxBatch/$($row.id)-run.tar.gz"
        $sourceRun = $reportPath.Substring(0, $reportPath.LastIndexOf('/'))
        $null = Invoke-Checked $wsl @('-d',$Distribution,'--exec','python3','-B',"$linuxTools/archive_run.py",$sourceRun,$archive)
        $null = Invoke-Checked $wsl @('-d',$Distribution,'--exec','python3','-c',$verifyArchive,$archive)
        $current.archive = "$($row.id)-run.tar.gz"
        $current.archive_sha256 = Hash (Join-Path $batch $current.archive)
        $current.status = 'checked_and_archived'; Write-Json $progressPath $progress
    }
    $progress.status = if ($DryRun) {'planned_only'} else {'completed_no_gate_verdict'}
} catch {
    if ($null -ne $current) { $current.status = 'stopped'; $current.error = $_.Exception.Message }
    $progress.status = 'stopped_for_assessment'; $progress.error = $_.Exception.Message
    throw
} finally { Write-Json $progressPath $progress }
Write-Output ("Matrix {0}: {1}; evidence {2}" -f $BatchId,$progress.status,$batch)
