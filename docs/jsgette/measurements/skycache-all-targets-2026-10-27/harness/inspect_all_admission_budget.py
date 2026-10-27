from skycache_all_common import *
import shutil

source='''package com.google.devtools.build.lib.skyframe.serialization.local;
import java.nio.file.*;
import java.util.*;
public final class InspectAllAdmissionBudget {
  public static void main(String[] args) throws Exception {
    var manifest = Path.of(args[0]);
    var header = LocalCacheSession.readManifestHeader(manifest, manifest.getParent().getFileName().toString(), 1024L*1024*1024);
    long witnessBytes=LocalWitnessTable.metadataBytes(header.blocks());
    long metadataBytes=header.encodedBytes()+witnessBytes;
    var results=new ArrayList<Map<String,Object>>();
    for(int mb : new int[]{256,512,1024}) {
      long remaining=mb*1024L*1024-metadataBytes;
      try(var store=SegmentedFingerprintValueStore.open(Path.of(args[1]),header.id(),remaining)) {
        results.add(Map.of("budget_mib",mb,"admitted",true,"index_bytes",store.indexBytes(),"total_metadata_bytes",metadataBytes+store.indexBytes()));
      } catch(java.io.IOException e) {
        results.add(Map.of("budget_mib",mb,"admitted",false,"error",e.getMessage()));
      }
    }
    System.out.println(new com.google.gson.Gson().toJson(Map.of("witness_admission_bytes",witnessBytes,"manifest_bytes",header.encodedBytes(),"checks",results)));
  }
}
'''
(ROOT/'InspectAllAdmissionBudget.java').write_text(source)
jar=ROOT/'server.jar'
subprocess.run([str(JDK/'bin/javac'),'-proc:none','-cp',str(jar),'-d',str(ROOT/'helper-classes'),
    str(ROOT/'InspectAllAdmissionBudget.java')],check=True)
manifest=next((ROOT/'local-seed/leased-v2/publications-v4').rglob('*.manifest'))
objects=next(p.parent.parent for p in (ROOT/'local-seed/leased-v2/objects').rglob('index') if p.parent.name==manifest.stem)
result=subprocess.run([str(JDK/'bin/java'),'-Xmx512m','-cp',str(ROOT/'helper-classes')+':'+str(jar),
    'com.google.devtools.build.lib.skyframe.serialization.local.InspectAllAdmissionBudget',str(manifest),str(objects)],
    check=True,capture_output=True,text=True)
probe=json.loads(result.stdout)
save(ROOT/'local-admission-budget.json',probe)
d=json.loads((ROOT/'local-matrix-results.json').read_text());r=d['runs'][-1]
assert d['status']=='failed' and r['exit_code']==0 and r['analysis_hits']==0 and r['outputs_match']
save(ROOT/'default-local-availability.json',dict(status='no_reuse_at_default_metadata_budget',
    result_sha256=sha(ROOT/'local-matrix-results.json'),probe=probe,
    wall_s=r['wall_s'],local=r['local-skycache'],validation=r['local-skycache-validation'],
    witness_check=json.loads((ROOT/'local-seed-witness-check-v3.json').read_text()),
    diagnostic_note='This excluded warmup includes one thread snapshot and concurrent read-only witness diagnostics; its duration is diagnostic, not a primary performance sample.'))
snapshot=ROOT/'metadata-256-attempt-harness';snapshot.mkdir()
for name in ['skycache_all_common.py','skycache_all_benchmark.py','run_all_local_measurements.py']:
    shutil.copy2(Path(__file__).parent/name,snapshot/name)
print(json.dumps(probe,indent=2))
