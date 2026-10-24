from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import shutil
import subprocess

source = Path('/home/jgett/REPOS/bazel-skycache-v2')
docs = Path('/home/jgett/REPOS/bazel-skyframe-findings/docs/jsgette')
run = Path('/mnt/skycache-v2-local')
handoff = Path('/home/jgett/REPOS/.work/skycache-v2-local/skycache-handoff')
evidence = docs / 'measurements/skycache-v2-wsl-2026-10-24'
evidence.mkdir(exist_ok=False)
shutil.copytree(handoff, evidence / 'cloud-handoff')
shutil.copyfile('/home/jgett/REPOS/.work/skycache-v2-local/storage-preflight.json', evidence / 'storage-preflight.json')
protocol = {
    'kind': 'skycache_v2_wsl_continuation',
    'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
    'user_timezone': 'Europe/Berlin',
    'implementation_base': '6f451b2dc6f783dda879ff3c7ed3fc8146d0f410',
    'research_base': '8f4ec5307f553018d00a2be926868c530678f5ef',
    'implementation_branch': 'jsgette/skycache-v2-local',
    'documentation_branch': 'jsgette/master',
    'source_path': str(source),
    'run_path': str(run),
    'archive_sha256': hashlib.sha256(Path('/mnt/c/Users/jgett/Downloads/skycache-handoff.zip').read_bytes()).hexdigest(),
    'all_archive_checksums_verified': True,
    'setup': [
        {'action': 'Inspect Downloads archive and WSL Ubuntu; read CONTINUE.md and repository AGENTS.md', 'status': 'passed'},
        {'action': 'Safely extract archive into /home/jgett/REPOS/.work/skycache-v2-local and verify SHA256SUMS', 'status': 'passed'},
        {'action': 'git worktree add -b jsgette/skycache-v2-local /home/jgett/REPOS/bazel-skycache-v2 6f451b2dc6f783dda879ff3c7ed3fc8146d0f410; git apply --check implementation.patch; git apply implementation.patch', 'status': 'passed'},
        {'action': 'First inline PowerShell/WSL build command', 'status': 'setup_failed', 'reason': 'PowerShell quoting/parser error before Python or Bazel executed; replaced with a saved bash script'},
        {'action': 'Storage preflight on Ubuntu-backed run directory', 'status': 'setup_failed', 'reason': 'E:\\WSL\\Ubuntu backing volume had 26.6 GiB free, below unchanged 30 GiB reserve; no build started'},
        {'action': 'Create and mount dedicated 64 GiB ext4 image on C: at /mnt/skycache-v2-local', 'status': 'passed', 'reason': 'Preserve Linux filesystem semantics and normal storage guards; all new build/output/cache writes redirected here', 'persistent_mount_configuration_changed': False},
        {'action': 'git fetch origin jsgette/master jsgette/skycache-concurrent; fast-forward clean existing jsgette/master checkout to origin/jsgette/master', 'status': 'passed'},
        {'action': 'git apply --check research-notes.patch; git apply research-notes.patch on jsgette/master at exact research base', 'status': 'passed'},
    ],
    'stages': [],
    'limitations': ['Cloud and WSL results have different provenance and must not be pooled.', 'The run volume is ext4 on a loop image backed by C: through WSL drvfs; timings are not a native ext4/NVMe claim.', 'Normal filesystem/page caches remain enabled.', 'No production rollout or whole-build performance claim.'],
    'status': 'in_progress',
}
(run / 'protocol.json').write_text(json.dumps(protocol, indent=2) + '\n')
shutil.copyfile(run / 'protocol.json', evidence / 'protocol.json')

def replace(path, before, after):
    text = path.read_text()
    assert text.count(before) == 1, (path, before)
    path.write_text(text.replace(before, after))

idle = source / 'tools/skycache/idle_retention_benchmark.py'
replace(idle, "    parser.add_argument('--trust-store', type=Path)\n", "    parser.add_argument('--trust-store', type=Path)\n    parser.add_argument('--output-user-root', type=Path,\n                        help='Keep Bazel install/repository caches on the selected run volume')\n")
replace(idle, "    if args.trust_store:\n        base +=", "    if args.output_user_root:\n        base += [f'--output_user_root={args.output_user_root.resolve()}']\n    if args.trust_store:\n        base +=")
probe = source / 'tools/skycache/local_worktree_probe.py'
replace(probe, '    parser.add_argument("--lock-file", type=Path)\n', '    parser.add_argument("--lock-file", type=Path)\n    parser.add_argument("--jdk", type=Path, help="Server JDK for source-built Bazel")\n    parser.add_argument("--output-user-root", type=Path)\n    parser.add_argument("--other-bazel", type=Path, help="Use this binary in worktree B for mixed-version checks")\n')
replace(probe, '    binary = args.bazel.resolve(strict=True)\n', '    binary = args.bazel.resolve(strict=True)\n    other_binary = args.other_bazel.resolve(strict=True) if args.other_bazel else binary\n')
replace(probe, '        return [str(binary), "--nosystem_rc", "--nohome_rc", "--noworkspace_rc",\n                "--host_jvm_args=-Xmx2g", "--output_base=" + str(root / ("out-" + name))]\n', '        arguments = [str(binary if name == "a" else other_binary),\n                     "--nosystem_rc", "--nohome_rc", "--noworkspace_rc",\n                     "--host_jvm_args=-Xmx2g", "--output_base=" + str(root / ("out-" + name))]\n        if args.jdk:\n            arguments.append("--server_javabase=" + str(args.jdk.resolve(strict=True)))\n        if args.output_user_root:\n            arguments.append("--output_user_root=" + str(args.output_user_root.resolve()))\n        return arguments\n')
replace(probe, '        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),\n', '        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),\n        "other_binary": str(other_binary),\n        "other_binary_sha256": hashlib.sha256(other_binary.read_bytes()).hexdigest(),\n')
for script in (idle, probe):
    subprocess.run(['python3', str(script), '--help'], check=True, stdout=subprocess.DEVNULL)
print('Restored cloud evidence, initialized WSL protocol, and added explicit run-volume/JDK options.')
