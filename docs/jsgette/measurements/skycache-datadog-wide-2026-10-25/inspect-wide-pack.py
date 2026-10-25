"""Read-only bounded record inspection of an unpublished pack."""
from pathlib import Path
import collections,hashlib,json,mmap,struct
R=Path('/home/jgett/REPOS/.work/datadog-wide-2026-10-25-attempt2')
q=next((R/'skycache/leased-v1/objects/packed-v1').glob('*/data.pack'))
counts=collections.Counter();sizes=collections.Counter();witnesses={};record_count=0;checked=0
with q.open('rb') as f,mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as data:
 pos=0
 while pos<len(data):
  magic,keylen,valuelen=struct.unpack_from('>III',data,pos)
  assert magic==0x534b5001 and 0<keylen<=4096 and valuelen<=64*1024**2
  value_at=pos+44+keylen;end=value_at+valuelen;assert end<=len(data)
  prefix=data[value_at:value_at+min(4,valuelen)];counts[prefix.hex()]+=1;sizes[prefix.hex()]+=valuelen
  if prefix==b'SKE1':
   value=data[value_at:end];assert hashlib.sha256(value).digest()==data[pos+12:pos+44];checked+=1
   at=4
   def integer():
    global at
    x=struct.unpack_from('>I',value,at)[0];at+=4;return x
   def utf():
    global at
    n=struct.unpack_from('>H',value,at)[0];at+=2;s=value[at:at+n];at+=n;return s
   parents=integer();assert parents<=512
   for _ in range(parents):utf()
   n=integer();assert 0<n<=1024
   for _ in range(n):
    path=utf();listing=value[at];at+=1;digest=utf();key=(path,listing)
    assert key not in witnesses or witnesses[key]==digest
    witnesses[key]=digest
   assert at==len(value)
  record_count+=1;pos=end
groups=collections.Counter();bytes_by_group=collections.Counter();global_bytes=0;global_count=0
for (path,listing),digest in witnesses.items():
 if path.startswith(b'workspace/'):continue
 global_count+=1;n=2+len(path)+1+2+len(digest);global_bytes+=n
 text=path.decode('utf-8',errors='replace');parts=text.split('/')
 group='/'.join(parts[:2]) if parts[0]=='external' else '/'.join(parts[:8])
 groups[group]+=1;bytes_by_group[group]+=n
result={'pack':str(q),'pack_bytes':q.stat().st_size,'records':record_count,'evidence_records_sha256_verified':checked,
 'unique_witnesses_in_evidence':len(witnesses),'external_or_absolute_witnesses':global_count,
 'manifest_witness_bytes_lower_bound':global_bytes,'manifest_limit':64*1024**2,'pack_entry_limit':1000000,
 'groups_by_encoded_bytes':[{'group':k,'witnesses':groups[k],'bytes':v} for k,v in bytes_by_group.most_common(30)],
 'value_prefixes':[{'hex':k,'records':n,'bytes':sizes[k]} for k,n in counts.most_common(20)],
 'scope':'Reconstructed from serialized SKE1 dependency evidence in the unpublished data pack. Excludes any additional witnesses recorded only by final repository-binding checks.'}
(R/'pack-diagnosis.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
