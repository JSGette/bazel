#!/usr/bin/env python3
"""Matched local, retained-remote and empty-retained-remote chain comparison."""
import concurrent.futures
import argparse
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import threading
import time

import grpc
from retention_cold_pages import evict

REPOS = Path('/home/jgett/REPOS')
PRIOR = REPOS / '.work/skycache-retained-chunks-2026-10-25/agent-matrix'
ROOT = REPOS / '.work/skycache-retention-2026-10-27/warm-controlled'
JDK = REPOS / '.tools/skycache-benchmark-jdk25'
SOURCE = REPOS / 'bazel-skycache-remote-mvp'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], text=True).strip()


def state(path):
    return [git(path, 'rev-parse', 'HEAD'), git(path, 'status', '--porcelain'),
            git(path, 'diff', '--binary', 'HEAD')]


class TimingProxy(grpc.GenericRpcHandler):
    """Forward serialized messages unchanged; record timing and sizes, not contents."""
    def __init__(self, endpoint, path):
        self.channel = grpc.insecure_channel(endpoint, options=[
            ('grpc.max_receive_message_length', 64 * 1024**2),
            ('grpc.max_send_message_length', 64 * 1024**2)])
        self.log = path.open('x')
        self.lock = threading.Lock()

    def service(self, details):
        method = details.method
        def call(request, context):
            started, wall = time.monotonic(), time.time()
            result_bytes, status = 0, 'OK'
            try:
                timeout = min(context.time_remaining() or 600, 600)
                metadata = [(x.key, x.value) for x in context.invocation_metadata()
                            if not x.key.startswith('grpc-') and x.key != 'user-agent']
                if method.endswith('/Read'):
                    for response in self.channel.unary_stream(method)(request, timeout=timeout, metadata=metadata):
                        result_bytes += len(response)
                        yield response
                else:
                    response = self.channel.unary_unary(method)(request, timeout=timeout, metadata=metadata)
                    result_bytes += len(response)
                    yield response
            except grpc.RpcError as error:
                status = error.code().name
                context.abort(error.code(), error.details())
            finally:
                with self.lock:
                    self.log.write(json.dumps(dict(method=method, start_epoch=wall, start_monotonic=started,
                        duration_s=time.monotonic()-started, request_bytes=len(request),
                        response_bytes=result_bytes, status=status)) + '\n')
                    self.log.flush()
        if method.endswith('/Read'):
            return grpc.unary_stream_rpc_method_handler(call)
        def unary(request, context):
            return next(call(request, context))
        return grpc.unary_unary_rpc_method_handler(unary)


def main():
    global ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--candidate128', type=Path, required=True)
    parser.add_argument('--cohort', choices=['warm','cold','eager','empty','diagnostic','confirm'],default='warm')
    args = parser.parse_args()
    cohort = args.cohort
    ROOT = args.root.resolve()
    assert ROOT.parent == REPOS/'.work/skycache-retention-2026-10-27'
    lock = (REPOS/'.work/skyframe-measurements.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    ROOT.mkdir(exist_ok=False)
    shutil.copy2(__file__, ROOT / 'harness.py')
    old = json.loads((PRIOR / 'results.json').read_text())
    template = next(x for x in old['runs'] if x['label'] == 'r1-minimal-remote-warm')
    ws, out = Path(old['workspace']), Path(old['output_base'])
    assert out.resolve() == REPOS / '.work/skycache-large-remote-2026-10-25/agent/output-base'
    pidfile = out / 'server/server.pid.txt'
    if pidfile.exists():
        proc = Path('/proc') / pidfile.read_text().strip() / 'cmdline'
        assert not (proc.exists() and str(out).encode() in proc.read_bytes()), 'fixture in use'
    binary = REPOS/'.work/skycache-validation-2026-10-26/bin/bazel'
    baseline_sha = '397ddf08c55af2d380df1b396ce4cbc96a3aa7575a0c7934a46b694bd82b52b9'
    candidate = args.candidate.resolve(strict=True)
    candidate_sha = sha(candidate)
    candidate128=args.candidate128.resolve(strict=True)
    executables={'baseline':candidate128,'retention':candidate}
    hashes={name:sha(path) for name,path in executables.items()}
    assert candidate_sha==json.loads((candidate.parent/'result.json').read_text())['binary_sha256']
    assert hashes['baseline']==json.loads((candidate128.parent/'result.json').read_text())['binary_sha256']
    worker_bin = Path(old['binary_paths']['worker'])
    assert sha(binary) == baseline_sha
    assert sha(worker_bin) == old['binary_sha256']['worker']
    assert git(ws, 'rev-parse', 'HEAD') == old['source_revision']
    assert sha(ws / 'MODULE.bazel.lock') == old['module_lock_sha256']
    before = {str(p): state(p) for p in [SOURCE, ws, REPOS / 'datadog-agent']}
    assert shutil.disk_usage(ROOT).free > 30 * 1024**3
    with socket.create_connection(('10.55.0.1', 8080), timeout=3):
        pass
    env = dict(os.environ, BAZEL_REAL=str(binary), BAZELISK_SKIP_WRAPPER='true',
               DD_BAZEL_REMOTE_CACHE='off', TMPDIR=str(Path(old['fixture_reuse']['scratch']) / 'tmp'))
    for key in ['PWD', 'OLDPWD', 'XDG_CACHE_HOME']:
        env.pop(key, None)
    # Strict repository environments still inherit PATH. Codex's temporary Windows PATH entries
    # change across turns, changing the cache namespace. Reproduce the valid seed environment
    # from its JFR rather than weakening namespace checks or treating a cold miss as warm.
    environment_jfr = REPOS/'.work/skycache-preliminary-2026-10-26/diagnostic-remote.jfr'
    captured = subprocess.run([str(JDK/'bin/jfr'), 'print', '--json', '--events',
        'jdk.InitialEnvironmentVariable', str(environment_jfr)], capture_output=True, text=True, check=True)
    captured_env = {e['values']['key']:e['values']['value']
        for e in json.loads(captured.stdout)['recording']['events']}
    prior_events = [json.loads(line) for line in (REPOS/
        '.work/skycache-preliminary-2026-10-26/r1-remote.bep.json').read_text().splitlines()]
    inherited_env = {'PATH','BZLMOD_ALLOW_YANKED_VERSIONS'}
    for event in prior_events:
        for section in event.get('structuredCommandLine',{}).get('sections',[]):
            for option in section.get('optionList',{}).get('option',[]):
                if option.get('optionName') in ['repo_env','action_env','test_env']:
                    value=option.get('optionValue','')
                    if value and '=' not in value:
                        inherited_env.add(value)
    assert captured_env.get('PATH')
    for name in inherited_env:
        if name in captured_env:
            env[name]=captured_env[name]
        else:
            env.pop(name,None)
    # Retain the prior installation/worktree/output identities; only new caches and logs vary.
    base = template['argv'][:template['argv'].index('build')]
    startup_base = base + ['--host_jvm_args=-XX:FlightRecorderOptions=stackdepth=256']
    startup = startup_base
    results = dict(status='running', source_head=git(SOURCE, 'rev-parse', 'HEAD'),
        binary_sha256=sha(binary), prior_results_sha256=sha(PRIOR/'results.json'),
        candidate_binary_sha256=candidate_sha, candidate_binary=str(candidate),
        class_source_binaries={name:str(path) for name,path in executables.items()},executable_sha256=hashes,
        runtime_binary=str(binary),runtime_binary_sha256=baseline_sha,agent_sha256=sha(ROOT.parent/'agent/agent.jar'),
        environment_control=dict(source_jfr_sha256=sha(environment_jfr),
            restored_allowlisted_names=sorted(inherited_env),
            path_sha256=hashlib.sha256(env['PATH'].encode()).hexdigest()),
        workspace=str(ws), output_base=str(out), runs=[],
        method='One frozen installation and the identical tested digest/buffer reader in both arms; startup-only replacement selects the original or optimized RemoteChunkCache. No per-read probes in primary timings. Cold disk arms evict private metadata pages with fadvise and verify residency using mincore. Eager includes production hydration before local restore; empty starts without a publication. '
               'Production hydrate reconstructs authenticated remote files for the local seed. Fresh JVM/clean graph and private cache per run; '
               'fixed source/tool/environment identity; fresh tiny genrule forces publication. Three samples per arm in rotated order. '
               'Remote timed commands connect directly to the worker; the created proxy is bypassed and records no RPCs. Local warmup excluded.')
    save(ROOT/'results.json', results)
    seed_preparation = REPOS/'.work/skycache-chain-2026-10-26'
    preparation=json.loads((seed_preparation/'preparation.json').read_text())
    assert preparation['binary_sha256']==baseline_sha
    for arm in ['remote-warm','remote-cold']:
        shutil.copytree(seed_preparation/'metadata-cas',ROOT/('metadata-cas-'+arm),copy_function=shutil.copy2)
        shutil.copytree(seed_preparation/'catalog-seed',ROOT/('catalog-seed-'+arm))
    results['seed_preparation']=dict(path=str(seed_preparation),sha256=sha(seed_preparation/'preparation.json'),publications=preparation['publications'])
    probe_directory=ws/'skycache_publication_probe_20261026'
    assert not probe_directory.exists() and probe_directory.parent==ws
    probe_directory.mkdir()
    probe_file=probe_directory/'BUILD.bazel'
    probe_content=''.join('genrule(name="probe_%d", outs=["probe_%d.txt"], cmd="printf \'skycache publication probe\\n\' > $@")\n' % (i,i) for i in range(32))
    probe_file.write_text(probe_content)
    results['publication_probe']=dict(package=str(probe_directory),sha256=sha(probe_file),
        expected_output_sha256=hashlib.sha256(b'skycache publication probe\n').hexdigest(),
        purpose='One new key forces equivalent publication work; six original binaries remain checked.')
    if cohort in ['eager','confirm']:
        subprocess.run([str(JDK/'bin/javac'),'-proc:none','-cp',str(seed_preparation/'server.jar'),
            '-d',str(ROOT/'helper-classes'),str(Path(__file__).parent/'HydrateSkycacheSeedParallel.java')],check=True)
    worker = proxy_server = proxy = None

    def simple(args, label, timeout=180):
        with (ROOT/(label+'.log')).open('x') as log:
            p = subprocess.run(args, cwd=ws, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        assert p.returncode == 0, label

    def shutdown(label):
        if pidfile.exists():
            simple(startup+['shutdown'], label, 120)

    def stop_worker():
        nonlocal worker, proxy_server, proxy
        if proxy_server:
            proxy_server.stop(3).wait(10)
            proxy.channel.close()
            proxy.log.close()
            proxy_server = proxy = None
        if worker:
            if worker.poll() is None:
                os.killpg(worker.pid, signal.SIGTERM)
                try:
                    worker.wait(15)
                except subprocess.TimeoutExpired:
                    os.killpg(worker.pid, signal.SIGKILL)
                    worker.wait(10)
            worker = None

    def run(label, variant, probe_index, warmup=False, primer=False, stabilization=False, diagnostic=False, implementation='baseline'):
        nonlocal worker, proxy_server, proxy, startup
        arm, jfr = variant, diagnostic
        local_mode=arm in ['local','local-empty','eager','eager-parallel']
        remote_variant='remote-cold' if arm.startswith('eager') else arm
        row = dict(label=label, arm=arm, variant=variant, implementation=implementation, warmup=warmup, primer=primer,
                   stabilization=stabilization,jfr=jfr)
        results['runs'].append(row)
        save(ROOT/'results.json', results)
        shutdown(label+'-shutdown-before')
        startup=startup_base+['--host_jvm_args=-javaagent:'+str(ROOT.parent/'agent/agent.jar')+'='+implementation]
        if diagnostic:
            startup+=['--host_jvm_args=-javaagent:'+str(ROOT.parent/'probe/probe.jar')+'='+str(ROOT/(label+'-stages.jsonl'))]
        env['BAZEL_REAL'] = str(binary)
        cache = ROOT/('cache-'+label)
        if arm == 'local':
            for part in ['objects', 'publications-v4']:
                shutil.copytree(seed_preparation/'local-seed/leased-v2'/part, cache/'leased-v2'/part, copy_function=shutil.copy2)
        elif arm!='local-empty':
            if arm=='remote-warm':
                shutil.copytree(seed_preparation/'retained-seed', cache/'leased-v2/remote-chunks-v1/sha256',copy_function=os.link)
            catalog = ROOT/('catalog-'+label)
            if primer:
                catalog.mkdir()
            else:
                shutil.copytree(ROOT/(('catalog-prime-' if stabilization else 'catalog-seed-')+remote_variant), catalog)
            with socket.socket() as s:
                s.bind(('127.0.0.1', 0))
                port = s.getsockname()[1]
            logfile = ROOT/(label+'-worker.log')
            with logfile.open('x') as log:
                worker = subprocess.Popen([str(worker_bin),'--listen_address=127.0.0.1',
                    '--listen_port='+str(port),'--cas_path='+str(ROOT/('metadata-cas-'+remote_variant)),
                    '--skycache_catalog_path='+str(catalog),'--skycache_instance_name=datadog-bench'],
                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic()+60
            while 'Not starting HTTP cache server' not in logfile.read_text():
                assert worker.poll() is None and time.monotonic()<deadline, 'worker readiness'
                time.sleep(.1)
            warm_python = REPOS/'.work/skycache-remote-mvp/benchmark-rpc-venv/bin/python'
            if not primer:
                simple([str(warm_python),str(SOURCE/'tools/skycache/remote_benchmark_warmup.py'),
                    '--endpoint',f'127.0.0.1:{port}','--catalog',str(catalog),'--rounds','1',
                    '--output',str(ROOT/(label+'-warmup.json'))],label+'-warmup',600)
            proxy = TimingProxy(f'127.0.0.1:{port}', ROOT/(label+'-rpc.jsonl'))
            proxy_server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=100), options=[
                ('grpc.max_receive_message_length',64*1024**2),('grpc.max_send_message_length',64*1024**2)])
            proxy_server.add_generic_rpc_handlers((proxy,))
            proxy_port = proxy_server.add_insecure_port('127.0.0.1:0')
            proxy_server.start()
        startup_started=time.monotonic()
        simple(startup+['clean','--noasync','--noexpunge','--color=no','--curses=no',
            '--experimental_local_skycache='+str(cache),
            '--symlink_prefix='+old['fixture_reuse']['scratch']+'/links/bazel-skycache-'],label+'-clean')
        row['startup_and_clean_seconds']=time.monotonic()-startup_started
        simple(startup+['dump','--skyframe=summary'],label+'-graph')
        graph=(ROOT/(label+'-graph.log')).read_text()
        assert 'Node count: 0' in graph and 'Edge count: 0' in graph
        assert not (out/'action_cache').exists()
        pid=int(pidfile.read_text())
        row['server_pid']=pid
        if jfr:
            simple([str(JDK/'bin/jcmd'),str(pid),'JFR.start','name=skycache','settings=profile',
                    'maxsize=256m','dumponexit=true','filename='+str(ROOT/(label+'.jfr'))],label+'-jfr-start')
        args=startup+template['argv'][template['argv'].index('build'):]
        if probe_index is not None:
            args.append('//'+probe_directory.name+':probe_'+str(probe_index))
        replace={'--experimental_local_skycache=':str(cache),
                 '--build_event_json_file=':str(ROOT/(label+'.bep.json')),
                 '--profile=':str(ROOT/(label+'.profile.gz')),
                 '--remote_grpc_log=':str(ROOT/(label+'.grpc.bin'))}
        transformed=[]
        for arg in args:
            if arg.startswith('--experimental_remote_skycache'):
                if local_mode:
                    continue
                if arg.startswith('--experimental_remote_skycache='):
                    arg=f'--experimental_remote_skycache=grpc://127.0.0.1:{port}'
            for prefix,value in replace.items():
                if arg.startswith(prefix):
                    arg=prefix+value
                    break
            transformed.append(arg)
        row['argv']=transformed
        row['start_epoch']=time.time()
        for name in ['local-skycache','local-skycache-validation','remote-skycache']:
            (out/(name+'.json')).unlink(missing_ok=True)
        if (cohort in ['cold','eager','diagnostic'] and arm!='remote-warm') or arm.startswith('eager'):
            disk_root=cache/'leased-v2' if arm=='local' else ROOT/('metadata-cas-'+remote_variant)
            row['page_cache']=evict(disk_root)
            assert row['page_cache']['resident_fraction_after'] < 0.001, row['page_cache']
        t=time.monotonic()
        row['command_start_monotonic']=t
        if arm.startswith('eager'):
            hydration=[str(JDK/'bin/java'),'-Xmx4g','-javaagent:'+str(ROOT.parent/'agent/agent.jar')+'=retention',
                '-cp',str(ROOT/'helper-classes')+':'+str(seed_preparation/'helper-classes')+':'+str(seed_preparation/'server.jar'),
                'com.google.devtools.build.lib.skyframe.serialization.local.HydrateSkycacheSeed'+('Parallel' if arm.startswith('eager-parallel') else ''),
                str(cache),preparation['namespace'],'grpc://127.0.0.1:'+str(port)]
            simple(hydration,label+'-hydrate',600)
            row['hydrate_seconds']=time.monotonic()-t
            row['hydrate_argv']=hydration
            hydrate_log=(ROOT/(label+'-hydrate.log')).read_text()
            row['hydrate_stats']=json.loads(next(x for x in hydrate_log.splitlines() if x.startswith('{')))
        build_started=time.monotonic()
        lines=[]
        with (ROOT/(label+'.log')).open('x') as log, (ROOT/(label+'-timeline.jsonl')).open('x') as timeline:
            proc=subprocess.Popen(transformed,cwd=ws,env=env,stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT,text=True,bufsize=1)
            watchdog=threading.Timer(600,lambda:proc.send_signal(signal.SIGINT) if proc.poll() is None else None)
            watchdog.start()
            try:
                for line in proc.stdout:
                    now=time.monotonic()-t
                    log.write(line);log.flush();lines.append(line)
                    timeline.write(json.dumps(dict(elapsed_s=now,text=line.rstrip()))+'\n');timeline.flush()
                row['exit_code']=proc.wait(timeout=30)
            finally:
                watchdog.cancel()
                if proc.poll() is None:
                    proc.kill();proc.wait()
        row['seconds']=time.monotonic()-t
        row['build_seconds']=time.monotonic()-build_started
        row['end_epoch']=time.time()
        if jfr:
            simple([str(JDK/'bin/jcmd'),str(pid),'JFR.stop','name=skycache',
                    'filename='+str(ROOT/(label+'.jfr'))],label+'-jfr-stop')
        text=''.join(lines)
        for name in ['local-skycache','local-skycache-validation','remote-skycache']:
            p=out/(name+'.json')
            if p.exists():
                row[name]=json.loads(p.read_text())
        row['analysis_hits']=sum(int(x) for x in re.findall(r'CONFIGURED_TARGET: (\d+)/',text))
        row['execution_hits']=sum(int(x) for x in re.findall(r'ACTION_EXECUTION: (\d+)/',text))
        row['committed']='Remote SkyCache: publication committed' in text
        row['errors']=re.findall(r'(?:SkyCache (?:publication|import) (?:skipped|discarded)|SkyCache disabled):[^\n]*',text)
        events=[json.loads(x) for x in (ROOT/(label+'.bep.json')).read_text().splitlines()]
        outputs={}
        probe_digest=None
        for event in events:
            for file in event.get('namedSetOfFiles',{}).get('files',[]):
                if file.get('name','').endswith('/probe_'+str(probe_index)+'.txt'):
                    probe_digest=file.get('digest')
                for name in old['expected_binaries']:
                    if file.get('name','').endswith('/'+name+'_/'+name):
                        outputs[name]=file.get('digest')
        row['output_digests']=outputs
        row['outputs_match']=outputs==old['expected_binaries']
        row['probe_output_matches']=probe_digest==results['publication_probe']['expected_output_sha256']
        row['build_metrics']=next((x['buildMetrics'] for x in events if 'buildMetrics' in x),{})
        profile=json.loads(gzip.open(ROOT/(label+'.profile.gz'),'rt').read())['traceEvents']
        row['profile_end_s']=max(x.get('ts',0)+x.get('dur',0) for x in profile)/1e6
        row['outside_profile_s']=row['seconds']-row['profile_end_s']
        stop_worker()
        if not local_mode or arm.startswith('eager'):
            row['rpc']=[json.loads(x) for x in (ROOT/(label+'-rpc.jsonl')).read_text().splitlines()]
        save(ROOT/'results.json',results)
        print(json.dumps({k:row[k] for k in ['label','seconds','profile_end_s','outside_profile_s','analysis_hits','execution_hits','outputs_match','committed']}),flush=True)
        assert row['exit_code']==0 and row['outputs_match'] and not row['errors'], label
        assert not row.get('rpc'), 'timed command must bypass proxy'
        if not local_mode:
            stats=row['remote-skycache']
            assert stats['cached_channel_capacity']==128
            assert stats['cached_channel_peak']<=stats['cached_channel_capacity']+stats['max_concurrency']
        assert row['local-skycache']['writes']>0,label
        if arm!='local-empty':
            assert row['local-skycache']['reused_witness_blocks']==103,label
        if local_mode:
            assert row['local-skycache']['published'],label
        if probe_index is not None:
            assert row['probe_output_matches'],label
        if arm=='local-empty':
            assert (row['analysis_hits'],row['execution_hits'])==(0,0),label
        elif stabilization:
            assert row['analysis_hits']>0 and row['execution_hits']==11,label
        elif primer:
            assert (row['analysis_hits'],row['execution_hits'])==(0,0),label
        else:
            assert (row['analysis_hits'],row['execution_hits'])==(18,11),label
        assert int(pidfile.read_text())==pid
        if not local_mode:
            assert row['committed'],label
            assert row['local-skycache']['writes']>0,label
            if not primer and not stabilization:
                assert row['local-skycache']['reused_witness_blocks']==103,label
            if arm=='remote-warm':
                assert row['remote-skycache']['downloaded_bytes']==0,label
            if arm=='remote-cold':
                assert row['remote-skycache']['downloaded_bytes']==1463742717,label
            assert all(x['status']=='OK' for x in row['rpc'])
        if primer:
            shutil.copytree(catalog,ROOT/('catalog-prime-'+remote_variant))
        if stabilization:
            shutil.copytree(catalog,ROOT/('catalog-seed-'+remote_variant))

    try:
        arms={
            'warm':[('remote-warm','baseline'),('remote-warm','retention')],
            'confirm':[('local','baseline'),('remote-warm','baseline'),('remote-warm','retention'),('eager-parallel-remote','retention')],
            'cold':[('local','baseline'),('remote-cold','baseline'),('remote-cold','retention')],
            'eager':[('eager','retention'),('eager-parallel','retention')],
            'empty':[('local-empty','baseline')],
            'diagnostic':[('remote-cold','retention')],
        }[args.cohort]
        index=0
        for variant,implementation in arms:
            run('warmup-'+variant+'-'+implementation,variant,index,warmup=True,implementation=implementation)
            index+=1
        orders={
            'warm':[0,1,1,0,0,1],
            'confirm':[0,1,2,3,3,2,1,0,2,3,0,1],
            'cold':[0,1,2,2,1,0,1,0,2],
            'eager':[0,1,1,0,0,1],
            'empty':[0,0,0],
            'diagnostic':[0],
        }[args.cohort]
        for number,which in enumerate(orders,1):
            variant,implementation=arms[which]
            run('r'+str(number)+'-'+variant+'-'+implementation,variant,index,
                diagnostic=args.cohort=='diagnostic',implementation=implementation)
            index+=1
        results['status']='passed'
    except BaseException as error:
        results['status']='failed';results['failure']=repr(error)
        raise
    finally:
        try:
            shutdown('final-shutdown')
            stop_worker()
        finally:
            assert probe_file.read_text()==probe_content
            probe_file.unlink()
            probe_directory.rmdir()
            results['source_states_unchanged']=all(state(Path(p))==v for p,v in before.items())
            results['binary_unchanged']=sha(binary)==baseline_sha
            results['candidate_binary_unchanged']=sha(candidate)==candidate_sha
            results['all_binaries_unchanged']=all(sha(path)==hashes[name] for name,path in executables.items())
            save(ROOT/'results.json',results)
            lock.close()


if __name__=='__main__':
    main()
