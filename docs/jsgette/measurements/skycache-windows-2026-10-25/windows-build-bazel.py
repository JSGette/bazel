from pathlib import Path
import os, subprocess, sys
from importlib.machinery import SourceFileLoader
p = SourceFileLoader('probe', str(Path(__file__).with_name('windows-probe.py'))).load_module()
env = dict(os.environ, BAZELISK_HOME='E:/scw/bazelisk', BAZEL_SH='E:/scw/msys64/usr/bin/bash.exe',
           BAZEL_VC='C:/Program Files/Microsoft Visual Studio/2022/Community/VC',
           TEMP='E:/scw/tmp', TMP='E:/scw/tmp', BAZELISK_SKIP_WRAPPER='true',
           PATH='E:/scw/msys64/usr/bin;'+os.environ['PATH'])
Path(env['TEMP']).mkdir(exist_ok=True)
label = sys.argv[1] if len(sys.argv)>1 else 'bazel-candidate-build-1'
argv = ['C:/bazel/bazel.exe', '--output_user_root=E:/scw/u', '--output_base=E:/scw/bo',
        '--host_jvm_args=-Xmx6g', '--max_idle_secs=600', 'build', '//src:bazel-dev',
        '--jobs=12', '--local_resources=memory=8192', '--color=no', '--curses=no',
        '--remote_cache=grpc://10.55.0.1:8080', '--remote_timeout=600', '--remote_retries=1',
        '--remote_max_connections=1', '--remote_max_concurrency_per_connection=16',
        '--remote_upload_local_results', '--disk_cache=',
        '--bes_backend=grpc://10.55.0.1:8080', '--bes_results_url=http://192.168.2.71:8088/invocation/',
        '--build_event_json_file=E:/scw/evidence/'+label+'.bep.json',
        '--profile=E:/scw/evidence/'+label+'.profile.gz']
p.save(p.LOG/'source-provenance.json', {
    'candidate_base': '6f451b2dc6f783dda879ff3c7ed3fc8146d0f410',
    'candidate_equivalent_commit': '71521fabb3420707c39d73329fa9aa9622e8c382',
    'candidate_tree': subprocess.check_output(['git','-C','E:/scw/bz','write-tree'],text=True).strip(),
    'expected_candidate_tree': 'f6a4aaf7fafcf54b4e06097e1eddb2710a07c74d',
    'source': 'Fresh https://github.com/JSGette/bazel.git clone plus published implementation-current.patch applied with --index. Exact tree verified.',
    'patch_sha256': p.sha('E:/scw/docs/docs/jsgette/measurements/skycache-execution-metadata-2026-10-25/implementation-current.patch'),
    'datadog_revision': subprocess.check_output(['git','-C','E:/scw/dd','rev-parse','HEAD'],text=True).strip(),
    'initial_datadog_lock_sha256': p.sha('E:/scw/dd/MODULE.bazel.lock'),
    'native_environment_overrides': {k:env[k] for k in ['BAZELISK_HOME','BAZEL_SH','BAZEL_VC','TEMP','TMP']}})
pidfile=Path('E:/scw/bo/server/server.pid.txt')
pids=[int(pidfile.read_text())] if pidfile.exists() else []
r = p.run(label, argv, cwd='E:/scw/bz', env=env, extra_pids=pids)
sys.exit(r['exit_code'])
