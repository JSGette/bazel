import importlib.util,subprocess,json,shutil,fcntl
from pathlib import Path
s=importlib.util.spec_from_file_location('wide',Path(__file__).with_name('datadog-wide.py'));w=importlib.util.module_from_spec(s);s.loader.exec_module(w)
h=w.h
r=h.ROOT=Path('/home/jgett/REPOS/.work/datadog-wide-dependencies-2026-10-25')
lock=(r.parent/'skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
r.mkdir(exist_ok=False);shutil.copy2(h.WS/'MODULE.bazel.lock',r/'MODULE.bazel.lock.before')
h.BINARY=w.VANILLA
args=[a for a in w.command('resolve','toplevel','vanilla') if not a.startswith(('--build_event_json_file=','--profile=','--remote_grpc_log=','--lockfile_mode='))]
args+=['--nobuild','--lockfile_mode=update','--build_event_json_file='+str(r/'resolve.bep.json')]
h.save(r/'command.json',{'argv':args,'before_lock_sha256':h.sha(h.WS/'MODULE.bazel.lock')})
try:
 h.simple(args,'resolve',1800)
finally:
 shutil.copy2(h.WS/'MODULE.bazel.lock',r/'MODULE.bazel.lock.after')
 h.shutdown('shutdown')
 lock.close()
