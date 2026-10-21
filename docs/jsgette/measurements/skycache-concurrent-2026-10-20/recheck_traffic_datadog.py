"""Recheck two Datadog servers using preserved worktrees/output bases to limit disk growth."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time

repo = Path('/home/jgett/REPOS/bazel-skycache-concurrent')
work = Path('/home/jgett/REPOS/.work/skycache-concurrent')
sys.path.insert(0,str(repo/'tools/skycache'))
from storage_preflight import check_storage

root = work/'datadog-crossrevision-admission-smoke'
saved = work/'traffic-stats-datadog-recheck'
binary = work/'bazel-depset-runfiles-aspect-mapping-traffic-v2-validator-symlink-packed-review'
assert root.resolve().parent == work.resolve() and not saved.exists()
old = json.loads((root/'results.json').read_text())
assert old['passed']
core = json.loads((work/'datadog-crossrevision-mapping-eviction-review/results.json').read_text())
baseline = core['phases'][0]['builds'][0]['output_sha256']
features = {b['worktree']:b['output_sha256'] for p in core['phases'] if p['phase']=='first-cross-revision-readers' for b in p['builds']}
commands = {b['worktree']:b['argv'] for p in old['phases'] if p['phase']=='restarted-base-readers' for b in p['builds']}
source = Path(old['feature_edit'])
assert not source.is_absolute() and '..' not in source.parts
originals = {}
for name in ('a','b'):
    originals[name] = subprocess.check_output(['git','-C',str(root/name),'show','HEAD:'+str(source)])
    assert (root/name/source).read_bytes() == originals[name]
    assert originals[name].count(b'version.MakeCommand("Cluster Agent")') == 1

def inventory():
    return {str(p.relative_to(root/'cache')):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((root/'cache').rglob('*.manifest'))}

protocol = {'binary':str(binary),'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
    'reused_fixture':str(root),'purpose':'Large-workload traffic and invalidation correctness; not a fresh-cache or controlled timing comparison.',
    'reuse':'Existing independent worktrees and output bases; fresh JVMs, existing complete cache publications. Prior logs/results preserved; source restored.',
    'resources':'Two 4 GiB heaps, two action jobs each; disk action cache on, remote action cache off.',
    'correctness_reference':'Step33 independent cache-off, no-disk-cache controls.',
    'publications_before':inventory(),'phases':[],'passed':False}
log_offsets = {str(p):p.stat().st_size for n in ('a','b') for p in (root/('out-'+n)).glob('java.log.*')}

def save():
    (saved/'results.json').write_text(json.dumps(protocol,indent=2)+'\n')

def environment(name,phase):
    return dict(os.environ,PWD=str(root/name),BAZEL_REAL=str(binary),BAZELISK_SKIP_WRAPPER='true',
                DD_BAZEL_REMOTE_CACHE='off',SKYCACHE_UNRELATED_SESSION=name+'-'+phase)

def startup(name):
    return commands[name][:commands[name].index('build')]

def shutdown(name):
    subprocess.run(startup(name)+['shutdown'],cwd=root/name,env=environment(name,'shutdown'),
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=90,check=True)

def build(name,phase):
    argv = [('--build_event_json_file='+str(saved/(phase+'-'+name+'.bep.json')))
            if a.startswith('--build_event_json_file=') else a for a in commands[name]]
    log_path = saved/(phase+'-'+name+'.log')
    started = time.monotonic()
    with log_path.open('w') as log:
        result = subprocess.run(argv,cwd=root/name,env=environment(name,phase),
                                stdout=log,stderr=subprocess.STDOUT,timeout=300)
    text = log_path.read_text()
    stats = re.findall(r'Local SkyCache: (\{[^\n]+\})',text)
    validation = re.findall(r'Local SkyCache dependency validation: (\{[^\n]+\})',text)
    uploaded = re.findall(r'Skycache write: uploaded (\d+)/(\d+) key/value bytes and (\d+) entries \((\d+) batches\)',text)
    downloaded = re.findall(r'Skycache read: (.+?) received in (\d+) requests,',text)
    row = {'worktree':name,'argv':argv,'exit_code':result.returncode,'seconds':time.monotonic()-started,
           'analysis_hits':sum(int(x) for x in re.findall(r'CONFIGURED_TARGET: (\d+)/\d+',text)),
           'disabled':'Local SkyCache disabled:' in text,
           'publication_failure':'Local SkyCache publication ' in text}
    if stats:
        row['skycache'] = json.loads(stats[-1])
    if validation:
        row['dependency_validation'] = json.loads(validation[-1])
    if uploaded:
        row['native_write'] = dict(zip(('key_bytes','value_bytes','entries','batches'),map(int,uploaded[-1])))
    if downloaded:
        row['native_read'] = {'formatted_bytes':downloaded[-1][0],'requests':int(downloaded[-1][1])}
    if result.returncode == 0:
        artifacts = list((root/('out-'+name)/'execroot/_main/bazel-out').glob('*/bin/cmd/cluster-agent/cluster-agent_/cluster-agent'))
        assert len(artifacts)==1
        with artifacts[0].open('rb') as stream:
            row['output_sha256'] = hashlib.file_digest(stream,'sha256').hexdigest()
        row['server_pid'] = (root/('out-'+name)/'server/server.pid.txt').read_text().strip()
    return row

with open('/home/jgett/REPOS/.work/skyframe-measurements.lock','a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    protocol['initial_storage'] = check_storage([saved])
    saved.mkdir()
    save()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(shutdown,('a','b')))
        expected_pids = {}
        for phase in ('baseline-readers','independent-edits','warm-revert'):
            for name in ('a','b'):
                content = originals[name]
                if phase=='independent-edits':
                    content = content.replace(b'version.MakeCommand("Cluster Agent")',
                        ('version.MakeCommand("Cluster Agent SkyCache-feature-'+name+'")').encode())
                (root/name/source).write_bytes(content)
            preflight = check_storage([saved])
            started = time.monotonic()
            with ThreadPoolExecutor(max_workers=2) as pool:
                rows = [f.result() for f in [pool.submit(build,n,phase) for n in ('a','b')]]
            protocol['phases'].append({'phase':phase,'seconds':time.monotonic()-started,'builds':rows,'storage_preflight':preflight})
            save()
            print(json.dumps({'phase':phase,'rows':[{k:r.get(k) for k in ('worktree','exit_code','seconds','analysis_hits','native_read')} for r in rows]}),flush=True)
            for row in rows:
                name = row['worktree']
                assert row['exit_code']==0 and not row['disabled'] and not row['publication_failure'],row
                assert not row['skycache']['failed'],row
                assert row['output_sha256']==(features[name] if phase=='independent-edits' else baseline),row
                assert row['native_write']['key_bytes']==row['skycache']['cas_put_key_bytes'],row
                assert row['native_write']['value_bytes']==row['skycache']['cas_put_value_bytes'],row
                assert row['native_write']['entries']==row['skycache']['cas_accepted_puts'],row
                assert row['native_write']['batches']==row['skycache']['cas_put_requests'],row
                assert row['skycache']['cas_get_hits']+row['skycache']['cas_get_misses']==row['skycache']['cas_get_requests'],row
                assert row['native_read']['requests']>0 and float(row['native_read']['formatted_bytes'].split()[0])>0,row
                if phase=='baseline-readers':
                    assert row['analysis_hits']>0 and row['skycache']['shared_reads']>0,row
                    expected_pids[name]=row['server_pid']
                else:
                    assert row['server_pid']==expected_pids[name],row
        after = inventory()
        assert all(after.get(p)==digest for p,digest in protocol['publications_before'].items())
        protocol['publications_after'] = after
        protocol['passed'] = True
    finally:
        failures = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            closing = {name:pool.submit(shutdown,name) for name in ('a','b')}
            for name,future in closing.items():
                try:
                    future.result()
                except Exception as error:
                    failures.append({'worktree':name,'error':str(error)})
        protocol['shutdown_failures'] = failures
        if failures:
            protocol['passed'] = False
        else:
            for name in ('a','b'):
                (root/name/source).write_bytes(originals[name])
        diagnostics = []
        for name in ('a','b'):
            for path in sorted((root/('out-'+name)).glob('java.log.*')):
                with path.open('rb') as stream:
                    stream.seek(log_offsets.get(str(path),0))
                    text = stream.read().decode(errors='replace')
                lines = [line for line in text.splitlines() if any(word in line for word in
                         ('Timeout waiting','Skycache invalidation counts','Invalidation lookup took'))]
                if lines:
                    diagnostics.append({'worktree':name,'log':path.name,'lines':lines})
        protocol['server_diagnostics'] = diagnostics
        protocol['invalidation_timeouts'] = sum('Timeout waiting' in line for item in diagnostics for line in item['lines'])
        protocol['sources_restored'] = all((root/n/source).read_bytes()==originals[n] for n in ('a','b'))
        save()
