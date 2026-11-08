# Captured publication witnesses — 2026-11-08

**Finding:** two publications in the older namespace record **12 conflicting fingerprints at the same logical external-input keys**, including ten repository bindings. This establishes a concrete incompatibility in their recorded publication-wide guards despite identical recorded source digests. It does not identify the historical first rejection or prove which client produced either publication.

The coordinator captured metadata after the matrix and diagnostics, at 01:42:00–01:42:19 UTC. This analysis read only those copies. The [inventory](evidence/skycache-i10-inventory-01/inventory.json) contains twenty manifests in two namespaces, ten each, totaling 14,200 bytes. The selected namespace is `61a771c496a254f6c2106d78c2c7a6747a831bb958bda6f4cd36dad5546cb3f4`; the two oldest manifest mtimes within it supplied the pair. That ordering is a heuristic, not a verified `c00/c01` association.

- **Left:** `b0dfa8a0f3a2561eb6f5b16593772127f46beec3dee5f712f7f8efafcc3f9ce4`.
- **Right:** `6d3abc8dc84905f386741e23d4988fb88245b693b54e0971f3e5136de3426200`.
- Both contain 33,938 witnesses, nine blocks, 5,787,093 charged metadata bytes, no parent publications, and recorded source digest `10f5a41cc23356c45c38e534117505a461926643007ab4bbf235e3121cf41590`. Recorded digest equality does not establish full semantic equivalence.

| Compared records | Count | What the copies establish |
|---|---:|---|
| Same logical path/operation and fingerprint | 33,715 | Matching recorded witnesses |
| Same logical path/operation, different fingerprint | 12 | Conflicting recorded input evidence; all twelve are non-listing witnesses |
| Present only in the left table | 211 | Different recorded physical-path coverage |
| Present only in the right table | 211 | Different recorded physical-path coverage |

All 422 one-sided records are beneath `absolute//mnt/shared-evaluation-20261107/repository-cache-v7/contents/`, spanning ten bucket/UUID roots per side. **Absence from the other table is not filesystem absence or an invalid witness.** An old physical tree may remain available and valid. These records do not independently explain rejection.

The twelve conflicting keys are exact records in the [comparison](evidence/skycache-i10-pair-01/comparison.json):

- Nine bindings with prefix `external/gazelle++go_deps+`: `com_github_bahlo_generic_list_go`, `com_github_buger_jsonparser`, `com_github_invopop_jsonschema`, `com_github_mailru_easyjson`, `com_github_spf13_cobra`, `com_github_spf13_pflag`, `com_github_wk8_go_ordered_map_v2`, `in_gopkg_yaml_v3`, `in_yaml_go_yaml_v2`.
- The tenth binding: `external/gazelle++non_module_deps+bazel_gazelle_go_repository_tools`.
- `external/gazelle++non_module_deps+bazel_gazelle_go_repository_cache/go.env`: left fingerprint `61ec4b518745cb5e6d13d7ad5a5842e681333bcc84723a4b4597db26d7bf27b3`, right `beb42e42f522c7769e4714a6a160761e6ddff85e3a78b773e878cbf10011d23d`.
- `external/rules_java++toolchains+local_jdk/BUILD.bazel`: left fingerprint `b7fb04ceac643e6434691fe3903219ddab407bfb456d4f0949d56b2a3a31324c`, right `ef1c44d0c9ce88e4d09eb2b718e0bb4c1d38ec9699bb590d943e43d37aff935a`.

These are SkyCache fingerprints, not bare file-content hashes: their source algorithm includes type/permissions and, where relevant, symlink targets/referents. No current file contents, permissions or link targets were read. The copies therefore do not establish which field differed, whether tool binaries were byte-identical, or whether `go.env` was the first rejected input.

The [source audit](m0-cross-worktree-reuse-notes.md) establishes that one mismatched global non-workspace witness rejects a whole publication before root lookup, and top-level repository bindings are checked before other witnesses. **Conditional implication:** if a reader's inputs reproduce the right publication's recorded fingerprints, the left publication fails its global guard; the reverse also holds. This is a sufficient incompatibility in the recorded states, not a replay of the actual rejection sequence. The archived initial preparation already showed zero admitted candidates/root validation with rejected input witnesses; the copied metadata now supplies concrete candidate mismatches consistent with that failure stage.

This sharpens the next architectural decision: make portable external-repository identity and publication-admission granularity explicit before assuming that identical worktrees can share analysis roots. An immutable repository result and stable logical paths are worth testing, but the test must preserve content, environment, permission and symlink semantics. Merely removing paths from hashes or discarding global witnesses is unsupported. A future cross-worktree probe should prove imports from another publication and include negative controls; prepared same-output-base restart hits remain a separate capability. No N2 probe, product change or milestone verdict was performed here.

Verification rechecked both copied manifests, both decoded JSON files and all eighteen copied witness blocks against the [capture record](evidence/skycache-i10-pair-01/capture.json). The records retain every exact fingerprint and source path. Checksums bind these copies to the capture evidence; they are not an independent authenticity proof.

| Evidence file | Rechecked SHA-256 |
|---|---|
| `skycache-i10-inventory-01/inventory.json` | `abccfd302480dda32fd299daf8db317746daeb82edf5a4ea3bc7f57382b5b37f` |
| `skycache-i10-pair-01/capture.json` | `ad32a1409b94c1eaa83f302b28b4701db5938cdb17395648bb529f9b3c484ca9` |
| `skycache-i10-pair-01/comparison.json` | `9429b5478d8ad8039d3d26dad4f0dba162dcf4553055524ca7d87a6ce5ac7979` |
