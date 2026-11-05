# Direct execution across Datadog worktrees

On the Datadog SNMP normalizer, direct execution sharing cut a four-worktree cold batch by **21.1%** and a matched eight-worktree batch by **29.5%**. All three target variants had the expected binary hash and behavior. The worker executed the same 23 distinct remote actions once at both widths. Without in-flight sharing, completed-result caching still allowed 65 physical executions at four worktrees and 107 at eight. [Four-worktree comparison](../experiments/datadog_execution_shared_4_windows.json) · [Eight-worktree comparison](../experiments/datadog_execution_shared_8_12g_windows.json)

The [action-ownership prototype](<Shared action ownership MVP.md>) saved about four seconds per four-build batch, but required a preselected standard-library digest and another Bazel process to rediscover the action. This follow-up lets stock Bazel upload the actual command and input tree to a local worker through its remote execution API. Each worktree runs its own analysis. The worker can share eligible actions across those analyses without knowing the target or Git revision in advance. [Worker](../experiments/reapi_worker.py) · [Content-addressed storage](../experiments/reapi_worker_store.py)

## Workload and comparison

The Windows workload builds Datadog Agent's `//pkg/networkdevice/profile/profiledefinition/normalize_cmd:normalize_cmd` at commit `85ad968bca856fa3d4e07b4d8e11961dabe6866f` with Bazel 9.1.0 in pure-Go mode. The tool normalizes SNMP device profiles. Its base reference loaded 175 packages and configured 11,559 targets. Each isolated variant executed 14 Go package compilations, compared with one for the previous header-processing tool.

Each worktree in this experiment is a private sparse workspace reconstructed from committed Git objects, with the original module, rule implementations, required package metadata and target sources. The edits apply only inside those private directories. The existing Agent checkout supplies the Git objects.

The client views contain base inputs, a Go source edit, or a BUILD edit that introduces a generator. The edits change the output filename. Three isolated builds produced three distinct 3,621,376-byte executables. Each was invoked on a YAML profile, with checks on the output filename and normalized JSON fields. The cohorts repeat both SHA-256 and behavioral checks for every successful client. [Target fixture](../experiments/datadog_execution_target.py) · [Independent references](../experiments/datadog_execution_target_windows.json)

Both comparison arms use the same worker, action cache, disk CAS, stock Bazel flags and local Windows host. **Control** admits separate executions for equal concurrent misses. **Shared** joins an already running eligible digest. Both may reuse a completed result. Each arm starts with an empty private worker store, separate output bases and a new JVM per client. Repository downloads are warm. Local fallback is disabled, and compiler execution records are checked against the worker's physical starts. Small operations that Bazel runs locally are reported separately. [Cohort harness](../experiments/datadog_execution_cohort.py)

The four-client burst uses two base views, one source edit and one BUILD edit. The eight-client burst uses four base views and two of each edited variant. These workloads have substantial overlap by design. The first comparison ran control then shared at four clients. At eight it ran shared then control. OS file caches were left in place. These are cold batch clients with fresh output bases, one trial per arm at each budget.

A Windows Job Object restricts each cohort, including the clients, worker and compiler processes, to eight logical CPUs and an aggregate commit budget. Each Bazel JVM has a 512 MiB heap limit. Bazel advertises two jobs per client at four worktrees and one per client at eight. The worker admits at most eight physical producers across all clients. Batch time spans first launch to last exit. Aggregate CPU is the job's user plus kernel CPU delta during the burst. Peak commit is the OS job high-water mark. Neither represents Skyframe alone. Workspace and installation preparation occur outside the timed burst.

## How ownership works

The worker receives a SHA-256 action digest scoped to one trusted execution instance, fetches the action and command from CAS, and copies the declared Merkle input tree into private execution scratch. It executes the command there with closed stdin, gathers declared outputs and publishes their content before making a reusable ActionResult available. Each caller independently obtains the final output in its own workspace. Both modes use the same materialization path.

Live operations are bounded. A matching subscriber can join while the physical window is full. A cancelled stream detaches only that subscriber. An admitted process continues through publication even if every subscriber leaves. Completed operation records and results live on disk and can be recovered with `WaitExecution` or an action-cache lookup. The worker can reopen a marked store after a graceful shutdown and uses an exclusive writer lock to keep ownership unambiguous.

On both Windows and Ubuntu/WSL, four real gRPC execution streams requested a gated process. The first stream was cancelled after three followers joined. The survivors and a resumed `WaitExecution` call received the output of one physical execution. A fresh client got a cache hit. A forced cache bypass executed again, and two `do_not_cache` requests started two independent processes. After worker restart, both the named operation and its cached result were recovered with zero physical starts. A second live store owner was rejected. [Windows result](../experiments/reapi_execution_probe_windows.json) · [Linux/WSL result](../experiments/reapi_execution_probe_linux.json) · [Protocol probe](../experiments/reapi_execution_probe.py)

## Qualification

Stock Bazel builds on Windows and Linux/WSL each sent a genuine genrule to the worker and verified its returned bytes. The one-client Windows normalizer qualification then executed the toolchain helper, Go standard library, 14 package compilations and the link remotely. It completed in 117.34 seconds and consumed 160.09 aggregate CPU-seconds. The output matched the isolated base reference and passed the functional check. [Windows smoke](../experiments/reapi_bazel_smoke_windows.json) · [Linux/WSL smoke](../experiments/reapi_bazel_smoke_linux.json) · [Normalizer qualification](../experiments/datadog_execution_qualification_windows.json)

Two earlier qualification attempts are retained separately. The initial RPC ingress limit rejected concurrent uploads of the Go SDK before compilation. The worker and clients now use gRPC flow control and bounded transport concurrency. A later build succeeded but the verifier expected a local output URI while Bazel advertised a CAS URI. The harness now asks Bazel to report the locally materialized output path before hashing the executable. The protocol probe also exposed inheritance of the worker's stdin control pipe by child commands on Windows. Action processes now receive empty stdin. [Upload preflight](../experiments/datadog_execution_ingress_preflight_windows.json) · [Output URI preflight](../experiments/datadog_execution_output_uri_preflight_windows.json)

## Four and eight concurrent worktrees

| Worktrees, 8 GiB budget | Verified binaries | Batch wall (s) | Job CPU (s) | Peak commit (GiB) | Worker starts / distinct remote digests |
|---|---:|---:|---:|---:|---:|
| Four, control | 4 / 4 | 189.67 | 668.52 | 4.99 | 65 / 23 |
| Four, shared | 4 / 4 | 149.64 | 433.80 | 3.61 | 23 / 23 |
| Eight, control | 2 / 8 | Incomplete | Incomplete | 8.01 | Incomplete |
| Eight, shared | 8 / 8 | 196.95 | 803.67 | 7.74 | 23 / 23 |

At four worktrees, sharing saved **40.03 seconds (21.1%)**, **234.72 CPU-seconds (35.1%)** and **1.38 GiB of peak commit**. The control started 52 Go package compilations for 17 distinct package digests. The shared arm started exactly 17. The standard library and toolchain helper each went from four executions to one. The link went from four to three. The shared worker recorded 45 in-flight joins and one completed-result hit. Each started digest ran once. [Four-client control](../experiments/datadog_execution_control_4_windows.json) · [Four-client shared](../experiments/datadog_execution_shared_4_windows.json)

At eight worktrees, the shared worker recorded 90 joins and 25 completed-result hits. Its 23 physical actions spanned the same three target variants as the four-client workload. Six control clients failed to allocate memory while setting up Gazelle repository tools. Two completed and matched their references. The kernel recorded a peak of 8.006 GiB against the configured 8 GiB job budget. The failed cohort's 189.45 seconds measure time until all requests ended. A matched completion-time comparison requires both cohorts to finish. The relationship between the repository setup failures and duplicate compiler executions warrants a separate measurement. [Eight-client shared](../experiments/datadog_execution_shared_8_windows.json) · [Eight-client control](../experiments/datadog_execution_control_8_windows.json) · [Allocation diagnostics](../experiments/datadog_execution_control_8_allocation_diagnostics.json)

Sharing execution leaves substantial client-side costs. Four shared clients reported 237,841 post-invocation Skyframe nodes in total, counted independently across JVMs. Eight reported 475,622. At the same budget and physical action count, going from four shared clients to eight added 47.31 seconds of batch time and 4.13 GiB of peak commit. This comparison also changes the client-side scheduling allowance from two jobs per client to one. Node counts indicate duplicated retained state but cannot identify how much of the memory or CPU delta belongs to Skyframe. Analysis, hashing and uploading inputs, output delivery, JVM startup and other work are included.

### Eight worktrees with a 12 GiB budget

To obtain a successful matched timing comparison, both eight-client arms ran again with a 12 GiB limit after checking system headroom. The CPU allocation, JVM heap sizes, worker slots, target variants and cold-start conditions matched. Control ran first. Both arms verified all eight binaries. The set of 23 action digests was identical across modes and widths. [Budget headroom](../experiments/datadog_execution_12g_headroom_windows.json) · [Control](../experiments/datadog_execution_control_8_12g_windows.json) · [Shared](../experiments/datadog_execution_shared_8_12g_windows.json)

| Eight clients | Remote starts | Batch wall (s) | Job CPU (s) | Peak commit (GiB) |
|---|---:|---:|---:|---:|
| Control | 107 | 289.69 | 1,378.14 | 11.67 |
| Shared | 23 | 204.19 | 827.05 | 9.02 |

Sharing saved **85.50 seconds (29.5%)**, **551.09 CPU-seconds (40.0%)** and **2.64 GiB of peak commit (22.7%)**. The control started 82 package compiles for 17 unique package digests and ran eight copies each of the toolchain helper, standard library and link. The eight same-key standard-library executions overlapped for 87.16 seconds. In shared mode, each standard-library and helper action ran once, the 17 unique package actions ran once each, and the three distinct links each ran once. The worker recorded 94 in-flight joins and 21 completed-result hits. The shared peak was 9.02 GiB in this run and 7.74 GiB in its successful 8 GiB run, so a single peak gives an observed outcome rather than a fixed capacity requirement.

### Design implication

Keep the worker-owned action boundary. It eliminated duplicate command execution across independently edited workspaces using stock Bazel's existing action identity, while preserving the outputs for each view. The next investigation is upstream of that boundary. Profile package loading, analysis, Merkle input construction and Gazelle repository-tool preparation across cold and warm edit bursts. In particular, compare an analysis-only run against the full build before using whole-job memory or CPU as evidence for an evaluator replacement. These runs use one target with heavily overlapping work and one matched observation per configuration.

## Final worker qualification

The timing arms used worker SHA-256 `44cd16f05d4e57b9e2273646a86a35e63ba022fbbfc3ffc52b9f8af87f788369`. A subsequent review found a race in which a forced refresh could briefly join an operation whose result was already cache-visible. The measured worker also accepted unverified client writes to its action cache and returned a generic error for an expired action. The measured cohorts used fresh trusted stores, successful commands, worker event logs and independent executable checks.

The final worker serializes publication with admission, permits only the worker to write ActionResults, and reports explicit timeout outcomes. Protocol tests on both hosts verified a fresh execution after cache-visible publication, merging of two genuinely concurrent refreshes, rejection of a client cache overwrite, rejection of invalid or excessive timeouts, and a deadline result after the observed parent and child stopped. Its store namespace version isolates it from earlier writable caches. Final worker SHA-256 is `2a7b96b4acca8e92e77b58ae9cf83a715084a38054dbb7c62d11b21e6eb2350f`. [Windows policy tests](../experiments/reapi_policy_probe_windows.json) · [Linux/WSL policy tests](../experiments/reapi_policy_probe_linux.json) · [Policy probe](../experiments/reapi_policy_probe.py)

### Cancelling a real Datadog client

Four stock Bazel clients built the normalizer with the final worker. The harness identified the client that first requested the actual `GoStdlib` digest, observed all four invocations attached to the same operation and confirmed that the compiler was running as a descendant of the worker. It then terminated only the originating Bazel process tree. The worker recorded a live detach from four subscribers to three, and the same compiler PID survived. The other base, source-edited and BUILD-edited builds matched their reference executables and behavior. A late fresh build of the cancelled base variant got a completed-result hit for the standard library and matched its reference. Across that run the worker started the standard library once and executed each of the 23 distinct remote digests once. This final-candidate test exercises actual Bazel cancellation by process termination and is separate from the timing comparisons. [Final Datadog cancellation record](../experiments/datadog_execution_cancel_windows.json) · [Observer](../experiments/datadog_execution_cancellation.py)

## Reproduction and scope

The probes ran with Python 3.11 on Windows and 3.12 on Ubuntu/WSL. Use Git, this Bazel checkout, and a Datadog checkout containing the pinned commit. The compiled Windows runs also use Git Bash and the stock Bazel 9.1.0 executable. The bootstrap prepares pinned Python dependencies and generates gRPC bindings in an empty directory you supply. Set `PYTHONPATH` to the value it prints. A prepopulated Bazel repository download cache can be supplied to the target and cohort harnesses using `--repository-cache`. Run arms sequentially.

```text
python -B experiments/reapi_bootstrap.py --destination EMPTY_PRIVATE_DIRECTORY
python -B experiments/reapi_execution_probe.py --output protocol.json
python -B experiments/datadog_execution_target.py --repo PATH_TO_AGENT --bazel PATH_TO_BAZEL --output references.json
python -B experiments/datadog_execution_cohort.py --repo PATH_TO_AGENT --bazel PATH_TO_BAZEL --references references.json --mode control --clients 4 --output control4.json
python -B experiments/datadog_execution_cohort.py --repo PATH_TO_AGENT --bazel PATH_TO_BAZEL --references references.json --mode shared --clients 4 --output shared4.json
python -B experiments/datadog_execution_cohort.py --repo PATH_TO_AGENT --bazel PATH_TO_BAZEL --references references.json --mode shared --clients 8 --output shared8.json
python -B experiments/datadog_execution_cohort.py --repo PATH_TO_AGENT --bazel PATH_TO_BAZEL --references references.json --mode control --clients 8 --output control8.json
python -B experiments/datadog_execution_cohort.py --repo PATH_TO_AGENT --bazel PATH_TO_BAZEL --references references.json --mode control --clients 8 --memory-mib 12288 --output control8_12g.json
python -B experiments/datadog_execution_cohort.py --repo PATH_TO_AGENT --bazel PATH_TO_BAZEL --references references.json --mode shared --clients 8 --memory-mib 12288 --output shared8_12g.json
python -B experiments/datadog_execution_cohort.py --repo PATH_TO_AGENT --bazel PATH_TO_BAZEL --references references.json --mode shared --clients 4 --cancel-origin --output cancellation.json
```

This research worker serves a trusted loopback endpoint and one fixed host execution environment. It supports regular files, directories, uncompressed transfers and SHA-256. Input hydration copies declared files to each private action root. Output-directory verification and publication have costs proportional to the active action's output tree. Completed records occupy disk. General toolchain/platform selection, authentication, storage eviction and crash-time prevention of duplicate external children remain open. The compiled benchmark uses Windows. Native macOS, a full Agent distribution build and eviction of Skyframe analysis state require separate experiments.

For an exact timing-source reproduction, apply the [timing reproduction patch](../experiments/reapi_reproduce_timing.patch) from the root of a disposable Bazel checkout with `git apply --directory=docs/jsgette/measurements/worktree-overlays docs/jsgette/measurements/worktree-overlays/experiments/reapi_reproduce_timing.patch`. It transforms the final worker and cohort harness back to their measured versions. The four source SHA-256 values were checked against the recorded measurement. Use the final version for the protocol, policy and cancellation qualifications.
