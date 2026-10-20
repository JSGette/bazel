// Copyright 2014 The Bazel Authors. All rights reserved.
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

import static com.google.common.truth.Truth.assertThat;
import static org.junit.Assert.assertThrows;

import com.google.common.collect.ImmutableList;
import com.google.common.collect.Iterables;
import com.google.devtools.build.skyframe.SkyFunctionException.ReifiedSkyFunctionException;
import java.io.IOException;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

/** Tests for the non-trivial creation logic of {@link ErrorInfo}. */
@RunWith(JUnit4.class)
public final class ErrorInfoTest {

  /** Dummy SkyFunctionException implementation for the sake of testing. */
  private static final class DummySkyFunctionException extends SkyFunctionException {
    private final boolean isCatastrophic;

    DummySkyFunctionException(Exception cause, boolean isTransient, boolean isCatastrophic) {
      super(cause, isTransient ? Transience.TRANSIENT : Transience.PERSISTENT);
      this.isCatastrophic = isCatastrophic;
    }

    @Override
    public boolean isCatastrophic() {
      return isCatastrophic;
    }
  }

  private static void runTestFromException(
      boolean isDirectlyTransient, boolean isTransitivelyTransient) {
    Exception exception = new IOException("ehhhhh");
    DummySkyFunctionException dummyException =
        new DummySkyFunctionException(exception, isDirectlyTransient, /* isCatastrophic= */ false);

    ErrorInfo errorInfo =
        ErrorInfo.fromException(
            new ReifiedSkyFunctionException(dummyException), isTransitivelyTransient);

    assertThat(errorInfo.getException()).isSameInstanceAs(exception);
    assertThat(errorInfo.getCycleInfo()).isEmpty();
    assertThat(errorInfo.isDirectlyTransient()).isEqualTo(isDirectlyTransient);
    assertThat(errorInfo.isTransitivelyTransient())
        .isEqualTo(isDirectlyTransient || isTransitivelyTransient);
    assertThat(errorInfo.isCatastrophic()).isFalse();
  }

  @Test
  public void testFromException_NonTransient() {
    runTestFromException(/* isDirectlyTransient= */ false, /* isTransitivelyTransient= */ false);
  }

  @Test
  public void testFromException_DirectlyTransient() {
    runTestFromException(/* isDirectlyTransient= */ true, /* isTransitivelyTransient= */ false);
  }

  @Test
  public void testFromException_TransitivelyTransient() {
    runTestFromException(/* isDirectlyTransient= */ false, /* isTransitivelyTransient= */ true);
  }

  @Test
  public void testFromException_DirectlyAndTransitivelyTransient() {
    runTestFromException(/* isDirectlyTransient= */ true, /* isTransitivelyTransient= */ true);
  }

  @Test
  public void testFromCycle() {
    CycleInfo cycle =
        CycleInfo.createCycleInfo(
            ImmutableList.of(GraphTester.skyKey("PATH, 1234")),
            ImmutableList.of(GraphTester.skyKey("CYCLE, 4321")));

    ErrorInfo errorInfo = ErrorInfo.fromCycle(cycle);

    assertThat(errorInfo.getException()).isNull();
    assertThat(errorInfo.isTransitivelyTransient()).isFalse();
    assertThat(errorInfo.isCatastrophic()).isFalse();
  }

  @Test
  public void testFromChildErrors() {
    CycleInfo cycle =
        CycleInfo.createCycleInfo(
            ImmutableList.of(GraphTester.skyKey("PATH, 1234")),
            ImmutableList.of(GraphTester.skyKey("CYCLE, 4321")));
    ErrorInfo cycleErrorInfo = ErrorInfo.fromCycle(cycle);

    Exception exception1 = new IOException("ehhhhh");
    DummySkyFunctionException dummyException1 =
        new DummySkyFunctionException(
            exception1, /* isTransient= */ true, /* isCatastrophic= */ false);
    ErrorInfo exceptionErrorInfo1 =
        ErrorInfo.fromException(
            new ReifiedSkyFunctionException(dummyException1), /* isTransitivelyTransient= */ false);

    // N.B this ErrorInfo will be catastrophic.
    Exception exception2 = new IOException("blahhhhh");
    DummySkyFunctionException dummyException2 =
        new DummySkyFunctionException(
            exception2, /* isTransient= */ false, /* isCatastrophic= */ true);
    ErrorInfo exceptionErrorInfo2 =
        ErrorInfo.fromException(
            new ReifiedSkyFunctionException(dummyException2), /* isTransitivelyTransient= */ false);

    SkyKey currentKey = GraphTester.skyKey("CURRENT, 9876");

    ErrorInfo errorInfo =
        ErrorInfo.fromChildErrors(
            currentKey, ImmutableList.of(cycleErrorInfo, exceptionErrorInfo1, exceptionErrorInfo2));

    // For simplicity we test the current implementation detail that we choose the first non-null
    // exception that we encounter. This isn't necessarily a requirement of the interface, but it
    // makes the test convenient and is a way to document the current behavior.
    assertThat(errorInfo.getException()).isSameInstanceAs(exception1);

    assertThat(errorInfo.getCycleInfo())
        .containsExactly(
            CycleInfo.createCycleInfo(
                ImmutableList.of(currentKey, Iterables.getOnlyElement(cycle.getPathToCycle())),
                cycle.getCycle()));
    assertThat(errorInfo.isTransitivelyTransient()).isTrue();
    assertThat(errorInfo.isCatastrophic()).isTrue();
  }

  @Test
  public void cannotCreateErrorInfoWithoutExceptionOrCycle() {
    Exception e =
        assertThrows(
            RuntimeException.class,
            () ->
                new ErrorInfo(
                    /* exception= */ null, /* cycles= */ ImmutableList.of(), false, false, false));
    assertThat(e).hasMessageThat().contains("At least one of exception and cycles must be present");
  }

  @Test
  public void cannotCreateErrorInfoWithDirectTransienceButNotTransitiveTransience() {
    Exception e =
        assertThrows(
            RuntimeException.class,
            () ->
                new ErrorInfo(
                    new Exception(),
                    /* cycles= */ ImmutableList.of(),
                    /* isDirectlyTransient= */ true,
                    /* isTransitivelyTransient= */ false,
                    /* isCatastrophic= */ false));
    assertThat(e)
        .hasMessageThat()
        .contains("Cannot be directly transient but not transitively transient");
  }

  @Test
  public void fromChildErrors_exceptionOnlyPreservesPreferenceAndFlags() {
    Exception tolerated = new IOException("tolerated");
    Exception selected = new IOException("selected");
    Exception catastrophic = new IOException("catastrophic");
    ErrorInfo result =
        ErrorInfo.fromChildErrors(
            GraphTester.skyKey("parent"),
            ImmutableList.of(
                ErrorInfo.withValue(
                    new ErrorInfo(tolerated, ImmutableList.of(), false, false, false)),
                new ErrorInfo(selected, ImmutableList.of(), true, true, false),
                new ErrorInfo(catastrophic, ImmutableList.of(), false, false, true)));

    assertThat(result.getException()).isSameInstanceAs(selected);
    assertThat(result.getCycleInfo()).isEmpty();
    assertThat(result.isDirectlyTransient()).isFalse();
    assertThat(result.isTransitivelyTransient()).isTrue();
    assertThat(result.isCatastrophic()).isTrue();
    assertThat(result.hasValue()).isFalse();
  }

  @Test
  public void fromChildErrors_emptyCycleChildrenPreserveCycleOrderAndPerChildDeduplication() {
    SkyKey parent = GraphTester.skyKey("parent");
    SkyKey firstPath = GraphTester.skyKey("firstPath");
    SkyKey duplicatePath = GraphTester.skyKey("duplicatePath");
    SkyKey secondPath = GraphTester.skyKey("secondPath");
    SkyKey thirdPath = GraphTester.skyKey("thirdPath");
    ImmutableList<SkyKey> firstCycle =
        ImmutableList.of(GraphTester.skyKey("a"), GraphTester.skyKey("b"));
    ImmutableList<SkyKey> secondCycle = ImmutableList.of(GraphTester.skyKey("c"));
    ErrorInfo first =
        new ErrorInfo(
            null,
            ImmutableList.of(
                CycleInfo.createCycleInfo(ImmutableList.of(firstPath), firstCycle),
                CycleInfo.createCycleInfo(ImmutableList.of(duplicatePath), firstCycle)),
            false,
            false,
            false);
    ErrorInfo second =
        ErrorInfo.fromCycle(CycleInfo.createCycleInfo(ImmutableList.of(secondPath), secondCycle));
    ErrorInfo third =
        ErrorInfo.fromCycle(CycleInfo.createCycleInfo(ImmutableList.of(thirdPath), firstCycle));
    ImmutableList<CycleInfo> expected =
        ImmutableList.of(
            CycleInfo.createCycleInfo(ImmutableList.of(parent, firstPath), firstCycle),
            CycleInfo.createCycleInfo(ImmutableList.of(parent, secondPath), secondCycle),
            CycleInfo.createCycleInfo(ImmutableList.of(parent, thirdPath), firstCycle));

    ErrorInfo cyclesOnly =
        ErrorInfo.fromChildErrors(parent, ImmutableList.of(first, second, third));
    assertThat(cyclesOnly.getCycleInfo()).containsExactlyElementsIn(expected).inOrder();
    assertThat(cyclesOnly.getException()).isNull();
    assertThat(cyclesOnly.isTransitivelyTransient()).isFalse();
    assertThat(cyclesOnly.isCatastrophic()).isFalse();

    Exception selected = new IOException("selected");
    ErrorInfo emptyCycles = new ErrorInfo(selected, ImmutableList.of(), false, true, true);
    ErrorInfo mixed =
        ErrorInfo.fromChildErrors(
            parent, ImmutableList.of(first, emptyCycles, second, emptyCycles, third));
    assertThat(mixed.getCycleInfo()).containsExactlyElementsIn(expected).inOrder();
    assertThat(mixed.getException()).isSameInstanceAs(selected);
    assertThat(mixed.isDirectlyTransient()).isFalse();
    assertThat(mixed.isTransitivelyTransient()).isTrue();
    assertThat(mixed.isCatastrophic()).isTrue();
  }

  @Test
  public void fromChildErrors_noDetailsSentinelRemainsScopedToItsChild() {
    SkyKey parent = GraphTester.skyKey("parent");
    CycleInfo detailed = CycleInfo.createCycleInfo(ImmutableList.of(GraphTester.skyKey("cycle")));
    ErrorInfo sentinelChild =
        new ErrorInfo(
            null, ImmutableList.of(detailed, CycleInfo.cycleInfoNoDetails()), false, false, false);
    ErrorInfo emptyCycles =
        new ErrorInfo(new IOException("error"), ImmutableList.of(), false, false, false);

    ErrorInfo result =
        ErrorInfo.fromChildErrors(
            parent, ImmutableList.of(sentinelChild, emptyCycles, ErrorInfo.fromCycle(detailed)));

    assertThat(result.getCycleInfo())
        .containsExactly(
            CycleInfo.cycleInfoNoDetails(),
            CycleInfo.createCycleInfo(ImmutableList.of(parent), detailed.getCycle()))
        .inOrder();
  }
}
