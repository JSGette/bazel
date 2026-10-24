from pathlib import Path
import json
import statistics
import shutil

RUN = Path('/mnt/skycache-v2-local')
DOCS = Path('/home/jgett/REPOS/bazel-skyframe-findings/docs/jsgette')
EVIDENCE = DOCS / 'measurements/skycache-v2-wsl-2026-10-24'

def read(relative):
    return json.loads((RUN / relative).read_text())

def rows(relative):
    return [json.loads(line) for line in (RUN / relative).read_text().splitlines() if line.startswith('{')]

protocol = read('protocol.json')
required_stages = {'build-and-tests', 'idle-ab', 'cold-import', 'worktrees',
                   'mixed-worktrees', 'dependency-1', 'dependency-2',
                   'packed-small-1', 'packed-small-2', 'packed-medium-1', 'packed-medium-2'}
completed_stages = {s['name'] for s in protocol['stages'] if s['status'] == 'passed'}
if not required_stages <= completed_stages:
    raise RuntimeError('Cannot aggregate unfinished stages: ' + ', '.join(sorted(required_stages-completed_stages)))
idle = read('idle-ab/results.json')
cold = read('cold-import/results.json')
worktrees = read('worktrees/results.json')
mixed = read('mixed-worktrees/results.json')
assert idle['status'] == cold['status'] == 'passed'
assert worktrees['passed'] and mixed['passed']
assert len(idle['attempts']) == 25 and all(a['exit_code'] == 0 and a['expected_output_matches'] for a in idle['attempts'])
summary = {'tests': protocol['tests'], 'candidate_binary': protocol['candidate_binary'],
           'baseline_binary': protocol['baseline_binary'], 'fixture_builds': {}, 'idle': {},
           'validator': [], 'packed_store': [], 'limitations': protocol['limitations']}

for enabled, name in [(True, 'skycache'), (False, 'off')]:
    cycles = [c for c in idle['cycles'] if c.get('enabled') is enabled]
    assert len(cycles) == 3 and all(c['eviction']['evicted'] for c in cycles)
    summary['idle'][name] = {
        'recovery_seconds': [c['recovery_seconds'] for c in cycles],
        'median_recovery_seconds': statistics.median(c['recovery_seconds'] for c in cycles),
        'median_warm_seconds': statistics.median(c['warm_seconds'] for c in cycles),
        'median_heap_released_mib': statistics.median((c['eviction']['heap_before_bytes']-c['eviction']['heap_after_bytes'])/2**20 for c in cycles),
        'median_pss_released_mib': statistics.median((c['retained_pss']['pss_kib']-c['evicted_pss']['pss_kib'])/1024 for c in cycles),
    }
summary['idle']['recovery_ratio_off_over_on'] = summary['idle']['off']['median_recovery_seconds']/summary['idle']['skycache']['median_recovery_seconds']
summary['fixture_builds']['idle'] = len(idle['attempts'])
summary['fixture_builds']['cold_import'] = len(cold['attempts'])
for name, result in [('worktrees', worktrees), ('mixed_worktrees', mixed)]:
    builds = [b for p in result['phases'] for b in p['builds']]
    assert len(builds) == 18 and all(b['exit_code'] == 0 for b in builds)
    summary['fixture_builds'][name] = len(builds)
    summary[name] = {'passed': result['passed'], 'phases': [p['phase'] for p in result['phases']],
                     'publication_failures': sum(bool(b['skycache'].get('failed')) for b in builds),
                     'corruption': result['corruption']}
summary['fixture_builds']['total'] = sum(summary['fixture_builds'].values())
summary['cold_import'] = {'passed': True, 'same_pid_recovery': cold['attempts'][0]['pid'] == cold['attempts'][1]['pid'],
                          'writes': [a['skycache']['writes'] for a in cold['attempts']],
                          'analysis_hits': [a['analysis_hits'] for a in cold['attempts']]}

for number in (1, 2):
    data = rows(f'dependency-{number}.jsonl')
    assert len(data) == 40
    assert all(r['statistics']['records_read'] == 8832 and r['statistics']['witnesses_checked'] == 8192 and r['statistics']['rejected_roots'] == 0 for r in data)
    for threads in (1, 2, 4, 8):
        row = {'jvm': number, 'threads': threads, 'records_read': 8832, 'witnesses_checked': 8192}
        for implementation in ('serial_baseline', 'root_stripes'):
            samples = [r['validation_ms'] for r in data if r['threads'] == threads and r['implementation'] == implementation]
            assert len(samples) == 5
            row[implementation + '_median_ms'] = statistics.median(samples)
        row['serial_over_parallel'] = row['serial_baseline_median_ms']/row['root_stripes_median_ms']
        summary['validator'].append(row)
for size in ('small', 'medium'):
    for number in (1, 2):
        data = rows(f'packed-{size}-{number}.jsonl')
        assert len(data) == 20
        for threads in (1, 2, 4, 8):
            selected = [r for r in data if r['threads'] == threads]
            assert len(selected) == 5
            summary['packed_store'].append({'size': size, 'jvm': number, 'threads': threads,
                'entries': selected[0]['entries'], 'value_bytes': selected[0]['value_bytes'],
                'median_durable_write_ms': statistics.median(r['durable_write_ms'] for r in selected),
                'median_read_ms': statistics.median(r['read_ms'] for r in selected)})

(RUN / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
shutil.copyfile(RUN / 'summary.json', EVIDENCE / 'summary.json')
protocol['status'] = 'local_validation_complete'
protocol['remaining_work'] = ['Representative real-target A/B on matched storage and configuration',
    'Separate serial-idle control and temporal drift control',
    'Broader design items: proof blocks, observation-bound provenance, selective publication, global in-flight byte budget']
(RUN / 'protocol.json').write_text(json.dumps(protocol, indent=2)+'\n')
shutil.copyfile(RUN / 'protocol.json', EVIDENCE / 'protocol.json')

lines = [
    'The rebuilt executable and all **190 cases in 12 test targets passed**. The',
    'fresh build completed in 478.174 seconds; this is setup cost, not a SkyCache',
    'performance measurement. All **63 fixture builds passed**, with independent',
    'output checks, same-JVM recovery, repeated imported-only cycles, same-mtime',
    'content invalidation, concurrent worktree edits, retained-value invalidation,',
    'and deliberately corrupted-chunk fallback. The cold-import probe confirmed',
    'zero novel writes in both its cold import and same-PID post-idle recovery.', '',
    '[Machine-readable summary](measurements/skycache-v2-wsl-2026-10-24/summary.json)',
    'contains every aggregate and links through the protocol to commands and logs.', '',
    '| Condition | Median full-client recovery | Median warm command |',
    '| --- | ---: | ---: |',
]
for name in ('skycache', 'off'):
    r=summary['idle'][name]
    lines.append(f"| {name} | {r['median_recovery_seconds']:.3f} s | {r['median_warm_seconds']:.3f} s |")
lines += ['', f"The median cache-off/SkyCache recovery ratio was {summary['idle']['recovery_ratio_off_over_on']:.2f}× in this synthetic fixture.",
    'The warm command always runs with SkyCache enabled so that both conditions have',
    'the same durable backing and eviction setup; the off row switches it off only',
    'for the measured recovery. Three samples do not support a tail-latency claim.',
    'The recovery result is not a representative real-build speedup.', '',
    '| Threads | Serial median, JVM 1 / 2 (ms) | Parallel median, JVM 1 / 2 (ms) |',
    '| ---: | ---: | ---: |']
for threads in (1,2,4,8):
    a,b=[r for r in summary['validator'] if r['threads']==threads]
    lines.append(f"| {threads} | {a['serial_baseline_median_ms']:.2f} / {b['serial_baseline_median_ms']:.2f} | {a['root_stripes_median_ms']:.2f} / {b['root_stripes_median_ms']:.2f} |")
lines += ['', 'Every measured validator round read exactly 8,832 records and checked 8,192',
    'witnesses, with zero rejected roots. Packed-store results retain separate fresh',
    'JVMs and sample sizes in the JSON; durable writes include sealing/fsync and all',
    'reads are byte-checked. Storage timings are specific to the C:-backed loop volume.', '',
    'The old/new worktree probe used the verified original base executable in B and',
    'the new executable in A. Both the same-version and mixed-version probes passed',
    'all ten phases (18 builds each). This covers the tested fixtures and format',
    'compatibility; it is not a general production compatibility certification.']
document=DOCS/'skycache-v2-wsl-continuation-2026-10-24.md'
text=document.read_text()
old='In progress. Only stages marked passed in the structured protocol are completed.\nThe original cloud evidence is not a substitute for these local checks.'
assert old in text
document.write_text(text.replace(old, '\n'.join(lines)))
print(json.dumps({k:summary[k] for k in ('fixture_builds','idle','cold_import')},indent=2))
