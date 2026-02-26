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

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.data.SpanData;
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

/** Tests for {@link OTelJsonFileWriter}. */
@RunWith(JUnit4.class)
public class OTelJsonFileWriterTest {

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
  public void writeProducesValidOtlpJson() throws Exception {
    Span parent = tracer.spanBuilder("root").startSpan();
    parent.setAttribute(AttributeKey.stringKey("key"), "value");
    parent.setAttribute(AttributeKey.longKey("count"), 42L);
    parent.setStatus(StatusCode.OK);
    parent.end();

    Span child =
        tracer
            .spanBuilder("child")
            .setParent(io.opentelemetry.context.Context.current().with(parent))
            .startSpan();
    child.setStatus(StatusCode.ERROR, "something failed");
    child.end();

    List<SpanData> spans = exporter.drainSpans();
    ByteArrayOutputStream out = new ByteArrayOutputStream();
    OTelJsonFileWriter.write(spans, out);

    String json = out.toString(StandardCharsets.UTF_8);
    JsonObject root = JsonParser.parseString(json).getAsJsonObject();

    assertThat(root.has("resourceSpans")).isTrue();
    JsonArray resourceSpans = root.getAsJsonArray("resourceSpans");
    assertThat(resourceSpans).hasSize(1);

    JsonObject rs = resourceSpans.get(0).getAsJsonObject();
    JsonArray scopeSpans = rs.getAsJsonArray("scopeSpans");
    assertThat(scopeSpans).hasSize(1);

    JsonObject ss = scopeSpans.get(0).getAsJsonObject();
    assertThat(ss.getAsJsonObject("scope").get("name").getAsString()).isEqualTo("bazel");

    JsonArray spanArray = ss.getAsJsonArray("spans");
    assertThat(spanArray).hasSize(2);

    JsonObject rootSpan = spanArray.get(0).getAsJsonObject();
    assertThat(rootSpan.get("name").getAsString()).isEqualTo("root");
    assertThat(rootSpan.has("traceId")).isTrue();
    assertThat(rootSpan.has("spanId")).isTrue();
    assertThat(rootSpan.has("startTimeUnixNano")).isTrue();
    assertThat(rootSpan.has("endTimeUnixNano")).isTrue();

    JsonObject status = rootSpan.getAsJsonObject("status");
    assertThat(status.get("code").getAsInt()).isEqualTo(1); // OK

    JsonArray attrs = rootSpan.getAsJsonArray("attributes");
    assertThat(attrs.size()).isAtLeast(2);

    JsonObject childSpan = spanArray.get(1).getAsJsonObject();
    assertThat(childSpan.get("name").getAsString()).isEqualTo("child");
    assertThat(childSpan.has("parentSpanId")).isTrue();
    JsonObject childStatus = childSpan.getAsJsonObject("status");
    assertThat(childStatus.get("code").getAsInt()).isEqualTo(2); // ERROR
    assertThat(childStatus.get("message").getAsString()).isEqualTo("something failed");
  }

  @Test
  public void writeEmptySpanList() throws Exception {
    ByteArrayOutputStream out = new ByteArrayOutputStream();
    OTelJsonFileWriter.write(List.of(), out);

    String json = out.toString(StandardCharsets.UTF_8);
    JsonObject root = JsonParser.parseString(json).getAsJsonObject();
    JsonArray resourceSpans = root.getAsJsonArray("resourceSpans");
    JsonObject rs = resourceSpans.get(0).getAsJsonObject();
    JsonArray scopeSpans = rs.getAsJsonArray("scopeSpans");
    JsonArray spanArray = scopeSpans.get(0).getAsJsonObject().getAsJsonArray("spans");
    assertThat(spanArray).hasSize(0);
  }
}
