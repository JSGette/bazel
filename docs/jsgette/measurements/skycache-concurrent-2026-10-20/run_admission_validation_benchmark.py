from pathlib import Path
import fcntl,hashlib,json,statistics,subprocess,sys

mode=sys.argv[1]
assert mode in ('baseline','finish')
repo=Path('/home/jgett/REPOS/bazel-skycache-concurrent')
work=Path('/home/jgett/REPOS/.work/skycache-concurrent')
fixture=json.loads((work/'admission-validation-fixture.json').read_text())
args=[fixture[k] for k in ('cache','workspace','output_base','sampled_roots','environment_file_local_only')]
assert hashlib.sha256(Path(args[3]).read_bytes()).hexdigest()==fixture['sampled_roots_sha256']
assert hashlib.sha256(Path(args[4]).read_bytes()).hexdigest()==fixture['environment_file_sha256']
java='/home/jgett/REPOS/.work/skyframe-go/work/build-output/external/rules_java++toolchains+remotejdk25_linux/bin/java'
result_file=work/'admission-validation-paired.json'
metadata={variant:json.loads((work/('admission-validation-'+variant+'-binary.json')).read_text())
    for variant in (('before',) if mode=='baseline' else ('before','after'))}
for entry in metadata.values():
    assert hashlib.sha256(Path(entry['path']).read_bytes()).hexdigest()==entry['sha256']
if mode=='baseline':
    assert not result_file.exists()
    result={'purpose':'Combined complete manifest admission and dependency validation on the same 512 real Datadog roots.',
        'scope':'Snapshot construction and final content revalidation excluded. Includes opening/validating all admitted manifests/indexes. Java current-thread allocation is not peak memory or native allocation.',
        'sampled_roots_sha256':fixture['sampled_roots_sha256'],
        'environment_values':'Kept local; identity matched the unmodified fixture namespace.',
        'fresh_jvm_pairs':3,'warmups_per_jvm':2,'measured_rounds_per_jvm':5,'runs':[]}
    schedule=[(0,'before')]
else:
    result=json.loads(result_file.read_text())
    assert len(result['runs'])==1 and result['runs'][0]['mode']=='before' and not result.get('passed')
    assert metadata['before']['benchmark_source_sha256']==metadata['after']['benchmark_source_sha256']
    schedule=[(0,'after'),(1,'after'),(1,'before'),(2,'before'),(2,'after')]
result['binaries']={variant:{k:v for k,v in entry.items() if k!='diff'} for variant,entry in metadata.items()}
def inventory():
    root=Path(fixture['cache'])
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*.manifest'))}
with open('/home/jgett/REPOS/.work/skyframe-measurements.lock','a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    for pair,variant in schedule:
        assert inventory()==fixture['publication_inventory']
        argv=[java,'-Xms256m','-Xmx2g','-jar',metadata[variant]['path'],*args]
        completed=subprocess.run(argv,cwd=repo,capture_output=True,text=True,timeout=600)
        (work/f'admission-validation-{pair}-{variant}.log').write_text(completed.stdout+'\n'+completed.stderr)
        if completed.returncode:
            print(completed.stderr[-2000:],flush=True)
        completed.check_returncode()
        row=json.loads(completed.stdout)
        assert row['sampled_roots']==512 and len(row['rounds'])==5
        expected_records=result['runs'][0]['rounds'][0]['fresh_stats']['records_read'] if result['runs'] else row['rounds'][0]['fresh_stats']['records_read']
        for measured in row['rounds']:
            assert measured['fresh_stats']['rejected_roots']==0
            assert measured['fresh_stats']['records_read']==expected_records==measured['memo_stats']['records_read']
            assert measured['store_stats']['peak_candidate_count']==4
            assert measured['store_stats']['shared_reads']>0 and measured['store_stats']['writes']==0
            assert measured['store_stats']['rejected_publications']==0 and measured['store_stats']['read_limit_skips']==0
        assert inventory()==fixture['publication_inventory']
        result['runs'].append({'pair':pair,'mode':variant,'argv':argv,**row})
        result_file.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'pair':pair,'mode':variant,'fresh_median_ms':statistics.median(r['fresh_ms'] for r in row['rounds']),
            'records':expected_records,'witness_checks':row['rounds'][0]['fresh_stats']['witnesses_checked']}),flush=True)
    if mode=='finish':
        result['medians']={variant:{metric:statistics.median(row[metric] for run in result['runs'] if run['mode']==variant for row in run['rounds'])
            for metric in ('fresh_ms','fresh_allocated_bytes','memo_ms','memo_allocated_bytes')} for variant in ('before','after')}
        result['passed']=True
        result_file.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result['medians'],indent=2),flush=True)
