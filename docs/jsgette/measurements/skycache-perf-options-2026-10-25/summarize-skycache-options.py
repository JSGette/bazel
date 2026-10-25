from pathlib import Path
import json,subprocess,collections,statistics,re,xml.etree.ElementTree as ET
R=Path('/home/jgett/REPOS/.work/skycache-perf-options-2026-10-25')
data=json.loads((R/'controlled/results.json').read_text());assert data['status']=='passed'
groups=collections.defaultdict(list)
for row in data['runs']:
 if not row['preparation']:groups[row['mode']+'/'+row['implementation']].append(row)
summary={}
for k,rows in groups.items():
 s={}
 for name,get in [('seconds',lambda r:r['seconds']),('peak_pss_mib',lambda r:r['peak_pss_kib']/1024),('analysis_seconds',lambda r:int(r['build_metrics']['timingMetrics']['analysisPhaseTimeInMs'])/1000)]:
  vals=[get(r) for r in rows];s[name]=dict(values=vals,median=statistics.median(vals),min=min(vals),max=max(vals))
 s['records_read']=[r['local-skycache-validation.json']['records_read'] for r in rows]
 s['witnesses_checked']=[r['local-skycache-validation.json']['witnesses_checked'] for r in rows]
 s['local_bytes']=[r['local-skycache.json']['cas_received_value_bytes'] for r in rows]
 summary[k]=s
(R/'benchmark-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
P=R/'profiles'
assert json.loads((P/'results.json').read_text())['status']=='passed'
profiles={}
for label in ['baseline','array']:
 export=P/(label+'-events.json')
 events=['jdk.ObjectAllocationSample','jdk.GarbageCollection','jdk.GCHeapSummary','jdk.ExecutionSample']
 with export.open('w') as f:
  subprocess.run(['/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin/jfr','print','--json','--stack-depth','128','--events',','.join(events),str(P/(label+'.jfr'))],stdout=f,check=True)
 entries=json.loads(export.read_text())['recording']['events'];alloc=collections.Counter();classes=collections.Counter();samples=collections.Counter();groups=collections.Counter();gcs=[];heap=[];stream=0
 for e in entries:
  v=e['values'];names=[f['method']['type']['name']+'.'+f['method']['name'] for f in (v.get('stackTrace') or {}).get('frames',[])]
  if e['type']=='jdk.ObjectAllocationSample':
   weight=v['weight'];origin=next((n for n in names if '/serialization/local/' in n),'other');alloc[origin]+=weight;classes[v['objectClass']['name']]+=weight
   if any('CodedInputStream$StreamDecoder.<init>' in n for n in names):stream+=weight
  elif e['type']=='jdk.ExecutionSample':
   samples[next((n for n in names if '/serialization/local/' in n),'other')]+=1
   group='other JVM/Bazel'
   for token,title in [('LocalInputSnapshot.captureSource','source snapshot'),('LocalCacheSession.refreshCandidates','publication admission'),('LocalDependencyValidator.','dependency validation'),('SharedValueDeserializationContext','shared-value restoration'),('FingerprintValueStore.','other pack reads')]:
    if any(token in n for n in names):group=title;break
   groups[group]+=1
  elif e['type']=='jdk.GarbageCollection':gcs.append(v)
  elif e['type']=='jdk.GCHeapSummary':heap.append(v)
 hist=(P/(label+'-histogram.txt')).read_text();m=re.search(r'Total\s+(\d+)\s+(\d+)',hist)
 result=dict(allocation_weight_bytes=sum(alloc.values()),allocation_origins=alloc.most_common(),allocation_classes=classes.most_common(20),stream_decoder_allocation_weight_bytes=stream,execution_sample_groups=dict(groups),execution_sample_first_local_frame=samples.most_common(),gc_count=len(gcs),gc_pause_seconds=sum(float(v['sumOfPauses'][2:-1]) for v in gcs),max_heap_used_at_gc=max(v['heapUsed'] for v in heap),post_command_histogram_instances=int(m[1]),post_command_histogram_live_bytes=int(m[2]),note='One diagnostic run; allocation weights are sampled traffic, not live heap; CPU sample groups are not wall-time phase measurements; histogram forces GC after command completion.')
 (P/(label+'-summary.json')).write_text(json.dumps(result,indent=2)+'\n');profiles[label]=result
 print(label,json.dumps({k:v for k,v in result.items() if k not in ['allocation_origins','allocation_classes','execution_sample_first_local_frame']}))
test_summary={}
for dirname in ['lookup-reuse-tests','lookup-array-tests']:
 files=list((R/dirname).glob('*.xml'));cases=sum(len(ET.parse(p).findall('.//testcase')) for p in files)
 assert all(not (ET.parse(p).findall('.//failure')+ET.parse(p).findall('.//error')) for p in files)
 test_summary[dirname]=dict(targets=len(files),cases=cases)
(R/'tests-summary.json').write_text(json.dumps(test_summary,indent=2)+'\n')
