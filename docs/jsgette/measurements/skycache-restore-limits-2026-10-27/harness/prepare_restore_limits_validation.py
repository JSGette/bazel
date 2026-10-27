"""Freeze a new validation harness; preserve the original failed cohort and seeds."""
from pathlib import Path
import hashlib, json, shutil, subprocess

work=Path(__file__).parent
previous=Path('/home/jgett/REPOS/.work/skycache-all-targets-2026-10-27')
task=Path('/home/jgett/REPOS/.work/skycache-restore-limits-2026-10-27')
root=task/'validation'
root.mkdir(exist_ok=False)
build=json.loads((task/'build/result.json').read_text())
assert build['exit_code']==0 and build['cases']==821
for name in ['workload.json','public-workload.json','expected-outputs.json','expected-targets.json']:
    shutil.copy2(previous/name,root/name)
for name in ['tools','local-seed']:
    (root/name).symlink_to(previous/name,target_is_directory=True)
dest=work/'restore_limits_validation'
dest.mkdir(exist_ok=False)
common=(work/'skycache_all_common.py').read_text()
common=common.replace("ROOT=REPOS/'.work/skycache-all-targets-2026-10-27'",'ROOT=Path('+repr(str(root))+')')
common=common.replace("CANDIDATE=REPOS/'.work/skycache-retention-2026-10-27/build-advisory/bazel'",'CANDIDATE=Path('+repr(str(task/'build/bazel'))+')')
common=common.replace("str(ROOT/'ordinary-action-disk-cache')",repr(str(previous/'ordinary-action-disk-cache')))
common=common.replace("toolroot=ROOT/'tools'",'toolroot=Path('+repr(str(previous/'tools'))+')')
(dest/'skycache_all_common.py').write_text(common)
benchmark=(work/'skycache_all_benchmark.py').read_text()
benchmark=benchmark.replace('cc20319bc83ccb68f20284f2462eaa0453e69c4e5b17c4be2ebba36510e0194c',build['bazel_sha256'])
benchmark=benchmark.replace('max_transfer_size=4097M','max_transfer_size=8193M')
benchmark=benchmark.replace("'stabilize','measure'","'stabilize','measure','validate'")
benchmark=benchmark.replace("        else:\n            arms=args.arms", "        elif args.mode=='validate':\n            for arm in args.arms or ['local-warm']:\n                runner.run(args.tag+'-'+arm,arm,preparation=True,timeout=1200)\n        else:\n            arms=args.arms")
(dest/'skycache_all_benchmark.py').write_text(benchmark)
shutil.copy2(work/'retention_cold_pages.py',dest/'retention_cold_pages.py')
# The worker is a Java launcher. Record its actual runfile jar hashes, not just its script.
worker=Path('/home/jgett/REPOS/.work/skycache-remote-mvp/build/execroot/_main/bazel-out/k8-fastbuild/bin/src/tools/remote/worker')
sha=lambda p:hashlib.file_digest(Path(p).open('rb'),'sha256').hexdigest()
jars={str(p.relative_to(worker.parent/'worker.runfiles')):sha(p) for p in (worker.parent/'worker.runfiles').rglob('*.jar')}
provenance=dict(build_result=build,worker_launcher_sha256=sha(worker),worker_runfile_jars=jars,
    seed_source=str(previous/'local-seed'),ordinary_action_cache=str(previous/'ordinary-action-disk-cache'),
    purpose='Single excluded full-graph correctness checks, not a new performance matrix.',
    harness_sha256={p.name:sha(p) for p in dest.glob('*.py')})
(root/'validation-provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
print(json.dumps(dict(root=str(root),harness=str(dest),worker_jars=len(jars))),flush=True)
