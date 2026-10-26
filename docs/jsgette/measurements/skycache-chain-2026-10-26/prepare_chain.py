from pathlib import Path
import fcntl, hashlib, json, os, shutil, signal, socket, subprocess, time, zipfile

REPOS=Path('/home/jgett/REPOS')
ROOT=REPOS/'.work/skycache-chain-2026-10-26'
SEED=REPOS/'.work/skycache-validation-2026-10-26/paired'
WORK=Path(__file__).parent
JDK=REPOS/'.tools/skycache-benchmark-jdk25/bin'

def sha(p):
    with p.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def main():
    lock=(REPOS/'.work/skyframe-measurements.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    ROOT.mkdir(exist_ok=False)
    original=json.loads((SEED/'results.json').read_text())
    assert original['status']=='passed'
    binary=Path(original['candidate_binary'])
    assert sha(binary)==original['candidate_binary_sha256']
    with zipfile.ZipFile(binary) as z:
        (ROOT/'server.jar').write_bytes(z.read('A-server.jar'))
    for source,target in [('metadata-cas-candidate','metadata-cas'),('catalog-seed-candidate','catalog-seed')]:
        shutil.copytree(SEED/source,ROOT/target,copy_function=os.link if source.startswith('metadata') else shutil.copy2)
    shutil.copytree(SEED/'cache-warmup-candidate/leased-v2/remote-chunks-v1/sha256', ROOT/'retained-seed',copy_function=os.link)
    shutil.copytree(ROOT/'retained-seed',ROOT/'local-seed/leased-v2/remote-chunks-v1/sha256',copy_function=os.link)
    old=json.loads((REPOS/'.work/skycache-retained-chunks-2026-10-25/agent-matrix/results.json').read_text())
    worker_bin=Path(old['binary_paths']['worker'])
    namespace=next((SEED/'cache-produce-candidate/leased-v2/publications-v4').iterdir()).name
    subprocess.run([str(JDK/'javac'),'-proc:none','-cp',str(ROOT/'server.jar'),'-d',str(ROOT/'helper-classes'),str(WORK/'HydrateSkycacheSeed.java')],check=True)
    # Low-frequency stage boundaries only; the hot record-read path is sampled using JFR.
    probe=(WORK/'SkycacheStageProbe.java').read_text().replace('SkycacheStageProbe','SkycacheChainProbe')
    start=probe.index('  static final Map<String, Set<String>> METHODS =')
    end=probe.index('\n\n  public static void enter',start)
    methods={
      'LocalSkycacheModule':['beforeCommand','afterCommand'],
      'LocalCacheSession':['refreshCandidates','finish','writeWitnesses','closeStores'],
      'LocalInputSnapshot':['captureNamespace','captureSource','recordRepositoryBindings','registerInheritedForFinalValidation','isUnchanged','hasSameGlobalWitnesses'],
      'LocalWitnessTable':['read','write','copy'],
      'LocalAnalysisCacheClient':['lookup'],
      'SegmentedFingerprintValueStore':['open','seal'],
      'RemoteSkyCache':['prepareExclusive','reuseRetainedChunk','publishPending','uploadLocal','commit','close'],
    }
    mapping='  static final Map<String, Set<String>> METHODS = Map.ofEntries(\n'+',\n'.join('      Map.entry('+json.dumps(k)+', Set.of('+','.join(json.dumps(v) for v in vs)+'))' for k,vs in methods.items())+');'
    probe=probe[:start]+mapping+probe[end:]
    (ROOT/'SkycacheChainProbe.java').write_text(probe)
    (ROOT/'probe-manifest.mf').write_text('Manifest-Version: 1.0\nPremain-Class: SkycacheChainProbe\n\n')
    subprocess.run([str(JDK/'javac'),'-d',str(ROOT/'probe-classes'),str(ROOT/'SkycacheChainProbe.java')],check=True)
    subprocess.run([str(JDK/'jar'),'--create','--file',str(ROOT/'chain-probe.jar'),'--manifest',str(ROOT/'probe-manifest.mf'),'-C',str(ROOT/'probe-classes'),'.'],check=True)
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    with (ROOT/'hydrate-worker.log').open('x') as log:
        worker=subprocess.Popen([str(worker_bin),'--listen_address=127.0.0.1','--listen_port='+str(port),'--cas_path='+str(ROOT/'metadata-cas'),'--skycache_catalog_path='+str(ROOT/'catalog-seed'),'--skycache_instance_name=datadog-bench'],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    try:
        deadline=time.monotonic()+60
        while 'Not starting HTTP cache server' not in (ROOT/'hydrate-worker.log').read_text():
            assert worker.poll() is None and time.monotonic()<deadline
            time.sleep(.1)
        command=[str(JDK/'java'),'-Xmx4g','-cp',str(ROOT/'helper-classes')+':'+str(ROOT/'server.jar'),'com.google.devtools.build.lib.skyframe.serialization.local.HydrateSkycacheSeed',str(ROOT/'local-seed'),namespace,'grpc://127.0.0.1:'+str(port)]
        (ROOT/'hydrate-command.json').write_text(json.dumps(command,indent=2)+'\n')
        with (ROOT/'hydrate.log').open('x') as log:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
        files=[]
        for part in ['objects','publications-v4']:
            for path in sorted((ROOT/'local-seed/leased-v2'/part).rglob('*')):
                if path.is_file(): files.append(dict(path=str(path.relative_to(ROOT/'local-seed')),bytes=path.stat().st_size,sha256=sha(path)))
        manifests=list((ROOT/'local-seed/leased-v2/publications-v4').rglob('*.manifest'))
        assert len(manifests)==2
        result=dict(binary=str(binary),binary_sha256=sha(binary),source_base=subprocess.check_output(['git','-C',str(REPOS/'bazel-skycache-remote-mvp'),'rev-parse','HEAD'],text=True).strip(),namespace=namespace,
            source_catalog=str(SEED/'catalog-seed-candidate'),source_results_sha256=sha(SEED/'results.json'),
            publications=sorted(p.stem for p in manifests),files=files,logical_bytes=sum(f['bytes'] for f in files),
            method='Production RemoteSkyCache.hydrate authenticated and reconstructed the exact remote seed as local publication files. Both arms use these same publication IDs and bytes.',
            probe_sha256=sha(ROOT/'chain-probe.jar'))
        (ROOT/'preparation.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({k:result[k] for k in ['binary_sha256','publications','logical_bytes','method']}),flush=True)
    finally:
        if worker.poll() is None:
            os.killpg(worker.pid,signal.SIGTERM)
            try: worker.wait(15)
            except subprocess.TimeoutExpired:
                os.killpg(worker.pid,signal.SIGKILL);worker.wait()
        lock.close()

if __name__=='__main__':main()
