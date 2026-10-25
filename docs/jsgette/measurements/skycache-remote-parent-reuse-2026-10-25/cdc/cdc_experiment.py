# SPDX-License-Identifier: Apache-2.0
# The embedded C translation is derived from BuildBuddy fastcdc2020 (Apache-2.0).
# See PROVENANCE.md and LICENSE.fastcdc2020; no production integration.
import argparse, pathlib, urllib.request, re, subprocess, ctypes, hashlib, json, time, struct, statistics
parser=argparse.ArgumentParser(description='Offline SkyCache fixed versus FastCDC pack experiment; writes only a new output directory.')
parser.add_argument('--data',type=pathlib.Path,required=True,help='Existing perf-demand-2 cache-* corpus directory (read-only)')
parser.add_argument('--output',type=pathlib.Path,required=True,help='New output directory; must not exist')
parser.add_argument('--source-commit',default='6a380e6eeba73d278d3e1b4a5625edb5fcefd7e5',help='Source version associated with corpus; metadata only')
args=parser.parse_args()
DATA=args.data.resolve(strict=True)
ROOT=args.output.resolve()
ROOT.mkdir(parents=True,exist_ok=False)
GO_SHA='2f01aaf2dde317b9875eea63b0c1f3bade2355dd'
REAPI_SHA='c2c07e3d718d3d490a4958344d9c3e47de0bfaf3'
SOURCE_SHA256={
 'fastcdc.go':'90f440e10dac90fc2bb818dd817bb18a90984c5b7869a8b2066024ccef3b6ee8',
 'upstream-LICENSE':'c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4',
 'fastcdc2020_test_vectors.txt':'f6bce93f38fbdc91321d8c38ac298c725f4421551f0ab926571ab5d026f457bb',
 'SekienAkashita.jpg':'d9e749d9367fc908876749d6502eb212fee88c9a94892fb07da5ef3ba8bc39ed',
}
def fetch(url,name):
    data=urllib.request.urlopen(url,timeout=30).read(); assert hashlib.sha256(data).hexdigest()==SOURCE_SHA256[name], ('reference hash mismatch',name); (ROOT/name).write_bytes(data); return data
src=fetch(f'https://raw.githubusercontent.com/buildbuddy-io/fastcdc2020/{GO_SHA}/fastcdc/fastcdc.go','fastcdc.go').decode()
fetch(f'https://raw.githubusercontent.com/buildbuddy-io/fastcdc2020/{GO_SHA}/LICENSE','upstream-LICENSE')
gear=re.search(r'var gear = \[256\]uint64\{(.*?)\n\}',src,re.S).group(1)
mask=re.search(r'var masks = \[26\]uint64\{(.*?)\n\}',src,re.S).group(1)
# Exact cut-function translation of pinned upstream Go, including its 2-byte scan.
c=r'''
/* SPDX-License-Identifier: Apache-2.0
 * Derived from BuildBuddy fastcdc2020/fastcdc/fastcdc.go, commit
 * 2f01aaf2dde317b9875eea63b0c1f3bade2355dd (see upstream-LICENSE).
 * Modifications: cut routine translated from Go to C, streaming wrapper omitted,
 * parameters passed directly; original GEAR and MASKS tables retained.
 * Generated solely for the 2026-10-25 offline SkyCache experiment.
 */
#include <stdint.h>
#include <stddef.h>
static const uint64_t gear[256]={GEAR};
static const uint64_t masks[26]={MASK};
size_t cut(const unsigned char *data,size_t n,size_t min,size_t avg,size_t max,uint64_t seed,uint64_t *result){
  if(n<=min){*result=0;return n;}
  size_t end=n<max?n:max, normal=avg<end?avg:end;
  unsigned bits=0; for(size_t v=avg;v>1;v>>=1)bits++;
  uint64_t ms=masks[bits+2],ml=masks[bits-2],fp=0;
  size_t start=min&~(size_t)1,mid=normal&~(size_t)1,last=end&~(size_t)1;
  for(size_t i=start;i<mid;i+=2){
    fp=(fp<<2)+((gear[data[i]]^seed)<<1);
    if((fp&(ms<<1))==0){*result=fp;return i;}
    fp+=gear[data[i+1]]^seed;
    if((fp&ms)==0){*result=fp;return i+1;}
  }
  for(size_t i=mid;i<last;i+=2){
    fp=(fp<<2)+((gear[data[i]]^seed)<<1);
    if((fp&(ml<<1))==0){*result=fp;return i;}
    fp+=gear[data[i+1]]^seed;
    if((fp&ml)==0){*result=fp;return i+1;}
  }
  *result=fp;return end;
}
'''.replace('GEAR',gear+'\n').replace('MASK',mask+'\n')
(ROOT/'fastcdc_cut.c').write_text(c)
subprocess.run(['gcc','-O3','-shared','-fPIC',str(ROOT/'fastcdc_cut.c'),'-o',str(ROOT/'fastcdc_cut.so')],check=True)
lib=ctypes.CDLL(str(ROOT/'fastcdc_cut.so')); cut=lib.cut
cut.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_size_t,ctypes.c_size_t,ctypes.c_size_t,ctypes.c_uint64,ctypes.POINTER(ctypes.c_uint64)];cut.restype=ctypes.c_size_t
def split(data,avg,minimum=None,maximum=None,seed=0,skip_small=False):
    minimum=minimum or avg//4;maximum=maximum or avg*4
    if skip_small and len(data)<maximum:return [(0,len(data),0)] if data else []
    buf=ctypes.create_string_buffer(data); off=0;out=[]
    while off<len(data):
        fp=ctypes.c_uint64();n=cut(ctypes.addressof(buf)+off,len(data)-off,minimum,avg,maximum,seed,ctypes.byref(fp))
        assert n>0 and n<=maximum
        out.append((off,n,fp.value));off+=n
    return out
vectors=fetch(f'https://raw.githubusercontent.com/bazelbuild/remote-apis/{REAPI_SHA}/build/bazel/remote/execution/v2/fastcdc2020_test_vectors.txt','fastcdc2020_test_vectors.txt').decode()
fixture=fetch('https://raw.githubusercontent.com/nlfiedler/fastcdc-rs/49c3d0b/test/fixtures/SekienAkashita.jpg','SekienAkashita.jpg')
assert hashlib.sha256(fixture).hexdigest()=='d9e749d9367fc908876749d6502eb212fee88c9a94892fb07da5ef3ba8bc39ed'
expected={};seed=None
for line in vectors.splitlines():
    if line.startswith('# Seed:'):seed=int(line.split(':')[1]);expected[seed]=[]
    elif line and not line.startswith('#'):
        a,b,h,f=line.split();expected[seed].append((int(a),int(b),h,int(f)))
for seed,rows in expected.items():
    got=[(a,b,hashlib.sha256(fixture[a:a+b]).hexdigest(),f) for a,b,f in split(fixture,16384,4096,65535,seed)]
    assert got==rows,(seed,got,rows)
algorithms={'fixed1m':('fixed',1048576),'fixed256k':('fixed',262144),'fixed64k':('fixed',65536),'fastcdc256k':('cdc',262144),'fastcdc64k':('cdc',65536)}
blobs={};snapshots={};files={};records={};times={k:[] for k in algorithms}
for snapshot in sorted(DATA.glob('cache-*')):
    refs=[]
    for path in sorted((snapshot/'leased-v2/objects/segmented-v1').glob('*/*.pack')):
        data=path.read_bytes();h=hashlib.sha256(data).hexdigest();refs.append((str(path.relative_to(snapshot)),h))
        if h not in blobs:
            blobs[h]=data;files[h]=str(path);off=0;rr=[]
            while off<len(data):
                magic,kl,vl=struct.unpack_from('>III',data,off);assert magic==0x534b5001
                key=data[off+44:off+44+kl];val=data[off+44+kl:off+44+kl+vl]
                assert hashlib.sha256(val).digest()==data[off+12:off+44]
                rr.append((off,44+kl+vl,key.hex(),hashlib.sha256(val).hexdigest()));off+=44+kl+vl
            assert off==len(data);records[h]=rr
    snapshots[snapshot.name]=refs
chunks={name:{} for name in algorithms}
for name,(typ,size) in algorithms.items():
    for h,data in blobs.items():
        start=time.perf_counter()
        bounds=[(o,min(size,len(data)-o),0) for o in range(0,len(data),size)] if typ=='fixed' else split(data,size,skip_small=True)
        times[name].append(time.perf_counter()-start)
        chunks[name][h]=[(off,n,hashlib.sha256(data[off:off+n]).hexdigest()) for off,n,_ in bounds]
def chunkset(refs,name):
    return {(d,n) for _,h in refs for _,n,d in chunks[name][h]}
def stats(refs,name,baseline=()):
    allc=[(d,n) for _,h in refs for _,n,d in chunks[name][h]];unique=set(allc);old=chunkset(baseline,name)
    return {'pack_bytes':sum(len(blobs[h]) for _,h in refs),'pack_files':len(refs),'chunk_count':len(allc),'unique_chunk_count':len(unique),'unique_bytes':sum(n for _,n in unique),'new_bytes':sum(n for d,n in unique-old),'new_chunks':len(unique-old),'reused_bytes':sum(n for d,n in unique&old)}
result={'method':'Offline exact-content chunk simulation on immutable actual publication packs; CAS assumed prepopulated with primer chunks for same algorithm. Chunk hashes SHA256. No RPC/wire or live build timing. FastCDC cut C translation of pinned Go; verified every official REAPI fixture chunk offset/length/hash/fingerprint for all supplied seeds. REAPI small-blob SHOULD bypass applied: files smaller than 4*avg kept whole.','source_commit':args.source_commit,'upstream_go':GO_SHA,'reapi':REAPI_SHA,'vectors_seeds':list(expected),'unique_pack_files':len(blobs),'unique_pack_bytes':sum(map(len,blobs.values())),'algorithms':algorithms,'snapshots':{},'cross_primer':{},'record_overlap':{},'random_record_model':{},'cpu':{}}
for label,refs in snapshots.items():
    mode='demand' if 'demand' in label else 'full';baseline=snapshots['cache-primer-'+mode]
    result['snapshots'][label]={name:stats(refs,name,baseline) for name in algorithms}
for name in algorithms:
    result['cross_primer'][name]=stats(snapshots['cache-primer-demand'],name,snapshots['cache-primer-full'])
    # Warm memory bound-selection cost; hashing excluded equally, no IO or JVM.
    sample=list(blobs.values());dur=[]
    for r in range(7):
        start=time.perf_counter()
        typ,size=algorithms[name]
        for data in sample:
            b=[(o,min(size,len(data)-o)) for o in range(0,len(data),size)] if typ=='fixed' else split(data,size,skip_small=True)
        dur.append(time.perf_counter()-start)
    result['cpu'][name]={'boundary_seconds_median':statistics.median(dur),'boundary_seconds_all':dur,'MiB_per_sec':sum(map(len,sample))/1048576/statistics.median(dur),'note':'Includes ctypes and one input copy per CDC file; boundary selection only, SHA256 and disk/network excluded; not production Java throughput.'}
    amplification=[];bytes_fetched=0;bytes_requested=0;crossing=0
    for _,h in snapshots['cache-primer-demand']:
        cc=chunks[name][h]
        for off,n,_,_ in records[h]:
            touched=[length for start,length,d in cc if start<off+n and start+length>off]
            fetched=sum(touched);amplification.append(fetched/n);bytes_fetched+=fetched;bytes_requested+=n;crossing+=len(touched)>1
    amplification.sort()
    result['random_record_model'][name]={'records':len(amplification),'weighted_amplification':bytes_fetched/bytes_requested,'median_ratio':statistics.median(amplification),'p95_ratio':amplification[int(.95*(len(amplification)-1))],'cross_chunk_records':crossing,'note':'All primer-demand records queried independently from cold chunk cache; model, not measured real demand trace.'}
a={(key,v):n for _,h in snapshots['cache-primer-full'] for _,n,key,v in records[h]};b={(key,v):n for _,h in snapshots['cache-primer-demand'] for _,n,key,v in records[h]}
result['record_overlap']={'full_records':len(a),'demand_records':len(b),'same_key_value_records':len(a.keys()&b.keys()),'demand_bytes_reusable_by_exact_record':sum(b[k] for k in a.keys()&b.keys()),'demand_unique_record_bytes':sum(b.values())}
# Control: a one-byte insertion halfway into actual largest pack, clearly synthetic.
h=max(blobs,key=lambda h:len(blobs[h]));data=blobs[h];modified=data[:len(data)//2]+b'X'+data[len(data)//2:];result['insertion_control']={'base_pack':files[h],'base_bytes':len(data),'modified_bytes':len(modified),'algorithms':{}}
for name,(typ,size) in algorithms.items():
    bounds=[(o,min(size,len(modified)-o),0) for o in range(0,len(modified),size)] if typ=='fixed' else split(modified,size,skip_small=True)
    new={(hashlib.sha256(modified[o:o+n]).hexdigest(),n) for o,n,_ in bounds};old={(d,n) for o,n,d in chunks[name][h]}
    result['insertion_control']['algorithms'][name]={'new_bytes':sum(n for d,n in new-old),'new_chunks':len(new-old),'chunks':len(new)}
(ROOT/'results.json').write_text(json.dumps(result,indent=2))
(ROOT/'pack_inventory.json').write_text(json.dumps({'snapshots':snapshots,'files':files,'pack_sha256':{h:len(b) for h,b in blobs.items()}},indent=2))
print(json.dumps({'vectors_seeds':result['vectors_seeds'],'unique_pack_files':len(blobs),'unique_pack_bytes':result['unique_pack_bytes'],'cross_primer':result['cross_primer'],'record_overlap':result['record_overlap'],'random_record_model':result['random_record_model'],'cpu':result['cpu'],'changed':{k:v for k,v in result['snapshots'].items() if 'r0' in k},'insertion_control':result['insertion_control']},indent=2))