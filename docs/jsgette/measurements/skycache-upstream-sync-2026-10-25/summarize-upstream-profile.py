from pathlib import Path
import json,collections
R=Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25/hit-profile')
events=json.loads((R/'jfr-samples.json').read_text())['recording']['events']
groups=collections.defaultdict(collections.Counter);methods=collections.defaultdict(collections.Counter);ranges={}
for e in events:
 frames=(e['values'].get('stackTrace') or {}).get('frames',[])
 stack=[f['method']['type']['name'].replace('/','.')+'.'+f['method']['name'] for f in frames]
 text='\n'.join(stack);kind=e['type']
 if 'LocalCacheSession.refreshCandidates' in text:group='publication admission'
 elif 'LocalDependencyValidator.' in text:group='dependency validation'
 elif 'LocalInputSnapshot.captureSource' in text:group='broad source capture'
 elif 'PackedFingerprintValueStore.' in text or 'SegmentedFingerprintValueStore.' in text:group='other local pack reads'
 elif '.skyframe.serialization.' in text:group='other serialization'
 else:group='other JVM/Bazel/background'
 groups[kind][group]+=1
 if group!='other JVM/Bazel/background':
  ranges.setdefault(group,[]).append(e['values']['startTime'])
  for method in set(stack):
   if any(x in method for x in ('LocalWitnessTable.','PackedFingerprintValueStore.','SegmentedFingerprintValueStore.','LocalInputSnapshot.','LocalContentHasher.','LocalDependencyValidator.','LocalCacheSession.')):methods[kind][method]+=1
out=dict(purpose='Sampled call-stack attribution, not wall-time percentages or a new latency benchmark. Native samples include blocked I/O/event-loop threads. Methods are inclusive and overlap.',
 events=len(events),groups={k:dict(v) for k,v in groups.items()},inclusive_methods={k:v.most_common(35) for k,v in methods.items()},observed_sample_spans={k:dict(first=min(v),last=max(v)) for k,v in ranges.items()})
(R/'profile-summary.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
