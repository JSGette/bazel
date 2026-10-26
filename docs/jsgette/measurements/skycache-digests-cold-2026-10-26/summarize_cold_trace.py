from pathlib import Path
import collections,json,statistics
ROOT=Path('/home/jgett/REPOS/.work/skycache-digests-2026-10-26')

def union(intervals):
 total=0;end=0
 for a,b in sorted(intervals):
  if a>=end:total+=b-a
  elif b>end:total+=b-end
  end=max(end,b)
 return total/1e9

traces={}
for path in (ROOT/'diagnostic').glob('r*-stages.jsonl'):
 events=[json.loads(x) for x in path.read_text().splitlines()]
 assert not any('transform_error' in x for x in events)
 groups=collections.defaultdict(list)
 for e in events:groups[e['method']].append(e)
 traces[path.stem]={method:dict(calls=len(v),thread_seconds=sum(e['duration_ns'] for e in v)/1e9,
   max_seconds=max(e['duration_ns'] for e in v)/1e9,
   active_union_seconds=union([(e['begin_ns'],e['end_ns']) for e in v])) for method,v in groups.items()}
(ROOT/'cold-trace.json').write_text(json.dumps(dict(traces=traces,note='Diagnostic normal-return timings only; nested and overlapping methods must not be added. Union means at least one call is active, not isolated critical-path cost.'),indent=2)+'\n')
import sys
if '--trace-only' in sys.argv:
 print(json.dumps(traces,indent=2));sys.exit(0)
rows=[]
for cohort in ['warm','confirm']:
 d=json.loads((ROOT/(cohort+'-comparison.json')).read_text())
 rows += [dict(cohort=cohort,**r) for r in d['rows'] if r['arm']=='remote-warm']
groups=collections.defaultdict(list)
for r in rows:groups[r['implementation']].append(r)
summary={k:dict(n=len(g),median_s=statistics.median(r['command_s'] for r in g),mean_s=statistics.mean(r['command_s'] for r in g),
 min_s=min(r['command_s'] for r in g),max_s=max(r['command_s'] for r in g),
 mean_bazel_cpu_s=statistics.mean(r['bazel_cpu_s'] for r in g),
 mean_heap_garbage_collected_bytes=statistics.mean(r['heap_garbage_collected_bytes'] for r in g)) for k,g in groups.items()}
(ROOT/'combined-warm.json').write_text(json.dumps(dict(summary=summary,rows=rows),indent=2)+'\n')
print(json.dumps(summary,indent=2))
