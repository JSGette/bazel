load("@rules_java//java:defs.bzl", "java_test")

package(
    default_applicable_licenses = ["//:license"],
    default_testonly = 1,
    default_visibility = ["//src/tools/remote:__pkg__"],
)

[java_test(
    name = name,
    srcs = [name + ".java"],
    size = "small",
    tags = ["no_windows"],
    deps = [
        "//src/tools/remote/src/main/java/com/google/devtools/build/remote/worker:worker",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/remote:protocol",
        "//src/main/java/com/google/devtools/build/lib/remote/common:cache_not_found_exception",
        "//src/main/protobuf:skycache_java_proto",
        "//src/main/protobuf:skycache_java_grpc",
        "@remoteapis//:build_bazel_remote_execution_v2_remote_execution_java_proto",
        "//third_party/java/grpc:core",
        "//third_party/java/grpc:stub",
        "//third_party/java/junit",
        "//third_party/java/truth",
        "//third_party/java/protobuf",
    ],
) for name in ["SkyCacheCatalogStoreTest", "SkyCacheServerTest"]]

filegroup(
    name = "srcs",
    testonly = 0,
    srcs = glob(["**"]) + [
        "//src/tools/remote/src/test/java/com/google/devtools/build/remote/worker/http:srcs",
    ],
    visibility = ["//src/tools/remote:__pkg__"],
)
