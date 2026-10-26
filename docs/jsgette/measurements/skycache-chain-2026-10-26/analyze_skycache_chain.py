#!/usr/bin/env python3
from pathlib import Path
import collections, gzip, json, statistics, subprocess, sys

ROOT=Path('/home/jgett/REPOS/.work/skycache-chain-2026-10-26')
JFR=Path('/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin/jfr')

def save(p,data):p.write_text(json.dumps(data,indent=2)+'\n')

def timings(direct=False):
    runroot=ROOT/('direct-runs' if direct else 'runs')
    results=json.loads((runroot/'results.json').read_text())
    assert results['status']=='passed'
    assert all(results[k] for k in ['source_states_unchanged','binary_unchanged','candidate_binary_unchanged'])
    rows=[]
    for r in results['runs']:
        if r['warmup']:continue
        assert r['outputs_match'] and r['probe_output_matches'] and (r['analysis_hits'],r['execution_hits'])==(18,11)
        assert r['local-skycache']['writes']>0 and not r['local-skycache']['failed']
        assert r['local-skycache']['published'] if r['arm']=='local' else r['committed']
        events=json.loads(gzip.open(runroot/(r['label']+'.profile.gz'),'rt').read())['traceEvents']
        before=sum(e.get('dur',0) for e in events if e.get('name')=='LocalSkycacheModule.beforeCommand')/1e6
        assert before>0
        timeline=[json.loads(s) for s in (runroot/(r['label']+'-timeline.jsonl')).read_text().splitlines()]
        build_done=next(t['elapsed_s'] for t in timeline if 'Build completed successfully' in t['text'])
        row=dict(label=r['label'],arm=r['arm'],command_s=r['seconds'],before_command_s=before,
            other_pre_build_complete_s=build_done-before,after_build_complete_s=r['seconds']-build_done,
            value_bytes=r['local-skycache']['cas_received_value_bytes'],cas_get_requests=r['local-skycache']['cas_get_requests'],
            dependency_validation=r['local-skycache-validation'])
        if r['arm']!='local':
            row.update(r['remote-skycache'])
            row['rpc_counts']=dict(collections.Counter(c['method'] for c in r['rpc']))
            if r['rpc']:
                first_upload=min(c['start_monotonic'] for c in r['rpc'] if c['method'].endswith('/BatchUpdateBlobs'))-r['command_start_monotonic']
                commit=next(c for c in r['rpc'] if c['method'].endswith('/CommitPublication'))
                committed=commit['start_monotonic']+commit['duration_s']-r['command_start_monotonic']
                row.update(build_to_upload_s=first_upload-build_done,upload_through_commit_s=committed-first_upload,after_commit_s=r['seconds']-committed)
            if direct: assert not r['rpc']
            if r['arm']=='remote-warm':assert row['downloaded_bytes']==0
        rows.append(row)
    fields=['command_s','before_command_s','other_pre_build_complete_s','after_build_complete_s']
    summary={a:dict(n=len(g),**{f:dict(median=statistics.median(r[f] for r in g),mean=statistics.mean(r[f] for r in g),min=min(r[f] for r in g),max=max(r[f] for r in g)) for f in fields}) for a in ['local','remote-warm','remote-cold'] if (g:=[r for r in rows if r['arm']==a])}
    gap={f:summary['remote-warm'][f]['median']-summary['local'][f]['median'] for f in fields}
    report=dict(direct_endpoint=direct,rows=rows,summary=summary,retained_remote_minus_local=gap,
        mean_retained_remote_minus_local={f:summary['remote-warm'][f]['mean']-summary['local'][f]['mean'] for f in fields},
        controls={k:results[k] for k in ['source_head','binary_sha256','seed_preparation','source_states_unchanged','binary_unchanged','environment_control']},
        caveats='Three samples per arm on loopback with warm backend/OS caches; empty retained cache is not cold storage at every layer. '
        'The middle timing interval includes command overhead, analysis, validation, restoration and execution. Stage medians need not add exactly. '
        'cached_read_nanos is overlapping thread time. Value bytes are not unique demand. JFR and counter runs are excluded.')
    save(ROOT/('comparison-direct.json' if direct else 'comparison.json'),report)
    print(json.dumps(dict(summary=summary,gap=gap),indent=2))

def names(value):
    return [f['method']['type']['name'].replace('/','.')+'.'+f['method']['name'] for f in (value.get('stackTrace') or {}).get('frames',[])]

def category(stack):
    checks=[('final_input_validation','LocalInputSnapshot.isUnchanged'),
            ('final_input_validation','LocalInputSnapshot.lambda$isUnchanged'),
            ('remote_range_read','RemoteSkyCache.readCachedRange'),
            ('remote_retained_activation','RemoteSkyCache.reuseRetainedChunk'),
            ('fresh_input_hashing','LocalContentHasher.'),
            ('remote_download','RemoteSkyCache.downloadLocked'),
            ('witness_table','LocalWitnessTable.'),
            ('packed_record_read','PackedFingerprintValueStore.readRecord'),
            ('dependency_validation','LocalDependencyValidator.'),
            ('candidate_admission','LocalCacheSession.refreshCandidates'),
            ('deserialization','SharedValueDeserializationContext.'),
            ('remote_prepare','RemoteSkyCache.prepareExclusive'),
            ('remote_publish','RemoteSkyCache.publishPending'),
            ('other_finalization','LocalCacheSession.finish')]
    for label,needle in checks:
        if any(needle in n for n in stack):return label
    return 'other'

def profile(path):
    output=path.with_suffix('.samples.json')
    if not output.exists():
        with output.open('w') as f:
            subprocess.run([str(JFR),'print','--json','--stack-depth','96','--events','jdk.ExecutionSample,jdk.NativeMethodSample,jdk.JavaMonitorEnter',str(path)],stdout=f,check=True)
    events=json.loads(output.read_text())['recording']['events']
    groups=collections.Counter();remote_tops=collections.Counter();tops=collections.Counter();inclusive=collections.Counter();waits=collections.Counter();threads=collections.Counter()
    for event in events:
        v=event['values'];stack=names(v);kind=category(stack)
        if event['type']=='jdk.JavaMonitorEnter':
            duration=v['duration']
            if duration.startswith('PT') and duration.endswith('S'):waits[kind]+=float(duration[2:-1])
            continue
        groups[kind]+=1
        threads[(v.get('sampledThread') or {}).get('javaName','?')]+=1
        if stack:tops[stack[0]]+=1
        for n in set(stack):
            if 'serialization.local.' in n:inclusive[n]+=1
        if kind=='remote_range_read':remote_tops[' <- '.join(stack[:5])]+=1
    report=dict(source=str(path),samples=sum(groups.values()),exclusive_categories=groups,inclusive_methods=inclusive.most_common(35),
        top_frames=tops.most_common(30),remote_range_top_stacks=remote_tops.most_common(20),monitor_thread_seconds=waits,sampled_threads=threads.most_common(10),
        caveat='Execution/native samples are not wall-time shares; native samples may represent blocking I/O. Monitor durations overlap. The recording spans only the diagnostic build.')
    save(path.with_suffix('.analysis.json'),report)
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--direct':timings(True)
    elif len(sys.argv)>1:profile(Path(sys.argv[1]))
    else:timings()
