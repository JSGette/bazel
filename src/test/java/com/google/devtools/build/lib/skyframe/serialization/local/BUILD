load("@rules_java//java:defs.bzl", "java_binary", "java_test")

package(default_applicable_licenses = ["//:license"])

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
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
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
