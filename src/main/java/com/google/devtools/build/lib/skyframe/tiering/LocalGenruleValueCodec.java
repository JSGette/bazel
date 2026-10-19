// Copyright 2026 The Bazel Authors. All rights reserved.
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

package com.google.devtools.build.lib.skyframe.tiering;

import com.google.common.collect.ImmutableClassToInstanceMap;
import com.google.common.collect.Interner;
import com.google.common.collect.Interners;
import com.google.common.util.concurrent.MoreExecutors;
import com.google.devtools.build.lib.actions.Artifact;
import com.google.devtools.build.lib.actions.Artifact.ArtifactSerializationContext;
import com.google.devtools.build.lib.actions.Artifact.DerivedArtifact;
import com.google.devtools.build.lib.actions.Artifact.SourceArtifact;
import com.google.devtools.build.lib.actions.ArtifactFactory;
import com.google.devtools.build.lib.actions.ArtifactOwner;
import com.google.devtools.build.lib.actions.ArtifactRoot;
import com.google.devtools.build.lib.analysis.config.BuildConfigurationValue;
import com.google.devtools.build.lib.analysis.config.BuildOptions;
import com.google.devtools.build.lib.analysis.configuredtargets.RuleConfiguredTarget;
import com.google.devtools.build.lib.bugreport.BugReporter;
import com.google.devtools.build.lib.collect.nestedset.NestedSetCodecWithStore;
import com.google.devtools.build.lib.collect.nestedset.NestedSetStore;
import com.google.devtools.build.lib.compress.CompressionService;
import com.google.devtools.build.lib.rules.genrule.GenRuleAction;
import com.google.devtools.build.lib.skyframe.RuleConfiguredTargetValue;
import com.google.devtools.build.lib.skyframe.serialization.DeserializationContext;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueService;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.ObjectCodec;
import com.google.devtools.build.lib.skyframe.serialization.ObjectCodecRegistry;
import com.google.devtools.build.lib.skyframe.serialization.ObjectCodecs;
import com.google.devtools.build.lib.skyframe.serialization.SerializationContext;
import com.google.devtools.build.lib.skyframe.serialization.SerializationDependencyProvider;
import com.google.devtools.build.lib.skyframe.serialization.SerializationException;
import com.google.devtools.build.lib.vfs.PathFragment;
import com.google.devtools.build.skyframe.DiskBackedGraph;
import com.google.devtools.build.skyframe.SkyValue;
import com.google.protobuf.ByteString;
import com.google.protobuf.CodedInputStream;
import com.google.protobuf.CodedOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.concurrent.ExecutionException;

/**
 * Experimental local codec for the exact native genrule value/action pair.
 *
 * <p>This is deliberately separate from remote analysis caching. It preserves transitive packages
 * and local configuration identity, and uses the shared nested-set store for mixed residency.
 * Configurations and BuildOptions remain pinned for the codec's graph lifetime. This codec does not
 * support Starlark actions, input discovery, templates, errors or event wrappers.
 */
final class LocalGenruleValueCodec implements DiskBackedGraph.ValueCodec {
  private ObjectCodecs codecs;
  private final CompressionService compression;
  private final FingerprintValueService fingerprints;
  private final Interner<DerivedArtifact> artifacts = Interners.newWeakInterner();
  private final ConfigurationReferences configurations = new ConfigurationReferences();

  LocalGenruleValueCodec(
      ObjectCodecRegistry.Builder registry,
      ImmutableClassToInstanceMap<Object> dependencies,
      ArtifactFactory artifactFactory,
      FingerprintValueStore store,
      FingerprintValueService fingerprints,
      CompressionService compression) {
    this.fingerprints = fingerprints;
    this.compression = compression;
    ArtifactSerializationContext artifactContext =
        new ArtifactSerializationContext() {
          @Override
          public SourceArtifact getSourceArtifact(
              PathFragment path, ArtifactRoot root, ArtifactOwner owner) {
            return artifactFactory.getSourceArtifact(path, root, owner);
          }

          @Override
          public DerivedArtifact intern(
              DerivedArtifact artifact, SerializationDependencyProvider context) {
            return artifacts.intern(artifact);
          }
        };
    codecs =
        new ObjectCodecs(
            registry
                .add(new LocalValueCodec())
                .add(configurations)
                .add(
                    new NestedSetCodecWithStore(
                        new NestedSetStore(
                            store,
                            MoreExecutors.directExecutor(),
                            BugReporter.defaultInstance(),
                            NestedSetStore.NO_CONTEXT)))
                .build(),
            ImmutableClassToInstanceMap.<Object>builder()
                .putAll(dependencies)
                .put(ArtifactSerializationContext.class, artifactContext)
                .put(
                    BuildOptions.OptionsChecksumCache.class,
                    new BuildOptions.MapBackedChecksumCache())
                .build());
  }

  @Override
  public boolean supports(SkyValue value) {
    if (!(value instanceof RuleConfiguredTargetValue target)
        || target.getConfiguredTarget() == null
        || !"genrule".equals(target.getConfiguredTarget().getRuleClassString())
        || target.getActions().size() != 1) {
      return false;
    }
    var action = target.getActions().getFirst();
    return action.getClass() == GenRuleAction.class && !((GenRuleAction) action).discoversInputs();
  }

  @Override
  public byte[] encode(SkyValue value) throws IOException {
    if (!supports(value)) {
      throw new IOException("unsupported local action value");
    }
    GenRuleAction action =
        (GenRuleAction) ((RuleConfiguredTargetValue) value).getActions().getFirst();
    // Seed weak interning while original artifacts may still be referenced by resident parents.
    for (Artifact input : action.getInputs().toList()) {
      remember(input);
    }
    for (Artifact output : action.getOutputs()) {
      remember(output);
    }
    try {
      var result = codecs.serializeMemoizedAndBlocking(compression, fingerprints, value);
      if (result.getFutureToBlockWritesOn() != null) {
        result.getFutureToBlockWritesOn().get();
      }
      return result.getObject().toByteArray();
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      throw new IOException("interrupted storing action value", e);
    } catch (SerializationException | ExecutionException e) {
      throw new IOException("cannot serialize local genrule", e);
    }
  }

  private void remember(Artifact artifact) {
    if (artifact instanceof DerivedArtifact derived) {
      var unused = artifacts.intern(derived);
    }
  }

  @Override
  public SkyValue decode(byte[] bytes) throws IOException {
    try {
      Object value =
          codecs.deserializeMemoizedAndBlocking(
              compression, fingerprints, ByteString.copyFrom(bytes));
      if (!(value instanceof SkyValue skyValue) || !supports(skyValue)) {
        throw new IOException("invalid local genrule value");
      }
      return skyValue;
    } catch (SerializationException e) {
      throw new IOException("cannot deserialize local genrule", e);
    }
  }

  /** Discards only the weak nested-set caches to exercise actual chunk decoding in tests. */
  void resetNestedSetCacheForTesting(FingerprintValueStore store) {
    codecs =
        codecs.withCodecOverridesForTesting(
            java.util.List.of(
                new NestedSetCodecWithStore(
                    new NestedSetStore(
                        store,
                        MoreExecutors.directExecutor(),
                        BugReporter.defaultInstance(),
                        NestedSetStore.NO_CONTEXT))));
  }

  int pinnedConfigurations() {
    return configurations.size();
  }

  /** Explicitly carries the transient field that DynamicCodec would otherwise omit. */
  private static final class LocalValueCodec implements ObjectCodec<RuleConfiguredTargetValue> {
    @Override
    public boolean autoRegister() {
      return false;
    }

    @Override
    public Class<RuleConfiguredTargetValue> getEncodedClass() {
      return RuleConfiguredTargetValue.class;
    }

    @Override
    public void serialize(
        SerializationContext context, RuleConfiguredTargetValue value, CodedOutputStream out)
        throws SerializationException, IOException {
      context.serialize(value.getConfiguredTarget(), out);
      context.serialize(value.getTransitivePackages(), out);
    }

    @Override
    public RuleConfiguredTargetValue deserialize(
        DeserializationContext context, CodedInputStream in)
        throws SerializationException, IOException {
      RuleConfiguredTarget target = context.deserialize(in);
      return new RuleConfiguredTargetValue(target, context.deserialize(in));
    }
  }

  /** Configuration identity is part of the local action-owner contract. */
  private static final class ConfigurationReferences
      implements ObjectCodec<BuildConfigurationValue> {
    private final IdentityHashMap<BuildConfigurationValue, Integer> ids = new IdentityHashMap<>();
    private final ArrayList<BuildConfigurationValue> values = new ArrayList<>();

    @Override
    public boolean autoRegister() {
      return false;
    }

    @Override
    public Class<BuildConfigurationValue> getEncodedClass() {
      return BuildConfigurationValue.class;
    }

    @Override
    public synchronized void serialize(
        SerializationContext context, BuildConfigurationValue value, CodedOutputStream out)
        throws IOException {
      int id =
          ids.computeIfAbsent(
              value,
              key -> {
                int next = values.size();
                values.add(key);
                return next;
              });
      out.writeInt32NoTag(id);
    }

    @Override
    public synchronized BuildConfigurationValue deserialize(
        DeserializationContext context, CodedInputStream in) throws IOException {
      int id = in.readInt32();
      if (id < 0 || id >= values.size()) {
        throw new IOException("unknown local configuration ID");
      }
      return values.get(id);
    }

    synchronized int size() {
      return values.size();
    }
  }
}
