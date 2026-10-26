#!/usr/bin/env python3
"""Summarize paired command timing and direct publication boundaries."""
from pathlib import Path
import hashlib
import json
import statistics

ROOT = Path('/home/jgett/REPOS/.work/skycache-witness-reuse-2026-10-26')
RUN = ROOT/'paired-probe-retry'


def main():
    result = json.loads((RUN/'results.json').read_text())
    assert result['status'] == 'passed'
    rows = []
    for run in result['runs']:
        if run.get('primer') or run.get('stabilization'):
            continue
        assert run['outputs_match'] and run['probe_output_matches']
        assert run['analysis_hits'] >= 17 and run['execution_hits'] >= 11
        assert run['committed']
        assert run['warmup'] or run['remote-skycache']['downloaded_bytes'] == 0
        timeline = [json.loads(x) for x in (RUN/(run['label']+'-timeline.jsonl')).read_text().splitlines()]
        build_complete = next(x['elapsed_s'] for x in timeline if 'Build completed successfully' in x['text'])
        uploads = [x for x in run['rpc'] if x['method'].endswith('/BatchUpdateBlobs')]
        commit = next(x for x in run['rpc'] if x['method'].endswith('/CommitPublication'))
        stats = run['local-skycache']
        rows.append(dict(label=run['label'], variant=run['variant'], warmup=run['warmup'],
            analysis_hits=run['analysis_hits'],execution_hits=run['execution_hits'],
            command_s=run['seconds'],
            upload_rpc_duration_sum_s=sum(x['duration_s'] for x in uploads),
            commit_rpc_duration_s=commit['duration_s'],
            wall_clock_elapsed_error_s=run['end_epoch']-run['start_epoch']-run['seconds'],
            after_build_complete_s=run['seconds']-build_complete,
            outside_profile_s=run['outside_profile_s'], profile_end_s=run['profile_end_s'],
            upload_calls=len(uploads), serialized_upload_request_bytes=sum(x['request_bytes'] for x in uploads),
            writes=stats['writes'], reused_witness_blocks=stats.get('reused_witness_blocks', 0),
            reused_witness_bytes=stats.get('reused_witness_bytes', 0),
            downloaded_bytes=run['remote-skycache']['downloaded_bytes']))
    fields=['command_s','upload_rpc_duration_sum_s','commit_rpc_duration_s','after_build_complete_s',
            'profile_end_s','outside_profile_s']
    measured=[r for r in rows if not r['warmup']]
    assert len(measured)==6
    assert {(r['analysis_hits'],r['execution_hits']) for r in measured}=={(18,11)}
    assert all(r['writes']>0 for r in measured)
    assert all(r['reused_witness_blocks']==(103 if r['variant']=='candidate' else 0) for r in measured)
    summary={}
    for variant in ['baseline','candidate']:
        group=[r for r in rows if r['variant']==variant and not r['warmup']]
        summary[variant]=dict(n=len(group),**{f:dict(median=statistics.median(r[f] for r in group),
            min=min(r[f] for r in group),max=max(r[f] for r in group)) for f in fields})
    changes={f:dict(saved_seconds=summary['baseline'][f]['median']-summary['candidate'][f]['median'],
        reduction_percent=100*(1-summary['candidate'][f]['median']/summary['baseline'][f]['median'])) for f in fields}
    pairings=[('r1-baseline','r2-candidate'),('r4-baseline','r3-candidate'),('r5-baseline','r6-candidate')]
    by_label={r['label']:r for r in rows}
    pairs=[dict(baseline=a,candidate=b,command_saved_s=by_label[a]['command_s']-by_label[b]['command_s'],
        after_build_saved_s=by_label[a]['after_build_complete_s']-by_label[b]['after_build_complete_s']) for a,b in pairings]
    source_files=['LocalCacheSession.java','LocalInputSnapshot.java','LocalWitnessTable.java']
    source=Path('/home/jgett/REPOS/bazel-skycache-remote-mvp/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local')
    report=dict(rows=rows,summary=summary,changes=changes,paired_differences=pairs,
        tests=json.loads((ROOT/'verification.json').read_text()),
        source_sha256={f:hashlib.sha256((source/f).read_bytes()).hexdigest() for f in source_files},
        source_head=result['source_head'],baseline_binary_sha256=result['binary_sha256'],
        candidate_binary_sha256=result['candidate_binary_sha256'],
        controls=dict(environment=result['environment_control'],
            source_states_unchanged=result['source_states_unchanged'],
            baseline_binary_unchanged=result['binary_unchanged'],candidate_binary_unchanged=result['candidate_binary_unchanged']),
        limitations='Three measured observations per binary, independent primers stabilized to two publications and loopback metadata service. '
        'One tiny fresh genrule per invocation forces both binaries to publish. Separate primers can differ in pack layout. '
        'Primers, stabilization and retained-chunk warmups excluded. Build-complete marker is output arrival, not exact method timing. '
        'Earlier attempts are excluded: an unpinned PATH changed the namespace; the rebuilt binary '
        'could not restore old analysis/execution keys; one candidate had no writes and skipped publication; '
        'and one baseline JVM crashed in compiled java.util.stream.Nodes$SpinedNodeBuilder.accept. '
        'Wall-clock steps occurred during this cohort, so no cross-clock pre-upload interval is reported; '
        'command and post-build intervals use the same monotonic clock. RPC durations are individual '
        'monotonic forwarding durations, not total publication latency. '
        'No WAN, percentile-latency or allocation claim.')
    (ROOT/'comparison.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(summary=summary,changes=changes,paired_differences=pairs,
        reuse=[(r['label'],r['reused_witness_blocks'],r['reused_witness_bytes']) for r in rows]),indent=2))


if __name__=='__main__':
    main()
