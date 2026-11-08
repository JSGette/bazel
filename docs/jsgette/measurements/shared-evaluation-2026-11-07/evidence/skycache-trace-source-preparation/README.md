# Reconstructed SkyCache baseline source

Prepared on C: on 2026-11-08. No build, instrumentation, WSL invocation or runtime-storage read was performed. The existing fork checkout and its Git metadata were not changed by reconstruction.

The source is in `../source/`. It is the exact tracked Git content obtained from the preserved base plus the complete safe-subset patch, with all 37 changed paths matching the historical validation manifest. The historical binary identity is retained as provenance; this preparation does not prove a fresh reproducible binary build or recreate ignored/generated files from the original working directory.

| Input/result | Identity |
| --- | --- |
| Base commit | `a23ae68903f03f08ce83dcf1be85cdff54bb1eeb` |
| Base Git tree | `8cd781b2cb31b40d07684d5c321976e0e94e70f4` |
| `base-source.tar`, 590,417,920 bytes | SHA-256 `2e9e828e002882d63383bec4533b28882f5e5be2902ee0275e4fe6687a5a8acf` |
| `safe-subset.patch`, 282,393 bytes | SHA-256 `238cac7dffac4ca6a89ca144d59643ecb60e465d0dbeab0b21da0c1a2a56eb7e` |
| Historical validation manifest | SHA-256 `cf159cdf56c464f32e00aff205318bba6893190436c599d9b65c969711d9e640` |
| Reconstructed Git tree | `9bcb73e357fe966c3f3442b012b5d8d8d148fd7d` |
| Historical safe-subset binary | SHA-256 `11547ce20ac9edcaa14c48206ac14618012d898ef24d4bbf6e0ac229b44b1e25` |

`prepare_source.py` records the complete native-Git commands in `reconstruction.json`. Extraction verified all 18,582 base blobs; the patched index and filesystem then verified all 18,588 resulting blobs. `source-members.json` records every path, Git mode/blob ID, length and SHA-256. All seven source hashes from the preserved `audited-source-notes.md` matched, including the modified cache session, client, snapshot and module. No symlinks or submodules occur in this base.

The new source repository has an isolated index and object directory, with a read-only alternates reference to the existing C-backed object store. Its HEAD remains the base commit; the 37 patch paths are deliberately staged, and the unstaged diff is empty. New patch objects/tree were written only in this repository. Git/index/archive executable modes are preserved; Windows filesystem modes are not Unix permission evidence. Future instrumentation must be a separate diff against the recorded reconstructed tree.

The complete source archive and member verification are now finished. Only bounded source reads followed; no bulk source work should overlap subsequent timed benchmark arms. See `trace-api-audit.md` and `trace-seams.json` for the trace-hook review.
