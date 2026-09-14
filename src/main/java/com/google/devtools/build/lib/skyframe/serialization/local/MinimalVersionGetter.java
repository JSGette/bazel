// Copyright 2026 The Bazel Authors. All rights reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.devtools.build.lib.versioning.LongVersionGetter;
import com.google.devtools.build.lib.vfs.Path;

/**
 * A {@link LongVersionGetter} that reports every path as never having changed.
 *
 * <p>Upload requires a version getter, and Bazel does not ship one: file versions exist to support
 * invalidation against a monotonically versioned source of truth, which a local checkout is not.
 *
 * <p>Reporting {@link LongVersionGetter#MINIMAL} everywhere makes every serialized node's maximum
 * transitive source version identical, so reuse safety cannot come from versions. It has to come
 * from the source state being part of the cache key, or from checking a cached node's recorded file
 * dependencies against the set of files that have since changed.
 */
public final class MinimalVersionGetter implements LongVersionGetter {
  public static final MinimalVersionGetter INSTANCE = new MinimalVersionGetter();

  private MinimalVersionGetter() {}

  @Override
  public long getFilePathOrSymlinkVersion(Path path) {
    return MINIMAL;
  }

  @Override
  public long getDirectoryListingVersion(Path path) {
    return MINIMAL;
  }

  @Override
  public long getNonexistentPathVersion(Path path) {
    return MINIMAL;
  }
}
