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

import com.google.devtools.build.lib.util.OptionsUtils;
import com.google.devtools.build.lib.vfs.PathFragment;
import com.google.devtools.common.options.Converters.ByteSizeConverter;
import com.google.devtools.common.options.Option;
import com.google.devtools.common.options.OptionDocumentationCategory;
import com.google.devtools.common.options.OptionEffectTag;
import com.google.devtools.common.options.OptionsBase;

/** Options for the local, disk backed analysis cache. */
public class LocalAnalysisCachingOptions extends OptionsBase {

  @Option(
      name = "experimental_local_analysis_cache",
      defaultValue = "false",
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.BAZEL_INTERNAL_CONFIGURATION},
      help =
          "Backs remote analysis caching with a local store: a bounded in-memory tier over a"
              + " directory on disk. Also permits analysis cache downloads without an analysis"
              + " cache service, and uploads from a server that has already analyzed something.")
  public boolean enabled;

  @Option(
      name = "experimental_local_analysis_cache_path",
      defaultValue = "null",
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.BAZEL_INTERNAL_CONFIGURATION},
      converter = OptionsUtils.PathFragmentConverter.class,
      help =
          "Directory holding the local analysis cache. Defaults to a 'skycache' directory inside"
              + " the output base. Fixed for the lifetime of the server: changing it requires"
              + " 'bazel shutdown'.")
  public PathFragment path;

  @Option(
      name = "experimental_local_analysis_cache_memory_size",
      defaultValue = "512M",
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.BAZEL_INTERNAL_CONFIGURATION},
      converter = ByteSizeConverter.class,
      help =
          "How many bytes of serialized analysis data to keep in memory in front of the on-disk"
              + " cache. Fixed for the lifetime of the server.")
  public long memorySize;

  @Option(
      name = "experimental_local_analysis_cache_max_size",
      defaultValue = "8G",
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.BAZEL_INTERNAL_CONFIGURATION},
      converter = ByteSizeConverter.class,
      help =
          "Size the on-disk cache is swept back down to once the server becomes idle. Zero"
              + " disables sweeping, letting the cache grow without bound.")
  public long maxSize;
}
