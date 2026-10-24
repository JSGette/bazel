#!/usr/bin/env python3
"""Repeated full-command idle A/B: matched JVM, outputs, warm graph and uncached control.

Creates a private fixture. Preserves every attempt and never edits an existing run.
Normal filesystem/page caches remain warm. No machine-wide cache dropping.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bazel', type=Path, required=True)
    parser.add_argument('--jdk', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--trust-store', type=Path)
    parser.add_argument('--output-user-root', type=Path,
                        help='Keep Bazel install/repository caches on the selected run volume')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--with-off-control', action='store_true')
    parser.add_argument('--repeated-import-test', action='store_true')
    args = parser.parse_args()
    if args.trust_store is None:
        stores = list(Path('/var/lib/bazel-java-trust').glob('*.p12'))
        if len(stores) == 1:
            args.trust_store = stores[0]
    root = args.root.resolve()
    root.mkdir(exist_ok=False)
    workspace = root / 'workspace'
    workspace.mkdir(exist_ok=False)
    output = root / 'output-base'
    cache = root / 'skycache'
    links = root / 'links'
    links.mkdir(exist_ok=False)
    width = 512
    (workspace / 'MODULE.bazel').write_text('module(name="idle_retention_probe")\n')
    (workspace / 'BUILD').write_text('exports_files(["defs.bzl"])\n')
    (workspace / 'defs.bzl').write_text('''Data = provider(fields = ["values"])
def _leaf(ctx):
    return [Data(values = ["%s:%d:%s" % (ctx.label.package + ":" + ctx.label.name, i, "x" * 64) for i in range(512)])]
leaf = rule(implementation = _leaf)
def _app(ctx):
    lines = []
    for dep in ctx.attr.deps:
        values = dep[Data].values
        lines.append("%s|%s|%d" % (values[0], values[-1], len(values)))
    ctx.actions.write(ctx.outputs.out, "\\n".join(lines) + "\\n")
app = rule(implementation = _app, attrs = {"deps": attr.label_list()}, outputs = {"out": "%{name}.txt"})
''')
    deps = []
    for package in range(64):
        directory = workspace / f'lib{package:02}'
        directory.mkdir(exist_ok=False)
        (directory / 'BUILD').write_text(
            'load("//:defs.bzl", "leaf")\npackage(default_visibility=["//visibility:public"])\n'
            + ''.join(f'leaf(name="n{i:02}")\n' for i in range(32)))
        deps.extend(f'//lib{package:02}:n{i:02}' for i in range(32))
    (workspace / 'app').mkdir(exist_ok=False)
    (workspace / 'app/BUILD').write_text(
        'load("//:defs.bzl", "app")\napp(name="all", deps=' + repr(deps) + ')\n')
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    subprocess.run(['git', '-C', str(workspace), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(workspace), '-c', 'user.name=Probe',
                        '-c', 'user.email=probe@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    base = [str(args.bazel.resolve()), f'--output_base={output}',
            f'--server_javabase={args.jdk.resolve()}', '--host_jvm_args=-Xmx4g',
            '--max_idle_secs=600', '--ignore_all_rc_files']
    if args.output_user_root:
        base += [f'--output_user_root={args.output_user_root.resolve()}']
    if args.trust_store:
        base += [f'--host_jvm_args=-Djavax.net.ssl.trustStore={args.trust_store}',
                 '--host_jvm_args=-Djavax.net.ssl.trustStoreType=PKCS12',
                 '--host_jvm_args=-Djavax.net.ssl.trustStorePassword=changeit']
    flags = ['build', '//app:all', '--jobs=4', '--local_resources=memory=4096',
             '--color=no', '--curses=no', '--noshow_progress', '--lockfile_mode=error',
             '--experimental_strict_repo_env', '--experimental_active_directories=app',
             f'--symlink_prefix={links}/bazel-',
             f'--experimental_local_skycache={cache}',
             '--experimental_local_skycache_gc_max_size=0',
             '--experimental_local_skycache_gc_max_age=0']
    report = {'kind': 'synthetic_full_command_idle_ab', 'timing': 'blocking_waitpid_with_watchdog', 'attempts': [], 'cycles': [],
              'binary_sha256': hashlib.sha256(args.bazel.read_bytes()).hexdigest(),
              'limitations': ['Warm filesystem and action outputs; no OS cache drop.',
                              'Synthetic Starlark analysis fixture, not a production repository.',
                              'Heap receipt uses explicit GC; PSS is sampled separately.',
                              'Sampling every 100 ms may miss brief memory peaks.']}
    def save():
        (root / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    def memory():
        try:
            pid = int((output / 'server/server.pid.txt').read_text())
            fields = {}
            for line in Path(f'/proc/{pid}/smaps_rollup').read_text().splitlines():
                pieces = line.split()
                if pieces[0] in ('Rss:', 'Pss:', 'Private_Dirty:', 'Private_Clean:'):
                    fields[pieces[0][:-1].lower() + '_kib'] = int(pieces[1])
            fields['pid'] = pid
            fields['cpu_ticks'] = sum(map(int, Path(f'/proc/{pid}/stat').read_text().split()[13:15]))
            fields['io'] = {k: int(v) for k, v in
                            (line.split(':') for line in Path(f'/proc/{pid}/io').read_text().splitlines())}
            return fields
        except (OSError, ValueError):
            return None

    def wait_until(predicate, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(0.1)
        raise RuntimeError('Timed out waiting for idle task')

    expected = ''.join(f'{dep[2:]}:0:{"x" * 64}|{dep[2:]}:{width-1}:{"x" * 64}|{width}\n'
                       for dep in deps).encode()
    def build(role, retention='0', enabled=True):
        row = {'role': role, 'before': memory(), 'enabled': enabled, 'retention': retention}
        report['attempts'].append(row)
        cmd = base + flags + [f'--experimental_analysis_cache_idle_retention={retention}']
        if not enabled:
            cmd += ['--experimental_local_skycache=']
        row['argv'] = cmd
        save()
        samples = []
        stop = threading.Event()
        def sample():
            while not stop.is_set():
                current = memory()
                if current:
                    samples.append(current)
                stop.wait(0.1)
        sampler = threading.Thread(target=sample)
        with (root / f'{role}.log').open('w') as log:
            started = time.monotonic()
            process = subprocess.Popen(cmd, cwd=workspace, stdout=log, stderr=subprocess.STDOUT)
            sampler.start()
            def expire():
                row['timed_out'] = True
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
            watchdog = threading.Timer(300, expire)
            watchdog.start()
            try:
                # wait(timeout=...) polls with up to 50 ms sleeps on POSIX, a large bias for
                # subsecond builds. A separate watchdog permits an accurate blocking waitpid.
                process.wait()
            finally:
                row['seconds'] = time.monotonic() - started
                watchdog.cancel()
                watchdog.join()
                stop.set()
                sampler.join()
        row.update(exit_code=process.returncode, after=memory(),
                   peak_server_pss_kib=max((s['pss_kib'] for s in samples), default=None))
        profile = output / 'command.profile.gz'
        if profile.exists(): shutil.copyfile(profile, root / f'{role}.profile.gz')
        log_text = (root / f'{role}.log').read_text()
        row['analysis_hits'] = sum(int(a) for a, _ in re.findall(r'CONFIGURED_TARGET: (\d+)/(\d+)', log_text))
        if enabled:
            for name in ('local-skycache.json', 'local-skycache-validation.json'):
                path = output / name
                if path.exists(): row[name] = json.loads(path.read_text())
        save()
        if process.returncode:
            raise RuntimeError(f'{role} failed')
        contents = (links / 'bazel-bin/app/all.txt').read_bytes()
        row['sha256'] = hashlib.sha256(contents).hexdigest()
        row['expected_output_matches'] = contents == expected
        save()
        if contents != expected:
            raise RuntimeError(f'{role}: output differs from independent expectation')
        if row['before'] and row['before']['pid'] != row['after']['pid']:
            raise RuntimeError(f'{role}: server unexpectedly restarted')
        if enabled and row.get('local-skycache.json', {}).get('failed'):
            raise RuntimeError(f'{role}: publication guard failed')
        return row

    receipt = output / 'skycache-idle-retention.json'
    def await_idle(cycle):
        time.sleep(12)  # Ordinary Bazel idle GC runs at 10 seconds.
        cycle['retained_pss'] = memory()
        if receipt.exists():
            raise RuntimeError('Idle reset preceded retained-memory sample')
        wait_until(receipt.exists, 30)
        cycle['eviction'] = json.loads(receipt.read_text())
        time.sleep(1)
        cycle['evicted_pss'] = memory()
        save()

    try:
        # Populate and freeze registry metadata before timing: disabling the lockfile forces
        # network module resolution after each graph reset and masks analysis-cache cost.
        setup_cmd = base + ['mod', 'deps', '--lockfile_mode=update',
                            '--experimental_strict_repo_env', '--noshow_progress']
        report['setup_argv'] = setup_cmd
        save()
        with (root / 'setup.log').open('w') as log:
            setup = subprocess.run(setup_cmd, cwd=workspace, stdout=log, stderr=subprocess.STDOUT, timeout=300)
        report['setup_exit_code'] = setup.returncode
        save()
        if setup.returncode: raise RuntimeError('Module lockfile preparation failed')
        seed = build('seed')
        if not seed['local-skycache.json'].get('published'):
            raise RuntimeError('Seed did not publish')
        modes = [True, False] if args.with_off_control else [True]
        for round_index in range(args.rounds):
            for enabled in (modes if round_index % 2 == 0 else modes[::-1]):
                label = f'r{round_index}-' + ('skycache' if enabled else 'off')
                # Force a small real source revision; the app proof changes, leaf values remain.
                with (workspace / 'app/BUILD').open('a') as f:
                    f.write(f'\n# revision {label}\n')
                writer = build(label + '-writer')
                if not writer['local-skycache.json'].get('published'):
                    raise RuntimeError('Revision did not produce durable backing')
                receipt.unlink(missing_ok=True)
                warm = build(label + '-warm', '15s')
                cycle = {'round': round_index, 'enabled': enabled, 'label': label,
                         'writer_seconds': writer['seconds'], 'warm_seconds': warm['seconds']}
                report['cycles'].append(cycle)
                await_idle(cycle)
                recovery = build(label + '-recovery', enabled=enabled)
                cycle['recovery_seconds'] = recovery['seconds']
                cycle['analysis_hits'] = recovery['analysis_hits']
                if enabled and recovery['analysis_hits'] == 0:
                    raise RuntimeError('Recovery did not reuse SkyCache analysis')
                save()
        if args.repeated_import_test:
            # The last recovery may import everything with zero novel writes. It must still
            # schedule another eviction, twice, with no intervening source change/publication.
            for i in range(2):
                receipt.unlink(missing_ok=True)
                build(f'import-cycle-{i}-warm', '15s')
                cycle = {'label': f'import-cycle-{i}', 'correctness_only': True}
                report['cycles'].append(cycle)
                await_idle(cycle)
                recovered = build(f'import-cycle-{i}-recovery')
                if not recovered['analysis_hits']:
                    raise RuntimeError('Repeated idle recovery missed cache')
            receipt.unlink(missing_ok=True)
            build('mutation-warm', '15s')
            mutation_cycle = {'label': 'mutation-eviction', 'correctness_only': True}
            report['cycles'].append(mutation_cycle)
            await_idle(mutation_cycle)
            # Equal-size mutation after graph reset must invalidate disk proofs even with the
            # original mtime. Loading is cold, so this does not test Bazel's live stat cache.
            path = workspace / 'defs.bzl'
            stat = path.stat()
            path.write_text(path.read_text().replace('"x" * 64', '"y" * 64'))
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            expected = expected.replace(b'x' * 64, b'y' * 64)
            build('same-mtime-mutation')
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = str(error)
        raise
    finally:
        save()
        try:
            cleanup = subprocess.run(base + ['shutdown'], cwd=workspace, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, timeout=15)
            report['shutdown_exit_code'] = cleanup.returncode
        except subprocess.TimeoutExpired:
            report['shutdown_warning'] = 'Launcher timed out; check task-owned server process state'
        save()


if __name__ == '__main__':
    main()
