from pathlib import Path
import fcntl, hashlib, json, os, shutil, subprocess, sys, time
import xml.etree.ElementTree as ET

repo = Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
work = Path('/home/jgett/REPOS/.work')
root = work / 'skycache-retained-chunks-2026-10-25'
build = work / 'skycache-remote-mvp/build'
lock = (work / 'skyframe-measurements.lock').open('a')
print('Waiting for shared measurement lock', flush=True)
fcntl.flock(lock, fcntl.LOCK_EX)
root.mkdir(exist_ok=False)
(root / 'tmp').mkdir()
sys.path.insert(0, str(repo / 'tools/skycache'))
from storage_preflight import check_storage
os.environ.pop('SKYCACHE_WINDOWS_STORAGE_SNAPSHOT', None)
result = {'storage_before': check_storage([root]), 'source_head': subprocess.check_output(
    ['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
    'source_tree': subprocess.check_output(['git', 'write-tree'], cwd=repo, text=True).strip(),
    'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
subprocess.run(['git', 'diff', '--exit-code'], cwd=repo, check=True, stdout=subprocess.DEVNULL)
(root / 'implementation.patch').write_bytes(subprocess.check_output(['git', 'diff', '--cached', '--binary'], cwd=repo))
startup = ['/home/jgett/.local/bin/bazel', '--output_user_root=/home/jgett/.cache/bazel/_bazel_jgett',
    '--output_base=' + str(build), '--server_javabase=/home/jgett/REPOS/.tools/skycache-benchmark-jdk25',
    '--host_jvm_args=-Xmx5g', '--max_idle_secs=120']
targets = ['//src:bazel-dev', '//src/tools/remote:worker',
    '//src/test/java/com/google/devtools/build/lib/skyframe/serialization/local:all',
    '//src/test/java/com/google/devtools/build/lib/skyframe/serialization/remote:all',
    '//src/tools/remote/src/test/java/com/google/devtools/build/remote/worker:all',
    '//src/test/java/com/google/devtools/build/lib/skyframe/serialization:SerializationTests',
    '//src/test/java/com/google/devtools/build/lib/skyframe/serialization:ChunkedValueSerializationTest']
argv = startup + ['test'] + targets + ['--jobs=6', '--local_test_jobs=3', '--local_resources=memory=6144',
    '--test_output=errors', '--color=no', '--curses=no', '--disk_cache=',
    '--remote_cache=grpc://10.55.0.1:8080', '--remote_max_connections=1',
    '--remote_max_concurrency_per_connection=16', '--remote_timeout=600', '--remote_retries=1',
    '--build_event_json_file=' + str(root / 'build.bep.json'), '--profile=' + str(root / 'build.profile.gz')]
(root / 'build.command.json').write_text(json.dumps(argv, indent=2) + '\n')
start = time.monotonic()
try:
    with (root / 'build.log').open('x') as log:
        run = subprocess.run(argv, cwd=repo, env=dict(os.environ, TMPDIR=str(root / 'tmp')),
                             stdout=log, stderr=subprocess.STDOUT)
    result.update(exit_code=run.returncode, seconds=time.monotonic() - start)
    if run.returncode == 0:
        (root / 'bin').mkdir()
        binary = root / 'bin/bazel'
        shutil.copy2(repo / 'bazel-bin/src/bazel-dev', binary)
        result['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
        result['worker'] = str((repo / 'bazel-bin/src/tools/remote/worker').resolve())
        logs = repo / 'bazel-testlogs'
        xmls = []
        for directory in ['src/test/java/com/google/devtools/build/lib/skyframe/serialization/local',
                          'src/test/java/com/google/devtools/build/lib/skyframe/serialization/remote',
                          'src/tools/remote/src/test/java/com/google/devtools/build/remote/worker']:
            xmls.extend((logs / directory).glob('*/test.xml'))
        xmls.extend(logs / 'src/test/java/com/google/devtools/build/lib/skyframe/serialization' / name / 'test.xml'
                    for name in ['SerializationTests', 'ChunkedValueSerializationTest'])
        suites = []
        for path in sorted(xmls):
            target = root / 'tests' / path.relative_to(logs)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            xml = ET.parse(path).getroot()
            suites.append({'target': str(path.parent.relative_to(logs)), 'cases': len(xml.findall('.//testcase')),
                           'failures': len(xml.findall('.//failure')), 'errors': len(xml.findall('.//error'))})
        result['tests'] = suites
        result['test_cases'] = sum(s['cases'] for s in suites)
        assert not any(s['failures'] or s['errors'] for s in suites)
finally:
    with (root / 'shutdown.log').open('w') as log:
        result['shutdown'] = subprocess.run(startup + ['shutdown'], cwd=repo,
            stdout=log, stderr=subprocess.STDOUT).returncode
    (root / 'build.json').write_text(json.dumps(result, indent=2) + '\n')
    lock.close()
print(json.dumps(result, indent=2), flush=True)
sys.exit(result['exit_code'])




