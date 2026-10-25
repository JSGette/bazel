from pathlib import Path
import json,hashlib
R=Path('/home/jgett/REPOS/.work/datadog-ssd-fresh-2026-10-25')
D=Path('/home/jgett/REPOS/bazel-skyframe-findings')
E=D/'docs/jsgette/measurements/skycache-v2-fresh-wsl-2026-10-25'
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
s=json.loads((R/'summary.json').read_text());p=json.loads((R/'results.json').read_text())
assert s['status']=='passed'
report=D/'docs/jsgette/skycache-v2-fresh-wsl-2026-10-25.md'
base=report.read_text().split('Benchmark preparation is in progress.')[0].split('The final Ubuntu virtual disk is ')[0]
state=json.loads((E/'final-reset-state.json').read_text())
base+=f"The final Ubuntu virtual disk is {state['ubuntu_vhd_bytes']/1024**3:.2f} GiB, versus 718.80 GiB before reset. After all builds, E: has {state['e_free_bytes']/1024**3:.2f} GiB free and C: has {state['c_free_bytes']/1024**3:.2f} GiB free.\n"
rows=[]
roles={'fresh_server':'Fresh server, existing outputs','warm':'Warm unchanged-source build','idle_recovery':'Build after 20 seconds idle','remote_rebuild':'Fresh server, local action cache disabled'}
names={'vanilla':'Vanilla baseline','off':'SkyCache binary, cache off','skycache':'SkyCache enabled'}
for role,title in roles.items():
 for v,name in names.items():
  g=s['groups'].get(v+'/'+role)
  if not g:continue
  w=g['wall_seconds'];m=g['peak_tree_pss_mib'];rss=g['peak_tree_rss_mib']
  rows.append(f"| {title} | {name} | {w['n']} | {w['median']:.3f} ({w['min']:.3f}–{w['max']:.3f}) | {m['median']:.0f} | {rss['median']:.0f} |")
idle=[]
for v,name in names.items():
 i=s['idle'][v]
 idle.append(f"| {name} | {i['retained_12s_pss_mib']['median']:.0f} | {i['retained_20s_pss_mib']['median']:.0f} | {i['change_12_to_20s_mib']['median']:.0f} |")
comparisons=[]
for role,title in roles.items():
 c=s['comparisons'][role]['skycache_vs_vanilla_wall_change_percent']
 comparisons.append(f"- {title}: SkyCache median wall time was {abs(c):.1f}% {'higher' if c>=0 else 'lower'} than vanilla.")
prep=[]
for r in s['preparation']:
 prep.append(f"| {r['label']} | {r['exit_code']} | {r['seconds']:.3f} |")
remote=[]
for v in ['vanilla','skycache']:
 g=s['groups'][v+'/remote_rebuild']
 local=[sum(x.get('count',0) for x in counts if x.get('execKind')=='Local') for counts in g['runner_counts']]
 remote.append(f"- {names[v]} Cache & Carry hits across the three forced rebuilds: {g['remote_hits']}; local sandbox actions: {local}.")
detail=f'''
## Completed comparison

This real Datadog workload does not reproduce the earlier synthetic speedups. SkyCache warm builds are {1/s['comparisons']['warm']['vanilla_over_skycache_speed_ratio']:.1f} times slower than vanilla at the median, and retained-validation work dominates that regression. Remote rebuild medians differ by only {s['comparisons']['remote_rebuild']['skycache_vs_vanilla_wall_change_percent']:.1f}% with overlapping observed ranges. SkyCache also uses more foreground process memory; idle eviction lowers its own footprint substantially but leaves it slightly above vanilla's retained footprint.

All {s['successful_builds']} Bazel builds succeeded and produced the same cluster-agent binary. Four preparation builds are excluded from comparison; 42 measured builds cover three rotated rounds of vanilla, SkyCache off, and SkyCache on, followed by three remote-cache rebuilds per enabled implementation. Nine idle cycles were recorded. The initial SkyCache publication rejection is preserved as a failed preparation validation, despite the underlying build succeeding.

The timings below include client startup and completion, including synchronous publication/validation work. Values are medians; parentheses show the full observed range. PSS is proportional resident memory of the Bazel process tree, including compiler children, sampled at 100 ms. RSS is also retained but can count shared pages more than once. These are small repeated samples on one machine, without significance claims.

| Build scenario | Implementation | n | Wall seconds, median (range) | Peak PSS MiB, median | Peak RSS MiB, median |
|---|---|---:|---:|---:|---:|
{chr(10).join(rows)}

{chr(10).join(comparisons)}

## Observed warm-build bottleneck

SkyCache's no-change warm builds repeatedly validated 163,619 dependency records and read 52,882,761 bytes of CAS values (about 50.4 MiB), despite configuring no targets. The first warm run logged 10.09 seconds in invalidation lookup; the second round's first warm run logged 3.380 seconds. These observed validation costs explain a substantial part of the warm-build regression. Per-run timings and counters are in `summary.json` under `warm_validation_overhead`, with profile observations alongside the evidence. The next optimization target is avoiding repeated dependency validation and CAS reads when the retained graph and its input evidence remain valid. The benchmark does not modify or disable validation to obtain a faster number.

## Idle memory

SkyCache alone uses a 15-second analysis retention deadline; it evicts the graph and requests GC. Vanilla and the disabled control retain their normal behavior. The 12-to-20-second change is the median paired change within each cycle, so it need not equal the difference of the two displayed medians. Eviction receipts are included in `results.json`.

| Implementation | PSS at 12 s, MiB | PSS at 20 s, MiB | Paired PSS reduction, MiB |
|---|---:|---:|---:|
{chr(10).join(idle)}

## Cache & Carry and preparation

Every invocation explicitly used `grpc://10.55.0.1:8080`, with no disk action cache. The repository wrapper's automatic cache selection was disabled to select this endpoint explicitly. Preparation filled fresh E: output bases; measurements reused the resolved dependencies and outputs. The remote rebuilds started a fresh server and set `--nouse_action_cache`, forcing action execution/cache lookup while retaining existing filesystem contents. They are not empty-output or cold-OS-cache download tests.

{chr(10).join(remote)}

Where remote-hit and local-action counts differ, the measured wall difference includes that action mix and network variability. It cannot be attributed solely to analysis-cache performance. Internal action counts are recorded separately in the raw metrics.

The first vanilla build ran 5,704 local sandbox actions. Six asynchronous remote uploads exceeded the original 120-second RPC deadline; the build itself succeeded. The first SkyCache build obtained 5,697 remote hits and ran seven local sandbox actions. Its output matched vanilla, but analysis snapshot publication was rejected with `inputs changed during the invocation`. Exact changed witnesses were not reported; fresh external-repository materialization is a hypothesis, not a proven cause. Retrying with the populated output bases and the module lockfile frozen succeeded. Both measured implementations used a 600-second remote timeout, and every measured run passed the no-remote-warning check. The original attempts, hashes, logs and diagnosis were retained.

| Preparation attempt | Bazel exit | Wall seconds (not comparative) |
|---|---:|---:|
{chr(10).join(prep)}

## Reproduction and limits

- Host: AMD Ryzen 9 9950X3D, 16 cores / 32 threads, about 32 GiB installed RAM. WSL exposes approximately 15.4 GiB RAM. Storage: Linux ext4 inside `E:\\WSL\\Ubuntu\\ext4.vhdx`, on the WD Blue SN570 2 TB NVMe SSD. C: is a separate WD Black NVMe drive.
- Datadog revision: `{s['datadog_revision']}`; target: `//cmd/cluster-agent:cluster-agent`. This measures the Datadog cluster-agent target, not all targets in the repository.
- SkyCache: `{s['skycache_commit']}`. Vanilla: `{s['vanilla_commit']}`, the source-built pre-SkyCache fork baseline, not the official Bazel 9.3.0 release binary. An additional control uses the SkyCache binary with the feature disabled.
- Same JDK 25, 6 GiB JVM heap cap, 16 action jobs, 6,144 MiB action-memory budget. At least 30 GiB E: and 10 GiB C: reserves were enforced. Exact command arrays and binary hashes are in `results.json`.
- Sources were unchanged during measurement; the resolved `MODULE.bazel.lock` is represented by its hash and patch. All measured invocations used `--lockfile_mode=error`. The initial module resolution is not a measurement.
- OS caches were warm. Timed runs are unchanged-source builds and forced remote-cache rebuilds; this does not measure incremental source edits or compare cold compilation throughput. The initial 793-second and 205-second setup times have different cache conditions and must not be interpreted as a SkyCache speedup.
- PSS/RSS exclude Windows host overhead and Linux filesystem page cache. Peaks are sampled, not an exact continuous maximum. Other Windows workloads were not controlled.
- Output SHA-256 for every successful build: `{s['output_sha256']}`.

Full raw BEP, profiles, logs and samples are retained in `/home/jgett/REPOS/.work/datadog-ssd-fresh-2026-10-25` and a verified private archive on D:. Public evidence includes the exact harness versions, filtered metrics, compact sample series, storage protocols, source-lock patch and hashes of raw evidence. Arbitrary BEP client environment values are not published.

Evidence: [reset, setup, benchmark and validation artifacts](measurements/skycache-v2-fresh-wsl-2026-10-25/).
'''
report.write_text(base+detail)
(T/'outputs/Datadog-fresh-WSL-benchmark.md').write_text(base+detail)
(E/'render-fresh-report.py').write_bytes(Path(__file__).read_bytes())
hashes=[]
for f in sorted(E.rglob('*')):
 if f.is_file() and f.name!='SHA256SUMS':hashes.append(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+str(f.relative_to(E)))
(E/'SHA256SUMS').write_text('\n'.join(hashes)+'\n')
print(report)
