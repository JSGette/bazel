from pathlib import Path
import json,subprocess
root=Path('/home/jgett/REPOS/.work/skycache-digests-2026-10-26/agent')
jdk=Path('/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin')
s=(root/'SkycacheColdProbe.java').read_text()
s=s.replace('SkycacheColdProbe','SkycacheColdProbeV2')
s=s.replace('Map.ofEntries(', 'Map.ofEntries(\n      Map.entry("RemoteChunkCache", Set.of("store")),')
s=s.replace('+ Instant.now().toEpochMilli()', '+ Instant.now().toEpochMilli() + ",\\\"begin_ns\\\":" + begin + ",\\\"end_ns\\\":" + end')
(root/'SkycacheColdProbeV2.java').write_text(s)
subprocess.run([str(jdk/'javac'),'-d',str(root/'probe-v2-classes'),str(root/'SkycacheColdProbeV2.java')],check=True)
(root/'probe-v2-manifest.mf').write_text('Manifest-Version: 1.0\nPremain-Class: SkycacheColdProbeV2\n\n')
subprocess.run([str(jdk/'jar'),'--create','--file',str(root/'cold-probe-v2.jar'),'--manifest',str(root/'probe-v2-manifest.mf'),'-C',str(root/'probe-v2-classes'),'.'],check=True)
