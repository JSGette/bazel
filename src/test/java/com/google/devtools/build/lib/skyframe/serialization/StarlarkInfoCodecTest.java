// Copyright 2022 The Bazel Authors. All rights reserved.
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
package com.google.devtools.build.lib.skyframe.serialization;

import static com.google.common.truth.Truth.assertThat;
import static org.junit.Assert.fail;

import com.google.common.collect.ImmutableMap;
import com.google.common.util.concurrent.Futures;
import com.google.common.util.concurrent.ListenableFuture;
import com.google.devtools.build.lib.analysis.util.BuildViewTestCase;
import com.google.devtools.build.lib.cmdline.Label;
import com.google.devtools.build.lib.collect.nestedset.Depset;
import com.google.devtools.build.lib.compress.CompressionService;
import com.google.devtools.build.lib.compress.CompressionServiceImpl;
import com.google.devtools.build.lib.packages.StarlarkInfo;
import com.google.devtools.build.lib.packages.StarlarkProvider;
import com.google.devtools.build.lib.skyframe.BzlLoadValue;
import com.google.devtools.build.lib.skyframe.serialization.SharedValueDeserializationContext.LookupAbandonedException;
import com.google.devtools.build.lib.skyframe.util.SkyframeExecutorTestUtils;
import com.google.devtools.build.skyframe.EvaluationResult;
import com.google.devtools.build.skyframe.state.EnvironmentForUtilities;
import com.google.protobuf.ByteString;
import java.io.IOException;
import java.util.concurrent.ExecutionException;
import net.starlark.java.eval.Starlark;
import net.starlark.java.eval.StarlarkInt;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

/** Unit tests for {@link StarlarkInfo} (de)serialization. */
@RunWith(JUnit4.class)
public final class StarlarkInfoCodecTest extends BuildViewTestCase {
  private static final CompressionService COMPRESSION_SERVICE = new CompressionServiceImpl();

  private final ObjectCodecs objectCodecs =
      new ObjectCodecs(AutoRegistry.get().getBuilder().addReferenceConstant(Starlark.NONE).build());
  private final FingerprintValueService fingerprintValueService =
      FingerprintValueService.createForTesting();

  @Test
  public void roundTripTests() throws Exception {
    StarlarkProvider provider = makeProvider();
    verifyRoundTripWithSkyframe(StarlarkInfo.create(provider, ImmutableMap.of()));
    verifyRoundTripWithSkyframe(
        StarlarkInfo.create(
            provider,
            ImmutableMap.of(
                "a", StarlarkInt.of(1), "b", StarlarkInt.of(2), "c", StarlarkInt.of(3))));
  }

  @Test
  public void schemafulDepsetsWithFreshReaderProvider() throws Exception {
    verifySchemafulDepsets(/* primeReaderWithDifferentType= */ false);
  }

  @Test
  public void schemafulDepsetsWithDifferentReaderPrediction() throws Exception {
    verifySchemafulDepsets(/* primeReaderWithDifferentType= */ true);
  }

  private void verifySchemafulDepsets(boolean primeReaderWithDifferentType) throws Exception {
    // Covers every compact Schema0..5 subclass and the array-backed SchemaN.
    for (int count = 0; count <= 7; count++) {
      String path = "schema" + count;
      scratch.file(path + "/BUILD");
      String definition =
          "fields = ['f' + str(i) for i in range("
              + count
              + ")]\n"
              + "MyTestInfo = provider(fields = fields)\n";
      scratch.file(
          path + "/lib.bzl", definition + "value = MyTestInfo(**{f: depset([f]) for f in fields})");
      BzlLoadValue.Key key =
          BzlLoadValue.keyForBuild(Label.parseCanonicalUnchecked("//" + path + ":lib.bzl"));
      StarlarkInfo original = (StarlarkInfo) getBzlLoadValue(key).getModule().getGlobal("value");
      ByteString serialized = objectCodecs.serializeMemoized(original);

      scratch.overwriteFile(
          path + "/lib.bzl",
          definition
              + (primeReaderWithDifferentType
                  ? "other = MyTestInfo(**{f: depset([123]) for f in fields})"
                  : "# A fresh reader has not created any provider instances."));
      invalidatePackages(/* alsoConfigs= */ false);
      StarlarkProvider reader =
          (StarlarkProvider) getBzlLoadValue(key).getModule().getGlobal("MyTestInfo");
      assertThat(reader).isNotSameInstanceAs(original.getProvider());
      StarlarkInfo restored = deserializeWithSkyframe(serialized);
      assertThat(restored.getProvider()).isSameInstanceAs(reader);
      assertThat(restored.getFieldNames()).containsExactlyElementsIn(original.getFieldNames());
      for (String field : original.getFieldNames()) {
        Depset depset = (Depset) restored.getValue(field);
        assertThat(depset.getElementClass()).isEqualTo(String.class);
        assertThat(depset.toList(String.class)).containsExactly(field);
      }
    }
  }

  @Test
  public void schemafulAbsentScalarAndEmptyDepsetFields() throws Exception {
    scratch.file("schema/BUILD");
    String definition = "MyTestInfo = provider(fields = ['absent', 'empty', 'none', 'scalar'])";
    scratch.file(
        "schema/lib.bzl",
        definition,
        "value = MyTestInfo(empty = depset(), none = None, scalar = 'plain')");
    BzlLoadValue.Key key =
        BzlLoadValue.keyForBuild(Label.parseCanonicalUnchecked("//schema:lib.bzl"));
    StarlarkInfo original = (StarlarkInfo) getBzlLoadValue(key).getModule().getGlobal("value");
    ByteString serialized = objectCodecs.serializeMemoized(original);
    scratch.overwriteFile("schema/lib.bzl", definition);
    invalidatePackages(/* alsoConfigs= */ false);
    StarlarkInfo restored = deserializeWithSkyframe(serialized);
    assertThat(restored.getValue("absent")).isNull();
    assertThat(((Depset) restored.getValue("empty")).isEmpty()).isTrue();
    assertThat(restored.getValue("none")).isSameInstanceAs(Starlark.NONE);
    assertThat(restored.getValue("scalar")).isEqualTo("plain");
  }

  private void verifyRoundTripWithSkyframe(StarlarkInfo original) throws Exception {
    ByteString serialized = objectCodecs.serializeMemoized(original);
    StarlarkInfo deserialized = deserializeWithSkyframe(serialized);
    verificationFunction(original, deserialized);
  }

  private StarlarkInfo deserializeWithSkyframe(ByteString serialized)
      throws ExecutionException,
          InterruptedException,
          SerializationException,
          SkyframeDependencyException,
          LookupAbandonedException {
    // Deserialization always returns a future because there is a Skyframe lookup. The future is
    // always done because there are no shared values to wait on.
    SkyframeLookupContinuation continuation =
        (SkyframeLookupContinuation)
            Futures.getDone(
                (ListenableFuture<?>)
                    objectCodecs.deserializeWithSkyframe(
                        COMPRESSION_SERVICE, fingerprintValueService, serialized));
    ListenableFuture<?> resultFuture =
        continuation.process(
            new EnvironmentForUtilities(
                // The only Skyframe lookup our deserializer needs is for one BzlLoadValue.
                key -> {
                  try {
                    return getBzlLoadValue((BzlLoadValue.Key) key);
                  } catch (InterruptedException e) {
                    throw new AssertionError(e);
                  }
                }));
    return (StarlarkInfo) Futures.getDone(resultFuture);
  }

  private static void verificationFunction(StarlarkInfo original, StarlarkInfo deserialized) {
    assertThat(deserialized).isEqualTo(original);
    assertThat(deserialized.getFieldNames())
        .containsExactlyElementsIn(original.getFieldNames())
        .inOrder();
  }

  /** Returns an exported, schemaless provider. */
  private StarlarkProvider makeProvider() throws IOException, InterruptedException {
    scratch.file("test/BUILD");
    scratch.file(
        "test/lib.bzl",
        """
        MyTestInfo = provider()
        """);

    BzlLoadValue.Key key =
        BzlLoadValue.keyForBuild(Label.parseCanonicalUnchecked("//test:lib.bzl"));
    return (StarlarkProvider) getBzlLoadValue(key).getModule().getGlobal("MyTestInfo");
  }

  private BzlLoadValue getBzlLoadValue(BzlLoadValue.Key key) throws InterruptedException {
    EvaluationResult<BzlLoadValue> result =
        SkyframeExecutorTestUtils.evaluate(
            getSkyframeExecutor(), key, /* keepGoing= */ false, reporter);
    if (result.hasError()) {
      fail(result.getError(key).getException().getMessage());
    }
    return result.get(key);
  }
}
