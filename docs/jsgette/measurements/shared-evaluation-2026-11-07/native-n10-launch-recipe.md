# Bounded cap-ten diagnostic: exactly three arms

Prepared 2026-11-08. This recipe prepares and later launches **only N10 private reference, N10 private restart, and N10 native restart**, in that order, on identical inputs. The coordinator launched the reference on 2026-11-08 after the frozen emitter, full cap-ten acceptance and literal manifest were reviewed; private/native comparison results are pending. The reference command below is its recorded launch, not an instruction to repeat it. Full cap-ten acceptance and its completed external guard must precede reference emission. The emitter rejects incomplete acceptance evidence.

The copied cap-ten build report is pinned to SHA256 `c58633b5467037d073733ef02b195696b9bc64f47aff46bac3daf23d46ac2e81`; its 181,505,965-byte candidate is `/mnt/shared-evaluation-20261107/native-cap10-build-v1/candidate/bazel-native-cap10`, SHA256 `8b9195a536c4718cad937bbe70045db958f50b2dd43efb7aa742f91e284cb5d0`. Source provenance explicitly separates base commit `4862608d940b49293c68c014e413ecc9dc98aa8e` / base Git tree `a28c113856ec48838041bba3b9591ab9ea67e4ac` / base archive SHA256 `a451b8353a29f92cb852785ff57ce5143b8a7ca37d561428926bee722353e18d`, the inherited installation-mismatch test repair, and the four-file cap-ten patch from commit `5c2d5bc56edbba9bdba7cc797658d2facb4d661e` (patch SHA256 `05eeccdc12ae4dfe08d350688dd4b4bc2701fe26a155cdda9f59faa422aa16c2`). The full product commit tree is **not** claimed to be the built tree. The emitter retains both patch records and verified final source-content identity.

Every arm uses six CPU quota, 10 GiB memory, no scope swap, `ActiveProcessorCount=2`, and 500 ms PSS cadence. Private engines each receive 768 MiB maximum heap; the native JVM receives 7,680 MiB. Preserve the frozen five-file measurement helper snapshot used at N2. Each arm has fresh output state, one serial preparation and two identity calls per view, verified shutdown, then ten measured builds. No identity call belongs between the restart boundary and those builds. Keep the existing 1,800-second per-build timeout and 7,200-second outer guard deadline; timeout or resource abort preserves evidence and stops this sequence, with no automatic retry or budget increase.

The unchanged C-archived M0 N10 reference supplies the Datadog projection/tree/source/BUILD identities; the archived 1,458-member download seed supplies exact seed paths and digests. The runner revalidates those explicit inputs at use. A new private N10 reference is mandatory; N2 oracles cannot satisfy its ten-view contract. All static source, helper, input and resource choices are fixed before performance results. Comparison manifests are finalized only after the independent correctness reference supplies its report and final guard hashes.

This is one diagnostic comparison of the native implementation bundle. It is not the five-pair acceptance protocol, observer qualification, full admission/drain boundary, typed-sharing ceiling, or a performance/milestone verdict. Isolated SkyCache remains the later economic denominator. Private graphs, action policy differences, sampled peaks and scheduling/pressure effects retain their existing interpretation.

## 1. Emit the reference from completed C evidence

Use PowerShell 7. The candidate build and full acceptance must already be archived on C. These commands only emit C files; they do not launch a process in R. Fresh outputs are required.

```powershell
$n10Work = 'C:\Users\jgett\Documents\Codex\2026-11-07\i-ha\work'
$n10E = Join-Path $n10Work 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07'
$n10Series = 'native-n10-probe01'
$n10Ref = Join-Path $n10Work "$n10Series-reference"
$n10Private = Join-Path $n10Work "$n10Series-private"
$n10Native = Join-Path $n10Work "$n10Series-native"
$n10Args = @('--series', $n10Series,
  '--source-repository', '/mnt/c/Users/jgett/datadog-agent',
  '--candidate-report', "$n10E/evidence/auxiliary/native-cap10-build-01/report.json",
  '--candidate-guard-result', "$n10E/evidence/auxiliary/native-cap10-build-01/guard/result.json",
  '--acceptance-directory', "$n10E/evidence/auxiliary/native-acceptance-cap10-01")
python -B "$n10E/harness/prepare_native_n10.py" --role reference @n10Args --output $n10Ref
if ($LASTEXITCODE -ne 0) { throw 'N10 reference preparation rejected; launch nothing.' }
```

Review the emitted `manifest.json`, `command.json`, `stage.json` and `harness/` snapshot. Acceptance binding requires all 21 named checks, the same candidate report/binary, cap ten, the 7,680-MiB/2-processor policy, ten distinct output bases, matching coordinator/guard/command/archive hashes, and explicit unsignalled final scope/daemon absence. Base-source and patch fields remain visible in every arm manifest.

The literal command names a future manifest path, so its bytes can be hashed before encoding that hash inside the manifest. No manifest hash appears in its own command. `host_preflight.path` names the exact future `host-budget-preflight.json` sibling of this arm's `guard/`; no predeclared preflight hash is required. The external wrapper generates it after freezing command bytes; the runner validates and hashes the same bytes at runtime. Never modify a manifest after staging or launch.

## 2. Stage and run the independent reference

This explicit staging function only creates the fresh series/config files and copies the frozen helpers. It never starts a build or deletes state. An incomplete stage remains for review; do not repair or reuse its root automatically.

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

$n10Archiver = '/mnt/c/' + (Join-Path $n10Work 'archive_native_n10.py').Substring(3).Replace('\', '/')
Stage-NativeArm $n10Ref
pwsh -NoProfile -File "$n10E/harness/run_auxiliary.ps1" `
  -CommandJson "$n10Ref/command.json" -RunId "$n10Series-ref" -TimeoutSeconds 7200
if ($LASTEXITCODE -ne 0) { throw 'N10 reference/guard failed; preserve evidence and stop.' }
wsl.exe -d Ubuntu --exec python3 -B $n10Archiver ref
if ($LASTEXITCODE -ne 0) { throw 'N10 reference archival verification failed; stop.' }
```

The wrapper owns the exclusive auxiliary lease, host preflight, six-CPU/10-GiB/no-swap scope and final cleanup. Require its completed control record and guard exit0 with `verified: true`, `present: false`, `signalled: false`, plus runner `passed: true`, `owned_daemons_absent` and `source_and_binary_unchanged`. The separate archive helper reads only the finished owned run and writes preserved C evidence, including `report.json`. Failed runs still require archival review; the success sequence above deliberately stops rather than silently advancing.

## 3. Emit both comparison manifests, then run private followed by native

The reference archive and final external guard must be complete before this step. Emitting both manifests now fixes their inputs before either comparison result. No role, lifecycle or configuration is selected from those results.

```powershell
$n10ReferenceReport = "$n10E/evidence/auxiliary/$n10Series-ref/report.json"
$n10ReferenceGuard = "$n10E/evidence/auxiliary/$n10Series-ref/guard/result.json"
foreach ($role in @('private', 'native')) {
    $n10Output = Join-Path $n10Work "$n10Series-$role"
    python -B "$n10E/harness/prepare_native_n10.py" --role $role @n10Args `
      --harness-source "$n10Ref/harness" --reference-report $n10ReferenceReport `
      --reference-guard-result $n10ReferenceGuard --reference-manifest "$n10Ref/manifest.json" --output $n10Output
    if ($LASTEXITCODE -ne 0) { throw "$role N10 manifest rejected; launch no comparison." }
}

Stage-NativeArm $n10Private
pwsh -NoProfile -File "$n10E/harness/run_auxiliary.ps1" `
  -CommandJson "$n10Private/command.json" -RunId "$n10Series-private" -TimeoutSeconds 7200
if ($LASTEXITCODE -ne 0) { throw 'N10 private/guard failed; preserve evidence and stop.' }
wsl.exe -d Ubuntu --exec python3 -B $n10Archiver private
if ($LASTEXITCODE -ne 0) { throw 'N10 private archival verification failed; stop.' }

Stage-NativeArm $n10Native
pwsh -NoProfile -File "$n10E/harness/run_auxiliary.ps1" `
  -CommandJson "$n10Native/command.json" -RunId "$n10Series-native" -TimeoutSeconds 7200
if ($LASTEXITCODE -ne 0) { throw 'N10 native/guard failed; preserve evidence and stop.' }
wsl.exe -d Ubuntu --exec python3 -B $n10Archiver native
if ($LASTEXITCODE -ne 0) { throw 'N10 native archival verification failed; stop.' }
```

Preserve all three reports and raw archives. Verify ten distinct workspaces/output views, unique BEP UUIDs, positive common overlap, per-index PID bindings, unchanged inputs/binary, and independent output/behavior oracles. Native must have one process identity mapped to ten engines; private must have ten identities. Do not pass a native `--output_base`. Report unique-process PSS coverage, process/harness CPU bounds, raw scope CPU, pressure/reclaim/throttling, per-client and cohort latency, preparation/lifecycle cost and action execution evidence separately. Non-harness CPU remains broader than analysis CPU; subtraction does not remove observer perturbation. Stop after these three arms for the recorded investment decision.
