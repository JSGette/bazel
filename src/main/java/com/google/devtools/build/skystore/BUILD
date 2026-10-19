load("@rules_java//java:defs.bzl", "java_library")

package(default_applicable_licenses = ["//:license"])

filegroup(
    name = "srcs",
    srcs = glob(["*"]),
    visibility = ["//src:__pkg__"],
)

java_library(
    name = "client",
    srcs = ["SkystoreClient.java"],
    visibility = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/tiering:__pkg__",
        "//tools/skystore:__pkg__",
    ],
)
