from pathlib import Path
import collections,json
ROOT=Path('/home/jgett/REPOS/.work/skycache-retention-2026-10-27')
def union(intervals):
 total=end=0
 for a,b in sorted(intervals):
  total+=max(0,b-max(a,end));end=max(end,b)
 return total/1e9
summary={}
for cohort in ['diagnostic-before-v2','diagnostic-after']:
 r=json.loads((ROOT/cohort/'results.json').read_text());assert r['status']=='passed'
 paths=list((ROOT/cohort).glob('r*-stages.jsonl'));assert len(paths)==1
 events=[json.loads(x) for x in paths[0].read_text().splitlines()]
 assert not any('transform_error' in e for e in events)
 groups=collections.defaultdict(list)
 for e in events:groups[e['method']].append(e)
 assert len(groups['RemoteChunkCache.store'])==1613
 if cohort=='diagnostic-before-v2':
  assert len(groups['store.file_force'])==len(groups['store.directory_force'])==1613
 else:
  assert 'store.file_force' not in groups and 'store.directory_force' not in groups
 summary[cohort]={k:dict(calls=len(v),worker_s=sum(e['duration_ns'] for e in v)/1e9,
  active_union_s=union([(e['begin_ns'],e['end_ns']) for e in v])) for k,v in groups.items()}
(ROOT/'trace-comparison.json').write_text(json.dumps(dict(summary=summary,
 note='Normal-return diagnostic timings; nested and concurrent intervals cannot be added. Active union is time with at least one call active, not critical-path attribution.'),indent=2)+'\n')
print(json.dumps(summary,indent=2))
