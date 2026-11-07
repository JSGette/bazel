#Requires -Version 7.0
param([Parameter(Mandatory)][string]$ScratchDirectory)
$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'run_matrix.ps1'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($runner, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
foreach ($name in @('Write-Json', 'Wait-HostBudgetHeadroom')) {
    $node = $ast.Find({param($item) $item -is [Management.Automation.Language.FunctionDefinitionAst] -and $item.Name -eq $name}, $true)
    Invoke-Expression $node.Extent.Text
}
function Assert-True($Condition, $Message) { if (-not $Condition) { throw $Message } }
$script:queue = [Collections.Generic.Queue[object]]::new()
$script:reads = 0
function Get-GuardHostMemory { $script:reads++; return $script:queue.Dequeue() }
function Start-Sleep { param($Milliseconds) } # Unit tests never wait or sample the host.
if (Test-Path -LiteralPath $ScratchDirectory) { throw 'Scratch directory must be new.' }
[void][IO.Directory]::CreateDirectory($ScratchDirectory)
$good = @{utc='test'; available_physical_bytes=13824MB; available_commit_bytes=14848MB}
$bad = @{utc='test'; available_physical_bytes=13824MB; available_commit_bytes=14848MB-1}
foreach ($sample in @($good, $bad, $good, $good)) { $script:queue.Enqueue($sample) }
$ready = Wait-HostBudgetHeadroom (Join-Path $ScratchDirectory 'ready.json') 10240
Assert-True ($ready.status -eq 'ready' -and $ready.samples.Count -eq 4) 'Consecutive samples or inclusive thresholds failed.'
Assert-True ($ready.policy.minimum_physical_bytes -eq 13824MB -and $ready.policy.minimum_commit_bytes -eq 14848MB) 'Budget arithmetic failed.'
$script:queue.Enqueue($bad)
$timeout = Wait-HostBudgetHeadroom (Join-Path $ScratchDirectory 'timeout.json') 10240 0
Assert-True ($timeout.status -eq 'timeout' -and $timeout.samples.Count -eq 1) 'Timeout must fail closed.'
$errorResult = Wait-HostBudgetHeadroom (Join-Path $ScratchDirectory 'error.json') 10240
Assert-True ($errorResult.status -eq 'error') 'Sampling errors must fail closed.'

$config = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'matrix-v7.json') -Raw | ConvertFrom-Json -AsHashtable
$config.require_budget_headroom = $true
$manifest = Join-Path $ScratchDirectory 'manifest.json'
Write-Json $manifest $config
$before = $script:reads
& $runner -MemoryMaxMiB 10240 -BatchId 'budget-dry-run' -Manifest $manifest -EvidenceDirectory $ScratchDirectory -DryRun
Assert-True ($script:reads -eq $before) 'DryRun sampled host memory.'
$progress = Get-Content -LiteralPath (Join-Path $ScratchDirectory 'budget-dry-run/matrix-progress.json') -Raw | ConvertFrom-Json
Assert-True ($progress.status -eq 'planned_only' -and $progress.require_budget_headroom) 'Enabled dry-run planning failed.'
Assert-True (-not (Get-ChildItem -LiteralPath (Join-Path $ScratchDirectory 'budget-dry-run') -Filter '*host-budget-preflight*')) 'DryRun performed preflight.'
foreach ($invalid in @('true', 1, $null)) {
    $config.require_budget_headroom = $invalid; Write-Json $manifest $config
    $rejected = $false
    try { & $runner -MemoryMaxMiB 10240 -BatchId ('invalid-' + [Guid]::NewGuid().ToString('N').Substring(0,8)) -Manifest $manifest -EvidenceDirectory $ScratchDirectory -DryRun }
    catch { $rejected = $_.Exception.Message -match 'JSON boolean' }
    Assert-True $rejected 'Non-boolean manifest setting accepted.'
}
Write-Output 'PASS: budget arithmetic, consecutive samples, timeout/error persistence, dry-run isolation and boolean validation; no host reads or Linux commands.'
