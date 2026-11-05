"""Measure a benchmark worker's wall time and sampled process-tree memory.

The sampler belongs to the coordinator. Each observation sums current resident
bytes across observed descendants and the worker (working set on Windows).
Shared pages can count in more than one process. Processes born and gone
between observations, inaccessible processes, and brief peaks can be missed.
This is neither an OS high-water mark nor a measurement of physical disk I/O.
"""

import os
import subprocess
import sys
import threading
import time

try:
    import psutil
except ImportError:
    psutil = None


def _psutil_sample(pid):
    try:
        parent = psutil.Process(pid)
        tree = [parent, *parent.children(recursive=True)]
    except psutil.NoSuchProcess:
        return 0, 0
    total = count = 0
    seen = set()
    for process in tree:
        if process.pid in seen:
            continue
        seen.add(process.pid)
        try:
            total += process.memory_info().rss
            count += 1
        except psutil.NoSuchProcess:
            pass
    return total, count


def _procfs_sample(pid):
    # Top-level numeric /proc entries represent process leaders, never each
    # individual thread. Read parent and RSS together from each stat record.
    page_bytes = os.sysconf("SC_PAGE_SIZE")
    children, rss = {}, {}
    with os.scandir("/proc") as entries:
        for entry in entries:
            if not entry.name.isdecimal():
                continue
            try:
                with open(entry.path + "/stat", "rb") as f:
                    raw = f.read()
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
            fields = raw[raw.rindex(b")") + 2:].split()  # state is field 3
            child = int(entry.name)
            children.setdefault(int(fields[1]), []).append(child)
            rss[child] = int(fields[21]) * page_bytes  # RSS is field 24
    pending, seen = [pid], set()
    total = 0
    while pending:
        current = pending.pop()
        if current in seen or current not in rss:
            continue
        seen.add(current)
        total += rss[current]
        pending.extend(children.get(current, ()))
    return total, len(seen)


def measure(argv, cwd=None, *, interval_ms=20):
    """Return (UTF-8 stdout, stats); raise on a failed child. None disables sampling.

    elapsed_ms covers spawn through exit/pipe drain; sampler teardown and JSON
    decoding are excluded. sample_interval_ms is the intended interval, and
    sampler_scan_ms records elapsed time actually spent making observations.
    """
    if interval_ms is not None and interval_ms <= 0:
        raise ValueError("interval_ms must be positive or None")
    if interval_ms is None:
        name, sample = "disabled", None
    elif psutil is not None:
        name, sample = "psutil", _psutil_sample
    elif sys.platform.startswith("linux") and os.path.isdir("/proc/self"):
        name, sample = "Linux /proc", _procfs_sample
    else:
        name, sample = "unavailable", None

    stats = {"memory_sampler": name, "sample_interval_ms": interval_ms,
             "samples": 0, "max_observed_processes": 0,
             "peak_sampled_tree_rss_bytes": None, "sampler_scan_ms": 0.0}
    stop = threading.Event()

    def monitor():
        try:
            while not stop.is_set():
                before = time.perf_counter()
                rss, count = sample(proc.pid)
                duration = time.perf_counter() - before
                stats["sampler_scan_ms"] += duration * 1000
                if count:
                    stats["samples"] += 1
                    stats["max_observed_processes"] = max(stats["max_observed_processes"], count)
                    stats["peak_sampled_tree_rss_bytes"] = max(stats["peak_sampled_tree_rss_bytes"] or 0, rss)
                stop.wait(max(0.001, interval_ms / 1000 - duration))
        except Exception as exc:
            # Timing remains useful when this host denies process inspection.
            stats["memory_sampler_error"] = f"{type(exc).__name__}: {exc}"
            stats["peak_sampled_tree_rss_bytes"] = None

    start = time.perf_counter()
    with subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        watcher = threading.Thread(target=monitor, daemon=True) if sample is not None else None
        if watcher is not None:
            watcher.start()
        try:
            out, err = proc.communicate()
            elapsed = time.perf_counter() - start
        finally:
            stop.set()
            if watcher is not None:
                watcher.join()
    if proc.returncode:
        raise RuntimeError(f"worker exited {proc.returncode}: {err.decode('utf-8', 'replace').strip()}")
    stats["elapsed_ms"] = round(elapsed * 1000, 3)
    stats["sampler_scan_ms"] = round(stats["sampler_scan_ms"], 3)
    return out.decode("utf-8"), stats
