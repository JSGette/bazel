from pathlib import Path
import subprocess, json, os, time, shutil, hashlib, sys, fcntl, xml.etree.ElementTree as ET

S = Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
R = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/home/jgett/REPOS/.work/skycache-remote-sync-2026-10-25')
old = Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25')
lock = (old.parent / 'skyframe-measurements.lock').open('a')
print('Waiting for the shared measurement lock before building.', flush=True)
fcntl.flock(lock, fcntl.LOCK_EX)
print('Measurement lock acquired; building and testing the merged source.', flush=True)
startup = ['/home/jgett/.local/bin/bazel', '--output_user_root=/home/jgett/.cache/bazel/_bazel_jgett',
           '--output_base=' + str(old / 'build'),
           '--server_javabase=/home/jgett/REPOS/.tools/skycache-benchmark-jdk25',
           '--host_jvm_args=-Xmx5g', '--max_idle_secs=120']
targets = ['//src:bazel-dev', '//src/tools/remote:worker',
           '//src/test/java/com/google/devtools/build/lib/skyframe/serialization/local:all',
           '//src/test/java/com/google/devtools/build/lib/skyframe/serialization/remote:all',
           '//src/tools/remote/src/test/java/com/google/devtools/build/remote/worker:all',
           '//src/test/java/com/google/devtools/build/lib/skyframe/serialization:SerializationTests',
           '//src/test/java/com/google/devtools/build/lib/skyframe/serialization:ChunkedValueSerializationTest']
args = startup + ['test'] + targets + ['--jobs=6', '--local_test_jobs=3', '--local_resources=memory=6144',
        '--test_output=errors', '--color=no', '--curses=no', '--disk_cache=',
        '--remote_cache=grpc://10.55.0.1:8080', '--remote_max_connections=1',
        '--remote_max_concurrency_per_connection=16', '--remote_timeout=600', '--remote_retries=1',
        '--bes_backend=grpc://10.55.0.1:8080', '--bes_results_url=http://192.168.2.71:8088/invocation/',
        '--build_event_json_file=' + str(R / 'build.bep.json'), '--profile=' + str(R / 'build.profile.gz')]
(R / 'build.command.json').write_text(json.dumps(args, indent=2) + '\n')
result = {'source_tree': subprocess.check_output(['git', 'write-tree'], cwd=S, text=True).strip(),
          'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
start = time.monotonic()
try:
    with (R / 'build.log').open('x') as f:
        p = subprocess.run(args, cwd=S, env=dict(os.environ, TMPDIR=str(old / 'tmp')), stdout=f, stderr=subprocess.STDOUT)
    result.update(exit_code=p.returncode, seconds=time.monotonic() - start)
    if p.returncode == 0:
        (R / 'bin').mkdir(exist_ok=True)
        binary = R / 'bin/bazel-remote-sync'
        shutil.copy2(S / 'bazel-bin/src/bazel-dev', binary)
        result['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
        worker = S / 'bazel-bin/src/tools/remote/worker'
        result['worker'] = str(worker.resolve())
        result['worker_sha256'] = hashlib.sha256(worker.read_bytes()).hexdigest()
        logs = S / 'bazel-testlogs'
        suites = []
        locations = [logs / 'src/test/java/com/google/devtools/build/lib/skyframe/serialization/local',
                     logs / 'src/test/java/com/google/devtools/build/lib/skyframe/serialization/remote',
                     logs / 'src/tools/remote/src/test/java/com/google/devtools/build/remote/worker']
        xmls = [p for parent in locations for p in parent.glob('*/test.xml')]
        xmls += [logs / 'src/test/java/com/google/devtools/build/lib/skyframe/serialization' / name / 'test.xml'
                 for name in ['SerializationTests', 'ChunkedValueSerializationTest']]
        for xml in xmls:
            dest = R / 'tests' / xml.relative_to(logs)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(xml, dest)
            root = ET.parse(xml).getroot()
            cases = root.findall('.//testcase')
            suites.append({'target': str(xml.parent.relative_to(logs)), 'cases': len(cases),
                           'failures': len(root.findall('.//failure')), 'errors': len(root.findall('.//error'))})
        result['tests'] = suites
        result['test_cases'] = sum(x['cases'] for x in suites)
        assert not any(x['failures'] or x['errors'] for x in suites)
finally:
    with (R / 'build-shutdown.log').open('w') as f:
        result['shutdown'] = subprocess.run(startup + ['shutdown'], cwd=S, stdout=f, stderr=subprocess.STDOUT).returncode
    (R / 'build.json').write_text(json.dumps(result, indent=2) + '\n')
    lock.close()
print(json.dumps(result, indent=2), flush=True)
sys.exit(result['exit_code'])
