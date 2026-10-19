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

java_test(
    name = "LocalGenruleValueCodecTest",
    size = "medium",
    srcs = ["LocalGenruleValueCodecTest.java"],
    data = ["//tools/skystore"],
    test_class = "com.google.devtools.build.lib.skyframe.tiering.LocalGenruleValueCodecTest",
    deps = [
        "//src/main/java/com/google/devtools/build/lib/actions",
        "//src/main/java/com/google/devtools/build/lib/actions:artifacts",
        "//src/main/java/com/google/devtools/build/lib/analysis:analysis_core",
        "//src/main/java/com/google/devtools/build/lib/analysis:blaze_directories",
        "//src/main/java/com/google/devtools/build/lib/analysis:build_view",
        "//src/main/java/com/google/devtools/build/lib/analysis:configured_target",
        "//src/main/java/com/google/devtools/build/lib/collect/nestedset",
        "//src/main/java/com/google/devtools/build/lib/compress:compression_service_impl",
        "//src/main/java/com/google/devtools/build/lib/concurrent/safeexecutor:safe_executor",
        "//src/main/java/com/google/devtools/build/lib/packages",
        "//src/main/java/com/google/devtools/build/lib/pkgcache:package_path_codec_dependencies",
        "//src/main/java/com/google/devtools/build/lib/rules/genrule",
        "//src/main/java/com/google/devtools/build/lib/skyframe:configured_target_key",
        "//src/main/java/com/google/devtools/build/lib/skyframe:rule_configured_target_value",
        "//src/main/java/com/google/devtools/build/lib/skyframe:skyframe_core",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:fingerprint_value_store",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:serialization_registry_setup_helpers",
        "//src/main/java/com/google/devtools/build/lib/skyframe/tiering:local_genrule_codec",
        "//src/main/java/com/google/devtools/build/lib/skyframe/tiering:module",
        "//src/main/java/com/google/devtools/build/lib/vfs",
        "//src/main/java/com/google/devtools/build/skyframe",
        "//src/main/java/com/google/devtools/build/skyframe:skyframe-objects",
        "//src/test/java/com/google/devtools/build/lib/analysis/util",
        "//src/test/java/com/google/devtools/build/lib/testutil",
        "//third_party/java/guava:collect",
        "//third_party/java/junit",
        "//third_party/java/truth",
        "@rules_java//java/runfiles",
    ],
)
