import argparse,pathlib,json,statistics,time,ctypes,hashlib
parser=argparse.ArgumentParser();parser.add_argument('--output',type=pathlib.Path,required=True);r=parser.parse_args().output.resolve(strict=True);d=json.loads((r/'results.json').read_text());inv=json.loads((r/'pack_inventory.json').read_text())
for mode in ['full','demand']:
 for change in ['unchanged','changed']:
  labels=[f'cache-{mode}-r{i}-{change}' for i in range(3)]
  print(mode,change,{a:[d['snapshots'][s][a]['new_bytes'] for s in labels] for a in d['algorithms']})
lib=ctypes.CDLL(str(r/'fastcdc_cut.so'));cut=lib.cut;cut.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_size_t,ctypes.c_size_t,ctypes.c_size_t,ctypes.c_uint64,ctypes.POINTER(ctypes.c_uint64)];cut.restype=ctypes.c_size_t
blobs=[pathlib.Path(p).read_bytes() for p in inv['files'].values()]
measure={}
for name,(kind,avg) in d['algorithms'].items():
 durations=[];count=0
 for rep in range(7):
  count=0;t=time.perf_counter()
  for data in blobs:
   if kind=='fixed':
    for off in range(0,len(data),avg):hashlib.sha256(data[off:off+avg]).digest();count+=1
   elif len(data)<avg*4:hashlib.sha256(data).digest();count+=1
   else:
    buf=ctypes.create_string_buffer(data);off=0
    while off<len(data):
     fp=ctypes.c_uint64();n=cut(ctypes.addressof(buf)+off,len(data)-off,avg//4,avg,avg*4,0,ctypes.byref(fp));hashlib.sha256(data[off:off+n]).digest();off+=n;count+=1
  durations.append(time.perf_counter()-t)
 measure[name]={'median_seconds':statistics.median(durations),'trials':durations,'chunks':count,'corpus_bytes':sum(map(len,blobs))}
(r/'chunk_and_hash_cpu.json').write_text(json.dumps(measure,indent=2));print(json.dumps(measure,indent=2))