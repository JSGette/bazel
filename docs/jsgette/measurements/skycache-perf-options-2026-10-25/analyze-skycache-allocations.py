from pathlib import Path
import subprocess,json,collections
R=Path('/home/jgett/REPOS/.work/skycache-perf-options-2026-10-25')
R.mkdir(exist_ok=True)
src=Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25/hit-profile/minimal.jfr')
events=['jdk.ObjectAllocationSample','jdk.GarbageCollection','jdk.GCHeapSummary','jdk.GCPhasePause','jdk.EvacuationFailed','jdk.JavaMonitorEnter','jdk.ThreadPark']
out=R/'baseline-allocation-events.json'
with out.open('w') as f:
 subprocess.run(['/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin/jfr','print','--json','--stack-depth','128','--events',','.join(events),str(src)],stdout=f,check=True)
data=json.loads(out.read_text())['recording']['events']
types=collections.Counter();classes=collections.Counter();origins=collections.Counter();stacks=collections.Counter();gcs=[];heaps=[];pauses=[];monitors=collections.Counter()
for e in data:
 t=e['type'];v=e['values'];types[t]+=1
 if t=='jdk.ObjectAllocationSample':
  weight=v['weight'];classes[v['objectClass']['name']]+=weight
  frames=(v.get('stackTrace') or {}).get('frames',[])
  names=[f['method']['type']['name']+'.'+f['method']['name'] for f in frames]
  local=next((n for n in names if '/serialization/local/' in n),'other')
  origins[local]+=weight
  stacks[';'.join(names[:10])]+=weight
 elif t=='jdk.GarbageCollection':gcs.append(v)
 elif t=='jdk.GCHeapSummary':heaps.append(v)
 elif t=='jdk.GCPhasePause':pauses.append(v)
 elif t=='jdk.JavaMonitorEnter':
  names=[f['method']['type']['name']+'.'+f['method']['name'] for f in (v.get('stackTrace') or {}).get('frames',[])]
  monitors[';'.join(names[:5])]+=1
result=dict(source=str(src),note='Allocation sample weights estimate allocation traffic, not retained heap or PSS; categories use first local frame. Diagnostic run, not steady benchmark.',events=dict(types),allocation_weight_bytes=sum(classes.values()),allocation_classes=classes.most_common(25),allocation_local_origins=origins.most_common(35),allocation_stacks=stacks.most_common(30),gc=gcs,heap=heaps,pauses=pauses,monitors=monitors.most_common(15))
(R/'baseline-allocation-summary.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('allocation_stacks','gc','heap','pauses','monitors')},indent=2))
print('GC first/last',gcs[:2],gcs[-2:])
print('Heap range',min(h['heapUsed'] for h in heaps),max(h['heapUsed'] for h in heaps))
