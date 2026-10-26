from pathlib import Path
import json

repo=Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
root=Path('/home/jgett/REPOS/.work/skycache-digests-2026-10-26')
p=repo/'docs/jsgette/skycache-remote-mvp.md'
s=p.read_text()
warm=json.loads((root/'slots-warm-comparison.json').read_text())['summary']
cold=json.loads((root/'slots-cold-comparison.json').read_text())['summary']
def med(summary,key):return summary[key]['command_s']['median']
section=f'''## Digest reuse, bounded buffers and cold restore (26 October 2026)

Remote authentication now reuses SHA-256 state, digest output arrays and range buffers
through bounded atomic slots. It retains at most **4 MiB in idle range buffers**;
oversized buffers are temporary and active reads retain their existing concurrency bounds.
Every covering block is still authenticated, returned arrays remain independent, and
failures reset digest state. Lifecycle cleanup and dynamic codec compatibility are preserved.

The selected implementation reduces range-buffer allocation from approximately **3.57 GB
to 21.7 MB per warm command (99.4%)**. Its three-sample warm median is
**{med(warm,'remote-warm-channels'):.3f} s before / {med(warm,'remote-warm-scratch'):.3f} s after**;
cold medians are **{med(cold,'remote-cold-channels'):.3f} s / {med(cold,'remote-cold-scratch'):.3f} s**.
These small loopback samples do not establish tail latency or statistical significance.
An earlier synchronized-pool prototype was slower; its measurements remain separately
archived and are not pooled with the selected revision. **635 tests across 23 targets pass.**

A separate chain comparison with that initial digest prototype distinguishes two local
"cold" conditions. Restoring an existing publication after evicting its Linux pages takes
**23.168 s** median, versus **28.900 s** remote with empty retained chunks and cold backend
pages. Recomputing from an empty local SkyCache takes **26.567 s**, but consumes **462
Bazel CPU-seconds**, versus **102** remote. Every case has a fresh JVM/graph, the same targets
and warm ordinary action-cache services; this is not a fully cold machine benchmark.

The cold remote path downloads **1.464 GB in 1,613 chunks**. Separate diagnostic traces
record **18.00 accumulated worker-seconds in transport** and **16.39 in retained storage**,
including file/directory synchronization; building block authentication tables takes **0.61**.
These nested, concurrent durations cannot be added as wall time. Repeated synchronous
retention work is the next narrow target.

Downloading the complete publication is plausible here: lazy retrieval already fetches
**96.89%** of its bytes. A benchmark-only 16-worker prefetch followed by authenticated
hydration and local restore takes **25.975 s**, including import. With remote publication
enabled, its median becomes **31.423 s**: locally admitted hydrated parents lose remote
provenance, so publication rereads/rehashes them instead of reusing their descriptors.
Preserve that provenance and account for duplicate pack/chunk storage before enabling an
eager mode. Full download still needs current-input validation; these serialized files
are not a complete live Skyframe heap with reverse edges for native invalidation.

All **48 primary measurements and four diagnostics** pass output/publication checks.
The final source matches the selected build and its 8,821-class registry matches the
baseline. [Implementation, complete results and research](measurements/skycache-digests-cold-2026-10-26/README.md),
[selected warm](measurements/skycache-digests-cold-2026-10-26/slots-warm-comparison.json),
[selected cold](measurements/skycache-digests-cold-2026-10-26/slots-cold-comparison.json),
[cold trace](measurements/skycache-digests-cold-2026-10-26/cold-trace.json), and
[verification](measurements/skycache-digests-cold-2026-10-26/verification.json).

'''
anchor='## Remaining limitations and next implementation choices'
assert section.splitlines()[0] not in s and s.count(anchor)==1
s=s.replace(anchor,section+anchor)
old='''   Bounded channel reuse now removes repeated opens on about 55% of warm range reads. Next isolate
   buffer/digest reuse while preserving authentication. Compare smaller fixed chunks for cold
   demand-read amplification. Batch FindMissingBlobs and skip existing uploads
   before considering CDC when service measurements establish upload/RPC overhead.'''
new='''   Bounded channel reuse removes repeated opens on about 55% of warm range reads; digest/buffer
   reuse removes about 99.4% of covering-range buffer allocation in the measured fixture.
   Next reduce repeated synchronous retained-chunk durability work. Compare bounded parallel
   import that preserves remote parent provenance against smaller fixed chunks and lazy reads
   on selective/changed target sets. Batch FindMissingBlobs and skip existing uploads before
   considering CDC when service measurements establish upload/RPC overhead.'''
assert old in s;s=s.replace(old,new)
old='''Next isolate buffer/digest reuse on the authenticated range path. Channel reuse is now bounded
and measured. Final validation is parallel and bounded; further concurrency needs
resource-pressure evidence. Empty retained storage adds another 9.629 s over warm remote on
loopback, so chunk size/layout remains a separate cold-path priority. WAN measurements are
needed before ranking upload deduplication against demand reads on the actual service.'''
new='''Next isolate repeated file/directory synchronization in advisory retained storage, preserving
atomic verified entries and durable complete publications. Then evaluate bounded parallel import
with remote parent provenance preserved; the local-only prototype's timing is not an end-to-end
remote-publication result. Final validation is already parallel and bounded. Smaller/changed
target sets, WAN latency and CPU pressure are needed before choosing eager or lazy loading as a
default or ranking upload deduplication against demand reads on the actual service.'''
assert old in s;s=s.replace(old,new)
p.write_text(s)
print('Updated',p)
