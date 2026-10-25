# Larger Datadog SkyCache comparison after clean — 25 October 2026

Six Linux application binaries were built with Cache & Carry after verified synchronous `bazel clean`: Cluster Agent, Process Agent, Trace Agent, DogStatsD, Installer and Private Action Runner. The vanilla build configured **80,301 targets including aspects**, compared with 57,369 for the preceding Cluster Agent scope (**1.40 times as many**). The broader core/security/system-probe scope reached 237,112 configured targets but exceeded current SkyCache publication limits, as detailed below. No product source or limits were changed for these tests.

## Repeated measurements

Each cell is the median of three runs. `vanilla` is the preserved pre-SkyCache source build at `4cfc05f77ba91900a2e3c6ab135861498cb2a0bf`, not a stock Bazel release. `off` and `on` use the same candidate executable at implementation commit `71521fabb3420707c39d73329fa9aa9622e8c382`; this pair isolates enabling SkyCache more closely.

| Policy | Variant | Build s | Build range s | Analysis s | Clean + build s | Peak PSS MiB | Peak RSS MiB | GetActionResult | CAS read MiB |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| minimal | vanilla | 11.68 | 11.67–19.79 | 4.04 | 12.90 | 2809 | 2816 | 14055 | 14.64 |
| minimal | off | 11.68 | 11.52–11.82 | 4.09 | 12.91 | 2751 | 2758 | 14055 | 14.64 |
| minimal | on | 6.42 | 6.42–6.47 | 4.20 | 7.85 | 2372 | 2379 | 0 | 0.00 |
| toplevel | vanilla | 20.58 | 20.37–20.68 | 3.96 | 21.81 | 2643 | 2650 | 14055 | 304.63 |
| toplevel | off | 20.94 | 20.63–21.23 | 4.09 | 22.32 | 2353 | 2360 | 14055 | 304.63 |
| toplevel | on | 15.33 | 15.28–15.53 | 4.13 | 16.76 | 2611 | 2617 | 0 | 289.99 |

Positive percentages below mean a reduction from the specified baseline; negative values mean a regression.

Analysis time remained near four seconds: SkyCache minimal measured 4.20 seconds versus 4.04 for vanilla, and toplevel measured 4.13 versus 3.96. Thus this experiment does not show faster analysis in isolation. The total-build gain coincides with avoiding most action processing and remote lookups, while local dependency validation remains substantial. Memory was also mixed: minimal peak PSS fell 15.6% versus vanilla; toplevel was only 1.2% lower, and 11.0% higher than the same candidate with SkyCache disabled. PSS ranges are retained in the summary.

| Policy | Baseline | Build time reduction | Peak PSS reduction |
|---|---|---:|---:|
| minimal | vanilla | 45.0% | 15.6% |
| minimal | off | 45.0% | 13.8% |
| toplevel | vanilla | 25.5% | 1.2% |
| toplevel | off | 26.8% | -11.0% |

The [individual results](measurements/skycache-datadog-wide-2026-10-25/datadog-wide-apps-2026-10-25/results.json) preserve every observation, command, binary hash, clean check, RPC count and sample. [Summary JSON](measurements/skycache-datadog-wide-2026-10-25/datadog-wide-apps-2026-10-25/summary.json) includes minima and maxima. Three repetitions on a shared network service do not establish statistical significance.

## Protocol and scope

Datadog revision `0cde96beb70963d0838119c308b9d0422ee66be0`; resolved lockfile SHA-256 `0bb43dc2960626918c20c350491be5cdf6fd18730bb34a956d4608a1e1aa98fc`. Exact targets:

```text
//cmd/cluster-agent:cluster-agent
//cmd/process-agent:process-agent
//cmd/trace-agent:trace-agent
//cmd/dogstatsd:dogstatsd
//cmd/installer:installer
//cmd/privateactionrunner:privateactionrunner
```

Before every build, the harness shut down its server, ran `clean --noasync --noexpunge`, and verified zero Skyframe nodes/edges, empty execroot, absent local action cache and byte-identical persistent SkyCache manifests. The clean starts a fresh JVM; the build uses that same PID and an empty graph. Build time excludes clean/JVM startup; the combined column includes the separately measured clean. OS page cache and downloaded external repositories remain warm. This deliberately exercises output deletion as well as analysis-cache discard.

All variants use the same workspace/output base, JDK 25, 6 GiB JVM heap, 16 jobs and 6144 MiB local action-memory budget. Disk action caching is disabled. Cache & Carry remote cache and BES are enabled; the remote transport is capped at one connection and 16 concurrent requests for every variant. SkyCache idle eviction and GC are disabled for the experiment; explicit clean clears the graph. Active directories are `cmd`, covering the six requested command packages.

The sequence first prepares vanilla toplevel outputs, then populates SkyCache in minimal/toplevel, followed by 18 measured builds in rotating variant/policy order. Preparation is excluded from the comparison. Process-tree RSS/PSS is sampled every 100 ms, including the build client, server and action descendants. It excludes unrelated processes and OS filesystem cache; memory sampling can miss short peaks.

Representative first minimal run per variant:

| Variant | Targets configured | Configured-target hits | Execution hits | Local regular files | Local bytes | Intermediate .a / .x files |
|---|---:|---:|---:|---:|---:|---:|
| vanilla | 80301 | 0 | 0 | 793 | 9977320 | 0 / 0 |
| off | 80301 | 0 | 0 | 793 | 9977320 | 0 / 0 |
| on | 23 | 18 | 11 | 14 | 139983 | 0 / 0 |

Configured-target cache hits describe restored frontier values, not the full transitive graph avoided. A SkyCache hit run can configure fewer targets because it stops traversal at those values. [Scope and output details](measurements/skycache-datadog-wide-2026-10-25/scope-and-output-details.json) retains all runs, analysis timing, inventories and runner counts.

## Correctness and cache traffic

All six requested application output digests matched the vanilla preparation in every successful measured build. Toplevel checks additionally hashed the actual executable files; measured minimal builds required those final binaries to remain absent. Intermediate archive counts and total local output bytes are recorded rather than inferred from the output policy.

| Binary | SHA-256 |
|---|---|
| trace-agent | `ce3a3840ce386eef5dafb2245ad0b7b2e6894f79179f038c3594d01c7fec7edf` |
| process-agent | `fd6381664afc248f3954d7d502cc292de579962904e8886c39abaf6451287096` |
| installer | `3088d1d687f686f63caea5ae417db53fd5f31a8b8e3c03960f2cfeecc7a549af` |
| dogstatsd | `bd53a22e9cf89d45d3216375e7d9db04ac15187ca18f67a17a460be77021b1ac` |
| privateactionrunner | `708649a638fef9788ca9fe0c633359a128b9d2a7de30f7f10d2e27408dd72ba7` |
| cluster-agent | `7f6e2b951113f9e05bbe14709175f872b7222090bd8e694159f9f5aa5cf45664` |

All six measured SkyCache builds made **zero GetActionResult requests**. Each minimal hit made **zero CAS reads**; each toplevel hit read ten requested output/runfile blobs totaling **304,075,144 bytes**. No intermediate `.a` or `.x` files were materialized in the SkyCache hit runs. Necessary internal manifests were still generated.

The RPC figures include the complete logged build. Hit runs still make `GetCapabilities`, and any `FindMissingBlobs` request was verified against the exact hash and size of that run's BES profile upload. [Profile checks](measurements/skycache-datadog-wide-2026-10-25/bes-profile-checks.json) distinguish this from action-result or build-output validation. Remote CAS bytes are counted from ByteStream responses. The original missing-CAS rewind and source-invalidation regressions remain documented in the [execution-metadata report](skycache-execution-metadata-2026-10-25.md); this larger-scope comparison changes no product source.

## Larger scopes exceeded publication limits

The first scope requested core Agent, Cluster Agent, Process Agent, Trace Agent, Security Agent and System Probe. All six binaries built correctly at **237,112 configured targets including aspects**. Its vanilla cold preparation took 457.02 seconds, executing 10,993 local actions and uploading their results; that observation is not a warm-cache performance baseline. Candidate minimal then built successfully in 58.45 seconds with 16,697 remote action-cache hits, but publication failed with `input manifest exceeds size limit`. No reusable manifest was admitted.

Removing only core Agent left a five-binary scope at **220,062 configured targets**. Vanilla toplevel built in 22.58 seconds; candidate minimal took 25.69 seconds and hit the same publication limit. These differ in output policy and include preparation, so they are not a paired speedup comparison. Both failed scopes and the source inputs are retained, not included in the successful six-application medians.

Read-only inspection of the six-binary unpublished pack verified the checksums of 347,658 serialized dependency-evidence records and reconstructed 418,098 unique external/absolute file witnesses. Their encoded witness data alone occupies **77,546,670 bytes (73.95 MiB)**, above the **64 MiB manifest limit**, before additional final repository-binding witnesses. Three Linux-header repository views account for 40,366,811 bytes, and absolute paths into the shared repository cache account for another 22,402,643 bytes. This is a concrete limit in the eager global witness manifest, beyond per-entry dependency validation.

The pack contains **1,664,716 unique records**, exceeding its separate **1,000,000-entry sealing limit**. Manifest admission happens before pack sealing, so the manifest error is the observed rejection; the entry limit is an additional source-verified blocker, not a second runtime error. Increasing only the manifest cap would leave that blocker. The unpublished pack is 1,421,905,110 bytes and was preserved for inspection. [Pack diagnosis](measurements/skycache-datadog-wide-2026-10-25/datadog-wide-2026-10-25-attempt2/pack-diagnosis.json) records exact counts, grouping and the reconstruction boundary.

The repeated comparison therefore uses the six application binaries listed above, which still exceed the earlier configured-target and action counts while fitting the current publication format. The implementation is not demonstrated to accelerate the 220k/237k scopes. Compacting or restructuring global witness storage and supporting larger or partitioned packs are follow-up design work; neither was bypassed for the benchmark.

The successful application scope retained one publication totaling 878,015,248 bytes across its cache files, approximately 837.3 MiB. This is local graph/metadata storage in addition to the remote output cache.

## Preparation and evidence retention

The first six-target attempt stopped during analysis because newly reached module-extension dependencies required a lockfile refresh. Its logs and partial metrics are preserved, excluded from performance medians. A separate `--nobuild --lockfile_mode=update` preparation completed successfully, after which all compared builds used `--lockfile_mode=error` and the same frozen lockfile. Both pre- and post-resolution lockfiles are retained. Compiler and dependency warnings remain in the logs. The read-only pack diagnosis ran during the excluded application preparation, not during the measured matrix.

The original Datadog lockfile and tracked workspace diff were restored exactly after all measured builds, and the benchmark server was shut down. Product sources remain unchanged.

Public evidence includes commands, logs, output digests, sampled memory/storage, lockfiles and checksums. Raw BEP, profiles, gRPC logs and the cache packs are retained in a SHA-256-verified 3,820,072,960-byte archive on D: because these formats can contain environment values or request headers. [Private backup metadata](measurements/skycache-datadog-wide-2026-10-25/private-backup.json) records the archive. The experiment tests the specified Linux build scope; it is not a full Datadog unit/E2E test suite or a qualification of arbitrary Bazel rules.
