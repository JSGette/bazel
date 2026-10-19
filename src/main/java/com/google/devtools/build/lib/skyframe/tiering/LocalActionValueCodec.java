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
import com.google.devtools.build.lib.analysis.Runfiles;
import com.google.devtools.build.lib.analysis.actions.StarlarkAction;
import com.google.devtools.build.lib.analysis.config.BuildConfigurationValue;
import com.google.devtools.build.lib.analysis.config.BuildOptions;
import com.google.devtools.build.lib.analysis.configuredtargets.RuleConfiguredTarget;
import com.google.devtools.build.lib.bugreport.BugReporter;
import com.google.devtools.build.lib.collect.nestedset.NestedSetCodecWithStore;
import com.google.devtools.build.lib.collect.nestedset.NestedSetStore;
import com.google.devtools.build.lib.compress.CompressionService;
import com.google.devtools.build.lib.packages.LocalGoArchiveCodec;
import com.google.devtools.build.lib.packages.StarlarkProvider;
import com.google.devtools.build.lib.rules.genrule.GenRuleAction;
import com.google.devtools.build.lib.skyframe.BzlLoadThreadOwner;
import com.google.devtools.build.lib.skyframe.RuleConfiguredTargetValue;
import com.google.devtools.build.lib.skyframe.serialization.ArrayCodec;
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
import net.starlark.java.eval.Module;

/**
 * Experimental local codec for audited native genrules and Go library spawn actions.
 *
 * <p>This is deliberately separate from remote analysis caching. It preserves transitive packages
 * and local configuration identity, and uses the shared nested-set store for mixed residency.
 * Configurations, BuildOptions, Starlark definitions, and Runfiles remain pinned for the codec's
 * graph lifetime. The identity tables are local to one evaluator and cannot be reused by another
 * server. This codec does not support input discovery, enhanced Starlark actions, templates, errors
 * or event wrappers.
 */
final class LocalActionValueCodec implements DiskBackedGraph.ValueCodec {
  private ObjectCodecs codecs;
  private final CompressionService compression;
  private FingerprintValueService fingerprints;
  private final Interner<DerivedArtifact> artifacts = Interners.newWeakInterner();
  private final ConfigurationReferences configurations = new ConfigurationReferences();
  private final boolean goActions;
  private final LocalReferences<Module> modules = new LocalReferences<>(Module.class);
  private final LocalReferences<BzlLoadThreadOwner> moduleOwners =
      new LocalReferences<>(BzlLoadThreadOwner.class);
  private final LocalReferences<StarlarkProvider> providers =
      new LocalReferences<>(StarlarkProvider.class);
  // GoArchiveData participates in depset equality and includes Runfiles, which uses identity
  // equality. Decoding a second Runfiles object would duplicate packages in the Go linker inputs.
  private final LocalReferences<Runfiles> runfiles = new LocalReferences<>(Runfiles.class);

  LocalActionValueCodec(
      ObjectCodecRegistry.Builder registry,
      ImmutableClassToInstanceMap<Object> dependencies,
      ArtifactFactory artifactFactory,
      FingerprintValueStore store,
      FingerprintValueService fingerprints,
      CompressionService compression,
      boolean goActions) {
    this.goActions = goActions;
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
                .add(ArrayCodec.forComponentType(Artifact.class))
                .add(configurations)
                .add(modules)
                .add(moduleOwners)
                .add(providers)
                .add(runfiles)
                .add(new LocalGoArchiveCodec(providers::id, providers::get))
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
        || target.getActions().isEmpty()) {
      return false;
    }
    String rule = target.getConfiguredTarget().getRuleClassString();
    if ("genrule".equals(rule) && target.getActions().size() == 1) {
      var action = target.getActions().getFirst();
      return action.getClass() == GenRuleAction.class
          && !((GenRuleAction) action).discoversInputs();
    }
    // The exact class check excludes EnhancedStarlarkAction, including input pruning and
    // shadowed actions whose mutable state cannot safely be reconstructed from analysis.
    return goActions
        && "go_library".equals(rule)
        && target.getActions().stream()
            .allMatch(
                action ->
                    action.getClass() == StarlarkAction.class
                        && !((StarlarkAction) action).discoversInputs());
  }

  @Override
  public byte[] encode(SkyValue value) throws IOException {
    if (!supports(value)) {
      throw new IOException("unsupported local action value");
    }
    // Seed weak interning while original artifacts may still be referenced by resident parents.
    for (var action : ((RuleConfiguredTargetValue) value).getActions()) {
      for (Artifact input : action.getInputs().toList()) {
        remember(input);
      }
      for (Artifact output : action.getOutputs()) {
        remember(output);
      }
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
      throw new IOException("cannot serialize local action value", e);
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
        throw new IOException("invalid local action value");
      }
      return skyValue;
    } catch (SerializationException e) {
      throw new IOException("cannot deserialize local action value", e);
    }
  }

  /** Replaces weak fingerprint caches to exercise actual shared-field decoding in tests. */
  void resetFingerprintServiceForTesting(FingerprintValueService fingerprints) {
    this.fingerprints = fingerprints;
  }

  /** Discards weak nested-set caches to exercise actual chunk decoding in tests. */
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

  int pinnedStarlarkDefinitions() {
    return modules.size() + providers.size() + moduleOwners.size();
  }

  int pinnedRunfiles() {
    return runfiles.size();
  }

  /** Local objects retain identity; records never escape this evaluator generation. */
  private static final class LocalReferences<T> implements ObjectCodec<T> {
    private final Class<T> type;
    private final IdentityHashMap<T, Integer> ids = new IdentityHashMap<>();
    private final ArrayList<T> values = new ArrayList<>();

    LocalReferences(Class<T> type) {
      this.type = type;
    }

    @Override
    public boolean autoRegister() {
      return false;
    }

    @Override
    public Class<T> getEncodedClass() {
      return type;
    }

    @Override
    public synchronized void serialize(SerializationContext context, T value, CodedOutputStream out)
        throws IOException {
      out.writeInt32NoTag(id(value));
    }

    synchronized int id(T value) {
      return ids.computeIfAbsent(
          value,
          key -> {
            int next = values.size();
            values.add(key);
            return next;
          });
    }

    synchronized T get(int id) {
      if (id < 0 || id >= values.size()) {
        throw new IllegalArgumentException("unknown local definition ID");
      }
      return values.get(id);
    }

    @Override
    public synchronized T deserialize(DeserializationContext context, CodedInputStream in)
        throws IOException {
      int id = in.readInt32();
      if (id < 0 || id >= values.size()) {
        throw new IOException("unknown local definition ID");
      }
      return values.get(id);
    }

    synchronized int size() {
      return values.size();
    }
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
