from pathlib import Path
import fcntl,json,statistics,subprocess
repo=Path('/home/jgett/REPOS/bazel-skycache-concurrent')
work=Path('/home/jgett/REPOS/.work/skycache-concurrent')
exe=repo/'bazel-bin/src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalRepositoryMappingBenchmark'
output=work/'repository-mapping-benchmark-v2.json'
assert exe.is_file() and not output.exists()
protocol={'purpose':'Isolated repeated repository mappings in 1024 independently serialized roots; not whole-build performance.',
          'warmup_roots_per_process':64,'measurement_roots_per_process':1024,'entries_per_mapping':1502,
          'rounds':[],'memory_measure':'Runtime used heap difference after explicit full GC, encoded roots/store/decoded roots retained; an estimate, not RSS or peak memory.'}
with open('/home/jgett/REPOS/.work/skyframe-measurements.lock','a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    for i in range(5):
        for shared in ([False,True] if i%2==0 else [True,False]):
            argv=[str(exe),str(shared).lower()]
            result=subprocess.run(argv,cwd=repo,capture_output=True,text=True,timeout=180)
            (work/f'repository-mapping-benchmark-v2-{i}-{shared}.log').write_text(result.stdout+result.stderr)
            result.check_returncode()
            row=json.loads(result.stdout)
            row['round']=i
            assert row['distinct_mappings']==(1 if shared else 1024)
            assert row['distinct_mapping_entries']==1502*(1 if shared else 1024)
            protocol['rounds'].append(row)
            output.write_text(json.dumps(protocol,indent=2)+'\n')
            print(json.dumps(row),flush=True)
    protocol['medians']={str(shared).lower():{key:statistics.median(r[key] for r in protocol['rounds'] if r['shared']==shared)
        for key in ('encode_ms','decode_ms','root_bytes','shared_store_bytes','distinct_mappings','distinct_mapping_entries','gc_live_heap_delta_bytes')}
        for shared in (False,True)}
    output.write_text(json.dumps(protocol,indent=2)+'\n')
    print(json.dumps(protocol['medians']),flush=True)
