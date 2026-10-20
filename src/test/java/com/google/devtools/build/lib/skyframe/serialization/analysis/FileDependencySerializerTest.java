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
package com.google.devtools.build.lib.skyframe.serialization.analysis;

import static com.google.common.truth.Truth.assertThat;
import static java.util.concurrent.TimeUnit.SECONDS;
import static org.junit.Assert.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.google.common.collect.ImmutableList;
import com.google.devtools.build.lib.actions.FileValue;
import com.google.devtools.build.lib.compress.CompressionService;
import com.google.devtools.build.lib.compress.CompressionServiceImpl;
import com.google.devtools.build.lib.concurrent.safeexecutor.SafeExecutorOwner;
import com.google.devtools.build.lib.skyframe.AbstractNestedFileOpNodes;
import com.google.devtools.build.lib.skyframe.FileKey;
import com.google.devtools.build.lib.skyframe.serialization.KeyValueWriter;
import com.google.devtools.build.lib.skyframe.serialization.PackedFingerprint;
import com.google.devtools.build.lib.skyframe.serialization.ProfileCollector;
import com.google.devtools.build.lib.skyframe.serialization.WriteStatuses;
import com.google.devtools.build.lib.skyframe.serialization.WriteStatuses.SettableWriteStatus;
import com.google.devtools.build.lib.skyframe.serialization.analysis.InvalidationDataInfoOrFuture.ConstantNodeData;
import com.google.devtools.build.lib.skyframe.serialization.analysis.InvalidationDataInfoOrFuture.FileDataInfoOrFuture;
import com.google.devtools.build.lib.skyframe.serialization.analysis.InvalidationDataInfoOrFuture.FutureFileDataInfo;
import com.google.devtools.build.lib.skyframe.serialization.analysis.InvalidationDataInfoOrFuture.FutureNodeDataInfo;
import com.google.devtools.build.lib.skyframe.serialization.analysis.InvalidationDataInfoOrFuture.NodeDataInfo;
import com.google.devtools.build.lib.skyframe.serialization.analysis.InvalidationDataInfoOrFuture.NodeInvalidationDataInfo;
import com.google.devtools.build.lib.versioning.IndependentFileDependencyProvider;
import com.google.devtools.build.lib.versioning.LongVersionGetter;
import com.google.devtools.build.lib.vfs.DigestHashFunction;
import com.google.devtools.build.lib.vfs.FileSystem;
import com.google.devtools.build.lib.vfs.PathFragment;
import com.google.devtools.build.lib.vfs.Root;
import com.google.devtools.build.lib.vfs.RootedPath;
import com.google.devtools.build.lib.vfs.inmemoryfs.InMemoryFileSystem;
import com.google.devtools.build.skyframe.InMemoryGraph;
import com.google.devtools.build.skyframe.InMemoryNodeEntry;
import com.google.perftools.profiles.ProfileProto.Profile;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ForkJoinPool;
import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;
import org.mockito.Mock;
import org.mockito.junit.MockitoJUnit;
import org.mockito.junit.MockitoRule;

@RunWith(JUnit4.class)
public final class FileDependencySerializerTest {

  @Rule public final MockitoRule mocks = MockitoJUnit.rule();

  private static final int THREAD_COUNT = 10;

  private static final CompressionService COMPRESSION_SERVICE = new CompressionServiceImpl();

  private final ForkJoinPool forkJoinPool = new ForkJoinPool(THREAD_COUNT);
  private final SafeExecutorOwner executor = new SafeExecutorOwner(forkJoinPool);

  @Mock private LongVersionGetter versionGetter;
  @Mock private InMemoryGraph graph;
  @Mock private KeyValueWriter writer;
  @Mock private InMemoryNodeEntry nodeEntry;

  private FileDependencySerializer serializer;
  private Root root;

  @Before
  public void setUp() throws Exception {
    FileSystem fs = new InMemoryFileSystem(DigestHashFunction.SHA256);
    root = Root.fromPath(fs.getPath("/root"));
    root.asPath().createDirectoryAndParents();
    serializer =
        new FileDependencySerializer(
            versionGetter, graph, COMPRESSION_SERVICE, writer, executor, null);
  }

  @Test
  public void missingNodeEntry_incrementsErrorCounter() {
    FileKey key = FileKey.create(RootedPath.toRootedPath(root, PathFragment.create("missing.txt")));
    when(graph.getIfPresent(key)).thenReturn(null);

    FileDataInfoOrFuture result = serializer.registerDependency(key);

    assertThat(result).isInstanceOf(FutureFileDataInfo.class);
    ExecutionException e =
        assertThrows(ExecutionException.class, () -> ((FutureFileDataInfo) result).get());
    assertThat(e).hasCauseThat().isInstanceOf(MissingSkyframeEntryException.class);
    assertThat(serializer.getCounters().nodesWithProcessingErrors.get()).isEqualTo(1);
    assertThat(serializer.getCounters().nodesWaitingForDeps.get()).isEqualTo(0);
  }

  @Test
  public void rootDirectoryDependency_isConstantAndDecrementsCounter() throws Exception {
    FileKey key = FileKey.create(RootedPath.toRootedPath(root, PathFragment.EMPTY_FRAGMENT));

    FileDataInfoOrFuture result = serializer.registerDependency(key);

    assertThat(result).isEqualTo(InvalidationDataInfoOrFuture.ConstantFileData.CONSTANT_FILE);
    assertThat(serializer.getCounters().nodesWaitingForDeps.get()).isEqualTo(0);
    assertThat(serializer.getCounters().nodesWithProcessingErrors.get()).isEqualTo(0);
  }

  @Test
  public void symlinkResolutionFailure_incrementsErrorCounter() throws Exception {
    PathFragment symlinkPathFragment = PathFragment.create("symlink.txt");
    PathFragment targetPathFragment = PathFragment.create("target.txt");
    RootedPath symlinkRootedPath = RootedPath.toRootedPath(root, symlinkPathFragment);
    FileKey symlinkKey = FileKey.create(symlinkRootedPath);

    FileValue symlinkFsv = mock(FileValue.class);
    when(symlinkFsv.isSymlink()).thenReturn(true);
    when(symlinkFsv.getUnresolvedLinkTarget()).thenReturn(targetPathFragment);
    when(symlinkFsv.realRootedPath(symlinkRootedPath)).thenReturn(symlinkRootedPath);
    when(symlinkFsv.exists()).thenReturn(true);
    when(symlinkFsv.isDirectory()).thenReturn(false);
    when(nodeEntry.getValue()).thenReturn(symlinkFsv);
    when(graph.getIfPresent(symlinkKey)).thenReturn(nodeEntry);

    // Symlink resolution calls getVersion on link path.
    when(versionGetter.getFilePathOrSymlinkVersion(symlinkRootedPath.asPath())).thenReturn(2L);

    // Create the failure mode where the symlink target does not exist in graph.
    RootedPath targetRootedPath = RootedPath.toRootedPath(root, targetPathFragment);
    when(graph.getIfPresent(targetRootedPath)).thenReturn(null);

    FileDataInfoOrFuture result = serializer.registerDependency(symlinkKey);

    assertThat(result).isInstanceOf(FutureFileDataInfo.class);
    ExecutionException e =
        assertThrows(ExecutionException.class, () -> ((FutureFileDataInfo) result).get());
    assertThat(e).hasCauseThat().isInstanceOf(MissingSkyframeEntryException.class);
    assertThat(serializer.getCounters().nodesWithProcessingErrors.get()).isEqualTo(1);
    assertThat(serializer.getCounters().nodesWaitingForDeps.get()).isEqualTo(0);
  }

  @Test
  public void externalSymlinkUsesIndependentDependencyEvidenceAndPropagatesFailure()
      throws Exception {
    RootedPath path = RootedPath.toRootedPath(root, PathFragment.create("external-link"));
    FileKey key = FileKey.create(path);
    FileValue value = mock(FileValue.class);
    when(value.isSymlink()).thenReturn(true);
    when(value.getUnresolvedLinkTarget()).thenReturn(PathFragment.create("/outside/file"));
    when(value.realRootedPath(path)).thenReturn(path);
    when(value.exists()).thenReturn(true);
    when(value.isDirectory()).thenReturn(false);
    when(nodeEntry.getValue()).thenReturn(value);
    when(graph.getIfPresent(key)).thenReturn(nodeEntry);
    when(versionGetter.recordExternalSymlinkDependencies(path.asPath()))
        .thenThrow(new java.io.IOException("could not persist link evidence"));
    var result = (FutureFileDataInfo) serializer.registerDependency(key);
    var error = assertThrows(ExecutionException.class, result::get);
    assertThat(error).hasCauseThat().hasMessageThat().contains("could not persist link evidence");
    verify(versionGetter).recordExternalSymlinkDependencies(path.asPath());
    assertThat(serializer.getCounters().nodesWithProcessingErrors.get()).isEqualTo(1);
  }

  @Test
  public void externalSymlinkCanUseIndependentlyValidatedDependencies() throws Exception {
    RootedPath path = RootedPath.toRootedPath(root, PathFragment.create("external-link"));
    FileKey key = FileKey.create(path);
    FileValue value = mock(FileValue.class);
    when(value.isSymlink()).thenReturn(true);
    when(value.getUnresolvedLinkTarget()).thenReturn(PathFragment.create("../outside/file"));
    when(value.realRootedPath(path)).thenReturn(path);
    when(value.exists()).thenReturn(true);
    when(value.isDirectory()).thenReturn(false);
    when(nodeEntry.getValue()).thenReturn(value);
    when(graph.getIfPresent(key)).thenReturn(nodeEntry);
    when(versionGetter.recordExternalSymlinkDependencies(path.asPath())).thenReturn(true);
    when(writer.put(any(), any())).thenReturn(WriteStatuses.immediateWriteStatus(true));
    ((FutureFileDataInfo) serializer.registerDependency(key)).get();
    verify(versionGetter).recordExternalSymlinkDependencies(path.asPath());
    assertThat(serializer.getCounters().nodesWithProcessingErrors.get()).isEqualTo(0);
  }

  @Test
  public void independentManifestObservesDirectoriesAndRootsWithoutLegacyWrites() throws Exception {
    when(versionGetter.usesIndependentInvalidationData()).thenReturn(true);
    serializer =
        new FileDependencySerializer(
            versionGetter, graph, COMPRESSION_SERVICE, writer, executor, null);
    RootedPath path = RootedPath.toRootedPath(root, PathFragment.create("directory"));
    FileKey key = FileKey.create(path);
    FileValue value = mock(FileValue.class);
    when(value.realRootedPath(path)).thenReturn(path);
    when(value.exists()).thenReturn(true);
    when(value.isDirectory()).thenReturn(true);
    when(nodeEntry.getValue()).thenReturn(value);
    when(graph.getIfPresent(key)).thenReturn(nodeEntry);
    ((FutureFileDataInfo) serializer.registerDependency(key)).get();
    verify(versionGetter).getFilePathOrSymlinkVersion(root.asPath());
    verify(versionGetter).getFilePathOrSymlinkVersion(path.asPath());
    verify(writer, never()).put(any(), any());
    assertThat(serializer.getCounters().nodesWithProcessingErrors.get()).isEqualTo(0);
  }

  @Test
  public void independentManifestResolvesRelativeSymlinkWithoutGraphFileState() throws Exception {
    when(versionGetter.usesIndependentInvalidationData()).thenReturn(true);
    serializer =
        new FileDependencySerializer(
            versionGetter, graph, COMPRESSION_SERVICE, writer, executor, null);
    RootedPath path = RootedPath.toRootedPath(root, PathFragment.create("relative-link"));
    FileKey key = FileKey.create(path);
    FileValue value = mock(FileValue.class);
    when(value.isSymlink()).thenReturn(true);
    when(value.getUnresolvedLinkTarget()).thenReturn(PathFragment.create("relative-target"));
    when(value.realRootedPath(path)).thenReturn(path);
    when(value.exists()).thenReturn(true);
    when(value.isDirectory()).thenReturn(false);
    when(nodeEntry.getValue()).thenReturn(value);
    when(graph.getIfPresent(key)).thenReturn(nodeEntry);
    when(versionGetter.recordExternalSymlinkDependencies(path.asPath())).thenReturn(true);
    // No FILE_STATE node is installed for the target. Filesystem evidence covers the chain.
    ((FutureFileDataInfo) serializer.registerDependency(key)).get();
    verify(versionGetter).recordExternalSymlinkDependencies(path.asPath());
    verify(writer, never()).put(any(), any());
    assertThat(serializer.getCounters().nodesWithProcessingErrors.get()).isEqualTo(0);
  }

  @Test
  public void independentParentAndListingReferencesPersistAndChangeTheRootFingerprint()
      throws Exception {
    RootedPath directory = RootedPath.toRootedPath(root, PathFragment.create("dir"));
    RootedPath file = RootedPath.toRootedPath(root, PathFragment.create("dir/file"));
    for (RootedPath path : java.util.List.of(directory, file)) {
      var entry = mock(InMemoryNodeEntry.class);
      var value = mock(FileValue.class);
      when(value.realRootedPath(path)).thenReturn(path);
      when(value.exists()).thenReturn(true);
      when(value.isDirectory()).thenReturn(path.equals(directory));
      when(entry.getValue()).thenReturn(value);
      when(graph.getIfPresent(FileKey.create(path))).thenReturn(entry);
    }
    PackedFingerprint previous = null;
    for (int round = 0; round < 2; round++) {
      int snapshot = round;
      var provider = mock(IndependentFileDependencyProvider.class);
      when(provider.usesIndependentInvalidationData()).thenReturn(true);
      var observedParents =
          new java.util.concurrent.ConcurrentHashMap<String, java.util.List<String>>();
      when(provider.captureFileDependency(
              any(),
              org.mockito.ArgumentMatchers.anyBoolean(),
              org.mockito.ArgumentMatchers.anyList()))
          .thenAnswer(
              call -> {
                com.google.devtools.build.lib.vfs.Path path = call.getArgument(0);
                boolean listing = call.getArgument(1);
                java.util.List<String> parents = call.getArgument(2);
                String name = path.getPathString() + ":" + listing;
                observedParents.put(name, java.util.List.copyOf(parents));
                return (name + parents + (path.equals(file.asPath()) ? snapshot : 0))
                    .getBytes(java.nio.charset.StandardCharsets.UTF_8);
              });
      var persisted = new java.util.concurrent.ConcurrentHashMap<String, byte[]>();
      org.mockito.Mockito.doAnswer(
              call ->
                  PackedFingerprint.getFingerprintForTesting(
                      java.util.HexFormat.of().formatHex((byte[]) call.getArgument(0))))
          .when(writer)
          .fingerprint(any());
      org.mockito.Mockito.doAnswer(
              call -> {
                com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider key =
                    call.getArgument(0);
                persisted.put(
                    java.util.HexFormat.of().formatHex(key.toBytes()), call.getArgument(1));
                return WriteStatuses.immediateWriteStatus(true);
              })
          .when(writer)
          .put(any(), any());
      var selected =
          new FileDependencySerializer(
              provider, graph, COMPRESSION_SERVICE, writer, executor, null);
      var nested =
          (AbstractNestedFileOpNodes)
              AbstractNestedFileOpNodes.from(
                  ImmutableList.of(
                      FileKey.create(file),
                      com.google.devtools.build.lib.skyframe.DirectoryListingKey.create(
                          directory)));
      var rootInfo =
          (NodeInvalidationDataInfo)
              ((FutureNodeDataInfo) selected.registerDependency(nested)).get();
      rootInfo.writeStatus().get();
      assertThat(persisted).hasSize(4); // directory, file, listing, and their composite node
      assertThat(persisted)
          .containsKey(java.util.HexFormat.of().formatHex(rootInfo.cacheKey().toBytes()));
      var directoryInfo =
          (InvalidationDataInfoOrFuture.FileInvalidationDataInfo)
              selected.registerDependency(FileKey.create(directory));
      assertThat(observedParents.get(file.asPath().getPathString() + ":false"))
          .containsExactly(directoryInfo.cacheKey());
      assertThat(observedParents.get(directory.asPath().getPathString() + ":true"))
          .containsExactly(directoryInfo.cacheKey());
      if (previous != null) {
        assertThat(rootInfo.cacheKey()).isNotEqualTo(previous);
      }
      previous = rootInfo.cacheKey();
    }
  }

  @Test
  public void independentLeafEvidenceIsPersistedWithAContentKey() throws Exception {
    var provider = mock(IndependentFileDependencyProvider.class);
    when(provider.usesIndependentInvalidationData()).thenReturn(true);
    RootedPath path = RootedPath.toRootedPath(root, PathFragment.create("file"));
    FileKey key = FileKey.create(path);
    FileValue value = mock(FileValue.class);
    when(value.realRootedPath(path)).thenReturn(path);
    when(value.exists()).thenReturn(true);
    when(nodeEntry.getValue()).thenReturn(value);
    when(graph.getIfPresent(key)).thenReturn(nodeEntry);
    byte[] bytes = {1, 2, 3};
    when(provider.captureFileDependency(path.asPath(), false, java.util.List.of()))
        .thenReturn(bytes);
    var fingerprint = PackedFingerprint.fromBytes(new byte[16]);
    when(writer.fingerprint(bytes)).thenReturn(fingerprint);
    var status = new SettableWriteStatus();
    when(writer.put(any(), any())).thenReturn(status);
    var selected =
        new FileDependencySerializer(provider, graph, COMPRESSION_SERVICE, writer, executor, null);
    var result =
        (InvalidationDataInfoOrFuture.FileInvalidationDataInfo)
            ((FutureFileDataInfo) selected.registerDependency(key)).get();
    String expectedKey =
        "local-input-v1/" + java.util.HexFormat.of().formatHex(fingerprint.toBytes());
    assertThat(result.cacheKey()).isEqualTo(expectedKey);
    verify(writer)
        .put(
            new com.google.devtools.build.lib.skyframe.serialization.StringKey(expectedKey), bytes);
    assertThat(result.writeStatus().isDone()).isFalse();
    status.markSuccess(true);
    assertThat(result.writeStatus().get()).isTrue();
    assertThat(selected.getCounters().nodesUploaded.get()).isEqualTo(1);
    verify(provider).getFilePathOrSymlinkVersion(root.asPath());
  }

  @Test
  public void independentLeafCaptureFailureCannotBecomeAPublicationDependency() throws Exception {
    var provider = mock(IndependentFileDependencyProvider.class);
    when(provider.usesIndependentInvalidationData()).thenReturn(true);
    RootedPath path = RootedPath.toRootedPath(root, PathFragment.create("file"));
    FileKey key = FileKey.create(path);
    FileValue value = mock(FileValue.class);
    when(value.realRootedPath(path)).thenReturn(path);
    when(value.exists()).thenReturn(true);
    when(nodeEntry.getValue()).thenReturn(value);
    when(graph.getIfPresent(key)).thenReturn(nodeEntry);
    when(provider.captureFileDependency(path.asPath(), false, java.util.List.of()))
        .thenThrow(new java.io.IOException("capture failed"));
    var selected =
        new FileDependencySerializer(provider, graph, COMPRESSION_SERVICE, writer, executor, null);
    var error =
        assertThrows(
            ExecutionException.class,
            () -> ((FutureFileDataInfo) selected.registerDependency(key)).get());
    assertThat(error).hasCauseThat().hasCauseThat().hasMessageThat().isEqualTo("capture failed");
    verify(writer, never()).put(any(), any());
    assertThat(selected.getCounters().nodesWaitingForDeps.get()).isEqualTo(0);
    assertThat(selected.getCounters().nodesWithProcessingErrors.get()).isEqualTo(1);
  }

  @Test
  public void registerFileDependency_recordsSamples() throws Exception {
    ProfileCollector profileCollector = new ProfileCollector();
    serializer =
        new FileDependencySerializer(
            versionGetter, graph, COMPRESSION_SERVICE, writer, executor, profileCollector);

    PathFragment filePathFragment = PathFragment.create("file.txt");
    RootedPath rootedPath = RootedPath.toRootedPath(root, filePathFragment);
    FileKey key = FileKey.create(rootedPath);

    FileValue fsv = mock(FileValue.class);
    when(fsv.isSymlink()).thenReturn(false);
    when(fsv.realRootedPath(rootedPath)).thenReturn(rootedPath);
    when(fsv.exists()).thenReturn(true);
    when(fsv.isDirectory()).thenReturn(false);
    when(nodeEntry.getValue()).thenReturn(fsv);
    when(graph.getIfPresent(key)).thenReturn(nodeEntry);

    when(versionGetter.getFilePathOrSymlinkVersion(rootedPath.asPath())).thenReturn(2L);

    SettableWriteStatus writeStatus = new WriteStatuses.SettableWriteStatus();
    when(writer.put(any(), any())).thenReturn(writeStatus);

    FileDataInfoOrFuture result = serializer.registerDependency(key);
    ((FutureFileDataInfo) result).get();

    // Not novel yet, no samples.
    assertThat(profileCollector.toProto().getSampleCount()).isEqualTo(0);

    writeStatus.markSuccess(true); // was novel

    // Samples should be recorded now.
    Profile profile = profileCollector.toProto();
    assertThat(profile.getSampleCount()).isGreaterThan(0);
  }

  @Test
  public void registerNestedNodes_completesSuccessfully() throws Exception {
    PathFragment file1Fragment = PathFragment.create("file1.txt");
    PathFragment file2Fragment = PathFragment.create("file2.txt");
    RootedPath rootedPath1 = RootedPath.toRootedPath(root, file1Fragment);
    RootedPath rootedPath2 = RootedPath.toRootedPath(root, file2Fragment);
    FileKey key1 = FileKey.create(rootedPath1);
    FileKey key2 = FileKey.create(rootedPath2);

    FileValue fsv1 = mock(FileValue.class);
    when(fsv1.isSymlink()).thenReturn(false);
    when(fsv1.realRootedPath(rootedPath1)).thenReturn(rootedPath1);
    when(fsv1.exists()).thenReturn(true);
    when(fsv1.isDirectory()).thenReturn(false);
    when(graph.getIfPresent(key1)).thenReturn(nodeEntry);
    when(nodeEntry.getValue()).thenReturn(fsv1);

    InMemoryNodeEntry nodeEntry2 = mock(InMemoryNodeEntry.class);
    FileValue fsv2 = mock(FileValue.class);
    when(fsv2.isSymlink()).thenReturn(false);
    when(fsv2.realRootedPath(rootedPath2)).thenReturn(rootedPath2);
    when(fsv2.exists()).thenReturn(true);
    when(fsv2.isDirectory()).thenReturn(false);
    when(graph.getIfPresent(key2)).thenReturn(nodeEntry2);
    when(nodeEntry2.getValue()).thenReturn(fsv2);

    when(versionGetter.getFilePathOrSymlinkVersion(rootedPath1.asPath())).thenReturn(1L);
    when(versionGetter.getFilePathOrSymlinkVersion(rootedPath2.asPath())).thenReturn(2L);

    SettableWriteStatus writeStatus = new WriteStatuses.SettableWriteStatus();
    when(writer.put(any(), any())).thenReturn(writeStatus);
    when(writer.fingerprint(any())).thenReturn(new PackedFingerprint(1L, 2L));

    AbstractNestedFileOpNodes nested =
        (AbstractNestedFileOpNodes) AbstractNestedFileOpNodes.from(ImmutableList.of(key1, key2));

    var result = serializer.registerDependency(nested);
    assertThat(result).isInstanceOf(FutureNodeDataInfo.class);

    NodeDataInfo info = ((FutureNodeDataInfo) result).get();
    assertThat(info).isInstanceOf(NodeInvalidationDataInfo.class);
    assertThat(serializer.getCounters().nodesWaitingForDeps.get()).isEqualTo(0);
    assertThat(serializer.getCounters().nodesWithProcessingErrors.get()).isEqualTo(0);
    assertThat(serializer.getCounters().nodesWaitingForUpload.get())
        .isEqualTo(3); // 2 files + 1 node

    writeStatus.markSuccess(true);
    assertThat(serializer.getCounters().nodesWaitingForUpload.get()).isEqualTo(0);
    assertThat(serializer.getCounters().nodesUploaded.get()).isEqualTo(3);
  }

  @Test
  public void registerNestedNodes_childFails_incrementsErrorCounter() throws Exception {
    PathFragment file1Fragment = PathFragment.create("file1.txt");
    PathFragment file2Fragment = PathFragment.create("file2.txt");
    RootedPath rootedPath1 = RootedPath.toRootedPath(root, file1Fragment);
    RootedPath rootedPath2 = RootedPath.toRootedPath(root, file2Fragment);
    FileKey key1 = FileKey.create(rootedPath1);
    FileKey key2 = FileKey.create(rootedPath2);

    FileValue fsv1 = mock(FileValue.class);
    when(fsv1.isSymlink()).thenReturn(false);
    when(fsv1.realRootedPath(rootedPath1)).thenReturn(rootedPath1);
    when(fsv1.exists()).thenReturn(true);
    when(fsv1.isDirectory()).thenReturn(false);
    when(graph.getIfPresent(key1)).thenReturn(nodeEntry);
    when(nodeEntry.getValue()).thenReturn(fsv1);
    when(versionGetter.getFilePathOrSymlinkVersion(rootedPath1.asPath())).thenReturn(1L);

    SettableWriteStatus writeStatus = new WriteStatuses.SettableWriteStatus();
    when(writer.put(any(), any())).thenReturn(writeStatus);

    // key2 is missing from graph
    when(graph.getIfPresent(key2)).thenReturn(null);

    var nested =
        (AbstractNestedFileOpNodes) AbstractNestedFileOpNodes.from(ImmutableList.of(key1, key2));

    var result = serializer.registerDependency(nested);
    assertThat(result).isInstanceOf(FutureNodeDataInfo.class);

    ExecutionException e =
        assertThrows(ExecutionException.class, () -> ((FutureNodeDataInfo) result).get());
    assertThat(e).hasCauseThat().isInstanceOf(MissingSkyframeEntryException.class);
    forkJoinPool.awaitQuiescence(5, SECONDS);
    assertThat(serializer.getCounters().nodesWaitingForDeps.get()).isEqualTo(0);
    assertThat(serializer.getCounters().nodesWithProcessingErrors.get()).isEqualTo(2);
  }

  @Test
  public void registerNestedNodes_constantChildren_resolvesToConstantNode() throws Exception {
    FileKey rootKey1 = FileKey.create(RootedPath.toRootedPath(root, PathFragment.EMPTY_FRAGMENT));
    FileKey rootKey2 = FileKey.create(RootedPath.toRootedPath(root, PathFragment.EMPTY_FRAGMENT));

    var nested =
        (AbstractNestedFileOpNodes)
            AbstractNestedFileOpNodes.from(ImmutableList.of(rootKey1, rootKey2));

    var result = serializer.registerDependency(nested);
    assertThat(result).isInstanceOf(FutureNodeDataInfo.class);

    NodeDataInfo info = ((FutureNodeDataInfo) result).get();
    assertThat(info).isEqualTo(ConstantNodeData.CONSTANT_NODE);
    assertThat(serializer.getCounters().nodesWaitingForDeps.get()).isEqualTo(0);
    assertThat(serializer.getCounters().nodesWithProcessingErrors.get()).isEqualTo(0);
  }

  @Test
  public void registerNestedNodes_unaryChild_unwrapsChildNode() throws Exception {
    PathFragment file1Fragment = PathFragment.create("file1.txt");
    PathFragment file2Fragment = PathFragment.create("file2.txt");
    RootedPath rootedPath1 = RootedPath.toRootedPath(root, file1Fragment);
    RootedPath rootedPath2 = RootedPath.toRootedPath(root, file2Fragment);
    FileKey key1 = FileKey.create(rootedPath1);
    FileKey key2 = FileKey.create(rootedPath2);

    FileValue fsv1 = mock(FileValue.class);
    when(fsv1.isSymlink()).thenReturn(false);
    when(fsv1.realRootedPath(rootedPath1)).thenReturn(rootedPath1);
    when(fsv1.exists()).thenReturn(true);
    when(fsv1.isDirectory()).thenReturn(false);
    when(graph.getIfPresent(key1)).thenReturn(nodeEntry);
    when(nodeEntry.getValue()).thenReturn(fsv1);

    InMemoryNodeEntry nodeEntry2 = mock(InMemoryNodeEntry.class);
    FileValue fsv2 = mock(FileValue.class);
    when(fsv2.isSymlink()).thenReturn(false);
    when(fsv2.realRootedPath(rootedPath2)).thenReturn(rootedPath2);
    when(fsv2.exists()).thenReturn(true);
    when(fsv2.isDirectory()).thenReturn(false);
    when(graph.getIfPresent(key2)).thenReturn(nodeEntry2);
    when(nodeEntry2.getValue()).thenReturn(fsv2);

    when(versionGetter.getFilePathOrSymlinkVersion(rootedPath1.asPath())).thenReturn(1L);
    when(versionGetter.getFilePathOrSymlinkVersion(rootedPath2.asPath())).thenReturn(2L);

    SettableWriteStatus writeStatus = new WriteStatuses.SettableWriteStatus();
    when(writer.put(any(), any())).thenReturn(writeStatus);
    PackedFingerprint childFingerprint = new PackedFingerprint(1L, 2L);
    when(writer.fingerprint(any())).thenReturn(childFingerprint);

    var childNested =
        (AbstractNestedFileOpNodes) AbstractNestedFileOpNodes.from(ImmutableList.of(key1, key2));

    FileKey constantKey =
        FileKey.create(RootedPath.toRootedPath(root, PathFragment.EMPTY_FRAGMENT));
    var parentNested =
        (AbstractNestedFileOpNodes)
            AbstractNestedFileOpNodes.from(ImmutableList.of(childNested, constantKey));

    var parentResult = serializer.registerDependency(parentNested);
    NodeDataInfo parentInfo = ((FutureNodeDataInfo) parentResult).get();

    // parentNested should unwrap to childNested's NodeInvalidationDataInfo
    assertThat(parentInfo).isInstanceOf(NodeInvalidationDataInfo.class);
    assertThat(((NodeInvalidationDataInfo) parentInfo).cacheKey()).isEqualTo(childFingerprint);
    assertThat(serializer.getCounters().nodesWaitingForDeps.get()).isEqualTo(0);
    assertThat(serializer.getCounters().nodesWithProcessingErrors.get()).isEqualTo(0);
  }
}
