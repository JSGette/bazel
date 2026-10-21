from pathlib import Path
import hashlib
import json
import shutil
import struct

work = Path('/home/jgett/REPOS/.work/skycache-concurrent')
fixture = work/'datadog-crossrevision-admission-smoke'
previous = json.loads((work/'traffic-stats-datadog-recheck-v2/results.json').read_text())
assert previous['passed'] and previous['sources_restored'] and not previous['shutdown_failures']
diagnosis = json.loads((work/'traffic-stats-seed-guard-comparison.json').read_text())
manifest = fixture/'cache'/diagnosis['new_publications'][0]
assert hashlib.sha256(manifest.read_bytes()).hexdigest() == previous['publications_after'][str(manifest.relative_to(fixture/'cache'))]
pack = fixture/'cache/objects/packed-v1'/manifest.stem/'data.pack'
keys,possible = set(),set()
with pack.open('rb') as stream:
    while header := stream.read(44):
        assert len(header)==44
        magic,key_size,value_size = struct.unpack_from('>iii',header)
        assert magic==0x534b5001 and 0<key_size<=4096 and value_size>=0
        key = stream.read(key_size)
        keys.add(key)
        prefix = stream.read(min(17,value_size))
        stream.seek(value_size-len(prefix),1)
        if len(key)==16 and len(prefix)==17 and prefix[0]==4:
            possible.add(prefix[1:])
roots = sorted(possible & keys)
assert len(roots)>512
sample = [roots[i*len(roots)//512].hex() for i in range(512)]
root_file = work/'binding-first-sampled-roots.txt'
assert not root_file.exists()
root_file.write_text('\n'.join(sample)+'\n')
environment_file = work/'admission-validation-environment-local.bin'
inventory = {str(p.relative_to(fixture/'cache')):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((fixture/'cache').rglob('*.manifest'))}
assert inventory==previous['publications_after'] and len(inventory)==10
result = {'purpose':'Admission/dependency validation with four obsolete Bazel installation publications and six current publications.',
          'cache':str(fixture/'cache'),'workspace':str(fixture/'a'),'output_base':str(fixture/'out-a'),
          'sampled_roots':str(root_file),'sampled_roots_sha256':hashlib.sha256(root_file.read_bytes()).hexdigest(),
          'population_roots':len(roots),'sampled_count':512,'source_manifest':str(manifest),
          'environment_file_local_only':str(environment_file),'environment_file_sha256':hashlib.sha256(environment_file.read_bytes()).hexdigest(),
          'publication_inventory':inventory,'note':'Exact existing namespace/environment and manifests. No input identity rewriting; private environment values remain local.'}
(work/'binding-first-fixture.json').write_text(json.dumps(result,indent=2)+'\n')
baseline = json.loads((work/'traffic-stats-after-binary.json').read_text())
assert hashlib.sha256(Path(baseline['path']).read_bytes()).hexdigest()==baseline['sha256']
destination = work/'binding-first-before.jar'
assert not destination.exists()
shutil.copy2(baseline['path'],destination)
baseline.pop('diff')
baseline.update({'mode':'before','path':str(destination),'product_commit':'9e3260773cce325396dff159eeeb8fbd37464630',
                 'baseline_provenance':'Exact Cache&Carry-built Step37 jar; production source unchanged before binding-order work.'})
(work/'binding-first-before-binary.json').write_text(json.dumps(baseline,indent=2)+'\n')
print(json.dumps({'sampled_roots':512,'population_roots':len(roots),'publications':len(inventory),'before_sha256':baseline['sha256']}))
