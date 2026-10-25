from pathlib import Path
import importlib.util,json,hashlib,sys,stat
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex');R=Path('/home/jgett/REPOS/.work/skycache-large-tree-2026-10-25')/('datadog-'+sys.argv[1])
spec=importlib.util.spec_from_file_location('grpc',T/'work/datadog-concurrent.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
p=json.loads((R/'results.json').read_text());assert p['status']=='passed';r=p['runs'][-1];assert r['label']=='r3-toplevel-on'
out=Path(p['output_base'])/'execroot/_main/bazel-out';files=[]
for f in out.rglob('*'):
 if not stat.S_ISREG(f.lstat().st_mode):continue
 with f.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
 files.append(dict(path=str(f.relative_to(out)),bytes=f.stat().st_size,sha256=digest))
data=(R/'r3-toplevel-on.grpc.bin').read_bytes();pos=0;reads=[]
while pos<len(data):
 n,pos=c.modes.varint(data,pos);entry=c.modes.fields(data[pos:pos+n]);pos+=n
 if not entry.get(3,[b''])[0].decode().endswith('/Read'):continue
 detail=c.modes.fields(c.modes.fields(entry[4][0])[5][0]);request=c.modes.fields(detail[1][0]);resource=request[1][0].decode();parts=resource.split('/')
 digest=parts[-2];size=int(parts[-1]);matches=[f['path'] for f in files if f['sha256']==digest and f['bytes']==size]
 assert matches,resource
 reads.append(dict(sha256=digest,bytes=size,matching_output_paths=matches))
(R/'top-output-hashes.json').write_text(json.dumps(files,indent=2)+'\n')
(R/'top-output-read-verification.json').write_text(json.dumps({'run':r['label'],'all_downloads_match_materialized_outputs':True,'reads':reads},indent=2)+'\n')
print(json.dumps({'scope':sys.argv[1],'read_count':len(reads),'all_downloads_match_materialized_outputs':True}))
