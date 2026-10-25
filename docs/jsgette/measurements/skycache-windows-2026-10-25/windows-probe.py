"""Native Windows SkyCache verification utilities; never uses WSL paths."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, os, platform, subprocess, time, threading
import psutil

ROOT = Path('E:/scw')
LOG = ROOT / 'evidence'
LOG.mkdir(exist_ok=True)

def sha(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def save(p, v):
    Path(p).write_text(json.dumps(v, indent=2) + '\n', encoding='utf-8')

def run(label, argv, cwd=ROOT, env=None, extra_pids=(), output_base=None):
    assert not any('wsl' in str(a).lower() for a in argv), argv
    result = {'label': label, 'argv': list(map(str, argv)), 'cwd': str(cwd),
              'started_at': datetime.now(timezone.utc).isoformat()}
    samples = []
    with (LOG / (label + '.log')).open('x', encoding='utf-8') as f:
        start = time.perf_counter()
        p = subprocess.Popen(argv, cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT)
        while True:
            members = {}
            server_pids=[]
            if output_base:
                try:
                    server_pids=[int((Path(output_base)/'server/server.pid.txt').read_text())]
                except (OSError, ValueError):
                    pass
            for pid in (p.pid, *extra_pids, *server_pids):
                try:
                    parent = psutil.Process(pid)
                    for q in [parent, *parent.children(recursive=True)]:
                        members[q.pid] = q
                except psutil.Error:
                    pass
            rss = private = 0
            for q in members.values():
                try:
                    m = q.memory_info()
                    rss += m.rss
                    private += m.private
                except psutil.Error:
                    pass
            samples.append([time.perf_counter() - start, rss, private, len(members)])
            if p.poll() is not None:
                break
            time.sleep(.2)
        result.update(exit_code=p.returncode, seconds=time.perf_counter()-start,
                      peak_tree_working_set_bytes=max(x[1] for x in samples),
                      peak_tree_private_bytes=max(x[2] for x in samples))
    save(LOG/(label+'.samples.json'), samples)
    save(LOG/(label+'.result.json'), result)
    print(json.dumps(result), flush=True)
    return result

if __name__ == '__main__':
    save(LOG/'host.json', {'platform': platform.platform(), 'python': platform.python_version(),
         'logical_cpus': psutil.cpu_count(), 'physical_cpus': psutil.cpu_count(logical=False),
         'ram': psutil.virtual_memory()._asdict(), 'disks': {d: psutil.disk_usage(d)._asdict() for d in ['C:/','D:/','E:/']},
         'method': 'Fresh GitHub clones on E: NTFS. Native Windows processes. No WSL paths or binaries.'})
