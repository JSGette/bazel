load("@rules_java//java:defs.bzl", "java_binary", "java_library", "java_test")

package(
    default_applicable_licenses = ["//:license"],
    default_visibility = ["//visibility:private"],
)

java_library(
    name = "client",
    srcs = ["java/com/google/devtools/build/skystore/SkystoreClient.java"],
)

java_binary(
    name = "ipc_benchmark",
    srcs = ["java/com/google/devtools/build/skystore/IpcBenchmark.java"],
    jvm_flags = [
        "-Xms256m",
        "-Xmx512m",
    ],
    main_class = "com.google.devtools.build.skystore.IpcBenchmark",
    deps = [":client"],
)

java_test(
    name = "SkystoreClientTest",
    size = "small",
    srcs = ["java/com/google/devtools/build/skystore/SkystoreClientTest.java"],
    tags = ["no_windows"],
    test_class = "com.google.devtools.build.skystore.SkystoreClientTest",
    deps = [
        ":client",
        "//third_party/java/junit",
    ],
)

java_binary(
    name = "interop_test",
    testonly = True,
    srcs = ["java/com/google/devtools/build/skystore/InteropTest.java"],
    main_class = "com.google.devtools.build.skystore.InteropTest",
    deps = [":client"],
)
