#Requires -Version 7.0
# Offline fixtures only: synthetic matrix evidence, mocked wsl.exe, and inert helpers.
# The copied wrapper differs only in the three expected fixture-helper digests.
param([Parameter(Mandatory)][string]$ScratchDirectory)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Assert-True($Condition, $Message) { if (-not $Condition) { throw $Message } }
function Write-Json($Path, $Value) {
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 20) + "`n")
}
function Sha($Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
$ScratchDirectory = [IO.Path]::GetFullPath($ScratchDirectory)
if (Test-Path -LiteralPath $ScratchDirectory) { throw 'ScratchDirectory must be new.' }
[void][IO.Directory]::CreateDirectory($ScratchDirectory)
$sourceWrapper = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'run_auxiliary.ps1'))
$expectedHelpers = @{
    'reset_task_mount.py' = 'da301d14b2e9916b03e106be86edd233488dbce1a935a160ce91e4451158382f'
    'run_guarded.ps1' = '392087d82b47dbb1fdf40e6814ede576cd371a861e828222c123fa621d2fe313'
    'host_memory_guard.psm1' = '1a90a0cf8bdc0b9390475733b8915447eb6e774e3de88428bc45aaed105a6b3c'
}
$fakeModule = @'
function Get-GuardHostMemory {
    @{utc='fixture'; available_physical_bytes=20GB; available_commit_bytes=20GB}
}
Export-ModuleMember -Function Get-GuardHostMemory
'@
$fakeGuard = @'
param($CommandJson, $EvidenceDirectory, $RunId, $MemoryMaxMiB, $CpuQuotaPercent, $TimeoutSeconds)
$ErrorActionPreference = 'Stop'
if (-not $env:CODEX_AUXILIARY_TEST_ROOT) { throw 'Missing offline fixture root.' }
$config = Get-Content -Raw -LiteralPath (Join-Path $env:CODEX_AUXILIARY_TEST_ROOT 'guard-fixture.json') | ConvertFrom-Json
[void][IO.Directory]::CreateDirectory($EvidenceDirectory)
$hash = (Get-FileHash -LiteralPath $CommandJson -Algorithm SHA256).Hash.ToLowerInvariant()
if ($config.mode -eq 'hash_mismatch') { $hash = '0' * 64 }
$policy = @{command_sha256=$hash; memory_max_mib=$MemoryMaxMiB; cpu_quota_percent=$CpuQuotaPercent;
    argv=(Get-Content -Raw -LiteralPath $CommandJson | ConvertFrom-Json -NoEnumerate)}
$policy | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $EvidenceDirectory 'policy.json')
@{status='completed'; exit_code=0; final_scope_cleanup=@{verified=($config.mode -ne 'bad_cleanup')}} |
    ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $EvidenceDirectory 'result.json')
exit 0
'@
$driver = @'
param([string]$Mode, [string]$RunId, [switch]$DryRun)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$global:auxTestRoot = $PSScriptRoot
$global:auxTestMode = $Mode
$global:auxTestCalls = 0
function global:wsl.exe {
    $global:auxTestCalls++
    [IO.File]::AppendAllText((Join-Path $global:auxTestRoot 'wsl-mock-calls.txt'), ($args -join ' ') + "`n")
    if ($global:auxTestMode -eq 'forbid') { throw 'OFFLINE FIXTURE: unexpected WSL admission.' }
    if ($global:auxTestMode -eq 'hold') {
        [IO.File]::WriteAllText((Join-Path $global:auxTestRoot 'held.txt'), 'owner must already be durable')
        while ($true) { [Threading.Thread]::Sleep(100) }
    }
    if ($global:auxTestMode -eq 'mutate' -and $global:auxTestCalls -eq 1) {
        [IO.File]::WriteAllText((Join-Path $global:auxTestRoot 'command.json'), '["changed-after-capture"]')
        foreach ($name in @('run_guarded.ps1', 'host_memory_guard.psm1', 'reset_task_mount.py')) {
            [IO.File]::WriteAllText((Join-Path $global:auxTestRoot "harness/$name"), 'throw "changed after capture"')
        }
    }
    $global:LASTEXITCODE = 0
    if ($args -contains 'wslpath') { '/offline-fixture' }
}
function global:Start-Sleep { param($Milliseconds) } # Do not wait or inspect the host.
if ((Get-Command wsl.exe).CommandType -ne 'Function') { throw 'WSL mock was not installed.' }
& (Join-Path $PSScriptRoot 'harness/run_auxiliary.ps1') -CommandJson (Join-Path $PSScriptRoot 'command.json') -RunId $RunId -DryRun:$DryRun
'@
function New-Fixture([string]$Name, [string]$GuardMode = 'good', [int]$Rows = 29) {
    $root = Join-Path $ScratchDirectory $Name
    $harness = Join-Path $root 'harness'
    [void][IO.Directory]::CreateDirectory($harness)
    [void][IO.Directory]::CreateDirectory((Join-Path $root 'evidence/m0-v7b-01'))
    [IO.File]::WriteAllText((Join-Path $root 'command.json'), '["fixture-command","before-capture"]')
    [IO.File]::WriteAllText((Join-Path $root 'driver.ps1'), $driver)
    Write-Json (Join-Path $root 'guard-fixture.json') @{mode=$GuardMode}
    Write-Json (Join-Path $root 'evidence/m0-v7b-01/matrix-progress.json') @{
        status='completed_no_gate_verdict'; rows=@(1..$Rows | ForEach-Object { @{status='checked_and_archived'} })
    }
    [IO.File]::WriteAllText((Join-Path $harness 'run_guarded.ps1'), $fakeGuard)
    [IO.File]::WriteAllText((Join-Path $harness 'host_memory_guard.psm1'), $fakeModule)
    [IO.File]::WriteAllText((Join-Path $harness 'reset_task_mount.py'), '# Inert fixture: never executed.')
    $wrapper = $sourceWrapper
    foreach ($name in $expectedHelpers.Keys) {
        $expected = $expectedHelpers[$name]
        Assert-True (($wrapper.Split($expected, [StringSplitOptions]::None).Count - 1) -eq 1) "Expected helper digest must occur once: $name"
        $wrapper = $wrapper.Replace($expected, (Sha (Join-Path $harness $name)))
    }
    [IO.File]::WriteAllText((Join-Path $harness 'run_auxiliary.ps1'), $wrapper)
    return $root
}
function Start-Fixture($Root, $Mode, $RunId, [switch]$DryRun) {
    $info = [Diagnostics.ProcessStartInfo]::new((Join-Path $PSHOME 'pwsh.exe'))
    $info.UseShellExecute = $false
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.CreateNoWindow = $true
    foreach ($arg in @('-NoProfile', '-File', (Join-Path $Root 'driver.ps1'), '-Mode', $Mode, '-RunId', $RunId)) {
        [void]$info.ArgumentList.Add($arg)
    }
    if ($DryRun) { [void]$info.ArgumentList.Add('-DryRun') }
    $info.Environment['CODEX_AUXILIARY_TEST_ROOT'] = $Root
    $process = [Diagnostics.Process]::Start($info)
    return @{process=$process; stdout=$process.StandardOutput.ReadToEndAsync(); stderr=$process.StandardError.ReadToEndAsync()}
}
function Finish-Fixture($Job) {
    try {
        if (-not $Job.process.WaitForExit(15000)) { throw 'Offline fixture subprocess timed out.' }
        return @{exit_code=$Job.process.ExitCode; stdout=$Job.stdout.GetAwaiter().GetResult(); stderr=$Job.stderr.GetAwaiter().GetResult()}
    } finally {
        if (-not $Job.process.HasExited) { $Job.process.Kill($true); $Job.process.WaitForExit() }
        $Job.process.Dispose()
    }
}
function Run-Fixture($Root, $Mode, $RunId, [switch]$DryRun) {
    Finish-Fixture (Start-Fixture $Root $Mode $RunId -DryRun:$DryRun)
}
function Read-Owner($Root) {
    Get-Content -Raw -LiteralPath (Join-Path $Root 'evidence/auxiliary/active-owner.json') | ConvertFrom-Json
}

# The workload path is not admitted before the exact frozen-matrix completion gate.
$root = New-Fixture 'incomplete' -Rows 28
$result = Run-Fixture $root forbid incomplete
Assert-True ($result.exit_code -ne 0 -and $result.stderr -match 'still incomplete') 'Incomplete matrix admitted.'
Assert-True (-not (Test-Path -LiteralPath (Join-Path $root 'wsl-mock-calls.txt'))) 'Incomplete matrix reached reset.'
$result = Run-Fixture $root forbid dry-run -DryRun
Assert-True ($result.exit_code -eq 0 -and ($result.stdout | ConvertFrom-Json).matrix_complete -eq $false) 'DryRun did not report incomplete matrix.'
Assert-True (-not (Test-Path -LiteralPath (Join-Path $root 'evidence/auxiliary'))) 'DryRun created runtime ownership/evidence.'

# Kill an actual wrapper subprocess after its durable owner is saved, before any
# reset/guard launch. Its OS file lock disappears; the persisted owner must suffice.
$root = New-Fixture 'terminated-owner'
$job = Start-Fixture $root hold interrupted
try {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while (-not (Test-Path -LiteralPath (Join-Path $root 'held.txt'))) {
        if ($job.process.HasExited) { throw "Wrapper failed before hold: $($job.stderr.GetAwaiter().GetResult())" }
        if ($timer.Elapsed.TotalSeconds -ge 15) { throw 'Wrapper never reached the inert hold point.' }
        [Threading.Thread]::Sleep(20)
    }
    Assert-True ((Read-Owner $root).status -eq 'starting') 'Owner was not durable before first reset interaction.'
    $job.process.Kill($true)
    $job.process.WaitForExit()
} finally {
    if (-not $job.process.HasExited) { $job.process.Kill($true); $job.process.WaitForExit() }
    $job.process.Dispose()
}
$callsBefore = [IO.File]::ReadAllText((Join-Path $root 'wsl-mock-calls.txt'))
$result = Run-Fixture $root forbid after-termination
Assert-True ($result.exit_code -ne 0 -and $result.stderr -match 'prior auxiliary owner is unresolved') 'Termination released admission despite unresolved durable owner.'
Assert-True ([IO.File]::ReadAllText((Join-Path $root 'wsl-mock-calls.txt')) -ceq $callsBefore) 'Second launch reached reset despite unresolved owner.'
Assert-True (-not (Test-Path -LiteralPath (Join-Path $root 'evidence/auxiliary/after-termination'))) 'Rejected second run created evidence destination.'

# Original command/helper files can change after capture: only the captured bytes
# may be archived, imported and executed by the copied wrapper.
$root = New-Fixture 'snapshot-mutation'
$commandHash = Sha (Join-Path $root 'command.json')
$helperHashes = @{}
foreach ($name in $expectedHelpers.Keys) { $helperHashes[$name] = Sha (Join-Path $root "harness/$name") }
$result = Run-Fixture $root mutate captured
Assert-True ($result.exit_code -eq 0) "Frozen snapshot failed: $($result.stderr)"
$destination = Join-Path $root 'evidence/auxiliary/captured'
$record = Read-Owner $root
Assert-True ($record.status -eq 'completed_no_gate_verdict' -and $record.command_sha256 -eq $commandHash) 'Completed owner/hash evidence mismatch.'
Assert-True ((Sha (Join-Path $destination 'command.json')) -eq $commandHash) 'Archived command did not preserve captured bytes.'
Assert-True ((Sha (Join-Path $root 'command.json')) -ne $commandHash) 'Mutation fixture did not change original command.'
foreach ($name in $expectedHelpers.Keys) {
    Assert-True ((Sha (Join-Path $destination "control-tools/$name")) -eq $helperHashes[$name]) "Helper snapshot changed: $name"
    Assert-True ($record.helper_sha256.$name -eq $helperHashes[$name]) "Recorded helper digest mismatch: $name"
}
$policy = Get-Content -Raw -LiteralPath (Join-Path $destination 'guard/policy.json') | ConvertFrom-Json
Assert-True ($policy.command_sha256 -eq $commandHash -and $policy.argv[1] -eq 'before-capture') 'Fake guard did not receive captured command.'
Assert-True ($policy.memory_max_mib -eq 10240 -and $policy.cpu_quota_percent -eq 600) 'Guard resource arguments changed.'
$preflight = Get-Content -Raw -LiteralPath (Join-Path $destination 'host-budget-preflight.json') | ConvertFrom-Json
Assert-True ($preflight.samples.Count -eq 2 -and $preflight.status -eq 'ready') 'Preflight did not require two ready samples.'

foreach ($failure in @('hash_mismatch', 'bad_cleanup')) {
    $root = New-Fixture $failure -GuardMode $failure
    $result = Run-Fixture $root normal first
    $expectedError = if ($failure -eq 'hash_mismatch') { 'different command snapshot' } else { 'Final scope cleanup is not verified' }
    Assert-True ($result.exit_code -ne 0 -and $result.stderr -match $expectedError) "Guard evidence failure was accepted: $failure"
    Assert-True ((Read-Owner $root).status -eq 'stopped_for_assessment') 'Failed owner was not preserved.'
    $result = Run-Fixture $root forbid second
    Assert-True ($result.exit_code -ne 0 -and $result.stderr -match 'prior auxiliary owner is unresolved') 'Stopped owner permitted another launch.'
}

# A completed owner permits another run only while its final cleanup proof exists.
$root = New-Fixture 'completed-owner'
$result = Run-Fixture $root normal first
Assert-True ($result.exit_code -eq 0) "Initial fixture failed: $($result.stderr)"
$result = Run-Fixture $root normal second
Assert-True ($result.exit_code -eq 0 -and (Read-Owner $root).run_id -eq 'second') 'Verified prior completion did not release admission.'
$resultPath = Join-Path $root 'evidence/auxiliary/second/guard/result.json'
$priorResult = Get-Content -Raw -LiteralPath $resultPath | ConvertFrom-Json
$priorResult.final_scope_cleanup.verified = $false
Write-Json $resultPath $priorResult
$result = Run-Fixture $root forbid third
Assert-True ($result.exit_code -ne 0 -and $result.stderr -match 'Prior owner lacks verified successful scope cleanup') 'Completed status bypassed missing cleanup proof.'

Write-Output 'PASS: matrix/DryRun isolation, terminated/stopped owner admission, frozen command/helper bytes, guard hash/cleanup proof, and verified completion. Only offline fixtures and PowerShell subprocesses; no WSL, real guard, host sampling, or workload.'
