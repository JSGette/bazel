from pathlib import Path
import collections,json,subprocess
root=Path('/home/jgett/REPOS/.work/skycache-digests-2026-10-26')
jfr='/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin/jfr'
result={}
for label in ['r3-remote-warm-channels','r4-remote-warm-scratch']:
 raw=subprocess.check_output([jfr,'print','--json','--events','jdk.JavaMonitorEnter',str(root/'diagnostic'/(label+'.jfr'))])
 (root/'diagnostic'/(label+'-monitors.json')).write_bytes(raw)
 events=json.loads(raw)['recording']['events'];relevant=[]
 for e in events:
  v=e['values'];stack=v.get('stackTrace') or {};frames=stack.get('frames') or []
  names=[f['method']['type']['name']+'.'+f['method']['name'] for f in frames]
  if any('RemoteSkyCache' in name for name in names):
   relevant.append(dict(duration=v['duration'],monitor=v.get('monitorClass',{}).get('name'),frames=names))
 result[label]=dict(total_events=len(events),remote_events=relevant)
(root/'monitor-comparison.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
