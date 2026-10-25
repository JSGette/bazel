load("@rules_java//java:defs.bzl", "java_library")

package(
    default_applicable_licenses = ["//:license"],
    default_visibility = ["//src:__subpackages__"],
)

filegroup(name = "srcs", srcs = glob(["*"]))

java_library(
    name = "protocol",
    srcs = ["SkyCacheProtocol.java"],
    deps = [
        "//src/main/protobuf:skycache_java_proto",
        "@remoteapis//:build_bazel_remote_execution_v2_remote_execution_java_proto",
        "//third_party/java/protobuf",
    ],
)

java_library(
    name = "transport",
    srcs = ["SkyCacheTransport.java"],
    deps = [
        ":protocol",
        "//src/main/java/com/google/devtools/build/lib/remote/util:tracing_metadata_utils",
        "//src/main/protobuf:skycache_java_proto",
        "//src/main/protobuf:skycache_java_grpc",
        "//third_party/google/bytestream:bytestream_grpc",
        "//third_party/google/bytestream:bytestream_java_proto",
        "//third_party/java/grpc:core",
        "//third_party/java/grpc:context",
        "//third_party/java/grpc:netty",
        "//third_party/java/grpc:stub",
        "//third_party/java/protobuf",
        "@remoteapis//:build_bazel_remote_execution_v2_remote_execution_java_grpc",
        "@remoteapis//:build_bazel_remote_execution_v2_remote_execution_java_proto",
        "//third_party/google/rpc:status_java_proto",
    ],
)
