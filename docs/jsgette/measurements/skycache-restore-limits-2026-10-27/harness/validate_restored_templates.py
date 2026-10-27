"""Verify both failed generated-template actions against the real full-graph publication."""
from pathlib import Path
import fcntl, json, shutil, sys

sys.path.insert(0,str(Path(__file__).parent/'restore_limits_validation'))
import skycache_all_common as common
import skycache_all_benchmark as benchmark

parent=common.ROOT
root=parent.parent/'templates'
root.mkdir(exist_ok=False)
common.ROOT=root
benchmark.ROOT=root
labels=['//pkg/security/seclwin/model:strip_go_build_windows_from_accessors_windows.go',
        '//pkg/security/seclwin/model:strip_go_build_windows_from_field_handlers_windows.go']
workload=json.loads((parent/'workload.json').read_text())
workload['targets']=labels
common.save(root/'workload.json',workload)
expected=json.loads((parent/'expected-outputs.json').read_text())
suffixes=['pkg/security/seclwin/model/accessors_windows.go','pkg/security/seclwin/model/field_handlers_windows.go']
subset={k:v for k,v in expected.items() if any(k.endswith(s) for s in suffixes)}
assert len(subset)==2,subset
common.save(root/'expected-outputs.json',subset)
common.save(root/'expected-targets.json',sorted(labels))
for name,source in [('local-seed','local-seed'),('metadata-cas-seed','metadata-cas-primer'),('catalog-seed','catalog-primer')]:
    (root/name).symlink_to(parent/source,target_is_directory=True)
with (common.REPOS/'.work/skyframe-measurements.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    runner=benchmark.Runner('templates')
    try:
        for arm in ['local-warm','remote-cold']:
            runner.run('templates-'+arm,arm,preparation=True,timeout=900)
        runner.result['status']='passed'
    except BaseException as e:
        runner.result['status']='failed';runner.result['failure']=repr(e);runner.persist();raise
    finally:
        runner.finish()
