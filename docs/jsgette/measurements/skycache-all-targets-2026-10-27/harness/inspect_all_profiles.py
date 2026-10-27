"""Read completed profiles only; report inclusive categories without adding overlaps."""
from skycache_all_common import *
import collections
import gzip
import sys

tag=json.loads((ROOT/'pipeline-status.json').read_text())['cohort']
data=json.loads((ROOT/(tag+'-results.json')).read_text())
for r in data['runs']:
    if 'wall_s' not in r or r['exit_code']!=0:continue
    if len(sys.argv)>1 and r['label']!=sys.argv[1]:continue
    path=ROOT/(r['label']+'.profile.gz')
    events=json.loads(gzip.open(path,'rt').read()).get('traceEvents',[])
    totals=collections.defaultdict(lambda:[0,0,0])
    for e in events:
        if e.get('ph')!='X':continue
        key=(e.get('cat',''),e.get('name',''))
        totals[key][0]+=e.get('dur',0)/1e6
        totals[key][1]+=1
        totals[key][2]=max(totals[key][2],e.get('dur',0)/1e6)
    top=sorted(totals.items(),key=lambda kv:kv[1][0],reverse=True)[:18]
    print(json.dumps(dict(label=r['label'],wall_s=r['wall_s'],cpu_s=r['client_process_delta'].get('cpu_s'),
        analysis_hits=r.get('analysis_hits'),execution_hits=r.get('execution_hits'),
        local=r.get('local-skycache'),validation=r.get('local-skycache-validation'),
        memory=r['build_metrics'].get('memoryMetrics'),swap=r.get('swap_pages_delta'),
        phase_markers=[{k:e.get(k) for k in ['name','ts','dur']} for e in events if e.get('cat')=='build phase marker'],
        top_inclusive_categories=[dict(category=k[0],name=k[1],seconds=v[0],calls=v[1],max_seconds=v[2]) for k,v in top]),indent=2))
