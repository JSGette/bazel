# OpenTelemetry (OTel) Support in Bazel — Summary of Changes

This document summarizes the changes introduced to add OpenTelemetry tracing to Bazel and export traces via OTLP (HTTP/gRPC) or to a JSON file.

---

## 1. Flags and options

| Flag | Purpose |
|------|--------|
| `--experimental_otel_trace_endpoint=<URL>` | Stream spans in real time via OTLP to the given endpoint. Protocol is inferred from the URL: `http://` or `https://` → OTLP over HTTP (e.g. Datadog agent at `http://localhost:4318`); `grpc://` or `grpcs://` → OTLP over gRPC (e.g. `grpc://localhost:4317`). Bare `host:port` is treated as gRPC. |
| `--experimental_otel_trace_json=<path>` | Write the full trace as OTLP JSON to a file at the end of the build. |
| `--experimental_otel_trace_all_actions` | When `true`, create spans for all actions; when `false` (default), only for failed (or otherwise selected) actions. |

Tracing is enabled when at least one of the two export options is set.

---

## 2. Export and protocol

- **OTLP over HTTP**  
  Used when the endpoint URL starts with `http://` or `https://`. Implemented with `OtlpHttpSpanExporter` (artifact `opentelemetry-exporter-otlp-http-trace`). The endpoint is normalized to include `/v1/traces` when missing.

- **OTLP over gRPC**  
  Used when the URL starts with `grpc://` or `grpcs://`, or when no scheme is given (e.g. `localhost:4317`). Implemented with `OtlpGrpcSpanExporter` (existing `opentelemetry-exporter-otlp`).

- **Streaming**  
  When an endpoint is set, a `BatchSpanProcessor` is used: spans are sent in batches during the build. At the end of the command, `forceFlush()` and `shutdown()` ensure any remaining spans are exported.

- **JSON file**  
  When `--experimental_otel_trace_json` is set, a `SimpleSpanProcessor` and a buffering exporter collect spans in memory; the full trace is written to the file in OTLP JSON format in `afterCommand()`.

---

## 3. Resource and service identity

- **Resource**  
  The OTel `Resource` is set with `service.name = "bazel"` so backends (e.g. Jaeger, Datadog) show a proper service name instead of a placeholder.

- **Trace ID at build start**  
  When tracing is enabled, Bazel prints a single line at the start of the build:  
  `OTel trace ID: <32-hex-trace-id>`  
  so users can look up the trace in their backend.

---

## 4. Span hierarchy and lifecycle

- **Build** → **Phases** (loading, analysis, execution) → **Targets** → **Actions** → **Spawns**, plus **Fetch** spans under the build where applicable.

- **Target as parent for actions**  
  A target span is created on demand (e.g. when the first action for that target is seen) so the hierarchy is **target → action → spawn**, not just target label on the action.

- **Action spans**  
  Started on `ActionStartedEvent` and ended on `ActionExecutedEvent`, `CachedActionEvent`, or `ActionCompletionEvent`. Spans use event timestamps (via `BlazeClock` / `Instant`) so duration reflects actual execution time. `ActionExecutedEvent` exposes `getStartTime()` and `getEndTime()` for this.

- **Fetch spans**  
  From `FetchProgress`: start when a fetch begins, end when it finishes. If only a “finished” event is seen, an instant fetch span is still created so the source is recorded.

---

## 5. Attributes (no truncation)

Attribute values are stored in full (no length limits or “… (N more)”):

- **Build:** `bazel.command`, `bazel.invocation_id`, `bazel.build.targets` (full list), `bazel.invocation.command_line` (full command line from `OriginalUnstructuredCommandLineEvent`), `bazel.exit_code`, `bazel.success`, etc.
- **Spawn:** `bazel.spawn.command_line`, `bazel.spawn.inputs` (newline-separated), `bazel.spawn.outputs` (newline-separated), `bazel.spawn.input_count`, `bazel.spawn.output_count`, `bazel.spawn.cache_hit`, `bazel.spawn.cache_digest`, plus timing and exit code attributes.
- **Action:** `bazel.action.cache_key_digest` when cache hit, mnemonic, target label, primary output, etc.
- **Fetch:** `bazel.fetch.resource`, `bazel.fetch.source` (URL, path, or repo id), `bazel.fetch.progress`.

Fetch span names use the full resource identifier (no truncation).

---

## 6. Main files changed

| Area | Files |
|------|--------|
| **Module and export** | `OTelTraceModule.java` — creates tracer, BatchSpanProcessor (endpoint) and/or SimpleSpanProcessor (JSON), resource with `service.name`, HTTP vs gRPC exporter selection, flush/shutdown in `afterCommand()`. |
| **Options** | `OTelTraceOptions.java` — `experimental_otel_trace_endpoint`, `experimental_otel_trace_json`, `experimental_otel_trace_all_actions`. |
| **Event → spans** | `OTelSpanMapper.java` — EventBus subscriptions, build/phase/target/action/spawn/fetch span creation, attributes, target-as-parent, trace ID print via `Reporter`. |
| **JSON export** | `OTelJsonFileWriter.java`, `BufferingSpanExporter.java` — in-memory span buffer and OTLP JSON write; resource includes `service.name`. |
| **Dependencies** | `BUILD` (oteltracing) — `opentelemetry-exporter-otlp`, `opentelemetry-exporter-otlp-http-trace`, events, runtime, actions, analysis, clock, etc. |
| **Maven** | `MODULE.bazel` — `opentelemetry-exporter-otlp-http-trace:1.46.0`. `third_party/BUILD` — alias for that artifact. |
| **Events** | `ActionExecutedEvent.java` — `getStartTime()`, `getEndTime()`. `OriginalUnstructuredCommandLineEvent.java` — `getArgs()` for full command line. |
| **Tests** | `OTelSpanMapperTest.java` — constructor updated for new `Reporter` parameter. |
| **Docs** | `docs/otel-trace-enrichment-plan.md` — design and attribute reference. |

---

## 7. Usage examples

- **Stream to Datadog agent (OTLP HTTP):**  
  `bazel build --experimental_otel_trace_endpoint=http://localhost:4318 //...`

- **Stream to OTLP gRPC collector:**  
  `bazel build --experimental_otel_trace_endpoint=grpc://localhost:4317 //...`

- **Write trace to JSON only:**  
  `bazel build --experimental_otel_trace_json=/tmp/trace.json //...`

- **Both endpoint and JSON:**  
  `bazel build --experimental_otel_trace_endpoint=http://localhost:4318 --experimental_otel_trace_json=/tmp/trace.json //...`

After a build with tracing enabled, the log line **OTel trace ID: &lt;id&gt;** can be used to find the trace in the configured backend.
