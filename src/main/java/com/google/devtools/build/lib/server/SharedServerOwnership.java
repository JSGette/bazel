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

import com.google.devtools.build.lib.jni.JniLoader;
import java.io.IOException;

/** Validates the launcher's Linux process-lifetime ownership handoff before runtime creation. */
public final class SharedServerOwnership {
  public static void adopt(String descriptors, String control, String output) throws IOException {
    Throwable loadError = JniLoader.getJniLoadError();
    if (loadError != null) {
      throw new IOException("Shared ownership requires native support", loadError);
    }
    String[] values = descriptors.split(",", -1);
    if (values.length != 6) {
      throw new IOException("Missing or invalid shared ownership handoff");
    }
    int[] fds = new int[6];
    try {
      for (int i = 0; i < fds.length; i++) {
        fds[i] = Integer.parseInt(values[i]);
      }
    } catch (NumberFormatException e) {
      throw new IOException("Invalid shared ownership descriptor", e);
    }
    adoptNative(fds, control, output);
  }

  // Paths use Bazel's internal Latin-1 byte encoding, like other JNI filesystem calls.
  private static native void adoptNative(int[] descriptors, String control, String output)
      throws IOException;

  private SharedServerOwnership() {}
}
