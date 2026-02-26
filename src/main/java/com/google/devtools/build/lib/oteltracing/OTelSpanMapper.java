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

import com.google.common.collect.Iterables;
import com.google.common.eventbus.AllowConcurrentEvents;
import com.google.common.eventbus.Subscribe;
import com.google.devtools.build.lib.actions.Action;
import com.google.devtools.build.lib.actions.ActionInput;
import com.google.devtools.build.lib.actions.ActionKeyContext;
import com.google.devtools.build.lib.actions.ActionCompletionEvent;
import com.google.devtools.build.lib.actions.ActionExecutedEvent;
import com.google.devtools.build.lib.actions.ActionExecutionMetadata;
import com.google.devtools.build.lib.actions.ActionStartedEvent;
import com.google.devtools.build.lib.actions.CachedActionEvent;
import com.google.devtools.build.lib.actions.Spawn;
import com.google.devtools.build.lib.actions.SpawnExecutedEvent;
import com.google.devtools.build.lib.actions.SpawnMetrics;
import com.google.devtools.build.lib.actions.SpawnResult;
import com.google.devtools.build.lib.analysis.AnalysisPhaseCompleteEvent;
import com.google.devtools.build.lib.analysis.AnalysisPhaseStartedEvent;
import com.google.devtools.build.lib.analysis.TargetCompleteEvent;
import com.google.devtools.build.lib.analysis.configuredtargets.RuleConfiguredTarget;
import com.google.devtools.build.lib.buildtool.buildevent.BuildCompleteEvent;
import com.google.devtools.build.lib.buildtool.buildevent.BuildStartingEvent;
import com.google.devtools.build.lib.buildtool.buildevent.ExecutionStartingEvent;
import com.google.devtools.build.lib.runtime.OriginalUnstructuredCommandLineEvent;
import com.google.devtools.build.lib.clock.BlazeClock;
import com.google.devtools.build.lib.events.Event;
import com.google.devtools.build.lib.events.ExtendedEventHandler.FetchProgress;
import com.google.devtools.build.lib.events.Reporter;
import com.google.devtools.build.lib.skyframe.TopLevelStatusEvents.TopLevelTargetAnalyzedEvent;
import com.google.common.base.Joiner;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.SpanKind;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.context.Context;
import java.time.Instant;
import java.util.Collection;
import java.util.List;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;
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
  private static final AttributeKey<String> ATTR_BUILD_TARGETS =
      AttributeKey.stringKey("bazel.build.targets");
  /** Full invocation: build command and arguments (like bazel.invocation in bazel_conduit). */
  private static final AttributeKey<String> ATTR_INVOCATION_COMMAND_LINE =
      AttributeKey.stringKey("bazel.invocation.command_line");
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
  private static final AttributeKey<String> ATTR_SPAWN_TARGET_LABEL =
      AttributeKey.stringKey("bazel.spawn.target_label");
  private static final AttributeKey<String> ATTR_SPAWN_COMMAND_LINE =
      AttributeKey.stringKey("bazel.spawn.command_line");
  private static final AttributeKey<Long> ATTR_SPAWN_INPUT_COUNT =
      AttributeKey.longKey("bazel.spawn.input_count");
  private static final AttributeKey<Long> ATTR_SPAWN_OUTPUT_COUNT =
      AttributeKey.longKey("bazel.spawn.output_count");
  private static final AttributeKey<String> ATTR_SPAWN_OUTPUTS_SAMPLE =
      AttributeKey.stringKey("bazel.spawn.outputs_sample");
  private static final AttributeKey<String> ATTR_SPAWN_INPUTS =
      AttributeKey.stringKey("bazel.spawn.inputs");
  private static final AttributeKey<String> ATTR_SPAWN_OUTPUTS =
      AttributeKey.stringKey("bazel.spawn.outputs");
  private static final AttributeKey<Boolean> ATTR_SPAWN_CACHE_HIT =
      AttributeKey.booleanKey("bazel.spawn.cache_hit");
  private static final AttributeKey<String> ATTR_SPAWN_CACHE_DIGEST =
      AttributeKey.stringKey("bazel.spawn.cache_digest");
  private static final AttributeKey<String> ATTR_ACTION_CACHE_KEY_DIGEST =
      AttributeKey.stringKey("bazel.action.cache_key_digest");
  // Analysis phase attributes
  private static final AttributeKey<Long> ATTR_ANALYSIS_TARGET_COUNT =
      AttributeKey.longKey("bazel.analysis.target_count");
  private static final AttributeKey<Long> ATTR_ANALYSIS_DURATION_MS =
      AttributeKey.longKey("bazel.analysis.duration_ms");
  private static final AttributeKey<Long> ATTR_ANALYSIS_TARGETS_CONFIGURED_TOTAL =
      AttributeKey.longKey("bazel.analysis.targets_configured_total");
  private static final AttributeKey<Long> ATTR_ANALYSIS_TARGETS_CONFIGURED_ONLY =
      AttributeKey.longKey("bazel.analysis.targets_configured_only");
  private static final AttributeKey<Long> ATTR_ANALYSIS_ACTIONS_CONSTRUCTED_TOTAL =
      AttributeKey.longKey("bazel.analysis.actions_constructed_total");
  private static final AttributeKey<String> ATTR_ANALYSIS_ACTIONS_BY_MNEMONIC =
      AttributeKey.stringKey("bazel.analysis.actions_by_mnemonic");
  private static final AttributeKey<Boolean> ATTR_ANALYSIS_CACHE_DROPPED =
      AttributeKey.booleanKey("bazel.analysis.cache_dropped");
  // Fetch attributes
  private static final AttributeKey<String> ATTR_FETCH_RESOURCE =
      AttributeKey.stringKey("bazel.fetch.resource");
  /** Source of the fetch: URL, file path, or repository identifier (e.g. "repository @repo"). */
  private static final AttributeKey<String> ATTR_FETCH_SOURCE =
      AttributeKey.stringKey("bazel.fetch.source");
  private static final AttributeKey<String> ATTR_FETCH_PROGRESS =
      AttributeKey.stringKey("bazel.fetch.progress");

  private final Tracer tracer;
  private final boolean traceAllActions;
  @Nullable private final com.google.devtools.build.lib.actions.ActionKeyContext actionKeyContext;
  @Nullable private final Reporter reporter;

  // Root build span and its OTel context
  @Nullable private volatile Span buildSpan;
  @Nullable private volatile Context buildContext;
  /** Last received command line (for invocation); set before build span may exist. */
  @Nullable private volatile String lastInvocationCommandLine;

  // Phase spans: lazily created, keyed by phase name
  private final ConcurrentHashMap<String, Span> phaseSpans = new ConcurrentHashMap<>();
  private final ConcurrentHashMap<String, Context> phaseContexts = new ConcurrentHashMap<>();

  // Target label -> span context, for parenting action spans under their target
  private final ConcurrentHashMap<String, Context> targetContexts = new ConcurrentHashMap<>();
  // Target label -> open span (for top-level targets started in TopLevelTargetAnalyzedEvent)
  private final ConcurrentHashMap<String, Span> openTargetSpans = new ConcurrentHashMap<>();

  // Action key -> open span context (for actions started in ActionStartedEvent)
  private final ConcurrentHashMap<String, Context> actionContexts = new ConcurrentHashMap<>();
  // Action key -> open span (to end on completion)
  private final ConcurrentHashMap<String, Span> openActionSpans = new ConcurrentHashMap<>();

  // Fetch resource id -> open span (for FetchProgress)
  private final ConcurrentHashMap<String, Span> openFetchSpans = new ConcurrentHashMap<>();

  OTelSpanMapper(
      Tracer tracer,
      boolean traceAllActions,
      @Nullable ActionKeyContext actionKeyContext,
      @Nullable Reporter reporter) {
    this.tracer = tracer;
    this.traceAllActions = traceAllActions;
    this.actionKeyContext = actionKeyContext;
    this.reporter = reporter;
  }

  // ---- Build lifecycle ----

  @Subscribe
  public void buildStarting(BuildStartingEvent event) {
    var builder =
        tracer
            .spanBuilder("bazel " + event.request().getCommandName())
            .setSpanKind(SpanKind.INTERNAL)
            .setStartTimestamp(event.request().getStartTime(), TimeUnit.MILLISECONDS)
            .setAttribute(ATTR_COMMAND, event.request().getCommandName())
            .setAttribute(ATTR_INVOCATION_ID, event.request().getId().toString())
            .setAttribute(ATTR_BUILD_REQUEST_ID, event.request().getId().toString());
    if (event.request().getTargets() != null && !event.request().getTargets().isEmpty()) {
      builder.setAttribute(ATTR_BUILD_TARGETS, Joiner.on(" ").join(event.request().getTargets()));
    }
    buildSpan = builder.startSpan();
    buildContext = Context.current().with(buildSpan);
    if (reporter != null) {
      reporter.handle(
          Event.info(
              "OTel trace ID: " + buildSpan.getSpanContext().getTraceId()));
    }
    if (lastInvocationCommandLine != null && !lastInvocationCommandLine.isEmpty()) {
      buildSpan.setAttribute(ATTR_INVOCATION_COMMAND_LINE, lastInvocationCommandLine);
    }
  }

  @Subscribe
  @AllowConcurrentEvents
  public void unstructuredCommandLine(OriginalUnstructuredCommandLineEvent event) {
    if (event.getArgs().isEmpty()) {
      return;
    }
    String line = Joiner.on(" ").join(event.getArgs());
    lastInvocationCommandLine = line;
    Span span = buildSpan;
    if (span != null) {
      span.setAttribute(ATTR_INVOCATION_COMMAND_LINE, line);
    }
  }

  @Subscribe
  public void buildComplete(BuildCompleteEvent event) {
    Span span = buildSpan;
    if (span == null) {
      return;
    }
    endAllPhases();
    // End any open target/action/fetch spans so they don't leak
    for (Span s : openTargetSpans.values()) {
      s.end();
    }
    openTargetSpans.clear();
    for (Span s : openActionSpans.values()) {
      s.end();
    }
    openActionSpans.clear();
    actionContexts.clear();
    for (Span s : openFetchSpans.values()) {
      s.end();
    }
    openFetchSpans.clear();

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
    Span phaseSpan = phaseSpans.get("analysis");
    if (phaseSpan != null) {
      phaseSpan.setAttribute(
          ATTR_ANALYSIS_TARGET_COUNT, (long) Iterables.size(event.getLabels()));
    }
  }

  @Subscribe
  public void analysisComplete(AnalysisPhaseCompleteEvent event) {
    Span phaseSpan = phaseSpans.get("analysis");
    if (phaseSpan != null) {
      phaseSpan.setAttribute(ATTR_ANALYSIS_DURATION_MS, event.getTimeInMs());
      phaseSpan.setAttribute(
          ATTR_ANALYSIS_TARGETS_CONFIGURED_TOTAL,
          (long) event.getTargetsConfigured().total());
      phaseSpan.setAttribute(
          ATTR_ANALYSIS_TARGETS_CONFIGURED_ONLY,
          (long) event.getTargetsConfigured().configuredTargetsOnly());
      phaseSpan.setAttribute(
          ATTR_ANALYSIS_ACTIONS_CONSTRUCTED_TOTAL,
          (long) event.getActionsConstructed().total());
      phaseSpan.setAttribute(
          ATTR_ANALYSIS_ACTIONS_BY_MNEMONIC,
          event.getActionsConstructedByMnemonic().entrySet().stream()
              .map(e -> e.getKey() + "=" + e.getValue())
              .reduce((a, b) -> a + "," + b)
              .orElse(""));
      phaseSpan.setAttribute(ATTR_ANALYSIS_CACHE_DROPPED, event.wasAnalysisCacheDropped());
    }
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
  public void topLevelTargetAnalyzed(TopLevelTargetAnalyzedEvent event) {
    Context parent = phaseContexts.getOrDefault("analysis", buildContext);
    if (parent == null) {
      return;
    }
    String label = event.configuredTarget().getLabel().toString();
    var builder =
        tracer
            .spanBuilder("target:" + label)
            .setParent(parent)
            .setAttribute(ATTR_TARGET_LABEL, label);
    if (event.configuredTarget() instanceof RuleConfiguredTarget rct) {
      builder.setAttribute(ATTR_TARGET_RULE_KIND, rct.getRuleClassString());
    }
    Span span = builder.startSpan();
    openTargetSpans.put(label, span);
    targetContexts.put(label, parent.with(span));
  }

  @Subscribe
  @AllowConcurrentEvents
  public void targetComplete(TargetCompleteEvent event) {
    String label = event.getLabel().toString();
    boolean success = !event.failed();

    Span openSpan = openTargetSpans.remove(label);
    if (openSpan != null) {
      openSpan.setAttribute(ATTR_TARGET_SUCCESS, success);
      openSpan.setStatus(success ? StatusCode.OK : StatusCode.ERROR);
      openSpan.end();
      return;
    }

    // Non-top-level target: create instant span (no TopLevelTargetAnalyzedEvent was seen)
    Context parent = phaseContexts.getOrDefault("execution", buildContext);
    if (parent == null) {
      return;
    }
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

  /**
   * Converts BlazeClock relative nano time to wall-clock {@link Instant} for accurate span
   * timestamps.
   */
  private static Instant blazeNanosToInstant(long nanoTime) {
    return Instant.ofEpochMilli(
        BlazeClock.createNanosToMillisSinceEpochConverter().toEpochMillis(nanoTime));
  }

  /** Stable key for an action (for matching start vs completion). */
  private static String actionKey(ActionExecutionMetadata action) {
    try {
      if (action.getPrimaryOutput() != null) {
        return action.getPrimaryOutput().getExecPathString();
      }
    } catch (IllegalStateException e) {
      // Some implementations (e.g. test FakeOwner) throw when primary output is not set.
    }
    String owner = action.getOwner() != null ? action.getOwner().getLabel().toString() : "";
    return owner + "|" + action.getMnemonic();
  }

  @Subscribe
  @AllowConcurrentEvents
  public void actionStarted(ActionStartedEvent event) {
    if (!traceAllActions) {
      return;
    }
    Action action = event.getAction();
    String key = actionKey(action);
    String targetLabel =
        action.getOwner() != null ? action.getOwner().getLabel().toString() : null;
    Context parent = resolveActionParent(targetLabel);

    var builder =
        tracer
            .spanBuilder("action:" + action.getMnemonic())
            .setParent(parent)
            .setStartTimestamp(blazeNanosToInstant(event.getNanoTimeStart()))
            .setAttribute(ATTR_ACTION_MNEMONIC, action.getMnemonic())
            .setAttribute(ATTR_ACTION_CACHE_HIT, false);
    if (targetLabel != null) {
      builder.setAttribute(ATTR_ACTION_TARGET, targetLabel);
    }
    if (action.getPrimaryOutput() != null) {
      builder.setAttribute(ATTR_ACTION_PRIMARY_OUTPUT, action.getPrimaryOutput().getExecPathString());
    }
    Span span = builder.startSpan();
    openActionSpans.put(key, span);
    actionContexts.put(key, parent.with(span));
  }

  @Subscribe
  @AllowConcurrentEvents
  public void actionExecuted(ActionExecutedEvent event) {
    Action action = event.getAction();
    String key = actionKey(action);
    boolean failed = event.getException() != null;
    Span openSpan = openActionSpans.remove(key);
    actionContexts.remove(key);
    if (openSpan != null) {
      openSpan.setStatus(failed ? StatusCode.ERROR : StatusCode.OK);
      if (event.getEndTime() != null) {
        openSpan.end(event.getEndTime());
      } else {
        openSpan.end();
      }
      if (!failed && !traceAllActions) {
        return;
      }
    }
    if (openSpan == null) {
      if (!failed && !traceAllActions) {
        return;
      }
      recordAction(
          action.getMnemonic(),
          action.getOwner() != null ? action.getOwner().getLabel().toString() : null,
          action.getPrimaryOutput() != null ? action.getPrimaryOutput().getExecPathString() : null,
          failed ? StatusCode.ERROR : StatusCode.OK,
          false,
          event.getStartTime(),
          event.getEndTime(),
          null);
    }
  }

  @Subscribe
  @AllowConcurrentEvents
  public void actionCompletion(ActionCompletionEvent event) {
    if (!traceAllActions) {
      return;
    }
    Action action = event.getAction();
    String key = actionKey(action);
    Span openSpan = openActionSpans.remove(key);
    actionContexts.remove(key);
    if (openSpan != null) {
      openSpan.setStatus(StatusCode.OK);
      openSpan.end(blazeNanosToInstant(event.getFinishTimeNanos()));
      return;
    }
    recordAction(
        action.getMnemonic(),
        action.getOwner() != null ? action.getOwner().getLabel().toString() : null,
        action.getPrimaryOutput() != null ? action.getPrimaryOutput().getExecPathString() : null,
        StatusCode.OK,
        false,
        blazeNanosToInstant(event.getRelativeActionStartTimeNanos()),
        blazeNanosToInstant(event.getFinishTimeNanos()),
        null);
  }

  @Subscribe
  @AllowConcurrentEvents
  public void cachedAction(CachedActionEvent event) {
    if (!traceAllActions) {
      return;
    }
    Action action = event.getAction();
    String key = actionKey(action);
    Span openSpan = openActionSpans.remove(key);
    actionContexts.remove(key);
    String cacheKeyDigest = null;
    if (actionKeyContext != null) {
      try {
        cacheKeyDigest = action.getKey(actionKeyContext, event.getInputMetadataProvider());
      } catch (InterruptedException e) {
        Thread.currentThread().interrupt();
      }
    }
    if (openSpan != null) {
      openSpan.setAttribute(ATTR_ACTION_CACHE_HIT, true);
      if (cacheKeyDigest != null && !cacheKeyDigest.isEmpty()) {
        openSpan.setAttribute(ATTR_ACTION_CACHE_KEY_DIGEST, cacheKeyDigest);
      }
      openSpan.setStatus(StatusCode.OK);
      openSpan.end(blazeNanosToInstant(event.getNanoTimeFinish()));
      return;
    }
    recordAction(
        action.getMnemonic(),
        action.getOwner() != null ? action.getOwner().getLabel().toString() : null,
        action.getPrimaryOutput() != null ? action.getPrimaryOutput().getExecPathString() : null,
        StatusCode.OK,
        true,
        blazeNanosToInstant(event.getNanoTimeStart()),
        blazeNanosToInstant(event.getNanoTimeFinish()),
        cacheKeyDigest);
  }

  private void recordAction(
      String mnemonic,
      @Nullable String targetLabel,
      @Nullable String primaryOutput,
      StatusCode status,
      boolean cacheHit,
      @Nullable Instant startTime,
      @Nullable Instant endTime,
      @Nullable String cacheKeyDigest) {
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
    if (cacheHit && cacheKeyDigest != null && !cacheKeyDigest.isEmpty()) {
      builder.setAttribute(ATTR_ACTION_CACHE_KEY_DIGEST, cacheKeyDigest);
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

  /**
   * Finds the best parent context for an action: its owning target (creating a target span on
   * demand if needed for grouping), or the execution phase.
   */
  private Context resolveActionParent(@Nullable String targetLabel) {
    if (targetLabel != null) {
      Context ctx =
          targetContexts.computeIfAbsent(
              targetLabel,
              label -> {
                Context parent =
                    phaseContexts.getOrDefault("execution", buildContext != null ? buildContext : Context.root());
                Span span =
                    tracer
                        .spanBuilder("target:" + label)
                        .setParent(parent)
                        .setAttribute(ATTR_TARGET_LABEL, label)
                        .startSpan();
                openTargetSpans.put(label, span);
                return parent.with(span);
              });
      return ctx;
    }
    Context execCtx = phaseContexts.get("execution");
    return execCtx != null ? execCtx : (buildContext != null ? buildContext : Context.root());
  }

  // ---- Spawn events ----

  /** Prefer action context when the spawn's owner matches an open action span. */
  private Context resolveSpawnParent(Spawn spawn, @Nullable String targetLabel) {
    if (spawn.getResourceOwner() != null) {
      try {
        String key = actionKey(spawn.getResourceOwner());
        Context actionCtx = actionContexts.get(key);
        if (actionCtx != null) {
          return actionCtx;
        }
      } catch (IllegalStateException e) {
        // Some owners (e.g. test FakeOwner) do not set primary output; fall back to target parent.
      }
    }
    return resolveActionParent(targetLabel);
  }

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

    Context parent = resolveSpawnParent(spawn, targetLabel);

    var builder =
        tracer
            .spanBuilder("spawn:" + mnemonic)
            .setParent(parent)
            .setStartTimestamp(event.getStartTimeInstant())
            .setAttribute(ATTR_SPAWN_RUNNER, result.getRunnerName())
            .setAttribute(ATTR_SPAWN_EXIT_CODE, (long) result.exitCode());

    if (targetLabel != null) {
      builder.setAttribute(ATTR_SPAWN_TARGET_LABEL, targetLabel);
    }
    boolean cacheHit = result.isCacheHit();
    builder.setAttribute(ATTR_SPAWN_CACHE_HIT, cacheHit);
    if (cacheHit) {
      var digest = result.getDigest();
      if (digest != null && !digest.getHash().isEmpty()) {
        builder.setAttribute(ATTR_SPAWN_CACHE_DIGEST, digest.getHash());
      }
    }
    addSpawnCommandAndIo(builder, spawn);
    addSpawnMetrics(builder, result.getMetrics());
    Span span = builder.startSpan();

    if (result.exitCode() != 0) {
      span.setStatus(StatusCode.ERROR, "exit code " + result.exitCode());
    } else {
      span.setStatus(StatusCode.OK);
    }
    span.end();
  }

  /** Joins path strings with the given separator (no truncation). */
  private static String joinPaths(
      Collection<? extends ActionInput> paths, String separator) {
    if (paths == null || paths.isEmpty()) {
      return "";
    }
    return paths.stream()
        .map(ActionInput::getExecPathString)
        .collect(Collectors.joining(separator));
  }

  private static void addSpawnCommandAndIo(
      io.opentelemetry.api.trace.SpanBuilder builder, Spawn spawn) {
    List<String> args = spawn.getArguments();
    if (args != null && !args.isEmpty()) {
      builder.setAttribute(ATTR_SPAWN_COMMAND_LINE, Joiner.on(" ").join(args));
    }
    try {
      Collection<? extends ActionInput> inputList = spawn.getInputFiles().toList();
      builder.setAttribute(ATTR_SPAWN_INPUT_COUNT, (long) inputList.size());
      String inputsStr = joinPaths(inputList, "\n");
      if (!inputsStr.isEmpty()) {
        builder.setAttribute(ATTR_SPAWN_INPUTS, inputsStr);
      }
    } catch (Exception e) {
      // Avoid failing trace on unexpected NestedSet behavior
    }
    Collection<? extends ActionInput> outputs = spawn.getOutputFiles();
    if (outputs != null) {
      builder.setAttribute(ATTR_SPAWN_OUTPUT_COUNT, (long) outputs.size());
      String outputsStr = joinPaths(outputs, "\n");
      if (!outputsStr.isEmpty()) {
        builder.setAttribute(ATTR_SPAWN_OUTPUTS, outputsStr);
      }
      String sample = joinPaths(outputs, ",");
      if (!sample.isEmpty()) {
        builder.setAttribute(ATTR_SPAWN_OUTPUTS_SAMPLE, sample);
      }
    }
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

  // ---- Fetch events ----

  @Subscribe
  @AllowConcurrentEvents
  public void fetchProgress(FetchProgress event) {
    Context parent = buildContext;
    if (parent == null) {
      return;
    }
    String resourceId = event.getResourceIdentifier();
    if (resourceId == null) {
      resourceId = "";
    }
    if (event.isFinished()) {
      Span span = openFetchSpans.remove(resourceId);
      if (span != null) {
        span.setAttribute(ATTR_FETCH_PROGRESS, event.getProgress());
        span.end();
      } else {
        // No ongoing span (e.g. only "finished" was posted): create instant span so source is visible
        Span instantSpan =
            tracer
                .spanBuilder("fetch:" + resourceId)
                .setParent(parent)
                .setAttribute(ATTR_FETCH_RESOURCE, resourceId)
                .setAttribute(ATTR_FETCH_SOURCE, resourceId)
                .setAttribute(ATTR_FETCH_PROGRESS, event.getProgress())
                .startSpan();
        instantSpan.end();
      }
      return;
    }
    Span span =
        tracer
            .spanBuilder("fetch:" + resourceId)
            .setParent(parent)
            .setAttribute(ATTR_FETCH_RESOURCE, resourceId)
            .setAttribute(ATTR_FETCH_SOURCE, resourceId)
            .setAttribute(ATTR_FETCH_PROGRESS, event.getProgress())
            .startSpan();
    openFetchSpans.put(resourceId, span);
  }
}
