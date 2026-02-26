// Copyright 2025 The Bazel Authors. All rights reserved.
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
package com.google.devtools.build.lib.oteltracing;

import com.google.devtools.build.lib.util.OptionsUtils;
import com.google.devtools.build.lib.vfs.PathFragment;
import com.google.devtools.common.options.Option;
import com.google.devtools.common.options.OptionDocumentationCategory;
import com.google.devtools.common.options.OptionEffectTag;
import com.google.devtools.common.options.OptionMetadataTag;
import com.google.devtools.common.options.OptionsBase;

/** Options for OpenTelemetry trace export. */
public class OTelTraceOptions extends OptionsBase {

  @Option(
      name = "experimental_otel_trace_json",
      defaultValue = "null",
      converter = OptionsUtils.EmptyToNullPathFragmentConverter.class,
      documentationCategory = OptionDocumentationCategory.LOGGING,
      effectTags = {OptionEffectTag.AFFECTS_OUTPUTS},
      metadataTags = {OptionMetadataTag.EXPERIMENTAL},
      help =
          "If set, write the build's OTel trace to the given file in OTLP JSON format. "
              + "The file is written as a complete snapshot at the end of the build.")
  public PathFragment otelTraceJsonFile;

  @Option(
      name = "experimental_otel_trace_endpoint",
      defaultValue = "null",
      documentationCategory = OptionDocumentationCategory.LOGGING,
      effectTags = {OptionEffectTag.AFFECTS_OUTPUTS},
      metadataTags = {OptionMetadataTag.EXPERIMENTAL},
      help =
          "If set, stream OTel trace spans in real-time via OTLP to the given endpoint "
              + "(e.g. http://localhost:4318).")
  public String otelTraceEndpoint;

  @Option(
      name = "experimental_otel_trace_all_actions",
      defaultValue = "false",
      documentationCategory = OptionDocumentationCategory.LOGGING,
      effectTags = {OptionEffectTag.AFFECTS_OUTPUTS},
      metadataTags = {OptionMetadataTag.EXPERIMENTAL},
      help = "If enabled, create OTel spans for all actions, not just failed ones.")
  public boolean otelTraceAllActions;

  public boolean isEnabled() {
    return otelTraceJsonFile != null || otelTraceEndpoint != null;
  }
}
