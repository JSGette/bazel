"""One exact task-image expansion; preserves all filesystem contents."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
from datetime import datetime, timezone

image = Path('/mnt/d/Codex/shared-evaluation-20261107/work/runtime.ext4')
mount = Path('/mnt/shared-evaluation-20261107')
uuid = '05abc6fb-e13c-4e06-bd41-2cb6911dd802'
evidence = Path('/mnt/c/Users/jgett/Documents/Codex/2026-11-07/i-ha/work/bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/evidence')
report_path = evidence / 'task-image-growth-512.json'
assert not report_path.exists()
assert os.geteuid() == 0
for arm in ('off', 'on'):
    guard = json.loads((evidence / f'auxiliary/m0-diagnostic-{arm}-01/guard/result.json').read_text())
    assert guard['status'] == 'completed' and guard['exit_code'] == 0
    assert guard['final_scope_cleanup']['verified'] and not guard['final_scope_cleanup']['present']
assert not list(Path('/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice').glob('shared-eval-guard-*.scope'))
assert image.resolve(strict=True) == image and stat.S_ISREG(image.lstat().st_mode)
before_size = image.stat().st_size
assert before_size == 256 * 1024**3
target_size = 512 * 1024**3
host_free = shutil.disk_usage('/mnt/d').free
assert host_free > (target_size - before_size) + 100 * 1024**3
fs = json.loads(subprocess.check_output(['findmnt', '-J', '-T', str(mount), '-o', 'TARGET,SOURCE,FSTYPE,OPTIONS,UUID'], text=True))['filesystems']
assert len(fs) == 1 and fs[0]['target'] == str(mount) and fs[0]['fstype'] == 'ext4' and fs[0]['uuid'] == uuid
device = fs[0]['source']
assert device.startswith('/dev/loop')
loops = json.loads(subprocess.check_output(['losetup', '-J', '-O', 'NAME,BACK-FILE,AUTOCLEAR,DIO'], text=True))['loopdevices']
matches = [row for row in loops if row['name'] == device]
assert len(matches) == 1 and matches[0]['back-file'] == str(image) and matches[0]['dio'] is True
before = os.statvfs(mount)
report = {'status': 'starting', 'utc': datetime.now(timezone.utc).isoformat(), 'image': str(image),
          'mount': str(mount), 'uuid': uuid, 'device': device, 'before_image_bytes': before_size,
          'target_image_bytes': target_size, 'backing_free_bytes_before': host_free,
          'filesystem_free_bytes_before': before.f_bavail * before.f_frsize,
          'contents_deleted': False, 'commands': []}
with report_path.open('x') as out:
    json.dump(report, out, indent=2)
try:
    os.truncate(image, target_size)
    for argv in (['losetup', '--set-capacity', device], ['resize2fs', device]):
        run = subprocess.run(argv, capture_output=True, text=True, timeout=300)
        report['commands'].append({'argv': argv, 'returncode': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr})
        run.check_returncode()
    after = os.statvfs(mount)
    report.update(status='completed', after_image_bytes=image.stat().st_size,
                  filesystem_bytes_after=after.f_blocks * after.f_frsize,
                  filesystem_free_bytes_after=after.f_bavail * after.f_frsize)
    assert report['filesystem_bytes_after'] > before.f_blocks * before.f_frsize
finally:
    report_path.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
