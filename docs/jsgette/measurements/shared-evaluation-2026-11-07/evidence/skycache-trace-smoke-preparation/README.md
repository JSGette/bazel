# Frozen real-trace smoke preparation

Source build03 reused the declared build02 state after an exact two-file repair. Both source trees, preserved prior evidence, the stable candidate, complete13-suite BEP and unsignalled guard completion were verified from C-backed archives. The runtime launcher rehashes the candidate before use. This preparation did not read runtime storage or launch a command.

Manifest SHA256: `3bacefc84fae14a315f78539784c42d1923f482a24ec9b2c159cf6ea3b585414`. Candidate SHA256: `ed3b60cfe197b3fc5dcd4208c269b57003550d374563eaff75a193019ee61cba`.

The coordinator can launch the existing guarded wrapper once:

```powershell
& 'C:\Users\jgett\Documents\Codex\2026-11-07\i-ha\work\bazel\docs\jsgette\measurements\shared-evaluation-2026-11-07\harness\run_auxiliary.ps1' -CommandJson 'C:\Users\jgett\Documents\Codex\2026-11-07\i-ha\work\skycache-trace-smoke-command.json' -RunId 'm1-trace-acceptance-01' -TimeoutSeconds 1800
```

Passing proves functional Java trace/replay interoperability and fixture isolation only; it is not a performance or M1 gate verdict.
