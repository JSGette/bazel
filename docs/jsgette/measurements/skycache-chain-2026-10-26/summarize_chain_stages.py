from pathlib import Path
import collections, json

root=Path('/home/jgett/REPOS/.work/skycache-chain-2026-10-26')
output={}
for name in ['diagnostic-local','diagnostic-retained']:
    rows=[json.loads(s) for s in (root/'warm-diagnostics'/(name+'-stages.jsonl')).read_text().splitlines()]
    assert not any('transform_error' in r for r in rows)
    grouped=collections.defaultdict(list)
    for r in rows:
        if 'method' in r:grouped[r['method']].append(r['duration_ns']/1e9)
    output[name]=dict(methods={k:dict(calls=len(v),sum_thread_seconds=sum(v),max_call_seconds=max(v),durations_s=v) for k,v in sorted(grouped.items())},counters=next(r for r in rows if r.get('summary')))
remote=output['diagnostic-retained']['counters']
c=remote['counters']
simulation=dict(requests=c['cached_ranges.calls'],unique_range_chunks=remote['unique_cached_range_chunks'],
    entries=[dict(capacity=n,hits=c[f'descriptor_lru_{n}.hits'],misses=c[f'descriptor_lru_{n}.misses'],hit_fraction=c[f'descriptor_lru_{n}.hits']/c['cached_ranges.calls']) for n in [16,32,64,128,256]],
    caveat='Simulation of descriptor identities in observed concurrent reference order only. It does not hold real descriptors, model pinned readers or path replacement, or demonstrate latency gains. Counters/locking perturb this diagnostic run; timed comparisons exclude it.')
(root/'stages.json').write_text(json.dumps(dict(runs=output,caveat='Diagnostic normal-return method boundaries; signatures retain overload distinctions. Nested calls and concurrent thread times must not be added to reconstruct command wall time.'),indent=2)+'\n')
(root/'descriptor-simulation.json').write_text(json.dumps(simulation,indent=2)+'\n')
for name,result in output.items():
    print(name)
    needles=['LocalSkycacheModule.beforeCommand','LocalCacheSession.refreshCandidates','LocalInputSnapshot.isUnchanged()','LocalCacheSession.writeWitnesses','LocalCacheSession.finish','RemoteSkyCache.prepareExclusive','RemoteSkyCache.publishPending','RemoteSkyCache.reuseRetainedChunk','RemoteSkyCache.close']
    print(json.dumps({k:{f:v[f] for f in ['calls','sum_thread_seconds','max_call_seconds']} for k,v in result['methods'].items() if any(k.startswith(n) for n in needles)},indent=2))
print(json.dumps(simulation,indent=2))
