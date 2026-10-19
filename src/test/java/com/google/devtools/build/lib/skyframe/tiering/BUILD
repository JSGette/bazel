load("@rules_java//java:defs.bzl", "java_test")

package(default_applicable_licenses = ["//:license"])

java_test(
    name = "IdleValueTierTest",
    size = "small",
    srcs = ["IdleValueTierTest.java"],
    test_class = "com.google.devtools.build.lib.skyframe.tiering.IdleValueTierTest",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe:glob_value",
        "//src/main/java/com/google/devtools/build/lib/skyframe:globs_value",
        "//src/main/java/com/google/devtools/build/lib/skyframe/tiering:module",
        "//src/main/java/com/google/devtools/build/lib/skyframe/tiering:tier",
        "//src/main/java/com/google/devtools/build/lib/vfs:pathfragment",
        "//src/main/java/com/google/devtools/build/skyframe",
        "//src/main/java/com/google/devtools/build/skyframe:skyframe-objects",
        "//src/main/java/com/google/devtools/build/skyframe:version",
        "//src/main/java/com/google/devtools/common/options",
        "//third_party:truth",
        "//third_party/java/guava:collect",
        "//third_party/java/junit",
    ],
)

filegroup(
    name = "srcs",
    testonly = False,
    srcs = glob(["*"]),
    visibility = ["//src/test/java/com/google/devtools/build/lib/skyframe:__pkg__"],
)

java_test(
    name = "SkystoreGraphTest",
    size = "medium",
    srcs = ["SkystoreGraphTest.java"],
    data = ["//tools/skystore"],
    test_class = "com.google.devtools.build.lib.skyframe.tiering.SkystoreGraphTest",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe:glob_value",
        "//src/main/java/com/google/devtools/build/lib/skyframe:globs_value",
        "//src/main/java/com/google/devtools/build/lib/skyframe/tiering:module",
        "//src/main/java/com/google/devtools/build/lib/skyframe/tiering:tier",
        "//src/main/java/com/google/devtools/build/lib/vfs:pathfragment",
        "//src/main/java/com/google/devtools/build/skyframe",
        "//src/main/java/com/google/devtools/build/skyframe:skyframe-objects",
        "//src/main/java/com/google/devtools/build/skyframe:version",
        "//src/main/java/com/google/devtools/common/options",
        "//third_party:truth",
        "//third_party/java/guava:collect",
        "//third_party/java/junit",
        "@rules_java//java/runfiles",
    ],
)
