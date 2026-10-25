#!/usr/bin/env python3
"""Read existing SkyCache storage benchmark traces; never starts Bazel or changes inputs."""

import argparse
from collections import defaultdict
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import re
import statistics


NOTES = [
    'runAnalysisAndExecutionPhase combines loading, analysis and execution under Skymeld; '
    'these profiles do not provide exclusive analysis wall time.',
    'Spans can overlap or contain other spans. Do not add named span durations, action '
    'envelopes or independently computed medians to reconstruct wall time.',
    'Time to first recorded Genrule includes preparation and analysis/read work; it is '
    'not a precise exclusive analysis duration. Small events may be compacted by Bazel.',
    'Recorded Genrule envelope spans first recorded action-processing start to last '
    'recorded action-processing end; it includes scheduling/waiting and is not CPU time.',
    'Registry-download span names establish profiled registry access, not measured '
    'external network bytes or an exclusive network-time measurement.',
    'Bazel default retention can remove earlier traces. Medians use only matched traces; '
    'profile coverage and missing labels are reported explicitly.',
    'LocalSkycacheModule.beforeCommand is separate from the combined analysis/execution '
    'span. Post-command publication work may fall outside the recorded profile.',
]

METRICS = [
    'wall_seconds', 'bazel_seconds', 'main_repository_mapping_seconds',
    'analysis_and_execution_seconds', 'skycache_before_command_seconds',
    'time_to_first_recorded_genrule_seconds', 'recorded_genrule_envelope_seconds',
    'profile_end_seconds', 'registry_download_span_count',
]


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def option(argv, name):
    prefix = name + '='
    for index, arg in enumerate(argv):
        if arg.startswith(prefix):
            return arg[len(prefix):]
        if arg == name and index + 1 < len(argv):
            return argv[index + 1]
    return None


def load_profile(path):
    encoded = path.read_bytes()
    data = json.loads(gzip.decompress(encoded) if encoded[:2] == b'\x1f\x8b' else encoded)
    return {'path': str(path), 'sha256': hashlib.sha256(encoded).hexdigest(),
            'metadata': data['otherData'], 'events': data['traceEvents']}


def matches_time(profile, row):
    start = profile['metadata'].get('profile_start_ts')
    return start is not None and (
        timestamp(row['started_utc']) - 0.25 <= start / 1000
        <= timestamp(row['finished_utc']) + 0.25)


def inspect_profile(profile, row, case, case_root, source):
    metadata = profile['metadata']
    build_id = metadata['build_id']
    expected_base = option(row['argv'], '--output_base')
    if str(Path(metadata['output_base']).resolve()) != str(Path(expected_base).resolve()):
        raise ValueError(row['label'] + ': profile output base does not match invocation')
    if not matches_time(profile, row):
        raise ValueError(row['label'] + ': profile timestamp outside invocation')
    path = Path(profile['path'])
    if source == 'automatic-profile':
        if path.name != 'command-' + build_id + '.profile.gz':
            raise ValueError(row['label'] + ': profile filename/build ID mismatch')
    else:
        configured = option(row['argv'], '--profile')
        if (configured is None or configured != row['profile']
                or Path(configured).name != path.name):
            raise ValueError(row['label'] + ': explicit profile path does not match argv')

    log_path = case_root / (row['label'] + '.log')
    log = log_path.read_text() if log_path.exists() else ''
    ids = sorted(set(re.findall(r'Invocation ID:\s*([0-9a-fA-F-]{36})', log)))
    if ids and ids != [build_id]:
        raise ValueError(row['label'] + ': log invocation ID does not match profile')

    events = profile['events']

    def named_span(name, optional=False):
        spans = [event for event in events
                 if event.get('ph') == 'X' and event.get('name') == name]
        if optional and not spans:
            return None
        if len(spans) != 1:
            raise ValueError(row['label'] + ': expected one span: ' + name)
        return spans[0]

    mapping = named_span('compute main repo mapping')
    combined = named_span('runAnalysisAndExecutionPhase')
    before_command = named_span('LocalSkycacheModule.beforeCommand', optional=True)
    actions = [event for event in events
               if event.get('ph') == 'X' and event.get('cat') == 'action processing'
               and event.get('args', {}).get('mnemonic') == 'Genrule']
    first_action = min((event['ts'] for event in actions), default=None)
    last_action = max((event['ts'] + event['dur'] for event in actions), default=None)
    result = {
        'case': case, 'mode': row['mode'], 'changed': row['changed'], 'label': row['label'],
        'profile': str(path), 'profile_sha256': profile['sha256'], 'build_id': build_id,
        'profile_date': metadata.get('date'),
        'mapping_evidence': {'source': source, 'output_base_matches': True,
                             'timestamp_within_invocation': True,
                             'log_invocation_ids': ids, 'log_invocation_id_available': bool(ids)},
        'wall_seconds': row['seconds'], 'bazel_seconds': row['bazel_seconds'],
        'main_repository_mapping_seconds': mapping['dur'] / 1e6,
        'analysis_and_execution_seconds': combined['dur'] / 1e6,
        'skycache_before_command_seconds': before_command['dur'] / 1e6 if before_command else None,
        'time_to_first_recorded_genrule_seconds': (
            (first_action - combined['ts']) / 1e6 if first_action is not None else None),
        'recorded_genrule_envelope_seconds': (
            (last_action - first_action) / 1e6 if first_action is not None else None),
        'recorded_genrule_span_count': len(actions),
        'profile_end_seconds': max(event.get('ts', 0) + event.get('dur', 0)
                                   for event in events) / 1e6,
        'registry_download_span_count': sum(
            event.get('name', '').startswith('download file: https://bcr.bazel.build/')
            for event in events),
    }
    return result


def analyze(root):
    cases = [root] if (root / 'results.json').exists() else sorted(
        path.parent for path in root.glob('*/results.json'))
    if not cases:
        raise ValueError('no results.json files below comparison root')
    analyzed, missing, case_status = [], [], []
    expected = 0
    for case_root in cases:
        report = json.loads((case_root / 'results.json').read_text())
        case_status.append({'case': case_root.name, 'benchmark_passed': report.get('passed', False),
                            'benchmark_finished_utc': report.get('finished_utc')})
        automatic = defaultdict(list)
        for path in sorted(case_root.glob('out-*/command-*.profile.gz')):
            profile = load_profile(path)
            automatic[path.parent.name.removeprefix('out-')].append(profile)
        for row in report['runs']:
            expected += 1
            if row.get('profile'):
                # Prefer the colocated copy when inspecting a saved evidence archive.
                path = case_root / Path(row['profile']).name
                if not path.exists():
                    path = Path(row['profile'])
                candidates = [load_profile(path)] if path.exists() else []
                source = 'explicit-profile-path'
            else:
                candidates = [profile for profile in automatic[row['mode']]
                              if matches_time(profile, row)]
                source = 'automatic-profile'
            if not candidates:
                missing.append({'case': case_root.name, 'label': row['label'],
                                'reason': 'profile unavailable; may have been removed by retention'})
                continue
            if len(candidates) != 1:
                raise ValueError(row['label'] + ': ambiguous profile match')
            analyzed.append(inspect_profile(candidates[0], row, case_root.name, case_root, source))

    groups = defaultdict(list)
    for row in analyzed:
        groups[(row['case'], row['mode'], row['changed'])].append(row)
    summaries = []
    for (case, mode, changed), rows in sorted(groups.items()):
        summaries.append({'case': case, 'mode': mode, 'changed': changed, 'n': len(rows),
                          'medians': {metric: statistics.median(row[metric] for row in rows
                                      if row[metric] is not None)
                                      for metric in METRICS
                                      if any(row[metric] is not None for row in rows)}})
    return {'comparison_root': str(root), 'notes': NOTES, 'case_status': case_status,
            'expected_runs': expected, 'matched_profiles': len(analyzed),
            'complete_profile_coverage': not missing, 'missing': missing,
            'runs': analyzed, 'summary': summaries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('comparison_root', type=Path)
    parser.add_argument('--output', type=Path, help='new output file; existing files are never overwritten')
    args = parser.parse_args()
    result = analyze(args.comparison_root.resolve())
    encoded = json.dumps(result, indent=2) + '\n'
    if args.output:
        with args.output.open('x') as stream:
            stream.write(encoded)
    else:
        print(encoded, end='')


if __name__ == '__main__':
    main()
