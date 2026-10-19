// Copyright 2023 The Bazel Authors. All rights reserved.
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
package com.google.devtools.build.skyframe;

import com.google.common.collect.ImmutableMap;
import org.junit.Rule;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

/** Evaluator conformance with supported done nodes evicted after every operation. */
@RunWith(JUnit4.class)
public final class DiskBackedMemoizingEvaluatorTest extends MemoizingEvaluatorTest {
  @Rule public final TemporaryFolder directory = new TemporaryFolder();

  @Override
  protected InMemoryMemoizingEvaluator getMemoizingEvaluator(
      ImmutableMap<SkyFunctionName, SkyFunction> functions,
      Differencer differencer,
      EvaluationProgressReceiver progressReceiver,
      GraphInconsistencyReceiver graphInconsistencyReceiver,
      EventFilter eventFilter) {
    return new InMemoryMemoizingEvaluator(
        functions,
        differencer,
        progressReceiver,
        graphInconsistencyReceiver,
        eventFilter,
        emittedEventState,
        /* keepEdges= */ true,
        new DiskBackedGraph(
            new DiskBackedGraphTestSupport.FileStore(directory.getRoot().toPath()),
            DiskBackedGraphTestSupport.STRINGS,
            /* residentLimit= */ 0));
  }
}
