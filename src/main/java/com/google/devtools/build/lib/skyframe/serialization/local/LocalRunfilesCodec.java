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
import java.util.UUID;

/**
 * Shared payload preserving Runfiles identity across independently serialized nested-set branches.
 *
 * <p>Must be installed through ValueSharingAdapter. Runfiles uses reference equality, which also
 * affects equality of Starlark providers containing it. Repeating its inline dynamic encoding
 * creates unequal copies and can duplicate provider leaves when a depset is expanded.
 *
 * <p>The complete producing key and creation ordinal distinguish separate originals while allowing
 * independent writers to agree on the same producer's value. Unscoped objects retain a nonce.
 * FingerprintValueService restores one object for each shared payload fingerprint. Content-only
 * interning would incorrectly merge distinct originals. This does not intern newly evaluated
 * values. The memoized allArtifacts field is omitted: whether its getter ran is not part of the
 * value.
 */
@SuppressWarnings("SunApi")
final class LocalRunfilesCodec extends DeferredObjectCodec<Runfiles> {
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
    out.writeBoolNoTag(origin != null);
    if (origin != null) {
      context.serialize(origin.owner(), out);
      out.writeInt32NoTag(origin.ordinal());
    } else {
      UUID identity = UUID.randomUUID();
      out.writeFixed64NoTag(identity.getMostSignificantBits());
      out.writeFixed64NoTag(identity.getLeastSignificantBits());
    }
    for (var field : FIELDS) {
      field.serialize(context, out, value);
    }
  }

  @Override
  public DeferredValue<Runfiles> deserializeDeferred(
      AsyncDeserializationContext context, CodedInputStream in)
      throws SerializationException, IOException {
    Object[] owner = new Object[1];
    boolean hasOrigin = in.readBool();
    int ordinal;
    if (hasOrigin) {
      context.deserialize(in, owner, (target, value) -> target[0] = value);
      ordinal = in.readInt32();
      if (ordinal < 0) {
        throw new SerializationException("negative Runfiles creation ordinal");
      }
    } else {
      in.readFixed64();
      in.readFixed64();
      ordinal = 0;
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
      if (hasOrigin) {
        RunfilesOrigin.restore(value, new RunfilesOrigin.Origin(owner[0], ordinal));
      }
      return value;
    };
  }
}
