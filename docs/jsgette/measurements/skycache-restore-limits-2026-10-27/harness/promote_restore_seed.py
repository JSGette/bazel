"""Retain only the newly published native-binary seed; never bypass install identity."""
from pathlib import Path
import hashlib, json, shutil, subprocess, zipfile

task=Path('/home/jgett/REPOS/.work/skycache-restore-limits-2026-10-27')
root=task/'validation'
old=Path('/home/jgett/REPOS/.work/skycache-all-targets-2026-10-27/local-seed')
cache=root/'cache-remote-publish-remote'
result=json.loads((root/'remote-publish-results.json').read_text())
row=result['runs'][0]
assert row['exit_code']==0 and row['outputs_match'] and row['targets_match']
assert row['local-skycache']['published'] and not row['local-skycache']['failed']
assert row['remote_committed'] and result['status']=='passed'
assert row['analysis_hits']==0 and not row['cache_errors']
oldnames={p.name for p in old.rglob('*.manifest')}
manifests=[p for p in (cache/'leased-v2/publications-v4').rglob('*.manifest') if p.name not in oldnames]
assert len(manifests)==1,manifests
manifest=manifests[0]
jar=task/'server.jar'
with zipfile.ZipFile(task/'build/bazel') as z:jar.write_bytes(z.read('A-server.jar'))
source=task/'InspectRestoreManifest.java'
source.write_text('''package com.google.devtools.build.lib.skyframe.serialization.local;
import java.nio.file.*;
public final class InspectRestoreManifest {
  public static void main(String[] args) throws Exception {
    var manifest=Path.of(args[0]);
    var header=LocalCacheSession.readManifestHeader(manifest, manifest.getParent().getFileName().toString(), 1024L*1024*1024);
    System.out.println(new com.google.gson.Gson().toJson(java.util.Map.of("id",header.id(),"parents",header.parents(),"manifest_bytes",header.encodedBytes())));
  }
}
''')
jdk=Path('/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin')
classes=task/'helper-classes'
subprocess.run([str(jdk/'javac'),'-proc:none','-cp',str(jar),'-d',str(classes),str(source)],check=True)
probe=subprocess.check_output([str(jdk/'java'),'-Xmx128m','-cp',str(classes)+':'+str(jar),
    'com.google.devtools.build.lib.skyframe.serialization.local.InspectRestoreManifest',str(manifest)],text=True)
header=json.loads(probe)
assert header['parents']==[],header
seed=root/'local-seed'
assert seed.is_symlink() and seed.resolve()==old.resolve()
seed.rename(root/'local-seed-previous')
objects=cache/'leased-v2/objects/segmented-v1'/header['id']
dest=seed/'leased-v2/publications-v4'/manifest.parent.name
dest.mkdir(parents=True)
shutil.copy2(manifest,dest/manifest.name)
shutil.copytree(objects,seed/'leased-v2/objects/segmented-v1'/header['id'])
files=[p for p in objects.iterdir() if p.is_file()]
info=dict(header=header,source_label=row['label'],binary_sha256=result['binaries']['candidate']['sha256'],
    logical_bytes=sum(p.stat().st_size for p in files)+manifest.stat().st_size,
    output_manifest_sha256=row['output_manifest_sha256'],outputs_match=row['outputs_match'],targets_match=row['targets_match'],
    remote_committed=row['remote_committed'],
    reason='Rebuilt native binary has a different installation MD5 in SkyCache keys; fresh same-binary publication is required. No checksum override.')
(root/'fresh-local-seed-provenance.json').write_text(json.dumps(info,indent=2)+'\n')
print(json.dumps(info),flush=True)
