"""Read authenticated headers after measurement; no pack contents needed."""
from pathlib import Path
import struct,json,hashlib,sys
ROOT=Path('/home/jgett/REPOS/.work/skycache-large-tree-2026-10-25')
def inspect(scope):
 root=ROOT/('datadog-'+scope)/'skycache/leased-v2';rows=[]
 for manifest in sorted(root.glob('publications-v4/*/*.manifest')):
  data=manifest.read_bytes();assert hashlib.sha256(data[:-32]).digest()==data[-32:];at=0
  def unpack(fmt):
   nonlocal at
   n=struct.calcsize(fmt);v=struct.unpack_from(fmt,data,at);at+=n;return v
  def utf():
   nonlocal at
   n=unpack('>H')[0];v=data[at:at+n].decode();at+=n;return v
  assert unpack('>I')[0]==0x534b5334
  id,namespace,source=utf(),utf(),utf()
  parents=[utf() for _ in range(unpack('>i')[0])]
  blocks=[]
  for i in range(unpack('>i')[0]):
   count,encoded,decoded,expanded=unpack('>iiiq');digest=data[at:at+32].hex();at+=32
   blocks.append(dict(count=count,encoded_bytes=encoded,decoded_bytes=decoded,expanded_bytes=expanded,sha256=digest))
  assert at==len(data)-32
  directory=root/'objects/segmented-v1'/id;index=(directory/'index').read_bytes()
  assert hashlib.sha256(index[:-32]).digest()==index[-32:]
  magic,count=struct.unpack_from('>ii',index);assert magic==0x53475331
  segments=[]
  for i in range(count):
   bucket,entries,data_size,index_size=struct.unpack_from('>iiqi',index,8+52*i)
   segments.append(dict(bucket=bucket,entries=entries,data_bytes=data_size,index_bytes=index_size))
  rows.append(dict(id=id,manifest_bytes=len(data),parents=parents,source_digest=source,witness_blocks=len(blocks),
   witness_count=sum(x['count'] for x in blocks),witness_encoded_bytes=sum(x['encoded_bytes'] for x in blocks),
   witness_decoded_bytes=sum(x['decoded_bytes'] for x in blocks),witness_expanded_bytes=sum(x['expanded_bytes'] for x in blocks),
   segment_count=count,record_count=sum(x['entries'] for x in segments),pack_data_bytes=sum(x['data_bytes'] for x in segments),
   index_bytes=len(index)+sum(x['index_bytes'] for x in segments),max_segment_entries=max((x['entries'] for x in segments),default=0),
   cache_file_bytes=sum(f.stat().st_size for f in directory.iterdir()),blocks=blocks,segments=segments))
 result=dict(scope=scope,publications=rows,total_cache_bytes=sum(f.stat().st_size for f in root.rglob('*') if f.is_file()))
 (ROOT/('datadog-'+scope)/'publication-layout.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps([{k:v for k,v in x.items() if k not in ('blocks','segments')} for x in rows],indent=2))
if __name__=='__main__':inspect(sys.argv[1])
