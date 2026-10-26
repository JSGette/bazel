#!/usr/bin/env python3
"""Recover command/profile boundaries and summarize diagnostic JFR samples."""
from pathlib import Path
import collections
import datetime
import gzip
import hashlib
import json
import statistics
import subprocess

WORK = Path('/home/jgett/REPOS/.work')
PRIOR = WORK/'skycache-retained-chunks-2026-10-25/agent-matrix'
ROOT = WORK/'skycache-preliminary-2026-10-26'
JFR = Path('/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin/jfr')


def median(values):
    return statistics.median(values)


def history():
    results = json.loads((PRIOR/'results.json').read_text())
    rows = []
    for row in results['runs']:
        if row.get('preparation'):
            continue
        path=PRIOR/(row['label']+'.profile.gz')
        events=json.loads(gzip.open(path,'rt').read())['traceEvents']
        end=max(x.get('ts',0)+x.get('dur',0) for x in events)/1e6
        rows.append(dict(label=row['label'],policy=row['mode'],arm=row['arm'],
            command_s=row['seconds'],profile_end_s=end,outside_profile_s=row['seconds']-end,
            before_command_s=sum(x.get('dur',0) for x in events
                if x.get('name')=='LocalSkycacheModule.beforeCommand')/1e6,
            profile_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    groups=[]
    for key in sorted(set((x['policy'],x['arm']) for x in rows)):
        group=[x for x in rows if (x['policy'],x['arm'])==key]
        summary=dict(policy=key[0],arm=key[1],n=len(group))
        for field in ['command_s','profile_end_s','outside_profile_s','before_command_s']:
            summary[field]=dict(median=median([x[field] for x in group]),
                min=min(x[field] for x in group),max=max(x[field] for x in group))
        groups.append(summary)
    output=dict(rows=rows,groups=groups,
        interpretation='Outside-profile elapsed is a boundary diagnostic, not an exclusive publication span. '
                       'Compare direct timeline, RPC and JFR evidence before causal attribution.')
    (ROOT/'historical-profile-boundaries.json').write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps(groups,indent=2))


def method_names(event):
    frames=(event.get('stackTrace') or {}).get('frames',[])
    return [f.get('method',{}).get('type',{}).get('name','').replace('/','.')+'.'+
            f.get('method',{}).get('name','') for f in frames]


def classify(names):
    checks=[('publication_final_validation','LocalInputSnapshot.isUnchanged'),
            ('publication_repository_bindings','LocalInputSnapshot.recordRepositoryBindings'),
            ('publication_inherit_witnesses','LocalInputSnapshot.registerInheritedForFinalValidation'),
            ('publication_witness_table','LocalWitnessTable.write'),
            ('publication_commit','SkyCacheTransport.commit'),
            ('publication_upload','SkyCacheTransport.upload'),
            ('publication_seal','SegmentedFingerprintValueStore.seal'),
            ('publication_finish_other','LocalCacheSession.finish'),
            ('retained_activation','RemoteSkyCache.reuseRetainedChunk'),
            ('authenticated_range_read','RemoteSkyCache.readCachedRange'),
            ('dependency_validation','LocalDependencyValidator.validate'),
            ('candidate_preparation','RemoteSkyCache.prepare'),
            ('skycache_cleanup','RemoteSkyCache.close')]
    for category,needle in checks:
        if any(needle in name for name in names):
            return category
    return 'other'


def diagnostics():
    report=json.loads((ROOT/'results.json').read_text())
    summaries=[]
    for row in report['runs']:
        if 'seconds' not in row:
            continue
        label=row['label']; timeline=[json.loads(s) for s in (ROOT/(label+'-timeline.jsonl')).read_text().splitlines()]
        markers={}
        for item in timeline:
            for key,needle in [('build_complete','Build completed successfully'),
                    ('commit_reported','Remote SkyCache: publication committed'),
                    ('transfers_reported','Remote SkyCache transfers:')]:
                if needle in item['text']:
                    markers[key]=item['elapsed_s']
        summary={k:row.get(k) for k in ['label','arm','jfr','seconds','profile_end_s','outside_profile_s',
                                      'outputs_match','analysis_hits','execution_hits','committed']}
        summary['markers']=markers
        summary['rpc']={}
        for method in sorted(set(x['method'] for x in row.get('rpc',[]))):
            calls=[x for x in row['rpc'] if x['method']==method]
            summary['rpc'][method]=dict(count=len(calls),sum_duration_s=sum(x['duration_s'] for x in calls),
                request_bytes=sum(x['request_bytes'] for x in calls),
                first_start_s=min(x['start_epoch'] for x in calls)-row['start_epoch'],
                last_end_s=max(x['start_epoch']+x['duration_s'] for x in calls)-row['start_epoch'])
        if row['jfr']:
            output=ROOT/(label+'-events.json')
            if not output.exists():
                with output.open('x') as file:
                    subprocess.run([str(JFR),'print','--json','--stack-depth','256','--events',
                        'jdk.ExecutionSample,jdk.NativeMethodSample,jdk.ThreadPark,jdk.JavaMonitorEnter,jdk.FileRead,jdk.FileWrite',
                        str(ROOT/(label+'.jfr'))],stdout=file,check=True)
            events=json.loads(output.read_text())['recording']['events']
            samples=collections.Counter(); late=collections.Counter(); methods=collections.Counter()
            late_methods=collections.Counter(); waits=collections.Counter(); threads=collections.Counter()
            late_stacks=collections.Counter(); total=0; late_total=0
            for event in events:
                value=event['values']; ts=datetime.datetime.fromisoformat(value['startTime'].replace('Z','+00:00')).timestamp()
                if not row['start_epoch']<=ts<=row['end_epoch']:
                    continue
                names=method_names(value); category=classify(names)
                if event['type'] in ['jdk.ExecutionSample','jdk.NativeMethodSample']:
                    total+=1; samples[category]+=1
                    thread=value.get('sampledThread') or {};threads[thread.get('javaName','?')]+=1
                    for name in set(names):
                        if 'serialization.local.' in name or 'serialization.remote.' in name:
                            methods[name]+=1
                    if ts>=row['start_epoch']+markers.get('build_complete',float('inf')):
                        late_total+=1;late[category]+=1
                        late_stacks[' <- '.join(names[:12])]+=1
                        for name in set(names):
                            if 'serialization.local.' in name or 'serialization.remote.' in name:
                                late_methods[name]+=1
                elif event['type'] in ['jdk.ThreadPark','jdk.JavaMonitorEnter']:
                    duration=value.get('duration','PT0S')
                    if duration.startswith('PT') and duration.endswith('S'):
                        waits[category]+=float(duration[2:-1])
            summary['jfr_summary']=dict(samples=total,after_build_complete_samples=late_total,
                exclusive_categories=samples,after_build_complete_categories=late,
                inclusive_methods=methods.most_common(30),after_build_complete_methods=late_methods.most_common(30),
                sampled_threads=threads.most_common(12),wait_seconds_by_category=waits,
                after_build_complete_top_stacks=late_stacks.most_common(15),
                interpretation='CPU/native execution sample counts, not wall-time shares; nested methods are inclusive. '
                    'Wait sums are overlapping thread-time and thresholded. Marker arrival includes output transport delay.')
        summaries.append(summary)
    (ROOT/'diagnostic-analysis.json').write_text(json.dumps(summaries,indent=2)+'\n')
    print(json.dumps(summaries,indent=2))


if __name__=='__main__':
    history()
    diagnostics()
