#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$CommandJson,
    [Parameter(Mandatory)][string]$EvidenceDirectory,
    [Parameter(Mandatory)][string]$RunId,
    [Parameter(Mandatory)][int]$MemoryMaxMiB,
    [string]$Distribution = 'Ubuntu',
    [int]$CpuQuotaPercent = 600,
    [int]$MinimumFreePhysicalMiB = 3072,
    [int]$MinimumFreeCommitMiB = 4096,
    [int]$IntervalMilliseconds = 500,
    [int]$TimeoutSeconds = 3600
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'host_memory_guard.psm1') -Force
$result = Invoke-GuardedCohort @PSBoundParameters
$result | ConvertTo-Json -Depth 12
exit [int]$result.exit_code
