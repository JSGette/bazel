"""Summarize execution/allocation stacks only; raw JFR and event JSON remain local."""
from pathlib import Path
from collections import Counter, defaultdict
import hashlib
import json
import subprocess

work=Path('/home/jgett/REPOS/.work/skycache-concurrent')
root=work/'datadog-crossrevision-witness-jfr'
jfr='/home/jgett/REPOS/.work/skyframe-go/work/build-output/external/rules_java++toolchains+remotejdk25_linux/bin/jfr'
assert json.loads((root/'results.json').read_text())['passed']

def category(frames):
    text='\n'.join(frames)
    if 'LocalInputSnapshot.captureSource' in text:
        return 'full_source_snapshot'
    if 'LocalCacheSession.finish' in text:
        return 'publication_finish'
    if 'LocalCacheSession.refreshCandidates' in text:
        if 'LocalInputSnapshot.fingerprint' in text:
            return 'candidate_manifest_input_checks'
        return 'candidate_discovery_other'
    if 'LocalDependencyValidator.' in text:
        if 'java.util.regex.' in text:
            return 'dependency_key_regex'
        if 'LocalInputSnapshot.fingerprint' in text:
            return 'dependency_input_checks'
        if 'PackedFingerprintValueStore.' in text:
            return 'dependency_record_reads'
        return 'dependency_validation_other'
    if 'AnalysisCacheInvalidator.' in text:
        return 'invalidation_other'
    if 'serialization.' in text:
        return 'serialization_other'
    return 'other'

summary={'purpose':__doc__,'scope':'Instrumented warm revert; execution sample counts are not wall-time percentages. Allocation sample weights are estimates, not measured retained heap.','servers':{}}
for name in ('a','b'):
    profile=root/(name+'-warm-revert.jfr')
    events_path=root/(name+'-sample-events.json')
    with events_path.open('w') as out:
        subprocess.run([jfr,'print','--json','--stack-depth','128','--events',
            'jdk.ExecutionSample,jdk.NativeMethodSample,jdk.ObjectAllocationSample',str(profile)],
            stdout=out,check=True)
    events=json.loads(events_path.read_text())['recording']['events']
    executions=Counter()
    allocations=Counter()
    event_types=Counter()
    methods=Counter()
    classes=Counter()
    hot_by_category=defaultdict(Counter)
    threads=Counter()
    for event in events:
        event_types[event['type']]+=1
        values=event['values']
        raw_frames=(values.get('stackTrace') or {}).get('frames') or []
        frames=[f["method"]["type"]["name"].replace('/','.')+'.'+f['method']['name'] for f in raw_frames]
        bucket=category(frames)
        if event['type']=='jdk.ObjectAllocationSample':
            weight=values['weight']
            allocations[bucket]+=weight
            classes[(values.get('objectClass') or {}).get('name','unknown').replace('/','.')]+=weight
        else:
            executions[bucket]+=1
            if frames:
                methods[frames[0]]+=1
                hot_by_category[bucket][frames[0]]+=1
            thread=values.get('sampledThread') or {}
            threads[thread.get('javaName') or thread.get('osName') or 'unknown']+=1
    summary['servers'][name]={
        'jfr_bytes':profile.stat().st_size,'jfr_sha256':hashlib.sha256(profile.read_bytes()).hexdigest(),
        'event_types':dict(event_types),'execution_samples_by_category':dict(executions.most_common()),
        'allocation_sample_weight_by_category':dict(allocations.most_common()),
        'top_execution_methods':methods.most_common(25),'top_allocation_classes':classes.most_common(20),
        'top_methods_by_category':{k:v.most_common(10) for k,v in hot_by_category.items()},
        'threads':threads.most_common(15)}
(work/'witness-invalidation-profile-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps({name:{k:row[k] for k in ('event_types','execution_samples_by_category','allocation_sample_weight_by_category')}
    for name,row in summary['servers'].items()},indent=2))
