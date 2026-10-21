#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Exercise shared SkyCache collection with two live servers and cache-off controls."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
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
    storage = check_storage([root])
    root.mkdir(parents=True, exist_ok=False)
    cache = root / 'cache'
    gate = root / 'gate'
    gate.mkdir()
    seed = root / 'seed'
    names = ('a', 'b', 'control-a', 'control-b')
    started = set()
    result = {'purpose': 'Correctness under real idle collection, live readers, independent edits and reverts.',
              'binary': str(binary), 'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
              'action_caches': 'Remote and disk disabled for all measured workloads and controls.',
              'initial_storage': storage, 'phases': [], 'passed': False}

    def save():
        (root / 'results.json').write_text(json.dumps(result, indent=2) + '\n')

    def startup(name):
        return [str(binary), '--nosystem_rc', '--nohome_rc', '--noworkspace_rc',
                '--host_jvm_args=-Xmx1g', '--max_idle_secs=120',
                '--output_base=' + str(root / ('out-' + name))]

    def environment(name):
        return dict(os.environ, PWD=str(root / name))

    def shutdown(name):
        subprocess.run(startup(name) + ['shutdown'], cwd=root/name, env=environment(name),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90, check=True)
        started.discard(name)

    def app(gated=False):
        sources = ','.join(f'"//lib{i:02d}:lib"' for i in range(6))
        prefix = (f'touch {shlex.quote(str(gate / "ready"))}; while test -e {shlex.quote(str(gate / "hold"))}; do sleep 0.05; done; '
                  if gated else '')
        return ('genrule(name="all", srcs=[' + sources + '], outs=["all.txt"], cmd='
                + json.dumps(prefix + 'cat $(SRCS) > $@') + ')\n')

    def build(name, label, *, collect=False):
        workspace = root/name
        enabled = not name.startswith('control-')
        started.add(name)
        argv = startup(name) + ['build', '//app:all', '--jobs=2', '--local_resources=memory=1024',
                '--lockfile_mode=off', '--color=no', '--curses=no', '--remote_cache=', '--disk_cache=',
                '--experimental_active_directories=app', '--sandbox_writable_path=' + str(gate),
                '--experimental_local_skycache=' + (str(cache) if enabled else ''),
                '--experimental_local_skycache_gc_max_size=' + ('1' if collect else '0'),
                '--experimental_local_skycache_gc_max_age=0',
                '--experimental_local_skycache_gc_idle_delay=0']
        begin = time.monotonic()
        log_path = root/(label + '-' + name + '.log')
        with log_path.open('w') as log:
            completed = subprocess.run(argv, cwd=workspace, env=environment(name),
                                       stdout=log, stderr=subprocess.STDOUT, timeout=180)
        text = log_path.read_text()
        expected = b''.join((workspace/f'lib{i:02d}/input').read_bytes() for i in range(6))
        output = (workspace/'bazel-bin/app/all.txt').read_bytes() if completed.returncode == 0 else None
        stats = re.findall(r'Local SkyCache: (\{[^\n]+\})', text)
        row = {'worktree':name, 'argv':argv, 'exit_code':completed.returncode,
               'seconds':time.monotonic()-begin, 'cache_enabled':enabled,
               'output_matches_expected':output==expected,
               'expected_sha256':hashlib.sha256(expected).hexdigest(),
               'output_sha256':hashlib.sha256(output).hexdigest() if output is not None else None,
               'analysis_hits':sum(map(int,re.findall(r'CONFIGURED_TARGET: (\d+)/\d+',text))),
               'warning':'Local SkyCache disabled:' in text or 'Local SkyCache publication ' in text}
        if stats:
            row['skycache'] = json.loads(stats[-1])
        assert row['exit_code']==0 and row['output_matches_expected'] and not row['warning'], row
        assert bool(stats)==enabled and (not enabled or not row['skycache']['failed']), row
        if not enabled:
            assert row['analysis_hits']==0, row
        return row

    def phase(label, *, participants=names, collect=False, require_hits=False):
        check = check_storage([root])
        rows = []
        for offset in range(0,len(participants),2):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(build,n,label,collect=collect) for n in participants[offset:offset+2]]
                rows.extend(f.result() for f in futures)
        record(label, rows, check)
        if require_hits:
            assert all(r['analysis_hits']>0 for r in rows if r['cache_enabled']), rows
        return rows

    def record(label, rows, check):
        for row in rows:
            controls = [r for r in rows if r['worktree']=='control-'+row['worktree']]
            if controls:
                assert row['output_sha256']==controls[0]['output_sha256']
        result['phases'].append({'phase':label,'builds':rows,'storage_preflight':check})
        save()
        print(json.dumps({'phase':label,'results':[{k:r[k] for k in
                          ('worktree','analysis_hits','output_matches_expected','seconds')} for r in rows]}),flush=True)

    def wait_for(path, future=None):
        deadline = time.monotonic()+45
        while not path.exists():
            if future is not None and future.done():
                future.result()
                raise RuntimeError("Gated build finished without creating its ready marker")
            if time.monotonic()>deadline:
                raise RuntimeError('Timed out waiting for '+str(path))
            time.sleep(0.05)

    def gc_stats(name):
        path=root/('out-'+name)/'local-skycache-gc.json'
        wait_for(path)
        return json.loads(path.read_text())

    with args.lock_file.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        seed.mkdir()
        (seed/'MODULE.bazel').write_text('module(name="skycache_gc_probe")\n')
        (seed/'BUILD').write_text('')
        for i in range(6):
            package=seed/f'lib{i:02d}'
            package.mkdir()
            (package/'input').write_text(f'base:{i:02d}\n')
            (package/'BUILD').write_text('genrule(name="lib",srcs=["input"],outs=["lib.txt"],'
                'cmd="cat $(SRCS) > $@",visibility=["//visibility:public"])\n')
        (seed/'app').mkdir()
        (seed/'app/BUILD').write_text(app())
        def git(*argv):
            subprocess.run(['git','-C',str(seed),*argv],check=True,
                           stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        git('init','--quiet')
        git('add','.')
        git('-c','user.name=SkyCache Probe','-c','user.email=skycache-probe@example.invalid',
            'commit','--quiet','-m','Concurrent cache collection correctness fixture')
        for name in names:
            git('worktree','add','--detach',str(root/name),'HEAD')
        try:
            phase('seed-writers')
            for name in ('a','b'):
                shutdown(name)
            phase('fresh-readers',participants=('a','b'),require_hits=True)
            # Hold B in execution after its fresh analysis has pinned shared publications. A can
            # finish its own build and collect while B's command is still alive in another JVM.
            shutdown('b')
            (root/'b/app/BUILD').write_text(app(gated=True))
            (root/'control-b/app/BUILD').write_text(app(gated=True))
            (gate/'hold').touch()
            check=check_storage([root])
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(build,'b','live-reader-collection',collect=True)
                try:
                    wait_for(gate/'ready',future)
                    a=build('a','live-reader-collection',collect=True)
                    protected=gc_stats('a')
                    result['collection_while_reader_live']=protected
                    save()
                    assert protected['status']=='complete' and protected['active_slots']>0
                    assert protected['protected_bytes']>0 and protected['known_bytes_retained']>1
                    assert not future.done(), 'Reader command finished before active-pin observation'
                finally:
                    (gate/'hold').unlink(missing_ok=True)
                b=future.result()
            assert b['analysis_hits']>0 and b['skycache']['shared_reads']>0, b
            controls=[build(n,'live-reader-collection') for n in ('control-a','control-b')]
            record('live-reader-collection',[a,b,*controls],check)
            after=gc_stats('b')
            result['collection_after_reader_finishes']=after
            assert after['status']=='complete' and after['active_slots']==0
            assert after['known_bytes_retained']==0 and after['deleted_publications']>0
            assert not list((cache/'leased-v1/publications-v3').rglob('*.manifest'))
            for name in ('b','control-b'):
                (root/name/'app/BUILD').write_text(app())
            for name in ('a','b'):
                shutdown(name)
            phase('rebuild-after-eviction')
            for name in names:
                side=name.removeprefix('control-')
                (root/name/'lib00/input').write_text('edit-'+side+'\n')
            phase('independent-edits')
            for name in names:
                (root/name/'lib00/input').write_text('base:00\n')
            phase('revert')
            result['passed']=True
            save()
        finally:
            (gate/'hold').unlink(missing_ok=True)
            failures=[]
            for name in sorted(started):
                try:
                    shutdown(name)
                except Exception as e:
                    failures.append({'worktree':name,'error':str(e)})
            result['shutdown_failures']=failures
            save()
            assert not failures,failures


if __name__=='__main__':
    main()
