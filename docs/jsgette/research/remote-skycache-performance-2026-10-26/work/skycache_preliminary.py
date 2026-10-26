#!/usr/bin/env python3
"""Isolated diagnostic runs of the existing SkyCache binary; no product edits."""
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

REPOS = Path('/home/jgett/REPOS')
PRIOR = REPOS / '.work/skycache-retained-chunks-2026-10-25/agent-matrix'
ROOT = REPOS / '.work/skycache-preliminary-2026-10-26'
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
                    self.log.write(json.dumps(dict(method=method, start_epoch=wall,
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
    parser.add_argument('--focused', action='store_true')
    parser.add_argument('--agent', type=Path)
    args = parser.parse_args()
    ROOT = args.root.resolve()
    assert ROOT.parent == REPOS/'.work' and ROOT.name.startswith('skycache-preliminary-')
    lock = (ROOT.parent / 'skyframe-measurements.lock').open('a')
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
    binary = Path(old['binary_paths']['candidate'])
    worker_bin = Path(old['binary_paths']['worker'])
    assert sha(binary) == old['binary_sha256']['candidate']
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
    # Retain the prior installation/worktree/output identities; only new caches and logs vary.
    base = template['argv'][:template['argv'].index('build')]
    startup = base + ['--host_jvm_args=-XX:FlightRecorderOptions=stackdepth=256']
    if args.agent:
        assert args.focused
        startup += ['--host_jvm_args=-javaagent:'+str(args.agent.resolve(strict=True))+'='+str(ROOT/'stages.jsonl')]
    results = dict(status='running', source_head=git(SOURCE, 'rev-parse', 'HEAD'),
        binary_sha256=sha(binary), prior_results_sha256=sha(PRIOR/'results.json'),
        workspace=str(ws), output_base=str(out), runs=[],
        method='Existing binary. Fresh JVM/clean graph per run. Frozen catalogs and independent caches. '
               'Loopback timing proxy in remote arms. JFR diagnostic runs excluded from timing comparisons.')
    save(ROOT/'results.json', results)
    if args.agent:
        results['diagnostic_agent_sha256'] = sha(args.agent)
        results['diagnostic_agent_path'] = str(args.agent.resolve())
    shutil.copytree(PRIOR/'metadata-cas', ROOT/'metadata-cas', copy_function=os.link)
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

    def run(label, arm, jfr=False):
        nonlocal worker, proxy_server, proxy
        row = dict(label=label, arm=arm, jfr=jfr)
        results['runs'].append(row)
        save(ROOT/'results.json', results)
        shutdown(label+'-shutdown-before')
        cache = ROOT/('cache-'+label)
        if arm == 'local':
            for part in ['objects', 'publications-v4']:
                shutil.copytree(PRIOR/'cache-primer/leased-v2'/part, cache/'leased-v2'/part, copy_function=os.link)
        else:
            shutil.copytree(Path(old['retained_seed']['cache'])/'leased-v2/remote-chunks-v1/sha256',
                            cache/'leased-v2/remote-chunks-v1/sha256', copy_function=os.link)
            catalog = ROOT/('catalog-'+label)
            shutil.copytree(PRIOR/'catalog-primer', catalog)
            with socket.socket() as s:
                s.bind(('127.0.0.1', 0))
                port = s.getsockname()[1]
            logfile = ROOT/(label+'-worker.log')
            with logfile.open('x') as log:
                worker = subprocess.Popen([str(worker_bin),'--listen_address=127.0.0.1',
                    '--listen_port='+str(port),'--cas_path='+str(ROOT/'metadata-cas'),
                    '--skycache_catalog_path='+str(catalog),'--skycache_instance_name=datadog-bench'],
                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic()+60
            while 'Not starting HTTP cache server' not in logfile.read_text():
                assert worker.poll() is None and time.monotonic()<deadline, 'worker readiness'
                time.sleep(.1)
            warm_python = REPOS/'.work/skycache-remote-mvp/benchmark-rpc-venv/bin/python'
            simple([str(warm_python),str(SOURCE/'tools/skycache/remote_benchmark_warmup.py'),
                '--endpoint',f'127.0.0.1:{port}','--catalog',str(catalog),'--rounds','1',
                '--output',str(ROOT/(label+'-warmup.json'))],label+'-warmup',600)
            proxy = TimingProxy(f'127.0.0.1:{port}', ROOT/(label+'-rpc.jsonl'))
            proxy_server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=100), options=[
                ('grpc.max_receive_message_length',64*1024**2),('grpc.max_send_message_length',64*1024**2)])
            proxy_server.add_generic_rpc_handlers((proxy,))
            proxy_port = proxy_server.add_insecure_port('127.0.0.1:0')
            proxy_server.start()
        simple(startup+['clean','--noasync','--noexpunge','--color=no','--curses=no',
            '--experimental_local_skycache='+str(cache),
            '--symlink_prefix='+old['fixture_reuse']['scratch']+'/links/bazel-skycache-'],label+'-clean')
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
        replace={'--experimental_local_skycache=':str(cache),
                 '--build_event_json_file=':str(ROOT/(label+'.bep.json')),
                 '--profile=':str(ROOT/(label+'.profile.gz')),
                 '--remote_grpc_log=':str(ROOT/(label+'.grpc.bin'))}
        transformed=[]
        for arg in args:
            if arg.startswith('--experimental_remote_skycache'):
                if arm=='local':
                    continue
                if arg.startswith('--experimental_remote_skycache='):
                    arg=f'--experimental_remote_skycache=grpc://127.0.0.1:{proxy_port}'
            for prefix,value in replace.items():
                if arg.startswith(prefix):
                    arg=prefix+value
                    break
            transformed.append(arg)
        row['argv']=transformed
        row['start_epoch']=time.time()
        t=time.monotonic()
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
        row['end_epoch']=time.time()
        if jfr:
            simple([str(JDK/'bin/jcmd'),str(pid),'JFR.stop','name=skycache',
                    'filename='+str(ROOT/(label+'.jfr'))],label+'-jfr-stop')
        text=''.join(lines)
        for name in ['local-skycache','local-skycache-validation','remote-skycache']:
            p=out/(name+'.json')
            if p.exists() and p.stat().st_mtime>=row['start_epoch']:
                row[name]=json.loads(p.read_text())
        row['analysis_hits']=sum(int(x) for x in re.findall(r'CONFIGURED_TARGET: (\d+)/',text))
        row['execution_hits']=sum(int(x) for x in re.findall(r'ACTION_EXECUTION: (\d+)/',text))
        row['committed']='Remote SkyCache: publication committed' in text
        row['errors']=re.findall(r'(?:SkyCache (?:publication|import) (?:skipped|discarded)|SkyCache disabled):[^\n]*',text)
        events=[json.loads(x) for x in (ROOT/(label+'.bep.json')).read_text().splitlines()]
        outputs={}
        for event in events:
            for file in event.get('namedSetOfFiles',{}).get('files',[]):
                for name in old['expected_binaries']:
                    if file.get('name','').endswith('/'+name+'_/'+name):
                        outputs[name]=file.get('digest')
        row['output_digests']=outputs
        row['outputs_match']=outputs==old['expected_binaries']
        row['build_metrics']=next((x['buildMetrics'] for x in events if 'buildMetrics' in x),{})
        profile=json.loads(gzip.open(ROOT/(label+'.profile.gz'),'rt').read())['traceEvents']
        row['profile_end_s']=max(x.get('ts',0)+x.get('dur',0) for x in profile)/1e6
        row['outside_profile_s']=row['seconds']-row['profile_end_s']
        stop_worker()
        if arm!='local':
            row['rpc']=[json.loads(x) for x in (ROOT/(label+'-rpc.jsonl')).read_text().splitlines()]
        save(ROOT/'results.json',results)
        print(json.dumps({k:row[k] for k in ['label','seconds','profile_end_s','outside_profile_s','analysis_hits','execution_hits','outputs_match','committed']}),flush=True)
        assert row['exit_code']==0 and row['outputs_match'] and not row['errors'], label
        assert row['analysis_hits']==17 and row['execution_hits']==11, label
        assert int(pidfile.read_text())==pid
        if arm!='local':
            assert row['remote-skycache']['downloaded_bytes']==0 and row['committed'],label
            assert all(x['status']=='OK' for x in row['rpc'])

    try:
        run('diagnostic-remote','remote-warm',True)
        if not args.focused:
            run('r1-remote','remote-warm')
            run('r1-local','local')
            run('r2-local','local')
            run('r2-remote','remote-warm')
            run('diagnostic-local','local',True)
        results['status']='passed'
    except BaseException as error:
        results['status']='failed';results['failure']=repr(error)
        raise
    finally:
        try:
            shutdown('final-shutdown')
            stop_worker()
        finally:
            results['source_states_unchanged']=all(state(Path(p))==v for p,v in before.items())
            results['binary_unchanged']=sha(binary)==old['binary_sha256']['candidate']
            save(ROOT/'results.json',results)
            lock.close()


if __name__=='__main__':
    main()
