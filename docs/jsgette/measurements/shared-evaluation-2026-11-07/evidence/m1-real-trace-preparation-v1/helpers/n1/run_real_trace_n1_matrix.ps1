#Requires -Version 7.0
# Fixed serial diagnostic sessions. No retry, resume, or milestone verdict.
[CmdletBinding()]
param([Parameter(Mandatory)][string]$OrderJson, [switch]$DryRun)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Sha($Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Linux-Path([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith('C:\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Only C-backed control paths are allowed.' }
    return '/mnt/c/' + $full.Substring(3).Replace('\','/')
}
function Save($Path, $Value) { [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 30) + "`n") }
function Invoke-Logged([string]$Executable, [string[]]$Arguments, [string]$Log) {
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $Executable
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($argument in $Arguments) { $start.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw 'Could not start the declared helper.' }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        [IO.File]::WriteAllText($Log, $stdout.GetAwaiter().GetResult() + $stderr.GetAwaiter().GetResult())
        return $process.ExitCode
    } finally { $process.Dispose() }
}
$work = [IO.Path]::GetFullPath($PSScriptRoot)
$orderPath = (Resolve-Path -LiteralPath $OrderJson).Path
if (-not $orderPath.StartsWith($work + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Order must be a C-backed task work child.' }
$order = Get-Content -Raw -LiteralPath $orderPath | ConvertFrom-Json
$parent = Split-Path -Parent $orderPath
$progressPath = Join-Path $parent 'execution-progress.json'
if (Test-Path -LiteralPath $progressPath) { throw 'Existing execution progress forbids an automatic retry/resume.' }
$archive = Join-Path $work 'archive_real_trace_n1.py'
$auxiliary = Join-Path $work 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/harness/run_auxiliary.ps1'
$accounting = Join-Path $work 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/harness/analyze_native_n2.py'
$evidenceRoot = Join-Path $work 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/evidence/auxiliary'
if ($order.schema_version -ne 1 -or $order.diagnostic_only -ne $true -or $null -ne $order.milestone_verdict -or
    $order.runner_timeout_seconds -ne 2400 -or $order.role -notin @('reference','matrix') -or
    @($order.rows).Count -ne $(if ($order.role -eq 'reference') { 1 } else { 20 })) { throw 'Fixed N1 order contract differs.' }
foreach ($tool in @($order.control_tools)) {
    if ((Sha $tool.path) -ne $tool.sha256) { throw "Pinned control helper changed: $($tool.path)" }
}
$expectedTools = @($archive, $auxiliary, $PSCommandPath, $accounting) | Sort-Object
if ((@($order.control_tools.path | Sort-Object) -join "`n") -ne ($expectedTools -join "`n")) { throw 'Complete control helper pins required.' }
$ids = [Collections.Generic.HashSet[string]]::new()
$expectedIds = @('m1-trace-n1-reference-01')
if ($order.role -eq 'matrix') {
    $expectedIds = @(
        foreach ($pair in 1..5) {
            foreach ($state in @('restart','warm')) {
                $modes = if (($pair % 2 -eq 1) -eq ($state -eq 'restart')) { @('off','on') } else { @('on','off') }
                foreach ($mode in $modes) { 'm1-trace-n1-p{0:d2}-{1}-{2}' -f $pair,$state,$mode }
            }
        }
    )
}
if ((@($order.rows.run_id) -join "`n") -ne ($expectedIds -join "`n")) { throw 'Fixed alternating five-pair order differs.' }
foreach ($row in @($order.rows)) {
    if ([IO.Path]::GetFullPath($row.manifest) -ne (Join-Path $parent ($row.run_id + '.json')) -or
        [IO.Path]::GetFullPath($row.command) -ne (Join-Path $parent ($row.run_id + '-command.json'))) { throw 'Row files must use their emitted fixed paths.' }
    if (-not $ids.Add($row.run_id) -or (Sha $row.manifest) -ne $row.manifest_sha256 -or
        (Sha $row.command) -ne $row.command_sha256) { throw 'Duplicate or changed row input.' }
    $manifest = Get-Content -Raw -LiteralPath $row.manifest | ConvertFrom-Json
    if ($manifest.run_id -ne $row.run_id -or $manifest.pair -ne $row.pair -or $manifest.state -ne $row.state -or
        $manifest.trace_enabled -ne $row.trace_enabled) { throw 'Manifest/order row differs.' }
    $literal = Get-Content -Raw -LiteralPath $row.command | ConvertFrom-Json -NoEnumerate
    $expectedCommand = @('python3','-B',(Linux-Path (Join-Path $work 'run_real_trace_n1.py')),
        '--manifest',(Linux-Path $row.manifest),'--manifest-sha256',$row.manifest_sha256)
    if (($literal -join "`n") -ne ($expectedCommand -join "`n")) { throw 'Literal launched command differs.' }
    if (Test-Path -LiteralPath (Join-Path $evidenceRoot $row.run_id)) { throw 'A declared session already has evidence; no reuse.' }
    if (Test-Path -LiteralPath (Join-Path $evidenceRoot ($row.run_id + '-wire'))) { throw 'A declared replay already has evidence; no reuse.' }
}
if ($DryRun) {
    @{status='planned_only'; role=$order.role; sessions=@($order.rows).Count;
      traced_sessions=@($order.rows | Where-Object trace_enabled -eq $true).Count;
      separate_postprocessing_guard=$true; order_sha256=(Sha $orderPath); launched=$false} | ConvertTo-Json
    exit 0
}
$python = @(Get-Command python.exe -CommandType Application -ErrorAction Stop)[0].Source
$wsl = @(Get-Command wsl.exe -CommandType Application -ErrorAction Stop)[0].Source
$pwsh = Join-Path $PSHOME 'pwsh.exe'
$progress = [ordered]@{schema_version=1; status='running'; role=$order.role; order_sha256=(Sha $orderPath);
    driver_sha256=(Sha $PSCommandPath); milestone_verdict=$null; rows=@()}
Save $progressPath $progress
try {
    foreach ($row in @($order.rows)) {
        foreach ($tool in @($order.control_tools)) {
            if ((Sha $tool.path) -ne $tool.sha256) { throw 'Control helper changed between sessions.' }
        }
        if ((Sha $row.manifest) -ne $row.manifest_sha256 -or (Sha $row.command) -ne $row.command_sha256) { throw 'Row input changed.' }
        $record = [ordered]@{run_id=$row.run_id; status='starting'; manifest_sha256=$row.manifest_sha256; command_sha256=$row.command_sha256}
        $progress.rows += $record
        Save $progressPath $progress
        $session = Join-Path $evidenceRoot $row.run_id
        $code = Invoke-Logged $pwsh @('-NoProfile','-File',$auxiliary,'-CommandJson',$row.command,
            '-RunId',$row.run_id,'-TimeoutSeconds','2400') (Join-Path $parent ($row.run_id + '-launcher.log'))
        $record.launch_exit_code = $code
        # Capture ended failures too. A failure never becomes eligible merely because capture succeeded.
        if (Test-Path -LiteralPath (Join-Path $session 'guard/result.json')) {
            $captureCode = Invoke-Logged $wsl @('-d','Ubuntu','--exec','python3','-B',(Linux-Path $archive),'capture',
                '--manifest',(Linux-Path $row.manifest),'--manifest-sha256',$row.manifest_sha256) (Join-Path $parent ($row.run_id + '-archive.log'))
            $record.capture_exit_code = $captureCode
            if ($captureCode -ne 0) { throw 'Archive failed; preserve partial evidence for assessment.' }
            $metadataPath = Join-Path $session 'runtime.tar.gz.json'
            $metadata = Get-Content -Raw -LiteralPath $metadataPath | ConvertFrom-Json
            $record.archive_metadata_sha256 = Sha $metadataPath
            if ($code -ne 0 -or $metadata.passed -ne $true) { throw 'Guard/session failed; evidence captured, assessment required.' }
        } else { throw 'Guard result missing; preserve auxiliary evidence and assess before continuing.' }
        if ($row.trace_enabled) {
            $wireCommandPath = Join-Path $parent ($row.run_id + '-wire-command.json')
            if (Test-Path -LiteralPath $wireCommandPath) { throw 'Wire command already exists.' }
            Save $wireCommandPath @('python3','-B',(Linux-Path $archive),'verify','--evidence',(Linux-Path $session))
            $wireId = $row.run_id + '-wire'
            $verifyCode = Invoke-Logged $pwsh @('-NoProfile','-File',$auxiliary,'-CommandJson',$wireCommandPath,
                '-RunId',$wireId,'-TimeoutSeconds','2400') (Join-Path $parent ($row.run_id + '-wire.log'))
            $wireGuardPath = Join-Path $evidenceRoot ($wireId + '/guard/result.json')
            if ($verifyCode -ne 0 -or -not (Test-Path -LiteralPath $wireGuardPath)) { throw 'Actual trace postprocessing failed.' }
            $wireGuard = Get-Content -Raw -LiteralPath $wireGuardPath | ConvertFrom-Json
            if ($wireGuard.status -ne 'completed' -or $wireGuard.exit_code -ne 0 -or
                $wireGuard.final_scope_cleanup.verified -ne $true -or $wireGuard.final_scope_cleanup.present -ne $false -or
                $wireGuard.final_scope_cleanup.signalled -ne $false) { throw 'Trace postprocessing scope cleanup differs.' }
            $record.wire_guard_sha256 = Sha $wireGuardPath
            $record.wire_command_sha256 = Sha $wireCommandPath
        } else {
            $verifyCode = Invoke-Logged $python @('-B',$archive,'verify','--evidence',$session) (Join-Path $parent ($row.run_id + '-verify.log'))
            if ($verifyCode -ne 0) { throw 'Archived off/reference evidence failed verification.' }
        }
        $verificationPath = Join-Path $session 'verification/result.json'
        $verification = Get-Content -Raw -LiteralPath $verificationPath | ConvertFrom-Json
        if ($verification.verified -ne $true -or $verification.performance_eligible -ne $false -or
            $null -ne $verification.milestone_verdict) { throw 'Diagnostic verification did not complete.' }
        $record.status = 'captured_and_verified_no_gate_verdict'
        $record.verification_sha256 = Sha $verificationPath
        Save $progressPath $progress
    }
    $progress.status = 'completed_diagnostic_no_gate_verdict'
} catch {
    $progress.status = 'stopped_for_assessment'
    $progress.error = $_.Exception.Message
    if ($progress.rows.Count -gt 0) { $progress.rows[-1].status = 'stopped_for_assessment' }
    throw
} finally { Save $progressPath $progress }
Write-Output "N1 diagnostic $($order.role) finished; no milestone verdict."
