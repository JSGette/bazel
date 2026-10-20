// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.devtools.build.lib.unsafe.UnsafeProvider.unsafe;

import com.google.devtools.build.lib.analysis.Runfiles;
import com.google.devtools.build.lib.analysis.RunfilesOrigin;
import com.google.devtools.build.lib.skyframe.serialization.AsyncDeserializationContext;
import com.google.devtools.build.lib.skyframe.serialization.DeferredObjectCodec;
import com.google.devtools.build.lib.skyframe.serialization.DynamicCodec;
import com.google.devtools.build.lib.skyframe.serialization.SerializationContext;
import com.google.devtools.build.lib.skyframe.serialization.SerializationException;
import com.google.protobuf.CodedInputStream;
import com.google.protobuf.CodedOutputStream;
import java.io.IOException;
import java.util.List;

/**
 * Shared payload preserving Runfiles identity across independently serialized nested-set branches.
 *
 * <p>Must be installed through ValueSharingAdapter. Runfiles uses reference equality, which also
 * affects equality of Starlark providers containing it. Repeating its inline dynamic encoding
 * creates unequal copies and can duplicate provider leaves when a depset is expanded.
 *
 * <p>The complete producing key and creation ordinal distinguish separate originals while allowing
 * independent writers to agree on the same producer's value. Unscoped objects cannot be published
 * safely and are rejected. FingerprintValueService restores one object for each shared payload
 * fingerprint. Content-only interning would incorrectly merge distinct originals. Restored values
 * reconnect to equivalent live values with the same origin. The memoized allArtifacts field is
 * omitted: whether its getter ran is not part of the value.
 */
@SuppressWarnings("SunApi")
final class LocalRunfilesCodec extends DeferredObjectCodec<Runfiles> {
  private static final int MAGIC = 0x52464933;
  private static final List<DynamicCodec.FieldHandler> FIELDS =
      DynamicCodec.getFieldHandlerMap(Runfiles.class).entrySet().stream()
          .filter(entry -> !entry.getKey().getName().equals("allArtifacts"))
          .map(entry -> entry.getValue())
          .toList();

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
    var origin = RunfilesOrigin.get(value);
    if (origin == null) {
      throw new SerializationException("missing creator provenance for Runfiles");
    }
    out.writeFixed32NoTag(MAGIC);
    context.serialize(origin.owner(), out);
    out.writeInt32NoTag(origin.ordinal());
    for (var field : FIELDS) {
      field.serialize(context, out, value);
    }
  }

  @Override
  public DeferredValue<Runfiles> deserializeDeferred(
      AsyncDeserializationContext context, CodedInputStream in)
      throws SerializationException, IOException {
    if (in.readFixed32() != MAGIC) {
      throw new SerializationException("invalid Runfiles identity payload");
    }
    Object[] owner = new Object[1];
    context.deserialize(in, owner, (target, value) -> target[0] = value);
    int ordinal = in.readInt32();
    if (ordinal < 0) {
      throw new SerializationException("negative Runfiles creation ordinal");
    }
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
    return () -> {
      var restored = RunfilesOrigin.restore(value, new RunfilesOrigin.Origin(owner[0], ordinal));
      if (restored == null) {
        throw new SerializationException("Runfiles producer is being evaluated: " + owner[0]);
      }
      return restored;
    };
  }
}
