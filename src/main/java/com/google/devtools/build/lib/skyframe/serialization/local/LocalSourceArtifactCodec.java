// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.devtools.build.lib.actions.Artifact.ArtifactSerializationContext;
import com.google.devtools.build.lib.actions.Artifact.SourceArtifact;
import com.google.devtools.build.lib.actions.ArtifactOwner;
import com.google.devtools.build.lib.actions.ArtifactRoot;
import com.google.devtools.build.lib.cmdline.Label;
import com.google.devtools.build.lib.cmdline.LabelSyntaxException;
import com.google.devtools.build.lib.skyframe.PackageValue;
import com.google.devtools.build.lib.skyframe.serialization.AsyncDeserializationContext;
import com.google.devtools.build.lib.skyframe.serialization.DeferredObjectCodec;
import com.google.devtools.build.lib.skyframe.serialization.SerializationContext;
import com.google.devtools.build.lib.skyframe.serialization.SerializationException;
import com.google.devtools.build.lib.vfs.PathFragment;
import com.google.protobuf.CodedInputStream;
import com.google.protobuf.CodedOutputStream;
import java.io.IOException;

/** Restores package roots needed by Bazel's execution symlink forest on a fresh reader. */
final class LocalSourceArtifactCodec extends DeferredObjectCodec<SourceArtifact> {
  @Override
  public boolean autoRegister() {
    return false;
  }

  @Override
  public Class<SourceArtifact> getEncodedClass() {
    return SourceArtifact.class;
  }

  @Override
  public void serialize(SerializationContext context, SourceArtifact value, CodedOutputStream out)
      throws IOException, SerializationException {
    Label label = value.getOwnerLabel();
    out.writeStringNoTag(label == null ? "" : label.getCanonicalForm());
    out.writeBoolNoTag(value.getRoot().isExternal());
    context.serialize(value.getExecPath(), out);
    context.serialize(value.getArtifactOwner(), out);
    // Unowned artifacts have no package to reload. Their Path objects still use LocalPathCodec.
    if (label == null) {
      context.serialize(value.getRoot(), out);
    }
  }

  @Override
  public DeferredValue<SourceArtifact> deserializeDeferred(
      AsyncDeserializationContext context, CodedInputStream in)
      throws IOException, SerializationException {
    String label = in.readStringRequireUtf8();
    var builder =
        new Builder(context.getDependency(ArtifactSerializationContext.class), in.readBool());
    context.deserialize(in, builder, (b, v) -> b.execPath = (PathFragment) v);
    context.deserialize(in, builder, (b, v) -> b.owner = (ArtifactOwner) v);
    if (label.isEmpty()) {
      context.deserialize(in, builder, (b, v) -> b.root = (ArtifactRoot) v);
    } else {
      try {
        context.getSkyValue(
            Label.parseCanonical(label).getPackageIdentifier(),
            builder,
            (b, v) -> {
              var root = ((PackageValue) v).getPackage().getSourceRoot();
              b.root =
                  b.external
                      ? ArtifactRoot.asExternalSourceRoot(root)
                      : ArtifactRoot.asSourceRoot(root);
            });
      } catch (LabelSyntaxException e) {
        throw new SerializationException("invalid source artifact label", e);
      }
    }
    return builder;
  }

  private static final class Builder implements DeferredValue<SourceArtifact> {
    private final ArtifactSerializationContext context;
    private final boolean external;
    private PathFragment execPath;
    private ArtifactOwner owner;
    private ArtifactRoot root;

    Builder(ArtifactSerializationContext context, boolean external) {
      this.context = context;
      this.external = external;
    }

    @Override
    public SourceArtifact call() {
      return context.getSourceArtifact(execPath, root, owner);
    }
  }
}
