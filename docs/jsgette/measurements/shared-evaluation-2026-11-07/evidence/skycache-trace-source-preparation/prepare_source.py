"""Reconstruct pinned SkyCache source on C only; no compilation or runtime access."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE, PROVENANCE = ROOT / 'source', ROOT / 'provenance'
GIT = Path('C:/Users/jgett/Documents/Codex/2026-10-31/start-contemplating-a-new-design-based/bazel-source/.git')
PATCH = Path('C:/Users/jgett/Documents/Codex/2026-10-29/loa/outputs/skycache-tiered-audit-safe-subset-2026-10-29.patch')
MANIFEST = Path('C:/Users/jgett/Documents/Codex/2026-10-29/loa/work/safe-subset-validation-summary.json')
NOTES = ROOT.parent / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/m0-cross-worktree-reuse-notes.md'
BASE = 'a23ae68903f03f08ce83dcf1be85cdff54bb1eeb'
BASE_TREE = '8cd781b2cb31b40d07684d5c321976e0e94e70f4'
PATCH_SHA = '238cac7dffac4ca6a89ca144d59643ecb60e465d0dbeab0b21da0c1a2a56eb7e'
MANIFEST_SHA = 'cf159cdf56c464f32e00aff205318bba6893190436c599d9b65c969711d9e640'
AUDITED = {
    'LocalAnalysisCacheClient.java': '1e1954e0477d38f00b7f460232d2fbccd8a79a236567beb9c949dfbe1317fdd4',
    'LocalCacheSession.java': 'f4a6ae49f2715e0b0b5aaf358d2c7f2f6bdc61e800dd9a73d5420b0937a6c777',
    'LocalInputSnapshot.java': '62b22894ffaa37db290f42f22c27693e4f829816a4ea33998770ebf9c356d8b5',
    'LocalSkycacheModule.java': 'b8c3fdedbd6d75c79005b2be04dfba4e8c0cfc1cb0656459727aec148e0a806f',
    'LocalPathCodec.java': 'b1abd484fd1c4f3abac4b979999d3a2afbabeaf8067908947368d1f23c186a84',
    'LocalSkycacheOptions.java': 'e55900cca06383733f672d37c6e8262cb7765fb0634af0a79ac4366d797c8069',
    'LocalWitnessTable.java': 'bedf45b0b1c2448e2cd4625fdf77fbe5f732d6aed2605cc75219b4ce0377f95d',
}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_new(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)


def git(*args, original=False):
    command = ['git', '--git-dir=' + str(GIT)] if original else ['git', '-C', str(SOURCE)]
    result = subprocess.run(command + list(args), capture_output=True, check=False)
    commands.append({'argv': command + list(args), 'returncode': result.returncode,
                     'stderr': result.stderr.decode('utf-8', errors='replace')})
    if result.returncode:
        raise RuntimeError(commands[-1])
    return result.stdout


commands = []
report = {'schema_version': 1, 'status': 'preparing', 'base_commit': BASE, 'base_git_tree': BASE_TREE,
          'source_root': str(SOURCE), 'original_git_dir_read_only': str(GIT),
          'build_executed': False, 'instrumentation_added': False, 'commands': commands}


def main():
    assert ROOT.drive.upper() == 'C:' and not SOURCE.exists()
    assert {p.name for p in PROVENANCE.iterdir()} == {'prepare_source.py'}
    assert sha(PATCH) == PATCH_SHA and sha(MANIFEST) == MANIFEST_SHA
    raw_patch, raw_manifest = PATCH.read_bytes(), MANIFEST.read_bytes()
    historical = json.loads(raw_manifest)
    assert historical['repository_head'] == BASE
    write_new(PROVENANCE / 'safe-subset.patch', raw_patch)
    write_new(PROVENANCE / 'historical-validation-summary.json', raw_manifest)
    write_new(PROVENANCE / 'audited-source-notes.md', NOTES.read_bytes())
    report.update(patch={'original_path': str(PATCH), 'sha256': PATCH_SHA, 'bytes': len(raw_patch)},
                  historical_manifest={'original_path': str(MANIFEST), 'sha256': MANIFEST_SHA},
                  historical_binary=historical['binary'], audited_notes_sha256=sha(NOTES))
    assert git('rev-parse', BASE + '^{tree}', original=True).decode().strip() == BASE_TREE
    tree = git('ls-tree', '-rz', BASE, original=True)
    entries = {}
    for record in tree.split(b'\0'):
        if not record:
            continue
        header, name = record.split(b'\t', 1)
        mode, kind, oid = header.decode().split()
        assert mode in ('100644', '100755') and kind == 'blob'
        entries[name.decode('utf-8')] = (mode, oid)
    assert len(entries) == 18582
    archive_path = PROVENANCE / 'base-source.tar'
    git('archive', '--format=tar', '--output=' + str(archive_path), BASE, original=True)
    report['base_archive'] = {'sha256': sha(archive_path), 'bytes': archive_path.stat().st_size}
    SOURCE.mkdir()
    seen, total = set(), 0
    with tarfile.open(archive_path, 'r:') as archive:
        for member in archive:
            relative = PurePosixPath(member.name)
            assert not relative.is_absolute() and '..' not in relative.parts and '\\' not in member.name
            assert ':' not in member.name and '.git' not in relative.parts
            destination = SOURCE.joinpath(*relative.parts)
            assert destination.resolve().is_relative_to(SOURCE)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            assert member.isfile() and member.name in entries and member.name not in seen
            seen.add(member.name)
            raw = archive.extractfile(member).read()
            total += len(raw)
            assert total <= 600 * 1024**2
            assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == entries[member.name][1]
            destination.parent.mkdir(parents=True, exist_ok=True)
            write_new(destination, raw)
    assert seen == set(entries)
    report['base_members_verified'] = len(seen)
    report['base_source_bytes'] = total
    git('init', '--quiet')
    git('config', '--local', 'core.autocrlf', 'false')
    git('config', '--local', 'core.filemode', 'false')
    git('config', '--local', 'core.longpaths', 'true')
    write_new(SOURCE / '.git/objects/info/alternates', (str(GIT / 'objects').replace('\\', '/') + '\n').encode())
    git('symbolic-ref', 'HEAD', 'refs/heads/skycache-baseline-reconstruction')
    git('update-ref', 'HEAD', BASE)
    git('read-tree', BASE)
    git('update-index', '--refresh')
    assert not git('diff', '--name-only').strip()
    patch = str(PROVENANCE / 'safe-subset.patch')
    git('apply', '--index', '--check', patch)
    git('apply', '--index', patch)
    patched_tree = git('write-tree').decode().strip()
    changed = git('diff', '--cached', '--name-only', BASE).decode().splitlines()
    expected_changed = [row['path'] for row in historical['source_changes']['all_current_repository_changes']]
    assert sorted(changed) == sorted(expected_changed)
    audited = {}
    local = SOURCE / 'src/main/java/com/google/devtools/build/lib/skyframe/serialization/local'
    for name, expected in AUDITED.items():
        actual = sha(local / name)
        assert actual == expected, (name, actual, expected)
        audited[name] = actual
    final_entries = []
    for record in git('ls-files', '--stage', '-z').split(b'\0'):
        if not record:
            continue
        header, name = record.split(b'\t', 1)
        mode, oid, stage = header.decode().split()
        assert stage == '0'
        name = name.decode('utf-8')
        raw = (SOURCE / name).read_bytes()
        assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == oid
        final_entries.append({'path': name, 'mode': mode, 'git_blob': oid, 'bytes': len(raw),
                              'sha256': hashlib.sha256(raw).hexdigest()})
    write_new(PROVENANCE / 'source-members.json', (json.dumps(final_entries, indent=2) + '\n').encode())
    assert not git('diff', '--name-only').strip()
    report.update(status='source_reconstructed_no_build', patched_git_tree=patched_tree,
                  changed_paths=changed, changed_paths_count=len(changed), audited_source_sha256=audited,
                  patched_members_verified=len(final_entries), source_members_sha256=sha(PROVENANCE / 'source-members.json'),
                  mode_note='Git index/archive preserve executable modes; Windows filesystem modes are not Unix mode evidence.',
                  object_storage_note='Only this new repository has an alternates reference to the existing immutable object store. New patch blobs/tree are written locally; original Git metadata and checkout are unchanged.')


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        write_new(PROVENANCE / 'reconstruction.json', (json.dumps(report, indent=2) + '\n').encode())
    print(json.dumps({key: report[key] for key in ('status', 'base_git_tree', 'patched_git_tree',
          'changed_paths_count', 'base_members_verified', 'patched_members_verified', 'base_archive')}))
