from skycache_all_common import *
import re
import time

path=(OUT/'java.log').resolve(strict=True)
with path.open('rb') as f:
    size=os.fstat(f.fileno()).st_size
    head=f.read(128*1024)
    f.seek(max(0,size-128*1024));tail=f.read(128*1024)
out=ROOT/('java-log-snapshot-'+str(time.time_ns())+'.txt')
out.write_bytes(head+b'\n...[middle omitted]...\n'+tail)
print(json.dumps(dict(source=str(path),size=size,snapshot=str(out))))
lines=tail.decode(errors='replace').splitlines()
errors=[line[:1000] for line in lines if any(x in line for x in ['SEVERE','Exception:','Error:','Caused by:'])]
print('\n'.join(list(dict.fromkeys(errors))[:15]))
print('\n'.join(lines[-18:]))
