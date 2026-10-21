// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.common.collect.ImmutableMap;
import com.google.devtools.build.lib.cmdline.RepositoryMapping;
import com.google.devtools.build.lib.cmdline.RepositoryName;
import com.google.devtools.build.lib.skyframe.serialization.AsyncDeserializationContext;
import com.google.devtools.build.lib.skyframe.serialization.DeferredObjectCodec;
import com.google.devtools.build.lib.skyframe.serialization.SerializationContext;
import com.google.devtools.build.lib.skyframe.serialization.SerializationException;
import com.google.protobuf.CodedInputStream;
import com.google.protobuf.CodedOutputStream;
import java.io.IOException;

/** Shares immutable repository mappings across independently serialized package metadata. */
final class LocalRepositoryMappingCodec extends DeferredObjectCodec<RepositoryMapping> {
  private static final int MAGIC = 0x524d5001;
  private static final Class<?> CACHED_INVERSE_CLASS =
      RepositoryMapping.EMPTY.withCachedInverseMap().getClass();

  @Override
  public boolean autoRegister() {
    return false;
  }

  @Override
  public Class<RepositoryMapping> getEncodedClass() {
    return RepositoryMapping.class;
  }

  @Override
  public void serialize(
      SerializationContext context, RepositoryMapping value, CodedOutputStream out)
      throws SerializationException, IOException {
    Class<?> type = value.getClass();
    if (type != RepositoryMapping.class && type != CACHED_INVERSE_CLASS) {
      throw new SerializationException("unsupported RepositoryMapping subtype: " + type.getName());
    }
    out.writeFixed32NoTag(MAGIC);
    out.writeBoolNoTag(type == CACHED_INVERSE_CLASS);
    // Preserve iteration order: getInverse chooses the first apparent name for each repository.
    context.serialize(value.entries(), out);
    context.serialize(value.contextRepo(), out);
  }

  @Override
  public DeferredValue<RepositoryMapping> deserializeDeferred(
      AsyncDeserializationContext context, CodedInputStream in)
      throws SerializationException, IOException {
    if (in.readFixed32() != MAGIC) {
      throw new SerializationException("invalid repository mapping payload");
    }
    var builder = new Builder(in.readBool());
    context.deserialize(in, builder, Builder::setEntries);
    context.deserialize(in, builder, Builder::setContextRepo);
    return builder;
  }

  private static final class Builder implements DeferredValue<RepositoryMapping> {
    private final boolean cachedInverse;
    private ImmutableMap<String, RepositoryName> entries;
    private RepositoryName contextRepo;

    Builder(boolean cachedInverse) {
      this.cachedInverse = cachedInverse;
    }

    @SuppressWarnings("unchecked")
    private static void setEntries(Builder builder, Object value) {
      builder.entries = (ImmutableMap<String, RepositoryName>) value;
    }

    private static void setContextRepo(Builder builder, Object value) {
      builder.contextRepo = (RepositoryName) value;
    }

    @Override
    public RepositoryMapping call() {
      var mapping = RepositoryMapping.create(entries, contextRepo);
      return cachedInverse ? mapping.withCachedInverseMap() : mapping;
    }
  }
}
