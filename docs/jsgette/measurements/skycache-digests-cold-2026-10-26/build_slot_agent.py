from pathlib import Path
import hashlib, io, json, subprocess, zipfile

root=Path('/home/jgett/REPOS/.work/skycache-digests-2026-10-26/agent-slots')
root.mkdir(exist_ok=False)
work=Path(__file__).parent
jdk=Path('/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin')
prefix='com/google/devtools/build/lib/skyframe/serialization/'
binaries={
 'channels':Path('/home/jgett/REPOS/.work/skycache-channels-2026-10-26/build-128-v2/bazel'),
 'scratch':root.parent/'build-slots-v2/bazel'}
sources={}
for variant,binary in binaries.items():
 with zipfile.ZipFile(binary) as outer:
  with zipfile.ZipFile(io.BytesIO(outer.read('A-server.jar'))) as jar:
   content=jar.read(prefix+'local/RemoteSkyCache.class')
   (root/(variant+'.bin')).write_bytes(content)
   sources[variant]=hashlib.sha256(content).hexdigest()
   if variant=='scratch':(root/'registry.bin').write_bytes(jar.read(prefix+'AutoRegistry.class'))
   for name in jar.namelist():
    if (name.startswith(prefix+'local/RemoteSkyCache$CachedChunkChannels') or
        name.startswith(prefix+'local/RemoteSkyCache$ReadScratch')) and name.endswith('.class'):
     path=root/'classes'/name;path.parent.mkdir(parents=True,exist_ok=True)
     content=jar.read(name)
     path.write_bytes(content)
     resource=root/'classes'/('variant-'+variant)/name
     resource.parent.mkdir(parents=True,exist_ok=True);resource.write_bytes(content)
     sources[variant+'/'+name]=hashlib.sha256(content).hexdigest()
s=(work/'SkycacheChannelAgent.java').read_text().replace('SkycacheChannelAgent','SkycacheDigestAgent')
s=s.replace('!mode.equals("baseline") && !mode.equals("channels128") && !mode.equals("channels256")',
            '!mode.equals("channels") && !mode.equals("scratch")')
s=s.replace('mode.equals("baseline") ? null : resource(mode + ".bin")','resource(mode + ".bin")')
s=s.replace('SKYCACHE_CHANNEL_AGENT','SKYCACHE_DIGEST_AGENT')
s=s.replace('        if (replacement != null) {', '''        if (name.startsWith("com/google/devtools/build/lib/skyframe/serialization/local/RemoteSkyCache$CachedChunkChannels")
            || name.startsWith("com/google/devtools/build/lib/skyframe/serialization/local/RemoteSkyCache$ReadScratch")) {
          try {
            replacement = resource("variant-" + mode + "/" + name + ".class");
          } catch (Exception e) { throw new AssertionError(e); }
        }
        if (replacement != null) {''')
(root/'SkycacheDigestAgent.java').write_text(s)
subprocess.run([str(jdk/'javac'),'-d',str(root/'classes'),str(root/'SkycacheDigestAgent.java')],check=True)
(root/'manifest.mf').write_text('Manifest-Version: 1.0\nPremain-Class: SkycacheDigestAgent\n\n')
subprocess.run([str(jdk/'jar'),'--create','--file',str(root/'agent.jar'),'--manifest',str(root/'manifest.mf'),
 '-C',str(root/'classes'),'.','-C',str(root),'registry.bin','-C',str(root),'channels.bin','-C',str(root),'scratch.bin'],check=True)
sources['registry']=hashlib.sha256((root/'registry.bin').read_bytes()).hexdigest()
sources['agent_jar']=hashlib.sha256((root/'agent.jar').read_bytes()).hexdigest()
(root/'provenance.json').write_text(json.dumps(sources,indent=2)+'\n')

# Normal-return boundaries only, deliberately excluded from primary timings.
s=(work/'SkycacheStageProbe.java').read_text().replace('SkycacheStageProbe','SkycacheColdProbe')
a=s.index('  static final Map<String, Set<String>> METHODS =');b=s.index('\n\n  public static void enter',a)
methods={
 'LocalSkycacheModule':['beforeCommand','afterCommand'],
 'LocalCacheSession':['refreshCandidates','finish','writeWitnesses','closeStores'],
 'LocalInputSnapshot':['captureNamespace','captureSource','recordRepositoryBindings','registerInheritedForFinalValidation','isUnchanged','hasSameGlobalWitnesses'],
 'LocalWitnessTable':['read','copy'],
 'RemoteSkyCache':['prepareExclusive','downloadLocked','rememberAuthentication','publishPending','uploadLocal','commit','close'],
 'SkyCacheTransport':['download'],
}
mapping='  static final Map<String, Set<String>> METHODS = Map.ofEntries(\n'+',\n'.join('      Map.entry('+json.dumps(k)+', Set.of('+','.join(json.dumps(v) for v in vs)+'))' for k,vs in methods.items())+');'
s=s[:a]+mapping+s[b:]
s=s.replace('serialization/local/','serialization/')
(root/'SkycacheColdProbe.java').write_text(s)
subprocess.run([str(jdk/'javac'),'-d',str(root/'probe-classes'),str(root/'SkycacheColdProbe.java')],check=True)
(root/'probe-manifest.mf').write_text('Manifest-Version: 1.0\nPremain-Class: SkycacheColdProbe\n\n')
subprocess.run([str(jdk/'jar'),'--create','--file',str(root/'cold-probe.jar'),'--manifest',str(root/'probe-manifest.mf'),'-C',str(root/'probe-classes'),'.'],check=True)
print(json.dumps(sources,indent=2))
