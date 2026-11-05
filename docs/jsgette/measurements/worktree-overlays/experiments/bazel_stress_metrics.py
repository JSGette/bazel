"""Post-run Bazel telemetry and resource limits for a private stress cohort.

Call govern_current_process ONCE inside a new coordinator, BEFORE spawning clients.
On Windows its job owns that coordinator and descendants. The memory limit is on
aggregate virtual memory *commit*, distinct from sampled RSS and JVM heap sizes.
Use measure_process.measure() from a separate parent to sample the cohort's RSS.
"""

import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from urllib.request import url2pathname


_job = None


def govern_current_process(cpu_count: int, memory_mib: int):
    """Restrict this new cohort process and future descendants. Fail on denied limits."""
    if cpu_count <= 0 or memory_mib <= 0:
        raise ValueError("cpu_count and memory_mib must be positive")
    if sys.platform == "win32":
        return _govern_windows(cpu_count, memory_mib)
    if hasattr(os, "sched_setaffinity"):
        use = sorted(os.sched_getaffinity(0))[:cpu_count]
        os.sched_setaffinity(0, use)
        return {"method": "sched_setaffinity", "logical_cpu_ids": use,
                "job_commit_limit_bytes": None, "aggregate_memory_limit_enforced": False}
    raise NotImplementedError("no verified cohort affinity/memory governor for this OS")


def _govern_windows(cpu_count, memory_mib):
    import ctypes as c
    from ctypes import wintypes as w

    global _job
    if _job is not None:
        raise RuntimeError("a cohort job is already active in this process")
    k = c.WinDLL("kernel32", use_last_error=True)
    size = c.c_size_t

    class Basic(c.Structure):
        _fields_ = [("process_time", c.c_int64), ("job_time", c.c_int64),
                    ("flags", w.DWORD), ("min_ws", size), ("max_ws", size),
                    ("active_limit", w.DWORD), ("affinity", size),
                    ("priority", w.DWORD), ("scheduling", w.DWORD)]

    class Extended(c.Structure):
        _fields_ = [("basic", Basic), ("io", c.c_uint64 * 6),
                    ("process_memory", size), ("job_memory", size),
                    ("peak_process_memory", size), ("peak_job_memory", size)]

    for name, args, ret in [
        ("GetCurrentProcess", [], w.HANDLE),
        ("GetProcessAffinityMask", [w.HANDLE, c.POINTER(size), c.POINTER(size)], w.BOOL),
        ("CreateJobObjectW", [c.c_void_p, w.LPCWSTR], w.HANDLE),
        ("SetInformationJobObject", [w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
        ("AssignProcessToJobObject", [w.HANDLE, w.HANDLE], w.BOOL),
        ("IsProcessInJob", [w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)], w.BOOL),
        ("OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        ("QueryInformationJobObject", [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
        ("CloseHandle", [w.HANDLE], w.BOOL),
    ]:
        fn = getattr(k, name)
        fn.argtypes, fn.restype = args, ret

    def checked(ok):
        if not ok:
            raise c.WinError(c.get_last_error())
        return ok

    process = k.GetCurrentProcess()
    permitted, system = size(), size()
    checked(k.GetProcessAffinityMask(process, c.byref(permitted), c.byref(system)))
    use = [i for i in range(c.sizeof(size) * 8) if permitted.value & (1 << i)][:cpu_count]
    if not use:
        raise RuntimeError("no usable CPU in this Windows processor group")
    info = Extended()
    # JOB_OBJECT_LIMIT_AFFINITY | JOB_OBJECT_LIMIT_JOB_MEMORY | KILL_ON_JOB_CLOSE
    info.basic.flags = 0x10 | 0x200 | 0x2000
    info.basic.affinity = sum(1 << i for i in use)
    info.job_memory = memory_mib * 1024**2
    handle = checked(k.CreateJobObjectW(None, None))
    try:
        checked(k.SetInformationJobObject(handle, 9, c.byref(info), c.sizeof(info)))
        checked(k.AssignProcessToJobObject(handle, process))
    except BaseException:
        k.CloseHandle(handle)
        raise
    # Keep the initial non-inheritable job handle alive until this cohort exits.
    _job = k, handle, Extended
    checked(k.GetProcessAffinityMask(process, c.byref(permitted), c.byref(system)))
    if permitted.value != info.basic.affinity:
        raise RuntimeError("Windows did not apply the requested affinity")
    return {"method": "Windows Job Object", "logical_cpu_ids": use,
            "job_commit_limit_bytes": info.job_memory, "aggregate_memory_limit_enforced": True}


def peak_job_commit_bytes():
    """OS job-wide peak committed memory, available while the Windows cohort lives."""
    if _job is None:
        return None
    import ctypes as c
    k, handle, struct = _job
    info = struct()
    if not k.QueryInformationJobObject(handle, 9, c.byref(info), c.sizeof(info), None):
        raise c.WinError(c.get_last_error())
    return info.peak_job_memory


def cohort_job_cpu_seconds():
    """Cumulative user + kernel CPU consumed by the current Windows job."""
    if _job is None:
        return None
    import ctypes as c
    from ctypes import wintypes as w

    class Accounting(c.Structure):
        _fields_ = [("user", c.c_int64), ("kernel", c.c_int64),
                    ("period_user", c.c_int64), ("period_kernel", c.c_int64),
                    ("faults", w.DWORD), ("processes", w.DWORD),
                    ("active", w.DWORD), ("terminated", w.DWORD)]

    api, handle, _ = _job
    info = Accounting()
    if not api.QueryInformationJobObject(handle, 1, c.byref(info), c.sizeof(info), None):
        raise c.WinError(c.get_last_error())
    return (info.user + info.kernel) / 10_000_000


def is_cohort_job_member(pid: int):
    """Check a live server PID from the governed coordinator. None means unavailable."""
    if _job is None or not pid:
        return None
    import ctypes as c
    from ctypes import wintypes as w
    k, handle, _ = _job
    proc = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not proc:
        if c.get_last_error() == 87:  # ERROR_INVALID_PARAMETER, PID no longer exists
            return None
        raise c.WinError(c.get_last_error())
    try:
        member = w.BOOL()
        if not k.IsProcessInJob(proc, handle, c.byref(member)):
            raise c.WinError(c.get_last_error())
        return bool(member.value)
    finally:
        k.CloseHandle(proc)


def cohort_job_pids():
    """Enumerate the governed Windows job, including detached server processes."""
    if _job is None:
        raise RuntimeError("a Windows cohort job is required")
    import ctypes as c
    from ctypes import wintypes as w
    k, handle, _ = _job
    capacity = 32
    while True:
        class Pids(c.Structure):
            _fields_ = [("assigned", w.DWORD), ("listed", w.DWORD),
                        ("pids", c.c_size_t * capacity)]

        info = Pids()
        # JobObjectBasicProcessIdList, a variable-length array of ULONG_PTR.
        if k.QueryInformationJobObject(handle, 3, c.byref(info), c.sizeof(info), None):
            return list(info.pids[:info.listed])
        if c.get_last_error() != 234:  # ERROR_MORE_DATA
            raise c.WinError(c.get_last_error())
        capacity = max(2 * capacity, info.assigned)


def _int(data, key):
    # Proto JSON omits default scalars; preserve absence rather than invent a sample.
    return int(data[key]) if key in data else None


def _duration_ms(value):
    if isinstance(value, str) and value.endswith("s"):
        return round(float(value[:-1]) * 1000, 3)
    return None


def _union_ms(spans):
    total = end = None
    for start, stop in sorted(spans):
        total = (total or 0) + max(0, stop - max(start, end if end is not None else start))
        end = max(stop, end if end is not None else stop)
    return round(total / 1000, 3) if total is not None else None


def _profile(path):
    if path is None or not Path(path).is_file():
        return {"status": "missing"}
    path = Path(path)
    # Offline only. Bazel JSON trace uses microsecond timestamp/duration fields.
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as f:
            trace = json.load(f)
        events = trace["traceEvents"] if isinstance(trace, dict) else trace
        end = None
        markers, spans = [], {}
        selected = {"package creation", "globbing", "action processing", "local action execution",
                    "complete action execution", "critical path component", "Fetching repository"}
        for event in events:
            if not isinstance(event.get("ts"), (int, float)):
                continue
            start = event["ts"]
            stop = start + event.get("dur", 0)
            end = max(stop, end if end is not None else stop)
            cat = event.get("cat", "")
            if cat == "build phase marker":
                markers.append((start, event["name"]))
            if cat in selected and event.get("ph") == "X":
                spans.setdefault(cat, []).append((start, stop))
        markers.sort()
        phases = [{"name": n, "ms": round((s2 - s) / 1000, 3)}
                  for (s, n), (s2, _) in zip(markers, markers[1:] + [(end, "")])]
        return {"status": "read", "events": len(events), "phase_marker_intervals": phases,
                "trace_category_union_ms": {cat: _union_ms(rows) for cat, rows in spans.items()}}
    except (OSError, EOFError, ValueError, KeyError, TypeError) as exc:
        return {"status": "unreadable", "error": type(exc).__name__}


def read_metrics(bep: Path, profile: Path | None = None):
    """Read the final BEP and optional trace OUTSIDE the measured cohort interval.

    BEP analysis and execution wall spans may overlap. Counts of actionsExecuted
    include remote/disk cache retrievals; runner_counts disambiguates them where
    Bazel emitted a runner. Trace category unions overlap across categories too.
    """
    report = {"bep_status": "missing", "finished": False, "exit_code": None,
              "exit_code_name": None, "server_pid": None, "build_tool_version": None,
              "targets": [], "aborted": [], "metrics_present": False}
    metrics = {}
    if Path(bep).is_file():
        report["bep_status"] = "read"
        try:
            with open(bep, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if "started" in event:
                        started = event["started"]
                        report.update(server_pid=_int(started, "serverPid"),
                                      build_tool_version=started.get("buildToolVersion"))
                    if "finished" in event:
                        result = event["finished"].get("exitCode", {})
                        report.update(finished=True, exit_code=result.get("code", 0) if result else None,
                                      exit_code_name=result.get("name"))
                    if "completed" in event:
                        target = event.get("id", {}).get("targetCompleted", {})
                        report["targets"].append({"label": target.get("label"),
                                                  "aspect": target.get("aspect"),
                                                  "success": bool(event["completed"].get("success", False))})
                    if "aborted" in event:
                        ident = event.get("id", {})
                        report["aborted"].append({"id_type": next(iter(ident), None), "id": ident,
                                                  "reason": event["aborted"].get("reason", "UNKNOWN"),
                                                  "description": event["aborted"].get("description")})
                    if "buildMetrics" in event:
                        metrics = event["buildMetrics"]
                        report["metrics_present"] = True
        except (ValueError, OSError) as exc:
            report.update(bep_status="incomplete", error=type(exc).__name__)
    summary = metrics.get("actionSummary", {})
    timing = metrics.get("timingMetrics", {})
    graph = metrics.get("buildGraphMetrics", {})
    cache = summary.get("actionCacheStatistics", {})
    # SpawnStats.getSummary always emits total, and omits zero cache runners.
    # Each remaining runner besides internal counts spawn results, not actions.
    runners = {r["name"]: int(r.get("count", 0)) for r in summary.get("runnerCount", [])}
    known = "total" in runners
    report.update(
        actions_executed=_int(summary, "actionsExecuted"),
        actions_created=_int(summary, "actionsCreated"),
        runner_counts=summary.get("runnerCount"),
        disk_cache_hits=runners.get("disk cache hit", 0) if known else None,
        reported_executed_spawns=sum(
            count for name, count in runners.items()
            if name not in ("total", "internal") and not name.endswith(" cache hit")
        ) if known else None,
        action_mnemonics=summary.get("actionData"),
        local_cache_hits=_int(cache, "hits"), local_cache_misses=_int(cache, "misses"),
        local_cache_miss_details=cache.get("missDetails"),
        packages_loaded=_int(metrics.get("packageMetrics", {}), "packagesLoaded"),
        targets_configured=_int(metrics.get("targetMetrics", {}), "targetsConfigured"),
        post_build_skyframe_nodes=_int(graph, "postInvocationSkyframeNodeCount"),
        wall_ms=_int(timing, "wallTimeInMs"), cpu_ms=_int(timing, "cpuTimeInMs"),
        analysis_ms=_int(timing, "analysisPhaseTimeInMs"),
        execution_ms=_int(timing, "executionPhaseTimeInMs"),
        execution_start_ms=_int(timing, "actionsExecutionStartInMs"),
        critical_path_ms=_duration_ms(timing.get("criticalPathTime")),
        profile=_profile(profile),
    )
    return report


def hash_target_outputs(bep: Path, label: str):
    """Hash actual local default-output bytes for one exact successful target.

    This is for a trusted BEP produced by the current fixture. Follow its named
    sets, not bazel-bin symlinks shared by concurrent invocations. Refuse an
    empty/incomplete set, remote URI, duplicate logical name or ambiguous config.
    The return keys are output names independent of the scratch workspace path.
    """
    sets, groups = {}, []
    with open(bep, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            event = json.loads(line)
            ident = event.get("id", {})
            if "namedSetOfFiles" in event:
                sets[ident["namedSet"]["id"]] = event["namedSetOfFiles"]
            target = ident.get("targetCompleted", {})
            if (target.get("label") == label and not target.get("aspect")
                    and "completed" in event):
                complete = event["completed"]
                if not complete.get("success"):
                    raise ValueError(f"target failed: {label}")
                groups.extend(g for g in complete.get("outputGroup", []) if g.get("name") == "default")
    if len(groups) != 1 or groups[0].get("incomplete"):
        raise ValueError(f"expected one complete default output group for {label}")
    group = groups[0]
    outputs = list(group.get("inlineFiles", []))
    todo = [s["id"] for s in group.get("fileSets", [])]
    seen = set()
    while todo:
        ident = todo.pop()
        if ident in seen:
            continue
        seen.add(ident)
        nested = sets[ident]
        outputs.extend(nested.get("files", []))
        todo.extend(s["id"] for s in nested.get("fileSets", []))
    if not outputs:
        raise ValueError(f"no materialized default outputs for {label}")
    answer = {}
    for item in outputs:
        uri = urlsplit(item.get("uri", ""))
        if uri.scheme != "file" or uri.netloc not in ("", "localhost"):
            raise ValueError(f"expected a local output file for {item.get('name')!r}")
        name = item["name"]
        path = Path(url2pathname(uri.path))
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as out:
            while block := out.read(1024 * 1024):
                digest.update(block)
                size += len(block)
        value = {"sha256": digest.hexdigest(), "bytes": size}
        if name in answer and answer[name] != value:
            raise ValueError(f"ambiguous local output {name}")
        answer[name] = value
    return answer
