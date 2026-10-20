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

java_library(
    name = "local_skycache_module",
    srcs = [
        "LocalAnalysisCacheClient.java",
        "LocalCacheSession.java",
        "LocalInputSnapshot.java",
        "LocalPathCodec.java",
        "LocalSkycacheModule.java",
        "LocalSkycacheOptions.java",
        "LocalSourceArtifactCodec.java",
    ],
    deps = [
        ":concurrent_file_store",
        "//src/main/java/com/google/devtools/build/lib/actions:artifact_owner",
        "//src/main/java/com/google/devtools/build/lib/actions:artifacts",
        "//src/main/java/com/google/devtools/build/lib/analysis:blaze_directories",
        "//src/main/java/com/google/devtools/build/lib/buildtool:build_result",
        "//src/main/java/com/google/devtools/build/lib/buildtool/buildevent",
        "//src/main/java/com/google/devtools/build/lib/cmdline",
        "//src/main/java/com/google/devtools/build/lib/concurrent/safeexecutor:safe_executor",
        "//src/main/java/com/google/devtools/build/lib/events",
        "//src/main/java/com/google/devtools/build/lib/packages",
        "//src/main/java/com/google/devtools/build/lib/runtime:blaze_command_cluster",
        "//src/main/java/com/google/devtools/build/lib/skyframe:package_value",
        "//src/main/java/com/google/devtools/build/lib/skyframe:rule_configured_target_value",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:fingerprint_value_store",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:key_bytes_provider",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:serialization_module",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:serialization_registry_setup_helpers",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_statuses",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/analysis:client_id",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/analysis:lookup_result",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/analysis:options",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/analysis:remote_analysis_cache_client",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/analysis:remote_analysis_cache_mode",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/analysis:remote_analysis_caching_services_supplier",
        "//src/main/java/com/google/devtools/build/lib/versioning:long_version_getter",
        "//src/main/java/com/google/devtools/build/lib/vfs",
        "//src/main/java/com/google/devtools/build/lib/vfs:pathfragment",
        "//src/main/java/com/google/devtools/common/options",
        "//third_party/java/guava:collect",
        "//third_party/java/guava:concurrent",
        "//third_party/java/guava:eventbus",
        "//third_party/java/guava:primitives",
        "//third_party/java/jsr305_annotations",
        "//third_party/java/protobuf:protobuf_lite",
    ],
)
