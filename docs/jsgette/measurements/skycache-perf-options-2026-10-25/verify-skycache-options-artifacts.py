from pathlib import Path
import json,subprocess,os,hashlib,importlib.util,shutil
R=Path('/home/jgett/REPOS/.work/skycache-perf-options-2026-10-25')
S=Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
D=Path('/mnt/e/scw/docs/docs/jsgette/measurements/skycache-perf-options-2026-10-25')
src=json.loads((R/'source-verification.json').read_text())
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=S).decode().strip()==src['experimental_commit']
assert not subprocess.check_output(['git','status','--porcelain'],cwd=S).strip()
index=R/'verify-prototype.index';assert not index.exists()
env=dict(os.environ,GIT_INDEX_FILE=str(index))
subprocess.run(['git','read-tree',src['base']],cwd=S,env=env,check=True)
subprocess.run(['git','apply','--cached',str(R/'prototype.patch')],cwd=S,env=env,check=True)
tree=subprocess.check_output(['git','write-tree'],cwd=S,env=env).decode().strip();assert tree==src['tree'];index.unlink()
spec=importlib.util.spec_from_file_location('grpc',T/'work/datadog-concurrent.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
checks=[]
for dirname in ['controlled','profiles']:
 root=R/dirname;p=json.loads((root/'results.json').read_text());assert p['status']=='passed' and p['module_lock_restored']
 for row in p['runs']:
  if row['preparation']:continue
  data=(root/(row['label']+'.grpc.bin')).read_bytes();pos=0;count=0
  while pos<len(data):
   size,pos=c.modes.varint(data,pos);f=c.modes.fields(data[pos:pos+size]);pos+=size
   if not f.get(3,[b''])[0].decode().endswith('/FindMissingBlobs'):continue
   req=c.modes.fields(c.modes.fields(c.modes.fields(f[4][0])[10][0])[1][0]);digests=[c.modes.fields(x) for x in req.get(2,[])]
   profile=root/(row['label']+'.profile.gz')
   assert len(digests)==1 and digests[0][1][0].decode()==hashlib.sha256(profile.read_bytes()).hexdigest() and digests[0][2][0]==profile.stat().st_size
   count+=1
  assert count<=1,(dirname,row['label'],count)
  checks.append(dict(directory=dirname,run=row['label'],find_missing_blobs_calls=count,find_missing_blobs_only_profile_digest=True))
result=dict(prototype_patch_reconstructs_tree=tree,bes_profile_checks=checks,measured_builds=18,diagnostic_profiles=2,verification_note='Initial verifier incorrectly required one FindMissingBlobs call per run; zero is valid. Corrected to permit zero or one and verify every observed digest against the exact uploaded profile.')
(R/'final-verification.json').write_text(json.dumps(result,indent=2)+'\n')
for p in [R/'final-verification.json',Path(__file__),T/'work/write-skycache-options-report.py']:
 shutil.copy2(p,D/p.name)
files=sorted(p for p in D.rglob('*') if p.is_file() and p.name!='SHA256SUMS')
(D/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(D).as_posix()+'\n' for p in files))
print(json.dumps(dict(patch_tree=tree,bes_profiles=len(checks),public_files=len(files)+1)))
