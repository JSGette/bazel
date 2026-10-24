// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
import java.util.*;
import org.roaringbitmap.RoaringBitmap;

/** Structural CPU/space comparison only: no disk, digest, or filesystem validation claims. */
public final class ProofLayoutBenchmark {
  static volatile long sink;

  record Graph(int leaves, int[][] groups, int[][] roots, int[] queries) {}

  static Graph graph(String shape) {
    int count = 4096, groupSize = shape.equals("disjoint") ? 32 : 128;
    int groupCount = shape.equals("disjoint") ? count : 64;
    int[][] groups = new int[groupCount][];
    for (int i = 0; i < groupCount; i++) {
      groups[i] = new int[groupSize];
      for (int j = 0; j < groupSize; j++) groups[i][j] = i * groupSize + j;
    }
    int[][] roots = new int[count][];
    for (int i = 0; i < count; i++) {
      roots[i] =
          shape.equals("disjoint")
              ? new int[] {i}
              : new int[] {i % 64, (i + 1) % 64, (i + 2) % 64, (i + 3) % 64};
    }
    int[] queries = new int[shape.equals("sparse") ? 16 : count];
    for (int i = 0; i < queries.length; i++) queries[i] = i * 251 % count;
    return new Graph(groupCount * groupSize, groups, roots, queries);
  }

  static long mix(int x) {
    return (x * 0x9e3779b9L) ^ (x >>> 3);
  }

  static long dag(Graph g) {
    boolean[] visited = new boolean[g.groups.length], leaves = new boolean[g.leaves];
    long sum = 0;
    for (int root : g.queries)
      for (int group : g.roots[root]) {
        if (visited[group]) continue;
        visited[group] = true;
        for (int leaf : g.groups[group])
          if (!leaves[leaf]) {
            leaves[leaf] = true;
            sum += mix(leaf);
          }
      }
    return sum;
  }

  static long flat(Graph g, int[][] roots) {
    boolean[] visited = new boolean[g.leaves];
    long sum = 0;
    for (int root : g.queries)
      for (int leaf : roots[root])
        if (!visited[leaf]) {
          visited[leaf] = true;
          sum += mix(leaf);
        }
    return sum;
  }

  static long bitmap(Graph g, BitSet[] roots) {
    BitSet needed = new BitSet(g.leaves);
    for (int root : g.queries) needed.or(roots[root]);
    long sum = 0;
    for (int leaf = needed.nextSetBit(0); leaf >= 0; leaf = needed.nextSetBit(leaf + 1))
      sum += mix(leaf);
    return sum;
  }

  static long roaring(Graph g, RoaringBitmap[] roots) {
    RoaringBitmap needed =
        org.roaringbitmap.FastAggregation.or(
            Arrays.stream(g.queries).mapToObj(i -> roots[i]).iterator());
    long sum = 0;
    var iterator = needed.getIntIterator();
    while (iterator.hasNext()) sum += mix(iterator.next());
    return sum;
  }

  public static void main(String[] args) {
    Locale.setDefault(Locale.ROOT);
    System.out.println(
        "{\"kind\":\"structural_only_no_io\",\"warmups\":5,\"samples\":7,\"cases\":[");
    boolean comma = false;
    for (String shape : List.of("shared", "disjoint", "sparse")) {
      long start = System.nanoTime();
      Graph g = graph(shape);
      long dagBuild = System.nanoTime() - start;
      long dagBytes = 0, flatBytes = 0, bitmapBytes = 0;
      for (int[] group : g.groups) dagBytes += 4L * group.length;
      for (int[] root : g.roots) dagBytes += 4L * root.length;
      start = System.nanoTime();
      int[][] flat = new int[g.roots.length][];
      for (int i = 0; i < flat.length; i++) {
        int count = 0;
        for (int group : g.roots[i]) count += g.groups[group].length;
        flat[i] = new int[count];
        int at = 0;
        for (int group : g.roots[i]) for (int leaf : g.groups[group]) flat[i][at++] = leaf;
        flatBytes += 4L * count;
      }
      long flatBuild = System.nanoTime() - start;
      start = System.nanoTime();
      BitSet[] bitmaps = new BitSet[flat.length];
      for (int i = 0; i < flat.length; i++) {
        bitmaps[i] = new BitSet();
        for (int leaf : flat[i]) bitmaps[i].set(leaf);
        bitmapBytes += bitmaps[i].size() / 8;
      }
      long bitmapBuild = System.nanoTime() - start;
      start = System.nanoTime();
      RoaringBitmap[] roaring = new RoaringBitmap[flat.length];
      long roaringBytes = 0;
      for (int i = 0; i < flat.length; i++) {
        roaring[i] = RoaringBitmap.bitmapOf(flat[i]);
        roaring[i].runOptimize();
        roaringBytes += roaring[i].serializedSizeInBytes();
      }
      long roaringBuild = System.nanoTime() - start;
      long expected = dag(g);
      if (flat(g, flat) != expected
          || bitmap(g, bitmaps) != expected
          || roaring(g, roaring) != expected) throw new AssertionError();
      if (comma) System.out.println(",");
      comma = true;
      System.out.printf(
          "{\"shape\":\"%s\",\"queries\":%d,\"payload_bytes\":{\"dag\":%d,\"flat\":%d,\"bitmap\":%d,\"roaring_serialized\":%d},\"construction_ms\":{\"dag\":%.3f,\"flat_incremental\":%.3f,\"bitmap_incremental\":%.3f,\"roaring_incremental\":%.3f},\"rounds\":[",
          shape,
          g.queries.length,
          dagBytes,
          flatBytes,
          bitmapBytes,
          roaringBytes,
          dagBuild / 1e6,
          flatBuild / 1e6,
          bitmapBuild / 1e6,
          roaringBuild / 1e6);
      for (int round = -5; round < 7; round++) {
        double[] timings = new double[4];
        for (int position = 0; position < 4; position++) {
          int method = Math.floorMod(round + position, 4);
          start = System.nanoTime();
          for (int repeat = 0; repeat < 100; repeat++) {
            long result =
                switch (method) {
                  case 0 -> dag(g);
                  case 1 -> flat(g, flat);
                  case 2 -> bitmap(g, bitmaps);
                  default -> roaring(g, roaring);
                };
            if (result != expected) throw new AssertionError();
            sink = result;
          }
          timings[method] = (System.nanoTime() - start) / 1e6 / 100;
        }
        if (round >= 0) {
          if (round > 0) System.out.print(",");
          System.out.printf(
              "{\"dag_ms\":%.6f,\"flat_ms\":%.6f,\"bitmap_ms\":%.6f,\"roaring_ms\":%.6f}",
              timings[0], timings[1], timings[2], timings[3]);
        }
      }
      System.out.print("]}");
    }
    System.out.println("]}");
  }
}
