from skycache_all_common import *
import shutil

attempt=json.loads((ROOT/'cache-prime-5-results.json').read_text())
r=attempt['runs'][0]
assert r['exit_code']==0 and r['outputs_match'] and r['targets_match']
assert r['cache_errors']==['SkyCache publication skipped: SkyCache transfer exceeds its byte budget']
assert r['local-skycache']['published'] and not r['remote_committed']
snapshot=ROOT/'remote-attempt-harness'
snapshot.mkdir()
for name in ['skycache_all_common.py','skycache_all_benchmark.py','run_all_measurements.py','prepare_all_seed.py']:
    shutil.copy2(Path(__file__).parent/name,snapshot/name)
files=[p for p in (ROOT/'cache-cache-prime-5-remote/leased-v2').rglob('*') if p.is_file() and not p.is_symlink()]
save(ROOT/'remote-availability.json',dict(status='unsupported_for_this_workload_at_current_limits',
    result_file='cache-prime-5-results.json',result_sha256=sha(ROOT/'cache-prime-5-results.json'),
    configured_targets=r['configured_targets'],successful_top_level_targets=len(r['successful_targets']),
    output_files=r['output_files'],outputs_match=r['outputs_match'],targets_match=r['targets_match'],
    maximum_transfer_bytes=4*1024**3+1024**2,maximum_publication_bytes=4*1024**3,
    serialized_key_bytes=r['local-skycache']['cas_put_key_bytes'],
    serialized_value_bytes=r['local-skycache']['cas_put_value_bytes'],
    local_publication_files=[dict(path=str(p.relative_to(ROOT)),bytes=p.stat().st_size) for p in files],
    ordinary_action_cache_warning='RESOURCE_EXHAUSTED: Not enough space to create a replica',
    measured_remote_warm_or_cold=False,
    next_cohort_controls='All remaining arms use the same isolated disk action cache, read-only external action cache, and disabled BES uploads. Production SkyCache limits and binaries are unchanged.'))
print(json.dumps(dict(local_storage_bytes=sum(p.stat().st_size for p in files),local_files=len(files),
    serialized_value_bytes=r['local-skycache']['cas_put_value_bytes'],remote_committed=r['remote_committed'])))
