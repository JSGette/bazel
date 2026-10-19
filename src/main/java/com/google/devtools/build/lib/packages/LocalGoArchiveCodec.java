// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.packages;

import com.google.common.collect.MapMaker;
import com.google.devtools.build.lib.skyframe.serialization.AsyncDeserializationContext;
import com.google.devtools.build.lib.skyframe.serialization.AsyncObjectCodec;
import com.google.devtools.build.lib.skyframe.serialization.DeferredObjectCodec;
import com.google.devtools.build.lib.skyframe.serialization.SerializationContext;
import com.google.devtools.build.lib.skyframe.serialization.SerializationException;
import com.google.protobuf.CodedInputStream;
import com.google.protobuf.CodedOutputStream;
import java.io.IOException;
import java.util.concurrent.ConcurrentMap;
import java.util.function.IntFunction;
import java.util.function.ToIntFunction;

/**
 * Local schema-provider codec that shares the acyclic rules_go GoArchive dependency graph.
 *
 * <p>Provider definitions are evaluator-local identities. These records are not portable SkyCache
 * records. Other schema providers stay inline, including cyclic values: registering the empty
 * instance before decoding its fields preserves their ordinary memoize-before behavior.
 */
public final class LocalGoArchiveCodec extends AsyncObjectCodec<StarlarkInfoWithSchema> {
  private static final String FIELDS = "local-go-archive-fields-v1";
  private final ToIntFunction<StarlarkProvider> encodeProvider;
  private final IntFunction<StarlarkProvider> decodeProvider;
  private final FieldsCodec fieldsCodec = new FieldsCodec();
  // Both sides are weak: an array contains descendants, and retaining it strongly could keep a
  // chain of provider owners alive through this index after their graph values are evicted.
  private final ConcurrentMap<StarlarkInfoWithSchema, Object[]> fields =
      new MapMaker().weakKeys().weakValues().makeMap();

  public LocalGoArchiveCodec(
      ToIntFunction<StarlarkProvider> encodeProvider,
      IntFunction<StarlarkProvider> decodeProvider) {
    this.encodeProvider = encodeProvider;
    this.decodeProvider = decodeProvider;
  }

  @Override
  public boolean autoRegister() {
    return false;
  }

  @Override
  public Class<StarlarkInfoWithSchema> getEncodedClass() {
    return StarlarkInfoWithSchema.class;
  }

  @Override
  public void serialize(
      SerializationContext context, StarlarkInfoWithSchema value, CodedOutputStream out)
      throws IOException, SerializationException {
    var provider = value.getProvider();
    out.writeInt32NoTag(encodeProvider.applyAsInt(provider));
    boolean shared =
        provider.isExported()
            && provider.getName().equals("GoArchive")
            && provider.getKey().getExtensionLabel().getPackageName().equals("go/private")
            && provider.getKey().getExtensionLabel().getName().equals("providers.bzl");
    out.writeBoolNoTag(shared);
    if (shared) {
      // GoArchive.direct contains other GoArchives, in addition to its shared depsets. Inline
      // serialization duplicates the transitive provider DAG for every configured target.
      context.putSharedValue(
          fields.computeIfAbsent(value, StarlarkInfoWithSchema::getValuesForSerialization),
          FIELDS,
          fieldsCodec,
          out);
    } else {
      context.serialize(value.getValuesForSerialization(), out);
    }
  }

  @Override
  public StarlarkInfoWithSchema deserializeAsync(
      AsyncDeserializationContext context, CodedInputStream in)
      throws IOException, SerializationException {
    StarlarkProvider provider;
    try {
      provider = decodeProvider.apply(in.readInt32());
    } catch (IllegalArgumentException e) {
      throw new SerializationException("Unknown local provider definition", e);
    }
    var result = StarlarkInfoWithSchema.create(provider, new Object[provider.getFields().size()]);
    context.registerInitialValue(result);
    if (in.readBool()) {
      context.getSharedValue(in, FIELDS, fieldsCodec, result, LocalGoArchiveCodec::setFields);
    } else {
      context.deserialize(in, result, LocalGoArchiveCodec::setFields);
    }
    return result;
  }

  @SuppressWarnings("ForOverride") // Populate only a fresh, registered deserialization placeholder.
  private static void setFields(StarlarkInfoWithSchema target, Object fields)
      throws SerializationException {
    if (!(fields instanceof Object[] values)
        || values.length != target.getProvider().getFields().size()) {
      throw new SerializationException("Invalid local schema provider fields");
    }
    for (int i = 0; i < values.length; i++) {
      target.setValueAt(i, values[i]);
    }
  }

  /** The shared payload is just a field array; the outer record supplies the local definition. */
  private static final class FieldsCodec extends DeferredObjectCodec<Object[]> {
    @Override
    public boolean autoRegister() {
      return false;
    }

    @Override
    public Class<Object[]> getEncodedClass() {
      return Object[].class;
    }

    @Override
    public void serialize(SerializationContext context, Object[] value, CodedOutputStream out)
        throws IOException, SerializationException {
      context.serialize(value, out);
    }

    @Override
    public DeferredValue<Object[]> deserializeDeferred(
        AsyncDeserializationContext context, CodedInputStream in)
        throws IOException, SerializationException {
      var value = SimpleDeferredValue.<Object[]>create();
      context.deserialize(in, value, SimpleDeferredValue::set);
      return value;
    }
  }
}
