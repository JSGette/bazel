#Requires -Version 7.0
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'host_memory_guard.psm1') -Force
function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function Assert-Throws([scriptblock]$Action, [string]$Message) {
    $threw = $false
    try { & $Action } catch { $threw = $true }
    Assert-True $threw $Message
}
$memory = @{ available_physical_bytes = 4GB; available_commit_bytes = 5GB }
Assert-True (-not (Get-GuardMemoryFailure $memory 3GB 4GB).unsafe) 'Healthy memory rejected'
Assert-True (Get-GuardMemoryFailure $memory 5GB 4GB).unsafe 'Physical threshold missed'
Assert-True (Get-GuardMemoryFailure $memory 3GB 6GB).unsafe 'Commit threshold missed'
Assert-True (-not (Get-GuardMemoryFailure $memory 4GB 5GB).unsafe) 'Exact boundary rejected'
$identity = New-GuardScopeIdentity 'unit-test'
$second = New-GuardScopeIdentity 'unit-test'
Assert-True ($identity.unit -ne $second.unit) 'Scope identities collided'
Assert-Throws { New-GuardScopeIdentity '../unrelated' } 'Unsafe unit name accepted'
$observed = @{ Id = $identity.unit; Description = $identity.description
               InvocationID = 'first'; ControlGroup = '/user.slice/' + $identity.unit }
Assert-GuardScopeOwnership $identity $observed
$identity.invocation_id = 'first'
Assert-GuardScopeOwnership $identity $observed
foreach ($field in @('Id', 'Description', 'InvocationID', 'ControlGroup')) {
    $changed = $observed.Clone()
    $changed[$field] = 'unrelated'
    Assert-Throws { Assert-GuardScopeOwnership $identity $changed } "Ownership field $field unchecked"
}
$changed = $observed.Clone()
$changed.ControlGroup += '-suffix'
Assert-Throws { Assert-GuardScopeOwnership $identity $changed } 'Scope path prefix collision accepted'
if ($IsWindows) {
    $actual = Get-GuardHostMemory
    Assert-True ($actual.total_physical_bytes -gt 0) 'Native physical memory unavailable'
    Assert-True ($actual.commit_limit_bytes -ge $actual.committed_bytes) 'Invalid native commit accounting'
}
Write-Output 'PASS: threshold boundaries, scope uniqueness, ownership rejection, native snapshot; no Linux command launched.'
