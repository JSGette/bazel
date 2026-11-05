# Committed hints for cold graph reconstruction

## What would a Git-aware driver and a committed index each contribute?

### Takeaway

A driver can answer requests for metadata or bytes against a pinned base plus known overlay edits. An index can save parsing by recording older structural answers. They are independent choices. The plain parser and the indexed controls in this fixture consult the same driver.

### Cited findings

- Reproduce from the workspace root with `python experiments/index_probe.py --output experiments/index_results.json`. The Python standard library and a Git executable with SHA-1 repository support are sufficient. The script creates its own temporary repository and commit, checks each result against an independently specified expected graph, and disposes of the repository. It ran on Windows with Python 3.11.9 and Ubuntu under WSL with Python 3.12.3. Both produced the same fixture commit and canonical JSON SHA-256, `0bde1a79b1c8efe99a3498884f94210b07ccdb4e927ccce98a95e96aa764c839`. macOS execution remains unverified. [Fixture, driver and oracles](../../experiments/index_probe.py) · [Captured results](../../experiments/index_results.json)
- A package can declare an unconditional dependency or a dependency conditional on the presence of a named sibling file. These are the complete semantics of the fixture language. Each shard records its package, schema version, parser version, Git blob ID of the package definition, observed file-presence answers, and resolved direct dependencies. File presence is a separate input even when the definition's blob ID stays the same. The generator writes shards before committing source and index together, without embedding the containing commit ID. [Generation](../../experiments/index_probe.py)
- The driver resolves a path against a sparse edit map first. A missing path there falls through to `git ls-tree` on the pinned base. A deletion in the map represents absence. Clean source and index data use existing Git blob IDs, with `git cat-file blob` fetching bytes only when requested. This synthetic driver hashes changed bytes when computing their blob identity. Git object format SHA-1 is fixed in the fixture, and files use controlled bytes with automatic line-ending conversion disabled. [Driver](../../experiments/index_probe.py) · [git ls-tree](https://git-scm.com/docs/git-ls-tree) · [git cat-file](https://git-scm.com/docs/git-cat-file)

### Inferences

A source driver can provide a portable read protocol or a place to exploit already-known tree IDs without committing hints. Conversely, a validated hint could be stored in a local or remote cache keyed by its producer and semantic inputs. Committing it trades a populated cold starting point for generation cost, repository churn and a trust decision.

### Gaps

This driver starts with the overlay edits already identified. It cannot establish the cost or consistency of capturing an actively changing directory. Existence tests here go through a general-purpose identity lookup. A specialized existence-only driver could avoid hashing content it need not inspect.

## Does the index reduce demand reconstruction work?

### Takeaway

The validated index eliminated parsing on the clean demand slice but increased both the returned content bytes and the number of explicit Git path lookups. This tiny workload offers no evidence that a committed index makes reconstruction faster.

### Cited findings

- The checked commit `9191466ba786810d09cf8d30ee41572cde6f2ddf` has 64 packages. The generator parsed all 64 and committed 64 shards totaling 9,772 payload bytes. A request for `app` on the clean base reaches only `app`, `lib`, `core`, and `util`. [Generation and ground truth](../../experiments/index_results.json)
- Plain cold reconstruction parsed four definitions totaling 48 bytes, with five Git path lookups. Validated reconstruction parsed zero definitions. It read four index shards totaling 612 bytes, validated four definition identities and one absence observation, and made nine Git path lookups. Both stopped after the same four packages. Unrequested shards were never fetched by the driver. [Base arms](../../experiments/index_results.json)
- Changing `lib` to depend on `alt` made its shard stale. Validation parsed that one 8-byte overlay definition, read 611 bytes of index for four reached packages, and returned the correct graph. Adding an empty `app/feature.flag` selected `extra` for a total of five packages. Validation parsed the 39-byte app definition, read 756 bytes of index, and returned the correct graph. [Overlay arms](../../experiments/index_results.json)

### Inferences

For a real repository, compare against plain demand-driven parsing and an ordinary blob-keyed parse cache. Savings would have to cover index reads, validation, generation and ongoing maintenance. Smaller shards avoid loading unrelated packages while adding per-shard lookup overhead. The toy definitions here are deliberately cheap to parse.

### Gaps

The byte metric counts payloads returned to the evaluator. Git pack IO, path lookups internal to Git, filesystem cache, JSON parsing costs and subprocess startup are outside it. The fixture uses actual subprocesses for clarity. No timing, CPU, RSS or Bazel speedup follows from these counters. The probe did not test a hot local compiler cache, an unsharded index, or real Starlark loads and configuration transitions.

## Under which edits do hints give the wrong graph?

### Takeaway

The blind control produced a stale graph in both adversarial cases. Verification of a trusted generator's input identities and recorded observations restored the expected answer with a one-package fallback.

### Cited findings

- When the `lib/BUILD.toy` blob changed, blind reuse kept the obsolete edge to `core`. When the previously absent `app/feature.flag` appeared, blind reuse omitted the newly reachable package `extra` even though the app definition bytes were identical. Both stale answers failed the manually specified oracle. In both cases the plain parser and validated hint agreed with that oracle. [Failures and controls](../../experiments/index_results.json)
- The remaining cases removed a shard, gave it malformed JSON, or changed its schema. Validated reconstruction fell back to one parse and returned the expected graph each time. [Degraded index cases](../../experiments/index_results.json)

### Inferences

Treat missing or stale hints as misses. A trusted generator must have observed and recorded all inputs affecting its answer. A hash of an unchanged declaration alone cannot validate a file-existence branch or a glob. For an untrusted index, matching the input IDs it declares does not prove those inputs were complete or the associated dependency list was honestly derived.

### Gaps

The parser is deterministic and has only two declarations. The experiment assumes honestly generated hints, known overlay contents and one fixed language version. It supplies no provenance authentication or validation of arbitrary adversarial graph metadata. Real build evaluation would also have to account for environment, toolchain, external repositories, loaded code and other inputs as applicable before reusing an answer.
