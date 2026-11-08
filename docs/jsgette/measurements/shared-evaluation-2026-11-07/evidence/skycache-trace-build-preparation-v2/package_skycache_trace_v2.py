#!/usr/bin/env python3
"""Package the frozen C-only trace source after the coordinator ends timed work.

Run once on Windows. Stages only the nine reviewed files in the isolated source
index; creates no commit, changes no source bytes, and never reads runtime R:.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

import build_skycache_trace_v2 as build

WORK = Path(__file__).resolve().parent
SOURCE = WORK / 'skycache-trace-source/source'
PROVENANCE = WORK / 'skycache-trace-source/provenance'
OUTPUT = WORK / 'trace-build-preparation/bundle-v2'
BASE, BASELINE, SAFE_PATCH = build.BASE, build.BASELINE_TREE, build.SAFE_PATCH
BASE_MEMBERS = 18588
PROVENANCE_PINS = {
    'reconstruction.json': 'cca1e0b73f7c38fa4c4061d01536cef677802db7a4d1beb9d78bff3b3a15d9c0',
    'source-members.json': '99d6c80a6e9826ecfd92590e9d364206bfeabfbd5b59371a696f70d092bec578',
    'safe-subset.patch': SAFE_PATCH,
    'base-source.tar': '2e9e828e002882d63383bec4533b28882f5e5be2902ee0275e4fe6687a5a8acf',
}
MAIN = 'src/main/java/com/google/devtools/build/lib/'
TEST = 'src/test/java/com/google/devtools/build/lib/skyframe/'
PREVIOUS_TREE = '26ad73611f1d5b7e51094e40b07d1290e9a134ae'
PREVIOUS_TRACE_PINS = {
    MAIN + 'skyframe/M1TraceCollector.java': '976a13db56fa51b6f118319d3e34c20e1954f43e4b35b8a1a8a95c56556b7d42',
    MAIN + 'skyframe/M1TraceModule.java': 'a36b7070a579bdb34dd3e4c98c4a3d893244e36c18463a4bf7169102deccbeb5',
    MAIN + 'skyframe/SkyframeExecutor.java': '16f4d96f4878e9cb42dada5c23cd7e62b00cd9b5b306aba33153251c27e0cb62',
    MAIN + 'skyframe/BUILD': '4e0a27c97c389f323db909088cdb8f6b63008d4dc5bd01532cff8176c4909d32',
    MAIN + 'bazel/Bazel.java': '0bc8971b6dc5383caf90f808174505b0bd3b397d689aad1c99f04e2ab7419ee3',
    MAIN + 'bazel/BUILD': '92df933611df7d8bf667ca72f3904f8047c501d32d3525dc1e61a0698fd000cf',
    TEST + 'M1TraceCollectorTest.java': '89edea598b187add783e3638536144dae73f012f19048aa765693a7373f6eca6',
    TEST + 'M1TraceModuleTest.java': 'c833fda8b54775df74eb6f20ccd3cab12ea092d2c274fc50d647172676b1b9a5',
    TEST + 'BUILD': '4cd60586e1df92465069830957abbdeaab81e1ec3fbf70269f183bd194e79292',
}
TRACE_PINS = dict(PREVIOUS_TRACE_PINS)
TRACE_PINS.update({
    MAIN + 'skyframe/M1TraceCollector.java': 'f14ba9bbcd0e5b8d09255e8b8ca9399dad75e87b72ab85b5a2ddaeee8357064e',
    TEST + 'M1TraceCollectorTest.java': '09fc04fc5fa7bedd08a5b07fd3ecb31ae1243ee4f735697a768d4b4e1693e3c2',
})

require, sha = build.require, build.sha


def write_new(path, data):
    with path.open('xb') as stream:
        stream.write(data)


def document(path, value):
    write_new(path, (json.dumps(value, indent=2) + '\n').encode())


def linux_path(path):
    path = path.resolve()
    require(path.drive.lower() == 'c:', 'Bundle must remain on C:')
    return '/mnt/c/' + '/'.join(path.parts[1:])


def git(source, *arguments, output=None):
    command = ['git', '-c', 'core.autocrlf=false', '-c', 'core.safecrlf=true',
               '-C', str(source), *arguments]
    if output is None:
        result = subprocess.run(command, capture_output=True, check=True)
        return result.stdout
    with output.open('xb') as stream:
        subprocess.run(command, stdout=stream, stderr=subprocess.PIPE, check=True)


def names(raw):
    return {name.decode('utf-8') for name in raw.split(b'\0') if name}


def tree_members(source, tree):
    result = {}
    for item in git(source, 'ls-tree', '-rz', tree).split(b'\0'):
        if not item:
            continue
        metadata, raw_name = item.split(b'\t', 1)
        mode, kind, blob = metadata.decode().split()
        name = raw_name.decode('utf-8')
        path = PurePosixPath(name)
        require(mode in ('100644', '100755') and kind == 'blob' and name not in result
                and not path.is_absolute() and str(path) == name and '..' not in path.parts
                and '\\' not in name, 'Unsupported/duplicate tree member')
        result[name] = {'path': name, 'mode': mode, 'git_blob': blob}
    return result


def verify_file(path, expected):
    require(path.resolve() == path and path.is_file() and not path.is_symlink(), 'Source alias/nonfile')
    require(sha(path) == expected, 'Source content differs: ' + str(path))


def verify_baseline():
    require(SOURCE.resolve() == SOURCE and (SOURCE / '.git').is_dir(), 'Expected isolated Git source')
    require(git(SOURCE, 'rev-parse', 'HEAD').decode().strip() == BASE, 'Source HEAD changed')
    require(git(SOURCE, 'write-tree').decode().strip() == PREVIOUS_TREE, 'Initial source index changed')
    require(set(TRACE_PINS) == set(PREVIOUS_TRACE_PINS), 'Reviewed instrumentation file set changed')
    for name, expected in PREVIOUS_TRACE_PINS.items():
        require(hashlib.sha256(git(SOURCE, 'show', PREVIOUS_TREE + ':' + name)).hexdigest() == expected,
                'Previous instrumented source pin differs: ' + name)
    for name, expected in PROVENANCE_PINS.items():
        verify_file(PROVENANCE / name, expected)
    reconstruction = json.loads((PROVENANCE / 'reconstruction.json').read_bytes())
    require(reconstruction['base_commit'] == BASE and reconstruction['patched_git_tree'] == BASELINE
            and reconstruction['patched_members_verified'] == BASE_MEMBERS
            and reconstruction['patch']['sha256'] == SAFE_PATCH,
            'Baseline reconstruction identity differs')
    require(names(git(SOURCE, 'diff', '--cached', '--name-only', '-z', BASE)) ==
            set(reconstruction['changed_paths']) | set(PREVIOUS_TRACE_PINS), 'Previous staged path set differs')
    rows = json.loads((PROVENANCE / 'source-members.json').read_bytes())
    baseline = tree_members(SOURCE, BASELINE)
    require(len(rows) == len(baseline) == BASE_MEMBERS and len({row['path'] for row in rows}) == len(rows),
            'Baseline source member count differs')
    for row in rows:
        require(baseline.get(row['path']) == {key: row[key] for key in ('path', 'mode', 'git_blob')},
                'Baseline manifest/tree differs')
    changed = names(git(SOURCE, 'diff', '--name-only', '-z'))
    changed |= names(git(SOURCE, 'ls-files', '--others', '-z'))  # Includes ignored files.
    expected_changes = {name for name in TRACE_PINS if TRACE_PINS[name] != PREVIOUS_TRACE_PINS[name]}
    require(expected_changes and changed == expected_changes, 'Unexpected or missing instrumentation repair paths')
    for name, expected in TRACE_PINS.items():
        verify_file(SOURCE / name, expected)
    return {row['path']: row for row in rows}


def verify_export(archive, expected, baseline):
    """Hash actual Git archive contents and filesystem bytes, with no extraction."""
    rows, seen = [], set()
    with tarfile.open(archive, 'r:') as stream:
        for member in stream:
            if member.isdir():
                continue
            require(member.isfile() and member.name in expected and member.name not in seen,
                    'Unexpected/duplicate/nonregular export member')
            seen.add(member.name)
            row = dict(expected[member.name])
            require(bool(member.mode & 0o111) == (row['mode'] == '100755'), 'Export Git mode differs')
            content_hash = hashlib.sha256()
            blob_hash = hashlib.sha1(b'blob ' + str(member.size).encode() + b'\0')
            with stream.extractfile(member) as reader:
                for block in iter(lambda: reader.read(1024 * 1024), b''):
                    content_hash.update(block)
                    blob_hash.update(block)
            require(blob_hash.hexdigest() == row['git_blob'], 'Export Git blob differs')
            row.update(bytes=member.size, sha256=content_hash.hexdigest())
            if member.name in TRACE_PINS:
                require(row['sha256'] == TRACE_PINS[member.name], 'Instrumentation changed during export')
            else:
                require(row == baseline.get(member.name), 'Undeclared change from baseline')
            verify_file(SOURCE / member.name, row['sha256'])
            rows.append(row)
    require(seen == set(expected), 'Git archive omitted tree files')
    rows.sort(key=lambda row: row['path'])
    build.verify_archive_members(archive, rows)
    return rows


def artifact(path):
    return {'path': linux_path(path), 'bytes': path.stat().st_size, 'sha256': sha(path)}


def package():
    require(OUTPUT.resolve() == OUTPUT and OUTPUT.is_relative_to(WORK)
            and not OUTPUT.exists() and not OUTPUT.is_symlink(), 'Fresh owned bundle directory required')
    baseline = verify_baseline()
    OUTPUT.mkdir()  # The existing preparation parent is coordinator-owned.
    git(SOURCE, 'add', '-f', '--', *sorted(TRACE_PINS))
    tree = git(SOURCE, 'write-tree').decode().strip()
    require(tree != BASELINE and names(git(SOURCE, 'diff', '--name-only', '-z', BASELINE, tree)) ==
            set(TRACE_PINS), 'Staged instrumentation path set differs')
    members = tree_members(SOURCE, tree)
    archive, patch = OUTPUT / 'source.tar', OUTPUT / 'instrumentation.patch'
    git(SOURCE, 'diff', '--binary', '--full-index', '--no-ext-diff', '--no-textconv',
        '--no-renames', '--no-color', BASELINE, tree, '--', *sorted(TRACE_PINS), output=patch)
    git(SOURCE, 'diff', '--binary', '--full-index', '--no-ext-diff', '--no-textconv',
        '--no-renames', '--no-color', BASE, tree, output=OUTPUT / 'combined.patch')
    git(SOURCE, 'archive', '--format=tar', tree, output=archive)
    rows = verify_export(archive, members, baseline)
    document(OUTPUT / 'source-members.json', rows)
    require(not git(SOURCE, 'diff', '--name-only', '-z') and
            not git(SOURCE, 'ls-files', '--others', '-z') and
            git(SOURCE, 'rev-parse', 'HEAD').decode().strip() == BASE and
            git(SOURCE, 'write-tree').decode().strip() == tree, 'Source changed while packaging')
    for name, expected in PROVENANCE_PINS.items():
        verify_file(PROVENANCE / name, expected)
    bundle = {'schema_version': 1, 'base_commit': BASE, 'baseline_git_tree': BASELINE,
              'safe_subset_patch_sha256': SAFE_PATCH, 'instrumented_git_tree': tree,
              'previous_instrumented_git_tree': PREVIOUS_TREE,
              'previous_trace_source_sha256': dict(PREVIOUS_TRACE_PINS),
              'repair_paths': sorted(name for name in TRACE_PINS if TRACE_PINS[name] != PREVIOUS_TRACE_PINS[name]),
              'tests': list(build.TESTS), 'source_archive': artifact(archive),
              'source_members': artifact(OUTPUT / 'source-members.json'),
              'instrumentation_patch': artifact(patch),
              'combined_patch': artifact(OUTPUT / 'combined.patch'),
              'trace_source_sha256': dict(TRACE_PINS), 'baseline_provenance_sha256': dict(PROVENANCE_PINS),
              'packager_sha256': sha(Path(__file__).resolve()),
              'source_root': linux_path(SOURCE), 'head_remains_base_commit': True,
              'source_bytes_verified_against_export': True, 'baseline_provenance_unchanged': True,
              'source_member_count': len(rows), 'build_executed': False}
    document(OUTPUT / 'bundle.json', bundle)
    print(json.dumps({'bundle': linux_path(OUTPUT / 'bundle.json'),
                      'sha256': sha(OUTPUT / 'bundle.json'), 'instrumented_git_tree': tree}))
    return bundle


if __name__ == '__main__':
    package()
