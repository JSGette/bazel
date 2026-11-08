#!/usr/bin/env python3
"""Capture only the completed formatter's twenty named runtime evidence files."""
import difflib
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

WORK = Path('/mnt/c/Users/jgett/Documents/Codex/2026-11-07/i-ha/work')
ROOT = Path('/mnt/shared-evaluation-20261107/m1-trace-format-v2')
E = WORK / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/evidence/auxiliary/m1-trace-format-02'
SOURCE = WORK / 'skycache-trace-source/source'
CONTROL = ('command.json', 'control.json', 'host-budget-preflight.json', 'launcher.log', 'task-reset.json',
           'control-tools/host_memory_guard.psm1', 'control-tools/reset_task_mount.py', 'control-tools/run_guarded.ps1',
           'guard/host-memory.jsonl', 'guard/policy.json', 'guard/result.json', 'guard/scope-identity.json',
           'guard/stderr.log', 'guard/stdout.log')
MAX_FILE = 4 * 1024**2


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_FILE, 'Invalid bounded file: ' + str(path))
    raw = path.read_bytes()
    require(len(raw) <= MAX_FILE, 'File grew beyond limit: ' + str(path))
    return raw


def write_new(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)


def java_tokens(raw):
    # A diagnostic lexical comparison, not a compiler or a semantic-equivalence proof.
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/|\w+|[^\s]', raw.decode('utf-8'), re.S)
    tokens = [token for token in tokens if not token.startswith(('//', '/*'))]
    code, imports, index = [], [], 0
    while index < len(tokens):
        if tokens[index] == 'import':
            end = tokens.index(';', index)
            imports.append(''.join(tokens[index + 1:end]))
            index = end + 1
        else:
            code.append(tokens[index])
            index += 1
    return code, imports


def fold_plain_strings(tokens):
    # Only unescaped literal content; used for GJF's observed @Option help reflow.
    result = []
    for token in tokens:
        if len(result) >= 2 and result[-1] == '+' and token.startswith('"') and result[-2].startswith('"') and '\\' not in token + result[-2]:
            previous = result.pop(-2)
            result.pop()
            result.append(previous[:-1] + token[1:])
        else:
            result.append(token)
    return result


def main():
    require(ROOT.resolve() == ROOT, 'Runtime root alias')
    require(all(not (E / name).exists() for name in ('runtime.tar.gz', 'runtime.tar.gz.json', 'report.json')), 'Archive output already exists')
    controls = {name: read(E / name) for name in CONTROL}
    guard = json.loads(controls['guard/result.json'])
    identity = json.loads(controls['guard/scope-identity.json'])
    policy = json.loads(controls['guard/policy.json'])
    require(guard['status'] == 'completed' and guard['exit_code'] == 0 and guard['command_exit_code'] == 0, 'Formatter guard failed')
    require(guard['final_scope_cleanup'] == {'present': False, 'signalled': False, 'verified': True}, 'Missing clean scope absence')
    require(re.fullmatch(r'shared-eval-guard-m1-trace-format-02-[0-9a-f]{32}\.scope', identity['Id'])
            and identity['Id'] == guard['scope']['unit'] and identity['InvocationID'] == guard['scope']['invocation_id'], 'Guard identity mismatch')
    group = identity['ControlGroup']
    require(group.startswith('/') and '..' not in Path(group).parts and group.endswith('/' + identity['Id']), 'Unexpected cgroup name')
    require(not (Path('/sys/fs/cgroup') / group.lstrip('/')).exists(), 'Formatter scope still present')
    require(policy['command_sha256'] == digest(controls['command.json']) and json.loads(controls['command.json']) ==
            ['python3', '-B', str(WORK / 'format_skycache_trace_v2.py')], 'Executed formatter command differs')
    inputs = E / 'formatter-inputs'
    input_manifest = json.loads(read(inputs / 'manifest.json'))
    for row in input_manifest['files']:
        raw = read(inputs / row['path'])
        require(len(raw) == row['bytes'] and digest(raw) == row['sha256'], 'Preserved formatter input differs')
    manifest_raw = read(inputs / 'trace-format-manifest-v2.json')
    manifest = json.loads(manifest_raw)
    files = list(manifest['files'])
    require(len(files) == 7 and sum(name.endswith('.java') for name in files) == 4 and all('..' not in Path(name).parts for name in files), 'Unexpected format file set')
    names = ['report.json', 'formatting.diff', 'java-format.log', 'java-check.log', 'buildifier-fix.log', 'buildifier-check.log']
    names += [folder + '/' + name for folder in ('before', 'formatted') for name in files]
    payloads = {name: read(ROOT / name) for name in names}
    require(sum(map(len, payloads.values())) <= 16 * 1024**2, 'Archive exceeds aggregate bound')
    report = json.loads(payloads['report.json'])
    require(payloads['report.json'] == read(WORK / 'trace-format-receipt-v2.json'), 'C receipt differs from runtime report')
    require(report['status'] == 'formatted_and_verified' and report['scope_before']['name'] == group, 'Wrong formatter status/scope')
    require(report['manifest'] == manifest and report['manifest_sha256'] == digest(manifest_raw)
            and report['helper_sha256'] == digest(read(inputs / 'format_skycache_trace_v2.py')), 'Formatter input identity differs')
    commands = report['commands']
    require([row['name'] for row in commands] == ['java-format', 'java-check', 'buildifier-fix', 'buildifier-check']
            and all(row['returncode'] == 0 for row in commands), 'Missing passing format/check commands')
    before, after = report['source_before_sha256'], report['source_after_sha256']
    require(len(before) == 9 and before.keys() == after.keys() and all(before[name] == old for name, old in manifest['files'].items()), 'Source map differs')
    require(all(digest(read(SOURCE / name)) == expected for name, expected in after.items()), 'Current source differs from formatter receipt')
    require(all(before[name] == after[name] for name in before if name not in files), 'Existing integration Java changed')
    comparisons, differences, build_counts = {}, [], {}
    for name in files:
        old, new = payloads['before/' + name], payloads['formatted/' + name]
        require(digest(old) == before[name] and digest(new) == after[name], 'Captured source hash differs: ' + name)
        lines = list(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True), fromfile='before/' + name, tofile='formatted/' + name))
        differences.extend(lines)
        if name.endswith('.java'):
            old_code, old_imports = java_tokens(old)
            new_code, new_imports = java_tokens(new)
            comparisons[name] = {'non_import_tokens_equal': old_code == new_code,
                'plain_string_literal_fold_tokens_equal': fold_plain_strings(old_code) == fold_plain_strings(new_code),
                'removed_imports': sorted(set(old_imports) - set(new_imports)),
                'added_imports': sorted(set(new_imports) - set(old_imports))}
            require(fold_plain_strings(old_code) == fold_plain_strings(new_code), 'Java tokens differ beyond plain string reflow: ' + name)
        else:
            build_counts[name] = sum(line.startswith(('+', '-')) and not line.startswith(('+++', '---')) for line in lines)
    require(''.join(differences).encode() == payloads['formatting.diff'], 'Formatting diff does not match captured bytes')
    require(build_counts == report['build_changed_lines'] and max(build_counts.values()) <= 200 and sum(build_counts.values()) <= 500, 'BUILD churn differs/exceeds budget')
    archive = E / 'runtime.tar.gz'
    with archive.open('xb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as zipped, tarfile.open(fileobj=zipped, mode='w') as tar:
        for name, data in payloads.items():
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(data), 0o644, 0
            tar.addfile(entry, io.BytesIO(data))
    with tarfile.open(archive, 'r:gz') as tar:
        require([member.name for member in tar.getmembers()] == names, 'Archive membership differs')
        require(all(tar.extractfile(name).read() == data for name, data in payloads.items()), 'Archive bytes differ')
    require(all(read(E / name) == raw for name, raw in controls.items()), 'Control evidence changed during capture')
    write_new(E / 'report.json', payloads['report.json'])
    result = {'schema_version': 1, 'verified': True, 'scope': 'Twenty exact formatter runtime files; excludes jars, binaries and runtime caches',
        'archive_sha256': digest(read(archive)), 'archive_bytes': archive.stat().st_size,
        'members': [{'path': name, 'bytes': len(data), 'sha256': digest(data)} for name, data in payloads.items()],
        'control_evidence': [{'path': name, 'bytes': len(data), 'sha256': digest(data)} for name, data in controls.items()],
        'formatter_input_manifest_sha256': digest(read(inputs / 'manifest.json')), 'report_sha256': digest(payloads['report.json']),
        'archive_helper_sha256': digest(read(Path(__file__))), 'source_after_matches': True, 'final_scope_absent_unsignalled': True,
        'java_lexical_diagnostic': comparisons, 'build_changed_lines': build_counts}
    write_new(E / 'runtime.tar.gz.json', (json.dumps(result, indent=2) + '\n').encode())
    print(json.dumps({'verified': True, 'archive_sha256': result['archive_sha256'], 'members': len(names), 'java_lexical_diagnostic': comparisons}, indent=2))


if __name__ == '__main__':
    main()
