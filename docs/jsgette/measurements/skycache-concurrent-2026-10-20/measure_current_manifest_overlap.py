from pathlib import Path
import hashlib
import json
import struct

work=Path('/home/jgett/REPOS/.work/skycache-concurrent')
root=work/'datadog-crossrevision-witness-jfr/cache'
rows=[]
union=set()
lists={}
for path in sorted(root.rglob('*.manifest')):
    data=path.read_bytes()
    offset=0
    def integer():
        global offset
        value=struct.unpack_from('>i',data,offset)[0]
        offset+=4
        return value
    def utf():
        global offset
        count=struct.unpack_from('>H',data,offset)[0]
        offset+=2
        value=data[offset:offset+count]
        offset+=count
        return value
    assert integer()==0x534b5333
    identity=utf().decode('ascii')
    namespace=utf().decode('ascii')
    source=utf().decode('ascii')
    for _ in range(integer()):
        utf()
    count=integer()
    start_list=offset
    witnesses=set()
    for _ in range(count):
        start=offset
        utf()
        assert data[offset] in (0,1)
        offset+=1
        utf()
        witnesses.add(hashlib.sha256(data[start:offset]).digest())
    list_hash=hashlib.sha256(data[start_list:offset]).hexdigest()
    lists.setdefault(list_hash,witnesses)
    rows.append({'publication':identity,'witness_count':count,'unique_witnesses':len(witnesses),
        'list_sha256':list_hash,'overlap_with_preceding_publications':len(witnesses & union)})
    union.update(witnesses)
result={'scope':'Immutable manifest witness identities only, hashed from exact encoded path/operation/digest bytes. Not a timing measurement.',
    'publications':rows,'distinct_complete_lists':len(lists),
    'unique_witness_union':len(union),'witness_checks_after_existing_complete_list_memo':sum(len(s) for s in lists.values())}
(work/'witness-current-manifest-overlap.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
