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
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.google.common.collect.ImmutableList;
import com.google.common.collect.ImmutableMap;
import com.google.common.collect.ImmutableSet;
import com.google.common.eventbus.EventBus;
import com.google.devtools.build.lib.actions.ActionInput;
import com.google.devtools.build.lib.actions.ActionInputHelper;
import com.google.devtools.build.lib.actions.ResourceSet;
import com.google.devtools.build.lib.actions.SimpleSpawn;
import com.google.devtools.build.lib.actions.SpawnExecutedEvent;
import com.google.devtools.build.lib.actions.SpawnMetrics;
import com.google.devtools.build.lib.actions.SpawnResult;
import com.google.devtools.build.lib.actions.SpawnResult.Status;
import com.google.devtools.build.lib.buildtool.BuildResult;
import com.google.devtools.build.lib.buildtool.buildevent.BuildCompleteEvent;
import com.google.devtools.build.lib.buildtool.buildevent.BuildStartingEvent;
import com.google.devtools.build.lib.buildtool.buildevent.ExecutionStartingEvent;
import com.google.devtools.build.lib.server.FailureDetails;
import com.google.devtools.build.lib.util.DetailedExitCode;
import com.google.devtools.build.lib.collect.nestedset.NestedSetBuilder;
import com.google.devtools.build.lib.collect.nestedset.Order;
import com.google.devtools.build.lib.exec.util.FakeActionInputFileCache;
import com.google.devtools.build.lib.exec.util.FakeOwner;
import com.google.devtools.build.lib.testutil.FoundationTestCase;
import com.google.devtools.build.lib.testutil.TestFileOutErr;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.data.SpanData;
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

/** Tests for {@link OTelSpanMapper}. */
@RunWith(JUnit4.class)
public class OTelSpanMapperTest extends FoundationTestCase {

  private BufferingSpanExporter exporter;
  private SdkTracerProvider tracerProvider;
  private OTelSpanMapper mapper;
  private EventBus testEventBus;

  @Before
  public void setUp() throws Exception {
    exporter = new BufferingSpanExporter();
    tracerProvider =
        SdkTracerProvider.builder()
            .addSpanProcessor(SimpleSpanProcessor.create(exporter))
            .build();
    Tracer tracer = tracerProvider.get("bazel");
    mapper = new OTelSpanMapper(tracer, /* traceAllActions= */ true);
    testEventBus = new EventBus();
    testEventBus.register(mapper);
  }

  @After
  public void tearDown() {
    tracerProvider.shutdown();
  }

  @Test
  public void buildLifecycle_createsRootSpan() {
    postBuildStarting("build");
    postBuildComplete(/* success= */ true);

    List<SpanData> spans = exporter.drainSpans();
    assertThat(spans).hasSize(1);
    SpanData root = spans.get(0);
    assertThat(root.getName()).isEqualTo("bazel build");
    assertThat(root.getStatus().getStatusCode()).isEqualTo(StatusCode.OK);
    assertThat(
            root.getAttributes()
                .get(AttributeKey.stringKey("bazel.command")))
        .isEqualTo("build");
    assertThat(
            root.getAttributes()
                .get(AttributeKey.booleanKey("bazel.success")))
        .isTrue();
  }

  @Test
  public void buildLifecycle_failedBuild_setsErrorStatus() {
    postBuildStarting("build");
    postBuildComplete(/* success= */ false);

    List<SpanData> spans = exporter.drainSpans();
    assertThat(spans).hasSize(1);
    SpanData root = spans.get(0);
    assertThat(root.getStatus().getStatusCode()).isEqualTo(StatusCode.ERROR);
    assertThat(
            root.getAttributes()
                .get(AttributeKey.booleanKey("bazel.success")))
        .isFalse();
  }

  @Test
  public void executionPhase_createsPhaseSpan() {
    postBuildStarting("build");
    testEventBus.post(new ExecutionStartingEvent(ImmutableList.of()));
    postBuildComplete(/* success= */ true);

    List<SpanData> spans = exporter.drainSpans();
    assertThat(spans.stream().map(SpanData::getName))
        .contains("phase:execution");
  }

  @Test
  public void spawnExecuted_createsSpanWithMetrics() {
    postBuildStarting("build");

    ActionInput output = ActionInputHelper.fromPath("output/foo/out");
    SimpleSpawn spawn =
        new SimpleSpawn(
            new FakeOwner("CppCompile", "Compiling foo.cc", "//foo:bar"),
            ImmutableList.of("gcc", "-c", "foo.cc"),
            ImmutableMap.of(),
            ImmutableMap.of(),
            NestedSetBuilder.emptySet(Order.STABLE_ORDER),
            ImmutableSet.of(output),
            ResourceSet.ZERO);
    SpawnResult result =
        new SpawnResult.Builder()
            .setRunnerName("remote")
            .setStatus(Status.SUCCESS)
            .setExitCode(0)
            .setSpawnMetrics(
                SpawnMetrics.Builder.forRemoteExec()
                    .setTotalTimeInMs(1000)
                    .setExecutionWallTimeInMs(800)
                    .setQueueTimeInMs(100)
                    .setUploadTimeInMs(50)
                    .build())
            .build();

    Instant start = Instant.now();
    testEventBus.post(
        new SpawnExecutedEvent(
            spawn,
            new FakeActionInputFileCache(),
            /* actionFileSystem= */ null,
            new TestFileOutErr(),
            result,
            start,
            /* spawnIdentifier= */ null));

    postBuildComplete(/* success= */ true);

    List<SpanData> spans = exporter.drainSpans();
    SpanData spawnSpan =
        spans.stream()
            .filter(s -> s.getName().equals("spawn:CppCompile"))
            .findFirst()
            .orElseThrow();

    assertThat(spawnSpan.getAttributes().get(AttributeKey.stringKey("bazel.spawn.runner")))
        .isEqualTo("remote");
    assertThat(spawnSpan.getAttributes().get(AttributeKey.longKey("bazel.spawn.exit_code")))
        .isEqualTo(0);
    assertThat(spawnSpan.getAttributes().get(AttributeKey.longKey("bazel.spawn.total_time_ms")))
        .isEqualTo(1000);
    assertThat(
            spawnSpan
                .getAttributes()
                .get(AttributeKey.longKey("bazel.spawn.execution_wall_time_ms")))
        .isEqualTo(800);
    assertThat(spawnSpan.getAttributes().get(AttributeKey.longKey("bazel.spawn.queue_time_ms")))
        .isEqualTo(100);
    assertThat(spawnSpan.getAttributes().get(AttributeKey.longKey("bazel.spawn.upload_time_ms")))
        .isEqualTo(50);
    assertThat(spawnSpan.getStatus().getStatusCode()).isEqualTo(StatusCode.OK);
  }

  @Test
  public void spawnExecuted_failedSpawn_setsErrorStatus() {
    postBuildStarting("build");

    ActionInput output = ActionInputHelper.fromPath("output/foo/out");
    SimpleSpawn spawn =
        new SimpleSpawn(
            new FakeOwner("CppCompile", "Compiling foo.cc", "//foo:bar"),
            ImmutableList.of("gcc", "-c", "foo.cc"),
            ImmutableMap.of(),
            ImmutableMap.of(),
            NestedSetBuilder.emptySet(Order.STABLE_ORDER),
            ImmutableSet.of(output),
            ResourceSet.ZERO);
    SpawnResult result =
        new SpawnResult.Builder()
            .setRunnerName("local")
            .setStatus(Status.NON_ZERO_EXIT)
            .setExitCode(1)
            .setFailureDetail(
                FailureDetails.FailureDetail.newBuilder()
                    .setSpawn(
                        FailureDetails.Spawn.newBuilder()
                            .setCode(FailureDetails.Spawn.Code.NON_ZERO_EXIT))
                    .build())
            .setSpawnMetrics(
                SpawnMetrics.Builder.forLocalExec().setTotalTimeInMs(500).build())
            .build();

    testEventBus.post(
        new SpawnExecutedEvent(
            spawn,
            new FakeActionInputFileCache(),
            null,
            new TestFileOutErr(),
            result,
            Instant.now(),
            null));
    postBuildComplete(/* success= */ false);

    List<SpanData> spans = exporter.drainSpans();
    SpanData spawnSpan =
        spans.stream()
            .filter(s -> s.getName().equals("spawn:CppCompile"))
            .findFirst()
            .orElseThrow();

    assertThat(spawnSpan.getStatus().getStatusCode()).isEqualTo(StatusCode.ERROR);
    assertThat(spawnSpan.getAttributes().get(AttributeKey.longKey("bazel.spawn.exit_code")))
        .isEqualTo(1);
  }

  @Test
  public void noSpansWithoutBuildStart() {
    List<SpanData> spans = exporter.drainSpans();
    assertThat(spans).isEmpty();
  }

  @Test
  public void allSpansShareSameTraceId() {
    postBuildStarting("test");
    testEventBus.post(new ExecutionStartingEvent(ImmutableList.of()));

    ActionInput output = ActionInputHelper.fromPath("output/bar/out");
    SimpleSpawn spawn =
        new SimpleSpawn(
            new FakeOwner("Javac", "Compiling Bar.java", "//bar:baz"),
            ImmutableList.of("javac", "Bar.java"),
            ImmutableMap.of(),
            ImmutableMap.of(),
            NestedSetBuilder.emptySet(Order.STABLE_ORDER),
            ImmutableSet.of(output),
            ResourceSet.ZERO);
    SpawnResult result =
        new SpawnResult.Builder()
            .setRunnerName("worker")
            .setStatus(Status.SUCCESS)
            .setExitCode(0)
            .setSpawnMetrics(
                SpawnMetrics.Builder.forWorkerExec().setTotalTimeInMs(200).build())
            .build();
    testEventBus.post(
        new SpawnExecutedEvent(
            spawn,
            new FakeActionInputFileCache(),
            null,
            new TestFileOutErr(),
            result,
            Instant.now(),
            null));

    postBuildComplete(/* success= */ true);

    List<SpanData> spans = exporter.drainSpans();
    assertThat(spans.size()).isAtLeast(2);
    String traceId = spans.get(0).getTraceId();
    for (SpanData span : spans) {
      assertThat(span.getTraceId()).isEqualTo(traceId);
    }
  }

  // ---- Helpers ----

  private void postBuildStarting(String command) {
    com.google.devtools.build.lib.buildtool.BuildRequest request =
        mock(com.google.devtools.build.lib.buildtool.BuildRequest.class);
    when(request.getCommandName()).thenReturn(command);
    when(request.getId()).thenReturn(UUID.randomUUID());
    when(request.getStartTime()).thenReturn(System.currentTimeMillis());

    BuildStartingEvent event = mock(BuildStartingEvent.class);
    when(event.request()).thenReturn(request);
    testEventBus.post(event);
  }

  private void postBuildComplete(boolean success) {
    BuildResult result = mock(BuildResult.class);
    when(result.getSuccess()).thenReturn(success);
    when(result.getDetailedExitCode()).thenReturn(DetailedExitCode.success());
    when(result.getStopTime()).thenReturn(System.currentTimeMillis());
    testEventBus.post(new BuildCompleteEvent(result));
  }
}
