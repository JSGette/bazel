from pathlib import Path
import struct,zlib,json,hashlib
R=Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25')
C=R/'datadog-six/skycache/leased-v2'
def read(id):
 p=next(C.glob('publications-v4/*/'+id+'.manifest'));b=p.read_bytes();assert hashlib.sha256(b[:-32]).digest()==b[-32:];at=4
 def utf():
  nonlocal at
  n=struct.unpack_from('>H',b,at)[0];at+=2;x=b[at:at+n].decode();at+=n;return x
 ids=[utf() for _ in range(3)];n=struct.unpack_from('>i',b,at)[0];at+=4
 for _ in range(n):utf()
 n=struct.unpack_from('>i',b,at)[0];at+=4;out={}
 for i in range(n):
  count,enc,dec,exp=struct.unpack_from('>iiiq',b,at);at+=20;digest=b[at:at+32];at+=32
  raw=(C/'objects/segmented-v1'/id/f'witnesses-{i:05d}.block').read_bytes();assert hashlib.sha256(raw).digest()==digest
  raw=zlib.decompress(raw,-15);assert len(raw)==dec;t=0;prev=b''
  for _ in range(count):
   prefix,suffix=struct.unpack_from('>HH',raw,t);t+=4;path=prev[:prefix]+raw[t:t+suffix];t+=suffix;prev=path
   listing,tag=struct.unpack_from('>BB',raw,t);t+=2
   value=raw[t:t+32].hex() if tag==0 else ('missing' if tag==1 else 'not-directory')
   if tag==0:t+=32
   out[(path.decode(),listing)]=value
  assert t==len(raw)
 return out
a=read('bb02d10ae13d207d922e75396d2e11550ec674fd7aa03052894e8672cc948f4f');b=read('e50350031be8c90ec814cb3f9cd3e2dca9e11018c0edd32f0d06ce60da4998a3')
diff=[dict(path=k[0],listing=bool(k[1]),before=a.get(k),after=b.get(k)) for k in sorted(a.keys()|b.keys()) if a.get(k)!=b.get(k)]
result=dict(before_count=len(a),after_count=len(b),changes=diff)
(R/'minimal-profile/witness-diff.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(dict(before_count=len(a),after_count=len(b),change_count=len(diff),changes=diff[:30]),indent=2))
