# Provenance and attribution

`cdc_experiment.py` embeds a C translation of the cut routine in:

- Project: BuildBuddy fastcdc2020
- File: `fastcdc/fastcdc.go`
- Commit: `2f01aaf2dde317b9875eea63b0c1f3bade2355dd`
- Source: https://github.com/buildbuddy-io/fastcdc2020/blob/2f01aaf2dde317b9875eea63b0c1f3bade2355dd/fastcdc/fastcdc.go
- License: Apache License 2.0, preserved verbatim as `LICENSE.fastcdc2020`.
- Upstream root at that commit has a LICENSE and no NOTICE file. The source has no separate copyright-owner notice; no specific copyright owner/date has been invented here.
- Source SHA256: `90f440e10dac90fc2bb818dd817bb18a90984c5b7869a8b2066024ccef3b6ee8`
- License SHA256: `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4`

Modifications: the cut function was translated from Go to C for an offline Python/ctypes experiment; streaming wrappers were omitted, parameters are direct C function arguments, and upstream GEAR/MASKS constants are retained exactly. Generated C files contain an explicit derivative/modification notice and SPDX Apache-2.0 marker. This is not production code or an unmodified official implementation. Reference tests validate boundaries, not all possible inputs or production performance.

The following are downloaded on reproduction and are NOT redistributed in this evidence bundle:

- Official REAPI FastCDC2020 vectors at commit `c2c07e3d718d3d490a4958344d9c3e47de0bfaf3`: https://github.com/bazelbuild/remote-apis/blob/c2c07e3d718d3d490a4958344d9c3e47de0bfaf3/build/bazel/remote/execution/v2/fastcdc2020_test_vectors.txt
  - SHA256 `f6bce93f38fbdc91321d8c38ac298c725f4421551f0ab926571ab5d026f457bb`; remote-apis repository is Apache-2.0.
- JPEG fixture referenced by those vectors: https://github.com/nlfiedler/fastcdc-rs/blob/49c3d0b/test/fixtures/SekienAkashita.jpg
  - SHA256 `d9e749d9367fc908876749d6502eb212fee88c9a94892fb07da5ef3ba8bc39ed`.
  - The artifact's redistribution provenance was not separately audited; leave it as a hash-verified download rather than copying it into repository evidence.
- The pinned upstream Go source and license (the license is additionally included with this derivative evidence bundle).

Algorithm citations:

- FastCDC 2016: https://www.usenix.org/conference/atc16/technical-sessions/presentation/xia
- 2020 journal extension: https://ranger.uta.edu/~jiang/publication/Journals/2020/2020-IEEE-TPDS%28Wen%20Xia%29.pdf
- REAPI FastCDC parameters: https://github.com/bazelbuild/remote-apis/blob/c2c07e3d718d3d490a4958344d9c3e47de0bfaf3/build/bazel/remote/execution/v2/remote_execution.proto#L2486-L2530

The corpus consists of local benchmark publication packs. No pack contents or upstream paper PDFs are included in this bundle. Results and input hashes identify observations; the one-byte insertion result is an explicitly synthetic control.