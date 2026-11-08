# First cap2 native/private diagnostics: three explicit arms

Prepared 2026-11-08. **Nothing in this recipe has been launched.** Wait for the successful candidate build, focused tests, final external guard and root's cap2 acceptance review. The test-only requalification's expected candidate is `/mnt/shared-evaluation-20261107/native-cap2-requalification-v1/candidate/bazel-native-cap2`; the emitter derives its actual path and SHA256 from the copied successful requalification report. Use that run's final guard, not the earlier failed build's guard. Never fill an unknown digest with a placeholder.

Run exactly these initial arms, serially: N2 private **reference**, N2 private **restart diagnostic**, N2 native **restart diagnostic**. All use identical base inputs, a fresh output-state root, one serial preparation and two identity calls per engine, verified shutdown before the measured requests, the same actual binary and seed, and 500 ms sampling. Each build timeout is 1,800 seconds; each outer guard timeout is 7,200 seconds. A failed arm, incomplete guard, fixture mismatch or missing accounting stops this sequence for review; preserve its files and do not reuse its root or RunId.

This is not the five-pair M3/M4 protocol. The helper's build-only window omits the full admission/drain boundary, the current native bundle changes more than graph sharing, N2 is below required N10, and observer perturbation is not yet qualified. Keep raw counters and CPU bounds; make no performance gate verdict. The reference always reports `performance_eligible: false`.

## Fixed inputs and decisions

- C archive: `E/evidence/m0-v7b-01/i10-ref-run.tar.gz`, SHA256 `30686e640a2de2d69c2d628e0aa9b11e3533a09bebb9ff91d6e92be23c63ef2b`; exact archived `report.json` SHA256 `7e6b14e43b72e5a762b5956913c6c094999e4227912fa880bf43e87bb226de85`.
- Datadog revision `85ad968bca856fa3d4e07b4d8e11961dabe6866f`; projection archive SHA256 `1108c28516419d55b8445135d42728478d9be14b372fdab3364987289d651b08`; private fixture tree `80a45a169b4d057558f3f8680dda3366b8d86f15`. The emitter checks these through the archived report, including base source/BUILD/configuration identities, before allowing comparisons.
- Download seed: exact 1,458 members of C `E/evidence/repository-cache-v7-seed.json`, SHA256 `f88890095d847d2832d5de4cc9565050a730d3423e4f057de543fd0d63415142`. The manifest names only those files under R `repository-cache-v7`; the runner verifies/copies each. It does not inventory live caches or inherit repository contents.
- Candidate base source commit `4862608d940b49293c68c014e413ecc9dc98aa8e`, Git tree SHA1 `a28c113856ec48838041bba3b9591ab9ea67e4ac`, unchanged admission cap 2. The native helper requires a 64-hex `source_tree_sha256`; use the builder's exact Git-archive tar SHA256 and retain the distinct `source_git_tree_sha1`. For an unpatched build its kind is `git-archive-tar-bytes`. For test-only requalification its kind is `base-git-archive-tar-bytes`, and the binary manifest separately carries `qualification_patch` with the test path, old/new digests and explicit exclusion from the base source pins. Preparation provenance preserves the prior-report lineage, base/qualified source verification and helper hashes. Require the before/after/candidate product binary hashes to match; this records the guarded report's unchanged-product evidence, while the arm still hashes the actual candidate at runtime. The base archive digest does not describe the patched test tree.
- Source repository remains an explicit canonical Linux path to the **original** Datadog Git repository, likely `/mnt/c/Users/jgett/datadog-agent` after root verifies it. The synthetic M0 fixture repository cannot supply the original source revision. `fixture.prepare` reads Git archive data and creates its own repository/worktrees below each new run root. Its source revision is checked before builds; the new reference's exact projection/tree/input identities are compared with C-archived M0 before any comparison is emitted.
- Both comparisons must use the exact five-file helper snapshot emitted for the reference. Preparation records all five hashes; the runner independently reports its four directly imported helper hashes. No frozen M0 helper is edited.

## 1. Capture the completed candidate report and emit the reference on C

Use PowerShell 7. The following variables deliberately distinguish C evidence from Linux paths. Select a fresh series name and a fresh C output directory. Do not execute any WSL command while the source build is active.

```powershell
$work = 'C:\Users\jgett\Documents\Codex\2026-11-07\i-ha\work'
$e = Join-Path $work 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07'
$series = 'native-n2-probe01'
$inputs = Join-Path $work "$series-inputs"
New-Item -ItemType Directory -Path $inputs -ErrorAction Stop | Out-Null

# Fill with the SUCCESSFUL requalification RunId after root's acceptance review.
$candidateGuard = Join-Path $e 'evidence/auxiliary/REPLACE-WITH-SUCCESSFUL-REQUALIFICATION-RUN-ID/guard/result.json'
$candidateReport = Join-Path $inputs 'candidate-build-report.json'
# Bounded copy of the final report only, after successful final guard; no scan.
$candidateReportLinux = '/mnt/c/' + $candidateReport.Substring(3).Replace('\', '/')
wsl.exe -d Ubuntu --exec python3 -B -c 'from pathlib import Path; import sys; data=Path(sys.argv[1]).read_bytes(); target=Path(sys.argv[2]); f=target.open("xb"); f.write(data); f.close()' '/mnt/shared-evaluation-20261107/native-cap2-requalification-v1/report.json' $candidateReportLinux
if ($LASTEXITCODE -ne 0) { throw 'Final report copy failed; preserve partial evidence.' }

$sourceRepository = '/mnt/c/Users/jgett/datadog-agent' # Root verifies original Git source path.
$ref = Join-Path $work "$series-reference"
python -B "$e/harness/prepare_native_n2.py" --role reference --series $series `
  --source-repository $sourceRepository --candidate-report $candidateReport `
  --candidate-guard-result $candidateGuard --output $ref
if ($LASTEXITCODE -ne 0) { throw 'Reference preparation failed.' }
```

The emitter only reads C evidence and writes a fresh C directory. It performs no WSL call, subprocess launch, live R read or candidate execution. Its outputs are `manifest.json`, `command.json`, `stage.json` and `harness/*.py`. Review these exact bytes. The command's argv names the future manifest path; its SHA256 is then placed inside the manifest. The manifest hash is **not** an argv dependency, so there is no hash cycle.

`host_preflight.path` points to the exact future sibling of `guard/`, `host-budget-preflight.json`. Its hash is intentionally absent from the prepared manifest: `run_auxiliary.ps1` creates that file after freezing command bytes. The runner reads the generated file once, hashes the same bytes it validates, checks the two recent samples, and records its observed digest. Do not mutate the manifest after launching to add a preflight hash.

## 2. Stage one emitted arm, then launch only through the external wrapper

Define this small staging function once. It copies explicit emitted files, validates their hashes, creates the fresh series root only for the reference, and uses exclusive file creation. It never launches a build or removes anything. A partial staging failure is preserved and requires review rather than repair/reuse.

```powershell
function Stage-NativeArm([string]$Bundle) {
    $bundleLinux = '/mnt/c/' + ([IO.Path]::GetFullPath($Bundle)).Substring(3).Replace('\', '/')
    $stageCode = @'
import hashlib,json,os,sys
from pathlib import Path
src=Path(sys.argv[1]); meta=json.loads((src/'stage.json').read_bytes())
raw=(src/'manifest.json').read_bytes(); cfg=json.loads(raw)
sha=lambda b:hashlib.sha256(b).hexdigest()
require=lambda ok,msg:None if ok else (_ for _ in ()).throw(ValueError(msg))
R=Path('/mnt/shared-evaluation-20261107'); task=Path(meta['task_root'])
require(R.resolve()==R and os.path.ismount(R),'R must be the owned mounted task volume')
require(task.parent==R and task.resolve()==task,'Task must be a normalized direct R child')
require(sha(raw)==meta['manifest_sha256'] and cfg['task_root']==str(task),'Manifest mismatch')
require(sha((src/'command.json').read_bytes())==meta['command_sha256']==cfg['guard_command_sha256'],'Command mismatch')
require(json.loads((src/'command.json').read_bytes())==cfg['launch_argv'],'Argv mismatch')
files={name:(src/'harness'/name).read_bytes() for name in meta['harness_sha256']}
require(all(sha(data)==meta['harness_sha256'][name] for name,data in files.items()),'Helper mismatch')
owner={'binary':cfg['binary'],'source_repository':cfg['source_repository'],'harness_sha256':meta['harness_sha256']}
if meta['role']=='reference':
    task.mkdir(); (task/'harness').mkdir(); (task/'config').mkdir()
    with (task/'staging-owner.json').open('x') as f: json.dump(owner,f,sort_keys=True)
    for name,data in files.items():
        with (task/'harness'/name).open('xb') as f: f.write(data)
else:
    require(json.loads((task/'staging-owner.json').read_bytes())==owner,'Series ownership or pins changed')
    require(all(sha((task/'harness'/name).read_bytes())==meta['harness_sha256'][name] for name in files),'Staged helper changed')
target=Path(meta['config_path'])
require(target.parent==task/'config' and target.resolve()==target,'Invalid config location')
require(not Path(cfg['run_root']).exists(),'Arm run root already exists')
with target.open('xb') as f: f.write(raw)
require(sha(target.read_bytes())==meta['manifest_sha256'],'Staged manifest digest mismatch')
print(json.dumps({'staged':str(target),'manifest_sha256':meta['manifest_sha256'],'launched':False}))
'@
    wsl.exe -d Ubuntu --exec python3 -B -c $stageCode $bundleLinux
    if ($LASTEXITCODE -ne 0) { throw 'Staging failed; preserve the partial series root.' }
}

Stage-NativeArm $ref
pwsh -NoProfile -File "$e/harness/run_auxiliary.ps1" `
  -CommandJson "$ref/command.json" -RunId "$series-ref" -TimeoutSeconds 7200
if ($LASTEXITCODE -ne 0) { throw 'Reference/guard failed. Do not launch comparisons.' }
```

The wrapper freezes exact command bytes, acquires its existing exclusive lease, requires completed archived M0, performs the fixed host preflight, and launches a fresh task-owned systemd scope at 10 GiB/no swap/six CPU quota. It owns host protection and final scope cleanup; the runner owns explicit engine shutdown and partial reports. Keep both final records. A reference success requires wrapper control `completed_no_gate_verdict`, guard `completed`/exit0, final cleanup `verified: true`/`signalled: false`/`present: false`, and runner `passed: true` plus `owned_daemons_absent`/`source_and_binary_unchanged`. The emitter also requires those three explicit final cleanup fields for the candidate guard; an inactive-but-still-present unit is insufficient for this stricter absence check. Do not infer success from an intermediate report or only the process exit.

## 3. Copy the independent reference, then emit private and native arms

After the reference's final guard has completed, copy only its exact report to C. The guard and policy already reside on C. The emitter hashes the copied report and final guard, binds the final guard's scope unit/invocation to the report, binds sibling `policy.json` and its command hash, checks archived fixture identities, and requires the same binary, seed, history and exact helper bundle.

```powershell
$referenceReport = Join-Path $inputs 'reference-report.json'
$referenceReportLinux = '/mnt/c/' + $referenceReport.Substring(3).Replace('\', '/')
wsl.exe -d Ubuntu --exec python3 -B -c 'from pathlib import Path; import sys; data=Path(sys.argv[1]).read_bytes(); target=Path(sys.argv[2]); f=target.open("xb"); f.write(data); f.close()' "/mnt/shared-evaluation-20261107/$series/ref/report.json" $referenceReportLinux
if ($LASTEXITCODE -ne 0) { throw 'Reference report copy failed.' }
$referenceGuard = Join-Path $e "evidence/auxiliary/$series-ref/guard/result.json"
$private = Join-Path $work "$series-private"
$native = Join-Path $work "$series-native"

# Two C-only emissions; neither command stages or launches anything.
foreach ($role in @('private', 'native')) {
    $output = Join-Path $work "$series-$role"
    python -B "$e/harness/prepare_native_n2.py" --role $role --series $series `
      --source-repository $sourceRepository --candidate-report $candidateReport `
      --candidate-guard-result $candidateGuard --harness-source "$ref/harness" `
      --reference-report $referenceReport --reference-guard-result $referenceGuard `
      --reference-manifest "$ref/manifest.json" --output $output
    if ($LASTEXITCODE -ne 0) { throw "$role manifest rejected; do not launch either comparison." }
}

Stage-NativeArm $private
pwsh -NoProfile -File "$e/harness/run_auxiliary.ps1" `
  -CommandJson "$private/command.json" -RunId "$series-private" -TimeoutSeconds 7200
if ($LASTEXITCODE -ne 0) { throw 'Private diagnostic/guard failed; preserve and review.' }

# Review private final report and final wrapper/guard evidence before this step.
Stage-NativeArm $native
pwsh -NoProfile -File "$e/harness/run_auxiliary.ps1" `
  -CommandJson "$native/command.json" -RunId "$series-native" -TimeoutSeconds 7200
if ($LASTEXITCODE -ne 0) { throw 'Native diagnostic/guard failed; preserve and review.' }
```

Archive each exact arm report, manifest, preparation/measured/BEP/resource evidence and wrapper guard files through the existing explicit owned-path archive workflow after final cleanup. Keep failures too. Validate two unique build UUIDs, exact workspaces, final engine PID identities, positive all-request BEP overlap and behavior/output checks. The helper must establish one native process with two engine bindings, versus two private processes, without any `info` call between restart and measured builds. Native commands must contain no `--output_base`; their engine bases derive from the shared control root. Do not reinterpret a startup failure or failed overlap as successful timing evidence.

For the first diagnostic report, present raw scope CPU, harness process CPU bounds, conservative non-harness bounds, daemon CPU, sampled aggregate daemon PSS/read coverage, scope memory peak/pressure, per-request times and cohort overlap separately. Non-harness CPU still includes clients, daemons and compiler descendants; it is not pure analysis CPU. A CPU bound does not remove observer-induced perturbation. Source/seed preparation costs remain visible in lifecycle evidence, while the narrower build interval stays explicitly labeled.

## Optional subsequent warm native diagnostic

Do not launch a warm arm as part of this recipe. If the three initial arms pass review, prepare a **new series/root** with an explicitly reviewed `state: warm` pair/reference contract and frozen command bytes, retaining exactly one preparation per view and no intervening shutdown. This emitter deliberately fixes `restart` and refuses to conceal a state change behind a command or manifest edit. Warm N2 remains diagnostic and cannot replace the required N1 warm or N10 acceptance strata.
