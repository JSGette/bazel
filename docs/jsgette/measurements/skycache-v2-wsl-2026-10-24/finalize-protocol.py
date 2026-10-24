from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import shutil
import subprocess

source = Path('/home/jgett/REPOS/bazel-skycache-v2')
docs_repo = Path('/home/jgett/REPOS/bazel-skyframe-findings')
docs = docs_repo / 'docs/jsgette'
evidence = docs / 'measurements/skycache-v2-wsl-2026-10-24'
run = Path('/mnt/skycache-v2-local')
work = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex/work')
base = '6f451b2dc6f783dda879ff3c7ed3fc8146d0f410'

def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, text=True, capture_output=True).stdout.strip()

report = json.loads((run / 'protocol.json').read_text())
assert report['status'] == 'local_validation_complete'
assert all(s['status'] == 'passed' for s in report['stages'])
assert git(source, 'rev-parse', 'HEAD') == base
assert git(source, 'branch', '--show-current') == 'jsgette/skycache-v2-local'
allowed = set('''src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/BUILD
src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/AnalysisCacheIdleRetentionTask.java
src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalCacheSession.java
src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalDependencyValidator.java
src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalSkycacheModule.java
src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalSkycacheOptions.java
src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/BUILD
src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/AnalysisCacheIdleRetentionTaskTest.java
src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalCacheSessionTest.java
src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalDependencyValidatorTest.java
src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/ParallelDependencyValidationBenchmark.java
src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/ParallelPackedStoreBenchmark.java
src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/SerialDependencyValidatorBenchmarkBaseline.java
tools/skycache/PROTOTYPE_BENCHMARKS.md
tools/skycache/ProofLayoutBenchmark.java
tools/skycache/idle_import_probe.py
tools/skycache/idle_retention_benchmark.py
tools/skycache/idle_retention_probe.py
tools/skycache/local_worktree_probe.py'''.splitlines())
changed = set(git(source, 'diff', '--name-only').splitlines()) | set(git(source, 'ls-files', '--others', '--exclude-standard').splitlines())
assert changed == allowed, {'unexpected': sorted(changed-allowed), 'missing': sorted(allowed-changed)}
git(source, 'diff', '--check')
git(source, 'add', '--', *sorted(allowed))
git(source, 'diff', '--cached', '--check')
print(git(source, 'commit', '-m', 'feat(skycache): add parallel validation and idle graph retention',
          '-m', 'Restore the cloud prototype and add explicit WSL run-volume, JDK and mixed-version probe options. Verified locally with 190 test cases, 63 fixture builds and repeated validator/store benchmarks.'))
commit = git(source, 'rev-parse', 'HEAD')
assert not git(source, 'status', '--porcelain')
patch = subprocess.check_output(['git', '-C', str(source), 'diff', '--binary', base, commit])
(evidence / 'implementation-current.patch').write_bytes(patch)
report['implementation_commit'] = commit
report['implementation_patch_sha256'] = hashlib.sha256(patch).hexdigest()
report['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
report['documentation_publication_target'] = 'https://github.com/JSGette/bazel/tree/jsgette/master/docs/jsgette'
report['hardware'] = json.loads((work/'hardware.json').read_text(encoding='utf-8-sig'))
report['setup'].append({'action':'Verify Resume-Skycache.ps1 against the mounted task volume', 'status':'passed_after_fix',
                        'details':'resume-helper-checks.json preserves the initial PowerShell CRLF boundary failure and successful correction'})
report['setup'].append({'action':'Aggregate results', 'status':'passed_after_setup_retries',
                        'details':'aggregation-checks.json preserves premature aggregation rejection and the missing-mount retry; no incomplete result was promoted'})
(run/'protocol.json').write_text(json.dumps(report,indent=2)+'\n')
shutil.copyfile(run/'protocol.json', evidence/'protocol.json')
aggregation=json.loads((evidence/'aggregation-checks.json').read_text())
aggregation['attempts'].extend([
    {'status':'setup_failed','exit_code':1,'reason':'After the last benchmark process exited, /mnt/skycache-v2-local was no longer mounted. The ext4 image and copied evidence remained intact. Cause of the mount loss was not established.'},
    {'status':'passed','exit_code':0,'correction':'Remounted the existing image with Resume-Skycache.ps1 and kept a WSL foreground sleep process alive while finalizing. Rechecked all expected stages and sample counts.'}])
(evidence/'aggregation-checks.json').write_text(json.dumps(aggregation,indent=2)+'\n')
for name in ('run-validation.py','summarize-validation.py','finalize-protocol.py'):
    shutil.copyfile(work/name,evidence/name)

document=docs/'skycache-v2-wsl-continuation-2026-10-24.md'
text=document.read_text()
text=text.replace('## Source and evidence\n', f'## Source and evidence\n\nLocal implementation commit: `{commit}`. The complete current source diff is\n[archived with the evidence](measurements/skycache-v2-wsl-2026-10-24/implementation-current.patch).\n')
text=text.replace('## Local results\n', '## Local results\n\nHost: AMD Ryzen 9 9950X3D, 16 cores / 32 logical processors; WSL had 15 GiB\nRAM and 4 GiB swap. The run image is on the WDC WD BLACK 512 GB NVMe C: drive.\n')
text=text.replace('Every measured validator round read exactly', 'Idle eviction released a median 130.8 MiB of heap in the SkyCache cycles.\nThe separately sampled median PSS drop was 292.9 MiB; these are fixture\nobservations and are not guaranteed OS-memory reductions.\n\nEvery measured validator round read exactly')
text=text.replace('The old/new worktree probe used', '''Packed reads scaled more strongly than durable writes on this volume:

| Workload | Threads | Durable write median, JVM 1 / 2 (ms) | Read median, JVM 1 / 2 (ms) |
| --- | ---: | ---: | ---: |
| 20,000 × 256 B | 1 | 236.29 / 246.05 | 13.89 / 13.07 |
| 20,000 × 256 B | 4 | 226.60 / 241.84 | 5.36 / 5.54 |
| 8,000 × 4 KiB | 1 | 1087.65 / 1079.45 | 22.66 / 22.64 |
| 8,000 × 4 KiB | 4 | 1075.20 / 1059.42 | 7.67 / 7.53 |

Durable-write gains were small; these measurements do not reproduce the cloud
write scaling on the local loop-backed storage path. Retain this negative result
alongside the read and validator improvements.

The old/new worktree probe used''')
text=text.replace('## Remaining scope and interpretation\n', '''## Report-generation checks and resuming

The evidence preserves two aggregation setup failures: an early attempt rejected
an unfinished packed-store sample file, and a later attempt found the temporary
mount absent after the benchmark processes ended. No partial summary was
published. The existing volume was remounted, every stage/sample count was
rechecked, and aggregation passed. The mount-loss cause was not established.
The [aggregation checks](measurements/skycache-v2-wsl-2026-10-24/aggregation-checks.json)
and [resume-helper checks](measurements/skycache-v2-wsl-2026-10-24/resume-helper-checks.json)
include the failed attempts and corrections.

After a WSL restart, run the saved `Resume-Skycache.ps1` helper to verify/remount
the existing image. Add `-OpenShell` to open the source checkout in WSL. The
helper does not format or rebuild the volume. Its initial Windows CRLF pipe
issue was corrected and its already-mounted path was tested successfully.

## Remaining scope and interpretation
''')
document.write_text(text)
summary=docs/'skycache-implementation-summary.md'
with summary.open('a') as out:
    out.write('''

## WSL continuation, 2026-10-24

The cloud v2 handoff was restored and validated locally on branch
`jsgette/skycache-v2-local`. All 190 local SkyCache cases and 63 fixture builds
passed, including imported-only idle eviction, same-mtime content mutation,
concurrent worktrees and old/new binary sharing. Synthetic median recovery was
0.228 s with SkyCache versus 0.365 s without it. Eight-thread validator medians
were about 2.1× faster than the serial baseline; packed durable-write scaling was
small on the C:-backed ext4 loop volume. These do not establish a representative
real-build speedup.

See the [complete WSL protocol](skycache-v2-wsl-continuation-2026-10-24.md) for
commands, source/binary hashes, storage provenance, every setup failure, raw
samples and outstanding work. The original cloud handoff is archived separately
with its incomplete runs intact.
''')
manifest=[]
for path in sorted(evidence.rglob('*')):
    if path.is_file() and path.name != 'LOCAL_SHA256SUMS':
        manifest.append(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+str(path.relative_to(evidence)))
(evidence/'LOCAL_SHA256SUMS').write_text('\n'.join(manifest)+'\n')
git(docs_repo,'diff','--check')
print(json.dumps({'implementation_commit':commit,'evidence_files':len(manifest),'evidence_bytes':sum(p.stat().st_size for p in evidence.rglob('*') if p.is_file())},indent=2))
