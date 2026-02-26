load("@rules_java//java:defs.bzl", "java_library", "java_test")

package(
    default_applicable_licenses = ["//:license"],
    default_testonly = 1,
    default_visibility = ["//src:__subpackages__"],
)

filegroup(
    name = "srcs",
    testonly = 0,
    srcs = glob(["**"]),
    visibility = ["//src:__subpackages__"],
)

java_library(
    name = "OTelTracingTests_lib",
    srcs = glob(["*.java"]),
    deps = [
        "//src/main/java/com/google/devtools/build/lib:runtime",
        "//src/main/java/com/google/devtools/build/lib/actions",
        "//src/main/java/com/google/devtools/build/lib/actions:action_input_helper",
        "//src/main/java/com/google/devtools/build/lib/actions:artifacts",
        "//src/main/java/com/google/devtools/build/lib/actions:localhost_capacity",
        "//src/main/java/com/google/devtools/build/lib/analysis:analysis_cluster",
        "//src/main/java/com/google/devtools/build/lib/collect/nestedset",
        "//src/main/java/com/google/devtools/build/lib/events",
        "//src/main/java/com/google/devtools/build/lib/oteltracing",
        "//src/main/java/com/google/devtools/build/lib/util:detailed_exit_code",
        "//src/main/protobuf:failure_details_java_proto",
        "//src/main/java/com/google/devtools/build/lib/oteltracing:otel_trace_options",
        "//src/main/java/com/google/devtools/common/options",
        "//src/test/java/com/google/devtools/build/lib/exec/util",
        "//src/test/java/com/google/devtools/build/lib/testutil",
        "//third_party:gson",
        "//third_party:guava",
        "//third_party:junit4",
        "//third_party:mockito",
        "//third_party:opentelemetry-api",
        "//third_party:opentelemetry-context",
        "//third_party:opentelemetry-sdk",
        "//third_party:opentelemetry-sdk-common",
        "//third_party:opentelemetry-sdk-trace",
        "//third_party:truth",
    ],
)

java_test(
    name = "OTelTracingTests",
    test_class = "com.google.devtools.build.lib.AllTests",
    runtime_deps = [
        ":OTelTracingTests_lib",
        "//src/test/java/com/google/devtools/build/lib:test_runner",
    ],
)
