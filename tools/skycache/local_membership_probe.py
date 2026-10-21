#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Verify repeated edits and glob membership across two cached worktrees and controls.

Small correctness fixture, not a performance benchmark. Every action-cache backend is disabled.
The output contains both input paths and bytes, so a rename with identical contents is observable.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from storage_preflight import check_storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bazel', required=True, type=Path)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--lock-file', required=True, type=Path)
    args = parser.parse_args()
    binary = args.bazel.resolve(strict=True)
    root = args.root.resolve()
    names = ('a', 'b', 'control-a', 'control-b')
    count = 6
    expected_pids = {}
    started_servers = set()
    protocol = {
        'binary': str(binary), 'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'purpose': 'Correctness of file bytes and glob additions/deletions/renames; not speed measurement.',
        'action_caches': 'Remote and disk action caches disabled for every build.',
        'phases': [], 'passed': False,
    }

    def save():
        (root / 'results.json').write_text(json.dumps(protocol, indent=2) + '\n')

    def git(workspace, *argv):
        return subprocess.check_output(['git', '-C', str(workspace), *argv], stderr=subprocess.PIPE, text=True)

    def startup(name):
        return [str(binary), '--nosystem_rc', '--nohome_rc', '--noworkspace_rc',
                '--output_user_root=' + str(root / 'user-root'),
                '--host_jvm_args=-Xmx1g', '--max_idle_secs=3600',
                '--output_base=' + str(root / ('out-' + name))]

    def environment(name, phase):
        return dict(os.environ, PWD=str(root / name), SKYCACHE_UNRELATED_SESSION=f'{name}-{phase}')

    def shutdown(name):
        subprocess.run(startup(name) + ['shutdown'], cwd=root / name,
                       env=environment(name, 'shutdown'), stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True, timeout=90)
        expected_pids.pop(name, None)

    def restart_cached():
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(shutdown, ('a', 'b')))

    def expected(name):
        workspace = root / name
        paths = [p for i in range(count)
                 for p in sorted((workspace / f'lib{i:02d}/inputs').glob('*.txt'))]
        return b''.join((str(p.relative_to(workspace)) + '\n').encode() + p.read_bytes()
                        for p in paths)

    def build(name, label):
        cached = not name.startswith('control-')
        output_base = root / ('out-' + name)
        argv = startup(name) + ['build', '//app:all', '--jobs=2', '--local_resources=memory=1024',
            '--lockfile_mode=off', '--experimental_strict_repo_env', '--color=no', '--curses=no',
            '--remote_cache=', '--disk_cache=', '--repository_cache=' + str(root / 'repository-cache'),
            '--experimental_active_directories=app',
            '--experimental_local_skycache=' + (str(root / 'cache') if cached else '')]
        log = root / f'{label}-{name}.log'
        started = time.monotonic()
        with log.open('w') as stream:
            completed = subprocess.run(argv, cwd=root / name, env=environment(name, label),
                stdout=stream, stderr=subprocess.STDOUT, timeout=300)
        text = log.read_text()
        stats = re.findall(r'Local SkyCache: (\{[^\n]+\})', text)
        wanted = expected(name)
        actual = (root / name / 'bazel-bin/app/all.txt').read_bytes() if completed.returncode == 0 else None
        row = {'worktree': name, 'argv': argv, 'exit_code': completed.returncode,
            'seconds': time.monotonic() - started, 'cache_enabled': cached,
            'analysis_hits': sum(int(x) for x in re.findall(r'CONFIGURED_TARGET: (\d+)/\d+', text)),
            'expected_sha256': hashlib.sha256(wanted).hexdigest(),
            'output_sha256': hashlib.sha256(actual).hexdigest() if actual is not None else None,
            'output_matches_expected': actual == wanted,
            'disabled_warning': 'Local SkyCache disabled:' in text,
            'publication_warning': 'Local SkyCache publication ' in text,
            'inputs': [str(p.relative_to(root / name)) for p in
                       sorted((root / name / 'lib00/inputs').glob('*.txt'))]}
        if stats:
            row['skycache'] = json.loads(stats[-1])
        if completed.returncode == 0:
            row['server_pid'] = (output_base / 'server/server.pid.txt').read_text().strip()
        return row

    def phase(label, *, controls=True, require_hits=False):
        storage = check_storage([root])
        rows = []
        for pair in (('a', 'b'), ('control-a', 'control-b')) if controls else (('a', 'b'),):
            started_servers.update(pair)
            with ThreadPoolExecutor(max_workers=2) as pool:
                rows.extend(f.result() for f in [pool.submit(build, n, label) for n in pair])
        protocol['phases'].append({'phase': label, 'storage_preflight': storage, 'builds': rows})
        save()
        print(json.dumps({'phase': label, 'builds': [{k: r.get(k) for k in
            ('worktree', 'exit_code', 'output_matches_expected', 'analysis_hits', 'seconds')} for r in rows]}), flush=True)
        for row in rows:
            name = row['worktree']
            assert row['exit_code'] == 0 and row['output_matches_expected'], row
            assert not row['disabled_warning'] and not row['publication_warning'], row
            assert ('skycache' in row) == row['cache_enabled'], row
            if row['cache_enabled']:
                assert not row['skycache']['failed'], row
                if require_hits:
                    assert row['analysis_hits'] > 0 and row['skycache']['shared_reads'] > 0, row
            else:
                assert row['analysis_hits'] == 0, row
            if name in expected_pids:
                assert row['server_pid'] == expected_pids[name], ('unexpected restart', row)
            expected_pids[name] = row['server_pid']
        if controls:
            for side in ('a', 'b'):
                assert next(r['output_sha256'] for r in rows if r['worktree'] == side) == next(
                    r['output_sha256'] for r in rows if r['worktree'] == 'control-' + side)

    def mutate(action):
        for name in names:
            directory = root / name / 'lib00/inputs'
            side = name.removeprefix('control-')
            if action.startswith('edit-'):
                (directory / 'base.txt').write_text(f'{action}:{side}\n')
            elif action == 'add':
                (directory / f'added-{side}.txt').write_text(f'added:{side}\n')
            elif action == 'rename':
                before = (directory / 'base.txt').read_bytes()
                (directory / 'base.txt').rename(directory / f'renamed-{side}.txt')
                assert (directory / f'renamed-{side}.txt').read_bytes() == before
            elif action == 'delete':
                (directory / 'fixed.txt').unlink()
            elif action == 'revert':
                allowed = {'base.txt', 'fixed.txt', f'added-{side}.txt', f'renamed-{side}.txt'}
                present = list(directory.iterdir())
                assert all(p.name in allowed and p.is_file() and not p.is_symlink() for p in present)
                for path in present:
                    path.unlink()
                (directory / 'base.txt').write_text('base:00\n')
                (directory / 'fixed.txt').write_text('fixed:00\n')
            else:
                raise AssertionError(action)

    with args.lock_file.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        protocol['initial_storage'] = check_storage([root])
        root.mkdir(parents=True, exist_ok=False)
        seed = root / 'seed'
        seed.mkdir()
        (seed / '.gitignore').write_text('bazel-*\nMODULE.bazel.lock\n')
        (seed / 'MODULE.bazel').write_text('module(name="skycache_membership_probe")\n')
        for i in range(count):
            package = seed / f'lib{i:02d}'
            (package / 'inputs').mkdir(parents=True)
            (package / 'inputs/base.txt').write_text(f'base:{i:02d}\n')
            (package / 'inputs/fixed.txt').write_text(f'fixed:{i:02d}\n')
            (package / 'BUILD').write_text('''genrule(name="data", srcs=glob(["inputs/*.txt"]), outs=["data.txt"],
    cmd="for f in $(SRCS); do printf '%s\\\\n' \\\"$$f\\\"; cat \\\"$$f\\\"; done > $@",
    visibility=["//visibility:public"])
''')
        (seed / 'app').mkdir()
        sources = ','.join(f'"//lib{i:02d}:data"' for i in range(count))
        (seed / 'app/BUILD').write_text('genrule(name="all", srcs=[' + sources + '], '
                                       'outs=["all.txt"], cmd="cat $(SRCS) > $@")\n')
        git(seed, 'init', '--quiet')
        git(seed, 'add', '.')
        git(seed, '-c', 'user.name=SkyCache Probe', '-c', 'user.email=skycache-probe@example.invalid',
            'commit', '--quiet', '-m', 'File membership correctness fixture')
        protocol['source_revision'] = git(seed, 'rev-parse', 'HEAD').strip()
        for name in names:
            git(seed, 'worktree', 'add', '--detach', str(root / name), 'HEAD')
        save()
        try:
            phase('initial-writers')
            assert all(row['skycache']['published'] for row in protocol['phases'][-1]['builds'] if row['cache_enabled'])
            restart_cached()
            phase('restarted-baseline-readers', controls=False, require_hits=True)
            for action in ('edit-one', 'edit-two', 'add', 'rename', 'delete'):
                mutate(action)
                phase('independent-' + action)
            restart_cached()
            phase('restarted-membership-readers', controls=False, require_hits=True)
            mutate('revert')
            phase('reverted-warm-graphs')
            restart_cached()
            phase('reverted-restarted-readers', controls=False, require_hits=True)
            protocol['passed'] = True
        finally:
            failures = []
            for name in sorted(started_servers):
                try:
                    shutdown(name)
                except Exception as error:
                    failures.append({'worktree': name, 'error': str(error)})
            protocol['shutdown_failures'] = failures
            if not failures:
                mutate('revert')
            protocol['sources_restored'] = all(not git(root / n, 'status', '--porcelain').strip() for n in names)
            diagnostics = []
            for name in names:
                for path in sorted((root / ('out-' + name)).glob('java.log.*')):
                    lines = [line for line in path.read_text(errors='replace').splitlines()
                             if 'Timeout waiting' in line]
                    diagnostics.extend({'worktree': name, 'line': line} for line in lines)
            protocol['invalidation_timeouts'] = diagnostics
            if failures or not protocol['sources_restored'] or diagnostics:
                protocol['passed'] = False
            save()
            assert not failures and protocol['sources_restored'] and not diagnostics


if __name__ == '__main__':
    main()
