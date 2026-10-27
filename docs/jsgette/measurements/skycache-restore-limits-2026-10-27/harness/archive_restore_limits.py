"""Archive compact regression evidence once validation has stopped."""
from pathlib import Path
import copy, gzip, hashlib, json, shutil

work=Path(__file__).parent
task=Path('/home/jgett/REPOS/.work/skycache-restore-limits-2026-10-27')
repo=Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
dest=repo/'docs/jsgette/measurements/skycache-restore-limits-2026-10-27'
sha=lambda p:hashlib.file_digest(Path(p).open('rb'),'sha256').hexdigest()
read=lambda p:json.loads(Path(p).read_text())
build=read(task/'build/result.json')
publish=read(task/'validation/remote-publish-results.json')
restore=read(task/'validation/fresh-restore-results.json')
templates=read(task/'templates/templates-results.json')
assert build['exit_code']==0 and build['cases']==821
assert publish['status']=='passed' and publish['runs'][0]['remote_committed']
assert restore['runs'][0]['exit_code']==33
assert templates['status']=='passed' and len(templates['runs'])==2
assert all(r['exit_code']==0 and r['outputs_match'] and r['targets_match'] and r['analysis_hits']>0 and r['execution_hits']>0 for r in templates['runs'])
assert all(d[k] for d in [publish,restore,templates] for k in ['source_unchanged','fixture_unchanged','binary_hashes_unchanged'])
dest.mkdir(exist_ok=False)
raw=[]
def compress(path,data):
    path.write_bytes(gzip.compress(data,mtime=0))
def sanitize(d):
    d=copy.deepcopy(d)
    for name in ['source','fixture']:
        if name in d:
            diff=d[name].pop('diff','')
            d[name]['diff_sha256']=hashlib.sha256(diff.encode()).hexdigest()
    for r in d.get('runs',[]):
        if 'successful_targets' in r:r['successful_target_count']=len(r.pop('successful_targets'))
        if 'argv' in r:
            r['argv']=[a.split('=',1)[0]+'=<redacted>' if any(w in a.lower() for w in ['password','token=','authorization','api-key','api_key','remote_header=','bes_header=']) else a for a in r['argv']]
    return d
for phase in ['red','build']:
    folder=dest/phase;folder.mkdir()
    for name in ['result.json','command.json']:shutil.copy2(task/phase/name,folder/name)
    compress(folder/'build.log.gz',(task/phase/'build.log').read_bytes())
    for p in (task/phase/'tests').glob('*.xml'):compress(folder/(p.name+'.gz'),p.read_bytes())
for cohort in ['validation','templates']:
    folder=dest/cohort;folder.mkdir()
    for p in (task/cohort).glob('*-results.json'):
        d=read(p)
        compress(folder/(p.name+'.gz'),(json.dumps(sanitize(d),indent=2)+'\n').encode())
        raw.append(dict(path=str(p),sha256=sha(p),bytes=p.stat().st_size))
        for r in d.get('runs',[]):
            for suffix in ['.log','-timeline.jsonl','-empty-graph.log','-final-graph.log']:
                f=p.parent/(r['label']+suffix)
                if f.exists():compress(folder/(f.name+'.gz'),f.read_bytes())
            for suffix in ['.profile.gz','.bep.json','-outputs.json']:
                f=p.parent/(r['label']+suffix)
                if f.exists():raw.append(dict(path=str(f),sha256=sha(f),bytes=f.stat().st_size))
    for name in ['fresh-local-seed-provenance.json','installation-identity.json','harness-path-correction.json',
                 'fresh-restore-oom.json','workload.json','templates-remote-cold-worker-warmup.json']:
        if (task/cohort/name).exists():shutil.copy2(task/cohort/name,folder/name)
    for name in ['fresh-restore-oom-jvm-tail.log','fresh-restore-thread-dump.txt','expected-outputs.json','expected-targets.json','validation-provenance.json']:
        if (task/cohort/name).exists():compress(folder/(name+'.gz'),(task/cohort/name).read_bytes())
(dest/'harness').mkdir()
for name in ['build_restore_limits.py','prepare_restore_limits_validation.py','promote_restore_seed.py',
             'capture_restore_heap.py','validate_restored_templates.py','archive_restore_limits.py']:
    shutil.copy2(work/name,dest/'harness'/name)
shutil.copytree(work/'restore_limits_validation',dest/'harness/restore_limits_validation',ignore=shutil.ignore_patterns('__pycache__'))
summary=dict(source_head=publish['source']['head'].strip(),native_root=str(task),tests=build,
    full_graph_publication={k:publish['runs'][0].get(k) for k in ['exit_code','configured_targets','output_files','outputs_match','targets_match','remote_committed','cache_storage','cache_errors']},
    local_full_graph_failure=read(task/'validation/fresh-restore-oom.json'),
    template_checks=[{k:r.get(k) for k in ['arm','exit_code','analysis_hits','execution_hits','outputs_match','targets_match','output_files','empty_graph_verified','remote-skycache']} for r in templates['runs']],
    raw_recordings=raw,
    notes=['Single correctness checks, not a new performance matrix.',
           'The failed full local restore has incomplete BEP summaries; 89,515 hits come from live progress, not final counters.',
           'Full local diagnostic includes a thread snapshot. Its duration is not a benchmark sample.',
           'No installation checksum override, fixture source changes, or external cache writes.',
           'Raw CAS data, profiles, BEP files, output manifests and native binaries remain outside Git.'])
(dest/'verification.json').write_text(json.dumps(summary,indent=2)+'\n')
remote=templates['runs'][1]['remote-skycache']
readme=f'''# SkyCache generated-input restoration and 8 GiB limit — 2026-10-27

The missing generated-template inputs are fixed. The client and worker now permit an
8 GiB logical publication and an opt-in transfer budget through 8 GiB + 1 MiB
(`--experimental_remote_skycache_max_transfer_size=8193M`). The real full-graph publication
committed successfully. A full local restore still fails with a **14 GiB Java heap OOM**;
this change does not establish usable full-graph cache performance.

## Generated-input failure

Restored `ActionExecutionValue` metadata bypasses its producer action, so the output's parent
directories may be absent. `RemoteActionFileSystem.isRemote` previously canonicalized the
path before looking up that metadata. Missing parents made it report a nonremote file;
`getInputStream` then opened the nonexistent local path without invoking the downloader.

The fix consults declared remote input metadata first, leaving materialization to the normal
fetcher. It does not create directories or download files merely to inspect metadata.
The regression test reproduces the original `FileNotFoundException` before the fix and
passes afterward. Existing filesystem and path-canonicalization tests also pass.

Both original `//pkg/security/seclwin/model:strip_go_build_windows_from_*` targets now succeed
from a clean output tree and fresh JVM against the real full-graph seed:

| Restore path | Analysis hits | Execution hits | Outputs |
| --- | ---: | ---: | --- |
| Local, warm metadata pages | 3 | 1 | Both digests match the full-build baseline |
| Remote, empty retained cache and cold server pages | 3 | 1 | Both digests match the full-build baseline |

The remote check downloaded {remote['downloaded_bytes']:,} metadata bytes. The metadata worker
is loopback, separate from the existing ordinary action cache. Its excluded readiness pass
also successfully read all 7,679,782,112 publication/CAS bytes and retried the existing commit.

## Larger remote publication

The shared protocol cap, client budget validation, and option help now agree on 8 GiB.
The 1 MiB chunk and bundle limits, 64-publication, 16,384-file and 32,768-chunk bounds remain.
The default attempted transfer budget remains 257 MiB; larger transfers are explicit.
Upgrade both client and catalog worker for publications above the previous 4 GiB cap.

The full `//...` workload retains the previously approved 36 packaging exclusions:
5,658 selected targets, 598,064 configured targets/aspects, 5,536 completed targets and
122 Bazel-skipped targets. The fresh publication contains **7,679,187,524 logical bytes**;
all 10,738 named output digests match the baseline and the remote commit succeeds.

Boundary coverage includes exactly 8 GiB, one byte above it, integer overflow, missing chunks,
cumulative materialization limits, and demand reads from a 6 GiB logical file while fetching
less than 4 MiB. **821 cases across 24 test targets pass.**

## Remaining full-graph failure

With WSL capped at 20 GiB, a 14 GiB Java heap, and the existing 512 MiB admission option,
the full local consumer reaches **89,515 analysis hits** before exit 33 with
`OutOfMemoryError: Java heap space`. The two previously missing inputs and both template
outputs exist with the expected contents. The input-download defect is therefore distinct
from the remaining heap-capacity failure. The final BEP metrics are incomplete after the crash.

A heap histogram could not be collected before the JVM exited. The allocation failure's
stack location alone does not identify which objects retain the heap. Next investigation:
capture a live heap histogram/retention profile before saturation, then distinguish persistent
restored graph cost from concurrent deserialization state. Larger metadata transfer limits do
not solve this JVM-memory requirement. The separate default 256 MiB admission-budget issue
documented in the prior full-graph report is also unchanged.

## Controls and exclusions

These are correctness checks, not repeated performance samples. Startup/clean, worker readiness,
seed generation, and failed attempts are excluded from timing comparisons. Native binaries,
source/fixture state, output digests, empty graphs and cache hits are recorded.

The first attempted old-seed restore had an accidental tools-directory `PATH` alias and was
excluded. After correcting it, the old seed still had no hits because the new native binary has
a different installation checksum in `FrontierNodeVersion`. No checksum guard was bypassed:
the successful fresh remote producer supplied a new seed for both consumers. The old-seed
recompute created a parent-linked publication; it was not used for validation.

See [verification.json](verification.json), compressed per-run results/logs, the red/green test
records, and the archived harness. Larger raw recordings and cache blobs remain under
`{task}`. No fixture files were changed and nothing was pushed.
'''
(dest/'README.md').write_text(readme)
doc=repo/'docs/jsgette/skycache-remote-mvp.md'
s=doc.read_text()
s=s.replace('Bounds per bundle: 64 publications, 16,384 files, 32,768 chunk references, 4 GiB of logical file',
            'Bounds per bundle: 64 publications, 16,384 files, 32,768 chunk references, 8 GiB of logical file',1)
s=s.replace('positive and at most 4 GiB + 1 MiB; failed reads consume the budget too.',
            'positive and at most 8 GiB + 1 MiB; failed reads consume the budget too.',1)
s=s.replace('Explicit parent materialization also has a cumulative 4 GiB logical-write budget, charged',
            'Explicit parent materialization also has a cumulative 8 GiB logical-write budget, charged',1)
s+='''\n\n## Generated-input restore fix and 8 GiB publication limit (2026-10-27)\n\nThe missing nested generated-template inputs now download correctly after an execution-cache\nhit. Both originally failing targets pass with matching output digests through local and cold\nremote restore. The shared logical cap is now 8 GiB; use an explicit attempted-transfer budget\nup to `--experimental_remote_skycache_max_transfer_size=8193M`. The real 7.68 GB full-graph\npublication commits successfully on the updated worker, with all output digests matching.\n\nAll 821 focused test cases pass. Full local restoration remains memory-limited: it reaches\n89,515 analysis hits and then exhausts a 14 GiB Java heap. This is separate from the fixed\nmissing-input error; full-graph local/remote performance remains unestablished. See the\n[regression evidence and remaining blocker](measurements/skycache-restore-limits-2026-10-27/README.md).\n'''
doc.write_text(s)
print(json.dumps(dict(archive=str(dest),tests=build['cases'],full_publication=True,full_restore='heap_oom',template_paths='local_and_remote_passed')))
