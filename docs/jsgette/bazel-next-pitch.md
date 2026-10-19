# Proposal: One Bazel Server for Many Commands and Worktrees, With Full-Fidelity OpenTelemetry

Working name: TBD (avoid "Bazel" in the name; it is a Google trademark).

Items marked `[TODO]` must be filled in or verified before sending.

---

## In one sentence

Run several Bazel commands and worktrees on one machine without paying for a separate copy of the build graph per server, and see every build end to end in the OpenTelemetry backend you already run.

---

## Who

Joseph Gette, Senior Software Engineer, Agent Build, Datadog. GitHub: [JSGette](https://github.com/JSGette).

- **Bazel migration of [datadog-agent](https://github.com/DataDog/datadog-agent)**, a large open-source polyglot monorepo (Go, Python, C/C++, Rust, eBPF; Linux, Windows, macOS): [212 merged PRs](https://github.com/DataDog/datadog-agent/pulls?q=is%3Apr+author%3AJSGette+is%3Amerged) since July 2025. This covers Go package migration, hermetic MSVC and CPython builds on Windows, hermetic kernel headers, remote cache rollout, coverage, execution-log generation, and codegen moved into Bazel.
- **Build observability:** [bazel_conduit](https://github.com/JSGette/bazel_conduit) (Rust; Build Event Protocol and execution log to OpenTelemetry traces) and [bzlog](https://github.com/JSGette/bzlog) (agent-friendly digest of BEP). Earlier: build-event-service, remote-cache-service, and remote-cache-debugger (2022–2023).
- **Bazel prototypes** (to be published on [JSGette/bazel](https://github.com/JSGette/bazel)) `[TODO: rebase onto master, push, link branches]`: a native OpenTelemetry module that reads Bazel's internal event bus and exports OTLP (about 2.8k lines with tests), and a local RAM+disk backend for remote analysis caching (about 1.35k lines with tests).
- **Upstream:** merged fixes in [rules_rust](https://github.com/bazelbuild/rules_rust/pull/3990) and [rules_pkg](https://github.com/bazelbuild/rules_pkg/pull/1076); open PRs in [rules_go](https://github.com/bazel-contrib/rules_go/pull/4729), [rules_cc](https://github.com/bazelbuild/rules_cc/pull/687), and [rules_foreign_cc](https://github.com/bazel-contrib/rules_foreign_cc/pull/1491); bug reports in [bazel](https://github.com/bazelbuild/bazel/issues/29129), [rules_go](https://github.com/bazel-contrib/rules_go/issues/4581), and Buildbarn.
- **Talks:** "Debugging Cache Misses and Sources of Nondeterminism in Bazel" (BazelCon Community Day Munich 2023, with Ben Radford); "How I Tried to Produce OTel Traces out of BEP" (Munich Bazel Build Meetup 2026); co-host of the Buildbarn Meetup at Datadog Amsterdam (October 2026).

---

## Problem

Agentic coding changes the shape of build load. Agents work in many git worktrees of the same repo and issue many short `build`, `test`, and `query` commands in parallel. Datadog engineers have reported publicly that Datadog's primary monorepo grew 160% in lines of code in four months, driven by agent-authored code, reaching billions of Bazel actions per week.

Bazel's local model was not designed for this:

1. **One command per server.** Each output base runs one Bazel server, and each server accepts one command at a time; others block or fail (`--block_for_lock`). Bazel's own contributor docs call the inability to run even `bazel version` in parallel "somewhat embarrassing" and name the blocker: "the life cycle of `BlazeModule`s and some state in `BlazeRuntime`" ([codebase.md](https://github.com/bazelbuild/bazel/blob/master/site/en/contribute/codebase.md)).

2. **Every worktree pays for its own graph.** Each worktree gets its own output base, server, and in-memory analysis graph, even though most of that graph is identical across worktrees of the same repo. `[TODO: measured on datadog-agent: warm server RSS = X GB; 4 worktrees = Y GB total; analysis time after server restart = Z s]`

3. **Analysis state lives only in RAM.** The existing memory flags (`--discard_analysis_cache`, `--nokeep_state_after_build`, `--notrack_incremental_state`, `--heuristically_drop_nodes`, `--skyframe_high_water_mark_threshold`) save memory by throwing state away, which you then pay for again as re-analysis. Remote analysis caching (Google's Skycache; in open-source Bazel, `--experimental_remote_analysis_cache_mode` with a gRPC analysis cache service) helps cold builds across machines, but upstream Bazel has no local-disk tier and no sharing between servers on the same machine.

4. **Build telemetry is fragmented.** The useful signal is split across the Build Event Protocol, JSON trace profiles, execution logs, execution graph logs, and BuildMetrics, each in its own format. Build Event Protocol (BEP) consumers miss most of it: critical-path and sub-action detail live only in profiles and graphs. Native OpenTelemetry support has been requested upstream since 2023 ([bazelbuild/bazel#19158](https://github.com/bazelbuild/bazel/issues/19158), still open). That request needs something only Bazel itself can do: join the caller's trace, and make each action's span the parent of the tools that action spawns. No external sidecar can see or pass on that context.

---

## Proposal

An open-source, time-boxed experiment in three parts. Two of them already have working prototypes; the work is to make them correct, measure them, and get them upstream. The design borrows from remote analysis caching (Skycache) and from Buildbarn's [Bonanza](https://github.com/buildbarn/bonanza) (shared, content-addressed evaluation), adapted to a single machine with many worktrees.

**Where the code lives:**

- **C (telemetry):** a patch to Bazel itself, off by default behind experimental flags, proposed upstream through a design doc in `bazelbuild/proposals` and a stack of small PRs. bazel_conduit stays as the fallback for released Bazel versions and for setups that only use the Build Event Service, and emits the same schema.
- **A and B (analysis cache, concurrency):** a public fork of `bazelbuild/bazel` (Java, Apache-2.0). The fork is for experiments and evidence, not a long-term product. Findings go back upstream as design docs and focused PRs.

All upstream contributions go through Google's CLA.

### C. Native OpenTelemetry in Bazel (committed deliverable)

**Prototype:** a Bazel module that subscribes to the internal event bus and turns the invocation and its actions into OTel spans, written to a JSON file or exported over OTLP (HTTP or gRPC). Flags: `--experimental_otel_trace_json`, `--experimental_otel_trace_endpoint`, `--experimental_otel_trace_all_actions`. Unit tests included.

**Remaining work:**

- **Trace context, in and out.** Join the caller's trace when Bazel is started inside one. Pass each action's span to the processes it spawns, injected at spawn time and kept out of the action digest, so tracing never changes cache keys or causes cache misses.
- **Coverage.** Add the data that today only exists in separate files, each behind its own toggle to control cost and cardinality:

  | Toggle | Content |
  |--------|---------|
  | `build` | Invocation, targets, actions, tests (what BEP carries today) |
  | `profile` | Profiler tasks as spans and events |
  | `spawn` | Per-spawn timing, I/O, cache hit or miss (what the execution log carries) |
  | `exec_graph` | Action dependencies and critical-path drag |
  | `analysis` | Analysis cache hits, resident memory, disk-tier stats (from A) |
  | `server` | Lock wait, concurrent commands, cross-worktree sharing (from B) |

- **Small dependency footprint.** The prototype bundles the OpenTelemetry Java SDK and OTLP exporter. For upstream, prefer sending OTLP protobufs over Bazel's existing gRPC stack, so Bazel gains no new runtime libraries.
- **Stable, safe schema.** Export a documented, versioned schema with redaction of command lines and environment variables, not a raw dump of the event bus. Internal event types have no stable schema and can carry secrets.

**Done when:** the design doc and PR stack are submitted upstream; the coverage matrix is published; output works in at least two OTel backends (for example Datadog and Jaeger); and bazel_conduit produces the same schema for released Bazel versions. Merging depends on the Bazel maintainers and is not promised.

### A. Analysis state on local disk (spike, prototype exists)

**Prototype:** a local backend for Bazel's remote analysis caching. Serialized analysis state is kept in a RAM tier plus a disk tier under the output base, reused across commands and across server restarts. Entries are appended to pack files with an in-memory index. Measured on `[TODO: name the workload and configurations]`:

| Scenario | Stock Bazel (re-analysis) | Local cache |
|----------|---------------------------|-------------|
| Restore a configuration on a cold server | 5.7 s | 2.7 s |
| Switch back to a configuration on a warm server | 1.5 s | 0.23 s |

Writing the cache (about 200k entries, 1.5 KB average) takes 4.4 s.

**Open problem:** invalidation. The prototype reports every file version as minimal, so whether a cached entry is safe to reuse rests on the cache key alone, and files behind absolute symlinks are not tracked. It is not safe for general use yet.

**Spike:** implement correct invalidation, then benchmark across edits, worktrees, and server restarts, and measure how much resident heap can be dropped when analysis state can be reloaded from disk.

**Go/no-go:** zero stale results compared with stock Bazel across a defined corpus of edits (source files, BUILD and `.bzl` files, `MODULE.bazel`, toolchain changes), while keeping at least half of the measured speedup. Otherwise, publish the measurements and the reasons, and stop.

### B. Shared worktrees and concurrent commands (spike)

In increasing order of difficulty:

1. **Shared cache across worktrees.** Point the servers of several worktrees of the same repo at one local analysis cache from A, so identical analysis is computed and stored once. Needs safe concurrent access to the pack files, but no change to Bazel's one-command-per-server model.
2. **Concurrent read-only commands.** Let `query`, `cquery`, `info`, and `version` run alongside a `build` in one server.
3. **One server for many worktrees,** with analysis deduplicated by content in memory.

Sharing across unrelated repos is out of scope: their analysis keys carry different repository mappings and configurations, so little is shareable beyond external dependencies.

**Go/no-go (initial targets):** step 1 gives a second worktree a cache hit rate of at least 50% on analysis `[TODO: revise after baseline]`; step 2 runs read-only commands during a build with zero lock wait and identical results. Step 3 is attempted only if both pass.

---

## Plan (6 months)

| Month | Work |
|-------|------|
| 1 | Rebase and publish both prototypes. Baseline on datadog-agent and one other public repo: memory per server, worktree cost, lock wait. Submit the OpenTelemetry design doc to `bazelbuild/proposals`. |
| 1–3 | C: trace context in and out, coverage toggles, redaction, reduced dependencies; PR stack upstream; bazel_conduit aligned to the same schema. |
| 2–4 | A: invalidation and benchmarks; go/no-go at end of month 4. |
| 3–5 | B: shared cache across worktrees, then concurrent read-only commands; go/no-go at end of month 5. |
| 6 | Upstream package: design docs for A and B, focused PRs, public report with all benchmarks. |

---

## Ask

1. **API credits** to fund agent-assisted work: navigating Bazel's roughly 1.2M lines of Java, building benchmark harnesses, prototyping in the fork, and running correctness comparisons against stock Bazel.
2. **Optional:** technical feedback from build-systems and developer-productivity teams that run Bazel under agentic load.
3. **Optional, after the pilot:** co-sponsorship through the [BUILD Foundation](https://blog.bazel.build/2026/04/08/bazel-q1-2026-community-update.html) if the results justify a longer effort.

**In return, everything is public:** the upstream PRs and design docs, the fork, bazel_conduit, benchmarks and raw data, the telemetry schema, and the go/no-go reports.

---

## What this is not

- Not a replacement for Bazel, and not a competing product. The fork exists to produce evidence for upstream.
- Not a promise of full rule or feature parity in a new engine.
- Not a request to change any company's internal build system.
