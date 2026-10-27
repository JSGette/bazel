"""Read-only admission check through the unchanged production classes."""
from skycache_all_common import *
import zipfile

jar=ROOT/'server.jar'
if not jar.exists():
    with zipfile.ZipFile(CANDIDATE) as z:jar.write_bytes(z.read('A-server.jar'))
source='''package com.google.devtools.build.lib.skyframe.serialization.local;
import java.nio.file.*;
import java.util.*;
public final class InspectAllWitnesses {
  public static void main(String[] args) throws Exception {
    var inputs = new LocalInputSnapshot(Path.of(args[0]), Path.of(args[1]), Map.of());
    var manifest = Path.of(args[2]);
    var header = LocalCacheSession.readManifestHeader(manifest, manifest.getParent().getFileName().toString(), 512L*1024*1024);
    var objectRoot = Path.of(args[3]).resolve(header.id());
    var witnesses = LocalWitnessTable.read(objectRoot, header.blocks(), 512L*1024*1024);
    var contents = new LocalContentHasher();
    int mismatches=0;
    var first=new ArrayList<Map<String,Object>>();
    for (var w : witnesses) {
      if (!inputs.matchesForAdmission(w, contents)) {
        ++mismatches;
        if(first.size()<25)first.add(Map.of("path",w.path(),"listing",w.listing()));
      }
    }
    System.out.println(new com.google.gson.Gson().toJson(Map.of("witness_count",witnesses.size(),"mismatches",mismatches,"first_mismatches",first)));
  }
}
'''
(ROOT/'InspectAllWitnesses.java').write_text(source)
subprocess.run([str(JDK/'bin/javac'),'-proc:none','-cp',str(jar),'-d',str(ROOT/'helper-classes'),
                str(ROOT/'InspectAllWitnesses.java')],check=True)
manifests=list((ROOT/'local-seed/leased-v2/publications-v4').rglob('*.manifest'))
assert len(manifests)==1
object_roots=[p.parent.parent for p in (ROOT/'local-seed/leased-v2/objects').rglob('index') if p.parent.name==manifests[0].stem]
assert len(object_roots)==1
with (ROOT/'local-seed-witness-check-v3.json').open('x') as out:
    subprocess.run([str(JDK/'bin/java'),'-Xmx1g','-cp',str(ROOT/'helper-classes')+':'+str(jar),
        'com.google.devtools.build.lib.skyframe.serialization.local.InspectAllWitnesses',str(WS),str(OUT),str(manifests[0]),
        str(object_roots[0])],stdout=out,check=True,timeout=120)
print((ROOT/'local-seed-witness-check-v3.json').read_text())
