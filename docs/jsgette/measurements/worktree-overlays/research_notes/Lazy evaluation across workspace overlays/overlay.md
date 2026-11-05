# Lazy reads and shared work across uncommitted overlays

## What was the input to each request?

### Takeaway

Eight independent callers pinned eight distinct uncommitted source trees over one Git commit. Each caller could resolve the library it wanted without enumerating every file. All eight roots referenced the same library subtree.

### Cited findings

- The standard-library Python probe creates a disposable repository, stages one version of `app/main.c` in the real index, and captures a different version from disk into A1. It constructs B1 and six other revisions from explicit edit batches in alternate indexes. The observed base commit is `12896084b565`, A1 is `8dc443585048`, B1 is `18887a497761`, and every root contains `lib/` tree `3fc53550c019`. The probe checks that the real index remains byte-identical and HEAD stays at the base commit. [Probe](../../experiments/overlay_probe.py) · [Windows results](../../experiments/overlay_results.json) · [Git write-tree](https://git-scm.com/docs/git-write-tree)
- While the first request was registered against A1, the probe published A2 at `34bba8875930`. Later inspection of A1 still produced `EDIT A` from its application source. B1 produced `BASE` and A2 produced `LATER A`. Both the Windows and Linux runs matched the full independent fixture oracle. [Windows results](../../experiments/overlay_results.json) · [Ubuntu WSL results](../../experiments/overlay_results_linux.json)

### Inferences

An immutable source root can name a request's view. Resolution of one regular file in this example requires its path and a pinned root. It does not require the application's graph or another overlay's edit history.

### Gaps

Publishing the fixture's roots uses `git read-tree` and an alternate index. The probe establishes **demanded reads during execution**. It does not establish bounded index memory or cheap capture from a live directory. The immutable objects remain available for the duration of this short run with automatic Git maintenance disabled. Long-lived builds would need an object-retention policy.

## What distinguishes useful sharing from a stale hit?

### Takeaway

In each of two batches, eight real caller threads first observed inputs in their own views, then met at a barrier. The library batch resolved to one operation and the header-manifest batch to two. Every delivered result matched an oracle supplied independently of the source observations and cache keys.

### Cited findings

- The library request resolves `lib/math.c` to a blob ID in each pinned tree. Its operation key includes a versioned transform, declared mode and target, path and blob ID. With eight requests admitted, one producer read and uppercased the library blob, seven callers joined its future, and all received `LIB\n`. The in-flight table contained zero entries on completion. A full-root key would have distinguished eight entries. [Implementation](../../experiments/overlay_probe.py) · [Run output](../../experiments/overlay_results.json)
- The second operation lists matching header **names** under `app/`, then renders a manifest. Its key uses the sorted names observed in the requesting tree. Eight callers formed two classes with two executions and six joins. Four received `old.h\n` and four received `new.h\n`. A deliberately under-keyed cache using only the common commit and target label returned A1's `old.h` for B1, whose oracle requires `new.h`. [Implementation](../../experiments/overlay_probe.py) · [Run output](../../experiments/overlay_results.json) · [Git ls-tree](https://git-scm.com/docs/git-ls-tree)
- An independent presence query recorded that `app/new.h` was absent in A1 and present in B1. A1 and A2 have equal directory-name and presence observations although their application contents differ. Across eight original views plus A2, application transforms had nine distinct keys. The same application source under three declared configurations or target contexts produced three distinct keys. These are key classifications, not executions of three native compilers. [Recorded observations](../../experiments/overlay_results.json)

### Inferences

Replicating cheap path lookups per request can establish equality before a more expensive payload read or operation. The safe sharing key depends on what the operation observes. A header-name list is sufficient for this manifest. It is insufficient for an operation that also reads header contents. A changed root alone reveals neither fact.

### Gaps

The registration barrier deliberately creates overlap. This is a functional test of coalescing under overlapping arrivals, not a workload estimate or performance measurement. Only pure, declared fixture transforms are eligible. The extension to arbitrary dynamic Bazel analysis, actual subprocesses, side effects, cancellation, failures and crash recovery remains open. The in-flight table is removed on completion. Late arrivals would require a separate completed-result record, which the window experiment investigates.

## Which hosts ran the probe?

### Takeaway

Native Windows and Ubuntu on WSL produced identical output except for the recorded environment field. macOS remains a design target with an unexecuted run.

### Cited findings

- On Windows the probe used Python 3.11.9 and Git 2.52.0.windows.1. On Ubuntu WSL it used Python 3.12.3 and Linux Git 2.43.0. In both cases the temporary repository lived under the interpreter's temporary directory. All reported root IDs, observations, producer counts, joins and control results agree. [Windows JSON](../../experiments/overlay_results.json) · [Linux JSON](../../experiments/overlay_results_linux.json)
- From the workspace root run `python experiments/overlay_probe.py` (or `python3`). It prints JSON, removes the scratch repository after execution, and exits unsuccessfully on any failed oracle. `--out <path>` saves the same result, and `--requests N` sets the number of distinct concurrent overlay callers, minimum three. The measured default is eight. The Linux command used here was `wsl.exe -d Ubuntu --exec python3 experiments/overlay_probe.py --out experiments/overlay_results_linux.json`. [Reproduction script](../../experiments/overlay_probe.py)

### Inferences

The restricted example depends on Git object reads and ordinary user-space synchronization. No platform mount or privileged interface is needed to reproduce these semantics.

### Gaps

The fixture uses LF bytes, ASCII names and regular files of mode 100644. Neither run establishes equivalence across case collisions, transformed checkout bytes, symbolic links or native toolchains. The library and manifest are research operations, not a Bazel build or a limit test of operating-system thread counts.
