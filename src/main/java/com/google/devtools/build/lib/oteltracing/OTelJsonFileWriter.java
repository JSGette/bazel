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

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.AttributeType;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.sdk.trace.data.EventData;
import io.opentelemetry.sdk.trace.data.SpanData;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * Writes a list of {@link SpanData} to an {@link OutputStream} in OTLP-JSON-compatible format.
 *
 * <p>The output follows the <a
 * href="https://opentelemetry.io/docs/specs/otlp/#json-protobuf-encoding">OTLP JSON encoding</a>
 * so it can be imported into any OTel-compatible backend.
 */
final class OTelJsonFileWriter {

  private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();

  private OTelJsonFileWriter() {}

  static void write(List<SpanData> spans, OutputStream out) throws IOException {
    JsonObject root = new JsonObject();
    JsonArray resourceSpans = new JsonArray();

    JsonObject rs = new JsonObject();
    rs.add("resource", new JsonObject());

    JsonArray scopeSpans = new JsonArray();
    JsonObject ss = new JsonObject();
    JsonObject scope = new JsonObject();
    scope.addProperty("name", "bazel");
    ss.add("scope", scope);

    JsonArray spanArray = new JsonArray();
    for (SpanData span : spans) {
      spanArray.add(spanToJson(span));
    }
    ss.add("spans", spanArray);
    scopeSpans.add(ss);
    rs.add("scopeSpans", scopeSpans);
    resourceSpans.add(rs);
    root.add("resourceSpans", resourceSpans);

    try (Writer writer = new BufferedWriter(new OutputStreamWriter(out, StandardCharsets.UTF_8))) {
      GSON.toJson(root, writer);
    }
  }

  private static JsonObject spanToJson(SpanData span) {
    JsonObject obj = new JsonObject();
    obj.addProperty("traceId", span.getTraceId());
    obj.addProperty("spanId", span.getSpanId());
    if (!span.getParentSpanId().equals("0000000000000000")) {
      obj.addProperty("parentSpanId", span.getParentSpanId());
    }
    obj.addProperty("name", span.getName());
    obj.addProperty("kind", spanKindToInt(span.getKind()));
    obj.addProperty("startTimeUnixNano", String.valueOf(span.getStartEpochNanos()));
    obj.addProperty("endTimeUnixNano", String.valueOf(span.getEndEpochNanos()));

    JsonArray attrs = attributesToJson(span.getAttributes());
    if (attrs.size() > 0) {
      obj.add("attributes", attrs);
    }

    if (span.getStatus().getStatusCode() != StatusCode.UNSET) {
      JsonObject status = new JsonObject();
      status.addProperty("code", statusCodeToInt(span.getStatus().getStatusCode()));
      if (span.getStatus().getDescription() != null
          && !span.getStatus().getDescription().isEmpty()) {
        status.addProperty("message", span.getStatus().getDescription());
      }
      obj.add("status", status);
    }

    if (!span.getEvents().isEmpty()) {
      JsonArray events = new JsonArray();
      for (EventData event : span.getEvents()) {
        JsonObject ev = new JsonObject();
        ev.addProperty("timeUnixNano", String.valueOf(event.getEpochNanos()));
        ev.addProperty("name", event.getName());
        JsonArray evAttrs = attributesToJson(event.getAttributes());
        if (evAttrs.size() > 0) {
          ev.add("attributes", evAttrs);
        }
        events.add(ev);
      }
      obj.add("events", events);
    }

    return obj;
  }

  @SuppressWarnings("unchecked")
  private static JsonArray attributesToJson(Attributes attributes) {
    JsonArray arr = new JsonArray();
    attributes.forEach(
        (key, value) -> {
          JsonObject attr = new JsonObject();
          attr.addProperty("key", key.getKey());
          JsonObject val = new JsonObject();
          if (key.getType() == AttributeType.STRING) {
            val.addProperty("stringValue", (String) value);
          } else if (key.getType() == AttributeType.LONG) {
            val.addProperty("intValue", String.valueOf(value));
          } else if (key.getType() == AttributeType.BOOLEAN) {
            val.addProperty("boolValue", (Boolean) value);
          } else if (key.getType() == AttributeType.DOUBLE) {
            val.addProperty("doubleValue", (Double) value);
          } else {
            val.addProperty("stringValue", value.toString());
          }
          attr.add("value", val);
          arr.add(attr);
        });
    return arr;
  }

  private static int spanKindToInt(io.opentelemetry.api.trace.SpanKind kind) {
    switch (kind) {
      case INTERNAL:
        return 1;
      case SERVER:
        return 2;
      case CLIENT:
        return 3;
      case PRODUCER:
        return 4;
      case CONSUMER:
        return 5;
    }
    return 0;
  }

  private static int statusCodeToInt(StatusCode code) {
    switch (code) {
      case UNSET:
        return 0;
      case OK:
        return 1;
      case ERROR:
        return 2;
    }
    return 0;
  }
}
