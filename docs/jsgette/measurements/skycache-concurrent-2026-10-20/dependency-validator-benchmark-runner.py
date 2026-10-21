from pathlib import Path
import fcntl
import hashlib
import json
import struct
import subprocess

repo = Path('/home/jgett/REPOS/bazel-skycache-concurrent')
work = Path('/home/jgett/REPOS/.work/skycache-concurrent')
cache = work / 'cross-jdk9-cache'
manifest = Path(json.loads((work / 'cross-jdk9-reader-input-publications.json').read_text())[0])
writer_id = manifest.stem
pack = cache / 'objects/packed-v1' / writer_id / 'data.pack'
keys = set()
possible_roots = set()
with pack.open('rb') as stream:
    while header := stream.read(44):
        assert len(header) == 44
        magic, key_size, value_size = struct.unpack_from('>iii', header)
        assert magic == 0x534b5001 and 0 < key_size <= 4096 and value_size >= 0
        key = stream.read(key_size)
        keys.add(key)
        first = stream.read(min(17, value_size))
        stream.seek(value_size - len(first), 1)
        if len(key) == 16 and len(first) == 17 and first[0] == 4:
            possible_roots.add(first[1:])
    assert stream.tell() == pack.stat().st_size
# Requiring a present 128-bit dependency key removes records whose first byte coincidentally is 4.
roots = sorted(possible_roots & keys)
assert len(roots) > 512
sample = [roots[(i * len(roots)) // 512].hex() for i in range(512)]
assert len(set(sample)) == 512
root_file = work / 'dependency-validator-sampled-roots.txt'
root_file.write_text('\n'.join(sample) + '\n')
java = '/home/jgett/REPOS/.work/skyframe-go/work/build-output/external/rules_java++toolchains+remotejdk25_linux/bin/java'
package = 'src/test/java/com/google/devtools/build/lib/skyframe/serialization/local'
subprocess.run([java, '-jar', '/home/jgett/REPOS/.tools/google-java-format.jar', '--replace',
                str(repo/package/'LocalDependencyValidationBenchmark.java')], check=True)
subprocess.run(['/home/jgett/REPOS/.tools/buildifier', str(repo/package/'BUILD')], check=True)
startup = ['/home/jgett/REPOS/.tools/bazel', '--host_jvm_args=-Xmx4g', '--output_base=' + str(work/'build-output')]
with open('/home/jgett/REPOS/.work/skyframe-measurements.lock', 'a') as lock:
    fcntl.flock(lock, fcntl.LOCK_SH)
    try:
        with (work/'dependency-validator-benchmark-build.log').open('w') as log:
            completed = subprocess.run(startup + ['build', '--config=efcache', '--bes_backend=',
                '--remote_upload_local_results=false', '--jobs=4', '--local_resources=memory=4096',
                '//' + package + ':LocalDependencyValidationBenchmark'], cwd=repo, stdout=log, stderr=subprocess.STDOUT)
        print((work/'dependency-validator-benchmark-build.log').read_text()[-3000:], flush=True)
        completed.check_returncode()
    finally:
        subprocess.run(startup + ['shutdown'], cwd=repo, check=False)
    fcntl.flock(lock, fcntl.LOCK_EX)
    argv = [str(repo/'bazel-bin'/package/'LocalDependencyValidationBenchmark'), str(cache/'objects/packed-v1'),
            writer_id, str(work/'cross-jdk9-writer-workspace'), str(work/'cross-jdk9-writer/output-base'), str(root_file)]
    completed = subprocess.run(argv, cwd=repo, capture_output=True, text=True)
    (work/'dependency-validator-benchmark-raw.log').write_text(completed.stdout + '\n' + completed.stderr)
    completed.check_returncode()
    result = json.loads(completed.stdout)
    result.update(purpose='Cost of complete dependency validation with ready repositories; first validation vs same-invocation memo, not whole-build timing',
                  population_roots=len(roots), sampled_root_file_sha256=hashlib.sha256(root_file.read_bytes()).hexdigest(),
                  argv=argv, warmup_rounds=2)
    for row in result['rounds']:
        assert row['fresh_stats']['rejected_roots'] == 0
        assert row['fresh_stats']['records_read'] == row['memo_stats']['records_read']
    (work/'dependency-validator-benchmark.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2), flush=True)
