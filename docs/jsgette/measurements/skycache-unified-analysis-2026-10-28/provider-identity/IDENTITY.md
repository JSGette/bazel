# Why the current identity failures need provenance

28 October 2026. Investigation of the existing four-case fixture; no codec,
identity policy, admission policy or wire-format change is proposed for this
implementation slice.

`StarlarkInfoNoSchema.equals` and `hashCode` compare the provider field table.
`Depset.equals` and `hashCode` use the identity of the underlying `NestedSet`.
`DeferredNestedSetCodec` encodes depth, order and children, then constructs a
fresh wrapper when decoding. It can share a child array without sharing the
wrapper. Separate shared records use fresh memo contexts, so reference memoization
within one record cannot connect the wrappers restored by different records.

Consider two wrappers with the same nonempty children, depth and order. They may
be two references to one original wrapper, or two distinct original wrappers.
The current wrapper payload contains no origin discriminator. If those cases
have the same encoding `E`, a decoder receiving only `E` cannot reconstruct
which identity relation held before serialization. This missing information is
why content-based interning cannot satisfy both existing controls.

| Proposed shortcut | Why it does not establish the contract |
| --- | --- |
| Intern by child-array pointer or fingerprint | Merges distinct original wrappers with identical children |
| Intern equal provider field tables | Changes semantics of identity-bearing depset fields |
| Reuse only a per-record memo table | Does not connect independently restored records |
| Turn on the general serialization/deserialization linked cache | Adds ownership and memory cost without specifying cross-process origin semantics |
| Assign an ID to every newly recomputed allocation | Does not prove that a fresh allocation represents the same semantic origin as a cached allocation |

A future positive design needs an explicit, versioned origin/import contract for
an audited value family. Preserve the existing provider/depset equality rules
and weak `NOT_LINKED` fingerprint cache. An origin-aware codec could use weak
writer registration and weak reader canonicalization with single-flight decode,
while validating origin, type, order and content together. It must preserve
imported origins across publication boundaries. Capacity exhaustion must reject
admission rather than replace a still-live identity representative.

That design can reunite a still-live original wrapper with its restored origin
only when both participate in the same explicit registry. Mixed recomputation
needs an additional verified semantic export/import mapping or recomputation of
the affected region. An owner-and-ordinal scheme from another value family is
not evidence that arbitrary depset origins have that mapping.

Required future tests include the existing four assertions unchanged; distinct
writer namespaces with equal contents; concurrent restoration of one origin;
live representatives surviving unrelated weak-cache collection; dead entries
releasing their references; conflicting origin/content rejection; bounded
admission; cold-reader and republished-import cases; and rejection of unproven
recomputed-allocation correspondence. Until then, the known failure remains
explicit and supplies no positive eligibility claim.

Source touchpoints:

- [`StarlarkInfoNoSchema`](../../../../../src/main/java/com/google/devtools/build/lib/packages/StarlarkInfoNoSchema.java)
- [`Depset`](../../../../../src/main/java/com/google/devtools/build/lib/collect/nestedset/Depset.java)
- [`DeferredNestedSetCodec`](../../../../../src/main/java/com/google/devtools/build/lib/collect/nestedset/DeferredNestedSetCodec.java)
- [`SharedValueSerializationContext`](../../../../../src/main/java/com/google/devtools/build/lib/skyframe/serialization/SharedValueSerializationContext.java)
- [`MemoizingSerializationContext`](../../../../../src/main/java/com/google/devtools/build/lib/skyframe/serialization/MemoizingSerializationContext.java)
- [`RemoteAnalysisCacheDeps`](../../../../../src/main/java/com/google/devtools/build/lib/skyframe/serialization/analysis/RemoteAnalysisCacheDeps.java)
