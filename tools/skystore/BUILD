load("@rules_java//java:defs.bzl", "java_binary", "java_test")
load("@rules_rs//rs:rust_binary.bzl", "rust_binary")
load("@rules_rs//rs:rust_library.bzl", "rust_library")
load("@rules_rs//rs:rust_test.bzl", "rust_test")
load("@rules_rust//rust:defs.bzl", "rust_clippy_test", "rustfmt_test")

package(
    default_applicable_licenses = ["//:license"],
    default_visibility = ["//visibility:private"],
)

rust_library(
    name = "skystore_lib",
    srcs = ["src/lib.rs"],
    crate_name = "skystore",
    edition = "2021",
)

rust_binary(
    name = "skystore",
    srcs = ["src/main.rs"],
    edition = "2021",
    visibility = ["//src/test/java/com/google/devtools/build/lib/skyframe/tiering:__pkg__"],
    deps = [":skystore_lib"],
)

rust_test(
    name = "skystore_test",
    size = "small",
    crate = ":skystore_lib",
)

rust_clippy_test(
    name = "clippy_test",
    size = "small",
    targets = [
        ":skystore",
        ":skystore_lib",
        ":skystore_test",
    ],
)

rustfmt_test(
    name = "rustfmt_test",
    size = "small",
    targets = [
        ":skystore",
        ":skystore_lib",
    ],
)

alias(
    name = "client",
    actual = "//src/main/java/com/google/devtools/build/skystore:client",
)

filegroup(
    name = "srcs",
    srcs = glob(
        ["**"],
        exclude = [
            "target/**",
            "__pycache__/**",
        ],
    ),
    visibility = ["//tools:__pkg__"],
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

java_test(
    name = "interop_test",
    size = "small",
    srcs = ["java/com/google/devtools/build/skystore/InteropTest.java"],
    args = ["$(rootpath :skystore)"],
    data = [":skystore"],
    main_class = "com.google.devtools.build.skystore.InteropTest",
    tags = ["no_windows"],
    use_testrunner = False,
    deps = [":client"],
)
