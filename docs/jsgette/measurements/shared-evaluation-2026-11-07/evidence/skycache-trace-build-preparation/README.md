# SkyCache trace source qualification inputs

This bundle records the D027 instrumentation before its first source build. The
instrumented tree is `26ad73611f1d5b7e51094e40b07d1290e9a134ae` on the exactly
reconstructed isolated SkyCache baseline. `instrumentation.patch` applies to
baseline tree `9bcb73e357fe966c3f3442b012b5d8d8d148fd7d`; `combined.patch` applies
directly to base commit `a23ae68903f03f08ce83dcf1be85cdff54bb1eeb`. The complete
combined patch hash is the trace context's source patch identity.

All 18,592 source files were verified against the Git export and source member
inventory. The 590,684,160-byte source archive remains in the task workspace;
its digest and exact membership are preserved here. It can be reproduced from
the base plus combined patch. The source tree retains the base HEAD and stages
the combined change in its isolated index; no native-hosting code was added.

The guarded run `m1-trace-source-build-01` uses a fresh runtime root, six CPUs,
10 GiB, no swap, two active JVM processors, a 3-GiB build heap and two build
jobs. It builds `//src:bazel-dev` and runs the thirteen targets listed in
`bundle.json`, then shuts down. Source bytes and binary identity are checked
before and after qualification. No performance or M1 verdict follows from
compilation or unit-test success.

These are exact copies of helpers run from `work/`, preserving their original
task-relative imports and input paths. They are provenance, not a relocated
distribution. `inputs.json` lists every copied artifact. `trace/` preserves the
twenty-test offline replay and contract; actual Java interoperability is next.

Formatting used pinned Google Java Format 1.35.0 and buildifier 10.1.0. Its
separate `../auxiliary/m1-trace-format-01/` evidence records checked copies,
tool hashes, source hashes, diff and absent unsignalled guard cleanup.
