#!/usr/bin/env python3
"""Verify idle eviction after a cold JVM imports analysis without publishing new records.

Uses a completed idle_retention_benchmark.py fixture, whose outputs were already
checked against independently generated expected bytes. Restarts only its server.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--bazel', type=Path, required=True)
    args = parser.parse_args()
    fixture = args.fixture.resolve()
    previous = json.loads((fixture / 'results.json').read_text())
    assert previous['status'] == 'passed'
    root = args.root.resolve()
    root.mkdir(exist_ok=False)
    command = previous['attempts'][0]['argv']
    command[0] = str(args.bazel.resolve())
    output = Path(next(x.split('=', 1)[1] for x in command if x.startswith('--output_base=')))
    links = Path(next(x.split('=', 1)[1] for x in command if x.startswith('--symlink_prefix=')))
    assert output.is_relative_to(fixture) and links.is_relative_to(fixture)
    artifact = Path(str(links) + 'bin/app/all.txt')
    expected = previous['attempts'][-1]['sha256']
    base = command[:command.index('build')]
    command = [x for x in command if not x.startswith('--experimental_analysis_cache_idle_retention=')]
    receipt = output / 'skycache-idle-retention.json'
    report = {'kind': 'cold_import_idle_correctness', 'attempts': [],
              'binary_sha256': hashlib.sha256(args.bazel.read_bytes()).hexdigest()}

    def save():
        (root / 'results.json').write_text(json.dumps(report, indent=2) + '\n')

    def shutdown():
        pidfile = output / 'server/server.pid.txt'
        pid = int(pidfile.read_text()) if pidfile.exists() else None
        try:
            subprocess.run(base + ['shutdown'], cwd=fixture / 'workspace',
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12)
        except subprocess.TimeoutExpired:
            status = Path(f'/proc/{pid}/stat') if pid else None
            if status and status.exists() and status.read_text().split()[2] != 'Z':
                raise RuntimeError('Fixture server did not stop')

    try:
        shutdown()
        receipt.unlink(missing_ok=True)
        for index in range(2):
            argv = command + [f'--experimental_analysis_cache_idle_retention={"2s" if index == 0 else "0"}']
            row = {'role': 'cold_import' if index == 0 else 'post_idle_recovery', 'argv': argv}
            report['attempts'].append(row)
            save()
            log_path = root / (row['role'] + '.log')
            with log_path.open('w') as log:
                result = subprocess.run(argv, cwd=fixture / 'workspace', stdout=log,
                                        stderr=subprocess.STDOUT, timeout=300)
            row['exit_code'] = result.returncode
            row['skycache'] = json.loads((output / 'local-skycache.json').read_text())
            row['analysis_hits'] = sum(int(a) for a, _ in re.findall(
                r'CONFIGURED_TARGET: (\d+)/(\d+)', log_path.read_text()))
            row['sha256'] = hashlib.sha256(artifact.read_bytes()).hexdigest()
            row['pid'] = int((output / 'server/server.pid.txt').read_text())
            save()
            assert result.returncode == 0 and row['sha256'] == expected
            assert row['analysis_hits'] > 0 and not row['skycache']['published']
            assert row['skycache']['writes'] == 0 and not row['skycache']['failed']
            if index == 0:
                end = time.monotonic() + 30
                while not receipt.exists() and time.monotonic() < end:
                    time.sleep(0.1)
                report['eviction'] = json.loads(receipt.read_text())
                assert report['eviction']['evicted']
            else:
                assert row['pid'] == report['attempts'][0]['pid']
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = repr(error)
        raise
    finally:
        save()
        shutdown()


if __name__ == '__main__':
    main()
