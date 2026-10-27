"""Stop only the owned excluded restore after its build errors are recorded."""
from skycache_all_common import *
import signal
import time

tag='local-matrix-512-heap14'
d=json.loads((ROOT/(tag+'-results.json')).read_text());r=d['runs'][-1]
assert d['status']=='running' and r['preparation'] and r['label']==tag+'-warmup-local-warm'
log=(ROOT/(r['label']+'.log')).read_text()
errors=[line for line in log.splitlines() if line.startswith('ERROR:') and 'failed to load template file' in line]
assert len(errors)>=2 and 'Build completed successfully' not in log
pid=int((OUT/'server/server.pid.txt').read_text());assert pid==r['server_pid']
needle=('--build_event_json_file='+str(ROOT/(r['label']+'.bep.json'))).encode()
clients=[]
for p in Path('/proc').iterdir():
    if not p.name.isdigit():continue
    try:argv=(p/'cmdline').read_bytes().split(b'\0')
    except (PermissionError,FileNotFoundError,ProcessLookupError):continue
    if argv and argv[0].decode(errors='replace')==str(CANDIDATE) and b'build' in argv and needle in argv:
        clients.append(int(p.name))
assert len(clients)==1,clients
record=dict(tag=tag,label=r['label'],java_heap_gib=14,wsl_limit_gib=20,metadata_budget_mib=512,
    errors=errors,client_pid=clients[0],server_pid=pid,reason='Confirmed missing generated inputs; stop excluded failed cleanup before the independent uncached baseline cohort.',signals=[])
path=ROOT/'local-restore-input-failure.json'
def send(target,sig):
    try:os.kill(target,sig)
    except ProcessLookupError:return
    record['signals'].append(dict(pid=target,signal=sig.name,epoch=time.time()));save(path,record)
def alive(target):
    try:
        txt=(Path('/proc')/str(target)/'stat').read_text()
        return txt.split(') ',1)[1].split()[0]!='Z'
    except (FileNotFoundError,ProcessLookupError):return False
save(path,record)
send(clients[0],signal.SIGINT)
for _ in range(15):
    if not alive(clients[0]):break
    time.sleep(1)
if alive(clients[0]):send(pid,signal.SIGTERM)
for _ in range(15):
    if not alive(pid):break
    time.sleep(1)
if alive(pid):send(pid,signal.SIGKILL)
print(json.dumps(record,indent=2))
