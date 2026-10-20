// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.versioning;

import com.google.devtools.build.lib.vfs.Path;
import java.io.IOException;
import java.util.List;

/** Opt-in content evidence for backends that validate dependency graphs without source versions. */
public interface IndependentFileDependencyProvider extends LongVersionGetter {
  /**
   * Captures deterministic, self-contained evidence for this file or listing operation.
   *
   * <p>The payload must include every supplied parent dependency key, path resolution and content
   * evidence needed by this backend's reader. The serializer stores the payload under a content
   * fingerprint and embeds that key in the existing invalidation dependency tree. Implementations
   * must also reject publication if observed inputs change before the invocation finishes.
   *
   * <p>These opaque payloads are only meaningful to a matching backend reader. Opting in does not
   * authorize reusing an entry without validating its complete transitive dependency evidence.
   */
  byte[] captureFileDependency(Path path, boolean listing, List<String> parentKeys)
      throws IOException;

  @Override
  default boolean usesIndependentInvalidationData() {
    return true;
  }
}
