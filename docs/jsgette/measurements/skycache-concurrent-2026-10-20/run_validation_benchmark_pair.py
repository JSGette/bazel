"""Measure preserved before/after jars on identical roots in alternating order."""
from pathlib import Path
import fcntl
import hashlib
import json
import statistics
import subprocess

repo = Path('/home/jgett/REPOS/bazel-skycache-concurrent')
work = Path('/home/jgett/REPOS/.work/skycache-concurrent')
original = json.loads((work/'dependency-validator-benchmark.json').read_text())
args = original['argv'][1:]
roots = Path(args[-1])
assert hashlib.sha256(roots.read_bytes()).hexdigest() == original['sampled_root_file_sha256']
java = '/home/jgett/REPOS/.work/skyframe-go/work/build-output/external/rules_java++toolchains+remotejdk25_linux/bin/java'
result_file = work/'dependency-validator-witness-paired.json'
assert not result_file.exists()
metadata = {mode: json.loads((work/('dependency-validator-witness-'+mode+'-binary.json')).read_text())
            for mode in ('before', 'after')}
for entry in metadata.values():
    assert hashlib.sha256(Path(entry['path']).read_bytes()).hexdigest() == entry['sha256']
result = {'purpose': __doc__, 'scope': 'Validation only; snapshot construction and final content revalidation are outside the timed region. Java current-thread allocation is not process memory or native allocation.',
    'sampled_root_file_sha256': original['sampled_root_file_sha256'], 'fixture_args': args,
    'binaries': {mode: {k:v for k,v in entry.items() if k != 'diff'} for mode,entry in metadata.items()},
    'fresh_jvm_pairs': 3, 'warmup_rounds_per_jvm': 2, 'measured_rounds_per_jvm': 5,
    'runs': []}
with open('/home/jgett/REPOS/.work/skyframe-measurements.lock', 'a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    for pair in range(3):
        for mode in (('before','after') if pair % 2 == 0 else ('after','before')):
            argv = [java, '-Xms256m', '-Xmx2g', '-jar', metadata[mode]['path'], *args]
            completed = subprocess.run(argv, cwd=repo, capture_output=True, text=True, timeout=600)
            (work/f'dependency-validator-witness-{pair}-{mode}.log').write_text(completed.stdout+'\n'+completed.stderr)
            completed.check_returncode()
            row = json.loads(completed.stdout)
            assert len(row['rounds']) == 5 and row['sampled_roots'] == 512
            for measured in row['rounds']:
                assert measured['fresh_stats']['rejected_roots'] == 0
                assert measured['fresh_stats']['records_read'] == 119969
                assert measured['memo_stats']['records_read'] == measured['fresh_stats']['records_read']
            result['runs'].append({'pair':pair, 'mode':mode, 'argv':argv, **row})
            result_file.write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps({'pair':pair, 'mode':mode, 'fresh_median_ms':statistics.median(r['fresh_ms'] for r in row['rounds'])}),flush=True)
    result['medians'] = {mode: {metric: statistics.median(row[metric] for run in result['runs'] if run['mode']==mode for row in run['rounds'])
        for metric in ('fresh_ms','fresh_allocated_bytes','memo_ms','memo_allocated_bytes')}
        for mode in ('before','after')}
    result['passed'] = True
    result_file.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['medians'],indent=2),flush=True)
