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

import io.opentelemetry.sdk.common.CompletableResultCode;
import io.opentelemetry.sdk.trace.data.SpanData;
import io.opentelemetry.sdk.trace.export.SpanExporter;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.List;

/**
 * A {@link SpanExporter} that accumulates finished spans in memory for later retrieval.
 *
 * <p>Used by the JSON-file export path: spans stream into this exporter during the build and are
 * written as a single JSON snapshot in {@code afterCommand()}.
 */
final class BufferingSpanExporter implements SpanExporter {

  private final List<SpanData> spans = Collections.synchronizedList(new ArrayList<>());

  @Override
  public CompletableResultCode export(Collection<SpanData> batch) {
    spans.addAll(batch);
    return CompletableResultCode.ofSuccess();
  }

  @Override
  public CompletableResultCode flush() {
    return CompletableResultCode.ofSuccess();
  }

  @Override
  public CompletableResultCode shutdown() {
    return CompletableResultCode.ofSuccess();
  }

  /** Returns a snapshot of all buffered spans and clears the internal buffer. */
  List<SpanData> drainSpans() {
    synchronized (spans) {
      List<SpanData> result = new ArrayList<>(spans);
      spans.clear();
      return result;
    }
  }
}
