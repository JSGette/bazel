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

package com.google.devtools.build.lib.server;

import java.io.IOException;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.SecureDirectoryStream;
import java.nio.file.attribute.BasicFileAttributeView;
import java.nio.file.attribute.BasicFileAttributes;

/**
 * Pins the physical workspace and output directories for the Linux shared-server prototype.
 *
 * <p>Capture before runtime initialization and retain through drained runtime cleanup. Open
 * directory handles prevent inode reuse while this owner exists. Every dispatch checks the current
 * path against the handle's identity; a mismatch or observation failure permanently fences ordinary
 * commands. This is an admission check, not a snapshot or a lock against concurrent filesystem
 * mutations.
 */
public final class EngineDirectories implements AutoCloseable {
  private final PinnedDirectory workspace;
  private final PinnedDirectory outputBase;
  private boolean fenced;
  private boolean closed;

  public static EngineDirectories open(Path workspace, Path outputBase) throws IOException {
    PinnedDirectory workspaceHandle = PinnedDirectory.open(workspace);
    try {
      return new EngineDirectories(workspaceHandle, PinnedDirectory.open(outputBase));
    } catch (IOException | RuntimeException e) {
      try {
        workspaceHandle.close();
      } catch (IOException cleanupFailure) {
        e.addSuppressed(cleanupFailure);
      }
      throw e;
    }
  }

  private EngineDirectories(PinnedDirectory workspace, PinnedDirectory outputBase) {
    this.workspace = workspace;
    this.outputBase = outputBase;
  }

  /** Applies the same admission rule before RPC dispatch and after waiting for the command lock. */
  public synchronized boolean canDispatch(String command) {
    return isValid() || (command.equals("shutdown") && canShutdown());
  }

  /** Fencing is sticky, even if the original directory is subsequently restored. */
  public synchronized boolean isValid() {
    if (!fenced && !originalDirectoriesPresent()) {
      fenced = true;
    }
    return !fenced;
  }

  /**
   * Cleanup may run after restoring the original objects, but never against replacement
   * directories. This does not remove the fence or permit subsequent ordinary commands.
   */
  public synchronized boolean canShutdown() {
    return originalDirectoriesPresent();
  }

  private boolean originalDirectoriesPresent() {
    if (closed) {
      return false;
    }
    try {
      return workspace.matches() && outputBase.matches();
    } catch (IOException | SecurityException e) {
      return false;
    }
  }

  @Override
  public synchronized void close() throws IOException {
    if (closed) {
      return;
    }
    closed = true;
    fenced = true;
    try {
      outputBase.close();
    } finally {
      workspace.close();
    }
  }

  private static final class PinnedDirectory implements AutoCloseable {
    private final Path path;
    private final DirectoryStream<Path> handle;
    private final Object identity;

    private PinnedDirectory(Path path, DirectoryStream<Path> handle, Object identity) {
      this.path = path;
      this.handle = handle;
      this.identity = identity;
    }

    static PinnedDirectory open(Path path) throws IOException {
      if (!path.isAbsolute()) {
        throw new IOException("Engine directory must be absolute: " + path);
      }
      DirectoryStream<Path> handle = Files.newDirectoryStream(path);
      try {
        if (!(handle instanceof SecureDirectoryStream<Path> secure)) {
          throw new IOException("Engine directory provider cannot pin identity: " + path);
        }
        BasicFileAttributes attributes =
            secure.getFileAttributeView(BasicFileAttributeView.class).readAttributes();
        Object identity = attributes.fileKey();
        if (!attributes.isDirectory() || identity == null) {
          throw new IOException("Engine directory has no stable file identity: " + path);
        }
        PinnedDirectory result = new PinnedDirectory(path, handle, identity);
        if (!result.matches()) {
          throw new IOException("Engine directory changed while opening: " + path);
        }
        return result;
      } catch (IOException | RuntimeException e) {
        try {
          handle.close();
        } catch (IOException cleanupFailure) {
          e.addSuppressed(cleanupFailure);
        }
        throw e;
      }
    }

    boolean matches() throws IOException {
      BasicFileAttributes current =
          Files.readAttributes(path, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
      return current.isDirectory() && identity.equals(current.fileKey());
    }

    @Override
    public void close() throws IOException {
      handle.close();
    }
  }
}
