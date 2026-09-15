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
        "LayeredFingerprintValueStore.java",
        "PackedFingerprintValueStore.java",
    ],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_statuses",
        "//third_party:caffeine",
        "//third_party:flogger",
        "//third_party:guava",
    ],
)

java_library(
    name = "local_analysis_caching_options",
    srcs = ["LocalAnalysisCachingOptions.java"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/util",
        "//src/main/java/com/google/devtools/build/lib/vfs:pathfragment",
        "//src/main/java/com/google/devtools/common/options",
    ],
)

java_library(
    name = "local_skycache_module",
    srcs = [
        "LocalAnalysisCachingServicesSupplier.java",
        "LocalSkycacheModule.java",
        "MinimalVersionGetter.java",
    ],
    deps = [
        ":local_analysis_caching_options",
        ":local_stores",
        "//src/main/java/com/google/devtools/build/lib:runtime",
        "//src/main/java/com/google/devtools/build/lib/concurrent:thread_safety",
        "//src/main/java/com/google/devtools/build/lib/server:idle_task",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:serialization_module",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/analysis:client_id",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/analysis:options",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/analysis:remote_analysis_caching_services_supplier",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/analysis:remote_analysis_json_log_writer",
        "//src/main/java/com/google/devtools/build/lib/util",
        "//src/main/java/com/google/devtools/build/lib/util:abrupt_exit_exception",
        "//src/main/java/com/google/devtools/build/lib/util:detailed_exit_code",
        "//src/main/java/com/google/devtools/build/lib/versioning:long_version_getter",
        "//src/main/java/com/google/devtools/build/lib/vfs",
        "//src/main/java/com/google/devtools/build/lib/vfs:pathfragment",
        "//src/main/java/com/google/devtools/common/options",
        "//src/main/protobuf:failure_details_java_proto",
        "//third_party:flogger",
        "//third_party:guava",
        "//third_party:jsr305",
    ],
)
