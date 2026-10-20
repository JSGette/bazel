"""Read-only storage audit of sealed local SkyCache packs, outside timed builds."""
from pathlib import Path
import hashlib
import json
import struct

work = Path('/home/jgett/REPOS/.work/skycache-concurrent')


def audit(prefix):
    cache = work / (prefix + '-cache')
    initial = json.loads((work / (prefix + '-reader-input-publications.json')).read_text())
    assert len(initial) == 1
    writer_id = Path(initial[0]).stem
    rows = []
    all_keys = set()
    required = set()
    for pack in sorted(cache.glob('objects/packed-v1/*/data.pack')):
        index = pack.parent / 'index'
        if not index.is_file():
            continue
        raw_index = index.read_bytes()
        assert hashlib.sha256(raw_index[:-32]).digest() == raw_index[-32:]
        magic, count, size = struct.unpack_from('>iiq', raw_index)
        assert magic == 0x534b4901 and size == pack.stat().st_size
        assert len(raw_index) == 16 + count * 48 + 32
        row = dict(id=pack.parent.name, role='writer' if pack.parent.name == writer_id else 'reader',
                   records=count, pack_bytes=size, index_bytes=len(raw_index), leaf_records=0,
                   leaf_record_bytes=0, leaf_payload_bytes=0, witness_count=0, parent_references=0)
        with pack.open('rb') as stream:
            seen = 0
            while header := stream.read(44):
                assert len(header) == 44
                record_magic, key_size, value_size = struct.unpack_from('>iii', header)
                assert record_magic == 0x534b5001 and 0 < key_size <= 4096
                assert 0 <= value_size <= 64 * 1024**2
                key = stream.read(key_size)
                assert len(key) == key_size
                all_keys.add(key)
                seen += 1
                if not key.startswith(b'local-input-v1/'):
                    stream.seek(value_size, 1)
                    continue
                value = stream.read(value_size)
                assert len(value) == value_size
                assert hashlib.sha256(value).digest() == header[12:44]
                row['leaf_records'] += 1
                row['leaf_record_bytes'] += 44 + key_size + value_size
                row['leaf_payload_bytes'] += value_size
                at = 0

                def integer():
                    nonlocal at
                    result = struct.unpack_from('>i', value, at)[0]
                    at += 4
                    return result

                def utf_bytes():
                    nonlocal at
                    length = struct.unpack_from('>H', value, at)[0]
                    at += 2
                    result = value[at:at + length]
                    assert len(result) == length
                    at += length
                    return result

                assert integer() == 0x534b4531
                parents = integer()
                assert 0 <= parents <= 512
                row['parent_references'] += parents
                for _ in range(parents):
                    parent = utf_bytes()
                    assert parent.startswith(b'local-input-v1/')
                    required.add(parent)
                witnesses = integer()
                assert 1 <= witnesses <= 1024
                row['witness_count'] += witnesses
                for _ in range(witnesses):
                    utf_bytes()
                    assert value[at] in (0, 1)
                    at += 1
                    utf_bytes()
                assert at == len(value)
            assert seen == count and stream.tell() == size
        rows.append(row)
    assert required <= all_keys, 'Missing persisted leaf parent references'
    return dict(prefix=prefix, packs=rows, unique_leaf_parent_keys=len(required),
                missing_leaf_parent_keys=len(required - all_keys),
                manifest_bytes=sum(p.stat().st_size for p in cache.glob('publications-v2/*/*.manifest')))


result = dict(purpose='Sealed-pack storage accounting and leaf parent closure; not timing or full dependency validation',
              before=audit('cross-jdk7'), after=audit('cross-jdk8'))
assert sum(p['leaf_records'] for p in result['before']['packs']) == 0
assert sum(p['leaf_records'] for p in result['after']['packs']) > 0
(work / 'dependency-evidence-storage.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
