#!/usr/bin/env python3
"""Summarize publication timings using one monotonic clock throughout."""
from pathlib import Path
import hashlib, json, statistics

ROOT=Path('/home/jgett/REPOS/.work/skycache-validation-2026-10-26')
RUN=ROOT/'paired'

def main():
    results=json.loads((RUN/'results.json').read_text())
    assert results['status']=='passed'
    assert all(results[k] for k in ['source_states_unchanged','binary_unchanged','candidate_binary_unchanged'])
    build=json.loads((ROOT/'build-result.json').read_text())
    assert build['exit_code']==0 and all(t['status']=='PASSED' and not t['errors'] and not t['failures'] for t in build['tests'])
    rows=[]
    for run in results['runs']:
        if run['primer'] or run['stabilization']: continue
        assert run['exit_code']==0 and run['outputs_match'] and run['probe_output_matches'] and run['committed']
        assert (run['analysis_hits'],run['execution_hits'])==(18,11)
        stats=run['local-skycache']
        assert stats['writes']>0 and stats['reused_witness_blocks']==103
        assert run['warmup'] or run['remote-skycache']['downloaded_bytes']==0
        timeline=[json.loads(line) for line in (RUN/(run['label']+'-timeline.jsonl')).read_text().splitlines()]
        build_done=next(x['elapsed_s'] for x in timeline if 'Build completed successfully' in x['text'])
        updates=[x for x in run['rpc'] if x['method'].endswith('/BatchUpdateBlobs')]
        commit=next(x for x in run['rpc'] if x['method'].endswith('/CommitPublication'))
        first=min(x['start_monotonic'] for x in updates)-run['command_start_monotonic']
        committed=commit['start_monotonic']+commit['duration_s']-run['command_start_monotonic']
        assert 0 < build_done < first < committed < run['seconds']
        rows.append(dict(label=run['label'],variant=run['variant'],warmup=run['warmup'],
            command_s=run['seconds'],build_complete_s=build_done,
            after_build_complete_s=run['seconds']-build_done,
            build_to_first_upload_s=first-build_done,
            first_upload_through_commit_s=committed-first,
            after_commit_s=run['seconds']-committed,
            upload_rpc_duration_sum_s=sum(x['duration_s'] for x in updates),
            upload_calls=len(updates),writes=stats['writes'],
            reused_witness_blocks=stats['reused_witness_blocks'],reused_witness_bytes=stats['reused_witness_bytes'],
            downloaded_bytes=run['remote-skycache']['downloaded_bytes'],
            analysis_hits=run['analysis_hits'],execution_hits=run['execution_hits'],
            wall_clock_elapsed_error_s=run['end_epoch']-run['start_epoch']-run['seconds']))
    fields=['command_s','build_complete_s','after_build_complete_s','build_to_first_upload_s','first_upload_through_commit_s','after_commit_s']
    measured=[r for r in rows if not r['warmup']]
    assert len(measured)==6
    summary={v:dict(n=len(g),**{f:dict(median=statistics.median(r[f] for r in g),min=min(r[f] for r in g),max=max(r[f] for r in g)) for f in fields}) for v in ['baseline','candidate'] if (g:=[r for r in measured if r['variant']==v])}
    changes={f:dict(saved_seconds=summary['baseline'][f]['median']-summary['candidate'][f]['median'],reduction_percent=100*(1-summary['candidate'][f]['median']/summary['baseline'][f]['median'])) for f in fields}
    by_label={r['label']:r for r in measured}
    pairs=[dict(baseline=a,candidate=b,command_saved_s=by_label[a]['command_s']-by_label[b]['command_s'],after_build_saved_s=by_label[a]['after_build_complete_s']-by_label[b]['after_build_complete_s']) for a,b in [('r1-baseline','r2-candidate'),('r4-baseline','r3-candidate'),('r5-baseline','r6-candidate')]]
    report=dict(rows=rows,summary=summary,changes=changes,paired_differences=pairs,
        source_base=results['source_head'],baseline_binary_sha256=results['binary_sha256'],candidate_binary_sha256=results['candidate_binary_sha256'],
        tests=dict(cases=build['cases'],targets=len(build['tests']),failed=0),
        controls={k:results[k] for k in ['source_states_unchanged','binary_unchanged','candidate_binary_unchanged','environment_control']},
        limitations='Three observations per binary; loopback metadata service and warm local storage. Separate binary-specific primers can differ in pack layout. '
        'Build-complete timing is log arrival, not a method boundary; the post-build interval includes more than input validation. '
        'RPC starts, command starts and log times share one Python monotonic clock. No WAN, tail-latency, peak memory or total CPU claim.')
    (ROOT/'comparison.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(summary=summary,changes=changes,paired_differences=pairs),indent=2))

if __name__=='__main__': main()
