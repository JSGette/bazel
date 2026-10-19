// Copyright 2023 The Bazel Authors. All rights reserved.
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
package com.google.devtools.build.skyframe;

import com.google.common.collect.ImmutableSet;
import java.lang.ref.WeakReference;
import java.util.Collection;
import java.util.List;
import java.util.Set;
import java.util.concurrent.locks.ReentrantLock;
import java.util.function.Function;
import javax.annotation.Nullable;

/** Stable entry identity; all operations retain the delegate until they finish. */
final class DiskBackedNodeEntry implements InMemoryNodeEntry {
  private final DiskBackedGraph graph;
  private final SkyKey key;
  final long id;
  final ReentrantLock lock = new ReentrantLock();
  @Nullable volatile IncrementalInMemoryNodeEntry delegate;
  volatile boolean eligible;
  volatile boolean removed;
  boolean persisted;
  private WeakReference<SkyValue> retainedValue = new WeakReference<>(null);
  private volatile Metadata metadata =
      new Metadata(LifecycleState.NOT_YET_EVALUATING, false, Version.minimal(), Version.minimal());

  private record Metadata(
      LifecycleState state, boolean changed, Version changedVersion, Version evaluatedVersion) {}

  DiskBackedNodeEntry(DiskBackedGraph graph, SkyKey key, long id) {
    this.graph = graph;
    this.key = key;
    this.id = id;
    delegate = new IncrementalInMemoryNodeEntry(key);
  }

  void rememberValue() {
    retainedValue = new WeakReference<>(delegate.getValueMaybeWithMetadata());
  }

  @Nullable
  SkyValue rememberedValue() {
    return retainedValue.get();
  }

  private interface Operation<T> {
    T apply(IncrementalInMemoryNodeEntry entry) throws InterruptedException;
  }

  private <T> T operate(Operation<T> operation, boolean mutation) throws InterruptedException {
    T result;
    lock.lock();
    try {
      if (delegate == null) {
        delegate = graph.load(this);
      }
      if (mutation) {
        persisted = false;
      }
      result = operation.apply(delegate);
    } finally {
      try {
        if (delegate != null) {
          metadata =
              new Metadata(
                  delegate.getLifecycleState(), delegate.isChanged(),
                  delegate.getVersion(), delegate.lastEvaluatedVersion());
          eligible = graph.eligible(delegate);
          graph.accessed(this);
        }
      } finally {
        lock.unlock();
      }
    }
    graph.trim();
    return result;
  }

  private <T> T call(Function<IncrementalInMemoryNodeEntry, T> operation, boolean mutation) {
    try {
      return operate(operation::apply, mutation);
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      throw new IllegalStateException("Unexpected interruption in node operation", e);
    }
  }

  @Override
  public SkyKey getKey() {
    return key;
  }

  @Override
  public boolean keepsEdges() {
    return true;
  }

  @Override
  public boolean isDone() {
    return metadata.state() == LifecycleState.DONE;
  }

  @Override
  public boolean isDirty() {
    return !isDone();
  }

  @Override
  public boolean isChanged() {
    return metadata.changed();
  }

  @Override
  public LifecycleState getLifecycleState() {
    return metadata.state();
  }

  @Override
  public Version getVersion() {
    return metadata.changedVersion();
  }

  @Override
  public Version lastEvaluatedVersion() {
    return metadata.evaluatedVersion();
  }

  @Override
  @Nullable
  public Iterable<SkyKey> lastBuildDepsIfChangePrunable() {
    if (isDone()) {
      return null;
    }
    return call(IncrementalInMemoryNodeEntry::lastBuildDepsIfChangePrunable, false);
  }

  @Override
  public Object getCompressedDirectDepsForDoneEntry() {
    return call(IncrementalInMemoryNodeEntry::getCompressedDirectDepsForDoneEntry, false);
  }

  @Override
  public LifecycleState markChangedIfCheckingDependencies() {
    return call(IncrementalInMemoryNodeEntry::markChangedIfCheckingDependencies, true);
  }

  @Override
  public String toString() {
    return "DiskBackedNodeEntry{key=" + key + ", state=" + metadata.state() + "}";
  }

  @Override
  public SkyValue getValue() {
    return call(e -> e.getValue(), false);
  }

  @Override
  public SkyValue getValueMaybeWithMetadata() {
    return call(e -> e.getValueMaybeWithMetadata(), false);
  }

  @Override
  public SkyValue toValue() {
    return call(e -> e.toValue(), false);
  }

  @Override
  public ErrorInfo getErrorInfo() {
    return call(e -> e.getErrorInfo(), false);
  }

  @Override
  public Set<SkyKey> getInProgressReverseDeps() {
    return call(e -> e.getInProgressReverseDeps(), false);
  }

  @Override
  public Set<SkyKey> setValue(
      SkyValue value, Version graphVersion, @Nullable Version maxTransitiveSourceVersion)
      throws InterruptedException {
    return operate(e -> e.setValue(value, graphVersion, maxTransitiveSourceVersion), true);
  }

  @Override
  public DependencyState addReverseDepAndCheckIfDone(@Nullable SkyKey reverseDep) {
    if (reverseDep == null && isDone()) {
      return DependencyState.DONE;
    }
    return call(e -> e.addReverseDepAndCheckIfDone(reverseDep), true);
  }

  @Override
  public DependencyState checkIfDoneForDirtyReverseDep(SkyKey reverseDep)
      throws InterruptedException {
    return operate(e -> e.checkIfDoneForDirtyReverseDep(reverseDep), true);
  }

  @Override
  public boolean signalDep(Version childVersion, @Nullable SkyKey childForDebugging) {
    return call(e -> e.signalDep(childVersion, childForDebugging), true);
  }

  @Override
  public NodeValueAndRdepsToSignal markClean() throws InterruptedException {
    return operate(e -> e.markClean(), true);
  }

  @Override
  public void forceRebuild() {
    call(
        e -> {
          e.forceRebuild();
          return null;
        },
        true);
  }

  @Override
  public Version getMaxTransitiveSourceVersion() {
    return call(e -> e.getMaxTransitiveSourceVersion(), false);
  }

  @Override
  public void setTemporaryMaxTransitiveSourceVersion(Version maxTransitiveSourceVersion) {
    call(
        e -> {
          e.setTemporaryMaxTransitiveSourceVersion(maxTransitiveSourceVersion);
          return null;
        },
        true);
  }

  @Override
  public List<SkyKey> getNextDirtyDirectDeps() throws InterruptedException {
    return operate(e -> e.getNextDirtyDirectDeps(), true);
  }

  @Override
  public ImmutableSet<SkyKey> getAllDirectDepsForIncompleteNode() throws InterruptedException {
    return operate(e -> e.getAllDirectDepsForIncompleteNode(), false);
  }

  @Override
  public ImmutableSet<SkyKey> getAllRemainingDirtyDirectDeps() throws InterruptedException {
    return operate(e -> e.getAllRemainingDirtyDirectDeps(), true);
  }

  @Override
  public Collection<SkyKey> getAllReverseDepsForNodeBeingDeleted() {
    return call(e -> e.getAllReverseDepsForNodeBeingDeleted(), true);
  }

  @Override
  public void markRebuilding() {
    call(
        e -> {
          e.markRebuilding();
          return null;
        },
        true);
  }

  @Override
  public GroupedDeps getTemporaryDirectDeps() {
    return call(e -> e.getTemporaryDirectDeps(), true);
  }

  @Override
  public boolean noDepsLastBuild() {
    return call(e -> e.noDepsLastBuild(), false);
  }

  @Override
  public void removeUnfinishedDeps(Set<SkyKey> unfinishedDeps) {
    call(
        e -> {
          e.removeUnfinishedDeps(unfinishedDeps);
          return null;
        },
        true);
  }

  @Override
  public void resetEvaluationFromScratch() {
    call(
        e -> {
          e.resetEvaluationFromScratch();
          return null;
        },
        true);
  }

  @Override
  public ImmutableSet<SkyKey> getResetDirectDeps() {
    return call(e -> e.getResetDirectDeps(), false);
  }

  @Override
  public void addSingletonTemporaryDirectDep(SkyKey dep) {
    call(
        e -> {
          e.addSingletonTemporaryDirectDep(dep);
          return null;
        },
        true);
  }

  @Override
  public void addTemporaryDirectDepGroup(List<SkyKey> group) {
    call(
        e -> {
          e.addTemporaryDirectDepGroup(group);
          return null;
        },
        true);
  }

  @Override
  public void addTemporaryDirectDepsInGroups(Set<SkyKey> deps, List<Integer> groupSizes) {
    call(
        e -> {
          e.addTemporaryDirectDepsInGroups(deps, groupSizes);
          return null;
        },
        true);
  }

  @Override
  public boolean isReadyToEvaluate() {
    return call(e -> e.isReadyToEvaluate(), false);
  }

  @Override
  public boolean hasUnsignaledDeps() {
    return call(e -> e.hasUnsignaledDeps(), false);
  }

  @Override
  public Iterable<SkyKey> getDirectDeps() {
    return call(e -> e.getDirectDeps(), false);
  }

  @Override
  public boolean hasAtLeastOneDep() {
    return call(e -> e.hasAtLeastOneDep(), false);
  }

  @Override
  public void removeReverseDep(SkyKey reverseDep) {
    call(
        e -> {
          e.removeReverseDep(reverseDep);
          return null;
        },
        true);
  }

  @Override
  public void removeReverseDepsFromDoneEntryDueToDeletion(Set<SkyKey> deletedKeys) {
    call(
        e -> {
          e.removeReverseDepsFromDoneEntryDueToDeletion(deletedKeys);
          return null;
        },
        true);
  }

  @Override
  public Collection<SkyKey> getReverseDepsForDoneEntry() {
    return call(e -> e.getReverseDepsForDoneEntry(), false);
  }

  @Override
  public MarkedDirtyResult markDirty(DirtyType dirtyType) {
    return call(e -> e.markDirty(dirtyType), true);
  }

  @Override
  public void addExternalDep() {
    call(
        e -> {
          e.addExternalDep();
          return null;
        },
        true);
  }
}
