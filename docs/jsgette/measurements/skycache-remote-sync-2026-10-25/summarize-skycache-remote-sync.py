from pathlib import Path
import json, statistics, hashlib, importlib.util

R = Path('/home/jgett/REPOS/.work/skycache-remote-sync-2026-10-25')
TASK = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
spec = importlib.util.spec_from_file_location('grpc_helpers', TASK/'work/datadog-concurrent.py')
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
summary = {'matrices': {}, 'probes': {}, 'publication_sizes': {}}
for name, folder in [('intermediate', R/'datadog-six-matched-tools'), ('final', R/'update/datadog-six')]:
    p = json.loads((folder/'results.json').read_text())
    assert p['status'] == 'passed' and p['module_lock_restored'] and p['workspace_restored']
    assert p['workspace_unchanged_during_matrix']
    rows = [x for x in p['runs'] if not x['preparation']]
    groups = []
    for mode in ['minimal', 'toplevel']:
        for implementation in dict.fromkeys(x['implementation'] for x in rows):
            runs = [x for x in rows if x['mode'] == mode and x['implementation'] == implementation]
            def aggregate(values):
                return dict(median=statistics.median(values), minimum=min(values), maximum=max(values), values=values)
            groups.append(dict(mode=mode, implementation=implementation, count=len(runs),
                               seconds=aggregate([x['seconds'] for x in runs]),
                               pss_mib=aggregate([x['peak_pss_kib']/1024 for x in runs]),
                               analysis_seconds=aggregate([int(x['build_metrics']['timingMetrics']['analysisPhaseTimeInMs'])/1000 for x in runs]),
                               action_result_calls=sorted({x['grpc']['method_attempt_counts'].get('build.bazel.remote.execution.v2.ActionCache/GetActionResult',0) for x in runs}),
                               cas_read_calls=sorted({x['grpc']['bytestream_read_calls'] for x in runs}),
                               cas_read_bytes=sorted({x['grpc']['bytestream_read_bytes'] for x in runs})))
    profile_checks = []
    for row in rows:
        data = (folder/(row['label']+'.grpc.bin')).read_bytes(); pos=0; count=0
        while pos < len(data):
            size,pos=c.modes.varint(data,pos); fields=c.modes.fields(data[pos:pos+size]);pos+=size
            if not fields.get(3,[b''])[0].decode().endswith('/FindMissingBlobs'):continue
            request=c.modes.fields(c.modes.fields(c.modes.fields(fields[4][0])[10][0])[1][0])
            digests=[c.modes.fields(x) for x in request.get(2,[])]
            profile=folder/(row['label']+'.profile.gz')
            assert len(digests)==1
            assert digests[0][1][0].decode()==hashlib.sha256(profile.read_bytes()).hexdigest()
            assert digests[0][2][0]==profile.stat().st_size
            count+=1
        assert count<=1
        profile_checks.append(dict(run=row['label'], find_missing_blobs=count, only_profile_digest=True))
    summary['matrices'][name] = dict(measured_runs=len(rows), preparations=len(p['runs'])-len(rows),
                                    boot_id=p['boot_id'], groups=groups, profile_checks=profile_checks,
                                    binary_sha256=p['binary_sha256'])
for name, folder in [('intermediate',R), ('final',R/'update')]:
    remote=json.loads((folder/'remote/results.json').read_text())
    execution=json.loads((folder/'execution/results.json').read_text())
    worktrees=json.loads((folder/'worktrees/results.json').read_text())
    assert remote['passed'] and execution['status']=='passed' and worktrees['passed']
    summary['probes'][name]=dict(remote=[dict(phase=x['phase'], hits=x['analysis_hits'],
        writes=x['local_stats']['writes'], deduplicated_writes=x['local_stats']['deduplicated_writes'],
        committed=x['remote_committed'], transfers=x['remote_transfers'], output_matches=x['output_matches_expected']) for x in remote['phases']],
        execution_runs=len(execution['runs']), worktree_phases=len(worktrees['phases']),
        shutdown_failures=remote['shutdown_failures'])
    sizes=[]
    for cache in folder.glob('*cache-*'):
        for obj in (cache/'leased-v2/objects/segmented-v1').glob('*'):
            if not obj.is_dir():continue
            files=[p for p in obj.iterdir() if p.is_file()]
            sizes.append(dict(cache=cache.name, publication=obj.name,
                              bytes=sum(p.stat().st_size for p in files), files=len(files)))
    summary['publication_sizes'][name]=sizes
(R/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps({name:mat['groups'] for name,mat in summary['matrices'].items()},indent=2))
