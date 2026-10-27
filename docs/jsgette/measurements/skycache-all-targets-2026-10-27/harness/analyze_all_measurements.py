"""Summarize only successful, matched primary commands; retain every sample."""
import argparse
import collections
import gzip
import statistics
from skycache_all_common import *

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--tag',default='matrix');args=parser.parse_args()
    source=ROOT/(args.tag+'-results.json')
    data=json.loads(source.read_text());assert data['status']=='passed'
    assert data['source_unchanged'] and data['fixture_unchanged'] and data['binary_hashes_unchanged']
    groups=collections.defaultdict(list);rows=[]
    for r in data['runs']:
        assert r['exit_code']==0 and r['outputs_match'] and r['targets_match'] and r['empty_graph_verified']
        assert not r['cache_errors']
        if r['preparation']:continue
        metrics=r['build_metrics']
        actions=metrics.get('actionSummary',{})
        timings=metrics.get('timingMetrics',{})
        events=json.loads(gzip.open(ROOT/(r['label']+'.profile.gz'),'rt').read())['traceEvents']
        phases=[dict(name=e.get('name'),start_s=e.get('ts',0)/1e6,duration_s=e.get('dur',0)/1e6)
                for e in events if e.get('cat')=='build phase marker']
        before=sum(e.get('dur',0) for e in events if e.get('name')=='LocalSkycacheModule.beforeCommand')/1e6
        timeline=[json.loads(line) for line in (ROOT/(r['label']+'-timeline.jsonl')).read_text().splitlines()]
        complete=next(x['s'] for x in timeline if 'Build completed successfully' in x['text'])
        row=dict(label=r['label'],arm=r['arm'],wall_s=r['wall_s'],
            wall_clock_elapsed_s=r.get('wall_clock_elapsed_s'),clock_disagreement_s=r.get('clock_disagreement_s'),
            adjusted_monotonic_elapsed_s=r.get('adjusted_monotonic_elapsed_s'),
            client_cpu_s=r['client_process_delta'].get('cpu_s'),bep_client_cpu_s=int(timings.get('cpuTimeInMs',0))/1000,
            bep_analysis_s=int(timings.get('analysisPhaseTimeInMs',0))/1000,
            bep_execution_s=int(timings.get('executionPhaseTimeInMs',0))/1000,
            time_to_analysis_complete_s=next((x['s'] for x in timeline if 'INFO: Analyzed ' in x['text']),None),
            metadata_worker_cpu_s=r['metadata_worker_delta'].get('cpu_s',0),
            startup_and_clean_s=r['startup_and_clean_s'],before_command_s=before,finish_s=r['wall_s']-complete,
            configured_targets=r['configured_targets'],final_graph=r['final_graph'],
            analysis_hits=r['analysis_hits'],execution_hits=r['execution_hits'],
            outputs=r['output_files'],successful_top_level_targets=len(r['successful_targets']),
            action_summary=actions,timing_metrics=timings,profile_phase_markers=phases,
            local=r.get('local-skycache'),validation=r.get('local-skycache-validation'),
            remote=r.get('remote-skycache'),cache_storage=r['cache_storage'],
            swap_pages_delta=r['swap_pages_delta'],client_rss_after=r['client_rss_after'],
            memory_metrics=metrics.get('memoryMetrics',{}),
            named_files_without_digest=r.get('named_files_without_digest',[]),
            client_io=r['client_process_delta'],metadata_worker_io=r['metadata_worker_delta'])
        if row['arm'].startswith('remote'):
            remote=row['remote']
            assert remote['local_cache_errors']==0
            if row['arm']=='remote-warm':assert remote['downloaded_bytes']==0
        row['client_plus_metadata_cpu_s']=row['client_cpu_s']+row['metadata_worker_cpu_s']
        for k in ['metadata_pages','client_metadata_pages','server_metadata_pages']:
            if k in r:row[k]=r[k]
        groups[r['arm']].append(row);rows.append(row)
    fields=['wall_s','client_cpu_s','bep_client_cpu_s','metadata_worker_cpu_s','client_plus_metadata_cpu_s','bep_analysis_s','bep_execution_s',
            'startup_and_clean_s','before_command_s','finish_s']
    summary={arm:dict(n=len(group),**{f:dict(samples=[r[f] for r in group],mean=statistics.mean(r[f] for r in group),
        median=statistics.median(r[f] for r in group),min=min(r[f] for r in group),max=max(r[f] for r in group)) for f in fields})
        for arm,group in groups.items()}
    result=dict(summary=summary,rows=rows,source_results_sha256=sha(source),elapsed_timing_clock=data.get('elapsed_timing_clock'),
        phase_clock_note='Command and log-timeline durations use CLOCK_MONOTONIC_RAW. BEP and JVM profile phase durations retain their native clock; they are not interchangeable wall-time partitions.',
        source_head=data['source']['head'].strip(),binary_hashes=data['binaries'],workload=data['workload'])
    save(ROOT/'comparison.json',result)
    print(json.dumps({arm:dict(n=s['n'],wall=s['wall_s'],cpu_mean=s['client_cpu_s']['mean'],
        worker_cpu_mean=s['metadata_worker_cpu_s']['mean']) for arm,s in summary.items()},indent=2))

if __name__=='__main__':main()
