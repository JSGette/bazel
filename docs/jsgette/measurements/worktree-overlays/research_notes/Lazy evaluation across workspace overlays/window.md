# Bounded demand and duplicate suppression

## What does the execution window bound?

### Takeaway

A window on outstanding dependency subscriptions bounded the logical hot frontier in a regular fan-in fixture when the scheduler requested the next inputs on demand. Applying the identical window after expanding the input list left the entire list waiting in RAM.

### Cited findings

- Run from the workspace root with `python experiments/window_probe.py --output experiments/window_results.json`. The captured run used Python 3.11.9 on Windows. Ubuntu under WSL with Python 3.12.3 produced identical JSON after decoding. macOS execution remains unverified. Python's standard library supplies all dependencies. The default is eight root requests and subscription windows of 4 and 16. The flags `--requests` and `--windows` select other counts. This is a deterministic interleaving in a single thread. [Probe](../../experiments/window_probe.py) · [Captured output](../../experiments/window_results.json)
- Each root asks for the first 4,096 leaves in a synthetic namespace designated as having a billion possible names. The other names have no allocated records and are never enumerated. This is an input-generation model, not a repository containing a billion files. All roots need the same 4,095 leaf inputs. Each has its own integer value for the final leaf. A leaf computes the square of its input, so the complete identity is the fixed pure function version and the integer. Summation streams the results to their request owners. An independent sum-of-squares formula checks each root, including each distinct last value. [Fixture and oracle](../../experiments/window_probe.py)
- The eager and cursor runs both requested 32,768 leaves. At W=4 the eager arm held up to 32,768 queued descriptors plus subscribed requests in total. The cursor arm peaked at 4. At W=16 the respective peaks were 32,768 and 16. The cursor used an index over a known ordered dependency stream, with eight additional root accumulators and transient per-operation records for the active subscriptions. Both controls returned the same eight outputs. A 64-leaf arm exercises the same shapes at smaller scale. [Counts and sums](../../experiments/window_results.json)

### Inferences

Demand discovery must cooperate with admission. Streaming a dependency list is cheap here because the fixture can resume enumeration by index and reduce results incrementally. Capping workers above an eager producer does nothing to bound the producer's queue.

### Gaps

The experiment counts logical records. It does not measure RSS, allocator overhead, SQLite page cache, working-set bytes, output materialization, filesystem discovery, or Bazel performance. Every subscriber gets the same admission opportunities in this favorable round-robin fixture. A user-defined build function that produces a large list in one invocation may require a different interface before the consumer can stream it.

## Does bounded in-flight coordination avoid duplicate work?

### Takeaway

Only callers that overlap within a flight can join it. Matching work that arrives after a flight closes needs a completed-result lookup or another means to recover the value. This matters even while all the original builds are still active.

### Cited findings

Each overlap phase has 4,103 distinct complete operation inputs. The cursor arm produced the following results. The SQLite control closes and reopens its database before the late request. The in-flight table is discarded after each wave. Within-wave hits on an unfinished operation increment the join counter. An operation whose value is already durable is returned directly to that individual subscriber, so a later subscriber in the same wave may issue another database lookup. [Algorithms](../../experiments/window_probe.py) · [Recorded counters](../../experiments/window_results.json)

| Subscription window | Completion storage | Executions during overlap | Joins during overlap | DB lookups during overlap | Executions for later replay of root 0 |
|---:|---|---:|---:|---:|---:|
| 4 | In flight only | 8,198 | 24,570 | 0 | 4,096 |
| 4 | SQLite | 4,103 | 12,285 | 20,483 | 0 |
| 16 | In flight only | 4,103 | 28,665 | 0 | 4,096 |
| 16 | SQLite | 4,103 | 28,665 | 4,103 | 0 |

- With W=4, two waves of four requests needed each shared input. Losing all evidence of the first wave doubled execution of the 4,095 common inputs. When W=16, eight callers for each common input fit in one wave. A later replay of root 0 came after every flight had closed. [Window results](../../experiments/window_results.json)
- Both disk-backed arms retained 4,103 input/value rows. The late replay traversed its 4,096 leaf requests again and satisfied all of them from disk. This avoided execution, while leaving discovery and lookup cost visible. [Late-request counters](../../experiments/window_results.json)

### Inferences

Separate active ownership from result retention. A useful experimental contract is one producer per **known, eligible** operation across current demand, with a replayable result retained for an explicit horizon. In a real action cache the metadata must resolve to available outputs. A bit saying “done” cannot serve a late caller after the output disappears. The small window here trades more index probes for a smaller logical frontier. A different arrival order or a small hot-results cache could change the crossover.

### Gaps

The study supplies complete operation keys before execution. It does not establish when two independent analysis lookups with unknown dynamic inputs may share. SQLite is a convenient way to exercise post-eviction lookup. The single-threaded protocol has no racing publishers, crashes, eviction policy or multi-process exactly-once semantics. The integer result in each row stands in for retrievable output data.

## Which state can grow outside the window?

### Takeaway

The number of parked parents or subscribers can grow independently of the number of active operations. Moving these continuations outside the resident frontier exchanges RAM records for storage, lookup and wake-up work.

### Cited findings

- With one active item and a 4,096-deep chain, evaluation parked 4,096 parent frames before unwinding. Switching from a Python stack to a SQLite stack moved all 4,096 parked frame records into database rows. Both resumed every parent and produced −2,048, the independent alternating-sum oracle. The same test also ran at depth 64. The simple recurrence deliberately models a parent that must resume. Its arithmetic has a closed form and does not establish a general lower bound on memory. [Chain probe](../../experiments/window_probe.py) · [Counts](../../experiments/window_results.json)
- In a separate arrival shape, 4,096 callers subscribed before a single eligible operation returned. The in-memory representation held 4,096 subscriber tokens. Streaming a SQLite waiter table used 4,096 rows and an empty Python waiter list. Each delivered 4,096 results with the same subscriber-sensitive checksum. [Waiter probe](../../experiments/window_probe.py) · [Counts](../../experiments/window_results.json)

### Inferences

Specify whether a window limits running operations, dependency subscriptions, parked continuations, or memory bytes. They are different quantities. Admit fewer outstanding subscriptions, represent some demand compactly, or park it with a resume token. Account for per-build state as the number of requesting overlays grows.

### Gaps

An arbitrary evaluator may retain more state in a continuation than this integer fixture. Interruptibility, cycles, dependencies discovered after suspension, cancellation ownership, multi-worker progress, and the cost of durable waiters warrant separate experiments. No tested count is a recommendation for a production window size.
