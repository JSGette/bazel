"""Publish verified summaries/source patches; retain raw RPC/BEP evidence privately."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,shutil,subprocess,tarfile,xml.etree.ElementTree as ET
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
W=Path('/home/jgett/REPOS/.work')
S=Path('/home/jgett/REPOS/bazel-skycache-v2')
D=Path('/home/jgett/REPOS/bazel-skyframe-findings')
R=W/'datadog-execution-2026-10-25-attempt3'
BASE=W/'datadog-download-modes-2026-10-25'
DEV=W/'skycache-execution-2026-10-25'
E=D/'docs/jsgette/measurements/skycache-execution-metadata-2026-10-25'
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,o):p.write_text(json.dumps(o,indent=2)+'\n')
def git(*args):return subprocess.check_output(['git','-C',str(S),*args],text=True).strip()
p=json.loads((R/'results.json').read_text()); s=json.loads((R/'summary.json').read_text())
old=json.loads((BASE/'summary.json').read_text())
probe=json.loads((DEV/'probe-6/results.json').read_text())
assert p['status']==s['status']==probe['status']=='passed'
assert len(p['runs'])==20 and not git('status','--porcelain')
commit=git('rev-parse','HEAD');assert commit=='71521fabb3420707c39d73329fa9aa9622e8c382'
assert sha(DEV/'bin/skycache-bwob')==p['binary_sha256']['candidate']==probe['binary_sha256']
E.mkdir(parents=True,exist_ok=False)
for root,name in [(BASE,'analysis-only-baseline'),(R,'execution-metadata')]:
 dest=E/name;dest.mkdir()
 data=json.loads((root/'results.json').read_text())
 for row in data['runs']:
  row['build_metrics']={k:v for k,v in row.get('build_metrics',{}).items() if k in ['timingMetrics','memoryMetrics','actionSummary','buildGraphMetrics','cumulativeMetrics']}
 save(dest/'results.json',data)
 for q in root.iterdir():
  if q.is_file() and (q.name.startswith('harness') or q.name in ['summary.json','workspace.patch'] or q.name.endswith(('-clean.log','-graph-after-clean.log','.samples.json','.storage.json'))):
   shutil.copy2(q,dest/q.name)
for name in ['datadog-execution-benchmark.py','datadog-download-modes.py','datadog-clean-benchmark.py','summarize-download-modes.py','publish-execution-protocol.py']:
 shutil.copy2(T/'work'/name,E/name)
shutil.copy2(S/'tools/skycache/local_execution_probe.py',E/'local_execution_probe.py')
for name in ['build.log','build-2.log','build-3.log','build-final.log','build-rewind.log','build-rewind-2.log','build-portable.log','build-symlink.log','build-symlink-2.log']:
 shutil.copy2(DEV/name,E/name)
tests=E/'test-xml';tests.mkdir()
test_root=DEV/'build/execroot/_main/bazel-out/k8-fastbuild/testlogs/src/test/java/com/google/devtools/build/lib'
test_summary=[]
for q in sorted((test_root/'skyframe/serialization/local').glob('*/test.xml'))+[test_root/'remote/RemoteTests/test.xml']:
 shutil.copy2(q,tests/(q.parent.name+'.xml'))
 cases=list(ET.parse(q).getroot().iter('testcase'))
 counts={'tests':len(cases),**{k:sum(len(c.findall(tag)) for c in cases) for k,tag in [('failures','failure'),('errors','error'),('skipped','skipped')]}}
 assert counts['failures']==counts['errors']==0
 test_summary.append({'target':q.parent.name,**counts})
save(E/'test-summary.json',test_summary)
fixtures=E/'fixtures';fixtures.mkdir()
for name in ['probe-1','probe-2','probe-3','probe-4','probe-5','probe-6']:
 dest=fixtures/name;dest.mkdir()
 for q in (DEV/name).iterdir():
  if q.is_file() and q.suffix in ['.json','.log'] and not q.name.endswith('.bep.json'):shutil.copy2(q,dest/q.name)
 for q in (DEV/name/'workspace').iterdir():
  if q.is_file() and q.name in ['BUILD.bazel','rules.bzl','MODULE.bazel','source.txt']:shutil.copy2(q,dest/q.name)
failures=E/'datadog-preparation-failures';failures.mkdir()
for name in ['datadog-execution-2026-10-25','datadog-execution-2026-10-25-attempt2']:
 dest=failures/name;dest.mkdir()
 for q in (W/name).iterdir():
  if q.is_file() and (q.name in ['results.json','harness.py'] or q.suffix=='.log'):shutil.copy2(q,dest/q.name)
(E/'implementation-incremental.patch').write_text(subprocess.check_output(['git','-C',str(S),'diff','--binary','303a149',commit],text=True))
(E/'implementation-current.patch').write_text(subprocess.check_output(['git','-C',str(S),'diff','--binary','6f451b2',commit],text=True))
(E/'implementation-commit.patch').write_text(subprocess.check_output(['git','-C',str(S),'format-patch','-1','--stdout',commit],text=True))
save(E/'provenance.json',{'implementation_commit':commit,'source_tree':git('rev-parse','HEAD^{tree}'),
 'incremental_patch_base':'303a149a195bbd6ad770584bd34138ea749c3e9a','current_patch_base':'6f451b2dc6f783dda879ff3c7ed3fc8146d0f410',
 'binary_sha256':p['binary_sha256'],'datadog_revision':p['source_revision'],'output_sha256':p['expected_output_sha256'],
 'vanilla_source':'4cfc05f77ba91900a2e3c6ab135861498cb2a0bf',
 'vanilla_note':'Preserved source-built pre-SkyCache fork baseline, not the Bazel 9.3 release.',
 'published_at':datetime.now(timezone.utc).isoformat()})
save(E/'windows-storage.json',json.loads((T/'work/datadog-storage-live.json').read_text(encoding='utf-8-sig')))
# Archive only evidence, not output bases, installations or persistent caches.
raw=[]
for root in [BASE,R,W/'datadog-execution-2026-10-25',W/'datadog-execution-2026-10-25-attempt2']:
 raw.extend((q,root.name+'/'+q.name) for q in root.iterdir() if q.is_file())
raw.extend((q,'development/'+q.name) for q in DEV.iterdir() if q.is_file())
for root in sorted(DEV.glob('probe-*')):
 if root.is_dir():raw.extend((q,'development/'+root.name+'/'+q.name) for q in root.iterdir() if q.is_file())
manifest=[{'name':name,'bytes':q.stat().st_size,'sha256':sha(q)} for q,name in raw]
save(E/'raw-evidence-manifest.json',manifest)
archive=Path('/mnt/d/Codex/wsl-reset-2026-10-25/skycache-execution-metadata-evidence.tar')
assert not archive.exists()
with tarfile.open(archive,'w') as tar:
 for q,name in raw:tar.add(q,arcname=name)
expected={x['name']:x['sha256'] for x in manifest}
with tarfile.open(archive) as tar:
 assert len(tar.getmembers())==len(manifest)
 for member in tar.getmembers():
  with tar.extractfile(member) as f:assert hashlib.file_digest(f,'sha256').hexdigest()==expected[member.name]
backup=Path('/mnt/d/Codex/wsl-reset-2026-10-25/bazel-skycache-execution-metadata')
assert not backup.exists();shutil.copy2(DEV/'bin/skycache-bwob',backup);assert sha(backup)==p['binary_sha256']['candidate']
save(E/'private-backup.json',{'archive':str(archive),'bytes':archive.stat().st_size,'sha256':sha(archive),'files':len(raw),'verified':True,
 'candidate_binary':str(backup),'candidate_binary_sha256':sha(backup)})
rows=[]
for mode in ['minimal','toplevel']:
 for variant,title in [('vanilla','Vanilla fork'),('off','Candidate, SkyCache off'),('on','SkyCache execution metadata')]:
  g=s['groups'][mode+'-'+variant]
  rows.append(f"| {mode} | {title} | {g['wall_seconds']['median']:.2f} | {g['peak_pss_mib']['median']:.0f} | {g['get_action_result']['median']:.0f} | {g['cas_read_bytes']['median']/1024**2:.2f} |")
improvement={mode:{base:{metric:100*(s['groups'][mode+'-on'][metric]['median']/s['groups'][mode+'-'+base][metric]['median']-1) for metric in ['wall_seconds','peak_pss_mib']} for base in ['off','vanilla']} for mode in ['minimal','toplevel']}
save(E/'comparison.json',improvement)
prior=[]
for mode in ['minimal','toplevel']:
 for variant in ['off','on']:
  g=old['groups'][mode+'-'+variant]
  prior.append(f"| {mode} | {variant} | {g['wall_seconds']['median']:.2f} | {g['peak_pss_mib']['median']:.0f} | 5,704 |")
report=f'''# SkyCache execution metadata and build without the bytes — 25 October 2026

The implementation now reuses validated remote execution metadata before walking the generating action's dependencies. On Datadog, all six measured SkyCache builds made **zero `GetActionResult` requests**. `minimal` also made **zero CAS reads**. `toplevel` fetched five requested output/runfile blobs, including the final executable. Missing remote blobs are recovered only when requested, through Bazel's existing action rewind path.

Implementation: `{commit}` on the local `jsgette/skycache-v2-local` branch. This documentation branch carries the [incremental implementation patch](measurements/skycache-execution-metadata-2026-10-25/implementation-incremental.patch), [complete patch since the established implementation base](measurements/skycache-execution-metadata-2026-10-25/implementation-current.patch), tests, harnesses, measurements and preserved failures. It does not merge the experimental implementation branch into the documentation checkout.

## Repeated Datadog results

Each cell is the median of three measured builds. Time excludes the separately recorded clean/JVM startup. PSS is sampled memory of the build process tree, not host RAM or filesystem cache.

| Output policy | Binary / mode | Build seconds | Peak PSS MiB | GetActionResult | CAS read MiB |
|---|---|---:|---:|---:|---:|
{chr(10).join(rows)}

Against the same candidate with SkyCache disabled, median time changed by {improvement['minimal']['off']['wall_seconds']:+.1f}% for `minimal` and {improvement['toplevel']['off']['wall_seconds']:+.1f}% for `toplevel`. Peak PSS changed by {improvement['minimal']['off']['peak_pss_mib']:+.1f}% and {improvement['toplevel']['off']['peak_pss_mib']:+.1f}%, respectively. Against the preserved vanilla fork, median time changed by {improvement['minimal']['vanilla']['wall_seconds']:+.1f}% and {improvement['toplevel']['vanilla']['wall_seconds']:+.1f}%.

The vanilla comparator is the preserved source build at `4cfc05f77ba91900a2e3c6ab135861498cb2a0bf`, not the released Bazel 9.3 binary. The same-binary off/on comparison isolates this feature more closely. Three repetitions and a shared network cache do not establish statistical significance. Full ranges and individual observations are in [summary.json](measurements/skycache-execution-metadata-2026-10-25/execution-metadata/summary.json).

Every measured build used Cache & Carry at `grpc://10.55.0.1:8080`, the same Datadog workspace/output base, JDK 25, 6 GiB JVM heap, 16 jobs and 6144 MiB local action-memory budget. Disk action caching was disabled. Each build was preceded by shutdown and synchronous `bazel clean --noasync --noexpunge`; the harness verified **zero Skyframe nodes/edges**, empty execroot, absent local action cache, unchanged persistent SkyCache publications, and unchanged JVM PID between clean and build. Clean started the server; its graph was empty. One Python process kept WSL running across the entire sequence, so the boot ID was stable. OS page cache and external repository downloads were warm. Source-build and fixture servers were shut down before timing.

The sequence contains two preparation builds plus 18 measured builds, rotating three variants per policy. The first preparation populates execution metadata and is excluded from measured medians. Every build recorded gRPC traffic, BEP, a profile, output inventory and process-tree PSS/RSS samples at 100 ms. Resource/storage guards remained enabled. Source revision, lockfile hash, exact commands and binary hashes are retained in [results.json](measurements/skycache-execution-metadata-2026-10-25/execution-metadata/results.json).

## Output and network behavior

- SkyCache restored six execution values and 12 configured targets per measured hit build. Bazel ran six internal actions, compared with 5,704 remote action-cache hits and 1,085 internal actions with SkyCache disabled.
- `minimal`: no `GetActionResult`, no ByteStream reads, no final executable, and no intermediate `.a` or `.x` files. Four small internal files totaling 45,186 bytes are generated; minimal does not suppress Bazel's necessary internal metadata/manifests.
- `toplevel`: no `GetActionResult`; five ByteStream reads totaling 121,907,152 bytes for requested top-level outputs/runfiles. No intermediate `.a` or `.x` files. The final executable is 119,596,056 bytes and its SHA-256 is `{p['expected_output_sha256']}` in every variant.
- Both hit policies still make one `GetCapabilities` request when initializing the remote client. Zero action-result lookups does not mean zero contact with the remote service.
- Source/analysis dependency validation remains local and conservative. A hit build validates a compact dependency graph; it does not query every action result or check every referenced CAS blob. This local validation is still substantial work and is not eliminated by this change.

## Implementation and correctness

The local reader accepts execution-node envelopes (type 5) as well as analysis nodes (type 4), validating their existing owner dependency root including source witnesses. The backend's upload policy admits only remote-backed execution outputs. Local/internal outputs, rich action data and absolute resolved-symlink metadata stay on normal evaluation. The namespace includes remote cache/executor/instance identity; output download policy is deliberately excluded so minimal and toplevel can share metadata. `--noremote_accept_cached` disables this execution reuse.

The existing `ActionExecutionFunction`/`ArtifactFunction` retrieval paths return metadata before traversing the action closure. Existing rewind handling prevents retrieving the same stale value again. A missing-final-output regression exposed the rewind synchronizer's assumption that input owners were already loaded. The fallback now requests only the owners/template expansion needed by actions actually being executed, including discovered inputs and runfiles. Cached execution hits still bypass this work. This setup also covers a concurrent action triggering rewind after another action has begun its fallback.

Datadog then exposed symlink validation depending on a remote input's parent directories already existing. `RemoteActionFileSystem` now supplies those parents in memory for a symlink to known remote metadata, without downloading data or creating target directories on disk. Its regression test checks both metadata resolution and absence of materialization.

The [private HTTP-cache fixture](measurements/skycache-execution-metadata-2026-10-25/local_execution_probe.py) passed 12 clean builds on the exact measured candidate. It checks zero-request minimal hits, final-only toplevel fetch, symlinks to remote files with absent parents, absent unused intermediates, required missing intermediates, missing final output, remote acceptance disabled, internal output regeneration after clean, and source-change invalidation. Deleting the required intermediate caused a CAS 404 and successful regeneration with **zero action-cache GETs**. Deleting the final blob also recovered correctly. All deletions were confined to this fixture's in-memory cache; the shared Cache & Carry server was not modified for fault injection.

The 13 local-backend test targets passed; the final portability restriction has its focused regression test. The filtered remote-filesystem suite passed 154 executed cases with zero errors/failures. [Test XML](measurements/skycache-execution-metadata-2026-10-25/test-xml) and [test summary](measurements/skycache-execution-metadata-2026-10-25/test-summary.json) preserve the exact outcomes. This verifies the exercised paths; it is not a complete qualification of all Bazel rule types or concurrent distributed-cache failure scenarios.

## Baseline and preserved development failures

Before this change, a separate 14-build series (two preparations, 12 measured builds) verified that output policy alone did not remove action traversal. Both policies still made 5,704 `GetActionResult` calls per build:

| Policy | Analysis-only SkyCache | Build seconds | Peak PSS MiB | GetActionResult |
|---|---|---:|---:|---:|
{chr(10).join(prior)}

This baseline is a separate series, so its cross-series timing comparison is descriptive. It downloaded 15,304,814 CAS bytes in minimal and 137,211,966 in toplevel, including ancillary cache data even when outputs were not materialized. [Baseline evidence](measurements/skycache-execution-metadata-2026-10-25/analysis-only-baseline/results.json) preserves all observations.

Development failures were retained, not counted as successful measurements: an incorrect BUILD dependency name; a test using an inaccessible artifact constructor; fixture shell-string quoting; the missing-owner rewind crash; a failed attempt to overwrite a read-only copied binary followed by an accidental old-binary rerun; Datadog publication rejection of absolute generated symlink metadata; Datadog symlink validation with absent input parents; and an initially incorrect remote-test target label. The corrective changes and reruns are in the build logs, fixture directories and Datadog preparation-failure evidence. The final series used a new cache directory and a distinct immutable binary path.

## Evidence retention

Public evidence includes source patches, commands, measurements, summarized RPC counts, samples, test XML and checksums. Raw gRPC/BEP/profile files remain local because RPC metadata can contain headers. The raw archive on D: and preserved candidate binary were verified by SHA-256; [private-backup.json](measurements/skycache-execution-metadata-2026-10-25/private-backup.json) records their paths. [SHA256SUMS](measurements/skycache-execution-metadata-2026-10-25/SHA256SUMS) covers every public evidence file.
'''
report_path=D/'docs/jsgette/skycache-execution-metadata-2026-10-25.md';report_path.write_text(report)
(T/'outputs/SkyCache-execution-metadata.md').write_text(report)
files=sorted(q for q in E.rglob('*') if q.is_file())
(E/'SHA256SUMS').write_text(''.join(sha(q)+'  '+str(q.relative_to(E))+'\n' for q in files))
print(json.dumps({'report':str(report_path),'evidence_files':len(files)+1,'comparison':improvement,'tests':test_summary,'raw_archive':str(archive)},indent=2))
