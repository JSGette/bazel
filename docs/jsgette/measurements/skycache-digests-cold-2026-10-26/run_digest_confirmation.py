from pathlib import Path
import json,subprocess,time,sys
root=Path('/home/jgett/REPOS/.work/skycache-digests-2026-10-26')
work=Path(__file__).parent
deadline=time.monotonic()+3600
while True:
 p=root/'diagnostic/results.json'
 if p.exists():
  data=json.loads(p.read_text())
  if data['status']=='passed' and 'source_states_unchanged' in data:break
  assert data['status']!='failed',data.get('failure')
 assert time.monotonic()<deadline,'diagnostics did not finish'
 time.sleep(5)
command=[sys.executable,str(work/'skycache_digest_benchmark.py'),'--root',str(root/'confirm'),'--cohort','confirm',
 '--candidate',str(root/'build-candidate/bazel'),'--candidate128','/home/jgett/REPOS/.work/skycache-channels-2026-10-26/build-128-v2/bazel']
subprocess.run(command,check=True)
subprocess.run([sys.executable,str(work/'analyze_digests.py'),'confirm'],check=True)
