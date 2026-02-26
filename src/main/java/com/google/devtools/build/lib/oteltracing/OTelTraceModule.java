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

import com.google.common.annotations.VisibleForTesting;
import com.google.common.collect.ImmutableList;
import com.google.devtools.build.lib.actions.ActionKeyContext;
import com.google.devtools.build.lib.events.Event;
import com.google.devtools.build.lib.runtime.BlazeModule;
import com.google.devtools.build.lib.runtime.CommandEnvironment;
import com.google.devtools.common.options.OptionsBase;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.sdk.resources.Resource;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.SpanProcessor;
import io.opentelemetry.sdk.trace.export.BatchSpanProcessor;
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor;
import io.opentelemetry.sdk.trace.export.SpanExporter;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;
import java.util.logging.Logger;
import javax.annotation.Nullable;

/**
 * A {@link BlazeModule} that exports an OpenTelemetry trace for each Bazel invocation.
 *
 * <p>The module is completely lazy: if neither {@code --experimental_otel_trace_json} nor {@code
 * --experimental_otel_trace_endpoint} is set, no OTel SDK objects are created and no EventBus
 * subscriptions are made.
 */
public class OTelTraceModule extends BlazeModule {

  private static final Logger logger = Logger.getLogger(OTelTraceModule.class.getName());

  @Nullable private SdkTracerProvider tracerProvider;
  @Nullable private OTelSpanMapper mapper;
  @Nullable private BufferingSpanExporter bufferingExporter;
  @Nullable private OTelTraceOptions options;
  @Nullable private CommandEnvironment env;

  @Override
  public Iterable<Class<? extends OptionsBase>> getCommonCommandOptions() {
    return ImmutableList.of(OTelTraceOptions.class);
  }

  @Override
  public void beforeCommand(CommandEnvironment env) {
    this.env = env;
    options = env.getOptions().getOptions(OTelTraceOptions.class);
    if (options == null || !options.isEnabled()) {
      return;
    }

    List<SpanProcessor> processors = new ArrayList<>(2);

    if (options.otelTraceEndpoint != null) {
      SpanExporter otlpExporter =
          createOtlpExporter(options.otelTraceEndpoint.trim());
      if (otlpExporter != null) {
        processors.add(BatchSpanProcessor.builder(otlpExporter).build());
      }
    }

    if (options.otelTraceJsonFile != null) {
      bufferingExporter = new BufferingSpanExporter();
      processors.add(SimpleSpanProcessor.create(bufferingExporter));
    }

    if (processors.isEmpty()) {
      return;
    }

    Resource resource =
        Resource.create(
            Attributes.of(AttributeKey.stringKey("service.name"), "bazel"));
    var providerBuilder =
        SdkTracerProvider.builder().setResource(resource);
    for (SpanProcessor processor : processors) {
      providerBuilder.addSpanProcessor(processor);
    }
    tracerProvider = providerBuilder.build();

    Tracer tracer = tracerProvider.get("bazel");
    ActionKeyContext actionKeyContext = null;
    if (env.getSkyframeExecutor() != null) {
      actionKeyContext = env.getSkyframeExecutor().getActionKeyContext();
    }
    mapper =
        new OTelSpanMapper(
            tracer,
            options.otelTraceAllActions,
            actionKeyContext,
            env.getReporter());
    env.getEventBus().register(mapper);
  }

  @Override
  public void afterCommand() {
    if (tracerProvider == null) {
      cleanup();
      return;
    }

    try {
      if (bufferingExporter != null && options != null && options.otelTraceJsonFile != null) {
        writeJsonFile();
      }
      tracerProvider.forceFlush().join(10, TimeUnit.SECONDS);
    } finally {
      tracerProvider.shutdown().join(5, TimeUnit.SECONDS);
      cleanup();
    }
  }

  private void writeJsonFile() {
    String path = options.otelTraceJsonFile.getPathString();
    try (OutputStream fos = new FileOutputStream(path)) {
      OTelJsonFileWriter.write(bufferingExporter.drainSpans(), fos);
    } catch (IOException e) {
      if (env != null) {
        env.getReporter().handle(Event.warn("Failed to write OTel trace JSON file: " + e));
      }
      logger.warning("Failed to write OTel trace JSON: " + e.getMessage());
    }
  }

  private void cleanup() {
    if (env != null && mapper != null) {
      env.getEventBus().unregister(mapper);
    }
    tracerProvider = null;
    mapper = null;
    bufferingExporter = null;
    options = null;
    env = null;
  }

  /**
   * Creates an OTLP span exporter for the given endpoint. Protocol is chosen by URL prefix:
   * http:// or https:// → OTLP over HTTP; grpc:// or grpcs:// → OTLP over gRPC. Returns null if
   * the exporter could not be created (e.g. due to missing classes at runtime).
   */
  @Nullable
  private SpanExporter createOtlpExporter(String endpoint) {
    if (endpoint.isEmpty()) {
      return null;
    }
    String lower = endpoint.toLowerCase(java.util.Locale.US);
    boolean useHttp =
        lower.startsWith("http://") || lower.startsWith("https://");
    boolean useGrpc =
        lower.startsWith("grpc://") || lower.startsWith("grpcs://");

    try {
      if (useHttp) {
        String base = endpoint.endsWith("/") ? endpoint.substring(0, endpoint.length() - 1) : endpoint;
        String tracesEndpoint = base.endsWith("/v1/traces") ? base : base + "/v1/traces";
        return io.opentelemetry.exporter.otlp.http.trace.OtlpHttpSpanExporter.builder()
            .setEndpoint(tracesEndpoint)
            .setTimeout(10, TimeUnit.SECONDS)
            .build();
      }
      if (useGrpc) {
        return io.opentelemetry.exporter.otlp.trace.OtlpGrpcSpanExporter.builder()
            .setEndpoint(endpoint)
            .setTimeout(10, TimeUnit.SECONDS)
            .build();
      }
      // No scheme: assume gRPC (e.g. localhost:4317) for backward compatibility
      return io.opentelemetry.exporter.otlp.trace.OtlpGrpcSpanExporter.builder()
          .setEndpoint(endpoint)
          .setTimeout(10, TimeUnit.SECONDS)
          .build();
    } catch (Exception e) {
      logger.warning("Could not create OTLP exporter for " + endpoint + ": " + e.getMessage());
      if (env != null) {
        env.getReporter()
            .handle(Event.warn("Could not create OTLP exporter: " + e.getMessage()));
      }
      return null;
    }
  }

  @VisibleForTesting
  OTelSpanMapper getMapper() {
    return mapper;
  }

  @VisibleForTesting
  SdkTracerProvider getTracerProvider() {
    return tracerProvider;
  }
}
