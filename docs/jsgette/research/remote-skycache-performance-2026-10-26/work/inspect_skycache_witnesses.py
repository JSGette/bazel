#!/usr/bin/env python3
"""Read-only point-in-time witness alias inventory; never accepts cache hits."""
from pathlib import Path
import collections
import hashlib
import json
import os
import stat
import struct
import time
import zlib

ROOT=Path('/home/jgett/REPOS/.work/skycache-preliminary-2026-10-26')
PRIOR=Path('/home/jgett/REPOS/.work/skycache-retained-chunks-2026-10-25/agent-matrix')


def decode(path):
    data=zlib.decompress(path.read_bytes(),-15);at=0;previous=b''
    while at<len(data):
        prefix,suffix=struct.unpack_from('>HH',data,at);at+=4
        assert prefix<=len(previous)
        name=previous[:prefix]+data[at:at+suffix];at+=suffix
        listing,tag=data[at:at+2];at+=2
        assert tag in [0,1,2]
        digest=data[at:at+32].hex() if tag==0 else ['','missing','not-directory'][tag]
        if tag==0:at+=32
        yield name.decode(),bool(listing),digest
        previous=name
    assert at==len(data)


def main():
    started=time.monotonic()
    prior=json.loads((PRIOR/'results.json').read_text())
    workspace=Path(prior['workspace']);external=Path(prior['output_base'])/'external'
    files=sorted((ROOT/'cache-r1-remote/leased-v2/objects').rglob('witnesses-*.block'))
    parent_hashes={hashlib.sha256(x.read_bytes()).hexdigest()
                   for x in (PRIOR/'cache-primer/leased-v2/objects').rglob('witnesses-*.block')}
    counts=collections.Counter();versions=collections.Counter();physical_sizes={};sizes=collections.Counter()
    kinds=collections.Counter();witness_count=0;expected_keys=set();replay_paths=[]
    for block in files:
        for name,listing,digest in decode(block):
            witness_count+=1;counts[name.split('/',1)[0]]+=1
            key=(name,listing);assert key not in expected_keys;expected_keys.add(key)
            if listing:
                kinds['listing']+=1;continue
            if name.startswith('absolute/'):
                path=Path(name[len('absolute/'):])
            elif name.startswith('external/'):
                path=external/name[len('external/'):]
            elif name.startswith('workspace/'):
                path=workspace/name[len('workspace/'):]
            else:
                raise AssertionError('unknown root')
            try:
                link=path.lstat();current=path.stat()
            except FileNotFoundError:
                kinds['missing_now']+=1;continue
            if not stat.S_ISREG(current.st_mode):
                kinds['nonregular_now']+=1;continue
            kinds['symlink_to_regular' if stat.S_ISLNK(link.st_mode) else 'regular']+=1
            replay_paths.append(str(path).encode())
            version=(current.st_dev,current.st_ino,current.st_ctime_ns,current.st_mtime_ns,current.st_size)
            versions[version]+=1;physical_sizes[version]=current.st_size
            sizes['logical_bytes']+=current.st_size
    small={k:v for k,v in versions.items() if physical_sizes[k]<1024**2}
    large={k:v for k,v in versions.items() if physical_sizes[k]>=1024**2}
    def summarize(data):
        return dict(observations=sum(data.values()),unique_file_versions=len(data),
            alias_observations=sum(v-1 for v in data.values()),
            logical_bytes=sum(physical_sizes[k]*v for k,v in data.items()),
            distinct_version_bytes=sum(physical_sizes[k] for k in data),
            alias_bytes=sum(physical_sizes[k]*(v-1) for k,v in data.items()),
            max_aliases=max(data.values(),default=0))
    result=dict(witnesses=witness_count,roots=counts,kinds=kinds,regular_content=summarize(versions),
        below_current_1MiB_reuse_threshold=summarize(small),at_or_above_1MiB=summarize(large),
        blocks=len(files),block_bytes=sum(p.stat().st_size for p in files),
        identical_parent_blocks=sum(hashlib.sha256(p.read_bytes()).hexdigest() in parent_hashes for p in files),
        seconds=time.monotonic()-started,
        interpretation='Read-only stat snapshot after diagnostic runs. Aliases identify a possible within-pass '
            'content-digest reuse opportunity, not measured avoided I/O. Does not verify content or authorize '
            'reusing old validation. Symlink, permissions, listing and negative-witness checks remain necessary.')
    with (ROOT/'hash-replay-paths.bin').open('wb') as output:
        output.write(struct.pack('>I',len(replay_paths)))
        for path in replay_paths:
            output.write(struct.pack('>I',len(path)));output.write(path)
    result['replay_paths_sha256']=hashlib.sha256((ROOT/'hash-replay-paths.bin').read_bytes()).hexdigest()
    (ROOT/'witness-alias-inventory.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
