from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

SOURCE = Path('/home/jgett/REPOS/bazel-skycache-v2')
RUN = Path('/mnt/skycache-v2-local')
DOCS = Path('/home/jgett/REPOS/bazel-skyframe-findings/docs/jsgette/measurements/skycache-v2-wsl-2026-10-24')
JDK = Path('/home/jgett/REPOS/.tools/skycache-benchmark-jdk25')
BINARY = RUN / 'bin/bazel-skycache-v2-1'
OLD = Path('/home/jgett/REPOS/.work/skycache-concurrent/nvme-batch-optimization-2026-10-21/bazel')
sys.path.insert(0, str(SOURCE / 'tools/skycache'))
from storage_preflight import check_storage

def now():
    return datetime.now(timezone.utc).isoformat()

def digest(path):
    return hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()

def save(report):
    (RUN / 'protocol.json').write_text(json.dumps(report, indent=2) + '\n')
    shutil.copyfile(RUN / 'protocol.json', DOCS / 'protocol.json')

def snapshot(name):
    source = RUN / name
    destination = DOCS / name
    if source.is_dir():
        destination.mkdir(exist_ok=True)
        for path in source.iterdir():
            if path.is_file() and path.suffix in ('.json', '.log'):
                shutil.copyfile(path, destination / path.name)
        profiles = {p.name: {'sha256': digest(p), 'bytes': p.stat().st_size} for p in source.glob('*.profile.gz')}
        if profiles:
            (destination / 'profiles-retained-locally.json').write_text(json.dumps(profiles, indent=2) + '\n')
    for suffix in ('.log', '.jsonl'):
        path = RUN / (name + suffix)
        if path.is_file():
            shutil.copyfile(path, DOCS / path.name)

def execute(name, argv, *, timeout=1200, stdout_suffix='.log'):
    report = json.loads((RUN / 'protocol.json').read_text())
    stage = {'name': name, 'argv': list(map(str, argv)), 'cwd': str(SOURCE), 'started_at_utc': now(),
             'storage': check_storage([RUN]), 'status': 'running'}
    report['stages'].append(stage)
    save(report)
    print('Starting ' + name, flush=True)
    started = time.monotonic()
    try:
        environment = dict(os.environ, TMPDIR=str(RUN / 'tmp'))
        with (RUN / (name + stdout_suffix)).open('x') as output:
            result = subprocess.run(list(map(str, argv)), cwd=SOURCE, env=environment,
                                    stdout=output, stderr=subprocess.STDOUT, timeout=timeout)
        stage['exit_code'] = result.returncode
        stage['status'] = 'passed' if result.returncode == 0 else 'failed'
    except Exception as error:
        stage['status'] = 'failed'
        stage['error'] = repr(error)
        raise
    finally:
        stage['seconds'] = time.monotonic() - started
        stage['completed_at_utc'] = now()
        snapshot(name)
        save(report)
    print(name + ': ' + stage['status'], flush=True)
    if stage['status'] != 'passed':
        raise RuntimeError(name + ' failed; inspect preserved log and protocol')

def prepare():
    report = json.loads((RUN / 'protocol.json').read_text())
    log = (RUN / 'build-tests.log').read_text()
    assert 'Build completed successfully' in log and '12 tests pass' in log
    (RUN / 'bin').mkdir(exist_ok=True)
    assert not BINARY.exists()
    shutil.copy2(RUN / 'build-output/execroot/_main/bazel-out/k8-fastbuild/bin/src/bazel-dev', BINARY)
    assert digest(OLD) == 'a275f62018c2e5fc25f0b5f89199aac7da7464c9696b1daeec640db52c2e0e23'
    report['candidate_binary'] = {'path': str(BINARY), 'sha256': digest(BINARY)}
    report['baseline_binary'] = {'path': str(OLD), 'sha256': digest(OLD), 'base': report['implementation_base'],
                                 'provenance': 'Previously built local binary, verified against committed batch-optimization-2026-10-21.json'}
    report['jdk'] = subprocess.run([str(JDK / 'bin/java'), '-version'], text=True, capture_output=True, check=True).stderr
    tests = []
    xml_root = RUN / 'build-output/execroot/_main/bazel-out/k8-fastbuild/testlogs/src/test/java/com/google/devtools/build/lib/skyframe/serialization/local'
    xml_dest = DOCS / 'test-xml'
    xml_dest.mkdir(exist_ok=True)
    for path in sorted(xml_root.glob('*/test.xml')):
        root = ET.parse(path).getroot()
        cases = list(root.iter('testcase'))
        tests.append({'target': path.parent.name, 'cases': len(cases),
                      'failures': sum(len(c.findall('failure')) + len(c.findall('error')) for c in cases),
                      'sha256': digest(path)})
        shutil.copyfile(path, xml_dest / (path.parent.name + '.xml'))
    report['tests'] = {'targets': len(tests), 'cases': sum(t['cases'] for t in tests),
                       'failures': sum(t['failures'] for t in tests), 'details': tests}
    assert report['tests']['targets'] == 12 and report['tests']['cases'] == 190 and report['tests']['failures'] == 0
    report['stages'].append({'name': 'build-and-tests', 'status': 'passed', 'completed_at_utc': now(),
                            'command_script': 'build-skycache.sh', 'log': 'build-tests.log'})
    report['planned_fixture_builds'] = {'idle_ab_including_same_mtime_mutation': 25, 'cold_import': 2,
                                      'same_version_worktrees': 18, 'mixed_version_worktrees': 18,
                                      'total': 63, 'note': 'Module lockfile setup and mechanism benchmark JVMs are separate.'}
    shutil.copyfile(RUN / 'build-tests.log', DOCS / 'build-tests.log')
    save(report)
    # Release compiler workers and build-server graph before timing fixture commands.
    execute('shutdown-build-server', ['/home/jgett/REPOS/.tools/bazel',
            '--output_user_root=' + str(RUN / 'bazel-user-root'), '--output_base=' + str(RUN / 'build-output'),
            '--host_jvm_args=-Xmx5g', 'shutdown'], timeout=120)

def idle():
    execute('idle-ab', ['python3', SOURCE / 'tools/skycache/idle_retention_benchmark.py',
            '--bazel', BINARY, '--jdk', JDK, '--output-user-root', RUN / 'bazel-user-root',
            '--root', RUN / 'idle-ab', '--rounds=3', '--with-off-control', '--repeated-import-test'])

def cold():
    execute('cold-import', ['python3', SOURCE / 'tools/skycache/idle_import_probe.py',
            '--bazel', BINARY, '--fixture', RUN / 'idle-ab', '--root', RUN / 'cold-import'])

def worktrees(mixed=False):
    name = 'mixed-worktrees' if mixed else 'worktrees'
    argv = ['python3', SOURCE / 'tools/skycache/local_worktree_probe.py', '--bazel', BINARY,
            '--jdk', JDK, '--output-user-root', RUN / 'bazel-user-root', '--root', RUN / name]
    if mixed:
        argv.extend(['--other-bazel', OLD])
    execute(name, argv)

def mechanism():
    directory = RUN / 'build-output/execroot/_main/bazel-out/k8-fastbuild/bin/src/test/java/com/google/devtools/build/lib/skyframe/serialization/local'
    for number in (1, 2):
        name = 'dependency-' + str(number)
        execute(name, [directory / 'ParallelDependencyValidationBenchmark', '--jvm_flag=-Xmx2g', RUN / name], stdout_suffix='.jsonl')
    for size, count, record_bytes in [('small', 20000, 256), ('medium', 8000, 4096)]:
        for number in (1, 2):
            name = f'packed-{size}-{number}'
            execute(name, [directory / 'ParallelPackedStoreBenchmark', '--jvm_flag=-Xmx2g', RUN / name,
                          count, record_bytes], stdout_suffix='.jsonl')

parser = argparse.ArgumentParser()
parser.add_argument('stage', choices=['prepare', 'idle', 'cold', 'worktrees', 'mixed', 'mechanism', 'all'])
args = parser.parse_args()
stages = {'prepare': prepare, 'idle': idle, 'cold': cold, 'worktrees': worktrees,
          'mixed': lambda: worktrees(True), 'mechanism': mechanism}
if args.stage == 'all':
    for function in stages.values():
        function()
else:
    stages[args.stage]()
