# SkyCache architecture experiments — preregistered 2026-10-21

Four experiments will run in order on a separate experimental branch. Preparation/review may be
parallel; source builds and measurements are sequential. The implementation branch stays at
`6f451b2dc6`. Every change, command, hash, failure and decision will be retained. This protocol is
registered before the experimental source edits or measurements.

| Experiment | Question | Preparation cap | Execution cap |
|---|---|---:|---:|
| E1: reader-only source scan avoidance | Can a command forbidden from publishing omit the initial whole-tree guard while keeping all read validation? | 20 min | 10 min |
| E2: bounded sealed-pack bulk loading | Does trading at most 512 MiB heap for bulk transfer beat many small reads? | 20 min | 10 min |
| E3: compact batch input certificate | Is fresh checking of the same deduplicated input set cheaper than proof-DAG replay? | 25 min | 15 min |
| E4: selective synchronous analysis frontier | Does existing frontier selection reduce writer/representation cost? | 25 min | 15 min |

E1 uses two fresh-server readers, normal candidate and dependency checks, and prohibits publication.
It is not a writable fast path. E2 adds bounded bulk loading to E1; index/key/header/checksum checks
remain, with the explicit immutable-snapshot behavior after later on-disk corruption. E3 is a
standalone mechanism benchmark using real dependency roots: two warmups and three measured rounds
per path, preparation cost, and negative content/listing/symlink probes. Its result is not a full-build
speedup. E4 uses a new publisher and two readers, with execution nodes excluded before serialization
and the graph retained. The active-directory flag alone is insufficient in the current async mode.

Budget: at most 15 Datadog build attempts, including preparations and controls; no automatic retries.
Each build has a 180-second timeout plus bounded shutdown. All use native WSL ext4 on E: NVMe,
32 jobs, Xmx6g and 6144 MiB action memory. Baselines use the same experimental binary with experiment
switches disabled. Full command time, phase times, CPU, PSS, hits, traffic, output hashes, action-cache
state and failures are recorded. One repair maximum per failed experimental integration, within the
stated timebox. Unexpected resource/correctness failures stop that experiment.

Select one or two directions using measured reader/writer economics, memory, correctness and
implementation feasibility. A faster unsafe shortcut cannot win. No implementation will be promoted
merely because an isolated microbenchmark improves. Raw evidence stays private with public curated
results and hashes; docs/results go to `jsgette/master`, experiment code to its separate pushed branch.
