import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
MEASURE = ROOT / 'bazel/docs/jsgette/measurements/multi-worktree-phase0-2026-10-31'
sys.path.insert(0, str(MEASURE / 'harness'))
from run import enable_child_subreaper, owned_directory, owned_daemon_identity, wait_for_shutdown_client, write_json

assert enable_child_subreaper()['enabled']
state = owned_directory(ROOT / 'datadog-preflight-state', 'state')
results = owned_directory(MEASURE / 'wsl-representative-preflight', 'results')
repo = ROOT / 'datadog-agent'
cache = state / 'cache'
cache.mkdir()
env = {'HOME': str(Path.home()), 'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
       'XDG_CACHE_HOME': str(cache), 'DD_BAZEL_REMOTE_CACHE': 'off',
       'USE_BAZEL_VERSION': str(ROOT / 'control/bazel-control')}
base = [str(Path.home() / '.local/bin/bazelisk'), '--nosystem_rc', '--nohome_rc',
        '--output_base=' + str(state / 'output-base'), '--host_jvm_args=-Xmx2048m',
        '--host_jvm_args=-XX:ActiveProcessorCount=4', '--max_idle_secs=600']
options = ['--config=no-remote-cache', '--remote_executor=', '--jobs=4',
           '--loading_phase_threads=1', '--local_resources=memory=4096',
           '--repository_cache=' + str(cache / 'repository-cache'),
           '--noskip_incompatible_explicit_targets', '--nocache_test_results',
           '--test_output=errors', '--color=no', '--curses=no']
os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:4])
write_json(results / 'manifest.json', {'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
    'environment': env, 'affinity': sorted(os.sched_getaffinity(0)), 'bazel_sha256': hashlib.sha256((ROOT / 'control/bazel-control').read_bytes()).hexdigest(),
    'bazelisk_sha256': hashlib.sha256((Path.home() / '.local/bin/bazelisk').read_bytes()).hexdigest()})
shutil.copy2(__file__, results / 'datadog_preflight.py')
failure = None
try:
    for name, targets in [('strings', ['//pkg/util/strings:strings_test']),
                          ('cohort', ['//pkg/util/strings:strings_test', '//pkg/util/cache:cache_test', '//pkg/metrics:metrics_test'])]:
        directory = results / name
        directory.mkdir()
        argv = base + ['test'] + targets + options + [f'--build_event_json_file={directory / "bep.jsonl"}', f'--profile={directory / "profile.json.gz"}']
        write_json(directory / 'command.json', {'argv': argv, 'cwd': str(repo), 'environment': env})
        start = time.time()
        with (directory / 'command.log').open('wb') as log:
            process = subprocess.Popen(argv, cwd=repo, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            try:
                code = process.wait(timeout=1800)
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
        write_json(directory / 'result.json', {'exit_code': code, 'wall_seconds': time.time() - start})
        print(json.dumps({'case': name, 'exit_code': code, 'seconds': round(time.time() - start, 2)}), flush=True)
        if code:
            raise RuntimeError(f'{name} failed: {code}')
except BaseException as error:
    failure = str(error)
    raise
finally:
    daemon = owned_daemon_identity(state / 'output-base')
    with (results / 'shutdown.log').open('wb') as log:
        process = subprocess.Popen(base + ['shutdown'], cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT)
        code, reaping = wait_for_shutdown_client(process, daemon, 60)
    write_json(results / 'summary.json', {'failure': failure, 'shutdown_exit_code': code, 'reaping': reaping})
