"""One explicit GC during excluded setup, after discard_analysis_cache has run."""
from skycache_all_common import *
import time
import sys
tag=sys.argv[1] if len(sys.argv)>1 else 'action-prime-2'
d=json.loads((ROOT/(tag+'-results.json')).read_text())
assert d['status']=='running'
r=d['runs'][-1]
assert r['preparation'] and r['arm']=='disabled'
assert '--discard_analysis_cache' in r['argv']
assert 'INFO: Analyzed ' in (ROOT/(r['label']+'.log')).read_text()
pid=r['server_pid']
assert str(OUT).encode() in (Path('/proc')/str(pid)/'cmdline').read_bytes()
with (ROOT/(tag+'-heap-trim.log')).open('x') as log:
    for cmd in ['GC.heap_info','GC.run','GC.heap_info']:
        log.write(cmd+'\n');log.flush()
        subprocess.run([str(JDK/'bin/jcmd'),str(pid),cmd],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
print((ROOT/(tag+'-heap-trim.log')).read_text())
