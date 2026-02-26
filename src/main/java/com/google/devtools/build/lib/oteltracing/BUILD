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
    name = "otel_trace_options",
    srcs = ["OTelTraceOptions.java"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/util",
        "//src/main/java/com/google/devtools/build/lib/vfs:pathfragment",
        "//src/main/java/com/google/devtools/common/options",
    ],
)

java_library(
    name = "oteltracing",
    srcs = [
        "BufferingSpanExporter.java",
        "OTelJsonFileWriter.java",
        "OTelSpanMapper.java",
        "OTelTraceModule.java",
    ],
    deps = [
        ":otel_trace_options",
        "//src/main/java/com/google/devtools/build/lib:runtime",
        "//src/main/java/com/google/devtools/build/lib/actions",
        "//src/main/java/com/google/devtools/build/lib/analysis:analysis_cluster",
        "//src/main/java/com/google/devtools/build/lib/analysis:analysis_phase_complete_event",
        "//src/main/java/com/google/devtools/build/lib/analysis:analysis_phase_started_event",
        "//src/main/java/com/google/devtools/build/lib/events",
        "//src/main/java/com/google/devtools/common/options",
        "//third_party:gson",
        "//third_party:guava",
        "//third_party:jsr305",
        "//third_party:opentelemetry-api",
        "//third_party:opentelemetry-context",
        "//third_party:opentelemetry-exporter-otlp",
        "//third_party:opentelemetry-sdk",
        "//third_party:opentelemetry-sdk-common",
        "//third_party:opentelemetry-sdk-trace",
    ],
)
