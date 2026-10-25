from pathlib import Path
import json, hashlib, re, subprocess, shutil

repo=Path('E:/scw/docs')
task=Path('C:/Users/jgett/Documents/Codex/2026-10-24/ex')
rel=Path('docs/jsgette/measurements/skycache-remote-sync-2026-10-25')
evidence=repo/rel
reports=[Path('docs/jsgette')/name for name in ['skycache-remote-sync-2026-10-25.md',
    'skycache-architecture-2026-10-25.md','skycache-perf-options-2026-10-25.md']]
git='C:/Program Files/Git/cmd/git.exe'
def run(*args):return subprocess.check_output([git,'-C',str(repo),*args])
def write(path,text):path.write_bytes(text.encode('utf-8'))
assert run('rev-parse','--abbrev-ref','HEAD').strip()==b'jsgette/master'
assert run('rev-parse','HEAD')==run('rev-parse','origin/jsgette/master')
source=json.loads((evidence/'update/source.json').read_text())
execution=json.loads((evidence/'update/execution/results.json').read_text())
builds=sum('build' in x['argv'] for x in execution['runs'])
assert builds==12 and len(execution['runs'])==37
report_path=repo/reports[0]
report=report_path.read_text(encoding='utf-8')
old=f"Product integration commit: `{source['commit']}`."
new=f"Tested product integration commit: `{source['build_commit']}`."
assert old in report or new in report
report=report.replace(old,new).replace('The separate execution fixture passes 37 builds covering',
    'The separate execution fixture passes 12 builds (37 commands including clean, graph checks and shutdown) covering')
write(report_path,report)
write(task/'outputs/SkyCache-remote-integration.md',report)
arch_path=repo/reports[1]
arch=arch_path.read_text(encoding='utf-8').replace('This is a shared local filesystem design.',
    'The local storage layer uses a shared filesystem.')
write(arch_path,arch);write(task/'outputs/SkyCache-architecture.md',arch)
for name in ['write-skycache-remote-sync-report.py','finalize-remote-sync-publication.py']:
    data=(task/'work'/name).read_bytes().replace(b'\r\n',b'\n')
    (evidence/name).write_bytes(data)
finalization=dict(execution_builds=builds,execution_commands=len(execution['runs']),
    report_review='Corrected command count versus build count, distinguished the tested product commit from the documentation merge, and clarified local-storage scope.',
    archive_snapshot_note='Raw results, source patches and measurements are unchanged. The public report generator and this finalization script were updated after the private archive snapshot.',
    patch_verification_note='Exact-tree reconstruction passed. Git apply reported 17 historical Markdown hard-break/trailing-space lines in the full upstream patches; they were retained to preserve the exact trees.')
write(evidence/'public-finalization.json',json.dumps(finalization,indent=2)+'\n')
files=sorted(p for p in evidence.rglob('*') if p.is_file() and p.name!='SHA256SUMS')
write(evidence/'SHA256SUMS',''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(evidence).as_posix()+'\n' for p in files))
remote=json.loads((evidence/'remote-documents.json').read_text())['files']
for x in remote:assert hashlib.sha256((repo/x['path']).read_bytes()).hexdigest()==x['sha256'],x['path']
for sums in {repo/x['path'] for x in remote if x['path'].endswith('/SHA256SUMS')}:
    for line in sums.read_text().splitlines():
        expected,name=line.split('  ',1)
        assert hashlib.sha256((sums.parent/name).read_bytes()).hexdigest()==expected,(sums,name)
promotion=json.loads((evidence/'promotion.json').read_text())
for x in promotion['patch_checks']:
    assert hashlib.sha256((evidence/x['path']).read_bytes()).hexdigest()==x['sha256']
patterns=[rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',rb'gh[pousr]_[A-Za-z0-9]{30,}',
    rb'github_pat_[A-Za-z0-9_]{40,}',rb'AKIA[A-Z0-9]{16}',
    rb'(?i)authorization\s*[:=]\s*[\"\']?Bearer\s+[A-Za-z0-9._~-]{20,}']
public=[repo/x for x in reports]+files+[repo/x['path'] for x in remote]
for p in public:
    data=p.read_bytes()
    for pattern in patterns:assert not re.search(pattern,data),f'Potential credential in {p.relative_to(repo)}'
    if p.suffix=='.md':
        for link in re.findall(r'\]\(([^)]+)\)',data.decode('utf-8')):
            if '://' in link or link.startswith('#'):continue
            assert (p.parent/link.split('#')[0]).exists(),(p.name,link)
scope=[p.as_posix() for p in reports]+[rel.as_posix()]+[x['path'] for x in remote]
run('add','--',*scope)
for p in files:
    assert run('show',':'+p.relative_to(repo).as_posix())==p.read_bytes(),p
for x in remote:assert hashlib.sha256(run('show',':'+x['path'])).hexdigest()==x['sha256'],x['path']
assert run('show',':'+(rel/'SHA256SUMS').as_posix())==(evidence/'SHA256SUMS').read_bytes()
run('diff','--cached','--check','--',*[p.as_posix() for p in reports],'docs/jsgette/skycache-remote-mvp.md')
staged=run('diff','--cached','--name-only').decode().splitlines()
allowed={p.as_posix() for p in reports}|{x['path'] for x in remote}
assert all(p in allowed or p.startswith(rel.as_posix()+'/') for p in staged)
result=dict(public_evidence_files=len(files)+1,remote_files=len(remote),staged_files=len(staged),
    staged_bytes_match_hashes=True,report_links='passed',credential_pattern_scan='passed',
    exact_tree_patch_checks=len(promotion['patch_checks']),execution_builds=builds)
write(task/'work/finalize-remote-sync-publication.json',json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
