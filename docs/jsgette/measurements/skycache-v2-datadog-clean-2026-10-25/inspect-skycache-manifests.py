"""Read-only decoder/validator of this revision's publication witnesses.

Fingerprint logic mirrors LocalInputSnapshot and LocalContentHasher. Source is
not altered. This diagnostic runs outside measured build intervals.
"""
from pathlib import Path
import hashlib,json,os,stat,struct,sys,time
ROOT=Path('/home/jgett/REPOS/.work/datadog-clean-2026-10-25')
BASE=Path('/home/jgett/REPOS/.work/datadog-ssd-fresh-2026-10-25')
OUT=BASE/'out-skycache';WS=Path('/home/jgett/REPOS/datadog-agent')
CACHE=BASE/'analysis-cache'

def manifest(path):
 b=path.read_bytes();assert hashlib.sha256(b[:-32]).digest()==b[-32:]
 pos=0
 def take(n):
  nonlocal pos
  r=b[pos:pos+n];assert len(r)==n;pos+=n;return r
 def integer():return struct.unpack('>i',take(4))[0]
 def utf():
  value=take(struct.unpack('>H',take(2))[0]);assert b'\xc0\x80' not in value
  return value.decode('utf-8')
 magic=integer();identity=utf();namespace=utf();source=utf()
 parents=[utf() for _ in range(integer())]
 witnesses=[{'path':utf(),'listing':bool(take(1)[0]),'digest':utf()} for _ in range(integer())]
 assert pos==len(b)-32 and identity==path.stem and namespace==path.parent.name
 return {'file':str(path.relative_to(CACHE)),'magic':magic,'id':identity,'namespace':namespace,'source':source,'parents':parents,'witnesses':witnesses}

def kind(s):
 return 'file' if stat.S_ISREG(s.st_mode) else 'directory' if stat.S_ISDIR(s.st_mode) else 'symlink' if stat.S_ISLNK(s.st_mode) else 'other'
def update(h,value):
 b=value.encode();h.update(struct.pack('>I',len(b)));h.update(b)
def content(path):
 with path.open('rb') as f:return hashlib.file_digest(f,'sha256').digest()
def resolve(logical):
 if logical.startswith('external/'):return OUT/'external'/logical[9:]
 if logical.startswith('workspace/'):return WS/logical[10:]
 assert logical.startswith('absolute/')
 return Path(logical[9:])
def fingerprint(path,listing):
 try:s=path.lstat()
 except FileNotFoundError:return 'missing'
 h=hashlib.sha256()
 if listing:
  if not stat.S_ISDIR(s.st_mode) and not (stat.S_ISLNK(s.st_mode) and path.is_dir()):return 'not-directory'
  update(h,'listing')
  for child in sorted(path.iterdir(),key=lambda q:q.name):update(h,child.name);update(h,kind(child.lstat()))
 else:
  update(h,kind(s));update(h,stat.filemode(s.st_mode)[1:])
  if stat.S_ISREG(s.st_mode):h.update(content(path))
  elif stat.S_ISLNK(s.st_mode):
   target=Path(os.readlink(path))
   def relative(root):
    value=str(target.relative_to(root));return '' if value=='.' else value
   logical='workspace/'+relative(WS) if target.is_absolute() and target.is_relative_to(WS) else 'output/'+relative(OUT) if target.is_absolute() and target.is_relative_to(OUT) else str(target)
   update(h,logical)
   try:
    referent=path.stat();update(h,kind(referent));update(h,stat.filemode(referent.st_mode)[1:])
    if stat.S_ISREG(referent.st_mode):h.update(content(path))
   except FileNotFoundError:update(h,'missing')
  else:assert stat.S_ISDIR(s.st_mode)
 return h.hexdigest()
def binding(w):return not w['listing'] and w['path'].startswith('external/') and '/' not in w['path'][9:]

def main():
 t=time.monotonic();items=[manifest(p) for p in sorted((CACHE/'leased-v1/publications-v3').rglob('*.manifest'))]
 summary=[]
 for m in items:
  r={k:v for k,v in m.items() if k!='witnesses'}
  r.update(witness_count=len(m['witnesses']),execroot_witnesses=[w for w in m['witnesses'] if '/execroot/' in w['path']])
  if '--validate' in sys.argv:
   r['checked']=0;r['first_mismatch']=None
   for w in sorted(m['witnesses'],key=lambda w:not binding(w)):
    actual=fingerprint(resolve(w['path']),w['listing']);r['checked']+=1
    if actual!=w['digest']:
     r['first_mismatch']={**w,'actual_digest':actual,'resolved_path':str(resolve(w['path']))};break
  summary.append(r)
 differences=[]
 if len(items)>=2:
  a={(w['path'],w['listing']):w['digest'] for w in items[0]['witnesses']}
  for m in items[1:]:
   b={(w['path'],w['listing']):w['digest'] for w in m['witnesses']}
   differences.append({'base':items[0]['id'],'other':m['id'],'differences':[{'path':k[0],'listing':k[1],'before':a.get(k),'after':b.get(k)} for k in sorted(a.keys()|b.keys()) if a.get(k)!=b.get(k)]})
 result={'elapsed_seconds':time.monotonic()-t,'publications':summary,'differences':differences}
 if '--output' in sys.argv:
  path=ROOT/sys.argv[sys.argv.index('--output')+1];assert not path.exists();path.write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({'elapsed_seconds':result['elapsed_seconds'],'publications':summary,'difference_counts':[len(d['differences']) for d in differences]},indent=2))
if __name__=='__main__':main()
