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

import static com.google.common.truth.Truth.assertThat;

import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.data.SpanData;
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor;
import java.util.List;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

/** Tests for {@link BufferingSpanExporter}. */
@RunWith(JUnit4.class)
public class BufferingSpanExporterTest {

  private BufferingSpanExporter exporter;
  private SdkTracerProvider tracerProvider;
  private Tracer tracer;

  @Before
  public void setUp() {
    exporter = new BufferingSpanExporter();
    tracerProvider =
        SdkTracerProvider.builder()
            .addSpanProcessor(SimpleSpanProcessor.create(exporter))
            .build();
    tracer = tracerProvider.get("test");
  }

  @After
  public void tearDown() {
    tracerProvider.shutdown();
  }

  @Test
  public void exportBuffersSpans() {
    tracer.spanBuilder("span1").startSpan().end();
    tracer.spanBuilder("span2").startSpan().end();

    List<SpanData> spans = exporter.drainSpans();
    assertThat(spans).hasSize(2);
    assertThat(spans.get(0).getName()).isEqualTo("span1");
    assertThat(spans.get(1).getName()).isEqualTo("span2");
  }

  @Test
  public void drainClearsBuffer() {
    tracer.spanBuilder("span1").startSpan().end();

    List<SpanData> first = exporter.drainSpans();
    assertThat(first).hasSize(1);

    List<SpanData> second = exporter.drainSpans();
    assertThat(second).isEmpty();
  }

  @Test
  public void drainReturnsIndependentCopy() {
    tracer.spanBuilder("span1").startSpan().end();
    List<SpanData> drained = exporter.drainSpans();

    tracer.spanBuilder("span2").startSpan().end();
    assertThat(drained).hasSize(1);
    assertThat(exporter.drainSpans()).hasSize(1);
  }
}
