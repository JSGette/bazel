load("@rules_java//java:defs.bzl", "java_test")

package(default_applicable_licenses = ["//:license"])

filegroup(
    name = "srcs",
    srcs = glob(["*"]),
    visibility = ["//src:__subpackages__"],
)

java_test(
    name = "SkyCacheTransportTest",
    srcs = ["SkyCacheTransportTest.java"],
    size = "small",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/remote/util:tracing_metadata_utils",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/remote:protocol",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/remote:transport",
        "//src/main/protobuf:skycache_java_proto",
        "//src/main/protobuf:skycache_java_grpc",
        "//third_party/google/bytestream:bytestream_grpc",
        "//third_party/google/bytestream:bytestream_java_proto",
        "//third_party/google/rpc:status_java_proto",
        "//third_party/java/grpc:core",
        "//third_party/java/grpc:context",
        "//third_party/java/grpc:netty",
        "//third_party/java/grpc:stub",
        "//third_party/java/protobuf",
        "//third_party/java/junit",
        "//third_party/java/truth",
        "@remoteapis//:build_bazel_remote_execution_v2_remote_execution_java_grpc",
        "@remoteapis//:build_bazel_remote_execution_v2_remote_execution_java_proto",
    ],
)
