load("@rules_java//java:defs.bzl", "java_binary", "java_test")

package(default_applicable_licenses = ["//:license"])

java_test(
    name = "RemoteChunkCacheTest",
    size = "small",
    srcs = ["RemoteChunkCacheTest.java"],
    tags = ["no_windows"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/remote:protocol",
        "//third_party/java/junit",
        "//third_party/java/truth",
        "@remoteapis//:build_bazel_remote_execution_v2_remote_execution_java_proto",
    ],
)

java_test(
    name = "RemoteSkyCacheTest",
    srcs = ["RemoteSkyCacheTest.java"],
    size = "small",
    tags = ["no_windows"],
    deps = [
        "//src/main/java/com/google/devtools/common/options",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:packed_store",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/remote:protocol",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/remote:transport",
        "//src/main/protobuf:skycache_java_proto",
        "//src/main/protobuf:skycache_java_grpc",
        "//third_party/google/bytestream:bytestream_grpc",
        "//third_party/google/bytestream:bytestream_java_proto",
        "//third_party/google/rpc:status_java_proto",
        "//third_party/java/grpc:core",
        "//third_party/java/grpc:netty",
        "//third_party/java/grpc:stub",
        "//third_party/java/guava:concurrent",
        "//third_party/java/protobuf",
        "//third_party/java/junit",
        "//third_party/java/truth",
        "@remoteapis//:build_bazel_remote_execution_v2_remote_execution_java_grpc",
        "@remoteapis//:build_bazel_remote_execution_v2_remote_execution_java_proto",
    ],
)

java_test(
    name = "AnalysisCacheIdleRetentionTaskTest",
    size = "small",
    srcs = ["AnalysisCacheIdleRetentionTaskTest.java"],
    tags = ["no_windows"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/server:idle_task",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//src/main/java/com/google/devtools/common/options",
        "//third_party/java/junit",
        "//third_party/java/truth",
    ],
)

java_test(
    name = "LocalPathCodecTest",
    size = "small",
    srcs = ["LocalPathCodecTest.java"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//src/main/java/com/google/devtools/build/lib/vfs",
        "//src/main/java/com/google/devtools/build/lib/vfs/inmemoryfs",
        "//third_party/java/junit",
        "//third_party/java/protobuf:protobuf_lite",
        "//third_party/java/truth",
    ],
)

filegroup(
    name = "srcs",
    srcs = glob(["*"]),
    visibility = ["//src:__subpackages__"],
)

java_test(
    name = "ConcurrentFileFingerprintValueStoreTest",
    size = "medium",
    srcs = ["ConcurrentFileFingerprintValueStoreTest.java"],
    tags = ["no_windows"],
    test_class = "com.google.devtools.build.lib.skyframe.serialization.local.ConcurrentFileFingerprintValueStoreTest",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:fingerprint_value_store",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:concurrent_file_store",
        "//third_party/java/guava:concurrent",
        "//third_party/java/junit",
        "//third_party/java/truth",
    ],
)

java_test(
    name = "PackedFingerprintValueStoreTest",
    size = "medium",
    srcs = ["PackedFingerprintValueStoreTest.java"],
    tags = ["no_windows"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:fingerprint_value_store",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:packed_store",
        "//third_party/java/guava:concurrent",
        "//third_party/java/junit",
        "//third_party/java/truth",
    ],
)

java_binary(
    name = "ParallelPackedStoreBenchmark",
    testonly = True,
    srcs = ["ParallelPackedStoreBenchmark.java"],
    main_class = "com.google.devtools.build.lib.skyframe.serialization.local.ParallelPackedStoreBenchmark",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:packed_store",
        "//third_party/java/guava:concurrent",
    ],
)

java_binary(
    name = "ConcurrentFileStoreBenchmark",
    testonly = True,
    srcs = ["ConcurrentFileStoreBenchmark.java"],
    main_class = "com.google.devtools.build.lib.skyframe.serialization.local.ConcurrentFileStoreBenchmark",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:fingerprint_value_store",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:concurrent_file_store",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:packed_store",
        "//third_party/java/guava:concurrent",
    ],
)

java_test(
    name = "LocalRunfilesCodecTest",
    size = "small",
    srcs = ["LocalRunfilesCodecTest.java"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/actions:action_lookup_data",
        "//src/main/java/com/google/devtools/build/lib/actions:artifacts",
        "//src/main/java/com/google/devtools/build/lib/analysis:runfiles",
        "//src/main/java/com/google/devtools/build/lib/analysis:symlink_entry",
        "//src/main/java/com/google/devtools/build/lib/collect/nestedset",
        "//src/main/java/com/google/devtools/build/lib/compress:compression_service",
        "//src/main/java/com/google/devtools/build/lib/compress:compression_service_impl",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//src/main/java/com/google/devtools/build/lib/vfs",
        "//src/main/java/com/google/devtools/build/lib/vfs:pathfragment",
        "//src/main/java/com/google/devtools/build/lib/vfs/inmemoryfs",
        "//src/test/java/com/google/devtools/build/lib/actions/util",
        "//third_party/java/guava:collect",
        "//third_party/java/guava:concurrent",
        "//third_party/java/guava:testing",
        "//third_party/java/junit",
        "//third_party/java/protobuf:protobuf_lite",
        "//third_party/java/truth",
    ],
)

java_binary(
    name = "LocalCandidateValidationBenchmark",
    testonly = True,
    srcs = ["LocalCandidateValidationBenchmark.java"],
    main_class = "com.google.devtools.build.lib.skyframe.serialization.local.LocalCandidateValidationBenchmark",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//third_party/java/guava:concurrent",
    ],
)

java_test(
    name = "LocalCacheSessionTest",
    size = "medium",
    srcs = ["LocalCacheSessionTest.java"],
    tags = ["no_windows"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:fingerprint_value_store",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:packed_store",
        "//src/main/java/com/google/devtools/build/lib/util:bucket",
        "//third_party/java/guava:concurrent",
        "//third_party/java/junit",
        "//third_party/java/truth",
    ],
)

java_binary(
    name = "LocalInputHashingBenchmark",
    testonly = True,
    srcs = ["LocalInputHashingBenchmark.java"],
    jvm_flags = [
        "-Xms256m",
        "-Xmx1g",
    ],
    main_class = "com.google.devtools.build.lib.skyframe.serialization.local.LocalInputHashingBenchmark",
    deps = ["//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module"],
)

java_binary(
    name = "LocalWitnessListBenchmark",
    testonly = True,
    srcs = ["LocalWitnessListBenchmark.java"],
    jvm_flags = [
        "-Xms256m",
        "-Xmx1g",
    ],
    main_class = "com.google.devtools.build.lib.skyframe.serialization.local.LocalWitnessListBenchmark",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//third_party/java/guava:concurrent",
    ],
)

java_binary(
    name = "LocalInheritedValidationBenchmark",
    testonly = True,
    srcs = ["LocalInheritedValidationBenchmark.java"],
    jvm_flags = [
        "-Xms256m",
        "-Xmx1g",
    ],
    main_class = "com.google.devtools.build.lib.skyframe.serialization.local.LocalInheritedValidationBenchmark",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//third_party/java/guava:concurrent",
    ],
)

java_binary(
    name = "LocalAdmissionBenchmark",
    testonly = True,
    srcs = ["LocalAdmissionBenchmark.java"],
    jvm_flags = [
        "-Xms256m",
        "-Xmx1g",
    ],
    main_class = "com.google.devtools.build.lib.skyframe.serialization.local.LocalAdmissionBenchmark",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//third_party/java/guava:concurrent",
    ],
)

java_test(
    name = "LocalDependencyValidatorTest",
    size = "medium",
    srcs = ["LocalDependencyValidatorTest.java"],
    tags = ["no_windows"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/compress:compression_service",
        "//src/main/java/com/google/devtools/build/lib/compress:compression_service_impl",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:fingerprint_value_store",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/analysis:lookup_result",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/analysis:remote_analysis_cache_client",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//src/main/java/com/google/devtools/build/lib/util:bucket",
        "//third_party/java/guava:concurrent",
        "//third_party/java/junit",
        "//third_party/java/protobuf:protobuf_lite",
        "//third_party/java/truth",
    ],
)

java_binary(
    name = "ParallelDependencyValidationBenchmark",
    testonly = True,
    srcs = [
        "ParallelDependencyValidationBenchmark.java",
        "SerialDependencyValidatorBenchmarkBaseline.java",
    ],
    main_class = "com.google.devtools.build.lib.skyframe.serialization.local.ParallelDependencyValidationBenchmark",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/compress:compression_service",
        "//src/main/java/com/google/devtools/build/lib/compress:compression_service_impl",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:fingerprint_value_store",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:packed_store",
        "//third_party/java/guava:concurrent",
        "//third_party/java/protobuf:protobuf_lite",
    ],
)

java_binary(
    name = "LocalDependencyValidationBenchmark",
    testonly = True,
    srcs = ["LocalDependencyValidationBenchmark.java"],
    main_class = "com.google.devtools.build.lib.skyframe.serialization.local.LocalDependencyValidationBenchmark",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/compress:compression_service",
        "//src/main/java/com/google/devtools/build/lib/compress:compression_service_impl",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:packed_store",
    ],
)

java_test(
    name = "LocalRepositoryMappingCodecTest",
    size = "small",
    srcs = ["LocalRepositoryMappingCodecTest.java"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/cmdline",
        "//src/main/java/com/google/devtools/build/lib/compress:compression_service",
        "//src/main/java/com/google/devtools/build/lib/compress:compression_service_impl",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//third_party/java/guava:collect",
        "//third_party/java/guava:concurrent",
        "//third_party/java/junit",
        "//third_party/java/protobuf:protobuf_lite",
        "//third_party/java/truth",
    ],
)

java_binary(
    name = "LocalRepositoryMappingBenchmark",
    testonly = True,
    srcs = ["LocalRepositoryMappingBenchmark.java"],
    jvm_flags = ["-Xmx1g"],
    main_class = "com.google.devtools.build.lib.skyframe.serialization.local.LocalRepositoryMappingBenchmark",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/cmdline",
        "//src/main/java/com/google/devtools/build/lib/compress:compression_service",
        "//src/main/java/com/google/devtools/build/lib/compress:compression_service_impl",
        "//src/main/java/com/google/devtools/build/lib/concurrent/safeexecutor:safe_executor",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//third_party/java/guava:collect",
        "//third_party/java/guava:concurrent",
        "//third_party/java/protobuf:protobuf_lite",
    ],
)

java_binary(
    name = "LocalAdmissionValidationBenchmark",
    testonly = True,
    srcs = ["LocalAdmissionValidationBenchmark.java"],
    main_class = "com.google.devtools.build.lib.skyframe.serialization.local.LocalAdmissionValidationBenchmark",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/compress:compression_service_impl",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
    ],
)

java_test(
    name = "LocalCacheLeaseManagerTest",
    size = "medium",
    srcs = ["LocalCacheLeaseManagerTest.java"],
    tags = ["no_windows"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//third_party/java/junit",
        "//third_party/java/truth",
    ],
)

java_test(
    name = "LocalCacheGarbageCollectorTest",
    size = "medium",
    srcs = ["LocalCacheGarbageCollectorTest.java"],
    tags = ["no_windows"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//third_party/java/guava:concurrent",
        "//third_party/java/junit",
        "//third_party/java/truth",
    ],
)

java_test(
    name = "LocalCacheGarbageCollectorIdleTaskTest",
    size = "small",
    srcs = ["LocalCacheGarbageCollectorIdleTaskTest.java"],
    tags = ["no_windows"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/server:idle_task",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//src/main/java/com/google/devtools/common/options",
        "//third_party/java/junit",
        "//third_party/java/truth",
    ],
)

java_binary(
    name = "LocalCacheCollectionBenchmark",
    testonly = True,
    srcs = ["LocalCacheCollectionBenchmark.java"],
    main_class = "com.google.devtools.build.lib.skyframe.serialization.local.LocalCacheCollectionBenchmark",
    deps = ["//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module"],
)

java_binary(
    name = "LocalPublicationValidationBenchmark",
    testonly = True,
    srcs = ["LocalPublicationValidationBenchmark.java"],
    jvm_flags = [
        "-Xms256m",
        "-Xmx1g",
    ],
    main_class = "com.google.devtools.build.lib.skyframe.serialization.local.LocalPublicationValidationBenchmark",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//third_party/java/guava:concurrent",
    ],
)

java_binary(
    name = "LocalInputAliasBenchmark",
    testonly = True,
    srcs = ["LocalInputAliasBenchmark.java"],
    jvm_flags = [
        "-Xms256m",
        "-Xmx1g",
    ],
    main_class = "com.google.devtools.build.lib.skyframe.serialization.local.LocalInputAliasBenchmark",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//third_party/java/guava:concurrent",
    ],
)

java_test(
    name = "LocalContentHasherTest",
    size = "small",
    srcs = ["LocalContentHasherTest.java"],
    tags = ["no_windows"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//third_party/java/junit",
        "//third_party/java/truth",
    ],
)

java_test(
    name = "LocalExecutionValuesTest",
    size = "small",
    srcs = ["LocalExecutionValuesTest.java"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/actions:artifacts",
        "//src/main/java/com/google/devtools/build/lib/actions:file_metadata",
        "//src/main/java/com/google/devtools/build/lib/collect/nestedset",
        "//src/main/java/com/google/devtools/build/lib/skyframe:action_execution_value",
        "//src/main/java/com/google/devtools/build/lib/skyframe:tree_artifact_value",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//src/main/java/com/google/devtools/build/lib/vfs:pathfragment",
        "//src/test/java/com/google/devtools/build/lib/actions/util",
        "//src/test/java/com/google/devtools/build/lib/testutil",
        "//third_party/java/guava:collect",
        "//third_party/java/junit",
        "//third_party/java/truth",
    ],
)

java_test(
    name = "LocalWitnessTableTest",
    size = "medium",
    srcs = ["LocalWitnessTableTest.java"],
    tags = ["no_windows"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//third_party/java/junit",
        "//third_party/java/truth",
    ],
)

java_test(
    name = "SegmentedFingerprintValueStoreTest",
    size = "medium",
    srcs = ["SegmentedFingerprintValueStoreTest.java"],
    tags = ["no_windows"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:packed_store",
        "//third_party/java/guava:concurrent",
        "//third_party/java/junit",
        "//third_party/java/truth",
    ],
)
