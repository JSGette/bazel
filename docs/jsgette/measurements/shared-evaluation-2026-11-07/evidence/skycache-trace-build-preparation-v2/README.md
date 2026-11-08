# Corrected SkyCache trace source qualification

D030's bounded API correction changes the collector and its regression test.
The previous failed build and runtime remain intact. The new source tree is
`d9ca5e8f8e38bdd6c18405f85e1b6d2edabe7889`; the bundle SHA-256 is
`520a271893427ac3a2715d261d522b9b288e2f0a82fcbc5b10103920969932c6`. All 18,592 source files were
verified against the original baseline and the new Git export. The source tar
stays in the workspace; its digest, size and complete membership are recorded.

`instrumentation.patch` applies to baseline tree
`9bcb73e357fe966c3f3442b012b5d8d8d148fd7d`. `combined.patch` applies to commit
`a23ae68903f03f08ce83dcf1be85cdff54bb1eeb`, and its full SHA is the trace
context source identity. `compile-repair.patch` preserves the minimal change
before formatter2; the complete patches describe the final formatted source.
The unchanged wire contract and replay are in the v1 preparation directory.

Run `m1-trace-source-build-02` in fresh `m1-skycache-trace-build-v2`, with the
same six-CPU/10-GiB/no-swap guard, 3-GiB build heap, two active JVM processors,
two jobs and thirteen focused targets. No failed output-base state is reused.
The v2 launcher changes only the root; the archiver changes corresponding
identity paths. Their fifteen offline tests passed; eleven packager/formatter
checks and independent review passed. Actual Java tests remain required.

Formatter2 evidence is preserved separately in
`../auxiliary/m1-trace-format-02/`: identical non-import Java tokens/import
sets, no BUILD formatting changes, and verified absent unsignalled cleanup.

Helpers are exact copies of files executed from `work/`, retaining their
task-relative imports and input paths. This folder preserves provenance rather
than providing a relocated distribution. A successful build permits the fixed
eight-build real-trace smoke; it does not pass M1 or a performance gate.
