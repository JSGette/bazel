// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.devtools.build.lib.unsafe.UnsafeProvider.unsafe;

import com.google.devtools.build.lib.analysis.Runfiles;
import com.google.devtools.build.lib.skyframe.serialization.AsyncDeserializationContext;
import com.google.devtools.build.lib.skyframe.serialization.DeferredObjectCodec;
import com.google.devtools.build.lib.skyframe.serialization.DynamicCodec;
import com.google.devtools.build.lib.skyframe.serialization.SerializationContext;
import com.google.devtools.build.lib.skyframe.serialization.SerializationException;
import com.google.protobuf.CodedInputStream;
import com.google.protobuf.CodedOutputStream;
import java.io.IOException;
import java.util.List;
import java.util.UUID;

/**
 * Shared payload preserving Runfiles identity across independently serialized nested-set branches.
 *
 * <p>Must be installed through ValueSharingAdapter. Runfiles uses reference equality, which also
 * affects equality of Starlark providers containing it. Repeating its inline dynamic encoding
 * creates unequal copies and can duplicate provider leaves when a depset is expanded.
 *
 * <p>A nonce keeps distinct original Runfiles objects distinct even when all fields are identical.
 * FingerprintValueService's identity-keyed put cache encodes each original object once; its reader
 * cache then restores one object for that fingerprint. Content-only interning would incorrectly
 * merge distinct originals. The ordinary field handlers preserve the existing field representation.
 */
@SuppressWarnings("SunApi")
final class LocalRunfilesCodec extends DeferredObjectCodec<Runfiles> {
  private static final List<DynamicCodec.FieldHandler> FIELDS =
      List.copyOf(DynamicCodec.getFieldHandlerMap(Runfiles.class).values());

  @Override
  public boolean autoRegister() {
    return false;
  }

  @Override
  public Class<Runfiles> getEncodedClass() {
    return Runfiles.class;
  }

  @Override
  public void serialize(SerializationContext context, Runfiles value, CodedOutputStream out)
      throws SerializationException, IOException {
    UUID identity = UUID.randomUUID();
    out.writeFixed64NoTag(identity.getMostSignificantBits());
    out.writeFixed64NoTag(identity.getLeastSignificantBits());
    for (var field : FIELDS) {
      field.serialize(context, out, value);
    }
  }

  @Override
  public DeferredValue<Runfiles> deserializeDeferred(
      AsyncDeserializationContext context, CodedInputStream in)
      throws SerializationException, IOException {
    // The identity is embodied by this shared payload's fingerprint, not by a Runfiles field.
    in.readFixed64();
    in.readFixed64();
    Runfiles value;
    try {
      value = (Runfiles) unsafe().allocateInstance(Runfiles.class);
    } catch (InstantiationException e) {
      throw new SerializationException("could not restore Runfiles", e);
    }
    for (var field : FIELDS) {
      field.deserialize(context, in, value);
    }
    // Deferred completion waits for the fields, including nested shared values, to be restored.
    return () -> value;
  }
}
