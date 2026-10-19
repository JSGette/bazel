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
    name = "tier",
    srcs = [
        "GlobValueCodec.java",
        "IdleValueTier.java",
    ],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe:glob_value",
        "//src/main/java/com/google/devtools/build/lib/skyframe:globs_value",
        "//src/main/java/com/google/devtools/build/lib/vfs:pathfragment",
        "//src/main/java/com/google/devtools/build/skyframe",
        "//src/main/java/com/google/devtools/build/skyframe:skyframe-objects",
        "//third_party/java/guava:collect",
    ],
)

java_library(
    name = "module",
    srcs = [
        "SkyframeTieringModule.java",
        "SkystoreProcess.java",
    ],
    deps = [
        ":tier",
        "//src/main/java/com/google/devtools/build/lib/events",
        "//src/main/java/com/google/devtools/build/lib/runtime:blaze_command_cluster",
        "//src/main/java/com/google/devtools/build/lib/skyframe:skyframe_core",
        "//src/main/java/com/google/devtools/build/lib/vfs",
        "//src/main/java/com/google/devtools/build/skyframe",
        "//src/main/java/com/google/devtools/build/skystore:client",
        "//src/main/java/com/google/devtools/common/options",
    ],
)
