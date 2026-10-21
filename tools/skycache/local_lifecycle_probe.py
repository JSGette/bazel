#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Check local SkyCache lifecycle against independent cache-off servers.

Small correctness fixture, not a performance benchmark. All commands and compact
results are saved; source-build caching is independent of this cache-isolated probe.
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
    parser.add_argument('--require-traffic-stats', action='store_true')
    parser.add_argument('--legacy-bazel', type=Path, help='Also test mixed-layout operation, rollout and rollback.')
    args = parser.parse_args()
    binary = args.bazel.resolve(strict=True)
    root = args.root.resolve()
    initial_storage = check_storage([root])
    root.mkdir(parents=True, exist_ok=False)
    protocol = {'binary': str(binary), 'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                'purpose': 'Correctness of warm cache/configuration/environment transitions; not speed measurement.',
                'action_caches': 'Both disk and remote action caches disabled.',
                'initial_storage': initial_storage, 'require_traffic_stats': args.require_traffic_stats,
                'phases': [], 'passed': False}
    result_path = root / 'results.json'

    def save():
        result_path.write_text(json.dumps(protocol, indent=2) + '\n')

    def git(*argv):
        subprocess.run(['git', '-C', str(seed), *argv], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    seed = root / 'seed'
    count = 6
    names = ('a', 'b', 'control-a', 'control-b')
    binaries = {name: binary for name in names}
    legacy = args.legacy_bazel.resolve(strict=True) if args.legacy_bazel else None
    if legacy:
        binaries['a'] = binaries['control-a'] = legacy
        protocol['legacy_binary'] = str(legacy)
        protocol['legacy_binary_sha256'] = hashlib.sha256(legacy.read_bytes()).hexdigest()
    previous_publications = {}

    def check_publications():
        nonlocal previous_publications
        current = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for cache in ('cache', 'second-cache')
                   for p in sorted((root/cache).rglob('*.manifest'))}
        assert all(current.get(p) == digest for p, digest in previous_publications.items()), 'Existing publication changed or disappeared'
        previous_publications = current
        protocol['publication_inventory'] = current

    def switch_binary(name, replacement):
        for server in (name, 'control-' + name):
            shutdown(server)
            expected_pids.pop(server, None)
            binaries[server] = replacement
    started_servers = set()
    expected_pids = {}
    rule_template = '''SUFFIX = "{suffix}"
def copy_rule(name, src):
    native.genrule(name = name, srcs = [src, "@env_data//:value"], outs = [name + ".txt"],
        cmd = "cat $(location " + src + ") $(location @env_data//:value) > $@; printf '%s\\n' '$(SKY_MARK)' '" + SUFFIX + "' >> $@",
        visibility = ["//visibility:public"])
'''

    def startup(name):
        return [str(binaries[name]), '--nosystem_rc', '--nohome_rc', '--noworkspace_rc',
                '--host_jvm_args=-Xmx1g', '--max_idle_secs=120',
                '--output_base=' + str(root / ('out-' + name))]

    def environment(name):
        return dict(os.environ, PWD=str(root / name))

    def shutdown(name):
        subprocess.run(startup(name) + ['shutdown'], cwd=root / name, env=environment(name),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90, check=True)
        started_servers.discard(name)

    def pid(name):
        return (root / ('out-' + name) / 'server/server.pid.txt').read_text().strip()

    def build(name, phase, *, cfg='C1', repo_env='R1', action_env='E1', test_env='T1',
              cache='cache', suffix='S0', source_suffix=''):
        workspace = root / name
        side = name.removeprefix('control-')
        (workspace / 'defs.bzl').write_text(rule_template.format(suffix=suffix))
        (workspace / 'lib00/input').write_text('base:00' + source_suffix.replace('{side}', side) + '\n')
        cache_enabled = cache is not None and not name.startswith('control-')
        argv = startup(name) + ['build', '//app:all', '--jobs=2', '--local_resources=memory=1024',
                '--lockfile_mode=off', '--experimental_strict_repo_env', '--color=no', '--curses=no',
                '--remote_cache=', '--disk_cache=', '--define=SKY_MARK=' + cfg,
                '--repo_env=SKYCACHE_REPO_MARK=' + repo_env,
                '--action_env=SKYCACHE_MARK=' + action_env, '--test_env=SKYCACHE_TEST_MARK=' + test_env,
                '--experimental_active_directories=app',
                '--experimental_local_skycache=' + (str(root / cache) if cache_enabled else '')]
        started = time.monotonic()
        log_path = root / (phase + '-' + name + '.log')
        with log_path.open('w') as log:
            completed = subprocess.run(argv, cwd=workspace, env=environment(name),
                                       stdout=log, stderr=subprocess.STDOUT, timeout=180)
        text = log_path.read_text()
        stats = re.findall(r'Local SkyCache: (\{[^\n]+\})', text)
        expected = b''.join((workspace / f'lib{i:02d}/input').read_bytes() +
                            (repo_env + '\n' + cfg + '\n' + suffix + '\n').encode()
                            for i in range(count)) + (action_env + '\n').encode()
        output = (workspace / 'bazel-bin/app/all.txt').read_bytes() if completed.returncode == 0 else None
        row = {'worktree': name, 'binary': str(binaries[name]), 'argv': argv, 'exit_code': completed.returncode,
               'seconds': time.monotonic() - started, 'cache_enabled': cache_enabled,
               'analysis_hits': sum(int(x) for x in re.findall(r'CONFIGURED_TARGET: (\d+)/\d+', text)),
               'output_matches_expected': output == expected,
               'expected_sha256': hashlib.sha256(expected).hexdigest(),
               'output_sha256': hashlib.sha256(output).hexdigest() if output is not None else None,
               'disabled_warning': 'Local SkyCache disabled:' in text,
               'publication_warning': 'Local SkyCache publication ' in text}
        if stats:
            row['skycache'] = json.loads(stats[-1])
        uploaded = re.findall(r'Skycache write: uploaded (\d+)/(\d+) key/value bytes and (\d+) entries \((\d+) batches\)', text)
        downloaded = re.findall(r'Skycache read: (.+?) received in (\d+) requests,', text)
        if uploaded:
            row['native_write'] = dict(zip(('key_bytes','value_bytes','entries','batches'), map(int,uploaded[-1])))
        if downloaded:
            row['native_read'] = {'formatted_bytes':downloaded[-1][0], 'requests':int(downloaded[-1][1])}

        if completed.returncode == 0:
            row['server_pid'] = pid(name)
        return row

    def phase(label, *, controls=True, require_hits=False, require_hits_on=(), **settings):
        storage = check_storage([root])
        rows = []
        for pair in (('a', 'b'), ('control-a', 'control-b')) if controls else (('a', 'b'),):
            started_servers.update(pair)
            with ThreadPoolExecutor(max_workers=2) as pool:
                rows.extend(f.result() for f in [pool.submit(build, n, label, **settings) for n in pair])
        check_publications()
        protocol['phases'].append({'phase': label, 'settings': settings, 'builds': rows,
                                   'storage_preflight': storage})
        save()
        print(json.dumps({'phase': label, 'results': [{k:row[k] for k in
                          ('worktree','exit_code','output_matches_expected','analysis_hits','seconds')} for row in rows]}), flush=True)
        for row in rows:
            name = row['worktree']
            assert row['exit_code'] == 0 and row['output_matches_expected'], row
            assert not row['disabled_warning'] and not row['publication_warning'], row
            assert ('skycache' in row) == row['cache_enabled'], row
            if row['cache_enabled']:
                assert not row['skycache']['failed'], row
                if args.require_traffic_stats:
                    stats = row['skycache']
                    native = row['native_write']
                    assert native['key_bytes'] == stats['cas_put_key_bytes'], row
                    assert native['value_bytes'] == stats['cas_put_value_bytes'], row
                    assert native['entries'] == stats['cas_accepted_puts'], row
                    assert native['batches'] == stats['cas_put_requests'], row
                    assert stats['cas_get_requests'] == stats['cas_get_hits'] + stats['cas_get_misses'], row
                    if row['analysis_hits'] > 0:
                        assert row['native_read']['requests'] > 0, row
                        assert float(row['native_read']['formatted_bytes'].split()[0]) > 0, row

            else:
                assert row['analysis_hits'] == 0, row
            if name in expected_pids:
                assert row['server_pid'] == expected_pids[name], 'server unexpectedly restarted: ' + name
            expected_pids[name] = row['server_pid']
        if controls:
            for name in ('a','b'):
                assert next(r for r in rows if r['worktree'] == name)['output_sha256'] == next(
                    r for r in rows if r['worktree'] == 'control-' + name)['output_sha256']
        required = {'a','b'} if require_hits else set(require_hits_on)
        assert all(row['analysis_hits'] > 0 and row['skycache']['shared_reads'] > 0
                   for row in rows if row['worktree'] in required), rows

    save()
    with args.lock_file.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        seed.mkdir()
        (seed / 'MODULE.bazel').write_text('module(name="skycache_lifecycle_probe")\n'
            'environment_repo = use_repo_rule("//:repo.bzl", "environment_repo")\n'
            'environment_repo(name="env_data")\n')
        (seed / 'BUILD').write_text('exports_files(["repo.bzl", "defs.bzl"])\n')
        (seed / 'defs.bzl').write_text(rule_template.format(suffix='S0'))
        (seed / 'repo.bzl').write_text('''def _repo(ctx):
    ctx.file("BUILD.bazel", 'exports_files(["value"])\\n')
    ctx.file("value", ctx.os.environ["SKYCACHE_REPO_MARK"] + "\\n")
environment_repo = repository_rule(implementation = _repo, environ = ["SKYCACHE_REPO_MARK"], local = True)
''')
        for i in range(count):
            package = seed / f'lib{i:02d}'
            package.mkdir()
            (package / 'input').write_text(f'base:{i:02d}\n')
            (package / 'BUILD').write_text('load("//:defs.bzl", "copy_rule")\ncopy_rule(name="lib", src="input")\n')
        (seed / 'app').mkdir()
        sources = ','.join(f'"//lib{i:02d}:lib"' for i in range(count))
        (seed / 'app/BUILD').write_text('genrule(name="all", srcs=[' + sources + '], outs=["all.txt"], '
            'cmd="cat $(SRCS) > $@; printf \'%s\\\\n\' \\\"$$SKYCACHE_MARK\\\" >> $@")\n')
        git('init','--quiet')
        git('add','.')
        git('-c','user.name=SkyCache Probe','-c','user.email=skycache-probe@example.invalid',
            'commit','--quiet','-m','Cache lifecycle correctness fixture')
        for name in names:
            git('worktree','add','--detach',str(root / name),'HEAD')
        try:
            phase('initial-writers')
            for name in ('a','b'):
                shutdown(name)
                expected_pids.pop(name)
            phase('restarted-readers', controls=False, require_hits=True)
            phase('configuration-change', cfg='C2')
            phase('configuration-revert', require_hits=True)
            phase('disable-and-edit', cache=None, suffix='disabled-edit', source_suffix='-off-{side}')
            phase('reenable-after-edit', suffix='disabled-edit', source_suffix='-off-{side}')
            phase('reenabled-source-revert')
            phase('switch-cache-directory', cache='second-cache')
            phase('switch-back')
            phase('repository-environment-change', repo_env='R2')
            phase('action-environment-change', repo_env='R2', action_env='E2')
            phase('test-environment-change', repo_env='R2', action_env='E2', test_env='T2')
            queries = []
            for name in ('a','b'):
                argv = startup(name) + ['query', '//app:all', '--lockfile_mode=off',
                    '--experimental_local_skycache=' + str(root/'cache'),
                    '--repo_env=SKYCACHE_REPO_MARK=R2', '--experimental_strict_repo_env',
                    '--color=no','--curses=no']
                completed = subprocess.run(argv, cwd=root/name, env=environment(name),
                                           capture_output=True, text=True, timeout=90)
                (root/('query-'+name+'.log')).write_text(completed.stdout + completed.stderr)
                queries.append({'worktree':name,'argv':argv,'exit_code':completed.returncode,
                                'server_pid':pid(name), 'local_cache_active':'Local SkyCache:' in completed.stderr})
            protocol['query_commands'] = queries
            save()
            assert all(q['exit_code']==0 and not q['local_cache_active'] and
                       q['server_pid']==expected_pids[q['worktree']] for q in queries), queries
            phase('build-after-query', repo_env='R2', action_env='E2', test_env='T2')
            phase('restore-original-environment')
            if legacy:
                inventory = protocol['publication_inventory']
                assert any(p.startswith('cache/publications-v3/') for p in inventory)
                assert any(p.startswith('cache/leased-v1/publications-v3/') for p in inventory)
                protocol['before_migration_inventory'] = dict(inventory)
                switch_binary('a', binary)
                phase('roll-forward-to-leases', require_hits_on=('a',))
                phase('both-leased-independent-edits', source_suffix='-leased-{side}')
                phase('both-leased-revert')
                switch_binary('a', legacy)
                phase('roll-back-to-legacy', require_hits_on=('a',))
                switch_binary('a', binary)
                phase('roll-forward-again', require_hits_on=('a',))
                protocol['layout_migration_passed'] = True
            protocol['passed'] = True
            save()
        finally:
            failures = []
            for name in sorted(started_servers):
                try:
                    shutdown(name)
                except Exception as e:
                    failures.append({'worktree':name,'error':str(e)})
            protocol['shutdown_failures'] = failures
            save()
            assert not failures, failures


if __name__ == '__main__':
    main()
