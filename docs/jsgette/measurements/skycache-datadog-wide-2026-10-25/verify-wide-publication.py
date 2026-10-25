from pathlib import Path
import hashlib,json,re,subprocess
D=Path('/home/jgett/REPOS/bazel-skyframe-findings');E=D/'docs/jsgette/measurements/skycache-datadog-wide-2026-10-25'
def sha(q):
 with q.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
files=sorted(q for q in E.rglob('*') if q.is_file() and q.name!='SHA256SUMS')
(E/'SHA256SUMS').write_text(''.join(sha(q)+'  '+str(q.relative_to(E))+'\n' for q in files))
for line in (E/'SHA256SUMS').read_text().splitlines():
 digest,name=line.split('  ',1);assert sha(E/name)==digest
reports=[D/'docs/jsgette/skycache-architecture-2026-10-25.md',D/'docs/jsgette/skycache-datadog-wide-2026-10-25.md']
links=0
for report in reports:
 data=report.read_text();assert data.count('```')%2==0
 for target in re.findall(r'\]\(([^)]+)\)',data):
  if target.startswith('https://github.com/JSGette/bazel/blob/jsgette/master/'):
   q=D/target.split('/blob/jsgette/master/',1)[1].split('#')[0]
  elif target.startswith('http'):continue
  else:q=report.parent/target.split('#')[0]
  assert q.exists(),str(q);links+=1
 assert 'A larger Datadog target-set comparison is the next experiment.' not in data
p=json.loads((E/'datadog-wide-apps-2026-10-25/results.json').read_text())
assert p['status']=='passed' and p['original_workspace_restored']
assert len([r for r in p['runs'] if not r['preparation']])==18
assert sha(Path('/home/jgett/REPOS/datadog-agent/MODULE.bazel.lock'))==p['restored_lockfile_sha256']
assert subprocess.check_output(['git','-C','/home/jgett/REPOS/datadog-agent','diff','--name-only','HEAD'],text=True).strip()=='MODULE.bazel.lock'
assert not subprocess.check_output(['git','-C','/home/jgett/REPOS/bazel-skycache-v2','status','--porcelain']).strip()
subprocess.run(['git','-C',str(D),'diff','--check'],check=True)
print(json.dumps({'public_evidence_files':len(files)+1,'local_source_links_verified':links,'measured_builds':18,'original_workspace_restored':True,'source_unchanged':True}))
