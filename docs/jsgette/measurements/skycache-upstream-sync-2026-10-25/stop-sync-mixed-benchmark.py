from pathlib import Path
import os,signal,json,time
R=Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25')
p=Path('/proc/72887/cmdline');cmd=p.read_bytes().split(b'\0')
assert any(x.endswith(b'/benchmark-upstream-sync.py') for x in cmd),cmd
(R/'datadog-six/stop-reason.json').write_text(json.dumps(dict(time=time.time(),reason='Stop interleaved premerge/upstream experiment: premerge reader after upstream switch configured 236112 targets and wrote 406980 records. It is not the steady full-hit baseline sought for the budget investigation. Preserve all evidence; rerun merged/on-off/upstream controls without interleaving binaries from the old upstream tree.',signal='SIGINT',pid=72887),indent=2)+'\n')
os.kill(72887,signal.SIGINT)
