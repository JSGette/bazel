load("@rules_java//java:defs.bzl", "java_library")

package(
    default_applicable_licenses = ["//:license"],
    default_visibility = ["//src:__subpackages__"],
)

filegroup(
    name = "srcs",
    srcs = glob(["**"]),
    visibility = ["//src:__subpackages__"],
)

java_library(
    name = "local_stores",
    srcs = [
        "DiskFingerprintValueStore.java",
        "LayeredFingerprintValueStore.java",
    ],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_statuses",
        "//third_party:caffeine",
        "//third_party:flogger",
        "//third_party:guava",
    ],
)
