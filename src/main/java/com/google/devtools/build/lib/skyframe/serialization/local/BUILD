load("@rules_java//java:defs.bzl", "java_library")

package(
    default_applicable_licenses = ["//:license"],
    default_visibility = ["//src:__subpackages__"],
)

filegroup(
    name = "srcs",
    srcs = glob(["*"]),
)

java_library(
    name = "concurrent_file_store",
    srcs = ["ConcurrentFileFingerprintValueStore.java"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:fingerprint_value_store",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_statuses",
        "//third_party/java/guava:concurrent",
    ],
)
