from skycache_all_common import *
import gzip
import re
import shutil

DEST=SOURCE/'docs/jsgette/measurements/skycache-all-targets-2026-10-27'
WORK=Path(__file__).parent
d=json.loads((ROOT/'baseline-matrix-heap14-results.json').read_text())
c=json.loads((ROOT/'comparison.json').read_text())
assert d['status']=='passed'
assert all(d[k] for k in ['source_unchanged','fixture_unchanged','binary_hashes_unchanged'])
primary=[r for r in d['runs'] if not r['preparation']]
assert len(primary)==6 and {a:sum(r['arm']==a for r in primary) for a in ['vanilla','disabled']}=={'vanilla':3,'disabled':3}
assert sha(DEST/'harness/skycache_all_benchmark.py')==d['harness_sha256']
for r in primary:
    assert r['exit_code']==0 and r['outputs_match'] and r['targets_match'] and r['empty_graph_verified']
    assert r['output_files']==10738 and len(r['successful_targets'])==5536 and int(r['configured_targets'])==598064
    assert '--host_jvm_args=-Xmx14g' in r['argv'] and '--check_visibility' in r['argv']
    assert '--remote_download_outputs=minimal' in r['argv']
    assert r['output_manifest_sha256']==sha(ROOT/(r['label']+'-outputs.json'))
assert c['source_results_sha256']==sha(ROOT/'baseline-matrix-heap14-results.json')
assert not (OUT/'server/server.pid.txt').exists()
probe=json.loads((ROOT/'local-admission-budget.json').read_text())
assert probe['checks'][0]['admitted'] is False and probe['checks'][1]['total_metadata_bytes']==313868680
assert json.loads((ROOT/'local-seed-witness-check-v3.json').read_text())['mismatches']==0
assert len(json.loads((ROOT/'local-restore-input-failure.json').read_text())['errors'])==2
outcomes=json.loads((ROOT/'target-outcomes.json').read_text())
assert len(outcomes)==6 and all(r['expanded_selected_targets']==5658 and len(r['skipped_labels'])==122 for r in outcomes)
shutil.copy2(ROOT/'target-outcomes.json',DEST/'target-outcomes.json')
for name in ['write_all_baseline_report.py','finalize_all_evidence.py','inspect_all_target_outcomes.py']:
    shutil.copy2(WORK/name,DEST/'harness'/name)

# Check only the new evidence for accidentally embedded credential values; report paths only.
patterns=[rb'gh[pousr]_[A-Za-z0-9]{30,}',rb'github_pat_[A-Za-z0-9_]{40,}',rb'AKIA[A-Z0-9]{16}']
for p in DEST.rglob('*'):
    if not p.is_file():continue
    data=gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_bytes()
    assert not any(re.search(pattern,data) for pattern in patterns),str(p)

section='## Full-repository graph limits and measurements (27 October 2026)'
main=SOURCE/'docs/jsgette/skycache-remote-mvp.md'
existing=main.read_text();assert section not in existing
vanilla=c['summary']['vanilla']['wall_s'];disabled=c['summary']['disabled']['wall_s']
main.write_text(existing.rstrip()+f'''

{section}

The approved `//...` workload excludes 36 unreachable packaging dependents and selects
5,658 top-level targets. It configures **598,064 targets including aspects**, about 2.52 times
the earlier graph; Bazel reports 5,536 completed and 122 skipped top-level targets in every
successful baseline. Three fresh-JVM/empty-graph measurements give a vanilla median of
**{vanilla['median']:.3f} s** ({vanilla['min']:.3f}–{vanilla['max']:.3f}) and an unchanged candidate with
SkyCache disabled median of **{disabled['median']:.3f} s** ({disabled['min']:.3f}–{disabled['max']:.3f}).
All six builds match the same 10,738 named output digest/length entries. Ordinary action
results are warm in an isolated disk cache; minimal downloads remain enabled. WSL was raised
to 20 GiB at the user's request, and the final paired controls use 14 GiB Java heaps.

There is **no valid local/remote cold/warm timing for this full graph yet**. Remote seeding
exceeds the 4 GiB logical-publication and 4 GiB + 1 MiB transfer limits with a 7.68 GB local
publication. Local admission requires 299.33 MiB, above its default 256 MiB limit; the default
attempt retries admission 72 times and restores nothing. The existing 512 MiB option admits
the graph, but a 10 GiB restore runs out of heap. With 14 GiB, restored execution state reaches
missing generated Go inputs in two `//pkg/security/seclwin/model` template actions. Those
same targets pass with vanilla and SkyCache disabled. Failed preparation is excluded from
performance summaries; local cold was not reached.

The next gates are generated-input materialization, cheap rejection of an over-budget
publication, restore memory use, and coordinated remote size limits. No production code
or protocol bound changed during these measurements. [Full method and findings](measurements/skycache-all-targets-2026-10-27/README.md),
[baseline samples](measurements/skycache-all-targets-2026-10-27/comparison.json), and
[preparation evidence](measurements/skycache-all-targets-2026-10-27/preparation-attempts.json).
''')
save(DEST/'verification.json',dict(status='passed',primary_samples=6,selected_top_level_targets=5658,
    completed_top_level_targets=5536,skipped_top_level_targets=122,
    configured_targets=598064,output_manifest_entries=10738,source_unchanged_during_measurement=True,
    fixture_unchanged_during_measurement=True,binaries_unchanged=True,harness_hash_matches=True,
    all_output_manifest_hashes_match=True,empty_graph_verified_each_run=True,
    cache_attempts_excluded=True,benchmark_servers_stopped=True,
    scope='Measurement evidence and documentation; no new production build or tests required.'))
files=[p for p in DEST.rglob('*') if p.is_file()]
print(json.dumps(dict(status='passed',files=len(files),archive_bytes=sum(p.stat().st_size for p in files),
    vanilla_median=vanilla['median'],disabled_median=disabled['median'])))
