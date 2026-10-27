"""Capture the new full-graph heap failure before the owned JVM exits."""
from pathlib import Path
import hashlib, json, subprocess, time

root=Path('/home/jgett/REPOS/.work/skycache-restore-limits-2026-10-27/validation')
out=Path('/home/jgett/REPOS/.work/skycache-large-remote-2026-10-25/agent/output-base')
result=json.loads((root/'fresh-restore-results.json').read_text())
pid=result['runs'][0]['server_pid']
log=out/'java.log'
with log.open('rb') as f:
    f.seek(max(0,log.stat().st_size-512*1024));tail=f.read()
client=(root/'fresh-restore-local-warm.log').read_text()
assert 'OutOfMemoryError: Java heap space' in client
(root/'fresh-restore-oom-jvm-tail.log').write_bytes(tail)
status=Path(f'/proc/{pid}/status')
probe=dict(pid=pid,captured_epoch=time.time(),status=status.read_text() if status.exists() else None,
    memory=Path('/proc/meminfo').read_text())
import re
probe['maximum_progress_analysis_hits']=max(int(n.replace(',','')) for n in re.findall(r'targets configured \(([\d,]+) remote cache hits\)',client))
probe['exit_code']=result['runs'][0].get('exit_code')
probe['failure']='OutOfMemoryError: Java heap space'
probe['jvm_tail_contains_original_oom']=b'OutOfMemoryError: Java heap space' in tail
base=out/'execroot/_main/bazel-out/k8-fastbuild/bin/pkg/security'
files=['secl/model/accessors_windows/accessors_windows.go','secl/model/accessors_windows/field_handlers_windows.go',
       'seclwin/model/accessors_windows.go','seclwin/model/field_handlers_windows.go']
probe['generated_inputs_and_outputs']=[dict(path=str(base/name),size=(base/name).stat().st_size,
    sha256=hashlib.file_digest((base/name).open('rb'),'sha256').hexdigest()) for name in files]
with (root/'fresh-restore-heap-histogram.txt').open('w') as f:
    try:
        if status.exists():
            r=subprocess.run(['/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin/jcmd',str(pid),'GC.class_histogram'],stdout=f,stderr=subprocess.STDOUT,timeout=60)
            probe['histogram_exit_code']=r.returncode
        else:
            probe['histogram_unavailable']='The JVM exited before the histogram could be collected.'
    except subprocess.TimeoutExpired:
        probe['histogram_timed_out']=True
(root/'fresh-restore-oom.json').write_text(json.dumps(probe,indent=2)+'\n')
print(json.dumps({k:v for k,v in probe.items() if k not in ['status','memory']}),flush=True)
