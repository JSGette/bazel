from pathlib import Path
import json, subprocess
S = Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
R = Path('/home/jgett/REPOS/.work/skycache-remote-sync-2026-10-25/update')
build=json.loads((R/'build.json').read_text())
probes=json.loads((R/'probes.json').read_text())
assert build['exit_code']==0 and build['test_cases']==562
assert len(probes)==3 and all(x['exit_code']==0 for x in probes)
def git(*args):return subprocess.check_output(['git',*args],cwd=S,text=True).strip()
assert git('write-tree')==build['source_tree'] and not git('diff','--name-only')
subprocess.run(['git','diff','--cached','--check'],cwd=S,check=True)
message=R/'merge-message.txt'
message.write_text('Merge remote SkyCache cached reads and parent descriptor reuse\n\n'
    'Integrate 91d51164d3. Resolve packed/segmented lookup conflicts by passing\n'
    'the prepared key and SHA-256 through both normal and cached-only reads.\n'
    'Preserve newest-segment ordering and cached-only no-network behavior.\n\n'
    'Validation: Bazel and worker build; 562 cases across 21 targets; real remote\n'
    'restart/mutation/eviction/offline checks; execution rewind/output policies;\n'
    'two-worktree concurrent mutation/corruption checks.\n')
subprocess.run(['git','commit','-F',str(message)],cwd=S,check=True)
result=dict(commit=git('rev-parse','HEAD'),tree=git('rev-parse','HEAD^{tree}'),
            built_tree=build['source_tree'],parents=git('show','-s','--format=%P','HEAD').split(),
            status=git('status','--porcelain'),baseline='36a73d8fb4f13c315002d74f2c73532a2db67cdd',
            remote='91d51164d3d315dc2d79bbe3d40af874b9dae9e0',upstream='a5c50fcb65841b505ec72840d83381a704e70afa')
assert result['tree']==build['source_tree'] and result['status']==''
for name,base in [('remote-sync.patch',result['baseline']),
                  ('integrated-from-previous.patch','1aac2aa2ec6fe31af2e979558f06a3b6971c601b'),
                  ('integrated-from-upstream.patch',result['upstream'])]:
    (R/name).write_bytes(subprocess.check_output(['git','diff','--binary',base,'HEAD'],cwd=S))
(R/'source.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
