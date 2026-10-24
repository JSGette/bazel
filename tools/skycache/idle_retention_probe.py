#!/usr/bin/env python3
"""Exactly three workload builds: seed, retained warm graph, post-idle recovery.

Synthetic provider-heavy analysis fixture, not a real-repository performance claim.
Creates its own Git workspace/output base and shuts down only its own Bazel server.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bazel', type=Path, required=True)
    parser.add_argument('--jdk', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--trust-store', type=Path)
    parser.add_argument('--resume-after-setup-failure', action='store_true')
    parser.add_argument('--final-memory-only', action='store_true')
    args = parser.parse_args()
    if args.trust_store is None:
        stores = list(Path('/var/lib/bazel-java-trust').glob('*.p12'))
        if len(stores) == 1:
            args.trust_store = stores[0]
    if args.final_memory_only:
        args.resume_after_setup_failure = True
    root = args.root.resolve()
    root.mkdir(exist_ok=args.resume_after_setup_failure)
    previous = None
    if args.resume_after_setup_failure:
        previous = json.loads((root / 'results.json').read_text())
        assert len(previous['attempts']) == (2 if args.final_memory_only else 1)
        assert previous['attempts'][0]['exit_code'] != 0
        assert previous['attempts'][0]['local-skycache.json']['writes'] == 0
        if args.final_memory_only:
            assert previous['attempts'][1]['exit_code'] != 0
        snapshot = 'first-two-failures.json' if args.final_memory_only else 'initial-failure.json'
        (root / snapshot).write_text(json.dumps(previous, indent=2) + '\n')
    workspace = root / 'workspace'
    workspace.mkdir(exist_ok=args.resume_after_setup_failure)
    output = root / ('output-base-final' if args.final_memory_only else
                     'output-base-fixed' if previous else 'output-base')
    cache = root / 'skycache'
    links = root / 'links'
    links.mkdir(exist_ok=args.resume_after_setup_failure)
    leaf_count, width = 2048, 512
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
        directory.mkdir(exist_ok=args.resume_after_setup_failure)
        (directory / 'BUILD').write_text(
            'load("//:defs.bzl", "leaf")\npackage(default_visibility=["//visibility:public"])\n'
            + ''.join(f'leaf(name="n{i:02}")\n' for i in range(32)))
        deps.extend(f'//lib{package:02}:n{i:02}' for i in range(32))
    (workspace / 'app').mkdir(exist_ok=args.resume_after_setup_failure)
    (workspace / 'app/BUILD').write_text(
        'load("//:defs.bzl", "app")\napp(name="all", deps=' + repr(deps) + ')\n')
    if not previous:
        subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
        subprocess.run(['git', '-C', str(workspace), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(workspace), '-c', 'user.name=Probe',
                        '-c', 'user.email=probe@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    base = [str(args.bazel.resolve()), f'--output_base={output}',
            f'--server_javabase={args.jdk.resolve()}', '--host_jvm_args=-Xmx4g',
            '--max_idle_secs=600', '--ignore_all_rc_files']
    if args.trust_store:
        base += [f'--host_jvm_args=-Djavax.net.ssl.trustStore={args.trust_store}',
                 '--host_jvm_args=-Djavax.net.ssl.trustStoreType=PKCS12',
                 '--host_jvm_args=-Djavax.net.ssl.trustStorePassword=changeit']
    flags = ['build', '//app:all', '--jobs=4', '--local_resources=memory=4096',
             '--color=no', '--curses=no', '--noshow_progress', '--lockfile_mode=off',
             '--experimental_strict_repo_env', '--experimental_active_directories=app',
             f'--symlink_prefix={links}/bazel-',
             f'--experimental_local_skycache={cache}',
             '--experimental_local_skycache_gc_max_size=0',
             '--experimental_local_skycache_gc_max_age=0']
    report = {'workload_build_cap': 3, 'attempts': [], 'fixture': {
        'leaf_count': leaf_count, 'strings_per_leaf': width,
        'description': 'Synthetic analysis-heavy Starlark providers; one FileWrite action'},
        'limitations': ['Single ordered sample per role; no statistical speedup claim.',
                        'No uncached-analysis control; A/B tests idle retention only.',
                        'Filesystem and action outputs remain warm; same JVM throughout.']}
    if previous:
        report['attempts'] = previous['attempts']
        report['amendment'] = ('Initial TLS setup failure consumes attempt 1. Use only two more '
                               'builds: publication with idle retention, then recovery. No warm '
                               'build control. New output base and explicit environment CA trust.')
        report['limitations'] = ['One failed setup attempt plus two successful-build opportunities.',
            'First successful build includes startup/publication and cold output; next is same-JVM '
            'SkyCache recovery with warm output. Their timing ratio is not an isolated speedup.',
            'Before/after idle memory is measured within the first successful server.']
    if args.final_memory_only:
        report['amendment'] = ('Attempts 1 and 2 failed (TLS trust then unsupported Starlark format). '
                               'Use final attempt only to publish and measure pre/post idle memory. '
                               'No recovery build is permitted within the three-attempt cap.')
        report['limitations'] = ['Two failed attempts; only one remaining build.',
                                 'Before/after idle memory only. No measured recovery latency.',
                                 'Synthetic fixture, not a real-repository benchmark.']

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
    try:
        schedule = ([('A_seed_before_idle', '20s'), ('B_after_idle', '0')] if previous else
                    [('seed', '0'), ('A_warm_retained', '20s'), ('B_after_idle', '0')])
        if args.final_memory_only:
            schedule = [('memory_only_seed', '20s')]
        for role, retention in schedule:
            assert len(report['attempts']) < 3
            row = {'role': role, 'retention': retention, 'before': memory()}
            report['attempts'].append(row)
            save()  # Every attempted build counts, including failure or timeout.
            cmd = base + flags + [f'--experimental_analysis_cache_idle_retention={retention}']
            row['argv'] = cmd
            start = time.monotonic()
            samples = []
            with (root / f'{role}.log').open('w') as log:
                process = subprocess.Popen(cmd, cwd=workspace, stdout=log, stderr=subprocess.STDOUT)
                stop = threading.Event()
                def sample_memory():
                    while not stop.is_set():
                        sample = memory()
                        if sample:
                            samples.append(sample)
                        stop.wait(0.1)
                sampler = threading.Thread(target=sample_memory)
                sampler.start()
                try:
                    process.wait(timeout=300)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    row['timed_out'] = True
                elapsed = time.monotonic() - start
                stop.set()
                sampler.join()
            row.update(seconds=elapsed, exit_code=process.returncode, after=memory(),
                       peak_server_pss_kib=max((s['pss_kib'] for s in samples), default=None))
            log_text = (root / f'{role}.log').read_text()
            hits = re.findall(r'CONFIGURED_TARGET: (\d+)/(\d+)', log_text)
            row['analysis_hits'] = sum(int(hit) for hit, _ in hits)
            for name in ['local-skycache.json', 'local-skycache-validation.json']:
                if (output / name).exists():
                    row[name] = json.loads((output / name).read_text())
            save()
            if process.returncode != 0:
                raise RuntimeError(f'{role} failed; no additional workload builds')
            artifact = links / 'bazel-bin/app/all.txt'
            contents = artifact.read_bytes()
            row['sha256'] = hashlib.sha256(contents).hexdigest()
            row['expected_output_matches'] = contents == expected
            save()
            if contents != expected:
                raise RuntimeError('Output differs from independently generated expected bytes')
            if role == 'B_after_idle':
                if row['analysis_hits'] == 0:
                    raise RuntimeError('Post-idle build did not reuse SkyCache analysis')
                baseline = report['attempts'][1 if previous else 0]
                if row['after']['pid'] != baseline['after']['pid']:
                    raise RuntimeError('Server restarted instead of retaining the JVM')
            if role in ('seed', 'A_seed_before_idle', 'memory_only_seed'):
                if not row['local-skycache.json'].get('published'):
                    raise RuntimeError('Seed did not publish')
            if role == 'seed':
                time.sleep(12)  # Let the normal 10-second idle GC finish before the warm control.
            elif role in ('A_warm_retained', 'A_seed_before_idle', 'memory_only_seed'):
                time.sleep(12)
                report['retained_idle_memory'] = memory()
                receipt = output / 'skycache-idle-retention.json'
                if receipt.exists():
                    raise RuntimeError('Eviction happened before the retained-memory observation')
                wait_until(receipt.exists, 45)
                report['eviction'] = json.loads(receipt.read_text())
                time.sleep(2)
                report['evicted_idle_memory'] = memory()
                save()
        report['status'] = ('memory_only_completed_with_two_failed_attempts' if args.final_memory_only
                            else 'completed_with_setup_failure' if previous else 'passed')
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
            report['shutdown_warning'] = 'Launcher timed out waiting for server exit; inspect PID state'
        save()


if __name__ == '__main__':
    main()
