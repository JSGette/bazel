# Incremental trace source qualification v3

This implements D031's declared build-state reuse. The full corrected source
tree is `e8aa57dfd71b302706f16f25389351ff6bb328ff`, bundle SHA-256 `96d2685e027cfe64620281ae0828e28069e17cfb8b73226411c9e61771b3add0`.
The previous tree, all nine instrumentation preimages, exact two repair paths,
and all 18,592 source member hashes are recorded. The complete source archive
remains in the task workspace with size/SHA preserved in the bundle.

Only `M1TraceModule.java` and its test change from v2: generated abstract option
getters replace annotated fields; a parser defaults/explicit-values regression
is added. Flag names/defaults and trace semantics stay fixed. There are 23
collector and eight module tests authored; actual Java execution is pending.

Run `m1-trace-source-build-03` through the unchanged auxiliary guard. Source and
build-output state are reused from `m1-skycache-trace-build-v2` only after its
pinned failed archive, full old source inventory, configuration/tool bytes,
old daemon absence and at least 10 GiB free task space are checked. Preserve
both replacement preimages, apply only the two declared tar members, and
verify every source file against the complete new inventory before building.

Fresh `m1-skycache-trace-qualification-v3` holds current logs, build/test BEPs,
report and candidate. Commands retain v2's build policy/output state and move
only the two BEP destinations. The product build, thirteen focused targets,
stable post-build/post-test binary bytes and shutdown remain required. Old
v2 report/log/BEP/source archive evidence is rehashed after the run; the reused
source is explicitly recorded as changed by this qualification.

`instrumentation.patch` applies to isolated SkyCache baseline tree
`9bcb73e357fe966c3f3442b012b5d8d8d148fd7d`; `combined.patch` applies directly
to base `a23ae68903f03f08ce83dcf1be85cdff54bb1eeb`. The latter's SHA is
the source identity used in trace contexts. The minimal module patch is a
pre-format record; full patches describe the exact formatted source.

Formatter03 passed: no BUILD changes; the only token reorder was the equivalent
`static abstract`/`abstract static` declaration order. Its independent archive
and host-preflight evidence are in `../auxiliary/m1-trace-format-03/`.

The helpers are copies of files run from `work/` and preserve those original
task-relative imports and paths. The inherited v2 build helper and native
build helper remain unchanged in the earlier preserved preparations. This
is functional compilation qualification, not a benchmark or M1 verdict.
