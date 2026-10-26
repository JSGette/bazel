from pathlib import Path
import subprocess

root=Path('/home/jgett/REPOS/.work/skycache-chain-2026-10-26')
work=Path(__file__).parent
jdk=Path('/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin')
source=(root/'SkycacheReadProbe.java').read_text().replace('SkycacheReadProbe','SkycacheReadProbeV2')
extra='''
  static final int[] capacities={16,32,64,128,256};
  static final List<LinkedHashMap<Object,Boolean>> lru=java.util.stream.IntStream.range(0,capacities.length)
      .mapToObj(i->new LinkedHashMap<Object,Boolean>(16,0.75f,true)).toList();
  static void simulate(Object digest) {
    synchronized(lru) {
      for(int i=0;i<capacities.length;i++) {
        var cache=lru.get(i);
        if(cache.get(digest)!=null) count("descriptor_lru_"+capacities[i]+".hits");
        else {
          count("descriptor_lru_"+capacities[i]+".misses");
          if(cache.size()==capacities[i]) cache.pollFirstEntry();
          cache.put(digest,true);
        }
      }
    }
  }
'''
source=source.replace('  public static void covered(Object digest, int offset, int length) {',extra+'\n  public static void covered(Object digest, int offset, int length) {\n    simulate(digest);')
(root/'SkycacheReadProbeV2.java').write_text(source)
(root/'read-probe-v2-manifest.mf').write_text('Manifest-Version: 1.0\nPremain-Class: SkycacheReadProbeV2\n\n')
subprocess.run([str(jdk/'javac'),'-d',str(root/'read-probe-v2-classes'),str(root/'SkycacheReadProbeV2.java')],check=True)
subprocess.run([str(jdk/'jar'),'--create','--file',str(root/'read-probe-v2.jar'),'--manifest',str(root/'read-probe-v2-manifest.mf'),'-C',str(root/'read-probe-v2-classes'),'.'],check=True)
s=(work/'skycache_chain_diagnostic.py').read_text().replace('.work/skycache-chain-2026-10-26/diagnostics','.work/skycache-chain-2026-10-26/warm-diagnostics').replace("ROOT.parent/'read-probe.jar'", "ROOT.parent/'read-probe-v2.jar'")
a=s.index("        run('diagnostic-local'")
b=s.index("        results['status']='passed'",a)
s=s[:a]+"        run('warmup-local','local',2,warmup=True)\n        run('diagnostic-local','local',0,warmup=True,diagnostic=True)\n        run('diagnostic-retained','remote-warm',1,warmup=True,diagnostic=True)\n"+s[b:]
(work/'skycache_chain_warm_diagnostic.py').write_text(s)
print('Prepared warmed diagnostics and bounded descriptor LRU simulation')
