"""Hydrate exact frozen remote publications through the production importer."""
import fcntl
import shutil
import signal
import socket
import time
import zipfile
from skycache_all_common import *

lock=(REPOS/'.work/skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
seed=ROOT/'local-seed'
assert not seed.exists()
prime=list(ROOT.glob('cache-*-remote/leased-v2/publications-v4'))
namespaces={p.name for folder in prime for p in folder.iterdir() if p.is_dir() and any(p.glob('*.manifest'))}
assert len(namespaces)==1,namespaces
namespace=next(iter(namespaces))
with zipfile.ZipFile(CANDIDATE) as z:(ROOT/'server.jar').write_bytes(z.read('A-server.jar'))
source='''package com.google.devtools.build.lib.skyframe.serialization.local;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheTransport;
import java.nio.file.Path;
import java.time.Duration;
public final class HydrateAllSkycacheSeed {
  public static void main(String[] args) throws Exception {
    var transport = new SkyCacheTransport(args[2], "datadog-bench", "", Duration.ofSeconds(1200), 4L*1024*1024*1024+1024*1024, 100);
    try (var remote = new RemoteSkyCache(Path.of(args[0]), args[1], 256L*1024*1024, "datadog-bench", transport, 32*1024*1024, 100, 8L*1024*1024*1024)) {
      int count=remote.hydrate();
      if(count<1) throw new AssertionError("Empty seed");
      System.out.println("HYDRATED_PUBLICATIONS="+count);
      System.out.println(remote.statisticsJson());
    }
  }
}
'''
(ROOT/'HydrateAllSkycacheSeed.java').write_text(source)
subprocess.run([str(JDK/'bin/javac'),'-proc:none','-cp',str(ROOT/'server.jar'),'-d',str(ROOT/'helper-classes'),
                str(ROOT/'HydrateAllSkycacheSeed.java')],check=True)
with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
with (ROOT/'seed-hydrate-worker.log').open('x') as log:
    worker=subprocess.Popen([str(WORKER),'--listen_address=127.0.0.1','--listen_port='+str(port),
        '--cas_path='+str(ROOT/'metadata-cas-seed'),'--skycache_catalog_path='+str(ROOT/'catalog-seed'),
        '--skycache_instance_name=datadog-bench'],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
try:
    deadline=time.monotonic()+60
    while 'Not starting HTTP cache server' not in (ROOT/'seed-hydrate-worker.log').read_text():
        assert worker.poll() is None and time.monotonic()<deadline
        time.sleep(.1)
    command=[str(JDK/'bin/java'),'-Xmx4g','-cp',str(ROOT/'helper-classes')+':'+str(ROOT/'server.jar'),
        'com.google.devtools.build.lib.skyframe.serialization.local.HydrateAllSkycacheSeed',str(seed),namespace,
        'grpc://127.0.0.1:'+str(port)]
    save(ROOT/'seed-hydrate-command.json',command)
    start=time.monotonic()
    with (ROOT/'seed-hydrate.log').open('x') as log:
        subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1800)
    seconds=time.monotonic()-start
    files=[]
    for part in ['objects','publications-v4']:
        for p in sorted((seed/'leased-v2'/part).rglob('*')):
            if p.is_file():files.append(dict(path=str(p.relative_to(seed)),bytes=p.stat().st_size,sha256=sha(p)))
    retained=seed/'leased-v2/remote-chunks-v1/sha256'
    assert retained.exists()
    # Supplement the consumer's retained set with the child publication's authenticated chunks.
    for p in retained.rglob('*'):
        if p.is_file():
            target=ROOT/'retained-seed'/p.relative_to(retained)
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():assert sha(target)==sha(p)
            else:shutil.copy2(p,target)
    manifests=list((seed/'leased-v2/publications-v4').rglob('*.manifest'))
    save(ROOT/'seed-preparation.json',dict(binary=str(CANDIDATE),binary_sha256=sha(CANDIDATE),
        namespace=namespace,publications=sorted(p.stem for p in manifests),files=files,
        logical_bytes=sum(f['bytes'] for f in files),hydrate_seconds=seconds,
        method='Exact frozen remote publications reconstructed and authenticated by native production RemoteSkyCache.hydrate.'))
    print(json.dumps(dict(publications=len(manifests),logical_bytes=sum(f['bytes'] for f in files),seconds=seconds)),flush=True)
finally:
    if worker.poll() is None:
        os.killpg(worker.pid,signal.SIGTERM)
        try:worker.wait(15)
        except subprocess.TimeoutExpired:os.killpg(worker.pid,signal.SIGKILL);worker.wait()
    lock.close()
