from pathlib import Path
import importlib.util, json, shutil, fcntl, hashlib, os, stat
TASK = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
spec = importlib.util.spec_from_file_location('wide', TASK / 'work/datadog-wide.py')
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w); h = w.h
PARENT = Path('/home/jgett/REPOS/.work/skycache-remote-sync-2026-10-25')
R = PARENT / 'update'
OLD = PARENT.parent / 'skycache-perf-options-2026-10-25'
lock = (PARENT.parent / 'skyframe-measurements.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX)
w.ROOT = h.ROOT = R / 'datadog-six'
w.ROOT.mkdir(exist_ok=False)
bins = {'integrated-local': OLD / 'bin/lookup-array', 'remote-merged': R / 'bin/bazel-remote-sync'}
caches = {name: R / ('datadog-cache-' + name) for name in bins}
for name in ['objects', 'publications-v4']:
    shutil.copytree(PARENT / 'matched-tools-cache-integrated-local/leased-v2' / name,
                    caches['integrated-local'] / 'leased-v2' / name, copy_function=os.link)
w.NAMES = ['agent', 'cluster-agent', 'process-agent', 'trace-agent', 'security-agent', 'system-probe']
w.TARGETS = ['//cmd/' + n + ':' + n for n in w.NAMES]
h.publications = lambda: {str(p.relative_to(h.CACHE)): h.sha(p)
                         for p in sorted((h.CACHE / 'leased-v2/publications-v4').rglob('*.manifest'))}
fixed_tools = (PARENT / 'remote/out-writer/external/bazel_tools').resolve(strict=True)
new_tools = (R / 'remote/out-writer/external/bazel_tools').resolve(strict=True)
def inventory(root):
    result = {}
    for p in sorted(root.rglob('*')):
        s = p.lstat()
        if stat.S_ISDIR(s.st_mode): continue
        value = {'mode': stat.S_IMODE(s.st_mode)}
        value['link' if p.is_symlink() else 'sha256'] = os.readlink(p) if p.is_symlink() else h.sha(p)
        result[str(p.relative_to(root))] = value
    return result
assert inventory(fixed_tools) == inventory(new_tools)
module = h.WS / 'MODULE.bazel.lock'; before = module.read_bytes()
assert hashlib.sha256(before).hexdigest() == '1988ae97ea0d31e99aa8683a9849a448049fe4d12f16c644b931d31553c77ee8'
source_before = h.git('diff', '--binary', 'HEAD')
module.write_bytes((PARENT.parent / 'datadog-wide-dependencies-2026-10-25/MODULE.bazel.lock.after').read_bytes())
source_during = h.git('diff', '--binary', 'HEAD')
prior = json.loads((PARENT / 'datadog-six-matched-tools/results.json').read_text())
w.P = dict(status='running', runs=[], expected_binaries=prior['expected_binaries'],
           method='Final parent-reuse merge versus the optimized local baseline. Three alternating repetitions per arm and minimal/toplevel policy after four excluded preparations. Local SkyCache enabled; remote metadata endpoint unset. Cache & Carry action cache/BES, no disk cache,16 jobs,6GiB JVM. Fresh JVM then synchronous clean, verified zero graph/empty execroot/no action cache. Same source/lock/output base/common embedded tools; each binary has its own populated SkyCache. Process-tree PSS sampled every100ms. Historical/intermediate vanilla timings are not pooled into this comparison.',
           binary_sha256={k: h.sha(v) for k, v in bins.items()}, caches={k:str(v) for k,v in caches.items()},
           embedded_tools=dict(common=str(fixed_tools), final_native=str(new_tools), identical_entries=len(inventory(fixed_tools))),
           targets=w.TARGETS, workspace_commit=h.git('rev-parse','HEAD'),
           started_at=h.stamp(), boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
old_command = w.command
w.command = lambda *args: old_command(*args) + ['--override_repository=bazel_tools=' + str(fixed_tools)]
def run(label, mode, implementation, prep=False):
    w.CANDIDATE = bins[implementation]; h.CACHE = caches[implementation]
    w.run(label, mode, 'on', prep)
    row = w.P['runs'][-1]; row['implementation'] = implementation
    row['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    assert row['boot_id'] == w.P['boot_id']
    if not prep:
        assert row['analysis_hits'] == 17 and row['execution_hits'] == 11, (label, 'hits')
        assert row['local-skycache.json']['writes'] == 0, (label, 'writes')
        assert not row['local-skycache.json']['read_limit_skips'], (label, 'budget')
        assert row['grpc']['method_attempt_counts'].get('build.bazel.remote.execution.v2.ActionCache/GetActionResult',0) == 0
        if mode == 'minimal': assert row['grpc']['bytestream_read_calls'] == 0
        assert row['output_inventory']['archives'] == row['output_inventory']['exports'] == 0
    w.persist()
try:
    for implementation in bins:
        for mode in ['minimal','toplevel']:
            run('prepare-' + mode + '-' + implementation, mode, implementation, True)
    for i in range(1,4):
        for mode in (['minimal','toplevel'] if i%2 else ['toplevel','minimal']):
            for implementation in (list(bins) if i%2 else list(reversed(bins))):
                run(f'r{i}-{mode}-{implementation}', mode, implementation)
    w.P['status'] = 'passed'
except BaseException as e:
    w.P['status'] = 'failed'; w.P['failure'] = repr(e); raise
finally:
    try: w.P['shutdown'] = h.shutdown('final-shutdown')
    except Exception as e: w.P['shutdown_error'] = repr(e)
    w.P['workspace_unchanged_during_matrix'] = h.git('diff','--binary','HEAD') == source_during
    module.write_bytes(before)
    w.P['module_lock_restored'] = module.read_bytes() == before
    w.P['workspace_restored'] = h.git('diff','--binary','HEAD') == source_before
    w.P['finished_at'] = h.stamp(); w.persist(); lock.close()
