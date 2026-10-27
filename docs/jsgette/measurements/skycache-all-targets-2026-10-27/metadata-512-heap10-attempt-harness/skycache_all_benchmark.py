"""Native-binary, fresh-JVM, full-repository SkyCache measurements.

Setup, dependency/action priming, and failed attempts never enter timing summaries.
Each timed cache consumer starts from its own copy of the same frozen publication set.
"""
import argparse
import collections
import fcntl
import gzip
import re
import shutil
import signal
import socket
import threading
import time
from skycache_all_common import *
from retention_cold_pages import residency

PIDFILE=OUT/'server/server.pid.txt'
CACHEFLAGS=['--experimental_local_skycache_gc_max_size=0',
            '--experimental_local_skycache_max_metadata_mb=512',
            '--experimental_local_skycache_gc_max_age=0',
            '--experimental_analysis_cache_idle_retention=0',
            '--experimental_active_directories=cmd']

def elapsed_clock():
    # WSL's adjusted monotonic clock can slew during host clock synchronization.
    return time.clock_gettime(time.CLOCK_MONOTONIC_RAW)

def state(path):
    return {key:subprocess.check_output(['git','-C',str(path)]+cmd,text=True)
            for key,cmd in [('head',['rev-parse','HEAD']),('status',['status','--porcelain']),('diff',['diff','--binary','HEAD'])]}

def checked_path(path):
    path=Path(path).resolve()
    assert path.is_relative_to(ROOT.resolve()) and path!=ROOT.resolve(),path
    return path

def storage(path):
    files=[p for p in path.rglob('*') if p.is_file() and not p.is_symlink()]
    return dict(files=len(files),bytes=sum(p.stat().st_size for p in files))

def pages(path,cold):
    path=checked_path(path)
    result=dict(files=0,bytes=0,pages=0,resident_before=0,resident_after=0)
    for p in sorted(path.rglob('*')):
        if not p.is_file() or p.is_symlink():continue
        fd=os.open(p,os.O_RDONLY)
        try:
            size=os.fstat(fd).st_size
            count,before=residency(fd,size)
            result['files']+=1;result['bytes']+=size;result['pages']+=count
            result['resident_before']+=before
            os.fsync(fd)
            if cold:os.posix_fadvise(fd,0,0,os.POSIX_FADV_DONTNEED)
            else:
                while os.read(fd,1024*1024):pass
            result['resident_after']+=residency(fd,size)[1]
        finally:os.close(fd)
    result['resident_fraction_after']=result['resident_after']/result['pages'] if result['pages'] else 0
    result['cold']=cold
    assert result['resident_fraction_after']<.001 if cold else result['resident_fraction_after']>.999,result
    return result

def procstat(pid):
    try:
        p=Path('/proc')/str(pid)
        fields=(p/'stat').read_text().split(') ',1)[1].split()
        io={k:int(v) for k,v in [line.split(': ') for line in (p/'io').read_text().splitlines()]}
        return dict(cpu_s=(int(fields[11])+int(fields[12]))/os.sysconf('SC_CLK_TCK'),
                    rss_bytes=int(fields[21])*os.sysconf('SC_PAGE_SIZE'),**io)
    except (FileNotFoundError, PermissionError, ProcessLookupError):return {}

class Runner:
    def __init__(self,tag):
        self.tag=tag
        self.path=ROOT/(tag+'-results.json')
        assert not self.path.exists(),self.path
        self.result=dict(status='running',tag=tag,started_epoch=time.time(),
            source=state(SOURCE),fixture=state(WS),module_lock_sha256=sha(WS/'MODULE.bazel.lock'),
            binaries={k:dict(path=str(v),sha256=sha(v)) for k,v in [('candidate',CANDIDATE),('vanilla',VANILLA),('worker',WORKER)]},
            workload=json.loads((ROOT/'workload.json').read_text()),runs=[])
        assert self.result['binaries']['candidate']['sha256']=='cc20319bc83ccb68f20284f2462eaa0453e69c4e5b17c4be2ebba36510e0194c'
        self.worker=None;self.envs={str(b):environment(b) for b in [CANDIDATE,VANILLA]}
        self.result['environment_path_sha256']={b:hashlib.sha256(env['PATH'].encode()).hexdigest() for b,env in self.envs.items()}
        self.result['system']=dict(cpu_count=os.cpu_count(),kernel=os.uname().release,
            memory={k:v.strip() for k,v in (line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
                    if k in ['MemTotal','SwapTotal']},
            cpu_model=next(line.split(':',1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')))
        self.result['harness_sha256']=sha(__file__)
        self.result['elapsed_timing_clock']='CLOCK_MONOTONIC_RAW'
        self.last_binary=CANDIDATE
        self.persist()

    def persist(self):save(self.path,self.result)

    def simple(self,argv,label,binary,timeout=300):
        with (ROOT/(label+'.log')).open('x') as log:
            result=subprocess.run(argv,cwd=WS,env=self.envs[str(binary)],stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
        assert result.returncode==0,(label,result.returncode)

    def shutdown(self,label):
        if PIDFILE.exists():
            self.simple(STARTUP+['shutdown'],label,self.last_binary,120)

    def worker_start(self,label,cas,catalog):
        assert self.worker is None
        cas.mkdir(exist_ok=True);catalog.mkdir(exist_ok=True)
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        path=ROOT/(label+'-worker.log')
        with path.open('x') as log:
            self.worker=subprocess.Popen([str(WORKER),'--listen_address=127.0.0.1',
                '--listen_port='+str(port),'--cas_path='+str(cas),
                '--skycache_catalog_path='+str(catalog),'--skycache_instance_name=datadog-bench'],
                stdout=log,stderr=subprocess.STDOUT,cwd=ROOT,start_new_session=True)
        deadline=time.monotonic()+60
        while 'Not starting HTTP cache server' not in path.read_text():
            assert self.worker.poll() is None and time.monotonic()<deadline,'metadata worker readiness'
            time.sleep(.1)
        return port

    def worker_stop(self):
        if self.worker:
            if self.worker.poll() is None:
                os.killpg(self.worker.pid,signal.SIGTERM)
                try:self.worker.wait(15)
                except subprocess.TimeoutExpired:os.killpg(self.worker.pid,signal.SIGKILL);self.worker.wait(10)
            self.worker=None

    def run(self,label,arm,*,preparation=False,seed=False,stabilize=False,prime_actions=False,resume_priming=False,timeout=1800):
        self.shutdown(label+'-shutdown-before')
        binary=VANILLA if arm=='vanilla' else CANDIDATE
        self.last_binary=binary
        cached=arm not in ['vanilla','disabled']
        remote=arm.startswith('remote')
        cache=ROOT/('cache-'+label)
        assert not cache.exists()
        extras=[]
        if prime_actions:
            assert preparation and arm=='disabled'
            # Only dependency/action priming: conserve memory while first-time compilers run.
            # Timed commands retain the common jobs=16 and incremental-state settings.
            extras+=['--jobs=16','--local_resources=memory=4096','--local_resources=cpu=2',
                     '--discard_analysis_cache','--notrack_incremental_state','--keep_going']
        if cached:
            extras=CACHEFLAGS+['--experimental_local_skycache='+str(cache)]
        if arm.startswith('local') and not seed:
            for part in ['objects','publications-v4']:
                shutil.copytree(ROOT/'local-seed/leased-v2'/part,cache/'leased-v2'/part)
        if remote:
            cas=ROOT/('cas-'+label);catalog=ROOT/('catalog-'+label)
            if not seed:
                shutil.copytree(ROOT/('metadata-cas-primer' if stabilize else 'metadata-cas-seed'),cas)
                shutil.copytree(ROOT/('catalog-primer' if stabilize else 'catalog-seed'),catalog)
                if arm=='remote-warm':
                    shutil.copytree(ROOT/'retained-seed',cache/'leased-v2/remote-chunks-v1/sha256')
            port=self.worker_start(label,cas,catalog)
            extras+=['--experimental_remote_skycache=grpc://127.0.0.1:'+str(port),
                     '--experimental_remote_skycache_instance_name=datadog-bench',
                     '--experimental_remote_skycache_max_transfer_size=4097M',
                     '--experimental_remote_skycache_local_cache_size=8G',
                     '--experimental_remote_skycache_timeout=1200s']
            if not seed:
                python=REPOS/'.work/skycache-remote-mvp/benchmark-rpc-venv/bin/python'
                self.simple([str(python),str(SOURCE/'tools/skycache/remote_benchmark_warmup.py'),
                    '--endpoint',f'127.0.0.1:{port}','--catalog',str(catalog),'--rounds','1',
                    '--output',str(ROOT/(label+'-worker-warmup.json'))],label+'-worker-warmup',binary,1200)
        row=dict(label=label,arm=arm,preparation=preparation,seed=seed,stabilize=stabilize,prime_actions=prime_actions,resume_priming=resume_priming)
        self.result['runs'].append(row);self.persist()
        started=elapsed_clock()
        clean=['clean','--noasync','--noexpunge','--color=no','--curses=no',
               '--symlink_prefix='+OLD['fixture_reuse']['scratch']+'/links/bazel-skycache-']
        if cached:clean+=['--experimental_local_skycache='+str(cache)]
        if resume_priming:
            assert prime_actions and preparation and arm=='disabled'
            self.simple(STARTUP+['info','server_pid'],label+'-resume-start',binary,600)
        else:
            self.simple(STARTUP+clean,label+'-clean',binary,600)
        row['startup_and_clean_s']=elapsed_clock()-started
        self.simple(STARTUP+['dump','--skyframe=summary'],label+'-empty-graph',binary)
        graph=(ROOT/(label+'-empty-graph.log')).read_text()
        assert 'Node count: 0' in graph and 'Edge count: 0' in graph
        if not resume_priming:assert not (OUT/'action_cache').exists()
        row['empty_graph_verified']=True
        pid=int(PIDFILE.read_text());row['server_pid']=pid
        if not seed:
            if arm.startswith('local'):
                row['metadata_pages']=pages(cache/'leased-v2',arm=='local-cold')
            elif remote:
                row['server_metadata_pages']=pages(cas,arm=='remote-cold')
                if arm=='remote-warm':
                    row['client_metadata_pages']=pages(cache/'leased-v2/remote-chunks-v1/sha256',False)
        for name in ['local-skycache','local-skycache-validation','remote-skycache']:
            (OUT/(name+'.json')).unlink(missing_ok=True)
        argv=command(label,binary,extras)
        row['argv']=argv;row['binary']=str(binary)
        row['start_epoch']=time.time();self.persist()
        psbefore=procstat(pid);wsbefore=procstat(self.worker.pid) if self.worker else {}
        before_swap=Path('/proc/vmstat').read_text()
        adjusted_start=time.monotonic()
        start=elapsed_clock()
        lines=[]
        with (ROOT/(label+'.log')).open('x') as log,(ROOT/(label+'-timeline.jsonl')).open('x') as timeline:
            p=subprocess.Popen(argv,cwd=WS,env=self.envs[str(binary)],stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,text=True,bufsize=1)
            timer=threading.Timer(timeout,lambda:p.send_signal(signal.SIGINT) if p.poll() is None else None)
            timer.start()
            try:
                for line in p.stdout:
                    log.write(line);log.flush();lines.append(line)
                    timeline.write(json.dumps(dict(s=elapsed_clock()-start,text=line.rstrip()))+'\n');timeline.flush()
                row['exit_code']=p.wait(timeout=60)
            finally:
                timer.cancel()
                if p.poll() is None:p.kill();p.wait()
        row['wall_s']=elapsed_clock()-start
        row['adjusted_monotonic_elapsed_s']=time.monotonic()-adjusted_start
        row['end_epoch']=time.time()
        row['wall_clock_elapsed_s']=row['end_epoch']-row['start_epoch']
        row['clock_disagreement_s']=row['wall_clock_elapsed_s']-row['wall_s']
        psafter=procstat(pid);wsafter=procstat(self.worker.pid) if self.worker else {}
        row['client_process_delta']={k:psafter[k]-v for k,v in psbefore.items() if k in psafter and k!='rss_bytes'}
        row['client_rss_after']=psafter.get('rss_bytes')
        row['metadata_worker_delta']={k:wsafter[k]-v for k,v in wsbefore.items() if k in wsafter and k!='rss_bytes'}
        swap=lambda txt:{k:int(v) for k,v in (l.split() for l in txt.splitlines()) if k in ['pswpin','pswpout']}
        b=swap(before_swap);a=swap(Path('/proc/vmstat').read_text())
        row['swap_pages_delta']={k:a[k]-v for k,v in b.items()}
        for name in ['local-skycache','local-skycache-validation','remote-skycache']:
            path=OUT/(name+'.json')
            if path.exists():row[name]=json.loads(path.read_text())
        text=''.join(lines)
        row['analysis_hits']=sum(map(int,re.findall(r'CONFIGURED_TARGET: (\d+)/',text)))
        row['execution_hits']=sum(map(int,re.findall(r'ACTION_EXECUTION: (\d+)/',text)))
        row['cache_errors']=re.findall(r'(?:SkyCache (?:publication|import) (?:skipped|discarded)|SkyCache disabled):[^\n]*',text)
        row['remote_committed']='Remote SkyCache: publication committed' in text
        if row['exit_code'] and 'OutOfMemoryError' in text:row['failure_kind']='jvm_out_of_memory'
        self.persist()
        events=[]
        bep=ROOT/(label+'.bep.json')
        if bep.exists():events=[json.loads(line) for line in bep.read_text().splitlines()]
        row['build_metrics']=next((e['buildMetrics'] for e in events if 'buildMetrics' in e),{})
        row['configured_targets']=row['build_metrics'].get('targetMetrics',{}).get('targetsConfigured',0)
        row['build_finished']=next((e['finished'] for e in events if 'finished' in e),{})
        row['successful_targets']=sorted(e['id']['targetCompleted']['label'] for e in events
            if e.get('completed',{}).get('success') and 'targetCompleted' in e.get('id',{}))
        outputs=collections.defaultdict(set)
        no_digest=set()
        for event in events:
            for f in event.get('namedSetOfFiles',{}).get('files',[]):
                if f.get('digest'):
                    outputs[f.get('name','')].add((f['digest'],int(f.get('length',0))))
                else:
                    no_digest.add(f.get('name',''))
        output_manifest={name:sorted(values) for name,values in sorted(outputs.items())}
        save(ROOT/(label+'-outputs.json'),output_manifest)
        row['output_files']=len(output_manifest)
        row['named_files_without_digest']=sorted(no_digest)
        row['output_manifest_sha256']=sha(ROOT/(label+'-outputs.json'))
        expected=ROOT/'expected-outputs.json'
        if expected.exists():
            row['outputs_match']=json.loads((ROOT/(label+'-outputs.json')).read_text())==json.loads(expected.read_text())
        if (ROOT/'expected-targets.json').exists():
            row['targets_match']=row['successful_targets']==json.loads((ROOT/'expected-targets.json').read_text())
        if row['exit_code']==0:
            self.simple(STARTUP+['dump','--skyframe=summary'],label+'-final-graph',binary)
            summary=(ROOT/(label+'-final-graph.log')).read_text()
            row['final_graph']={name:int(re.search(name+r' count: ([\d,]+)',summary).group(1).replace(',','')) for name in ['Node','Edge']}
        else:row['final_graph']=None
        row['cache_storage']=storage(cache) if cache.exists() else dict(files=0,bytes=0)
        self.worker_stop();self.persist()
        print(json.dumps({k:row.get(k) for k in ['label','wall_s','exit_code','configured_targets','analysis_hits','execution_hits','output_files','outputs_match','cache_errors']}),flush=True)
        assert row['exit_code']==0,(label,row['exit_code'])
        assert not row['cache_errors'],(label,row['cache_errors'])
        if expected.exists():assert row['outputs_match'],label+' outputs'
        if 'targets_match' in row:assert row['targets_match'],label+' targets'
        if cached and not seed:
            assert row['analysis_hits']>0,label+' must reuse analysis'
        if remote and arm=='remote-warm':assert row['remote-skycache']['downloaded_bytes']==0,label+' must download zero blobs'
        if remote and arm=='remote-cold' and not seed:assert row['remote-skycache']['downloaded_bytes']>0,label+' must fetch metadata'
        if not cached:assert row['analysis_hits']==0 and row['execution_hits']==0
        return row

    def finish(self):
        try:self.shutdown(self.tag+'-final-shutdown')
        finally:
            self.worker_stop()
            self.result['source_unchanged']=state(SOURCE)==self.result['source']
            self.result['fixture_unchanged']=state(WS)==self.result['fixture']
            self.result['binary_hashes_unchanged']=all(sha(x['path'])==x['sha256'] for x in self.result['binaries'].values())
            self.persist()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--tag',required=True)
    parser.add_argument('--mode',choices=['prime-actions','prime-local','prime-remote','stabilize','measure'],required=True)
    parser.add_argument('--rounds',type=int,default=3)
    parser.add_argument('--arms',nargs='+',choices=['vanilla','disabled','local-warm','local-cold','remote-warm','remote-cold'])
    parser.add_argument('--resume-priming',action='store_true')
    args=parser.parse_args()
    lock=(REPOS/'.work/skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    runner=Runner(args.tag)
    try:
        if args.mode=='prime-actions':
            row=runner.run(args.tag+'-disabled','disabled',preparation=True,prime_actions=True,resume_priming=args.resume_priming,timeout=10800)
            shutil.copy2(ROOT/(row['label']+'-outputs.json'),ROOT/'expected-outputs.json')
            save(ROOT/'expected-targets.json',row['successful_targets'])
        elif args.mode=='prime-local':
            row=runner.run(args.tag+'-local','local-warm',preparation=True,seed=True,timeout=3600)
            assert row['local-skycache']['published'] and not row['local-skycache']['failed']
            for part in ['objects','publications-v4']:
                shutil.copytree(ROOT/('cache-'+row['label'])/'leased-v2'/part,ROOT/'local-seed/leased-v2'/part)
            save(ROOT/'local-seed-provenance.json',dict(source_label=row['label'],
                output_manifest_sha256=row['output_manifest_sha256'],cache_storage=row['cache_storage'],
                binary_sha256=sha(CANDIDATE),method='Frozen native local publication from the successful full-graph seed under the final action-cache controls.'))
        elif args.mode=='prime-remote':
            row=runner.run(args.tag+'-remote','remote-cold',preparation=True,seed=True,timeout=3600)
            assert row['remote_committed'],'prime must publish'
            shutil.copytree(ROOT/('cas-'+row['label']),ROOT/'metadata-cas-primer')
            shutil.copytree(ROOT/('catalog-'+row['label']),ROOT/'catalog-primer')
        elif args.mode=='stabilize':
            row=runner.run(args.tag+'-remote','remote-cold',preparation=True,stabilize=True,timeout=3600)
            shutil.copytree(ROOT/('cas-'+row['label']),ROOT/'metadata-cas-seed')
            shutil.copytree(ROOT/('catalog-'+row['label']),ROOT/'catalog-seed')
            shutil.copytree(ROOT/('cache-'+row['label'])/'leased-v2/remote-chunks-v1/sha256',ROOT/'retained-seed')
        else:
            arms=args.arms or ['vanilla','disabled','local-warm','local-cold','remote-warm','remote-cold']
            assert len(arms)==len(set(arms))
            for arm in arms:runner.run(args.tag+'-warmup-'+arm,arm,preparation=True)
            third=[arms[i] for i in [2,4,0,5,1,3]] if len(arms)==6 else arms[2:]+arms[:2]
            orders=[arms,list(reversed(arms)),third]
            for roundnum in range(args.rounds):
                for arm in orders[roundnum%len(orders)]:
                    runner.run(args.tag+'-r'+str(roundnum+1)+'-'+arm,arm)
        runner.result['status']='passed'
    except BaseException as e:
        runner.result['status']='failed';runner.result['failure']=repr(e);runner.persist();raise
    finally:runner.finish();lock.close()

if __name__=='__main__':main()
