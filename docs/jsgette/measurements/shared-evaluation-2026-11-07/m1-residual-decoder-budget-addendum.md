# Decoder-only admission correction

Frozen 2026-11-12 before attempt `m1-residual-decode-02`.

The inherited build-sized 10-GiB auxiliary wrapper timed out its 120-second
host-budget preflight for `m1-residual-decode-01`. It never launched the decoder
or created a guard scope. Preserve the complete preflight/control/reset files.
The wrapper exited, the exact owned unit pattern was absent, and no decoder
output directory existed before retiring that failed admission owner. No other
workload was stopped or altered.

Use a decoder-only copy of the same wrapper, restricted to the literal archived
decoder command SHA-256
`e42fa300b69b3f899efb1c249f7ea0e48f3ce4b815902ba6a4621ebb4060077d`
and run ID `m1-residual-decode-02`. Its cgroup maximum is **1536 MiB**; the
decoder heap remains **512 MiB**. Admission still requires the entire new job
budget plus the unchanged 3072-MiB physical / 4096-MiB commit reserves and
512-MiB margin: 5 GiB available physical and 6 GiB available commit. Keep two
consecutive samples, the 120-second preflight timeout, six-CPU ceiling,
zero-swap guard, ten-minute job limit and all ownership/cleanup checks.

Wrapper SHA-256:
`a004c197ceac7ab7ce1ef1bb91260c527a5bf33d733d16f09c7930569b3dc4bf`.
The preserved generator records the exact constant substitutions from the
unchanged original wrapper, plus the run/command restriction. No build or
performance experiment uses this smaller wrapper. The decoder still processes
only the original two recordings, at the original depth/output limits, once.
This changes resource admission for offline decoding, not a benchmark
denominator, architecture threshold, host reserve or measurement boundary.

If this admission or decoding fails, retain the failure and finish the residual
assessment with that unavailable evidence; do not start another capture or
expand the investigation. The original residual protocol remains immutable and
this addendum is separately hashed in the final analysis manifest.
