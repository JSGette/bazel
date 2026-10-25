from pathlib import Path
import hashlib,json,shutil,subprocess,time
ROOT=Path('/home/jgett/REPOS/.work/skycache-v2-datadog-ssd-2026-10-25')
TASK=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
DOC=Path('/home/jgett/REPOS/bazel-skyframe-findings')
REL=Path('docs/jsgette/measurements/skycache-v2-datadog-ssd-2026-10-25')
DEST=DOC/REL
def git(*args):return subprocess.check_output(['git','-C',str(DOC),*args],text=True).strip()
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def du(name):
 return {Path(p):int(n) for n,p in (line.split('\t',1) for line in (ROOT/name).read_text().splitlines())}
def gib(n):return n/1024**3
rows=du('work-storage-breakdown.txt')
work=ROOT.parent
assert work in rows, 'Wait for complete du scan; never present a partial total.'
other=du('other-storage-breakdown.txt')
windows=json.loads((TASK/'work/e-storage-analysis.json').read_text(encoding='utf-8-sig'))
cache=json.loads((ROOT/'duplicate-cache-audit.json').read_text())
attempts=json.loads((ROOT/'results.json').read_text())
assert len(attempts['runs'])==2 and all(r['exit_code']==8 for r in attempts['runs'])
old=work/'skycache-concurrent'
experiments=sorted([{'path':str(p),'bytes':n} for p,n in rows.items() if p.parent==old],key=lambda x:x['bytes'],reverse=True)
caches=[]
for p,n in sorted(rows.items(),key=lambda x:len(x[0].parts)):
 if old not in p.parents:continue
 if (p.name=='cache' or p.name.endswith('-cache')) and not any(Path(c['path']) in p.parents for c in caches):caches.append({'path':str(p),'bytes':n})
summary={'observed_unix':time.time(),'windows':windows,'wsl_work_bytes':rows[work],
 'skycache_experiments_bytes':rows[old],'top_experiments':experiments[:20],
 'identified_skycache_cache_directories':caches,'identified_skycache_cache_bytes':sum(c['bytes'] for c in caches),
 'bazel_home_cache_bytes':other[Path('/home/jgett/.cache/bazel')],
 'new_repository_cache_allocated_bytes':other[Path('/home/jgett/.cache/bazel/cache')],
 'new_benchmark_allocated_bytes':rows[ROOT],
 'duplicate_cache_audit':cache,'new_vhd_growth_bytes':771806461952-758619570176,
 'notes':['Windows totals use logical file sizes; WSL ext4.vhdx is non-sparse.',
 'Linux du uses allocated blocks, does not follow symlinks and counts hardlinks once per scan.',
 'Nested rows overlap; add only disjoint directories.',
 'Old experiment cache subtotal includes only directories identifiable at scanned depth; it is a lower bound.',
 'Another Bazel server was present during setup; not all host disk growth can be attributed to this task.',
 'No completed Datadog build or performance comparison; both attempts were interrupted by guards.',
 'No deletion, move, or offline VHD compaction performed.']}
(ROOT/'storage-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
assert git('branch','--show-current')=='jsgette/master'
assert not git('status','--porcelain'), 'Review existing changes before writing evidence.'
DEST.mkdir(parents=True,exist_ok=False)
for p in ROOT.iterdir():
 if p.is_file() and (p.suffix in ['.log','.py','.txt'] or p.name.endswith('.samples.json') or p.name.endswith('.storage.json') or p.name in ['results.json','hardware.json','storage-summary.json','duplicate-cache-audit.json']):
  # Thread dumps remain local; preserve content hashes in the local-only manifest.
  if 'threads' not in p.name:shutil.copy2(p,DEST/p.name)
for name in ['datadog-ssd-benchmark.py','watch-datadog-storage.ps1','analyze-e-storage.ps1','compare-repository-caches.py','publish-datadog-storage-protocol.py']:
 shutil.copy2(TASK/'work'/name,DEST/name)
(DEST/'e-storage-analysis.json').write_text(json.dumps(windows,indent=2)+'\n')
(DEST/'local-only-artifacts.json').write_text(json.dumps({p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in ROOT.iterdir() if p.is_file() and (p.name.endswith('.bep.json') or p.name.endswith('.profile.gz') or 'threads' in p.name)},indent=2)+'\n')
(DEST/'.gitattributes').write_text('* -text\n*.log -whitespace\nhardware.json whitespace=cr-at-eol\n')
lines=['# Datadog SSD benchmark preparation and E: storage audit — 25 October 2026','',
'The repeated Datadog performance comparison is **not complete**. Both vanilla preparation attempts stopped at safety guards before a successful build. No speed or RAM improvement is established by these attempts. Cache & Carry TCP connectivity was verified; action-cache hits have not yet been established in this run.','',
'## Storage findings','',
'E: is the WD Blue SN570 2 TB NVMe SSD; C: is the other NVMe SSD. D: is a Toshiba SATA hard disk. The new benchmark binaries, output base, graph-cache destination, sources, JDK, and repository caches use the E-backed WSL filesystem. The older 64 GiB C: image remains intact as historical evidence; it is not the new benchmark output location.','',
'| E: consumer | GiB |','|---|---:|']
top={r['path']:r['bytes'] for r in windows['totals']}
for key in ['E:\\SteamLibrary','E:\\WSL','E:\\QWEN','E:\\GOGLibrary','E:\\Projects','E:\\Epic Games']:
 lines.append(f'| `{key}` | {gib(top[key]):.2f} |')
lines+=['',f'Inside WSL, `/home/jgett/REPOS/.work` accounts for **{gib(rows[work]):.2f} GiB**, including **{gib(rows[old]):.2f} GiB** of historical SkyCache experiments. The normal Bazel home caches account for another **{gib(summary["bazel_home_cache_bytes"]):.2f} GiB**. Source repositories outside `.work` total about 7.02 GiB; Docker uses about 0.23 GiB.','',
f'At least **{gib(summary["identified_skycache_cache_bytes"]):.2f} GiB** of the old experiment total is in directories explicitly named `cache` or ending in `-cache`. These experiments retained separate analysis-object stores, output bases and worktrees. This retention, repeated across many probes, is the main development storage consumer. Parent totals and child caches overlap and must not be added together.','',
'| Largest historical experiment | Total GiB |','|---|---:|']
for r in experiments[:12]:lines.append(f'| `{Path(r["path"]).name}` | {gib(r["bytes"]):.2f} |')
lines+=['','## Growth during this attempt','',
f'The WSL VHD file grew from 758,619,570,176 to 771,806,461,952 bytes, or **12.28 GiB**. Host E: free space fell from 26.53 GiB to roughly 14.25 GiB. Another Bazel task was also present, so the full host delta is not assigned exclusively to this benchmark.','',
f'The initial harness mistakenly used `--output_user_root=/home/jgett/.cache/bazel` instead of the established `/home/jgett/.cache/bazel/_bazel_jgett`. This created a separate repository cache occupying **{gib(summary["new_repository_cache_allocated_bytes"]):.2f} GiB**, plus a 0.22 GiB installation. A same-relative-path SHA-256 audit verified **{gib(cache["duplicate_bytes"]):.2f} GiB / {cache["duplicate_files"]} files** as exact duplicates of the existing cache. Other new entries are not proven duplicates by that audit. The corrected reproduction harness reuses the established cache root.','',
f'The new benchmark directory occupies **{gib(summary["new_benchmark_allocated_bytes"]):.2f} GiB**, including about 2.01 GiB of unpacked dependencies and 0.34 GiB of copied Bazel binaries. These are setup/dependency bytes, not compiled Datadog artifacts from a successful build.','',
'`fsutil sparse queryflag` reports that the VHD is not sparse. A root `fstrim -v /` reported 306.3 GiB trimmed but did not increase Windows free space. Free capacity shown inside Linux therefore does not mean the Windows SSD has that capacity available. Deleting old caches inside WSL may need an offline VHD compaction step to return their blocks to E:. No compaction or deletion was performed.','',
'## Preserved attempts','',
'| Preparation | Exit | Wall seconds | Cause |','|---|---:|---:|---|']
for r in attempts['runs']:lines.append(f'| `{r["label"]}` | {r["exit_code"]} | {r["seconds"]:.3f} | {"; ".join(r["guard_errors"]).replace(chr(10)," ")} |')
lines+=['','The first attempt lost WSL-to-Windows interop while checking free space. It was cancelled, recorded, and replaced by a Windows-side snapshot watcher with freshness checks. The retry then crossed the explicit 15 GiB E: reserve and was cancelled. Neither attempt is included in any performance comparison. The baseline process-tree sampled peaks are setup-only and cannot answer the requested comparison.','',
'## Planned controlled comparison','',
'Datadog commit `0cde96beb70963d0838119c308b9d0422ee66be0`, target `//cmd/cluster-agent:cluster-agent`; unchanged fixture and resolved lockfile. Three rotated rounds of vanilla, new binary with SkyCache disabled, and new binary with SkyCache enabled. Each cycle has a fresh-server build, two warm builds, a 20-second idle period, and recovery. The enabled variant evicts at 15 seconds. Preparation is excluded.','',
'Vanilla is the recorded pre-SkyCache source build at `4cfc05f77ba91900a2e3c6ab135861498cb2a0bf`, SHA-256 `1ef0d6ff94b24c787d539f63672f93f0da9bd96d49750d729489cbffc80aa588`. This fork baseline includes documentation and codec-visibility changes; it is not the official Bazel 9.3.0 release. Candidate source is `303a149a195bbd6ad770584bd34138ea749c3e9a`, binary SHA-256 `28026d467ef10667c0fcfb1741495fbefe4fee9dfed43d3d2d9733726b5cb159`. Both use the same JDK 25, 6 GiB heap, 16 jobs and 6,144 MiB action-memory budget.','',
'All builds explicitly use `grpc://10.55.0.1:8080` (Cache & Carry), an empty instance name, remote reads/uploads, full output downloads and no local disk action cache. BES is disabled to isolate cache/build performance. The monitor samples launcher/server/descendant RSS and PSS every 100 ms; client time uses blocking waitpid. Guards monitor Windows and guest storage. RSS can double-count shared pages; PSS excludes filesystem page cache and Windows/WSL VM overhead.','',
'Before resuming: restore sufficient E: capacity, ensure other build work is idle, start the Windows storage watcher, run the corrected harness with `resume_prepare`, inspect remote-hit/output checks, then run `measure`. Preserve existing attempts. No further build was started after the storage guard fired.','',
'## Reclamation options, not executed','',
'Historical experiment cache/output directories are the primary development cleanup candidates. Preserve source worktrees, binary/source provenance, protocols, logs and measurement artifacts; verify exact cache/output paths before removal or archival. Offline VHD compaction requires stopping WSL and appropriate Windows privileges, so coordinate with active tasks.','',
'Moving the four Qwen model files from `E:\\QWEN` to D: would free about 148.51 GiB directly on E: without touching WSL, but model paths and loading performance would change. Steam library relocation is another option: Battlefield 6 is about 161.90 GiB and Baldur’s Gate 3 about 144.80 GiB. These are alternatives for user selection, not authorizations to move unrelated data.','',
f'Raw protocol, corrected harness, samples, disk inventories and SHA-256 manifest: [{REL.name}/](measurements/{REL.name}/). Raw BEP files and profiles remain local because BEP can contain environment values; their hashes are recorded. No credentials, compiled binaries or cache payloads are committed.','']
report=DOC/'docs/jsgette/skycache-v2-datadog-ssd-2026-10-25.md'
report.write_text('\n'.join(lines))
manifest=''.join(f'{sha(p)}  {p.relative_to(DEST).as_posix()}\n' for p in sorted(DEST.rglob('*')) if p.is_file())
(DEST/'SHA256SUMS').write_text(manifest)
shutil.copy2(report,TASK/'outputs/Datadog-SSD-storage-analysis.md')
print(json.dumps({'summary':{k:v for k,v in summary.items() if k not in ['windows','identified_skycache_cache_directories','duplicate_cache_audit','top_experiments']},'report':str(report)},indent=2))
