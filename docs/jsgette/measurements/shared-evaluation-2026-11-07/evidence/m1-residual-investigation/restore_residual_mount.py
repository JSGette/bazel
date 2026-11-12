"""Restore only the detached, existing experiment image after WSL restarted."""
import json
import os
from pathlib import Path
import subprocess
import time

image = Path('/mnt/d/Codex/shared-evaluation-20261107/work/runtime.ext4')
target = Path('/mnt/shared-evaluation-20261107')
output = Path('/mnt/c/Users/jgett/Documents/Codex/2026-11-07/i-ha/work/residual-mount-restore.json')
expected_uuid = '05abc6fb-e13c-4e06-bd41-2cb6911dd802'
assert os.geteuid() == 0 and not output.exists()
assert image.is_file() and image.resolve() == image and image.stat().st_size == 549755813888
report = {'image': str(image), 'target': str(target), 'expected_uuid': expected_uuid,
          'pid': os.getpid(), 'start_ticks': Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19], 'commands': []}

def run(*argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    report['commands'].append({'argv': argv, 'exit_code': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()

try:
    loops = json.loads(run('/usr/sbin/losetup', '--list', '--json', '--output', 'NAME,BACK-FILE,DIO,AUTOCLEAR'))['loopdevices']
    assert not any(row['back-file'] == str(image) for row in loops), 'Image is already attached'
    mounts = Path('/proc/self/mountinfo').read_text().splitlines()
    assert not any(line.split()[4] == str(target) for line in mounts), 'Target already mounted'
    assert target.is_dir() and target.resolve() == target and not any(target.iterdir()), 'Mountpoint must exist and be empty'
    device = run('/usr/sbin/losetup', '--find', '--show', '--direct-io=on', str(image))
    assert run('/usr/sbin/blkid', '-s', 'UUID', '-o', 'value', device) == expected_uuid
    run('/usr/bin/mount', '-t', 'ext4', '-o', 'noatime', device, str(target))
    report['mount'] = json.loads(run('/usr/bin/findmnt', '--json', '--mountpoint', str(target), '--output', 'SOURCE,FSTYPE,UUID,OPTIONS,TARGET'))
    row = report['mount']['filesystems'][0]
    assert row['source'] == device and row['uuid'] == expected_uuid and row['fstype'] == 'ext4'
    report['status'] = 'restored_existing_image_no_data_reset'
finally:
    with output.open('x') as f:
        json.dump(report, f, indent=2)
        f.write('\n')
print(json.dumps({'status': report['status'], 'keeper_pid': os.getpid(), 'report': str(output)}), flush=True)
while True:
    time.sleep(60)
