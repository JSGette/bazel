load("@rules_java//java:defs.bzl", "java_test")

package(
    default_applicable_licenses = ["//:license"],
    default_visibility = ["//src:__subpackages__"],
)

filegroup(
    name = "srcs",
    srcs = glob(["**"]),
    visibility = ["//src:__subpackages__"],
)

java_test(
    name = "PackedFingerprintValueStoreTest",
    size = "small",
    srcs = ["PackedFingerprintValueStoreTest.java"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_stores",
        "//third_party:junit4",
        "//third_party:truth",
    ],
)

java_test(
    name = "LayeredFingerprintValueStoreTest",
    size = "small",
    srcs = ["LayeredFingerprintValueStoreTest.java"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_statuses",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_stores",
        "//third_party:guava",
        "//third_party:junit4",
        "//third_party:truth",
    ],
)
