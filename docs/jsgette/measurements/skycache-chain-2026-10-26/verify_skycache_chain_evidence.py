from pathlib import Path
import gzip, hashlib, json, math, re, shutil, subprocess

root=Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
dest=root/'docs/jsgette/measurements/skycache-chain-2026-10-26'
work=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-26/review-our-current-progress-and-find/work')

def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

# Preserve the generator inputs as well as each exact executed harness.
for name in ['SkycacheStageProbe.java','skycache_validation_benchmark.py',
             'skycache_chain_benchmark.py','skycache_chain_diagnostic.py',
             'archive_skycache_chain.py']:
    shutil.copy2(work/name,dest/name)
for name in ['probe-manifest.mf','read-probe-manifest.mf','read-probe-v2-manifest.mf']:
    original=Path('/home/jgett/REPOS/.work/skycache-chain-2026-10-26')/name
    (dest/(name+'.gz')).write_bytes(gzip.compress(original.read_bytes(),mtime=0))
    (dest/name).unlink(missing_ok=True)
readme=dest/'README.md'
readme.write_text(readme.read_text().rstrip()+'\n')
count=0
for p in dest.rglob('*'):
    if not p.is_file():continue
    if p.name.endswith('.json.gz'):json.loads(gzip.decompress(p.read_bytes()))
    elif p.name.endswith('.jsonl.gz'):
        for line in gzip.decompress(p.read_bytes()).splitlines():json.loads(line)
    elif p.suffix=='.json':json.loads(p.read_text())
    elif p.suffix=='.py':compile(p.read_text(),str(p),'exec')
    count+=1

comparison=json.loads((dest/'comparison-direct.json').read_text())
s=comparison['summary'];delta=comparison['mean_retained_remote_minus_local']
assert math.isclose(sum(delta[k] for k in ['before_command_s','other_pre_build_complete_s','after_build_complete_s']),delta['command_s'])
assert all(v['n']==3 for v in s.values())
for a in ['direct-runs','runs','diagnostics','warm-diagnostics']:
    r=json.loads(gzip.decompress((dest/a/'results.json.gz').read_bytes()))
    assert r['status']=='passed' and r['source_states_unchanged'] and r['binary_unchanged']
    for row in r['runs']:
        assert row['outputs_match'] and row['probe_output_matches']
        assert (row['analysis_hits'],row['execution_hits'])==(18,11)
        assert row['local-skycache']['reused_witness_blocks']==103
        if row['arm']=='local':assert row['local-skycache']['published']
        else:assert row['committed']

checked_links=0
for md in [dest/'README.md',root/'docs/jsgette/skycache-remote-mvp.md']:
    for target in re.findall(r'\]\(([^)]+)\)',md.read_text()):
        if '://' in target or target.startswith('#'):continue
        name,_,anchor=target.partition('#')
        path=(md.parent/name).resolve()
        assert path.exists(),(md,target)
        if re.fullmatch(r'L\d+',anchor):assert len(path.read_text().splitlines())>=int(anchor[1:])
        checked_links+=1

check=subprocess.run(['git','-C',str(root),'diff','--check'],capture_output=True,text=True,check=True)
verification=dict(files_checked=count,local_links_checked=checked_links,
    median_warm_gap_s=s['remote-warm']['command_s']['median']-s['local']['command_s']['median'],
    median_warm_overhead_percent=100*(s['remote-warm']['command_s']['median']/s['local']['command_s']['median']-1),
    middle_share_of_mean_gap=delta['other_pre_build_complete_s']/delta['command_s'],
    invariant_checks='passed',diff_check='passed',production_code_changed=False)
(dest/'verification.json').write_text(json.dumps(verification,indent=2)+'\n')
shutil.copy2(work/'verify_skycache_chain_evidence.py',dest/'verify_skycache_chain_evidence.py')

# Evidence index intentionally excludes narrative docs and itself.
index=[dict(path=str(p.relative_to(dest)),bytes=p.stat().st_size,sha256=sha(p))
       for p in sorted(dest.rglob('*')) if p.is_file() and p.name not in ['README.md','evidence-index.json']]
(dest/'evidence-index.json').write_text(json.dumps(index,indent=2)+'\n')
for row in index:assert sha(dest/row['path'])==row['sha256']
print(json.dumps(verification,indent=2))
