from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,shutil,tarfile
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
R=Path('/home/jgett/REPOS/.work/datadog-clean-2026-10-25')
D=Path('/home/jgett/REPOS/bazel-skyframe-findings')
E=D/'docs/jsgette/measurements/skycache-v2-datadog-clean-2026-10-25'
B=Path('/mnt/d/Codex/wsl-reset-2026-10-25')
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,o):p.write_text(json.dumps(o,indent=2)+'\n')
p=json.loads((R/'results.json').read_text());s=json.loads((R/'summary.json').read_text())
assert p['status']==s['status']=='passed' and s['build_count']==6
E.mkdir(parents=True,exist_ok=False)
for name in ['datadog-clean-benchmark.py','analyze-datadog-clean.py','publish-datadog-clean.py','inspect-skycache-manifests.py','diagnose-clean-snapshot.py','inspect-clean-profiles.py','finalize-clean-protocol.py']:
 shutil.copy2(T/'work'/name,E/name)
for name in ['harness.py','harness-initial.py','summary.json','workspace.patch','manifest-comparison-before-r2.json','manifest-validation-corrected.json','snapshot-diagnosis.json','profile-observations.json']:shutil.copy2(R/name,E/name)
assert sha(R/'harness.py')==sha(T/'work/datadog-clean-benchmark.py')
pub=json.loads(json.dumps(p))
for r in pub['runs']:
 r['build_metrics']={k:v for k,v in r['build_metrics'].items() if k in ['timingMetrics','memoryMetrics','actionSummary','buildGraphMetrics','cumulativeMetrics']}
save(E/'results.json',pub)
for r in p['runs']:
 for suffix in ['-clean.log','-graph-after-clean.log']:shutil.copy2(R/(r['label']+suffix),E/(r['label']+suffix))
 samples=json.loads((R/(r['label']+'.samples.json')).read_text())
 (E/(r['label']+'.samples.jsonl')).write_text(''.join(json.dumps(x,separators=(',',':'))+'\n' for x in samples))
save(E/'final-windows-storage.json',json.loads((T/'work/datadog-storage-live.json').read_text(encoding='utf-8-sig')))
files=sorted(q for q in R.iterdir() if q.is_file())
save(E/'raw-evidence-manifest.json',{'root':str(R),'files':[{'name':q.name,'bytes':q.stat().st_size,'sha256':sha(q)} for q in files]})
archive=B/'datadog-clean-benchmark.tar';assert not archive.exists()
with tarfile.open(archive,'w') as a:
 for q in files:a.add(q,arcname=q.name)
with tarfile.open(archive) as a:
 for member in a.getmembers():
  with a.extractfile(member) as f:assert hashlib.file_digest(f,'sha256').hexdigest()==sha(R/member.name)
save(E/'private-backup.json',{'path':str(archive),'bytes':archive.stat().st_size,'sha256':sha(archive),'files':len(files),'verified':True})
table=[]
for key,title,unit in [('wall_seconds','Build after clean','s'),('analysis_seconds','Analysis phase (BEP)','s'),('execution_seconds','Execution phase (BEP)','s'),('clean_seconds','Clean, including JVM startup','s'),('clean_plus_build_seconds','Clean + build','s'),('peak_pss_mib','Peak process PSS','MiB'),('peak_rss_mib','Peak process RSS','MiB')]:
 vals=[]
 for v in ['off','on']:
  a=s['groups'][v][key];vals.append(f"{a['median']:.2f} ({a['min']:.2f}–{a['max']:.2f}) {unit}")
 table.append(f"| {title} | {vals[0]} | {vals[1]} |")
trials=[]
for r in p['runs']:
 remote=sum(x['count'] for x in r['runner_counts'] if x['name']=='remote cache hit')
 local=sum(x['count'] for x in r['runner_counts'] if x.get('execKind')=='Local')
 trials.append(f"| {r['label']} | {r['clean']['seconds']:.2f} | {r['seconds']:.2f} | {float(r['build_metrics']['timingMetrics']['analysisPhaseTimeInMs'])/1000:.3f} | {r['peak_tree_pss_kib']/1024:.0f} | {r['analysis_hits']} | {remote} | {local} |")
change=s['on_vs_off_percent']['wall_seconds'];analysis=s['on_vs_off_percent']['analysis_seconds'];mem=s['on_vs_off_percent']['peak_pss_mib']
restored=s['restored_snapshot_pairs'];restored_table=[]
for key,title,unit in [('wall_seconds','Build after clean','s'),('analysis_seconds','Analysis phase','s'),('peak_pss_mib','Peak process PSS','MiB')]:
 vals=[]
 for v in ['off','on']:
  a=restored['groups'][v][key];vals.append(f"{a['median']:.2f} ({a['min']:.2f}–{a['max']:.2f}) {unit}")
 restored_table.append(f"| {title} | {vals[0]} | {vals[1]} |")
body=f'''# Datadog builds after `bazel clean` — 25 October 2026

This experiment answers the explicit request to compare SkyCache with no SkyCache after an actual `bazel clean`. It uses the same experimental binary with the feature disabled/enabled, the same Datadog workspace, output base, machine, resource limits and Cache & Carry endpoint. It does not compare the earlier warm no-op measurements.

Across three runs per condition, enabling SkyCache changed median post-clean build time by {change:+.1f}%, analysis phase elapsed time by {analysis:+.1f}%, and peak process PSS by {mem:+.1f}%. The table gives medians and full observed ranges; three samples do not establish statistical significance, particularly for remote-transfer time.

The first SkyCache run rejected an older snapshot because Python toolchain symlink referents were missing. It published a replacement; the subsequent runs restored 4,501 configured targets each. Both cases are retained below. The prior **4.56-second** result was a warm build and does not measure this clean-build scenario.

| Measurement | SkyCache off | SkyCache on |
|---|---:|---:|
{chr(10).join(table)}

## What was cleaned and verified

Before **each** timed build, the harness ran synchronous `bazel clean --noasync --noexpunge`. After clean, every run verified:

- `bazel dump --skyframe=summary` reported **zero nodes and zero edges**.
- The `execroot` had no children, including no previous binary or generated outputs.
- The local `action_cache` directory was absent.
- All persisted SkyCache publication hashes were unchanged. Its store is outside the output base, so standard clean preserves it.

The clean and graph-dump logs are included in the public evidence. The code paths examined were `CleanCommand.actuallyClean` (calls `clearCaches`, `resetEvaluator`, and removes execroot contents) and `BlazeWorkspace.clearCaches` (clears both in-memory and on-disk action caches).

Each run shut down the previous server before clean, which started a fresh JVM. The subsequent build used that **same PID**. This equalizes JVM history between conditions and ensures no retained in-process graph or SkyCache helper state crosses runs. It is a post-clean comparison with fresh JVM history, not a measurement of clean inside an already JIT-warmed developer JVM. Clean/startup time is separate from build time; graph verification and shutdown are not included in either timer.

## Runs and cache use

The order was off/on, on/off, off/on. All six builds succeeded and produced the same binary. Cache & Carry was enabled throughout at `grpc://10.55.0.1:8080`; no disk action cache was configured. Unlike the earlier forced-cache experiment, these commands did **not** use `--nouse_action_cache`: standard clean actually removed local action state and outputs before each run.

| Run | Clean s | Build s | Analysis s | Peak PSS MiB | SkyCache configured-target hits | Remote action hits | Local actions |
|---|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(trials)}

All six runs passed the remote-warning, output-equivalence, storage and unchanged-source checks. The analysis and execution timers come from Bazel's BEP. With interleaved analysis and execution they can overlap, so they should not be summed or treated as pure CPU time. End-to-end build time includes the client and synchronous completion work.

## Persisted snapshot behavior

The initial manifest and its replacement have identical project namespaces and source digests. Their 900 witness differences all involve the Python toolchain's `__pycache__` paths. For the first changed symlink, the old fingerprint exactly matches an existing directory referent; its current target under `/tmp/rules_python_pycache/` is absent, and the current fingerprint matches the replacement manifest. The read-only decoder verifies manifest checksums and mirrors `LocalInputSnapshot` fingerprinting. It reproduced the old snapshot's first mismatching witness and verified all 142,366 replacement witnesses. `LocalCacheSession.matchesForAdmission` rejects a publication when a global witness differs. An initial decoder-only root-relative-path normalization error was corrected; its output remains in the private raw evidence and is identified in the protocol.

The deletion event itself was not captured, so this evidence does not attribute the missing temporary directories to clean. Clean preserved the persisted publication bytes. Subsequent clean builds restored from the replacement without changing the implementation, cache settings, repository sources, or missing Python symlink targets. The old publication remained present and was rejected while the replacement was admitted.

The first attempt's harness incorrectly asserted that every enabled build must have positive analysis hits, and stopped after the successful `r1-on` build. The protocol was corrected to accept a cache miss as an observed outcome; all other validation and build commands remained unchanged. Original harness/results, the failure, and every completed trial were preserved. The remaining four builds resumed in their planned order.

For a secondary comparison restricted to the paired rounds with successful snapshot restoration ({', '.join(map(str,restored['rounds']))}; {len(restored['rounds'])} samples per condition), the medians and ranges are:

| Measurement | SkyCache off | SkyCache on |
|---|---:|---:|
{chr(10).join(restored_table)}

This subset is selected by observed cache behavior and is descriptive; it does not replace the complete three-pair comparison. Each restored run reads 163,219 dependency records and 336,656,848 bytes from the local analysis store. Those are local SkyCache reads, separate from Cache & Carry action hits. Fewer newly configured targets did not translate to a faster observed analysis phase. The counters do not isolate the CPU cost of validation versus deserialization.

OS page cache and WSL runtime history were not controlled. The observed WSL boot preceded resumed `r2-on` by approximately 56 seconds. Its analysis took 21.511 seconds, versus 5.319 seconds in `r3-on`; the profile's `LocalSkycacheModule.beforeCommand` span likewise fell from 4.021 to 0.393 seconds. The last paired comparison was 144.081 seconds off versus 146.913 seconds on, a much smaller difference than the aggregate median. This variability limits attribution of the full median difference to the feature. Even that final restored build showed no speedup and had higher peak PSS (5,203 versus 2,518 MiB).

## Reproduction

- Datadog revision `{p['source_revision']}`, target `{p['target']}`.
- SkyCache revision `{p['skycache_revision']}`. Both conditions use binary SHA-256 `{p['binary_sha256']}`; the changed flag is `--experimental_local_skycache` (empty versus the persisted store). Shared analysis-related options are unchanged.
- Local SkyCache was primed by the [preceding fresh-WSL experiment](skycache-v2-fresh-wsl-2026-10-25.md). Initial publication hashes, exact command arrays and per-run clean checks are recorded in `results.json`.
- Ubuntu on the E: WD Blue SN570 NVMe SSD; AMD Ryzen 9 9950X3D, 32 logical CPUs. Same JDK 25, 6 GiB heap limit, 16 action jobs and 6,144 MiB action-memory budget.
- `--remote_timeout=600`, `--remote_retries=1`, `--remote_download_outputs=all`, remote uploads enabled and no remote-error fallback. Analysis idle retention and SkyCache idle garbage collection are disabled.
- MODULE lockfile SHA-256 `{p['module_lock_sha256']}`; `--lockfile_mode=error`; no source changes during measurement.
- Output SHA-256 `{p['expected_output_sha256']}` for all six builds, also matching the preceding experiment.
- Process-tree PSS/RSS is sampled with a 100 ms wait between readings; actual timestamps are included. Memory excludes filesystem page cache and Windows/WSL host overhead. Existing external-repository downloads are retained; neither Cache & Carry nor the persisted analysis store is cleared. OS cache state, other Windows activity and network transfer variability are uncontrolled.

Public [evidence and scripts](measurements/skycache-v2-datadog-clean-2026-10-25/) include cleanup proofs, filtered BEP metrics, memory samples and raw-file hashes. Complete logs, BEP and profiles remain at `{R}` and in the verified private archive `{archive}`. Raw BEP client environment values are not published.
'''
report=D/'docs/jsgette/skycache-v2-datadog-clean-2026-10-25.md';report.write_text(body)
(T/'outputs/Datadog-after-clean-benchmark.md').write_text(body)
(B/'Datadog-after-clean-benchmark.md').write_text(body)
previous=D/'docs/jsgette/skycache-v2-fresh-wsl-2026-10-25.md'
previous.write_text(previous.read_text().rstrip()+'\n\n## Actual clean comparison\n\nA separate [six-build comparison after verified `bazel clean`](skycache-v2-datadog-clean-2026-10-25.md) tests SkyCache enabled versus disabled using the same binary, with zero graph nodes and empty local outputs before every build. The warm-build figures above do not describe that scenario.\n')
(E/'.gitattributes').write_text('* -text\n*.log -whitespace\n')
(E/'SHA256SUMS').write_text(''.join(sha(q)+'  '+str(q.relative_to(E))+'\n' for q in sorted(E.iterdir()) if q.is_file() and q.name!='SHA256SUMS'))
print(json.dumps({'report':str(report),'summary':s['on_vs_off_percent'],'private_archive_bytes':archive.stat().st_size},indent=2))
