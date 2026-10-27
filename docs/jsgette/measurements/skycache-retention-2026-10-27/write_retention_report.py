from pathlib import Path
import json
ROOT=Path('/home/jgett/REPOS/.work/skycache-retention-2026-10-27')
REPO=Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
DEST=REPO/'docs/jsgette/measurements/skycache-retention-2026-10-27'
WORKSPACE=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-26/review-our-current-progress-and-find')
def read(name):return json.loads((ROOT/name).read_text())
cold=read('cold-comparison.json');warm=read('warm-comparison.json');traces=read('trace-comparison.json')['summary']
def stat(data,key,field='command_s',kind='median'):return data['summary'][key][field][kind]
local=stat(cold,'local');before=stat(cold,'remote-cold-baseline');after=stat(cold,'remote-cold-retention')
warmbefore=stat(warm,'remote-warm-baseline');warmafter=stat(warm,'remote-warm-retention')
residencies=[r['page_cache']['resident_fraction_after'] for data in [cold,read('diagnostic-before-v2-comparison.json'),read('diagnostic-after-comparison.json')] for r in data['rows'] if 'page_cache' in r]
max_residency=max(residencies)
gain=100*(before-after)/before
old=traces['diagnostic-before-v2'];new=traces['diagnostic-after']
storebefore=old['RemoteChunkCache.store']['worker_s'];storeafter=new['RemoteChunkCache.store']['worker_s']
trace_rows=[]
for label,key in [('Transport download','SkyCacheTransport.download'),('Ancestor-directory setup','store.directory_setup'),
 ('Chunk file force','store.file_force'),('Retained-prefix directory force','store.directory_force'),
 ('Complete retained store (inclusive)','RemoteChunkCache.store'),('Block hash table','RemoteSkyCache.rememberAuthentication'),
 ('Enclosing download (inclusive)','RemoteSkyCache.downloadLocked')]:
 a=old.get(key,dict(worker_s=0,active_union_s=0));b=new.get(key,dict(worker_s=0,active_union_s=0))
 trace_rows.append(f"| {label} | {a['worker_s']:.3f} | {b['worker_s']:.3f} | {a['active_union_s']:.3f} | {b['active_union_s']:.3f} |")
rows=[]
for name,data,key in [('Cold local publication',cold,'local'),('Cold remote, previous',cold,'remote-cold-baseline'),
 ('Cold remote, advisory retention',cold,'remote-cold-retention'),('Warm remote, previous',warm,'remote-warm-baseline'),
 ('Warm remote, advisory retention',warm,'remote-warm-retention')]:
 v=data['summary'][key];t=v['command_s']
 rows.append(f"| {name} | {v['n']} | {t['median']:.3f} | {t['mean']:.3f} | {t['min']:.3f}–{t['max']:.3f} | {v['bazel_cpu_s']['mean']:.2f} |")
table='''| Scenario | Samples | Median s | Mean s | Range s | Mean Bazel CPU-s |
| --- | ---: | ---: | ---: | ---: | ---: |
'''+ '\n'.join(rows)
phase=[]
for name,key in [('Local','local'),('Remote, previous','remote-cold-baseline'),('Remote, advisory','remote-cold-retention')]:
 v=cold['summary'][key]
 phase.append('| '+name+' | '+' | '.join(f"{v[x]['mean']:.3f}" for x in ['before_command_s','middle_s','finalization_s','command_s'])+' |')
text=f'''# Advisory retention removes synchronous chunk durability

27 October 2026; source base f94779009f. Continues the
[digest and cold-start investigation](../skycache-digests-cold-2026-10-26/README.md).

**Cold remote median moves from {before:.3f} s to {after:.3f} s ({gain:.1f}% lower in this
experiment).** The matched cold local median is {local:.3f} s. The retained-store diagnostic
drops from {storebefore:.3f} to {storeafter:.3f} accumulated worker-seconds. All 637 tests,
15 primary measurements and two separate diagnostics pass their required checks.

## What changed

[RemoteChunkCache.store](../../../../src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/RemoteChunkCache.java)
still checks the caller's complete regular file and declared length, checks every ancestor
without following symlinks, and installs a non-replacing hardlink atomically. It now leaves
advisory chunk writeback to the operating system. It no longer recursively forces the parent
directory chain or forces the chunk and prefix directory on every cache miss. Initial cache
root/algorithm setup retains its previous synchronization.

This deliberately changes **retained-chunk durability after a system crash**. Recent entries
can disappear or be incomplete and need downloading again. A normal process exit leaves the
kernel's writeback intact. No background Java queue or command-close sync was introduced.

Integrity and graph-publication durability remain separate:

- A later reader privately pins a retained file and verifies its complete CAS SHA-256 and
  length before admitting it. Missing, empty, truncated, corrupt or symbolic entries become
  misses; a network read can repair them. Cached-only reads still do not issue RPCs.
- Active range reads still authenticate requested covering blocks. Decoded record checksums
  and fresh input/dependency validation remain in place.
- Full local publication and authenticated hydration still force their data/directories and
  install manifests only after the complete closure is durable. Those paths were not edited.
- Soft retention limits, pruning, permissions, atomic visibility and non-replacement are
  unchanged. No protocol, format, option, budget or concurrency limit changes.

The new directory helper preserves the old ancestor checks while omitting durability. It
does not cache directory identities or assume a previously observed path is still safe.

## Measurements

{table}

Every primary arm has three samples in a rotated order. The observed cold local/remote median
gap changes from {before-local:.3f} s to {after-local:.3f} s. Warm median changes by
{warmafter-warmbefore:+.3f} s. Warm reads do not enter the changed store path when all chunks
are retained, but this small sample neither establishes a warm improvement nor rules out a
small regression. These measurements do not establish tail latency or statistical significance.

All cold remote commands still download **1,463,742,717 bytes in 1,613 chunks**, reuse the two
remote parents, produce 18 analysis / 11 execution hits and publish the new child. Every
restored child retains the same 103 witness blocks. Warm commands download zero blob bytes.
Six original binary digests and the new publication probe match in every accepted run.

Means below partition the command clock. The middle includes admission, validation,
restoration, analysis/execution and command overhead; it is not pure storage time.

| Cold scenario | Setup s | Middle s | Finalization s | Total s |
| --- | ---: | ---: | ---: | ---: |
{chr(10).join(phase)}

## Why this path was selected

Before editing, call-site probes separated recursive durable-directory setup, chunk-file
force and retained-prefix force. They consumed 8.823, 3.977 and 2.593 accumulated
worker-seconds respectively, inside a 15.633-second retained-store total. The trace
identified synchronous durability in this advisory tier as the next optimization target.

The separate before/after diagnostic uses the same fixture and cold-page controls:

| Operation | Before worker-s | After worker-s | Before active union s | After active union s |
| --- | ---: | ---: | ---: | ---: |
{chr(10).join(trace_rows)}

Both diagnostic runs contain 1,613 stores. The old run contains 1,613 calls to each explicit
file/prefix force; the new run contains none. The new directory-setup span covers the checked
creation walk. **These are nested/concurrent intervals and cannot be added as wall time.**
Active union means at least one operation is active, not isolated critical-path cost.
Diagnostic JFR and probes are excluded from the primary timing statistics.

[Linux fsync documentation](https://man7.org/linux/man-pages/man2/fsync.2.html) explains that
forcing file contents does not itself persist the directory entry; forcing both is a
durability protocol. [Java 25 FileChannel](https://docs.oracle.com/en/java/javase/25/docs/api/java.base/java/nio/channels/FileChannel.html)
likewise specifies storage forcing separately from normal reads/writes. The patch preserves
atomic visibility and delegates crash durability of this cache tier to OS writeback. The
content-addressed verification path makes loss recoverable as a cache miss.

## Correctness and compatibility evidence

**637 cases across 23 targets pass.** Two new tests reject incomplete/symbolic private source
files and post-construction symlink replacement of the cache root or its ancestors. The
existing retained-corruption repair test now also covers missing and zero-length entries,
proves cached-only misses leave the target untouched, verifies exact network repair, and
then reopens the cache without another download. Existing concurrent publication/pruning,
active pin, interruption, eviction, cancellation and publication tests also pass.

These tests inject possible recovery states; they do not simulate a real machine power cut
or validate every filesystem's crash behavior. The stronger full-publication path is
unchanged, and the selected source hashes match the tested build. The 8,821-class dynamic
registry and registry checksum match both the frozen runtime and previous production build.

## Controls and limits

One frozen installation and the identical two-publication seed are used for all arms.
A startup-only agent chooses exact compiled RemoteChunkCache/helper bytes while sharing the
same tested digest/buffer reader and AutoRegistry bytes. Installation identity, namespace,
authentication and freshness guards are unchanged. Per-run agent hashes and mode logs are
checked; class mapping/provenance is archived, and the diagnostic probes confirm the selected
store behavior. Other startup class-load stderr is not reliably forwarded to the client.
No per-read instrumentation is present in primary measurements.

Every build has a fresh JVM and empty Skyframe graph. Source/repository inputs and the
ordinary action/output-cache services remain warm. Private local publication files or
backend CAS files are synced and evicted with POSIX_FADV_DONTNEED. The predeclared acceptance
limit is less than 0.1% resident Linux metadata pages. mincore reports zero in nine of the
eleven measured cold/diagnostic commands; two primary runs retain 6 and 250 pages, respectively.
The maximum resident fraction is **{max_residency*100:.5f}%** (250 of 369,673 pages). Every
accepted run is retained in the results. Cold remote additionally starts
without retained client chunks. Host/device caches are not flushed. Worker startup, seed
copying, excluded warmups, page eviction and server startup/clean are outside command time;
normal finalization and retention pruning are included.

Source and binaries remain fixed within each cohort. Benchmarks do not overlap builds,
tests or another measured command. The environment is single-host WSL/Linux and loopback;
WAN behavior, other filesystems, CPU pressure and power-loss behavior need separate evaluation.
OS writeback can overlap later work; this is intentional and is not a claim that physical
write traffic disappears. The initial diagnostic harness setup error happened before timing
and is excluded; the successful rerun and every accepted sample are retained.

## Next step

Revisit bounded parallel import with authenticated remote-parent provenance preserved. The
previous full-import prototype caused publication to reread/rehash imported parents; fix that
integration before comparing it with this faster lazy reader. Its old timing results used
the synchronous-retention implementation and cannot be presented as new results here.
Then compare selective/changed target sets and WAN conditions before choosing an eager default.

- [Cold results](cold-comparison.json), [warm results](warm-comparison.json),
  [before diagnostic](diagnostic-before-v2-comparison.json),
  [after diagnostic](diagnostic-after-comparison.json), [stage intervals](trace-comparison.json).
- [Tests/build](build-advisory/result.json), [registry equality](registry-comparison.json),
  [agent provenance](agent/provenance.json), [full provenance](provenance.json),
  [verification](verification.json), [evidence index](evidence-index.json).
- Exact harnesses, compressed results/timelines and helper sources are archived here. They
  depend on the private Datadog fixture, fixed seed, JDK 25, Python gRPC environment and action
  cache. Large binaries/profiles/JFR files remain at the native paths recorded in provenance.
'''
(DEST/'README.md').write_text(text)

short=f'''# Remote Skycache advisory retention results

Removed synchronous file/directory forcing from advisory chunk retention. Full graph
publications keep their durability guarantees. Every retained chunk is still authenticated
before reuse; recent chunks may need downloading again after a system crash.

| Scenario | Median seconds |
| --- | ---: |
| Cold local publication | {local:.2f} |
| Cold remote, previous | {before:.2f} |
| Cold remote, new retention | {after:.2f} |
| Warm remote, previous | {warmbefore:.2f} |
| Warm remote, new retention | {warmafter:.2f} |

Cold remote improves by {gain:.1f}% in this three-sample-per-arm experiment. The local/remote
gap shrinks from {before-local:.2f} to {after-local:.2f} seconds. These are controlled loopback
measurements, not production tail-latency claims. Cold arms use fresh JVMs/graphs and verify
at least {100*(1-max_residency):.3f}% of Linux metadata pages are nonresident. Two samples retain
6 and 250 pages, within the predeclared 0.1% residency limit. Source inputs and action-cache services
remain warm.

Separate diagnostics reduce retained-store time from {storebefore:.2f} to {storeafter:.2f}
accumulated worker-seconds. Previously, recursive directory setup consumed 8.82, chunk-file
forcing 3.98 and prefix-directory forcing 2.59 worker-seconds. Concurrent/nested spans overlap.
The physical writes still happen through OS writeback; there is no Java background queue or
new sync work at command shutdown.

637 tests across 23 targets pass, including missing/empty/corrupt retention repair and
symlink-ancestor rejection. All 15 primary measurements and two diagnostics match outputs,
hit counts and publication checks. Serialized registry compatibility is unchanged.
Tests inject damaged/missing recovery states; no actual machine power-cut test was performed.

Next: preserve authenticated remote-parent provenance during parallel full import, then
compare it against this faster lazy path. Full-import measurements from the previous report
used the older synchronous retention path and have not been rerun in this change.

The policy follows the distinction between atomic visibility and durability in
[Linux fsync](https://man7.org/linux/man-pages/man2/fsync.2.html) and
[Java FileChannel](https://docs.oracle.com/en/java/javase/25/docs/api/java.base/java/nio/channels/FileChannel.html).
Full evidence is in `docs/jsgette/measurements/skycache-retention-2026-10-27/README.md` in the
Bazel worktree.
'''
(WORKSPACE/'reports/Remote Skycache advisory retention results.md').write_text(short)

p=REPO/'docs/jsgette/skycache-remote-mvp.md';s=p.read_text()
anchor='## Remaining limitations and next implementation choices'
section=f'''## Advisory retained-chunk writeback (27 October 2026)

Cold cache misses no longer synchronously force retained chunk files or recursively force
their parent-directory chains. Retention uses checked directory creation and atomic,
non-replacing hardlinks, with OS writeback providing persistence. Recent advisory chunks
can be lost or damaged by a system crash and need downloading again. Every new private pin
still passes complete CAS authentication, and full graph publication/hydration retains its
existing durable installation protocol. No background Java queue or close-time flush was added.

In the matched three-sample cold comparison, median command time changes from **{before:.3f} s
to {after:.3f} s ({gain:.1f}% lower)**, versus **{local:.3f} s** for a page-cold local publication.
Warm remote medians are **{warmbefore:.3f} s before / {warmafter:.3f} s after**; fully retained
reads do not enter the changed store path. The separate diagnostic reduces retained storage
from **{storebefore:.3f} to {storeafter:.3f} accumulated worker-seconds**. These are small loopback
samples; concurrent worker durations are not additive wall-time attribution.

**637 tests across 23 targets, 15 primary commands and two diagnostics pass.** The source
matches the tested binary, registry compatibility is unchanged, output digests match and
all remote children retain identical witness blocks. Recovery tests inject missing, empty,
truncated, corrupt and symbolic entries; they are not actual machine power-cut tests.
[Implementation, measurements and limits](measurements/skycache-retention-2026-10-27/README.md),
[cold comparison](measurements/skycache-retention-2026-10-27/cold-comparison.json),
[diagnostic](measurements/skycache-retention-2026-10-27/trace-comparison.json), and
[verification](measurements/skycache-retention-2026-10-27/verification.json).

'''
title=section.splitlines()[0];assert s.count(anchor)==1
if title in s:
 assert s.count(title)==1
 start=s.index(title);end=s.index(anchor,start);s=s[:start]+section+s[end:]
else:s=s.replace(anchor,section+anchor)
old='''   Next reduce repeated synchronous retained-chunk durability work. Compare bounded parallel
   import that preserves remote parent provenance against smaller fixed chunks and lazy reads'''
new='''   Advisory retention now avoids synchronous file/directory forcing on misses. Next compare
   bounded parallel import that preserves remote parent provenance against smaller fixed chunks
   and lazy reads'''
assert old in s or new in s;s=s.replace(old,new)
old='''Next isolate repeated file/directory synchronization in advisory retained storage, preserving
atomic verified entries and durable complete publications. Then evaluate bounded parallel import
with remote parent provenance preserved; the local-only prototype's timing is not an end-to-end
remote-publication result.'''
new='''Next evaluate bounded parallel import with authenticated remote parent provenance preserved
against the lazy reader with advisory chunk writeback. The previous local-only import timing
is not an end-to-end remote-publication result, and must be rerun after this retention change.'''
assert old in s or new in s;s=s.replace(old,new)
s=s.replace('retention change. Final validation','retention change.\nFinal validation')
p.write_text(s)
print(json.dumps(dict(cold_local=local,cold_before=before,cold_after=after,cold_percent=gain,
 warm_before=warmbefore,warm_after=warmafter,store_before_worker_s=storebefore,store_after_worker_s=storeafter),indent=2))
