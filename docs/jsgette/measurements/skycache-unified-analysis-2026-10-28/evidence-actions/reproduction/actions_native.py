from pathlib import Path
import fcntl
import hashlib
import json
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

root = Path('/home/jgett/REPOS/.work/skycache-analysis-foundation-2026-10-28')
base = Path('/home/jgett/REPOS/.work/skycache-analysis-foundation-evidence')
stage = sys.argv[1]
out = base / stage
out.mkdir(exist_ok=False)
output_base = Path('/home/jgett/REPOS/.work/skycache-analysis-foundation-build')
red = stage.startswith('actions-red')
targets = ['//src/test/java/com/google/devtools/build/lib/skyframe:IncrementalArtifactConflictFinderTest',
           '//src/test/java/com/google/devtools/build/lib/actions:ActionsTests']
if stage == 'actions-red-02':
    targets = targets[:1]
if not red:
    targets += ['//src:bazel-dev',
                '//src/test/java/com/google/devtools/build/lib/buildtool:OutputArtifactConflictTest',
                '//src/test/java/com/google/devtools/build/lib/skyframe:SequencedSkyframeExecutorTest']
command = ['/home/jgett/.local/bin/bazel', f'--output_base={output_base}',
           '--server_javabase=/home/jgett/REPOS/.tools/skycache-benchmark-jdk25',
           '--host_jvm_args=-Xmx5g', '--max_idle_secs=120', 'test', *targets,
           '--test_output=errors', '--nocache_test_results', '--flaky_test_attempts=1', '--local_test_jobs=2',
           '--jobs=6', '--local_resources=memory=6144', '--color=no', '--curses=no', '--disk_cache=',
           '--remote_cache=grpc://10.55.0.1:8080', '--remote_upload_local_results=false',
           '--remote_max_connections=1', '--remote_max_concurrency_per_connection=16',
           '--remote_timeout=120', '--remote_retries=1',
           f'--build_event_json_file={out}/build.bep.json', f'--profile={out}/build.profile.gz']
if red:
    assert not subprocess.check_output(['git', 'diff', 'HEAD', '--', 'src/main/java/com/google/devtools/build/lib/skyframe/IncrementalArtifactConflictFinder.java'], cwd=root)
provenance = {'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
              'command': command}
subprocess.run(['tar', '-czf', str(out / 'new-tests.tar.gz'), 'src/test/java/com/google/devtools/build/lib/actions/ActionsTest.java', 'src/test/java/com/google/devtools/build/lib/skyframe/IncrementalArtifactConflictFinderTest.java'], cwd=root, check=True)
(out / 'source.diff').write_bytes(subprocess.check_output(['git', 'diff', 'HEAD', '--', 'src'], cwd=root))
(out / 'command.json').write_text(json.dumps(provenance, indent=2) + '\n')
with (base.parent / 'skyframe-measurements.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    started = time.monotonic()
    with (out / 'build.log').open('w') as log:
        result = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT)
    record = {'exit_code': result.returncode, 'elapsed_seconds': time.monotonic() - started}
    tests = []
    for target in targets:
        if target == '//src:bazel-dev':
            continue
        label = target.removeprefix('//').replace(':', '/')
        directory = output_base / 'execroot/_main/bazel-out/k8-fastbuild/testlogs' / label
        xml = directory / 'test.xml'
        if xml.exists():
            tree = ET.parse(xml)
            cases = tree.findall('.//testcase')
            tests.append({'target': target, 'cases': len(cases), 'failures': len(tree.findall('.//failure')),
                          'errors': len(tree.findall('.//error')),
                          'failed_cases': [case.attrib for case in cases if case.find('failure') is not None or case.find('error') is not None]})
            for name in ('test.xml', 'test.log'):
                shutil.copyfile(directory / name, out / (target.split(':')[-1] + '-' + name))
    record['tests'] = tests
    if not red and result.returncode == 0:
        binary = base / (stage + '-bazel')
        shutil.copyfile(root / 'bazel-bin/src/bazel-dev', binary)
        binary.chmod(0o555)
        record['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    (out / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record), flush=True)
    if result.returncode:
        print((out / 'build.log').read_text()[-5000:])
