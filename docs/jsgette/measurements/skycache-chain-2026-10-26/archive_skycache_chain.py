#!/usr/bin/env python3
"""Archive reproducible chain evidence without raw JFR environment records."""
from pathlib import Path
import collections
import gzip
import hashlib
import importlib.util
import json
import contextlib
import io
import shutil

ROOT = Path('/home/jgett/REPOS/.work/skycache-chain-2026-10-26')
REPO = Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
WORK = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-26/review-our-current-progress-and-find/work')
DEST = REPO/'docs/jsgette/measurements/skycache-chain-2026-10-26'

def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')

def compress(src, dst):
    dst.write_bytes(gzip.compress(src.read_bytes(), mtime=0))

DEST.mkdir(exist_ok=False)
for name in ['comparison-direct.json','comparison.json','descriptor-simulation.json']:
    shutil.copy2(ROOT/name, DEST/name)
compress(ROOT/'preparation.json', DEST/'preparation.json.gz')
stages=json.loads((ROOT/'stages.json').read_text())
for run in stages['runs'].values():
    for method in run['methods'].values():
        del method['durations_s']
save(DEST/'stages.json', stages)

cohorts={}
for name in ['direct-runs','runs','diagnostics','warm-diagnostics']:
    folder=ROOT/name
    results=json.loads((folder/'results.json').read_text())
    assert results['status']=='passed', name
    assert all(results[k] for k in ['source_states_unchanged','binary_unchanged','candidate_binary_unchanged']), name
    archive=DEST/name
    archive.mkdir()
    compress(folder/'results.json', archive/'results.json.gz')
    compact={k:v for k,v in results.items() if k!='runs'}
    compact['runs']=[]
    for row in results['runs']:
        assert row['exit_code']==0 and row['outputs_match'] and not row['errors']
        assert row['local-skycache']['published'] if row['arm']=='local' else row['committed']
        if row['arm']=='remote-warm': assert row['remote-skycache']['downloaded_bytes']==0
        if name=='direct-runs': assert not row.get('rpc')
        compact['runs'].append({k:v for k,v in row.items() if k not in ['rpc','build_metrics']})
        compact['runs'][-1]['rpc_counts']=dict(collections.Counter(r['method'] for r in row.get('rpc',[])))
        # Timelines and RPC logs are transport/timing records, not payloads.
        for suffix in ['-timeline.jsonl','-rpc.jsonl','-stages.jsonl']:
            p=folder/(row['label']+suffix)
            if p.exists():compress(p,archive/(p.name+'.gz'))
    compact['complete_results']='results.json.gz'
    save(archive/'results.json', compact)
    shutil.copy2(folder/'harness.py',archive/'harness.py')
    cohorts[name]=dict(status=results['status'],runs=len(results['runs']),
        measured=[r['label'] for r in results['runs'] if not r['warmup']],
        source_states_unchanged=results['source_states_unchanged'],binary_unchanged=results['binary_unchanged'])

spec=importlib.util.spec_from_file_location('chain_analysis',WORK/'analyze_skycache_chain.py')
analysis=importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)
for path in [ROOT/'runs/diagnostic-retained.jfr',ROOT/'warm-diagnostics/diagnostic-local.jfr',ROOT/'warm-diagnostics/diagnostic-retained.jfr']:
    with contextlib.redirect_stdout(io.StringIO()):analysis.profile(path)
    shutil.copy2(path.with_suffix('.analysis.json'),DEST/path.parent.name/path.with_suffix('.analysis.json').name)

for name in ['SkycacheChainProbe.java','SkycacheReadProbe.java','SkycacheReadProbeV2.java']:
    shutil.copy2(ROOT/name,DEST/name)
for name in ['probe-manifest.mf','read-probe-manifest.mf','read-probe-v2-manifest.mf']:
    compress(ROOT/name,DEST/(name+'.gz'))
for name in ['HydrateSkycacheSeed.java','prepare_chain.py','analyze_skycache_chain.py',
             'summarize_chain_stages.py','make_chain_benchmark.py','make_chain_counters.py',
             'make_chain_followup.py','make_chain_direct.py']:
    shutil.copy2(WORK/name,DEST/name)

# Reference native recordings by hash; do not publish JFR startup environment events.
recordings=[]
for folder in ['direct-runs','runs','diagnostics','warm-diagnostics']:
    for pattern in ['*.profile.gz','*.jfr']:
        for p in sorted((ROOT/folder).glob(pattern)):
            recordings.append(dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)))
cleanup=dict(probe_removed=not Path('/home/jgett/REPOS/.work/skycache-large-remote-2026-10-25/agent/worktree/skycache_publication_probe_20261026').exists(),
    fixture_server_pidfile_removed=not Path('/home/jgett/REPOS/.work/skycache-large-remote-2026-10-25/agent/output-base/server/server.pid.txt').exists())
assert all(cleanup.values()),cleanup
save(DEST/'provenance.json',dict(source_commit='9a25005f547c67bea14581f45f80b731155679b8',
    binary_sha256=sha(Path('/home/jgett/REPOS/.work/skycache-validation-2026-10-26/bin/bazel')),
    native_root=str(ROOT),cohorts=cohorts,cleanup=cleanup,recordings=recordings,
    production_changes=False,
    limitations=['Native profiles/JFR and immutable seed data are referenced, not checked in.',
      'Three observations per arm; original proxy cohort is not used for primary timings.',
      'Instrumented diagnostics and all warmups are excluded from primary timing statistics.']))
save(DEST/'evidence-index.json',[dict(path=str(p.relative_to(DEST)),bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(DEST.rglob('*')) if p.is_file()])
print(json.dumps(dict(destination=str(DEST),cohorts=cohorts,cleanup=cleanup,
    files=sum(p.is_file() for p in DEST.rglob('*')),bytes=sum(p.stat().st_size for p in DEST.rglob('*') if p.is_file())),indent=2))
