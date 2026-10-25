from pathlib import Path
import subprocess, json
S = Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
R = Path('/home/jgett/REPOS/.work/skycache-remote-sync-2026-10-25')
build = json.loads((R / 'build.json').read_text())
assert build['exit_code'] == 0 and build['test_cases'] == 551
assert all(x['exit_code'] == 0 for x in json.loads((R / 'probes.json').read_text()))
def git(*args):
    return subprocess.check_output(['git', *args], cwd=S).decode().strip()
assert git('write-tree') == build['source_tree']
assert not git('diff', '--name-only')
assert not git('diff', '--name-only', '--diff-filter=U')
subprocess.run(['git', 'diff', '--cached', '--check'], cwd=S, check=True)
message = R / 'merge-message.txt'
message.write_text('Merge remote SkyCache demand reads with local performance optimizations\n\n'
                   'Integrate remote MVP 6a380e6eeb and retain lookup key/hash reuse and\n'
                   'array decoding from 36a73d8fb4. The merge has no manual resolutions.\n\n'
                   'Validation: Bazel and remote worker build; 551 cases across 21 targets;\n'
                   'real remote restart/mutation/eviction/offline probe; local execution\n'
                   'rewind/output-mode probe; two-worktree concurrent mutation/corruption probe.\n')
subprocess.run(['git', 'commit', '-F', str(message)], cwd=S, check=True)
result = dict(commit=git('rev-parse', 'HEAD'), tree=git('rev-parse', 'HEAD^{tree}'),
              parents=git('show', '-s', '--format=%P', 'HEAD').split(),
              status=git('status', '--porcelain'),
              baseline='36a73d8fb4f13c315002d74f2c73532a2db67cdd',
              remote='6a380e6eeba73d278d3e1b4a5625edb5fcefd7e5',
              upstream='a5c50fcb65841b505ec72840d83381a704e70afa')
assert result['tree'] == build['source_tree'] and result['status'] == ''
for name, base in [('remote-sync.patch', result['baseline']),
                   ('integrated-from-previous.patch', '1aac2aa2ec6fe31af2e979558f06a3b6971c601b'),
                   ('integrated-from-upstream.patch', result['upstream'])]:
    (R / name).write_bytes(subprocess.check_output(['git', 'diff', '--binary', base, 'HEAD'], cwd=S))
(R / 'source.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
