# Reproduce the offline CDC investigation

Requirements: Linux (or WSL), Python 3 standard library, gcc, HTTPS access to raw.githubusercontent.com, and the original `perf-demand-2/cache-*` publication packs. The corpus is read-only. No Bazel build, production code changes, package installation, or remote-cache mutation occurs.

Run from any directory; choose an output directory that does not exist:

```sh
python3 cdc_experiment.py --data /home/jgett/REPOS/.work/skycache-remote-mvp/perf-demand-2 --output /tmp/skycache-cdc-reproduction
python3 cdc_cpu.py --output /tmp/skycache-cdc-reproduction
```

The first script downloads four hash-pinned reference files (Go source, its license, REAPI vectors, JPEG fixture), translates the Go cut routine and original tables to C, compiles a private shared library, and verifies both official vector seeds before testing the corpus. Generated outputs remain in the new directory. It refuses to overwrite an existing output directory. All corpus records are parsed and payload hashes checked.

`results.json` contains deterministic byte/chunk metrics plus a boundary-only CPU sample. `chunk_and_hash_cpu.json` contains separate seven-trial chunk-plus-SHA256 timings. Byte/chunk metrics must match when inputs match; CPU times vary. `pack_inventory.json` is generated to identify every source pack. `input-packs.sha256.tsv` in this bundle lists the 108 distinct baseline packs and an example relative source path for each (exact copies under other snapshots are intentionally deduplicated).

The included results are the original 2026-10-25 measurement, not overwritten by the portable-script validation rerun. Both scripts were rerun in a fresh directory; all deterministic sections matched. Scope and limitations are in `findings.md`.

Copy this entire `repro/` directory into repository evidence if desired. Do not copy the parent scratch directory: it also contains generated binaries, downloaded source, JPEG fixture, and a larger inventory. Keep `PROVENANCE.md` and `LICENSE.fastcdc2020` alongside the scripts, because the embedded C cut routine is a derivative of Apache-2.0 upstream code.