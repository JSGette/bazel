"""One diagnostic snapshot during the excluded warmup, never a primary sample."""
from skycache_all_common import *
import time
import sys

tag=sys.argv[1] if len(sys.argv)>1 else 'local-matrix'
d=json.loads((ROOT/(tag+'-results.json')).read_text())
r=d['runs'][-1]
assert d['status']=='running' and r['label']==tag+'-warmup-local-warm'
assert r['preparation'] and 'exit_code' not in r
pid=int((OUT/'server/server.pid.txt').read_text())
assert pid==r['server_pid']
basename='local-warmup-thread-dump' if tag=='local-matrix' else tag+'-warmup-thread-dump'
output=ROOT/(basename+'.txt')
assert not output.exists()
start=time.clock_gettime(time.CLOCK_MONOTONIC_RAW)
with output.open('x') as f:
    result=subprocess.run([str(JDK/'bin/jcmd'),str(pid),'Thread.print','-l'],stdout=f,stderr=subprocess.STDOUT,timeout=30)
save(ROOT/(basename+'.json'),dict(pid=pid,label=r['label'],exit_code=result.returncode,
    duration_s=time.clock_gettime(time.CLOCK_MONOTONIC_RAW)-start,excluded_warmup=True,sha256=sha(output)))
blocks=output.read_text().split('\n\n')
matching=[b for b in blocks if 'LocalDependencyValidator' in b or 'LocalCacheSession' in b or 'SegmentedFingerprintValueStore' in b]
print(json.dumps(dict(matching_threads=len(matching),total_sections=len(blocks))))
for b in matching[:6]:print(b[:3000]+'\n')
