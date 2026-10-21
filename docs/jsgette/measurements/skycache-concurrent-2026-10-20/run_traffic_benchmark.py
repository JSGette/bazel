from pathlib import Path
import fcntl
import hashlib
import json
import statistics
import subprocess

repo = Path('/home/jgett/REPOS/bazel-skycache-concurrent')
work = Path('/home/jgett/REPOS/.work/skycache-concurrent')
fixture = json.loads((work/'admission-validation-fixture.json').read_text())
args = [fixture[k] for k in ('cache','workspace','output_base','sampled_roots','environment_file_local_only')]
assert hashlib.sha256(Path(args[3]).read_bytes()).hexdigest() == fixture['sampled_roots_sha256']
assert hashlib.sha256(Path(args[4]).read_bytes()).hexdigest() == fixture['environment_file_sha256']
java = '/home/jgett/REPOS/.work/skyframe-go/work/build-output/external/rules_java++toolchains+remotejdk25_linux/bin/java'
metadata = {v:json.loads((work/('traffic-stats-'+v+'-binary.json')).read_text()) for v in ('before','after')}
for m in metadata.values():
    assert hashlib.sha256(Path(m['path']).read_bytes()).hexdigest() == m['sha256']
assert metadata['before']['benchmark_source_sha256'] == metadata['after']['benchmark_source_sha256']
result_file = work/'traffic-stats-paired.json'
assert not result_file.exists()
result = {'purpose':'Measure overhead of logical traffic statistics with the unchanged 512-root combined admission/validation benchmark.',
          'scope':'Includes manifest/index admission; excludes snapshot creation/final revalidation. Java allocation is current-thread allocation, not retained or native memory.',
          'environment_values':'Private exact fixture environment; no identity or manifest rewrite.',
          'warmups_per_jvm':2,'measured_rounds_per_jvm':5,
          'binaries':{v:{k:x for k,x in m.items() if k!='diff'} for v,m in metadata.items()},'runs':[]}

def inventory():
    root = Path(fixture['cache'])
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*.manifest'))}

with open('/home/jgett/REPOS/.work/skyframe-measurements.lock','a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    for pair,order in enumerate((('before','after'),('after','before'),('before','after'))):
        for variant in order:
            assert inventory() == fixture['publication_inventory']
            argv = [java,'-Xms256m','-Xmx2g','-jar',metadata[variant]['path'],*args]
            completed = subprocess.run(argv,cwd=repo,capture_output=True,text=True,timeout=600)
            (work/f'traffic-stats-{pair}-{variant}.log').write_text(completed.stdout+'\n'+completed.stderr)
            completed.check_returncode()
            row = json.loads(completed.stdout)
            assert row['sampled_roots']==512 and len(row['rounds'])==5
            for r in row['rounds']:
                assert r['fresh_stats']['records_read']==101755 and r['fresh_stats']['rejected_roots']==0
                assert r['store_stats']['peak_candidate_count']==4 and r['store_stats']['shared_reads']>0
                assert r['store_stats']['writes']==0 and r['store_stats']['rejected_publications']==0
                assert r['store_stats']['read_limit_skips']==0
                if variant == 'after':
                    assert r['store_stats']['cas_get_requests']==101755
                    assert r['store_stats']['cas_get_hits']==101755 and r['store_stats']['cas_get_misses']==0
                    assert r['store_stats']['cas_received_value_bytes']>0 and r['store_stats']['cas_put_requests']==0
            assert inventory() == fixture['publication_inventory']
            result['runs'].append({'pair':pair,'mode':variant,'argv':argv,**row})
            result_file.write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps({'pair':pair,'mode':variant,'fresh_median_ms':statistics.median(r['fresh_ms'] for r in row['rounds'])}),flush=True)
    result['medians'] = {v:{metric:statistics.median(r[metric] for run in result['runs'] if run['mode']==v for r in run['rounds'])
        for metric in ('fresh_ms','fresh_allocated_bytes','memo_ms','memo_allocated_bytes')} for v in ('before','after')}
    result['passed'] = True
    result_file.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['medians'],indent=2),flush=True)
