// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.analysis;

import static com.google.common.truth.Truth.assertThat;
import static com.google.devtools.build.lib.skyframe.serialization.analysis.FrontierSerializer.SelectionMarking.ACTIVE;
import static com.google.devtools.build.lib.skyframe.serialization.analysis.FrontierSerializer.SelectionMarking.FRONTIER_CANDIDATE;

import com.google.common.collect.ImmutableMap;
import com.google.devtools.build.lib.actions.ActionLookupData;
import com.google.devtools.build.lib.cmdline.Label;
import com.google.devtools.build.lib.skyframe.ConfiguredTargetKey;
import com.google.devtools.build.lib.skyframe.serialization.analysis.FrontierSerializer.SelectionMarking;
import com.google.devtools.build.skyframe.SkyKey;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

/** Selection boundaries for the opt-in local synchronous frontier experiment. */
@RunWith(JUnit4.class)
public final class FrontierSerializerExperimentTest {
  private static final ConfiguredTargetKey ACTIVE_KEY =
      ConfiguredTargetKey.builder().setLabel(Label.parseCanonicalUnchecked("//active:lib")).build();
  private static final ConfiguredTargetKey FRONTIER_KEY =
      ConfiguredTargetKey.builder().setLabel(Label.parseCanonicalUnchecked("//stable:lib")).build();
  private static final ActionLookupData EXECUTION_KEY = ActionLookupData.create(FRONTIER_KEY, 0);
  private static final ImmutableMap<SkyKey, SelectionMarking> SELECTION =
      ImmutableMap.of(
          ACTIVE_KEY, ACTIVE,
          FRONTIER_KEY, FRONTIER_CANDIDATE,
          EXECUTION_KEY, FRONTIER_CANDIDATE);

  @Test
  public void enabledSyncFrontierPreservesAnalysisMarkingsAndDropsExecutionKeys() {
    assertThat(
            FrontierSerializer.selectExperimentalAnalysisFrontier(
                SELECTION, RemoteAnalysisCacheMode.BIDI, true))
        .containsExactly(ACTIVE_KEY, ACTIVE, FRONTIER_KEY, FRONTIER_CANDIDATE);
  }

  @Test
  public void enabledUploadAlsoDropsExecutionKeys() {
    assertThat(
            FrontierSerializer.selectExperimentalAnalysisFrontier(
                SELECTION, RemoteAnalysisCacheMode.UPLOAD, true))
        .containsExactly(ACTIVE_KEY, ACTIVE, FRONTIER_KEY, FRONTIER_CANDIDATE);
  }

  @Test
  public void disabledExperimentPreservesOriginalSelection() {
    assertThat(
            FrontierSerializer.selectExperimentalAnalysisFrontier(
                SELECTION, RemoteAnalysisCacheMode.BIDI, false))
        .isSameInstanceAs(SELECTION);
  }

  @Test
  public void enabledExperimentDoesNotChangeAsyncSelection() {
    assertThat(
            FrontierSerializer.selectExperimentalAnalysisFrontier(
                SELECTION, RemoteAnalysisCacheMode.ASYNC_BIDI, true))
        .isSameInstanceAs(SELECTION);
  }
}
