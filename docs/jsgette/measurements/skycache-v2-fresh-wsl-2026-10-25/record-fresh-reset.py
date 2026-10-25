from pathlib import Path
import hashlib,json,shutil,subprocess
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex');B=Path('/mnt/d/Codex/wsl-reset-2026-10-25')
D=Path('/home/jgett/REPOS/bazel-skyframe-findings');R=Path('/home/jgett/REPOS/.work/datadog-ssd-fresh-2026-10-25')
E=D/'docs/jsgette/measurements/skycache-v2-fresh-wsl-2026-10-25';E.mkdir(parents=True,exist_ok=False)
for name in ['purge-qwen.ps1','qwen-purge-protocol.json','reset-ubuntu.ps1','wsl-reset-protocol.json',
 'setup-fresh-ubuntu.sh','prepare-fresh-datadog.py','install-fresh-bazelisk.py',
 'backup-before-wsl-reset.py','archive-old-c-evidence.py','purge-old-c-image.ps1','old-c-purge-protocol.json',
 'watch-datadog-storage.ps1','analyze-fresh-benchmark.py','record-fresh-reset.py']:
 p=T/'work'/name
 if p.suffix=='.json':(E/name).write_text(json.dumps(json.loads(p.read_text(encoding='utf-8-sig')),indent=2)+'\n')
 else:shutil.copy2(p,E/name)
for name in ['fresh-setup.json','bazelisk-bootstrap.json','bazelisk-version.log']:
 shutil.copy2(R/name,E/name)
backup=json.loads((B/'backup-verified.json').read_text())
public={k:backup[k] for k in ['started_unix','finished_unix','archives','tool_binaries','verified','evidence_files']}
public['private_backup_location']='D:\\Codex\\wsl-reset-2026-10-25'
public['source_repository_count']=len(backup['source_repositories'])
public['skycache_bundle_sha256']='c422ab4263b9de667e11136a392af7c94f670d36a3b7a12b611383bdf683b289'
(E/'backup-verification.json').write_text(json.dumps(public,indent=2)+'\n')
old=json.loads((B/'old-C-evidence-verified.json').read_text())
(E/'old-C-evidence-verification.json').write_text(json.dumps({k:v for k,v in old.items() if k!='manifest'},indent=2)+'\n')
(E/'.gitattributes').write_text('* -text\n*.log -whitespace\n')
(D/'docs/jsgette/skycache-v2-fresh-wsl-2026-10-25.md').write_text('''# Fresh WSL and Datadog comparison — 25 October 2026

The user authorized purging WSL and Qwen and restarting Datadog from scratch.

The four Qwen model shards in `E:\\QWEN` (148.51 GiB) were deleted. The old Ubuntu distribution at `E:\\WSL\\Ubuntu` was unregistered, removing its 718.80 GiB virtual disk. A new Ubuntu 24.04.5 LTS distribution was installed at the same E: location, with user `jgett`. E: had about 881.56 GiB free immediately after the purge and about 880 GiB after initial installation.

Ten repositories, including local branches and uncommitted working files, and 6,442 experiment evidence files were backed up and verified before unregistering. The private backup is `D:\\Codex\\wsl-reset-2026-10-25`; it contains source/history archives, evidence, the exact benchmark binaries and JDK, and the incremental bundle for the local SkyCache commit. Access is restricted to the current Windows user, SYSTEM, and Administrators. Private archives and credentials are not uploaded to Git.

The old 64 GiB C: loopback benchmark image was also removed after archiving and verifying 143 evidence files. The scratch build caches were discarded. The Downloads handoff and unrelated Windows applications remain available.

Fresh checkouts were created for Datadog commit `0cde96beb70963d0838119c308b9d0422ee66be0`, SkyCache commit `303a149a195bbd6ad770584bd34138ea749c3e9a`, and the documentation branch. No old Datadog output bases, repository caches, action caches, or graph caches were restored. Preserved Bazel binaries and JDK 25 make the implementation/runtime comparison reproducible.

Bazelisk v1.30.0 was installed from the official GitHub release after checking its published SHA-256. The ordinary Datadog workflow resolves its pinned Bazel 9.3.0. Benchmark variants use the explicit source-built vanilla and experimental binaries recorded in the setup manifest. `~/.bazelrc` provides `--config=efcache` for Cache & Carry at `grpc://10.55.0.1:8080`.

Benchmark preparation is in progress. No result is claimed until the repeated matrix, output equivalence, remote-cache-hit checks, and memory measurements complete.

Evidence: [reset protocol and setup](measurements/skycache-v2-fresh-wsl-2026-10-25/).
''')
print('Reset protocol recorded; benchmark result section will follow completed measurements.')
