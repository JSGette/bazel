from pathlib import Path
import hashlib,json,os,shutil,socket,subprocess,time
R=Path('/home/jgett/REPOS');B=Path('/mnt/d/Codex/wsl-reset-2026-10-25')
ROOT=R/'.work/datadog-ssd-fresh-2026-10-25';ROOT.mkdir(parents=True,exist_ok=False)
report={'started_unix':time.time(),'commands':[]}
def call(argv,cwd=None):
 report['commands'].append({'argv':list(map(str,argv)),'cwd':str(cwd) if cwd else None})
 with (ROOT/'setup.log').open('a') as log:subprocess.run(list(map(str,argv)),cwd=cwd,stdout=log,stderr=subprocess.STDOUT,check=True)
def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
call(['git','config','--global','user.name','Joseph Gette'])
call(['git','config','--global','user.email','joseph.gette@datadoghq.com'])
call(['git','config','--global','credential.helper',r'/mnt/c/Program\ Files/Git/mingw64/bin/git-credential-manager.exe'])
for directory,url,revision in [
 ('datadog-agent','https://github.com/DataDog/datadog-agent.git','0cde96beb70963d0838119c308b9d0422ee66be0'),
 ('bazel-skycache-v2','https://github.com/JSGette/bazel.git','4cfc05f77ba91900a2e3c6ab135861498cb2a0bf')]:
 p=R/directory;call(['git','init',p]);call(['git','remote','add','origin',url],p)
 call(['git','fetch','--depth=1','origin',revision],p);call(['git','checkout','--detach','FETCH_HEAD'],p)
call(['git','fetch',B/'skycache-v2-local.bundle','refs/heads/jsgette/skycache-v2-local:refs/heads/jsgette/skycache-v2-local'],R/'bazel-skycache-v2')
call(['git','checkout','jsgette/skycache-v2-local'],R/'bazel-skycache-v2')
call(['git','clone','--depth=1','--branch','jsgette/master','https://github.com/JSGette/bazel.git',R/'bazel-skyframe-findings'])
call(['tar','-xf',B/'benchmark-jdk25.tar','-C',R/'.tools'])
for name,dest in [('skycache','bazel-skycache-v2'),('vanilla','bazel-vanilla'),('bazel-9.3.0','bazel')]:
 p=R/'.tools'/dest;shutil.copy2(B/'benchmark-tools'/name,p);p.chmod(0o755)
 report.setdefault('binaries',{})[dest]={'sha256':sha(p),'path':str(p)}
assert report['binaries']['bazel-skycache-v2']['sha256']=='28026d467ef10667c0fcfb1741495fbefe4fee9dfed43d3d2d9733726b5cb159'
assert report['binaries']['bazel-vanilla']['sha256']=='1ef0d6ff94b24c787d539f63672f93f0da9bd96d49750d729489cbffc80aa588'
for name in ['datadog-agent','bazel-skycache-v2','bazel-skyframe-findings']:
 report.setdefault('sources',{})[name]={'head':subprocess.check_output(['git','-C',str(R/name),'rev-parse','HEAD'],text=True).strip(),
 'status':subprocess.check_output(['git','-C',str(R/name),'status','--porcelain'],text=True)}
with socket.create_connection(('10.55.0.1',8080),5):pass
report.update(finished_unix=time.time(),cache_carry_tcp_reachable=True)
(ROOT/'fresh-setup.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'sources':report['sources'],'binaries':report['binaries'],'cache_carry_tcp_reachable':True},indent=2),flush=True)
