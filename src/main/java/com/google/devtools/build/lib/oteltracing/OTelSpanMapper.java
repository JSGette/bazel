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

import com.google.common.eventbus.AllowConcurrentEvents;
import com.google.common.eventbus.Subscribe;
import com.google.devtools.build.lib.actions.Action;
import com.google.devtools.build.lib.actions.ActionCompletionEvent;
import com.google.devtools.build.lib.actions.ActionExecutedEvent;
import com.google.devtools.build.lib.actions.CachedActionEvent;
import com.google.devtools.build.lib.actions.Spawn;
import com.google.devtools.build.lib.actions.SpawnExecutedEvent;
import com.google.devtools.build.lib.actions.SpawnMetrics;
import com.google.devtools.build.lib.actions.SpawnResult;
import com.google.devtools.build.lib.analysis.AnalysisPhaseCompleteEvent;
import com.google.devtools.build.lib.analysis.AnalysisPhaseStartedEvent;
import com.google.devtools.build.lib.analysis.TargetCompleteEvent;
import com.google.devtools.build.lib.buildtool.buildevent.BuildCompleteEvent;
import com.google.devtools.build.lib.buildtool.buildevent.BuildStartingEvent;
import com.google.devtools.build.lib.buildtool.buildevent.ExecutionStartingEvent;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.SpanKind;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.context.Context;
import java.time.Instant;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;
import javax.annotation.Nullable;

/**
 * Maps Bazel EventBus events to OpenTelemetry spans.
 *
 * <p>Registered on the {@link com.google.common.eventbus.EventBus} by {@link OTelTraceModule}
 * when tracing is enabled. Event handler methods create, populate and end OTel spans forming
 * a hierarchical trace: Build -> Phases -> Targets -> Actions -> Spawns.
 *
 * <p>Thread-safety: methods are annotated {@code @AllowConcurrentEvents} and all shared state uses
 * {@link ConcurrentHashMap}. Spans are safe to use from multiple threads per the OTel API contract.
 */
final class OTelSpanMapper {

  private static final String INSTRUMENTATION_SCOPE = "bazel";

  // ---- Attribute keys (allocated once) ----
  private static final AttributeKey<String> ATTR_INVOCATION_ID =
      AttributeKey.stringKey("bazel.invocation_id");
  private static final AttributeKey<String> ATTR_BUILD_REQUEST_ID =
      AttributeKey.stringKey("bazel.build_request_id");
  private static final AttributeKey<String> ATTR_COMMAND =
      AttributeKey.stringKey("bazel.command");
  private static final AttributeKey<String> ATTR_WORKSPACE =
      AttributeKey.stringKey("bazel.workspace");
  private static final AttributeKey<Long> ATTR_EXIT_CODE =
      AttributeKey.longKey("bazel.exit_code");
  private static final AttributeKey<Boolean> ATTR_SUCCESS =
      AttributeKey.booleanKey("bazel.success");
  private static final AttributeKey<String> ATTR_PHASE =
      AttributeKey.stringKey("bazel.phase.name");
  private static final AttributeKey<String> ATTR_TARGET_LABEL =
      AttributeKey.stringKey("bazel.target.label");
  private static final AttributeKey<String> ATTR_TARGET_RULE_KIND =
      AttributeKey.stringKey("bazel.target.rule_kind");
  private static final AttributeKey<Boolean> ATTR_TARGET_SUCCESS =
      AttributeKey.booleanKey("bazel.target.success");
  private static final AttributeKey<String> ATTR_ACTION_MNEMONIC =
      AttributeKey.stringKey("bazel.action.mnemonic");
  private static final AttributeKey<String> ATTR_ACTION_TARGET =
      AttributeKey.stringKey("bazel.action.target_label");
  private static final AttributeKey<String> ATTR_ACTION_PRIMARY_OUTPUT =
      AttributeKey.stringKey("bazel.action.primary_output");
  private static final AttributeKey<Boolean> ATTR_ACTION_CACHE_HIT =
      AttributeKey.booleanKey("bazel.action.cache_hit");
  private static final AttributeKey<String> ATTR_SPAWN_RUNNER =
      AttributeKey.stringKey("bazel.spawn.runner");
  private static final AttributeKey<Long> ATTR_SPAWN_EXIT_CODE =
      AttributeKey.longKey("bazel.spawn.exit_code");
  private static final AttributeKey<Long> ATTR_SPAWN_TOTAL_MS =
      AttributeKey.longKey("bazel.spawn.total_time_ms");
  private static final AttributeKey<Long> ATTR_SPAWN_QUEUE_MS =
      AttributeKey.longKey("bazel.spawn.queue_time_ms");
  private static final AttributeKey<Long> ATTR_SPAWN_SETUP_MS =
      AttributeKey.longKey("bazel.spawn.setup_time_ms");
  private static final AttributeKey<Long> ATTR_SPAWN_EXEC_MS =
      AttributeKey.longKey("bazel.spawn.execution_wall_time_ms");
  private static final AttributeKey<Long> ATTR_SPAWN_UPLOAD_MS =
      AttributeKey.longKey("bazel.spawn.upload_time_ms");
  private static final AttributeKey<Long> ATTR_SPAWN_FETCH_MS =
      AttributeKey.longKey("bazel.spawn.fetch_time_ms");
  private static final AttributeKey<Long> ATTR_SPAWN_PROCESS_OUTPUTS_MS =
      AttributeKey.longKey("bazel.spawn.process_outputs_time_ms");
  private static final AttributeKey<Long> ATTR_SPAWN_NETWORK_MS =
      AttributeKey.longKey("bazel.spawn.network_time_ms");
  private static final AttributeKey<Long> ATTR_SPAWN_INPUT_BYTES =
      AttributeKey.longKey("bazel.spawn.input_bytes");
  private static final AttributeKey<Long> ATTR_SPAWN_INPUT_FILES =
      AttributeKey.longKey("bazel.spawn.input_files");

  private final Tracer tracer;
  private final boolean traceAllActions;

  // Root build span and its OTel context
  @Nullable private volatile Span buildSpan;
  @Nullable private volatile Context buildContext;

  // Phase spans: lazily created, keyed by phase name
  private final ConcurrentHashMap<String, Span> phaseSpans = new ConcurrentHashMap<>();
  private final ConcurrentHashMap<String, Context> phaseContexts = new ConcurrentHashMap<>();

  // Target label -> span context, for parenting action spans under their target
  private final ConcurrentHashMap<String, Context> targetContexts = new ConcurrentHashMap<>();

  OTelSpanMapper(Tracer tracer, boolean traceAllActions) {
    this.tracer = tracer;
    this.traceAllActions = traceAllActions;
  }

  // ---- Build lifecycle ----

  @Subscribe
  public void buildStarting(BuildStartingEvent event) {
    buildSpan =
        tracer
            .spanBuilder("bazel " + event.request().getCommandName())
            .setSpanKind(SpanKind.INTERNAL)
            .setStartTimestamp(event.request().getStartTime(), TimeUnit.MILLISECONDS)
            .setAttribute(ATTR_COMMAND, event.request().getCommandName())
            .setAttribute(ATTR_INVOCATION_ID, event.request().getId().toString())
            .setAttribute(ATTR_BUILD_REQUEST_ID, event.request().getId().toString())
            .startSpan();
    buildContext = Context.current().with(buildSpan);
  }

  @Subscribe
  public void buildComplete(BuildCompleteEvent event) {
    Span span = buildSpan;
    if (span == null) {
      return;
    }
    endAllPhases();

    boolean success = event.getResult().getSuccess();
    span.setAttribute(ATTR_SUCCESS, success);
    if (event.getResult().getDetailedExitCode() != null) {
      span.setAttribute(
          ATTR_EXIT_CODE, (long) event.getResult().getDetailedExitCode().getExitCode().getNumericExitCode());
    }
    span.setStatus(success ? StatusCode.OK : StatusCode.ERROR);
    span.end();
    buildSpan = null;
    buildContext = null;
  }

  // ---- Phase events ----

  @Subscribe
  public void analysisStarted(AnalysisPhaseStartedEvent event) {
    startPhase("analysis");
  }

  @Subscribe
  public void analysisComplete(AnalysisPhaseCompleteEvent event) {
    endPhase("analysis");
  }

  @Subscribe
  public void executionStarting(ExecutionStartingEvent event) {
    startPhase("execution");
  }

  private void startPhase(String phaseName) {
    Context parent = buildContext;
    if (parent == null) {
      return;
    }
    Span phaseSpan =
        tracer
            .spanBuilder("phase:" + phaseName)
            .setParent(parent)
            .setAttribute(ATTR_PHASE, phaseName)
            .startSpan();
    phaseSpans.put(phaseName, phaseSpan);
    phaseContexts.put(phaseName, parent.with(phaseSpan));
  }

  private void endPhase(String phaseName) {
    Span span = phaseSpans.remove(phaseName);
    if (span != null) {
      span.end();
    }
    phaseContexts.remove(phaseName);
  }

  private void endAllPhases() {
    for (String phaseName : phaseSpans.keySet()) {
      endPhase(phaseName);
    }
  }

  // ---- Target events ----

  @Subscribe
  @AllowConcurrentEvents
  public void targetComplete(TargetCompleteEvent event) {
    Context parent = phaseContexts.getOrDefault("execution", buildContext);
    if (parent == null) {
      return;
    }
    String label = event.getLabel().toString();
    boolean success = !event.failed();

    Span span =
        tracer
            .spanBuilder("target:" + label)
            .setParent(parent)
            .setAttribute(ATTR_TARGET_LABEL, label)
            .setAttribute(ATTR_TARGET_SUCCESS, success)
            .startSpan();
    span.setStatus(success ? StatusCode.OK : StatusCode.ERROR);

    targetContexts.put(label, parent.with(span));
    span.end();
  }

  // ---- Action events ----

  @Subscribe
  @AllowConcurrentEvents
  public void actionExecuted(ActionExecutedEvent event) {
    boolean failed = event.getException() != null;
    if (!failed && !traceAllActions) {
      return;
    }
    Action action = event.getAction();
    recordAction(
        action.getMnemonic(),
        action.getOwner() != null ? action.getOwner().getLabel().toString() : null,
        action.getPrimaryOutput() != null ? action.getPrimaryOutput().getExecPathString() : null,
        failed ? StatusCode.ERROR : StatusCode.OK,
        false,
        null,
        null);
  }

  @Subscribe
  @AllowConcurrentEvents
  public void actionCompletion(ActionCompletionEvent event) {
    if (!traceAllActions) {
      return;
    }
    Action action = event.getAction();
    recordAction(
        action.getMnemonic(),
        action.getOwner() != null ? action.getOwner().getLabel().toString() : null,
        action.getPrimaryOutput() != null ? action.getPrimaryOutput().getExecPathString() : null,
        StatusCode.OK,
        false,
        null,
        null);
  }

  @Subscribe
  @AllowConcurrentEvents
  public void cachedAction(CachedActionEvent event) {
    if (!traceAllActions) {
      return;
    }
    Action action = event.getAction();
    recordAction(
        action.getMnemonic(),
        action.getOwner() != null ? action.getOwner().getLabel().toString() : null,
        action.getPrimaryOutput() != null ? action.getPrimaryOutput().getExecPathString() : null,
        StatusCode.OK,
        true,
        null,
        null);
  }

  private void recordAction(
      String mnemonic,
      @Nullable String targetLabel,
      @Nullable String primaryOutput,
      StatusCode status,
      boolean cacheHit,
      @Nullable Instant startTime,
      @Nullable Instant endTime) {
    Context parent = resolveActionParent(targetLabel);

    var builder =
        tracer
            .spanBuilder("action:" + mnemonic)
            .setParent(parent)
            .setAttribute(ATTR_ACTION_MNEMONIC, mnemonic)
            .setAttribute(ATTR_ACTION_CACHE_HIT, cacheHit);

    if (targetLabel != null) {
      builder.setAttribute(ATTR_ACTION_TARGET, targetLabel);
    }
    if (primaryOutput != null) {
      builder.setAttribute(ATTR_ACTION_PRIMARY_OUTPUT, primaryOutput);
    }
    if (startTime != null) {
      builder.setStartTimestamp(startTime);
    }

    Span span = builder.startSpan();
    span.setStatus(status);
    if (endTime != null) {
      span.end(endTime);
    } else {
      span.end();
    }
  }

  /** Finds the best parent context for an action: its owning target, or the execution phase. */
  private Context resolveActionParent(@Nullable String targetLabel) {
    if (targetLabel != null) {
      Context ctx = targetContexts.get(targetLabel);
      if (ctx != null) {
        return ctx;
      }
    }
    Context execCtx = phaseContexts.get("execution");
    return execCtx != null ? execCtx : (buildContext != null ? buildContext : Context.root());
  }

  // ---- Spawn events ----

  @Subscribe
  @AllowConcurrentEvents
  public void spawnExecuted(SpawnExecutedEvent event) {
    Spawn spawn = event.getSpawn();
    SpawnResult result = event.getSpawnResult();
    String mnemonic = spawn.getMnemonic();
    String targetLabel =
        spawn.getResourceOwner() != null && spawn.getResourceOwner().getOwner() != null
            ? spawn.getResourceOwner().getOwner().getLabel().toString()
            : null;

    Context parent = resolveActionParent(targetLabel);

    var builder =
        tracer
            .spanBuilder("spawn:" + mnemonic)
            .setParent(parent)
            .setStartTimestamp(event.getStartTimeInstant())
            .setAttribute(ATTR_SPAWN_RUNNER, result.getRunnerName())
            .setAttribute(ATTR_SPAWN_EXIT_CODE, (long) result.exitCode());

    addSpawnMetrics(builder, result.getMetrics());
    Span span = builder.startSpan();

    if (result.exitCode() != 0) {
      span.setStatus(StatusCode.ERROR, "exit code " + result.exitCode());
    } else {
      span.setStatus(StatusCode.OK);
    }
    span.end();
  }

  private static void addSpawnMetrics(io.opentelemetry.api.trace.SpanBuilder builder, SpawnMetrics m) {
    if (m.totalTimeInMs() > 0) {
      builder.setAttribute(ATTR_SPAWN_TOTAL_MS, (long) m.totalTimeInMs());
    }
    if (m.queueTimeInMs() > 0) {
      builder.setAttribute(ATTR_SPAWN_QUEUE_MS, (long) m.queueTimeInMs());
    }
    if (m.setupTimeInMs() > 0) {
      builder.setAttribute(ATTR_SPAWN_SETUP_MS, (long) m.setupTimeInMs());
    }
    if (m.executionWallTimeInMs() > 0) {
      builder.setAttribute(ATTR_SPAWN_EXEC_MS, (long) m.executionWallTimeInMs());
    }
    if (m.uploadTimeInMs() > 0) {
      builder.setAttribute(ATTR_SPAWN_UPLOAD_MS, (long) m.uploadTimeInMs());
    }
    if (m.fetchTimeInMs() > 0) {
      builder.setAttribute(ATTR_SPAWN_FETCH_MS, (long) m.fetchTimeInMs());
    }
    if (m.processOutputsTimeInMs() > 0) {
      builder.setAttribute(ATTR_SPAWN_PROCESS_OUTPUTS_MS, (long) m.processOutputsTimeInMs());
    }
    if (m.networkTimeInMs() > 0) {
      builder.setAttribute(ATTR_SPAWN_NETWORK_MS, (long) m.networkTimeInMs());
    }
    if (m.inputBytes() > 0) {
      builder.setAttribute(ATTR_SPAWN_INPUT_BYTES, m.inputBytes());
    }
    if (m.inputFiles() > 0) {
      builder.setAttribute(ATTR_SPAWN_INPUT_FILES, m.inputFiles());
    }
  }
}
