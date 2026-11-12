"""Close only this task's mount, attached image, and verified keeper process."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

W = Path('/mnt/c/Users/jgett/Documents/Codex/2026-11-07/i-ha/work')
target = '/mnt/shared-evaluation-20261107'
image = '/mnt/d/Codex/shared-evaluation-20261107/work/runtime.ext4'
out = W / 'residual-runtime-close.json'
assert os.geteuid() == 0 and not out.exists()
keeper = json.loads((W / 'residual-mount-restore.json').read_text())
report = {'target': target, 'image': image, 'commands': [], 'status': 'error'}
fds = []

def run(*argv, pass_fds=()):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=30, pass_fds=pass_fds)
    report['commands'].append({'argv': argv, 'exit_code': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()

def mounts(major_minor):
    seen, found = set(), []
    for p in sorted(Path('/proc').glob('[0-9]*'), key=lambda p: int(p.name)):
        fd = None
        try:
            fd = os.open(p / 'ns/mnt', os.O_RDONLY)
            inode = os.fstat(fd).st_ino
            if inode in seen:
                os.close(fd)
                continue
            # Read via the host procfs; a sandbox need not mount its own /proc.
            contents = (p / 'mountinfo').read_text()
            assert (p / 'ns/mnt').stat().st_ino == inode, 'Namespace changed during inspection'
            matches = []
            for line in contents.splitlines():
                fields = line.split()
                if fields[4] == target or fields[2] == major_minor:
                    separator = fields.index('-')
                    assert fields[4] == target and fields[2] == major_minor and fields[3] == '/'
                    assert fields[separator+1:separator+3] == ['ext4', '/dev/loop0']
                    matches.append(line)
            assert len(matches) <= 1, 'Stacked task mounts unsupported'
            seen.add(inode)
            if matches:
                found.append({'pid': int(p.name), 'namespace_inode': inode, 'mountinfo': matches, 'fd': fd})
                fds.append(fd)
            else:
                os.close(fd)
        except FileNotFoundError:
            if fd is not None:
                os.close(fd)
    return found

try:
    loops = json.loads(run('/usr/sbin/losetup', '--list', '--json', '--output', 'NAME,BACK-FILE,MAJ:MIN,DIO,AUTOCLEAR'))['loopdevices']
    selected = [row for row in loops if row['back-file'] == image]
    assert len(selected) == 1 and selected[0]['name'] == '/dev/loop0'
    device = selected[0]
    report['loop_before'] = device
    assert run('/usr/sbin/blkid', '-s', 'UUID', '-o', 'value', device['name']) == '05abc6fb-e13c-4e06-bd41-2cb6911dd802'
    owned_scopes = list(Path('/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice').glob('shared-eval-guard-m1-residual-decode-*'))
    assert not owned_scopes, 'Residual guard still exists'
    report['residual_guard_scopes'] = []
    rows = mounts(device['maj:min'])
    current_ns = Path('/proc/self/ns/mnt').stat().st_ino
    rows.sort(key=lambda row: row['namespace_inode'] != current_ns)
    report['mounts_before'] = rows
    run('/usr/bin/sync', '-f', target)
    for row in rows:
        fd = row['fd']
        run('/usr/bin/nsenter', f'--mount=/proc/self/fd/{fd}', '--', '/usr/bin/umount', target, pass_fds=(fd,))
    remaining = mounts(device['maj:min'])
    report['mounts_after'] = remaining
    assert not remaining, 'Task mount remains; leave device attached'
    now = json.loads(run('/usr/sbin/losetup', '--list', '--json', '--output', 'NAME,BACK-FILE'))['loopdevices']
    assert any(row['name'] == device['name'] and row['back-file'] == image for row in now)
    run('/usr/sbin/losetup', '--detach', device['name'])
    after = json.loads(run('/usr/sbin/losetup', '--list', '--json', '--output', 'NAME,BACK-FILE'))['loopdevices']
    assert not any(row['back-file'] == image for row in after)
    report['task_loop_attached_after'] = False
    proc = Path('/proc') / str(keeper['pid'])
    if proc.exists():
        ticks = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
        assert ticks == keeper['start_ticks'] and 'restore_residual_mount.py' in (proc / 'cmdline').read_text()
        os.kill(keeper['pid'], signal.SIGTERM)
        report['keeper_signal'] = 'SIGTERM to verified task keeper only'
        for _ in range(30):
            if not proc.exists():
                break
            time.sleep(0.1)
    report['keeper_absent'] = not proc.exists()
    assert report['keeper_absent']
    report['image_preserved_bytes'] = Path(image).stat().st_size
    report['status'] = 'closed_task_runtime_image_preserved'
finally:
    for fd in fds:
        os.close(fd)
    with out.open('x') as f:
        json.dump(report, f, indent=2)
        f.write('\n')
print(json.dumps({'status': report['status'], 'report': str(out)}))
