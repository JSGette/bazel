"""Datadog full builds on E-backed WSL ext4 with Cache & Carry.

Two preparation builds; three rotated rounds of vanilla, SkyCache off and on.
Each round has a fresh server, two warm builds, a 20-second idle interval,
and recovery. Blocking waitpid wall timings; 100-ms process-tree RSS/PSS.
Never overwrite a run. Stop on any build, output, storage, or cache failure.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse, fcntl, hashlib, json, os, re, shutil, signal, socket, subprocess, sys, threading, time

ROOT = Path('/home/jgett/REPOS/.work/skycache-v2-datadog-ssd-2026-10-25')
SOURCE = Path('/home/jgett/REPOS/bazel-skycache-v2')
FIXTURE = Path('/home/jgett/REPOS/.work/skycache-concurrent/datadog-crossrevision-mapping-eviction-review')
WS = FIXTURE / 'a'
JDK = Path('/home/jgett/REPOS/.tools/skycache-benchmark-jdk25')
TARGET = '//cmd/cluster-agent:cluster-agent'
REV = '0cde96beb70963d0838119c308b9d0422ee66be0'
TASK = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
os.environ['SKYCACHE_WINDOWS_STORAGE_SNAPSHOT']=str(TASK/'work/datadog-storage-live.json')
sys.path.insert(0, str(SOURCE / 'tools/skycache'))
from datadog_probe import process_memory
from storage_preflight import check_storage

def digest(p):
    with Path(p).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()
def git(p, *args): return subprocess.check_output(['git','-C',str(p),*args],text=True).strip()
def save(p, data):
    tmp=p.with_suffix(p.suffix+'.tmp'); tmp.write_text(json.dumps(data,indent=2)+'\n'); tmp.replace(p)
def storage(): return check_storage([ROOT], minimum_bytes=15*1024**3)
def stamp(): return datetime.now(timezone.utc).isoformat()
def output(variant): return ROOT / ('out-vanilla' if variant=='vanilla' else 'out-skycache')
def pid(variant):
    try: return int((output(variant)/'server/server.pid.txt').read_text())
    except (OSError,ValueError): return 0
def environment(variant):
    env=dict(os.environ, BAZEL_REAL=str(ROOT/'bin'/('vanilla' if variant=='vanilla' else 'skycache')),
             BAZELISK_SKIP_WRAPPER='true', DD_BAZEL_REMOTE_CACHE='off', TMPDIR=str(ROOT/'tmp'))
    env.pop('PWD',None); env.pop('OLDPWD',None); env.pop('XDG_CACHE_HOME',None)
    return env
def startup(variant):
    return [str(WS/'tools/bazel'), '--output_base='+str(output(variant)),
            '--output_user_root=/home/jgett/.cache/bazel', '--server_javabase='+str(JDK.resolve()),
            '--host_jvm_args=-Xmx6g','--max_idle_secs=600']
def command(variant,label,retention=False):
    # Disable wrapper auto-selection, then explicitly select Cache & Carry. No disk action cache.
    args=startup(variant)+['build',TARGET,'--jobs=16','--local_resources=memory=6144',
       '--lockfile_mode=off','--experimental_strict_repo_env','--color=no','--curses=no',
       '--disk_cache=','--remote_cache=grpc://10.55.0.1:8080','--remote_instance_name=',
       '--remote_timeout=120','--remote_retries=1','--noremote_local_fallback',
       '--remote_upload_local_results','--remote_accept_cached','--remote_download_outputs=all',
       '--bes_backend=','--repo_env=JAVA_HOME='+str(JDK),
       '--symlink_prefix='+str(ROOT/'links'/('bazel-'+variant+'-')),
       '--build_event_json_file='+str(ROOT/(label+'.bep.json')),
       '--profile='+str(ROOT/(label+'.profile.gz'))]
    if variant!='vanilla':
        args+=['--experimental_local_skycache='+ (str(ROOT/'analysis-cache') if variant=='skycache' else ''),
               '--experimental_active_directories=cmd/cluster-agent',
               '--experimental_local_skycache_gc_max_size=0','--experimental_local_skycache_gc_max_age=0',
               '--experimental_analysis_cache_idle_retention='+('15s' if retention else '0')]
    return args
def shutdown(variant):
    if pid(variant):
        with (ROOT/'shutdown.log').open('a') as log:
            subprocess.run(startup(variant)+['shutdown'],cwd=WS,env=environment(variant),stdout=log,stderr=subprocess.STDOUT,check=True,timeout=90)
def mem(variant):
    p=pid(variant)
    return {'pid':p,'tree_rss_kib':process_memory([p])[0],'tree_pss_kib':process_memory([p])[1]} if p else None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('stage',choices=['prepare','resume_prepare','measure']);args=ap.parse_args()
    lock=(ROOT.parent/'skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if args.stage=='prepare':
        assert not ROOT.exists(), 'Use a new directory; do not overwrite evidence.'
        ROOT.mkdir();(ROOT/'bin').mkdir();(ROOT/'tmp').mkdir();(ROOT/'links').mkdir()
        for name,origin,expected in [
          ('skycache',Path('/mnt/skycache-v2-local/bin/bazel-skycache-v2-1'),'28026d467ef10667c0fcfb1741495fbefe4fee9dfed43d3d2d9733726b5cb159'),
          ('vanilla',Path('/home/jgett/REPOS/.work/bazel-skyframe/bazel-vanilla'),'1ef0d6ff94b24c787d539f63672f93f0da9bd96d49750d729489cbffc80aa588')]:
            assert digest(origin)==expected;shutil.copy2(origin,ROOT/'bin'/name)
            assert digest(ROOT/'bin'/name)==expected
        shutil.copy2(__file__,ROOT/'harness.py');shutil.copy2(TASK/'work/datadog-ssd-hardware.json',ROOT/'hardware.json')
        assert git(WS,'rev-parse','HEAD')==REV
        assert set(git(WS,'diff','--name-only','HEAD').splitlines()) <= {'MODULE.bazel.lock'}
        assert not git(SOURCE,'status','--porcelain')
        with socket.create_connection(('10.55.0.1',8080),5): pass
        report={'started_at':stamp(),'status':'preparing','target':TARGET,'datadog_revision':REV,
          'skycache_commit':git(SOURCE,'rev-parse','HEAD'),'vanilla_commit':'4cfc05f77ba91900a2e3c6ab135861498cb2a0bf',
          'binary_sha256':{n:digest(ROOT/'bin'/n) for n in ['skycache','vanilla']},
          'workspace':str(WS),'workspace_diff_before':git(WS,'diff','--binary','HEAD'),
          'lock_sha256':digest(WS/'MODULE.bazel.lock'),'jdk':str(JDK.resolve()),
          'filesystem':json.loads(subprocess.check_output(['findmnt','-J','-T',str(ROOT),'-o','SOURCE,FSTYPE,TARGET'],text=True)),
          'initial_storage':storage(),'kernel':subprocess.check_output(['uname','-a'],text=True),
          'limits':{'jobs':16,'jvm_heap_gib':6,'action_memory_mib':6144,'host_reserve_gib':15},
          'remote_cache':'grpc://10.55.0.1:8080','disk_action_cache':False,'runs':[],'cycles':[],
          'limitations':['Warm OS page cache; no machine-wide cache drop.',
            'Sampled guest process tree RSS/PSS excludes Windows/WSL overhead and filesystem page cache.',
            'Vanilla is the source-built pre-SkyCache fork baseline; not the 9.3.0 release binary.',
            'Idle eviction includes explicit GC; idle recovery is a memory/latency tradeoff.',
            'Preparation populates action state; repeated measurements use existing local outputs and Cache & Carry remains enabled.']}
        save(ROOT/'results.json',report)
    elif args.stage=='resume_prepare':
        report=json.loads((ROOT/'results.json').read_text())
        assert report['status']=='failed' and report['runs'][-1]['guard_errors']
        shutil.copy2(ROOT/'harness.py',ROOT/'harness-initial-interop-guard.py')
        shutil.copy2(__file__,ROOT/'harness.py')
        report['setup_retry_reason']='WSL interop storage checks failed; use a fresh Windows-side snapshot watcher.'
        report['status']='preparing';save(ROOT/'results.json',report)
    else:
        report=json.loads((ROOT/'results.json').read_text());assert report['status']=='prepared'
        assert digest(WS/'MODULE.bazel.lock')==report['lock_sha256']
        report['status']='measuring';save(ROOT/'results.json',report)

    def build(variant,label,role,retention=False):
        assert not (ROOT/(label+'.log')).exists()
        before=storage();argv=command(variant,label,retention)
        row={'variant':variant,'label':label,'role':role,'argv':argv,'started_at':stamp(),'storage_before':before,'before':mem(variant)}
        report['runs'].append(row);save(ROOT/'results.json',report)
        files=['local-skycache.json','local-skycache-validation.json']
        prior={n:(output(variant)/n).stat().st_mtime_ns if (output(variant)/n).exists() else 0 for n in files}
        samples=[];stop=threading.Event();errors=[];guards=[]
        with (ROOT/(label+'.log')).open('x') as log:
            t=time.monotonic();proc=subprocess.Popen(argv,cwd=WS,env=environment(variant),stdout=log,stderr=subprocess.STDOUT)
            def sample():
                while not stop.is_set():
                    samples.append([time.monotonic()-t,*process_memory([proc.pid,pid(variant)])]);stop.wait(.1)
            def guard():
                while not stop.wait(10):
                    try: guards.append({'elapsed':time.monotonic()-t,'storage':storage()})
                    except Exception as e:
                        errors.append(str(e));proc.send_signal(signal.SIGINT);return
            def timeout():
                errors.append('1800-second watchdog expired');proc.send_signal(signal.SIGINT)
            sampler=threading.Thread(target=sample);monitor=threading.Thread(target=guard);watchdog=threading.Timer(1800,timeout)
            sampler.start();monitor.start();watchdog.start()
            try: code=proc.wait();seconds=time.monotonic()-t
            finally: stop.set();sampler.join();monitor.join();watchdog.cancel();watchdog.join()
        text=(ROOT/(label+'.log')).read_text(errors='replace')
        row.update(exit_code=code,seconds=seconds,after=mem(variant),guard_errors=errors,
          peak_tree_rss_kib=max((s[1] for s in samples),default=0),peak_tree_pss_kib=max((s[2] for s in samples),default=0),
          analysis_hits=sum(int(a) for a,b in re.findall(r'CONFIGURED_TARGET: (\d+)/(\d+)',text)),
          cache_disabled='Local SkyCache disabled:' in text,
          publication_failures=re.findall(r'Local SkyCache publication (?:discarded|skipped): (.*)',text))
        save(ROOT/(label+'.samples.json'),samples);save(ROOT/(label+'.storage.json'),guards)
        for n in files:
            p=output(variant)/n
            if p.exists() and p.stat().st_mtime_ns!=prior[n]: row[n]=json.loads(p.read_text())
        bep=ROOT/(label+'.bep.json')
        if bep.exists():
            for line in bep.open():
                e=json.loads(line)
                if 'buildMetrics' in e: row['build_metrics']=e['buildMetrics']
                if 'finished' in e: row['finished']=e['finished']
        row['runner_counts']=row.get('build_metrics',{}).get('actionSummary',{}).get('runnerCount',[])
        row['action_cache_statistics']=row.get('build_metrics',{}).get('actionSummary',{}).get('actionCacheStatistics',{})
        artifacts=list((output(variant)/'execroot/_main/bazel-out').glob('*/bin/cmd/cluster-agent/cluster-agent_/cluster-agent'))
        if code==0 and len(artifacts)==1: row['output_sha256']=digest(artifacts[0])
        save(ROOT/'results.json',report)
        print(json.dumps({k:row[k] for k in ['label','exit_code','seconds','peak_tree_pss_kib','analysis_hits','runner_counts']}),flush=True)
        assert code==0 and not errors,label
        assert row.get('output_sha256'), 'Missing output '+label
        if 'expected_output_sha256' not in report: report['expected_output_sha256']=row['output_sha256']
        assert row['output_sha256']==report['expected_output_sha256'],label+' output mismatch'
        if variant=='skycache':
            assert not row['cache_disabled'] and not row['publication_failures'],label
            assert not row.get('local-skycache.json',{}).get('failed'),label
        save(ROOT/'results.json',report);return row

    try:
        if args.stage in ['prepare','resume_prepare']:
            for v in ['vanilla','skycache']:
                label='prepare-'+v+('-retry1' if args.stage=='resume_prepare' else '')
                build(v,label,'preparation');shutdown(v)
            report['status']='prepared'
        else:
            orders=[['vanilla','off','skycache'],['skycache','vanilla','off'],['off','skycache','vanilla']]
            for i,order in enumerate(orders,1):
                for v in order:
                    shutdown(v)
                    label=f'r{i}-{v}'
                    build(v,label+'-cold','fresh_server')
                    build(v,label+'-warm1','warm')
                    receipt=output(v)/'skycache-idle-retention.json'
                    if receipt.exists(): receipt.unlink()
                    build(v,label+'-warm2','warm',retention=(v=='skycache'))
                    cycle={'round':i,'variant':v,'label':label,'before_idle':mem(v)};report['cycles'].append(cycle)
                    time.sleep(12);cycle['idle_12s']=mem(v)
                    time.sleep(8);cycle['idle_20s']=mem(v)
                    if receipt.exists(): cycle['receipt']=json.loads(receipt.read_text())
                    save(ROOT/'results.json',report)
                    if v=='skycache': assert cycle.get('receipt'), 'Idle eviction did not occur'
                    build(v,label+'-recovery','idle_recovery')
                    shutdown(v)
            report['status']='passed'
    except BaseException as e:
        report['status']='failed';report['failure']=repr(e);raise
    finally:
        for v in ['vanilla','skycache']:
            try: shutdown(v)
            except Exception as e: report.setdefault('shutdown_errors',[]).append(repr(e))
        report['workspace_unchanged']=git(WS,'diff','--binary','HEAD')==report['workspace_diff_before']
        report['updated_at']=stamp();save(ROOT/'results.json',report);lock.close()

if __name__=='__main__': main()
