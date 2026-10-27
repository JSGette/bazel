from pathlib import Path
work=Path(__file__).parent
s=(work/'build_digests.py').read_text().replace('skycache-digests-2026-10-26','skycache-retention-2026-10-27')
start=s.index('changed=[');end=s.index('subprocess.run(',start)
changed=['src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/RemoteChunkCache.java',
 'src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/RemoteChunkCacheTest.java',
 'src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/RemoteSkyCacheTest.java']
s=s[:start]+'changed='+repr(changed)+'\n'+s[end:]
(work/'build_retention.py').write_text(s)
