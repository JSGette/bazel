from pathlib import Path
import hashlib,io,json,subprocess,zipfile

ROOT=Path('/home/jgett/REPOS/.work/skycache-retention-2026-10-27')
BASE=Path('/home/jgett/REPOS/.work/skycache-digests-2026-10-26/build-slots-v2/bazel')
WORK=Path(__file__).parent
JDK=Path('/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin')
ROOT.mkdir(exist_ok=True)

def build_agent(destination,candidate):
 destination.mkdir(exist_ok=False)
 resources=destination/'classes';resources.mkdir()
 prefix='com/google/devtools/build/lib/skyframe/serialization/'
 common={};sources={}
 for mode,binary in [('baseline',BASE),('retention',candidate)]:
  with zipfile.ZipFile(binary) as outer:
   with zipfile.ZipFile(io.BytesIO(outer.read('A-server.jar'))) as jar:
    names=[n for n in jar.namelist() if n.endswith('.class') and (n==prefix+'AutoRegistry.class' or n.startswith(prefix+'local/RemoteChunkCache') or n.startswith(prefix+'local/RemoteSkyCache'))]
    sources[mode]={}
    for name in names:
     content=jar.read(name);sources[mode][name]=hashlib.sha256(content).hexdigest()
     p=resources/('variant-'+mode)/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content)
     if not name.startswith(prefix+'local/RemoteChunkCache'):
      if mode=='baseline':common[name]=content
      else:assert common[name]==content,name
     if '$' in name:
      p=resources/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content)
 source='''import java.lang.instrument.*;
import java.security.*;
import java.util.HexFormat;
public final class SkycacheRetentionAgent {
  public static void premain(String mode, Instrumentation instrumentation) {
    if (!mode.equals("baseline") && !mode.equals("retention")) throw new IllegalArgumentException(mode);
    instrumentation.addTransformer(new ClassFileTransformer() {
      @Override public byte[] transform(ClassLoader loader, String name, Class<?> type,
          ProtectionDomain domain, byte[] original) {
        if (!name.equals("com/google/devtools/build/lib/skyframe/serialization/AutoRegistry")
            && !name.startsWith("com/google/devtools/build/lib/skyframe/serialization/local/RemoteChunkCache")
            && !name.startsWith("com/google/devtools/build/lib/skyframe/serialization/local/RemoteSkyCache")) return null;
        try (var stream = SkycacheRetentionAgent.class.getResourceAsStream("/variant-"+mode+"/"+name+".class")) {
          if (stream == null) throw new IllegalStateException(name);
          byte[] bytes = stream.readAllBytes();
          System.err.println("SKYCACHE_RETENTION_AGENT "+mode+" "+name+" sha256="
              +HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)));
          return bytes;
        } catch (Exception e) { throw new AssertionError(e); }
      }
    });
  }
}
'''
 (destination/'SkycacheRetentionAgent.java').write_text(source)
 subprocess.run([str(JDK/'javac'),'-d',str(resources),str(destination/'SkycacheRetentionAgent.java')],check=True)
 (destination/'manifest.mf').write_text('Manifest-Version: 1.0\nPremain-Class: SkycacheRetentionAgent\n\n')
 subprocess.run([str(JDK/'jar'),'--create','--file',str(destination/'agent.jar'),'--manifest',str(destination/'manifest.mf'),'-C',str(resources),'.'],check=True)
 sources['agent_jar']=hashlib.sha256((destination/'agent.jar').read_bytes()).hexdigest()
 (destination/'provenance.json').write_text(json.dumps(sources,indent=2)+'\n')

def build_probe():
 p=ROOT/'probe';p.mkdir(exist_ok=True)
 s=(WORK/'SkycacheRetentionProbe.java').read_text();(p/'SkycacheRetentionProbe.java').write_text(s)
 subprocess.run([str(JDK/'javac'),'-d',str(p/'classes'),str(p/'SkycacheRetentionProbe.java')],check=True)
 (p/'manifest.mf').write_text('Manifest-Version: 1.0\nPremain-Class: SkycacheRetentionProbe\n\n')
 subprocess.run([str(JDK/'jar'),'--create','--file',str(p/'probe.jar'),'--manifest',str(p/'manifest.mf'),'-C',str(p/'classes'),'.'],check=True)

def harness():
 s=(WORK/'skycache_slots_benchmark.py').read_text()
 s=s.replace('from cold_pages import evict','from retention_cold_pages import evict')
 s=s.replace('skycache-digests-2026-10-26','skycache-retention-2026-10-27')
 s=s.replace('agent-slots/agent.jar','agent/agent.jar')
 s=s.replace("'channels'","'baseline'").replace("'scratch'","'retention'")
 s=s.replace("old['fixture_reuse']['retention']","old['fixture_reuse']['scratch']")
 s=s.replace("'agent/cold-probe-v2.jar'","'probe/probe.jar'")
 a=s.index("    if cohort=='diagnostic':");b=s.index("    if cohort in ['eager','confirm']:",a)
 s=s[:a]+s[b:]
 s=s.replace("'diagnostic':[('local','retention'),('remote-cold','retention'),('remote-warm','baseline'),('remote-warm','retention')]", "'diagnostic':[('remote-cold','baseline')]")
 s=s.replace("'diagnostic':[0,1,2,3]", "'diagnostic':[0]")
 s=s.replace("'cold':[('remote-cold','baseline'),('remote-cold','retention')]", "'cold':[('local','baseline'),('remote-cold','baseline'),('remote-cold','retention')]")
 s=s.replace("'cold':[0,1,1,0,0,1]", "'cold':[0,1,2,2,1,0,1,0,2]")
 s=s.replace("'diagnostic':[('remote-cold','baseline')]", "'diagnostic':[('remote-cold','retention')]")
 s=s.replace("+'=scratch'", "+'=retention'")
 s=s.replace('One frozen baseline installation with startup-only replacement of the tested 128-channel reader or its digest/buffer optimization.', 'One frozen installation and the identical tested digest/buffer reader in both arms; startup-only replacement selects the original or optimized RemoteChunkCache. No per-read probes in primary timings.')
 (WORK/'skycache_retention_benchmark.py').write_text(s);compile(s,'harness','exec')
 (WORK/'retention_cold_pages.py').write_text((WORK/'cold_pages.py').read_text().replace('skycache-digests-2026-10-26','skycache-retention-2026-10-27'))

if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--candidate',type=Path,default=BASE);parser.add_argument('--agent',default='agent');args=parser.parse_args()
 build_agent(ROOT/args.agent,args.candidate)
 build_probe();harness()
 print('Prepared',ROOT)
