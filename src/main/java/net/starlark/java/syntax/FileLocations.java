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

package net.starlark.java.syntax;

import com.google.common.collect.Interner;
import com.google.common.collect.Interners;
import java.util.Arrays;
import java.util.Objects;
import javax.annotation.concurrent.Immutable;

/**
 * FileLocations maps each source offset within a file to a Location. An offset is a (UTF-16) char
 * index such that {@code 0 <= offset <= size}. A Location is a (file, line, column) triple.
 */
@Immutable
final class FileLocations {

  // A line map depends only on newline offsets, so it can be shared across files and workspaces.
  // Filenames and input sizes remain local. Weak interning does not retain discarded syntax trees.
  private static final Interner<LineStarts> lineStartsInterner = Interners.newWeakInterner();

  private final LineStarts linestart; // maps line number (line >= 1) to char offset
  private final String file;
  private final int size; // size of file in chars

  private FileLocations(LineStarts linestart, String file, int size) {
    this.linestart = linestart;
    this.file = file;
    this.size = size;
  }

  static FileLocations create(char[] buffer, String file) {
    return new FileLocations(computeLinestart(buffer), file, buffer.length);
  }

  LineStarts lineStarts() {
    return linestart;
  }

  String file() {
    return file;
  }

  private int getLineAt(int offset) {
    if (offset < 0 || offset > size) {
      throw new IllegalStateException("Illegal position: " + offset);
    }
    int lowBoundary = 1;
    int highBoundary = linestart.length() - 1;
    while (true) {
      if ((highBoundary - lowBoundary) <= 1) {
        if (linestart.get(highBoundary) > offset) {
          return lowBoundary;
        } else {
          return highBoundary;
        }
      }
      int medium = lowBoundary + ((highBoundary - lowBoundary) >> 1);
      if (linestart.get(medium) > offset) {
        highBoundary = medium;
      } else {
        lowBoundary = medium;
      }
    }
  }

  Location getLocation(int offset) {
    int line = getLineAt(offset);
    int column = offset - linestart.get(line) + 1;
    return new Location(file, line, column);
  }

  int size() {
    return size;
  }

  @Override
  public int hashCode() {
    return Objects.hash(linestart.hashCode(), file, size);
  }

  @Override
  public boolean equals(Object other) {
    if (!(other instanceof FileLocations)) {
      return false;
    }
    FileLocations that = (FileLocations) other;
    return this.size == that.size
        && this.linestart.equals(that.linestart)
        && this.file.equals(that.file);
  }

  private static LineStarts computeLinestart(char[] buffer) {
    // Compute the size.
    int size = 2;
    for (int i = 0; i < buffer.length; i++) {
      if (buffer[i] == '\n') {
        size++;
      }
    }
    int[] linestart = new int[size];
    int index = 0;
    linestart[index++] = 0; // The 0th line does not exist - so we fill something in
    // to make sure the start pos for the 1st line ends up at
    // linestart[1]. Using 0 is useful for tables that are
    // completely empty.
    linestart[index++] = 0; // The first line ("line 1") starts at offset 0.

    // Scan the buffer and record the offset of each line start. Doing this
    // once upfront is faster than checking each char as it is pulled from
    // the buffer.
    for (int i = 0; i < buffer.length; i++) {
      if (buffer[i] == '\n') {
        linestart[index++] = i + 1;
      }
    }
    return lineStartsInterner.intern(new LineStarts(linestart));
  }

  /** Owns a line-offset array which is never exposed or modified after construction. */
  @Immutable
  static final class LineStarts {
    private final int[] offsets;

    private LineStarts(int[] offsets) {
      this.offsets = offsets;
    }

    int length() {
      return offsets.length;
    }

    int get(int line) {
      return offsets[line];
    }

    @Override
    public int hashCode() {
      return Arrays.hashCode(offsets);
    }

    @Override
    public boolean equals(Object obj) {
      return obj instanceof LineStarts other && Arrays.equals(offsets, other.offsets);
    }
  }
}
