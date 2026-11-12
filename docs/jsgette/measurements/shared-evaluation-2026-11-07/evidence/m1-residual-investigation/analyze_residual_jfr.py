"""Bounded historical JFR caller attribution. Never derives CPU seconds or reuse."""
import argparse
from collections import Counter
import hashlib
import io
import json
import math
from pathlib import Path
import re
import tarfile


EVENTS = ('jdk.ExecutionSample', 'jdk.NativeMethodSample')
MAX_JSON_BYTES = 128 * 1024**2
MAX_EVENTS = 10_000
MAX_DEPTH = 2048
BUILD = 'com/google/devtools/build/'
SERIAL = BUILD + 'lib/skyframe/serialization/'
LOCAL = SERIAL + 'local/'
VALIDATION_METHODS = {
    'isUnchanged', 'matchesAll', 'matches', 'matchesForAdmission',
    'validateAndInclude', 'includeAdmittedWitness', 'checkValidationInterrupted',
}
STORE_CLASSES = {
    'SegmentedFingerprintValueStore', 'PackedFingerprintValueStore',
    'LocalContentHasher', 'LocalWitnessTable', 'LocalDependencyEvidence',
}
STORE_METHODS = {
    'get', 'readForRestore', 'nextAnalysisCandidate', 'nextSharedAnalysisCandidate',
    'nextPrivateAnalysisCandidate', 'readPublication', 'readCandidate',
}
PROTOCOL_SHA = 'b4bf447302e4e4a7cf39f6b7940026f26694d4cc6927d808bdd8aa315e2c66fa'
TOOL_SHA = 'c313fd7830d5acd4ae73749bbbb48a5705e1c3bbb41957d4c3223eb636417d5a'
ARCHIVE_PINS = {
    'off': '58a49f20aed91d0576e3780d8f8170cdc1b12d71e9a1b169c20c12c3c7061b87',
    'on': 'f0fa344da92e7eeffaf596909f50a9d8bd9b6aee5e0c972e71add156d8202676',
}
RAW_PINS = {
    'off': '8805ee7fbdb2d6461927c82f10a4675ff755861fc1ac7498364544b633cdfa08',
    'on': '63baa6d9388d970a4126eba2de079e4b9ce680eac0a66edcc2fee030643ac55b',
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(data):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(
                          RuntimeError('Non-finite JSON value: ' + value)))


def local_path(value):
    require(isinstance(value, str), 'Input path must be a string')
    if value.startswith('/mnt/c/'):
        value = 'C:/' + value[len('/mnt/c/'):]
    require(not value.startswith(('/mnt/', '\\\\')), 'Runtime/UNC paths are not analysis inputs')
    return Path(value)


def read_pinned(record, maximum=MAX_JSON_BYTES, allow_empty=False):
    path = local_path(record['path'])
    require(path.is_file() and not path.is_symlink(), 'Missing/non-regular input: ' + str(path))
    size = path.stat().st_size
    require(type(record['bytes']) is int and (size > 0 or allow_empty)
            and size == record['bytes'] <= maximum,
            'Input size differs/exceeds bound: ' + str(path))
    data = path.read_bytes()
    require(len(data) == size and sha(data) == record['sha256'], 'Input digest differs: ' + str(path))
    return data


def frame_identity(frame):
    require(isinstance(frame, dict), 'Malformed stack frame')
    method = frame.get('method') or {}
    require(isinstance(method, dict), 'Malformed frame method')
    owner = (method.get('type') or {}).get('name') or ''
    name = method.get('name') or ''
    require(isinstance(owner, str) and isinstance(name, str), 'Malformed method identity')
    # JVM class names normally use slash notation; also accept dotted JFR names.
    if owner != '.' and not owner.startswith('/') and '.so' not in owner and '.dll' not in owner:
        owner = owner.replace('.', '/')
    return owner, name


def method_matches(name, methods):
    return name in methods or any(name.startswith('lambda$' + method + '$') for method in methods)


def decisive_category(owner, method):
    """Specific method/class owners; generic utilities are deliberately not decisive."""
    if owner.startswith(LOCAL + 'LocalDependencyValidator'):
        return 'cache_dependency_validation'
    if owner.startswith(LOCAL + 'LocalInputSnapshot') and method_matches(method, VALIDATION_METHODS):
        return 'cache_dependency_validation'
    simple = owner.rsplit('/', 1)[-1].split('$', 1)[0]
    if owner.startswith(LOCAL) and simple in STORE_CLASSES:
        return 'cache_store_evidence_hash_decode'
    if owner.startswith(LOCAL + 'LocalCacheSession') and method_matches(method, STORE_METHODS):
        return 'cache_store_evidence_hash_decode'
    if owner.startswith(SERIAL) and (
            'DeserializationContext' in simple or simple == 'SkyframeLookupContinuation'
            or method.startswith(('deserialize', 'safeCast'))):
        return 'cache_value_reconstruction'
    if owner.startswith(SERIAL + 'FingerprintValueService') and method == 'get':
        return 'cache_store_evidence_hash_decode'
    if owner.startswith(LOCAL + 'LocalCacheSession') and method_matches(method, {
            'finish', 'writeWitnesses', 'closeStores'}):
        return 'cache_publication_close'
    if owner.startswith(LOCAL + 'LocalAnalysisCacheClient'):
        return 'cache_lookup_bookkeeping'
    if owner.startswith(LOCAL + 'LocalInputSnapshot'):
        # Capture/registration/helpers alone do not establish validation ownership.
        return None
    # Generic JNI/VM stubs do not override a concrete Java caller. JIT/GC methods do.
    if 'libjvm' in owner or owner in ('.', ''):
        if any(token in method for token in ('CompileBroker::', 'C2Compiler::', 'C1Compiler::',
                                            'Compilation::', 'Compile::', 'CompilerThread::',
                                            'G1BarrierSetC2::', 'G1BarrierSetC1::')):
            return 'jvm_jit'
        if any(token in method for token in ('G1CollectedHeap::', 'G1YoungCollector::',
                                            'G1ConcurrentMark::', 'G1ConcurrentMarkThread::',
                                            'G1ParScanThreadState::', 'G1ServiceThread::',
                                            'ShenandoahConcurrentGC::', 'ShenandoahFullGC::',
                                            'PSScavenge::', 'ParallelScavengeHeap::', 'ZDriver::',
                                            'GC_Thread', 'GCThread::', 'GenCollectedHeap::')):
            return 'jvm_gc'
    return None


def classify(frames):
    """Nearest decisive caller wins. Broader Bazel frames are fallback only."""
    if not frames:
        return 'unknown_missing_stack', None, []
    identities = [frame_identity(frame) for frame in frames]
    matches = [(i, decisive_category(*identity)) for i, identity in enumerate(identities)]
    matches = [(i, category) for i, category in matches if category is not None]
    inclusive = sorted({category for _, category in matches})
    if matches:
        index, category = matches[0]
        return category, {'depth': index, 'owner': identities[index][0],
                          'method': identities[index][1]}, inclusive
    for index, (owner, method) in enumerate(identities):
        if owner.startswith(BUILD) or owner.startswith('net/starlark/'):
            category = 'other_cache_serialization' if owner.startswith(SERIAL) else 'other_bazel_starlark'
            return category, {'depth': index, 'owner': owner, 'method': method}, []
    if any('libjvm' in owner for owner, _ in identities):
        return 'jvm_runtime_unresolved', None, []
    return 'unknown_no_decisive_caller', None, []


def stack_of(event):
    require(isinstance(event, dict) and event.get('type') in EVENTS, 'Unexpected CPU event')
    values = event.get('values')
    require(isinstance(values, dict) and isinstance(values.get('startTime'), str),
            'Missing event metadata')
    stack = values.get('stackTrace')
    if stack is None:
        return [], False
    require(isinstance(stack, dict) and type(stack.get('truncated')) is bool,
            'Missing recorded truncation flag')
    frames = stack.get('frames')
    require(isinstance(frames, list) and len(frames) <= MAX_DEPTH, 'Invalid/deep stack')
    return frames, stack['truncated']


def without_frames(event):
    values = dict(event['values'])
    if values.get('stackTrace') is not None:
        values['stackTrace'] = {key: value for key, value in values['stackTrace'].items()
                                if key != 'frames'}
    return {'type': event['type'], 'values': values}


def summary_counts(summary):
    found = re.findall(r'^\s*(jdk\.(?:ExecutionSample|NativeMethodSample))\s+(\d+)\s+\d+\s*$',
                       summary, re.M)
    require(found and len({name for name, _ in found}) == len(found),
            'Missing/duplicate CPU event summary')
    return {name: int(count) for name, count in found}


def analyze_events(decoded, original, summary):
    events = decoded['recording']['events']
    old_events = original['recording']['events']
    require(isinstance(events, list) and isinstance(old_events, list)
            and 0 < len(events) == len(old_events) <= MAX_EVENTS, 'Event count differs/exceeds bound')
    counts, categories, original_categories, inclusive = Counter(), Counter(), Counter(), Counter()
    depths, old_depths, evidence, transitions = Counter(), Counter(), Counter(), Counter()
    truncated_categories = Counter()
    missing = truncated = at_limit = added_frames = 0
    for event, old in zip(events, old_events):
        frames, is_truncated = stack_of(event)
        old_frames, old_truncated = stack_of(old)
        require(len(old_frames) <= 5, 'Original decode is not the archived max-five view')
        require(without_frames(event) == without_frames(old), 'Original/deep event metadata differs')
        require(old_truncated == is_truncated and frames[:len(old_frames)] == old_frames
                and len(frames) >= len(old_frames), 'Original/deep frame prefix differs')
        counts[event['type']] += 1
        depths[len(frames)] += 1
        old_depths[len(old_frames)] += 1
        missing += not bool(frames)
        truncated += is_truncated
        at_limit += len(frames) == MAX_DEPTH
        added_frames += len(frames) > len(old_frames)
        category, caller, present = classify(frames)
        old_category, _, _ = classify(old_frames)
        categories[category] += 1
        original_categories[old_category] += 1
        transitions[(old_category, category)] += 1
        inclusive.update(present)  # Once per sample/category; non-additive secondary view.
        if is_truncated:
            truncated_categories[category] += 1
        if caller:
            evidence[(category, caller['owner'], caller['method'], caller['depth'])] += 1
    reported = summary_counts(summary)
    require(all(counts[name] == reported.get(name, 0) for name in EVENTS)
            and sum(reported.values()) == len(events), 'Decoded counts differ from JFR summary')
    require(sum(categories.values()) == len(events), 'Exclusive categories do not reconcile')
    return {
        'samples': len(events), 'event_counts': dict(counts), 'summary_event_counts': reported,
        'first_sample_utc': min(event['values']['startTime'] for event in events),
        'last_sample_utc': max(event['values']['startTime'] for event in events),
        'missing_stack_samples': missing, 'recorded_truncated_stack_samples': truncated,
        'at_decode_depth_limit_samples': at_limit, 'samples_with_more_than_original_frames': added_frames,
        'decoded_frame_depths': dict(sorted(depths.items())),
        'original_frame_depths': dict(sorted(old_depths.items())),
        'disjoint_categories': dict(sorted(categories.items())),
        'original_max5_same_classifier': dict(sorted(original_categories.items())),
        'recorded_truncated_by_category': dict(sorted(truncated_categories.items())),
        'inclusive_categories_nonadditive': dict(sorted(inclusive.items())),
        'category_transitions': [{'original': old, 'deep': new, 'samples': count}
                                 for (old, new), count in sorted(transitions.items())],
        'decisive_caller_evidence': [
            {'category': category, 'owner': owner, 'method': method, 'depth': depth, 'samples': count}
            for (category, owner, method, depth), count in evidence.most_common(100)],
        'cpu_seconds_derived_from_samples': None, 'avoided_cpu_seconds': None,
        'memory_savings': None, 'semantic_equivalence': 'unproven',
        'original_event_metadata_and_prefixes_verified': True,
    }


def archive_members(archive_record, metadata_record, wanted):
    """Read only explicit archived members after verifying the bounded complete inventory."""
    raw = read_pinned(archive_record, 128 * 1024**2)
    meta = strict_json(read_pinned(metadata_record, 1024**2))
    require(meta['archive_sha256'] == sha(raw), 'Archive metadata digest differs')
    inventory = {row['path']: row for row in meta['members']}
    require(len(inventory) == len(meta['members']) <= 300
            and sum(row['bytes'] for row in inventory.values()) <= 128 * 1024**2,
            'Archive inventory exceeds bound/duplicates')
    result, seen = {}, set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        for member in archive:
            require(member.isfile() and member.name not in seen and member.name in inventory,
                    'Archive member type/name differs')
            seen.add(member.name)
            row = inventory[member.name]
            require(member.size == row['bytes'] <= 32 * 1024**2, 'Archive member size differs')
            contents = archive.extractfile(member).read()
            require(sha(contents) == row['sha256'], 'Archive member digest differs')
            if member.name in wanted:
                result[member.name] = contents
    require(seen == set(inventory) and set(result) == set(wanted), 'Missing archive member')
    require(sha(read_pinned(archive_record, 128 * 1024**2)) == meta['archive_sha256'],
            'Archive changed during analysis')
    return result


def check_command(row, tool, output_root):
    command = row['command']
    argv = command['argv']
    require(isinstance(argv, list) and len(argv) == 11, 'Decoder argv length differs')
    require(argv[:-1] == [tool['path'], '-J-Xmx512m', '-J-XX:ActiveProcessorCount=2',
                         '-J-Djava.io.tmpdir=/mnt/shared-evaluation-20261107/m1-residual-decoder-01',
                         'print', '--json', '--stack-depth', '2048', '--events', ','.join(EVENTS)],
            'Decoder argv differs from frozen command')
    require(type(command['exit_code']) is int and command['exit_code'] == 0,
            'Decoder did not complete successfully')
    wall = command['wall_seconds']
    require(type(wall) in (float, int) and math.isfinite(wall) and 0 <= wall <= 600,
            'Decoder elapsed bound invalid')
    require(local_path(argv[-1]).resolve() == local_path(row['raw']['path']).resolve(),
            'Decoder input does not match extracted raw proof')
    for name in ('raw', 'decoded', 'stderr'):
        path = local_path(row[name]['path']).resolve()
        require(path.is_relative_to(output_root) and path != output_root,
                'Decoder artifact outside fresh output directory')


def analyze_manifest(manifest):
    require(manifest['schema_version'] == 1 and manifest['kind'] == 'm1-residual-jfr-analysis-v1',
            'Wrong analysis manifest')
    protocol = read_pinned(manifest['protocol'], 64 * 1024)
    require(sha(protocol) == PROTOCOL_SHA, 'Frozen protocol differs')
    addenda = manifest.get('addenda', [])
    require(isinstance(addenda, list) and len(addenda) <= 1, 'Unexpected protocol addenda')
    addendum_hashes = [sha(read_pinned(record, 64 * 1024)) for record in addenda]
    report_bytes = read_pinned(manifest['decode_report'], 1024**2)
    report = strict_json(report_bytes)
    require(report['schema_version'] == 1 and report['kind'] == 'archived-m0-restart-fullstack-decode'
            and report['passed'] is True and report['diagnostic_only'] is True
            and report['milestone_verdict'] is None and report['protocol_sha256'] == PROTOCOL_SHA,
            'Decode report is not successful frozen diagnostic')
    require(report['tool']['sha256'] == TOOL_SHA, 'Decoder tool differs')
    require(type(report['wall_seconds']) in (float, int) and math.isfinite(report['wall_seconds'])
            and 0 <= report['wall_seconds'] <= 600, 'Whole decode exceeded frozen time bound')
    helper_hashes = {}
    for name, key in (('decode_residual_jfr.py', 'decoder_sha256'),
                      ('analyze_m0_diagnostics.py', 'archive_validator_sha256')):
        helper_hashes[name] = sha(Path(__file__).with_name(name).read_bytes())
        require(helper_hashes[name] == report[key], 'Decoder helper source differs: ' + name)
    rows = report['rows']
    arms = manifest['arms']
    require(isinstance(rows, list) and isinstance(arms, list) and len(rows) == len(arms) == 2
            and {row['arm'] for row in rows} == {arm['arm'] for arm in arms} == {'off', 'on'},
            'Exactly the two frozen restart recordings are required')
    output_root = local_path(manifest['decode_report']['path']).resolve().parent
    results = []
    for arm in arms:
        name = arm['arm']
        row = next(row for row in rows if row['arm'] == name)
        require(arm['archive']['sha256'] == row['archive_sha256'] == ARCHIVE_PINS[name]
                and arm['raw_sha256'] == row['raw_sha256'] == RAW_PINS[name]
                and local_path(arm['archive']['path']).resolve() == local_path(row['archive_path']).resolve(),
                'Historical archive/raw binding differs')
        require(arm['raw_member'] == row['raw_member'] == 'restart/cpu.jfr'
                and arm['original_decoded_member'] == row['original_decoded_member']
                    == 'restart-jfr-decoded/stdout.log'
                and arm['summary_member'] == row['summary_member'] == 'restart-jfr-summary/stdout.log',
                'Historical arm/window member differs')
        wanted = [arm['raw_member'], arm['original_decoded_member'], arm['summary_member'],
                  'report.json', 'restart-jfr-decoded/command.json']
        data = archive_members(arm['archive'], arm['archive_metadata'], wanted)
        require(sha(data[arm['raw_member']]) == RAW_PINS[name]
                and sha(data['report.json']) == row['historical_report_sha256'],
                'Historical member/report identity differs')
        historical = strict_json(data['report.json'])
        original_command = strict_json(data['restart-jfr-decoded/command.json'])
        require(row['original_command'] == original_command
                and historical['decoder']['tool_sha256'] == TOOL_SHA
                and historical['decoder']['tool'] == report['tool']['path'],
                'Original decoder provenance differs')
        check_command(row, report['tool'], output_root)
        raw = read_pinned(row['raw'], 1024**2)
        require(raw == data[arm['raw_member']], 'Extracted raw bytes differ from archive')
        decoded_bytes = read_pinned(row['decoded'])
        stderr = read_pinned(row['stderr'], 1024**2, allow_empty=True)
        result = analyze_events(strict_json(decoded_bytes),
                                strict_json(data[arm['original_decoded_member']]),
                                data[arm['summary_member']].decode('utf-8'))
        result.update(arm=name, lifecycle='restart', historical_binary_sha256=historical['binary_sha256'],
                      source_archive_sha256=ARCHIVE_PINS[name], raw_jfr_sha256=RAW_PINS[name],
                      decoded_sha256=sha(decoded_bytes), stderr_sha256=sha(stderr),
                      stderr_bytes=len(stderr), original_decoder_command=original_command,
                      decoder_command=row['command'], original_summary_sha256=sha(data[arm['summary_member']]))
        results.append(result)
    require(sum(row['command']['wall_seconds'] for row in rows) <= 600, 'Cumulative decode time exceeded')
    # Recheck all external bindings after analysis; output is only created after these pass.
    for record, bound in ((manifest['protocol'], 64 * 1024), (manifest['decode_report'], 1024**2)):
        read_pinned(record, bound)
    for record in addenda:
        read_pinned(record, 64 * 1024)
    for row in rows:
        read_pinned(row['decoded'])
        read_pinned(row['raw'], 1024**2)
        read_pinned(row['stderr'], 1024**2, allow_empty=True)
    return {
        'schema_version': 1, 'kind': 'historical-m0-restart-residual-caller-audit',
        'protocol_sha256': PROTOCOL_SHA, 'decode_report_sha256': sha(report_bytes),
        'administrative_addenda_sha256': addendum_hashes,
        'analyzer_sha256': sha(Path(__file__).read_bytes()), 'decode_helper_sha256': helper_hashes,
        'diagnostic_only': True, 'performance_eligible': False, 'milestone_verdict': None,
        'classification_rule': 'Nearest explicit method/class caller from leaf toward root; '
                               'generic utility/Bazel/native frames cannot mask a specific caller. '
                               'Thread names never classify work. Inclusive matches are a separate view.',
        'rows': results,
        'limitations': [
            'Historical binary and command-profiler window; no current N1 cost denominator.',
            'Counts are sample frequencies, never CPU seconds, avoided cost or removable work.',
            'Recorded truncation and decode-depth saturation are preserved; absent callers are unknown.',
            'The max-five comparison applies the same caller classifier, not the older leaf-only taxonomy.',
            'No memory ownership, peak-PSS savings, cross-snapshot equivalence or replacement-cost proof.',
            'Observed native/runtime frames cannot prove all JIT or GC work was observed.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), 'Refusing to replace existing analysis')
    require(args.manifest.stat().st_size <= 1024**2, 'Manifest exceeds bound')
    manifest_bytes = args.manifest.read_bytes()
    result = analyze_manifest(strict_json(manifest_bytes))
    require(args.manifest.read_bytes() == manifest_bytes, 'Manifest changed during analysis')
    result['manifest_sha256'] = sha(manifest_bytes)
    with args.output.open('x', encoding='utf-8', newline='\n') as out:
        json.dump(result, out, indent=2, sort_keys=True, allow_nan=False)
        out.write('\n')
    print(json.dumps({'output': str(args.output.resolve()), 'rows': len(result['rows']),
                      'diagnostic_only': True, 'milestone_verdict': None}))


if __name__ == '__main__':
    main()
