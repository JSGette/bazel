"""Prepare a reviewable evidence package after all measurement checks pass."""
from pathlib import Path
import hashlib,json,shutil,subprocess,tarfile
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
R=Path('/home/jgett/REPOS/.work/datadog-ssd-fresh-2026-10-25')
D=Path('/home/jgett/REPOS/bazel-skyframe-findings')
E=D/'docs/jsgette/measurements/skycache-v2-fresh-wsl-2026-10-25'
B=Path('/mnt/d/Codex/wsl-reset-2026-10-25')
def digest(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
p=json.loads((R/'results.json').read_text());s=json.loads((R/'summary.json').read_text())
assert p['status']==s['status']=='passed' and s['measured_builds']==42
shutil.copy2(T/'work/fresh-ubuntu-setup.log',R/'fresh-ubuntu-setup.log')
(R/'fresh-packages.tsv').write_bytes(subprocess.check_output(['dpkg-query','-W','-f=${binary:Package}\t${Version}\n']))
(R/'guest-memory.txt').write_text(Path('/proc/meminfo').read_text())
(R/'guest-cpu.txt').write_bytes(subprocess.check_output(['lscpu']))
for name in ['datadog-fresh-benchmark.py','analyze-fresh-benchmark.py','record-cache-preparation.py','publish-fresh-evidence.py','inspect-fresh-profiles.py','verify-fresh-evidence.py']:
 shutil.copy2(T/'work'/name,E/name)
for name in ['summary.json','cache-preparation-diagnosis.json','hardware.json','harness.py','harness-preparation.py','harness-before-resume-1.py','fresh-packages.tsv','guest-memory.txt','guest-cpu.txt','profile-observations.json']:
 if name.endswith('.json'):save(E/name,json.loads((R/name).read_text(encoding='utf-8-sig')))
 else:shutil.copy2(R/name,E/name)
save(E/'fresh-host-hardware.json',json.loads((T/'work/fresh-host-hardware.json').read_text(encoding='utf-8-sig')))
save(E/'final-windows-storage.json',json.loads((T/'work/datadog-storage-live.json').read_text(encoding='utf-8-sig')))
save(E/'final-reset-state.json',json.loads((T/'work/final-reset-state.json').read_text(encoding='utf-8-sig')))
public=json.loads(json.dumps(p))
for field in ['workspace_diff_before','initial_workspace_diff']:
 text=public.pop(field,'')
 if text:
  name=field+'.patch';(E/name).write_text(text+'\n');public[field+'_artifact']=name
save(E/'results.json',public)
sampledir=E/'samples';sampledir.mkdir(exist_ok=False)
for row in p['runs']:
 name=row['label']+'.samples.json'
 samples=json.loads((R/name).read_text())
 (sampledir/(row['label']+'.jsonl')).write_text(''.join(json.dumps(x,separators=(',',':'))+'\n' for x in samples))
raw=[]
for q in sorted(R.iterdir()):
 if q.is_file() and (q.suffix in ['.log','.gz'] or q.name.endswith(('.bep.json','.storage.json','.samples.json'))):
  raw.append({'name':q.name,'bytes':q.stat().st_size,'sha256':digest(q)})
save(E/'raw-evidence-manifest.json',{'location':str(R),'raw_results_sha256':digest(R/'results.json'),'files':raw,'note':'Full BEP/profiles/logs remain local and in the private D: archive. Public metrics omit arbitrary client environment values.'})
archive=B/'fresh-datadog-measurements.tar'
assert not archive.exists()
with tarfile.open(archive,'w') as a:
 for q in sorted(R.iterdir()):
  if q.is_file():a.add(q,arcname=q.name)
with tarfile.open(archive) as a:
 names=a.getnames();assert 'results.json' in names and 'summary.json' in names
 for member in a.getmembers():
  with a.extractfile(member) as f:assert hashlib.file_digest(f,'sha256').hexdigest()==digest(R/member.name),member.name
save(E/'fresh-evidence-backup.json',{'path':str(archive),'sha256':digest(archive),'bytes':archive.stat().st_size,'files':len(names)})
save(R/'published-evidence-location.json',{'repository':str(D),'evidence':str(E),'private_archive':str(archive)})
print(json.dumps({'evidence_files':len(list(E.rglob('*'))),'private_archive_bytes':archive.stat().st_size},indent=2))
