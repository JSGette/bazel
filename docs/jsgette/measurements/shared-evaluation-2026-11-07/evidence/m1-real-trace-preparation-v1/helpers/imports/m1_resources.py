"""Pull-based M1 process/cgroup observations; no threads, launches or discovery scans.

Integration contract:
* expected_engines is the fixture's complete engine-id -> output-base mapping.
  EngineMapping records must come from runner-verified engine identities, not
  from assuming that one live native control PID proves N engines were admitted.
* Native records share control/server/server.pid.txt; private records use each
  output base's server/server.pid.txt. Pin PID AND /proc start ticks. Call
  read_process_identity after discovering a PID, without warming it with info.
  Verify engine-specific info/BEP mappings outside restarted build-only timing.
* Call sample(mappings) serially on whichever thread owns a read. Every call,
  including initial/final main-thread reads, measures its own thread CPU/time.
  No scheduling is supplied: the runner must use matched cadence in both arms.
* A missing mapping/read makes a sample ineligible, never zero. Cold-start
  pending samples need an explicit runner policy; they cannot count as complete
  engine observations. Any observed process replacement permanently invalidates
  identity continuity for this sampler/window. Use a fresh sampler per window.
* cpu_ticks are raw lifetime counters, not deltas. Difference identity-matched
  endpoints for existing JVMs; count a new JVM's lifetime ticks only after proving
  it was born after the measurement boundary. Do not subtract summed totals
  across changed PID sets. Missing final ticks remain an accounting limitation.
* Cgroup files are inexpensive, non-atomic snapshots. memory.stat includes raw
  reclaim counters; memory.events and PSI totals are cumulative. Form endpoint
  deltas only within the same owned cgroup, and reject counter resets. Gauges,
  PSI averages and memory.peak are not additive. Unknown optional PSI stays None.
  The counter directory is derived from cgroup_root + expected_cgroup, rejects
  path aliases, and pins (device,inode). Use sampler.cgroup_snapshot() for raw
  boundary reads with the same sticky pin; account their reader cost externally.
* PID files, membership and directory identities are checked at both endpoints.
  Exact membership transitions, including moves within the owned subtree, are
  rejected. Endpoint equality is not an ABA proof: the runner/guard must enforce
  stable process/cgroup ownership and prohibit migration or directory replacement.
* This helper does not enforce limits, attest scope ownership, perform host
  headroom checks, or control processes. Retain the existing outer guard.

The frozen M0 baseline is not imported or modified. Only explicitly provided
PID files, proc PID directories and the cgroup directory are read.
"""

from dataclasses import dataclass
import math
import os
from pathlib import Path, PurePosixPath
import stat as stat_mode
import time


@dataclass(frozen=True, order=True)
class ProcessIdentity:
    pid: int
    start_ticks: int

    def __post_init__(self):
        if self.pid <= 0 or self.start_ticks < 0:
            raise ValueError("Invalid process identity")


@dataclass(frozen=True)
class EngineMapping:
    output_base: str
    pid_file: Path
    identity: ProcessIdentity


def _nonnegative(value):
    value = int(value)
    if value < 0:
        raise ValueError("Negative counter")
    return value


def _positive(value):
    value = _nonnegative(value)
    if value == 0:
        raise ValueError("Zero PID")
    return value


def _stat(raw):
    opening, closing = raw.find("("), raw.rfind(")")
    if opening < 0 or closing <= opening:
        raise ValueError("Malformed proc stat command")
    pid = _positive(raw[:opening].strip())
    fields = raw[closing + 1:].split()
    if fields[0] in ("Z", "X", "x"):
        raise ValueError("Process has exited")
    return {"pid": pid, "start_ticks": _nonnegative(fields[19]),
            "cpu_ticks": _nonnegative(fields[11]) + _nonnegative(fields[12])}


def _pss(raw):
    values = [line.split() for line in raw.splitlines() if line.startswith("Pss:")]
    if len(values) != 1 or len(values[0]) != 3 or values[0][2] != "kB":
        raise ValueError("Missing or malformed Pss")
    return _nonnegative(values[0][1]) * 1024


def _membership(raw):
    values = [line[3:] for line in raw.splitlines() if line.startswith("0::")]
    if len(values) != 1 or not values[0].startswith("/"):
        raise ValueError("Missing unified cgroup membership")
    return values[0]


def _counters(raw):
    values = {}
    for line in raw.splitlines():
        key, value = line.split()
        if key in values:
            raise ValueError("Duplicate counter")
        values[key] = _nonnegative(value)
    if not values:
        raise ValueError("Empty counters")
    return values


def _pressure(raw):
    values = {}
    for line in raw.splitlines():
        kind, *fields = line.split()
        if kind in values:
            raise ValueError("Duplicate pressure row")
        row = {}
        for field in fields:
            key, value = field.split("=", 1)
            if key in row:
                raise ValueError("Duplicate pressure field")
            number = float(value) if key.startswith("avg") else _nonnegative(value)
            if not math.isfinite(number) or number < 0:
                raise ValueError("Invalid pressure value")
            row[key] = number
        if not {"avg10", "avg60", "avg300", "total"} <= row.keys():
            raise ValueError("Incomplete pressure row")
        values[kind] = row
    if "some" not in values:
        raise ValueError("Missing some pressure")
    return values


def _read(path, parser, errors, name):
    try:
        return parser(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError, IndexError) as error:
        errors[name] = type(error).__name__
        return None


def read_process_identity(pid, proc_root=Path("/proc")):
    """Read one known PID; return None if unknown/exited. Does not pin a window."""
    pid = _positive(pid)
    stat = _read(Path(proc_root) / str(pid) / "stat", _stat, {}, "stat")
    return ProcessIdentity(pid, stat["start_ticks"]) if stat and stat["pid"] == pid else None


def _cgroup_directory(cgroup_root, expected_cgroup):
    path = PurePosixPath(expected_cgroup)
    if (path.anchor != "/" or str(path) == "/" or ".." in path.parts
            or str(path) != expected_cgroup):
        raise ValueError("A canonical dedicated absolute cgroup path is required")
    root = Path(cgroup_root).absolute()
    return root.joinpath(*path.parts[1:])


def _directory_identity(directory, errors, name):
    try:
        if directory.resolve(strict=True) != directory:
            raise ValueError("Aliased cgroup directory")
        value = directory.lstat()
        if not stat_mode.S_ISDIR(value.st_mode):
            raise ValueError("Not a cgroup directory")
        return {"device": value.st_dev, "inode": value.st_ino}
    except (OSError, ValueError, RuntimeError) as error:
        errors[name] = type(error).__name__
        return None


def read_cgroup_snapshot(*, expected_cgroup, cgroup_root=Path("/sys/fs/cgroup"),
                         expected_identity=None):
    """Read named cgroup-v2 files; required failures affect eligibility, PSI is optional.

    This standalone read is not timed. Include it in the calling phase's observer
    accounting, or use ResourceSampler.sample, which times the complete read.
    For deltas with this free function, pass the first valid directory_identity
    as expected_identity on subsequent reads. A ResourceSampler retains this pin
    automatically, including a matching first identity when the final read fails.
    """
    directory = _cgroup_directory(cgroup_root, expected_cgroup)
    errors, snapshot = {}, {}
    parsers = {name: _nonnegative for name in ("memory.current", "memory.peak", "memory.swap.current")}
    parsers.update({name: _counters for name in ("cpu.stat", "memory.stat", "memory.events", "memory.events.local")})
    parsers.update({name: _pressure for name in ("memory.pressure", "cpu.pressure", "io.pressure")})
    before = _directory_identity(directory, errors, "directory_before")
    if before is not None and expected_identity is not None and before != expected_identity:
        errors["directory_identity"] = "DirectoryReplaced"
    for name, parser in parsers.items():
        snapshot[name] = (_read(directory / name, parser, errors, name)
                          if before is not None and "directory_identity" not in errors else None)
    after = _directory_identity(directory, errors, "directory_after")
    if before is not None and after != before:
        errors["directory_identity"] = "DirectoryChangedDuringRead"
    binding_valid = before is not None and after == before and "directory_identity" not in errors
    if not binding_valid:
        # Do not publish a mixture of counters from different/replaced paths.
        snapshot.update({name: None for name in parsers})
    required = {"cpu.stat": ("usage_usec",), "memory.stat": ("anon", "file"),
                "memory.events": ("oom", "oom_kill"), "memory.current": ()}
    for name, keys in required.items():
        if snapshot[name] is not None:
            for key in keys:
                if key not in snapshot[name]:
                    errors[name + "." + key] = "MissingCounter"
    snapshot["eligible"] = binding_valid and all(
        name not in errors and not any(name + "." + key in errors for key in keys)
        for name, keys in required.items())
    snapshot["read_errors"] = errors
    snapshot.update(directory=str(directory), expected_cgroup=expected_cgroup,
                    directory_identity=before, directory_identity_after=after,
                    binding_valid=binding_valid)
    return snapshot


class ResourceSampler:
    """Verify mappings, pin process continuity, and sample each JVM once per call."""

    def __init__(self, expected_engines, *, expected_cgroup,
                 cgroup_root=Path("/sys/fs/cgroup"),
                 proc_root=Path("/proc"), clock_ticks_per_second=None,
                 monotonic_ns=time.monotonic_ns, thread_cpu_ns=time.thread_time_ns):
        self.expected = {key: str(base) for key, base in expected_engines.items()}
        if not self.expected or len(set(self.expected.values())) != len(self.expected):
            raise ValueError("Expected engines need distinct output bases")
        self.expected_cgroup = expected_cgroup
        self.cgroup_root = Path(cgroup_root).absolute()
        self.group = _cgroup_directory(self.cgroup_root, expected_cgroup)
        self.proc = Path(proc_root)
        if clock_ticks_per_second is None:
            clock_ticks_per_second = os.sysconf("SC_CLK_TCK")
        if clock_ticks_per_second <= 0:
            raise ValueError("Invalid CPU tick rate")
        self.hz = clock_ticks_per_second
        self.monotonic_ns, self.thread_cpu_ns = monotonic_ns, thread_cpu_ns
        self.identities = {}
        self.identity_failures = set()
        self.memberships = {}
        self.membership_failures = set()
        self.group_identity = None
        self.group_identity_failed = False

    def _pin_directory(self, identity):
        if identity is None:
            self.group_identity_failed = True
        elif self.group_identity is None:
            self.group_identity = dict(identity)
        elif self.group_identity != identity:
            self.group_identity_failed = True

    def cgroup_snapshot(self):
        """Untimed raw boundary read sharing this sampler's sticky scope binding."""
        snapshot = read_cgroup_snapshot(expected_cgroup=self.expected_cgroup,
                                       cgroup_root=self.cgroup_root,
                                       expected_identity=self.group_identity)
        self._pin_directory(snapshot["directory_identity"])
        if not snapshot["binding_valid"]:
            self.group_identity_failed = True
        snapshot["identity_continuity_valid"] = not self.group_identity_failed
        snapshot["expected_directory_identity"] = dict(self.group_identity) if self.group_identity is not None else None
        snapshot["eligible"] &= not self.group_identity_failed
        return snapshot

    def sample(self, mappings):
        started, cpu_started = self.monotonic_ns(), self.thread_cpu_ns()
        directory_errors = {}
        directory_at_start = _directory_identity(self.group, directory_errors, "directory_at_sample_start")
        self._pin_directory(directory_at_start)
        engine_rows, groups, pid_files = {}, {}, {}
        pid_file_engines = {}
        unexpected = sorted(set(mappings) - self.expected.keys())
        for engine, base in self.expected.items():
            row = engine_rows[engine] = {"output_base": base, "errors": {}}
            mapping = mappings.get(engine)
            if mapping is None:
                row["errors"]["mapping"] = "MissingEngine"
                continue
            identity = mapping.identity
            row.update(pid=identity.pid, start_ticks=identity.start_ticks,
                       pid_file=str(mapping.pid_file))
            if str(mapping.output_base) != base:
                row["errors"]["mapping"] = "OutputBaseMismatch"
                continue
            if engine in self.identities and self.identities[engine] != identity:
                row["errors"]["identity"] = "ProcessReplaced"
                self.identity_failures.add(engine)
                continue
            pid_path = Path(mapping.pid_file)
            pid_file_engines.setdefault(pid_path, []).append(engine)
            if pid_path not in pid_files:
                errors = {}
                pid = _read(pid_path, _positive, errors, "pid_file")
                pid_files[pid_path] = (pid, errors)
            pid, errors = pid_files[pid_path]
            row["pid_file_value_before"] = pid
            row["errors"].update(errors)
            if pid is not None and pid != identity.pid:
                row["errors"]["identity"] = "PidFileChanged"
                self.identity_failures.add(engine)
            if not row["errors"]:
                groups.setdefault(identity, []).append(engine)

        identities_by_pid = {}
        for identity in groups:
            identities_by_pid.setdefault(identity.pid, set()).add(identity)
        processes = []
        for identity, engines in sorted(groups.items()):
            errors = {}
            process = {"pid": identity.pid, "start_ticks": identity.start_ticks,
                       "engines": engines, "cpu_ticks": None, "pss_bytes": None,
                       "cgroup": None, "cgroup_before": None, "cgroup_after": None, "errors": errors}
            processes.append(process)
            if len(identities_by_pid[identity.pid]) != 1:
                errors["identity"] = "ConflictingProcessIdentities"
            else:
                directory = self.proc / str(identity.pid)
                member_before = _read(directory / "cgroup", _membership, errors, "cgroup_before")
                process["cgroup_before"] = member_before
                before = _read(directory / "stat", _stat, errors, "stat_before")
                if before and (before["pid"], before["start_ticks"]) != (identity.pid, identity.start_ticks):
                    errors["identity"] = "ProcessReplaced"
                if before and "identity" not in errors:
                    # Pin immediately: disappearance/failure of the trailing stat
                    # must not let the caller silently enroll a replacement.
                    for engine in engines:
                        self.identities[engine] = identity
                    if member_before is not None:
                        prior = self.memberships.setdefault(identity, member_before)
                        if prior != member_before:
                            errors["membership"] = "MembershipChanged"
                    memory = _read(directory / "smaps_rollup", _pss, errors, "pss")
                    after = _read(directory / "stat", _stat, errors, "stat_after")
                    if after and (after["pid"], after["start_ticks"]) != (identity.pid, identity.start_ticks):
                        errors["identity"] = "ProcessReplacedDuringRead"
                    elif after and after["cpu_ticks"] < before["cpu_ticks"]:
                        errors["identity"] = "CpuCounterRegressed"
                    elif after:
                        process.update(cpu_ticks=after["cpu_ticks"], pss_bytes=memory)
                member_after = _read(directory / "cgroup", _membership, errors, "cgroup_after")
                process["cgroup_after"] = member_after
                if member_before is not None and member_after is not None and member_before != member_after:
                    errors["membership"] = "MembershipChangedDuringRead"
                if any(member is not None and not (
                        member == self.expected_cgroup or member.startswith(self.expected_cgroup + "/"))
                       for member in (member_before, member_after)):
                    errors["membership"] = "OutsideExpectedCgroup"
                if member_before == member_after:
                    process["cgroup"] = member_after
            if "identity" in errors:
                self.identity_failures.update(engines)
            if "membership" in errors:
                self.membership_failures.update(engines)
            for engine in engines:
                engine_rows[engine]["errors"].update(errors)
        cgroup = self.cgroup_snapshot()
        cgroup["directory_identity_at_sample_start"] = directory_at_start
        cgroup["read_errors"].update(directory_errors)
        for pid_path, engines in pid_file_engines.items():
            errors = {}
            pid_after = _read(pid_path, _positive, errors, "pid_file_after")
            pid_before = pid_files[pid_path][0]
            for engine in engines:
                row = engine_rows[engine]
                row["pid_file_value_after"] = pid_after
                row["errors"].update(errors)
                if pid_after != pid_before:
                    row["errors"]["identity"] = "PidFileChangedDuringRead"
                    self.identity_failures.add(engine)
        valid_engines = sum(not row["errors"] for row in engine_rows.values())
        mapping_complete = valid_engines == len(self.expected) and not unexpected
        process_complete = mapping_complete and not self.identity_failures and not self.membership_failures
        total_pss = sum(p["pss_bytes"] for p in processes) if process_complete else None
        total_ticks = sum(p["cpu_ticks"] for p in processes) if process_complete else None
        result = {"eligible": process_complete and cgroup["eligible"],
                  "expected_engine_count": len(self.expected), "valid_engine_count": valid_engines,
                  "unexpected_engine_ids": unexpected, "engines": engine_rows, "processes": processes,
                  "sampled_process_count": sum(not p["errors"] for p in processes),
                  "identity_continuity_valid": not self.identity_failures,
                  "identity_failure_engines": sorted(self.identity_failures),
                  "membership_continuity_valid": not self.membership_failures,
                  "membership_failure_engines": sorted(self.membership_failures),
                  "pss_bytes": total_pss, "cpu_ticks": total_ticks,
                  "cpu_lifetime_seconds": total_ticks / self.hz if total_ticks is not None else None,
                  "clock_ticks_per_second": self.hz, "cgroup": cgroup,
                  "sample_started_monotonic_ns": started}
        result["sample_thread_cpu_ns"] = self.thread_cpu_ns() - cpu_started
        result["sample_duration_ns"] = self.monotonic_ns() - started
        return result
