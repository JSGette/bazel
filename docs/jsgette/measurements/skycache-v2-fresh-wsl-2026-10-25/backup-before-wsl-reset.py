"""Preserve source history and evidence, excluding regenerable caches and Qwen."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,tarfile,time
B=Path('/mnt/d/Codex/wsl-reset-2026-10-25')
H=Path('/home/jgett')
R=H/'REPOS'
W=R/'.work'
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def run(argv,**kw):return subprocess.check_output(argv,**kw)
assert B.is_dir() and not (B/'backup-verified.json').exists()
report={'started_unix':time.time(),'source_repositories':{},'archives':{},'tool_binaries':{}}
for p in R.iterdir():
 if p.name=='Qwen3-Coder':continue
 if (p/'.git').exists():
  report['source_repositories'][p.name]={
   'head':run(['git','-C',str(p),'rev-parse','HEAD'],text=True).strip(),
   'status':run(['git','-C',str(p),'status','--porcelain'],text=True)}
(B/'source-status.json').write_text(json.dumps(report['source_repositories'],indent=2)+'\n')
# Preserve all repository histories, working files and home configuration. No build caches.
archive=B/'sources-and-home.tar'
with (B/'sources-archive.log').open('w') as log:
 subprocess.run(['tar','-cf',str(archive),'-C','/home',
   '--exclude=jgett/REPOS/.work','--exclude=jgett/REPOS/.tools',
   '--exclude=jgett/REPOS/Qwen3-Coder','--exclude=jgett/.cache','--exclude=jgett/go',
   '--exclude=jgett/.cursor-server','--exclude=jgett/.vscode-remote-containers',
   '--exclude=jgett/.rustup','jgett'],stdout=log,stderr=subprocess.STDOUT,check=True)
# Top-level experiment evidence is kept privately, including raw BEP/environment-bearing files.
paths=[]
suffixes=('.json','.jsonl','.log','.txt','.md','.py','.sh','.csv','.patch','.xml','.profile.gz','.jfr')
for parent,dirs,files in os.walk(W):
 d=Path(parent);depth=len(d.relative_to(W).parts)
 dirs[:]=[x for x in dirs if depth<2 and x not in ['cache','objects','external','action-cache'] and not x.startswith('out-') and not x.endswith('-cache')]
 for name in files:
  if name.endswith(suffixes):paths.append(d/name)
evidence=B/'benchmark-evidence.tar'
with tarfile.open(evidence,'w') as tar:
 for p in paths:tar.add(p,arcname=str(p.relative_to(W)),recursive=False)
tools=B/'benchmark-tools';tools.mkdir()
for name,p in {
 'skycache':W/'skycache-v2-datadog-ssd-2026-10-25/bin/skycache',
 'vanilla':W/'skycache-v2-datadog-ssd-2026-10-25/bin/vanilla',
 'bazel-9.3.0':R/'.tools/bazel'}.items():
 shutil.copy2(p,tools/name);report['tool_binaries'][name]={'sha256':sha(p),'bytes':p.stat().st_size}
assert report['tool_binaries']['skycache']['sha256']=='28026d467ef10667c0fcfb1741495fbefe4fee9dfed43d3d2d9733726b5cb159'
assert report['tool_binaries']['vanilla']['sha256']=='1ef0d6ff94b24c787d539f63672f93f0da9bd96d49750d729489cbffc80aa588'
subprocess.run(['tar','-chf',str(B/'benchmark-jdk25.tar'),'-C',str(R/'.tools'),'skycache-benchmark-jdk25'],check=True)
shutil.copy2(W/'skycache-concurrent/datadog-crossrevision-mapping-eviction-review/a/MODULE.bazel.lock',B/'datadog-resolved-MODULE.bazel.lock')
shutil.copy2('/etc/os-release',B/'prior-os-release.txt')
shutil.copy2('/etc/wsl.conf',B/'prior-wsl.conf')
(B/'packages.tsv').write_text(run(['dpkg-query','-W','-f=${binary:Package}\t${Version}\n'],text=True))
# Verify archive readability and essential source entries before allowing unregister.
for p in [archive,evidence,B/'benchmark-jdk25.tar']:
 count=0;essential=set()
 with tarfile.open(p) as tar:
  for item in tar:
   count+=1
   if item.name in ['jgett/REPOS/bazel/.git/HEAD','jgett/REPOS/bazel-skycache-v2/.git','jgett/REPOS/bzlog/PLAN.md','skycache-benchmark-jdk25/bin/java']:essential.add(item.name)
 if p==archive:assert len(essential)==3,essential
 if p.name=='benchmark-jdk25.tar':assert 'skycache-benchmark-jdk25/bin/java' in essential
 report['archives'][p.name]={'sha256':sha(p),'bytes':p.stat().st_size,'entries':count}
report.update(finished_unix=time.time(),verified=True,private_backup=True,evidence_files=len(paths))
(B/'backup-verified.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'verified':True,'archives':report['archives'],'source_repositories':len(report['source_repositories']),'evidence_files':len(paths)},indent=2),flush=True)
