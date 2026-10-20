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

package com.google.devtools.build.lib.analysis;

import static com.google.common.base.Preconditions.checkNotNull;

import com.google.common.base.MoreObjects.ToStringHelper;
import com.google.common.collect.ImmutableList;
import com.google.common.collect.ImmutableSet;
import com.google.devtools.build.lib.actions.ActionAnalysisMetadata;
import com.google.devtools.build.lib.actions.BasicActionLookupValue;
import com.google.devtools.build.lib.collect.nestedset.NestedSet;
import com.google.devtools.build.lib.packages.Aspect;
import com.google.devtools.build.lib.packages.Package;
import com.google.devtools.build.lib.skyframe.AspectKeyCreator.AspectKey;
import com.google.devtools.build.lib.skyframe.serialization.AsyncDeserializationContext;
import com.google.devtools.build.lib.skyframe.serialization.DeferredObjectCodec;
import com.google.devtools.build.lib.skyframe.serialization.DeserializedSkyValue;
import com.google.devtools.build.lib.skyframe.serialization.SerializationContext;
import com.google.devtools.build.lib.skyframe.serialization.SerializationException;
import com.google.devtools.build.lib.skyframe.serialization.VisibleForSerialization;
import com.google.devtools.build.lib.skyframe.serialization.autocodec.AutoCodec;
import com.google.protobuf.CodedInputStream;
import com.google.protobuf.CodedOutputStream;
import java.io.IOException;
import javax.annotation.Nullable;

/** An aspect in the context of the Skyframe graph. */
@AutoCodec(deserializedInterface = DeserializedSkyValue.class)
public class AspectValue extends BasicActionLookupValue
    implements ConfiguredAspect, RuleConfiguredObjectValue {

  public static AspectValue create(
      AspectKey key,
      Aspect aspect,
      ConfiguredAspect configuredAspect,
      @Nullable NestedSet<Package.Metadata> transitivePackages) {
    return transitivePackages == null
        ? new AspectValue(aspect, configuredAspect)
        : new AspectValueWithTransitivePackages(key, aspect, configuredAspect, transitivePackages);
  }

  public static AspectValue createForAlias(
      AspectKey key,
      Aspect aspect,
      ConfiguredAspect configuredAspect,
      @Nullable NestedSet<Package.Metadata> transitivePackages) {
    return transitivePackages == null
        ? new AspectValueForAlias(aspect, configuredAspect)
        : new AspectValueWithTransitivePackagesForAlias(
            key, aspect, configuredAspect, transitivePackages);
  }

  // These variables are only non-final because they may be clear()ed to save memory. They are null
  // only after they are cleared except for transitivePackagesForPackageRootResolution.
  @Nullable private Aspect aspect;
  @Nullable private TransitiveInfoProviderMap providers;

  // We store this in a boolean because the aspect variable from which it comes may be cleared to
  // save memory.
  private final boolean writesOutputToMasterLog;

  private AspectValue(Aspect aspect, ConfiguredAspect configuredAspect) {
    this(
        configuredAspect.getActions(),
        checkNotNull(aspect),
        configuredAspect.getProviders(),
        aspect.getDefinition().getAttributes().containsKey("$print_to_master_log"));
  }

  @AutoCodec.Instantiator
  AspectValue(
      ImmutableList<ActionAnalysisMetadata> actions,
      Aspect aspect,
      TransitiveInfoProviderMap providers,
      boolean writesOutputToMasterLog) {
    super(actions);
    this.aspect = aspect;
    this.providers = providers;
    this.writesOutputToMasterLog = writesOutputToMasterLog;
  }

  public AspectKey getKeyForTransitivePackageTracking() {
    throw new UnsupportedOperationException("Only supported if transitive packages are tracked.");
  }

  public final Aspect getAspect() {
    return checkNotNull(aspect);
  }

  @Override
  public TransitiveInfoProviderMap getProviders() {
    return checkNotNull(providers);
  }

  public boolean getWritesOutputToMasterLog() {
    return writesOutputToMasterLog;
  }

  @Override
  public boolean isCleared() {
    return this.aspect == null;
  }

  /**
   * Clears data from this value.
   *
   * <p>Should only be used when user specifies --discard_analysis_cache. Must be called at most
   * once per value, after which this object's other methods cannot be called.
   */
  public void clear(boolean clearEverything) {
    if (clearEverything) {
      aspect = null;
      providers = null;
    }
  }

  @Nullable
  @Override
  public NestedSet<Package.Metadata> getTransitivePackages() {
    return null;
  }

  @Override
  public final ProviderCollection getConfiguredObject() {
    return this;
  }

  @Override
  protected ToStringHelper getStringHelper() {
    return super.getStringHelper().add("aspect", aspect).add("providers", providers);
  }

  @Override
  public String toString() {
    return getStringHelper().toString();
  }

  private static class AspectValueWithTransitivePackages extends AspectValue {
    @Nullable
    private transient NestedSet<Package.Metadata> transitivePackages; // Null after clear().

    @Nullable private AspectKey key;

    private AspectValueWithTransitivePackages(
        AspectKey key,
        Aspect aspect,
        ConfiguredAspect configuredAspect,
        NestedSet<Package.Metadata> transitivePackages) {
      super(aspect, configuredAspect);
      this.transitivePackages = checkNotNull(transitivePackages);
      this.key = checkNotNull(key);
    }

    @Override
    public NestedSet<Package.Metadata> getTransitivePackages() {
      return transitivePackages;
    }

    @Override
    public AspectKey getKeyForTransitivePackageTracking() {
      return checkNotNull(key);
    }

    @Override
    public void clear(boolean clearEverything) {
      super.clear(clearEverything);
      transitivePackages = null;
      key = null;
    }

    @Override
    protected ToStringHelper getStringHelper() {
      return super.getStringHelper().add("key", key).add("transitivePackages", transitivePackages);
    }
  }

  @AutoCodec(deserializedInterface = DeserializedSkyValue.class)
  @VisibleForSerialization
  static class AspectValueForAlias extends AspectValue {
    private AspectValueForAlias(Aspect aspect, ConfiguredAspect configuredAspect) {
      super(aspect, configuredAspect);
    }

    @AutoCodec.Instantiator
    AspectValueForAlias(
        ImmutableList<ActionAnalysisMetadata> actions,
        Aspect aspect,
        TransitiveInfoProviderMap providers,
        boolean writesOutputToMasterLog) {
      super(actions, aspect, providers, writesOutputToMasterLog);
    }
  }

  private static final class AspectValueWithTransitivePackagesForAlias
      extends AspectValueWithTransitivePackages {
    private AspectValueWithTransitivePackagesForAlias(
        AspectKey key,
        Aspect aspect,
        ConfiguredAspect configuredAspect,
        NestedSet<Package.Metadata> transitivePackages) {
      super(key, aspect, configuredAspect, transitivePackages);
    }
  }

  public static boolean isForAliasTarget(AspectValue aspectValue) {
    return aspectValue instanceof AspectValueForAlias
        || aspectValue instanceof AspectValueWithTransitivePackagesForAlias
        || (aspectValue instanceof RestoredAspectValue restored && restored.forAlias);
  }

  /** Local readers need this metadata when combining a restored aspect with a computed parent. */
  public static DeferredObjectCodec<AspectValue> codecWithTransitivePackages() {
    return new LocalAspectValueCodec();
  }

  private static final class RestoredAspectValue extends AspectValue
      implements DeserializedSkyValue {
    @Nullable private AspectKey key;
    @Nullable private NestedSet<Package.Metadata> transitivePackages;
    private final boolean forAlias;

    private RestoredAspectValue(
        ImmutableList<ActionAnalysisMetadata> actions,
        Aspect aspect,
        TransitiveInfoProviderMap providers,
        boolean writesOutputToMasterLog,
        @Nullable AspectKey key,
        @Nullable NestedSet<Package.Metadata> transitivePackages,
        boolean forAlias) {
      super(actions, aspect, providers, writesOutputToMasterLog);
      this.key = key;
      this.transitivePackages = transitivePackages;
      this.forAlias = forAlias;
    }

    @Override
    public AspectKey getKeyForTransitivePackageTracking() {
      return key != null ? key : super.getKeyForTransitivePackageTracking();
    }

    @Override
    @Nullable
    public NestedSet<Package.Metadata> getTransitivePackages() {
      return transitivePackages;
    }

    @Override
    public void clear(boolean clearEverything) {
      super.clear(clearEverything);
      key = null;
      transitivePackages = null;
    }
  }

  /** Explicitly selected by the local module; the ordinary generated codec stays unchanged. */
  private static final class LocalAspectValueCodec extends DeferredObjectCodec<AspectValue> {
    @Override
    public boolean autoRegister() {
      return false;
    }

    @Override
    public Class<AspectValue> getEncodedClass() {
      return AspectValue.class;
    }

    @Override
    public ImmutableSet<Class<? extends AspectValue>> additionalEncodedClasses() {
      return ImmutableSet.of(
          AspectValueWithTransitivePackages.class,
          AspectValueForAlias.class,
          AspectValueWithTransitivePackagesForAlias.class,
          RestoredAspectValue.class);
    }

    @Override
    public void serialize(SerializationContext context, AspectValue value, CodedOutputStream out)
        throws SerializationException, IOException {
      out.writeBoolNoTag(isForAliasTarget(value));
      out.writeBoolNoTag(value.getWritesOutputToMasterLog());
      context.serialize(value.getActions(), out);
      context.serialize(value.getAspect(), out);
      context.serialize(value.getProviders(), out);
      var packages = value.getTransitivePackages();
      context.serialize(packages == null ? null : value.getKeyForTransitivePackageTracking(), out);
      context.serialize(packages, out);
    }

    @Override
    @SuppressWarnings("unchecked") // The five fields have fixed types in this codec's format.
    public DeferredValue<AspectValue> deserializeDeferred(
        AsyncDeserializationContext context, CodedInputStream in)
        throws SerializationException, IOException {
      boolean alias = in.readBool();
      boolean writesOutputToMasterLog = in.readBool();
      Object[] fields = new Object[5];
      for (int i = 0; i < fields.length; i++) {
        int index = i;
        context.deserialize(in, fields, (target, value) -> target[index] = value);
      }
      return () -> {
        if ((fields[3] == null) != (fields[4] == null)) {
          throw new SerializationException("incomplete aspect package-tracking metadata");
        }
        return new RestoredAspectValue(
            (ImmutableList<ActionAnalysisMetadata>) fields[0],
            (Aspect) fields[1],
            (TransitiveInfoProviderMap) fields[2],
            writesOutputToMasterLog,
            (AspectKey) fields[3],
            (NestedSet<Package.Metadata>) fields[4],
            alias);
      };
    }
  }
}
