from pathlib import Path
import collections,gzip,json
R=Path('/home/jgett/REPOS/.work/datadog-ssd-fresh-2026-10-25')
assert json.loads((R/'results.json').read_text())['status']=='passed'
out={}
for name in ['r1-skycache-warm1','r2-skycache-warm1','r3-skycache-warm1','r2-skycache-cold','r2-vanilla-cold']:
 with gzip.open(R/(name+'.profile.gz'),'rt') as f:p=json.load(f)
 durations=collections.defaultdict(list)
 for e in p['traceEvents']:
  if e.get('ph')=='X' and 'dur' in e:durations[(e.get('cat',''),e['name'])].append(e['dur']/1e6)
 out[name]=[{'category':k[0],'name':k[1],'count':len(v),'total_seconds':sum(v),'max_seconds':max(v)} for k,v in sorted(durations.items(),key=lambda kv:sum(kv[1]),reverse=True)[:25]]
(R/'profile-observations.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out,indent=2))
