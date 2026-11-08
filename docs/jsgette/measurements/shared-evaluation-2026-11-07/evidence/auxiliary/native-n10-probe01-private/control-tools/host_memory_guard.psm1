Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-GuardHostMemory {
    if (-not ('SharedEvaluationHostGuard.Native' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
namespace SharedEvaluationHostGuard {
  [StructLayout(LayoutKind.Sequential)]
  public struct PerformanceInformation {
    public uint cb;
    public UIntPtr CommitTotal, CommitLimit, CommitPeak;
    public UIntPtr PhysicalTotal, PhysicalAvailable, SystemCache;
    public UIntPtr KernelTotal, KernelPaged, KernelNonpaged, PageSize;
    public uint HandleCount, ProcessCount, ThreadCount;
  }
  public static class Native {
    [DllImport("psapi.dll", SetLastError=true)]
    static extern bool GetPerformanceInfo(out PerformanceInformation info, uint size);
    public static ulong[] Read() {
      PerformanceInformation info;
      if (!GetPerformanceInfo(out info, (uint)Marshal.SizeOf<PerformanceInformation>()))
        throw new Win32Exception(Marshal.GetLastWin32Error());
      ulong page = info.PageSize.ToUInt64();
      return new ulong[] {
        info.PhysicalAvailable.ToUInt64() * page,
        (info.CommitLimit.ToUInt64() - info.CommitTotal.ToUInt64()) * page,
        info.PhysicalTotal.ToUInt64() * page,
        info.CommitLimit.ToUInt64() * page,
        info.CommitTotal.ToUInt64() * page
      };
    }
  }
}
'@
    }
    $value = [SharedEvaluationHostGuard.Native]::Read()
    return [ordered]@{
        utc = [DateTimeOffset]::UtcNow.ToString('o')
        available_physical_bytes = $value[0]
        available_commit_bytes = $value[1]
        total_physical_bytes = $value[2]
        commit_limit_bytes = $value[3]
        committed_bytes = $value[4]
    }
}

function Get-GuardMemoryFailure {
    param($Memory, [long]$MinimumPhysicalBytes, [long]$MinimumCommitBytes)
    $reasons = @()
    if ($Memory.available_physical_bytes -lt $MinimumPhysicalBytes) {
        $reasons += 'host_available_physical_below_reserve'
    }
    if ($Memory.available_commit_bytes -lt $MinimumCommitBytes) {
        $reasons += 'host_available_commit_below_reserve'
    }
    return [pscustomobject]@{ unsafe = ($reasons.Count -gt 0); reasons = $reasons }
}

function New-GuardScopeIdentity {
    param([string]$RunId)
    if ($RunId -notmatch '\A[a-zA-Z0-9][a-zA-Z0-9-]{0,48}\z') {
        throw 'RunId must contain only 1-49 letters, digits, or hyphens.'
    }
    $nonce = [Guid]::NewGuid().ToString('N')
    return [ordered]@{
        unit = "shared-eval-guard-$RunId-$nonce.scope"
        description = "shared-evaluation-owned:$nonce"
        invocation_id = $null
    }
}

function Assert-GuardScopeOwnership {
    param($Expected, $Observed)
    if ($null -eq $Observed -or $Observed.Id -cne $Expected.unit -or
        $Observed.Description -cne $Expected.description -or
        -not $Observed.ControlGroup.EndsWith('/' + $Expected.unit, [StringComparison]::Ordinal) -or
        [string]::IsNullOrWhiteSpace($Observed.InvocationID)) {
        throw 'Scope ownership could not be verified; refusing to signal it.'
    }
    if ($Expected.invocation_id -and $Observed.InvocationID -cne $Expected.invocation_id) {
        throw 'Scope invocation changed; refusing to signal it.'
    }
}

function Write-GuardJson {
    param([string]$Path, $Value)
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    $writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
    try { $writer.WriteLine(($Value | ConvertTo-Json -Depth 16)) }
    finally { $writer.Dispose() }
}

function Invoke-GuardNative {
    param([string]$Executable, [string[]]$Arguments, [int]$TimeoutMilliseconds = 5000)
    $start = [Diagnostics.ProcessStartInfo]::new($Executable)
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($argument in $Arguments) { $start.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::Start($start)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    try {
        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            $process.Kill()
            throw "Guard control command timed out: $Executable"
        }
        return [pscustomobject]@{
            exit_code = $process.ExitCode
            stdout = $stdout.GetAwaiter().GetResult()
            stderr = $stderr.GetAwaiter().GetResult()
        }
    } finally { $process.Dispose() }
}

function Get-GuardScope {
    param([string]$Wsl, [string]$Distribution, [string]$Unit)
    $result = Invoke-GuardNative $Wsl @('-d', $Distribution, '--exec', 'systemctl', '--user',
        'show', '--no-pager', '--property=Id,Description,InvocationID,ControlGroup,LoadState,ActiveState', $Unit)
    $values = @{}
    foreach ($line in $result.stdout.Split("`n")) {
        if ($line.Contains('=')) {
            $name, $value = $line.TrimEnd("`r").Split('=', 2)
            $values[$name] = $value
        }
    }
    if ($values['LoadState'] -eq 'not-found') { return $null }
    if ($result.exit_code -ne 0) {
        throw "Cannot inspect generated scope: $($result.stderr.Trim())"
    }
    return $values
}

function Stop-GuardScope {
    param([string]$Wsl, [string]$Distribution, $Identity)
    $observed = Get-GuardScope $Wsl $Distribution $Identity.unit
    if ($null -eq $observed -or $observed.ActiveState -in @('inactive', 'failed')) {
        return [ordered]@{ present = ($null -ne $observed); signalled = $false; verified = $true }
    }
    Assert-GuardScopeOwnership $Identity $observed
    $result = Invoke-GuardNative $Wsl @('-d', $Distribution, '--exec', 'systemctl', '--user',
        'kill', '--kill-whom=all', '--signal=SIGKILL', $Identity.unit)
    if ($result.exit_code -ne 0) {
        $remaining = Get-GuardScope $Wsl $Distribution $Identity.unit
        if ($null -ne $remaining -and $remaining.ActiveState -notin @('inactive', 'failed')) {
            throw "Could not stop owned scope: $($result.stderr.Trim())"
        }
    }
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $remaining = Get-GuardScope $Wsl $Distribution $Identity.unit
        if ($null -eq $remaining -or $remaining.ActiveState -in @('inactive', 'failed')) {
            return [ordered]@{ present = $true; signalled = $true; verified = $true; identity = $observed }
        }
        Assert-GuardScopeOwnership $Identity $remaining
        Start-Sleep -Milliseconds 100
    }
    throw 'Owned scope did not become inactive after SIGKILL.'
}

function Invoke-GuardedCohort {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$CommandJson,
        [Parameter(Mandatory)][string]$EvidenceDirectory,
        [Parameter(Mandatory)][string]$RunId,
        [Parameter(Mandatory)][ValidateRange(256, 1048576)][int]$MemoryMaxMiB,
        [string]$Distribution = 'Ubuntu',
        [ValidateRange(1, 6400)][int]$CpuQuotaPercent = 600,
        [ValidateRange(256, 1048576)][int]$MinimumFreePhysicalMiB = 3072,
        [ValidateRange(256, 1048576)][int]$MinimumFreeCommitMiB = 4096,
        [ValidateRange(100, 5000)][int]$IntervalMilliseconds = 500,
        [ValidateRange(1, 86400)][int]$TimeoutSeconds = 3600
    )
    if (-not $IsWindows -or $PSVersionTable.PSVersion.Major -lt 7) {
        throw 'The external guard requires PowerShell 7 on the Windows host.'
    }
    $identity = New-GuardScopeIdentity $RunId
    $commandPath = (Resolve-Path -LiteralPath $CommandJson).Path
    $decoded = [IO.File]::ReadAllText($commandPath) | ConvertFrom-Json -NoEnumerate
    if ($decoded -isnot [Array] -or $decoded.Count -eq 0 -or
        @($decoded | Where-Object { $_ -isnot [string] -or $_.Contains([char]0) }).Count -gt 0) {
        throw 'CommandJson must contain a nonempty JSON array of literal string arguments.'
    }
    $evidence = [IO.Path]::GetFullPath($EvidenceDirectory)
    if (Test-Path -LiteralPath $evidence) { throw 'EvidenceDirectory must not already exist.' }
    [void][IO.Directory]::CreateDirectory($evidence)
    $wsl = (Get-Command wsl.exe -CommandType Application | Select-Object -First 1).Source
    # A late-starting WSL launcher must never start the workload after the guard aborts.
    # This tiny gate expires without a release; its abstract socket leaves no files.
    $gateCode = @'
import os, socket, sys
gate = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
gate.bind('\0' + sys.argv[1])
gate.listen(1)
gate.settimeout(15)
try:
    connection, _ = gate.accept()
    connection.settimeout(2)
    token = connection.recv(256).decode()
    connection.close()
    if token != sys.argv[1]:
        sys.exit(75)
finally:
    gate.close()
os.execvp(sys.argv[2], sys.argv[2:])
'@
    $releaseCode = @'
import socket, sys, time
deadline = time.monotonic() + 2
while True:
    channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        channel.connect('\0' + sys.argv[1])
        channel.sendall(sys.argv[1].encode())
        break
    except (ConnectionRefusedError, FileNotFoundError):
        if time.monotonic() >= deadline:
            raise
        time.sleep(0.05)
    finally:
        channel.close()
'@
    $argv = @('-d', $Distribution, '--exec', 'systemd-run', '--user', '--scope',
              "--unit=$($identity.unit)", "--description=$($identity.description)",
              '--property=CPUAccounting=yes', '--property=MemoryAccounting=yes',
              "--property=CPUQuota=$CpuQuotaPercent%", "--property=MemoryMax=$($MemoryMaxMiB)M",
              '--property=MemorySwapMax=0', '--', 'python3', '-c', $gateCode, $identity.unit) + [string[]]$decoded
    $policy = [ordered]@{
        schema_version = 1; scope = $identity; distribution = $Distribution
        memory_max_mib = $MemoryMaxMiB; cpu_quota_percent = $CpuQuotaPercent
        minimum_free_physical_bytes = [long]$MinimumFreePhysicalMiB * 1MB
        minimum_free_commit_bytes = [long]$MinimumFreeCommitMiB * 1MB
        sample_interval_ms = $IntervalMilliseconds; timeout_seconds = $TimeoutSeconds; startup_gate_timeout_seconds = 15
        command_file = $commandPath; command_sha256 = (Get-FileHash -LiteralPath $commandPath).Hash.ToLowerInvariant()
        executable = $wsl; argv = $argv
    }
    Write-GuardJson (Join-Path $evidence 'policy.json') $policy
    $samples = [IO.StreamWriter]::new((Join-Path $evidence 'host-memory.jsonl'), $false,
                                     [Text.UTF8Encoding]::new($false))
    $samples.AutoFlush = $true
    $result = [ordered]@{ schema_version = 1; status = 'guard_error'; exit_code = 75
                         scope = $identity; launched = $false; evidence = $evidence }
    $process = $null
    $stdoutFile = $null
    $stderrFile = $null
    $stdoutTask = $null
    $stderrTask = $null
    $watch = [Diagnostics.Stopwatch]::StartNew()
    try {
        $memory = Get-GuardHostMemory
        $samples.WriteLine(($memory | ConvertTo-Json -Compress))
        $result.initial_memory = $memory
        $failure = Get-GuardMemoryFailure $memory $policy.minimum_free_physical_bytes $policy.minimum_free_commit_bytes
        if ($failure.unsafe) {
            $result.status = 'infrastructure_abort'
            $result.reason = 'preflight_host_memory_reserve'
            $result.threshold_failures = $failure.reasons
        } else {
            if ($null -ne (Get-GuardScope $wsl $Distribution $identity.unit)) {
                throw 'Generated scope already exists; refusing to attach to it.'
            }
            $start = [Diagnostics.ProcessStartInfo]::new($wsl)
            $start.UseShellExecute = $false
            $start.CreateNoWindow = $true
            $start.RedirectStandardOutput = $true
            $start.RedirectStandardError = $true
            foreach ($argument in $argv) { $start.ArgumentList.Add($argument) }
            $stdoutFile = [IO.File]::Open((Join-Path $evidence 'stdout.log'), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
            $stderrFile = [IO.File]::Open((Join-Path $evidence 'stderr.log'), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
            $process = [Diagnostics.Process]::Start($start)
            $result.launched = $true
            $result.windows_launcher_pid = $process.Id
            $stdoutTask = $process.StandardOutput.BaseStream.CopyToAsync($stdoutFile)
            $stderrTask = $process.StandardError.BaseStream.CopyToAsync($stderrFile)
            while (-not $process.HasExited) {
                $memory = Get-GuardHostMemory
                $samples.WriteLine(($memory | ConvertTo-Json -Compress))
                $failure = Get-GuardMemoryFailure $memory $policy.minimum_free_physical_bytes $policy.minimum_free_commit_bytes
                if ($failure.unsafe -or $watch.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
                    $result.status = 'infrastructure_abort'
                    $result.reason = $(if ($failure.unsafe) { 'host_memory_reserve' } else { 'guard_timeout' })
                    $result.threshold_failures = $failure.reasons
                    $result.abort_memory = $memory
                    break
                }
                if (-not $identity.invocation_id) {
                    $observed = Get-GuardScope $wsl $Distribution $identity.unit
                    if ($null -ne $observed -and $observed.ActiveState -eq 'active') {
                        Assert-GuardScopeOwnership $identity $observed
                        $identity.invocation_id = $observed.InvocationID
                        Write-GuardJson (Join-Path $evidence 'scope-identity.json') $observed
                        $memory = Get-GuardHostMemory
                        $samples.WriteLine(($memory | ConvertTo-Json -Compress))
                        $failure = Get-GuardMemoryFailure $memory $policy.minimum_free_physical_bytes $policy.minimum_free_commit_bytes
                        if ($failure.unsafe) {
                            $result.status = 'infrastructure_abort'
                            $result.reason = 'host_memory_reserve_before_release'
                            $result.threshold_failures = $failure.reasons
                            $result.abort_memory = $memory
                            break
                        }
                        $released = Invoke-GuardNative $wsl @('-d', $Distribution, '--exec', 'python3', '-c', $releaseCode, $identity.unit)
                        if ($released.exit_code -ne 0) { throw "Cannot release owned startup gate: $($released.stderr.Trim())" }
                        $result.workload_released = $true
                    }
                }
                Start-Sleep -Milliseconds $IntervalMilliseconds
            }
            if ($process.HasExited -and $result.status -ne 'infrastructure_abort') {
                $result.command_exit_code = $process.ExitCode
                $result.exit_code = $process.ExitCode
                $result.status = $(if ($process.ExitCode -eq 0) { 'completed' } else { 'command_failed' })
            }
        }
    } catch {
        $result.status = 'guard_error'
        $result.error = $_.Exception.Message
        $result.exit_code = 75
    } finally {
        if ($result.launched) {
            try {
                $result.scope_cleanup = Stop-GuardScope $wsl $Distribution $identity
                if ($result.scope_cleanup.signalled -and $result.status -eq 'completed') {
                    $result.status = 'infrastructure_abort'
                    $result.reason = 'command_left_scope_processes'
                    $result.exit_code = 75
                }
            } catch {
                $result.cleanup_error = $_.Exception.Message
                $result.exit_code = 75
                if ($result.status -eq 'completed') { $result.status = 'guard_error' }
            }
            try {
                if (-not $process.HasExited -and -not $process.WaitForExit(5000)) {
                    # Only our Windows launcher is terminated; no other WSL session is stopped.
                    $process.Kill()
                    [void]$process.WaitForExit(5000)
                    $result.launcher_terminated = $true
                }
                # Recheck after launcher exit to catch scope creation during initial cleanup.
                $result.final_scope_cleanup = Stop-GuardScope $wsl $Distribution $identity
                foreach ($task in @($stdoutTask, $stderrTask)) {
                    if ($null -ne $task -and -not $task.Wait(5000)) { throw 'Output drain timed out.' }
                }
            } catch {
                $result.final_cleanup_error = $_.Exception.Message
                $result.exit_code = 75
                if ($result.status -eq 'completed') { $result.status = 'guard_error' }
            }
        }
        foreach ($resource in @($stdoutFile, $stderrFile, $process, $samples)) {
            try { if ($null -ne $resource) { $resource.Dispose() } }
            catch {
                $result.dispose_error = $_.Exception.Message
                $result.exit_code = 75
                if ($result.status -eq 'completed') { $result.status = 'guard_error' }
            }
        }
        $result.elapsed_seconds = $watch.Elapsed.TotalSeconds
        Write-GuardJson (Join-Path $evidence 'result.json') $result
    }
    return $result
}

Export-ModuleMember -Function Get-GuardHostMemory, Get-GuardMemoryFailure, New-GuardScopeIdentity,
    Assert-GuardScopeOwnership, Invoke-GuardedCohort
