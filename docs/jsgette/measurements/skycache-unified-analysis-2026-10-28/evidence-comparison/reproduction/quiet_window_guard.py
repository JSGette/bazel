from pathlib import Path
import fcntl,os,signal,json,time,datetime
b=Path('/home/jgett/REPOS/.work/skycache-analysis-foundation-evidence')
lockpath=Path('/home/jgett/skyframe-upstream-20261028/trial-output/.benchmark.lock')
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
record={'purpose':'Reserve peer benchmark advisory lock while our host lock serializes our native runs; no peer process control','started_utc':now(),'lock_path':str(lockpath),'status':'waiting'}
def save(): (b/'quiet-window.json').write_text(json.dumps(record,indent=2)+'\n')
save()
with lockpath.open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 st=os.fstat(lock.fileno());assert st.st_ino==lockpath.stat().st_ino
 record.update(status='held',acquired_utc=now(),device=st.st_dev,inode=st.st_ino);save();print('Quiet window lock acquired',flush=True)
 pid=422112
 p=Path('/proc')/str(pid)
 if p.exists():
  args=p.joinpath('cmdline').read_bytes().decode().split('\0')
  assert args[1]=='/home/jgett/REPOS/.work/skycache-analysis-foundation-2026-10-28/docs/jsgette/measurements/skycache-unified-analysis-2026-10-28/compare_datadog.py'
  assert str(b/'datadog-three-arm-01') in args
  os.kill(pid,signal.SIGCONT)
  record.update(our_dispatcher_resumed_pid=pid,resumed_utc=now());save()
 started=time.monotonic()
 while not (b/'release-quiet-window').exists() and time.monotonic()-started<5400:
  time.sleep(5)
 record.update(status='released',released_utc=now(),reason='requested' if (b/'release-quiet-window').exists() else 'bounded guard duration elapsed');save()
print('Quiet window lock released',flush=True)
