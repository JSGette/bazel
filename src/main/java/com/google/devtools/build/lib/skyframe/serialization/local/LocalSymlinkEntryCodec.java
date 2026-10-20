// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.devtools.build.lib.unsafe.UnsafeProvider.unsafe;

import com.google.devtools.build.lib.analysis.RunfilesOrigin;
import com.google.devtools.build.lib.analysis.SymlinkEntry;
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
 * Preserves a symlink entry's producer identity without adding structural equality.
 *
 * <p>Distinct same-path entries must remain distinct for last-writer conflict precedence. Sharing
 * agrees only on the same producer/ordinal and contents. Install through ValueSharingAdapter.
 */
@SuppressWarnings("SunApi")
final class LocalSymlinkEntryCodec extends DeferredObjectCodec<SymlinkEntry> {
  private static final int MAGIC = 0x534c4932;
  private static final List<DynamicCodec.FieldHandler> FIELDS =
      DynamicCodec.getFieldHandlerMap(SymlinkEntry.class).entrySet().stream()
          .map(entry -> entry.getValue())
          .toList();

  @Override
  public boolean autoRegister() {
    return false;
  }

  @Override
  public Class<SymlinkEntry> getEncodedClass() {
    return SymlinkEntry.class;
  }

  @Override
  public void serialize(SerializationContext context, SymlinkEntry value, CodedOutputStream out)
      throws SerializationException, IOException {
    var origin = RunfilesOrigin.get(value);
    if (origin == null) {
      throw new SerializationException("missing creator provenance for SymlinkEntry");
    }
    out.writeFixed32NoTag(MAGIC);
    context.serialize(origin.owner(), out);
    out.writeInt32NoTag(origin.ordinal());
    for (var field : FIELDS) {
      field.serialize(context, out, value);
    }
  }

  @Override
  public DeferredValue<SymlinkEntry> deserializeDeferred(
      AsyncDeserializationContext context, CodedInputStream in)
      throws SerializationException, IOException {
    if (in.readFixed32() != MAGIC) {
      throw new SerializationException("invalid SymlinkEntry identity payload");
    }
    Object[] owner = new Object[1];
    context.deserialize(in, owner, (target, value) -> target[0] = value);
    int ordinal = in.readInt32();
    if (ordinal < 0) {
      throw new SerializationException("negative SymlinkEntry creation ordinal");
    }
    SymlinkEntry value;
    try {
      value = (SymlinkEntry) unsafe().allocateInstance(SymlinkEntry.class);
    } catch (InstantiationException e) {
      throw new SerializationException("could not restore SymlinkEntry", e);
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
