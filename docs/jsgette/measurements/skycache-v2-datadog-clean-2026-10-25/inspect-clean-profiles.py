from pathlib import Path
import collections,gzip,json
R=Path('/home/jgett/REPOS/.work/datadog-clean-2026-10-25')
p=json.loads((R/'results.json').read_text())
out={}
for row in p['runs']:
 if row.get('exit_code')!=0:continue
 name=row['label']
 with gzip.open(R/(name+'.profile.gz'),'rt') as f:profile=json.load(f)
 durations=collections.defaultdict(list)
 for e in profile['traceEvents']:
  if e.get('ph')=='X' and 'dur' in e and e.get('cat')=='general information' and any(s in e.get('name','').lower() for s in ['local','skycache','analysis','deserialize','serialize']):
   durations[(e.get('cat',''),e['name'])].append(e['dur']/1e6)
 out[name]=[{'category':k[0],'name':k[1],'count':len(v),'summed_seconds':sum(v),'max_seconds':max(v)} for k,v in sorted(durations.items(),key=lambda kv:sum(kv[1]),reverse=True)[:30]]
if p['status']=='passed':(R/'profile-observations.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out,indent=2))
