from pathlib import Path
import hashlib, io, json, subprocess, zipfile
root=Path('/home/jgett/REPOS/.work/skycache-channels-2026-10-26/agent')
root.mkdir(exist_ok=False)
work=Path(__file__).parent
jdk=Path('/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin')
prefix='com/google/devtools/build/lib/skyframe/serialization/'
sources={}
for variant in ['128','256']:
 binary=root.parent/('build-'+variant+'-v2')/'bazel'
 with zipfile.ZipFile(binary) as outer:
  with zipfile.ZipFile(io.BytesIO(outer.read('A-server.jar'))) as jar:
   content=jar.read(prefix+'local/RemoteSkyCache.class')
   (root/('channels'+variant+'.bin')).write_bytes(content)
   sources['channels'+variant]=hashlib.sha256(content).hexdigest()
   registry=jar.read(prefix+'AutoRegistry.class')
   if variant=='128':(root/'registry.bin').write_bytes(registry)
   else:assert registry==(root/'registry.bin').read_bytes()
   for name in jar.namelist():
    if name.startswith(prefix+'local/RemoteSkyCache$CachedChunkChannels') and name.endswith('.class'):
     path=root/'classes'/name;path.parent.mkdir(parents=True,exist_ok=True)
     content=jar.read(name)
     if path.exists():assert path.read_bytes()==content,name
     else:path.write_bytes(content)
     sources[name]=hashlib.sha256(content).hexdigest()
subprocess.run([str(jdk/'javac'),'-d',str(root/'classes'),str(work/'SkycacheChannelAgent.java')],check=True)
(root/'manifest.mf').write_text('Manifest-Version: 1.0\nPremain-Class: SkycacheChannelAgent\n\n')
subprocess.run([str(jdk/'jar'),'--create','--file',str(root/'agent.jar'),'--manifest',str(root/'manifest.mf'),
 '-C',str(root/'classes'),'.','-C',str(root),'registry.bin','-C',str(root),'channels128.bin','-C',str(root),'channels256.bin'],check=True)
sources['registry']=hashlib.sha256((root/'registry.bin').read_bytes()).hexdigest()
sources['agent_jar']=hashlib.sha256((root/'agent.jar').read_bytes()).hexdigest()
(root/'provenance.json').write_text(json.dumps(sources,indent=2)+'\n')
print(json.dumps(sources,indent=2))
