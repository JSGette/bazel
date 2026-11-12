#!/usr/bin/env python3
"""Reset only this experiment's ext4 mount; default is inspection, --apply mutates."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

IMAGE = Path('/mnt/d/Codex/shared-evaluation-20261107/work/runtime.ext4')
TARGET = Path('/mnt/shared-evaluation-20261107')
UUID = '05abc6fb-e13c-4e06-bd41-2cb6911dd802'


def task_mounts(text, device, major_minor):
    result = []
    for line in text.splitlines():
        fields = line.split()
        separator = fields.index('-')
        if fields[4] != str(TARGET) and fields[2] != major_minor:
            continue
        if (fields[4] != str(TARGET) or fields[2] != major_minor or fields[3] != '/'
                or fields[separator + 1:separator + 3] != ['ext4', device]):
            raise RuntimeError('Unexpected mount of task path/device: ' + line)
        result.append(line)
    if len(result) > 1:
        raise RuntimeError('Stacked task mounts are unsupported')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as root; namespace inspection must be complete')
    for path in (Path(__file__).resolve(), Path.cwd().resolve(), args.report.resolve()):
        if path == TARGET or TARGET in path.parents:
            parser.error('Script, cwd, and report must be outside the task mount')
    report = {'schema_version': 1, 'image': str(IMAGE), 'target': str(TARGET),
              'expected_uuid': UUID, 'apply': args.apply, 'commands': [], 'status': 'error'}
    descriptors = []
    started = time.monotonic()

    def run(*argv, pass_fds=(), record_output=True):
        argv = (('/usr/sbin/' if argv[0] in ('losetup', 'blkid') else '/usr/bin/')
                + argv[0], *argv[1:])
        proc = subprocess.run(argv, text=True, capture_output=True, timeout=20, pass_fds=pass_fds)
        row = {'argv': list(argv), 'exit_code': proc.returncode}
        if record_output:
            row.update(stdout=proc.stdout, stderr=proc.stderr)
        report['commands'].append(row)
        if proc.returncode:
            raise RuntimeError(f'{argv[0]} exited {proc.returncode}: {proc.stderr.strip()}')
        return proc.stdout.strip()

    def loops():
        return json.loads(run('losetup', '--list', '--json', '--output',
                              'NAME,BACK-FILE,MAJ:MIN,DIO,AUTOCLEAR'))['loopdevices']

    def namespaces(device, major_minor):
        seen, found = set(), []
        for process in sorted(Path('/proc').glob('[0-9]*'), key=lambda p: int(p.name)):
            try:
                fd = os.open(process / 'ns/mnt', os.O_RDONLY)
                inode = os.fstat(fd).st_ino
            except FileNotFoundError:
                continue
            if inode in seen:
                os.close(fd)
                continue
            seen.add(inode)
            descriptors.append(fd)  # Pin namespace, avoiding PID-exit/reuse races.
            contents = run('nsenter', f'--mount=/proc/self/fd/{fd}', '--',
                           '/usr/bin/cat', '/proc/self/mountinfo', pass_fds=(fd,), record_output=False)
            selected = task_mounts(contents, device, major_minor)
            if selected:
                found.append({'namespace_inode': inode, 'representative_pid': int(process.name),
                              'mountinfo': selected, 'fd': fd})
        return found

    # Exclusive creation prevents overwriting a prior reset's evidence.
    with args.report.open('x', encoding='utf-8') as output:
        try:
            if IMAGE.resolve() != IMAGE or not IMAGE.is_file():
                raise RuntimeError('Task image missing or symlinked')
            report['meminfo_before'] = Path('/proc/meminfo').read_text()
            before = [row for row in loops() if row['back-file'] == str(IMAGE)]
            if len(before) != 1:
                raise RuntimeError('Expected exactly one loop device attached to task image')
            device, major_minor = before[0]['name'], before[0]['maj:min']
            if run('blkid', '-s', 'UUID', '-o', 'value', device) != UUID:
                raise RuntimeError('Task filesystem UUID mismatch')
            mounted = namespaces(device, major_minor)
            if not mounted:
                raise RuntimeError('Task filesystem is not mounted in any visible namespace')
            report['loop_before'], report['namespaces_before'] = before[0], mounted
            if args.apply:
                first_fd = mounted[0]['fd']
                run('nsenter', f'--mount=/proc/self/fd/{first_fd}', '--', '/usr/bin/sync',
                    '-f', str(TARGET), pass_fds=(first_fd,))
                for entry in mounted:
                    fd = entry['fd']
                    contents = run('nsenter', f'--mount=/proc/self/fd/{fd}', '--', '/usr/bin/cat',
                                   '/proc/self/mountinfo', pass_fds=(fd,), record_output=False)
                    if task_mounts(contents, device, major_minor):
                        run('nsenter', f'--mount=/proc/self/fd/{fd}', '--', '/usr/bin/umount',
                            str(TARGET), pass_fds=(fd,))
                if namespaces(device, major_minor):
                    raise RuntimeError('Task mount remains; refusing loop detach/remount')
                remaining = [row for row in loops() if row['name'] == device]
                if remaining and remaining[0]['back-file'] != str(IMAGE):
                    raise RuntimeError('Loop device was reassigned; refusing detach')
                if remaining:
                    run('losetup', '--detach', device)
                if any(row['back-file'] == str(IMAGE) for row in loops()):
                    raise RuntimeError('Task loop did not detach')
                device = run('losetup', '--find', '--show', '--direct-io=on', str(IMAGE))
                if run('blkid', '-s', 'UUID', '-o', 'value', device) != UUID:
                    raise RuntimeError('Reattached filesystem UUID mismatch')
                run('mount', '-t', 'ext4', '-o', 'noatime', device, str(TARGET))
                after = [row for row in loops() if row['name'] == device]
                if len(after) != 1 or after[0]['back-file'] != str(IMAGE) or not after[0]['dio']:
                    raise RuntimeError('Reattached loop direct I/O was not enabled')
                report['loop_after'] = after[0]
                report['mount_after'] = json.loads(run('findmnt', '--json', '--mountpoint',
                    str(TARGET), '--output', 'SOURCE,FSTYPE,UUID,OPTIONS,TARGET'))
                final = report['mount_after']['filesystems']
                if (len(final) != 1 or final[0]['source'] != device or final[0]['uuid'] != UUID
                        or final[0]['fstype'] != 'ext4' or final[0]['target'] != str(TARGET)
                        or 'noatime' not in final[0]['options'].split(',')):
                    raise RuntimeError('Remounted task filesystem metadata mismatch')
            report['status'] = 'completed' if args.apply else 'inspected'
        except Exception as exc:
            report['error'] = str(exc)
        finally:
            report['meminfo_after'] = Path('/proc/meminfo').read_text()
            report['elapsed_seconds'] = time.monotonic() - started
            for fd in descriptors:
                os.close(fd)
            json.dump(report, output, indent=2)
            output.write('\n')
    print(json.dumps({'status': report['status'], 'report': str(args.report)}))
    return 0 if report['status'] in ('completed', 'inspected') else 1


if __name__ == '__main__':
    sys.exit(main())
