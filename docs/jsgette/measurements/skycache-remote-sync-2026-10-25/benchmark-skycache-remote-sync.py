from pathlib import Path
import importlib.util, json, subprocess, shutil, fcntl, hashlib, os, stat

TASK = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
spec = importlib.util.spec_from_file_location('wide', TASK / 'work/datadog-wide.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
h = w.h
R = Path('/home/jgett/REPOS/.work/skycache-remote-sync-2026-10-25')
OLD = R.parent / 'skycache-perf-options-2026-10-25'
UP = R.parent / 'skycache-upstream-sync-2026-10-25'
lock = (R.parent / 'skyframe-measurements.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
w.ROOT = h.ROOT = R / 'datadog-six-matched-tools'
w.ROOT.mkdir(exist_ok=False)
bins = {'integrated-local': OLD / 'bin/lookup-array', 'remote-merged': R / 'bin/bazel-remote-sync',
        'vanilla': UP / 'bin/vanilla-upstream'}
caches = {name: R / ('matched-tools-cache-' + name) for name in bins}
w.NAMES = ['agent', 'cluster-agent', 'process-agent', 'trace-agent', 'security-agent', 'system-probe']
w.TARGETS = ['//cmd/' + n + ':' + n for n in w.NAMES]
h.publications = lambda: {str(p.relative_to(h.CACHE)): h.sha(p)
                         for p in sorted((h.CACHE / 'leased-v2/publications-v4').rglob('*.manifest'))}
old_tools = Path('/home/jgett/.cache/bazel/_bazel_jgett/install/80a15f52b9b3373384f22804a4dc15c5/embedded_tools')
new_tools = (R / 'remote/out-writer/external/bazel_tools').resolve(strict=True)

def inventory(root):
    result = {}
    for p in sorted(root.rglob('*')):
        s = p.lstat()
        if stat.S_ISDIR(s.st_mode):
            continue
        value = {'mode': stat.S_IMODE(s.st_mode)}
        value['link' if p.is_symlink() else 'sha256'] = os.readlink(p) if p.is_symlink() else h.sha(p)
        result[str(p.relative_to(root))] = value
    return result

old_inventory, new_inventory = inventory(old_tools), inventory(new_tools)
protocol_name = 'src/main/protobuf/skycache.proto'
assert new_inventory.keys() - old_inventory.keys() == {protocol_name}
assert {k: v for k, v in new_inventory.items() if k != protocol_name} == old_inventory
assert new_inventory[protocol_name]['sha256'] == h.sha(Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25') / protocol_name)
# The merge adds only the protocol definition to embedded tools. Pin the merged superset for
# every arm and populate both SkyCaches anew; no old publication identity is forced to match.
fixed_tools = new_tools
tools_check = {'baseline_native': str(old_tools), 'merged_native': str(new_tools),
               'common_tools_for_all_arms': str(fixed_tools),
               'identical_existing_entries': len(old_inventory),
               'only_added_file': protocol_name, 'added_sha256': new_inventory[protocol_name]['sha256']}
module = h.WS / 'MODULE.bazel.lock'
before = module.read_bytes()
assert hashlib.sha256(before).hexdigest() == '1988ae97ea0d31e99aa8683a9849a448049fe4d12f16c644b931d31553c77ee8'
source_before = h.git('diff', '--binary', 'HEAD')
module.write_bytes((R.parent / 'datadog-wide-dependencies-2026-10-25/MODULE.bazel.lock.after').read_bytes())
source_during = h.git('diff', '--binary', 'HEAD')
prior = json.loads((UP / 'datadog-six-stable/results.json').read_text())
w.P = dict(status='running', runs=[], expected_binaries=prior['expected_binaries'],
           method='Three rotated repetitions of the integrated local optimizations, remote-merged binary in local-only mode, and matched upstream vanilla. Each binary has its own populated publication cache, identical embedded_tools pinned. Fresh JVM then synchronous clean and zero graph/empty execroot/no action cache. Same resolved lock, native WSL output base on E SSD, Cache & Carry action cache/BES, no disk cache, 6GiB heap,16 jobs. Preparation excluded. This checks local-path merge regression; it does not measure the remote metadata backend.',
           binary_sha256={k: h.sha(v) for k, v in bins.items()}, caches={k: str(v) for k, v in caches.items()},
           embedded_tools=tools_check, targets=w.TARGETS, workspace_commit=h.git('rev-parse', 'HEAD'),
           started_at=h.stamp(), boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
old_command = w.command
w.command = lambda *args: old_command(*args) + ['--override_repository=bazel_tools=' + str(fixed_tools)]

def run(label, mode, implementation, prep=False):
    w.CANDIDATE = w.VANILLA = bins[implementation]
    h.CACHE = caches[implementation]
    w.run(label, mode, 'vanilla' if implementation == 'vanilla' else 'on', prep)
    row = w.P['runs'][-1]
    row['implementation'] = implementation
    row['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    assert row['boot_id'] == w.P['boot_id'], 'WSL restarted inside matrix'
    if not prep and implementation != 'vanilla':
        assert row['analysis_hits'] == 17 and row['execution_hits'] == 11, (label, 'hits')
        assert row['local-skycache.json']['writes'] == 0, (label, 'writes')
        assert not row['local-skycache.json']['read_limit_skips'], (label, 'budget')
        assert row['grpc']['method_attempt_counts'].get('build.bazel.remote.execution.v2.ActionCache/GetActionResult', 0) == 0
        if mode == 'minimal':
            assert row['grpc']['bytestream_read_calls'] == 0
        assert row['output_inventory']['archives'] == row['output_inventory']['exports'] == 0
    w.persist()

try:
    run('prepare-vanilla', 'toplevel', 'vanilla', True)
    for implementation in ['integrated-local', 'remote-merged']:
        for mode in ['minimal', 'toplevel']:
            run('prepare-' + mode + '-' + implementation, mode, implementation, True)
    orders = [['integrated-local', 'remote-merged', 'vanilla'],
              ['remote-merged', 'vanilla', 'integrated-local'],
              ['vanilla', 'integrated-local', 'remote-merged']]
    for i, order in enumerate(orders, 1):
        for mode in (['minimal', 'toplevel'] if i % 2 else ['toplevel', 'minimal']):
            for implementation in order:
                run(f'r{i}-{mode}-{implementation}', mode, implementation)
    w.P['status'] = 'passed'
except BaseException as e:
    w.P['status'] = 'failed'
    w.P['failure'] = repr(e)
    raise
finally:
    try:
        w.P['shutdown'] = h.shutdown('final-shutdown')
    except Exception as e:
        w.P['shutdown_error'] = repr(e)
    w.P['workspace_unchanged_during_matrix'] = h.git('diff', '--binary', 'HEAD') == source_during
    module.write_bytes(before)
    w.P['module_lock_restored'] = module.read_bytes() == before
    w.P['workspace_restored'] = h.git('diff', '--binary', 'HEAD') == source_before
    w.P['finished_at'] = h.stamp()
    w.persist()
    lock.close()
