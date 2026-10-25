from pathlib import Path
import hashlib,json,re,shutil,subprocess,tarfile,io
root=Path('/home/jgett/REPOS/.work/skycache-v2-datadog-ssd-2026-10-25')
task=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
repo=Path('/home/jgett/REPOS/bazel-skyframe-findings')
rel=Path('docs/jsgette/measurements/skycache-v2-datadog-ssd-2026-10-25')
dest=repo/rel
report_rel=Path('docs/jsgette/skycache-v2-datadog-ssd-2026-10-25.md')
def git(*a):return subprocess.check_output(['git','-C',str(repo),*a],text=True).strip()
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
assert git('branch','--show-current')=='jsgette/master'
assert git('rev-parse','HEAD')==git('rev-parse','origin/jsgette/master')
windows=json.loads((task/'work/e-storage-analysis.json').read_text(encoding='utf-8-sig'))
for folder in [root,dest]:
 s=json.loads((folder/'storage-summary.json').read_text());s['windows']=windows
 (folder/'storage-summary.json').write_text(json.dumps(s,indent=2)+'\n')
(dest/'e-storage-analysis.json').write_text(json.dumps(windows,indent=2)+'\n')
for name in ['datadog-ssd-benchmark.py','watch-datadog-storage.ps1','analyze-e-storage.ps1','finalize-datadog-storage.py']:
 shutil.copy2(task/'work'/name,dest/name)
shutil.copy2(task/'work/datadog-ssd-benchmark.py',root/'harness-ready-to-resume.py')
(dest/'setup-notes.json').write_text(json.dumps({
 'successful_datadog_builds':0,'measured_datadog_builds':0,
 'prior_C_image_preserved':True,'E_vhd_sparse':False,
 'fstrim_discard_bytes':328864768000,'fstrim_host_space_increase_observed':False,
 'unmount_C_loop_attempt':'WSL launch failed with Wsl/Service/0x8007274c; mount remained present; no forced unmount.',
 'duplicate_cache_scope':'Same relative paths and actual SHA-256 file contents; unmatched paths are not claimed unique content globally.',
 'scan_method':'Windows .NET directory traversal, skip reparse points/system folders; Linux du -x at depth 3, no symlink following.',
 'scan_error_files':['work-storage-scan-errors.log','system-storage-scan-errors.log'],
 'no_cleanup_executed':True,'benchmark_status':'Blocked on SSD capacity. Corrected harness is ready to resume.'
},indent=2)+'\n')
text=(repo/report_rel).read_text().replace('The normal Bazel home caches account for another **44.66 GiB**.',
 'The Bazel home caches total another **44.66 GiB**, including the new 6.07 GiB cache described below.')
(repo/report_rel).write_text(text)
shutil.copy2(repo/report_rel,task/'outputs/Datadog-SSD-storage-analysis.md')
# Do not publish raw BEP or environment dumps. Check common credential forms before committing.
patterns=[rb'ghp_[A-Za-z0-9]{30,}',rb'github_pat_[A-Za-z0-9_]{30,}',rb'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',rb'Authorization:\s*Bearer\s+\S{15,}']
for p in dest.rglob('*'):
 if p.is_file():
  assert not p.name.endswith('.bep.json'),p
  for pattern in patterns:assert not re.search(pattern,p.read_bytes(),re.I), 'Potential credential in '+str(p)
(dest/'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.relative_to(dest).as_posix()}\n' for p in sorted(dest.rglob('*')) if p.is_file() and p.name!='SHA256SUMS'))
subprocess.run(['git','-C',str(repo),'add','--',str(rel),str(report_rel)],check=True)
subprocess.run(['git','-C',str(repo),'diff','--cached','--check'],check=True)
staged=git('diff','--cached','--name-only').splitlines()
assert all(p.startswith(str(rel)+'/') or p==str(report_rel) for p in staged)
subprocess.run(['git','-C',str(repo),'commit','-m','docs: record Datadog SSD setup and storage audit'],check=True)
archive=subprocess.check_output(['git','-C',str(repo),'archive','HEAD',str(rel)])
with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
 manifest=tar.extractfile(str(rel/'SHA256SUMS')).read().decode()
 for line in manifest.splitlines():
  expected,name=line.split('  ',1)
  assert hashlib.sha256(tar.extractfile(str(rel/name)).read()).hexdigest()==expected,name
print(json.dumps({'commit':git('rev-parse','HEAD'),'evidence_files':len(manifest.splitlines()),'archive_checksums_verified':True},indent=2))
subprocess.run(['git','-C',str(repo),'push','origin','HEAD:refs/heads/jsgette/master'],check=True)
remote=git('ls-remote','origin','refs/heads/jsgette/master').split()[0]
assert remote==git('rev-parse','HEAD')
assert not git('status','--porcelain')
print('Remote verified: '+remote)
