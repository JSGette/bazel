load("@rules_java//java:defs.bzl", "java_binary", "java_test")

package(default_applicable_licenses = ["//:license"])

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

java_binary(
    name = "ConcurrentFileStoreBenchmark",
    testonly = True,
    srcs = ["ConcurrentFileStoreBenchmark.java"],
    main_class = "com.google.devtools.build.lib.skyframe.serialization.local.ConcurrentFileStoreBenchmark",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:concurrent_file_store",
        "//third_party/java/guava:concurrent",
    ],
)
