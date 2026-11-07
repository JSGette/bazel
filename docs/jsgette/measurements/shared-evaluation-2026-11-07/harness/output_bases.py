"""Exclusively leased, strictly bound mutable Bazel state; run evidence lives elsewhere."""

import json
import os
from pathlib import Path


def write_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Pool:
    """No recovery/reset path: incomplete or failed prior runs make a pool unusable."""

    def __init__(self, root, binding, report_path):
        self.root = Path(root).resolve()
        self.binding = binding
        self.report_path = Path(report_path).resolve()
        self.lock = None
        self.previous_report_path = None
        self.run_number = None

    def __enter__(self):
        import fcntl

        if self.report_path.is_relative_to(self.root):
            raise ValueError("Output-base pool must be separate from immutable run evidence")
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.root / ".lock").open("a", encoding="utf-8")
        try:
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("Output-base pool is already leased: " + str(self.root)) from error
            binding_path = self.root / "binding.json"
            if binding_path.exists():
                if json.loads(binding_path.read_text()) != self.binding:
                    raise ValueError("Output-base pool binding differs: " + str(self.root))
            else:
                if any(path.name != ".lock" for path in self.root.iterdir()):
                    raise ValueError("Unbound output-base pool must be empty: " + str(self.root))
                write_new(binding_path, self.binding)
            markers = self.root / "runs"
            markers.mkdir(exist_ok=True)
            previous = sorted(markers.iterdir())
            if any(path.name != "%012d.json" % (index + 1) for index, path in enumerate(previous)):
                raise ValueError("Output-base pool has an invalid run-marker sequence")
            if previous:
                marker = json.loads(previous[-1].read_text())
                self.validate_previous(marker)
                self.previous_report_path = marker["report_path"]
            elif any(self.root.glob("o[0-9]*")):
                raise ValueError("Output-base pool contains state without a prior run marker")
            self.require_stopped()
            self.run_number = len(previous) + 1
            write_new(markers / ("%012d.json" % self.run_number),
                      {"report_path": str(self.report_path)})
            return self
        except BaseException:
            self.close()
            raise

    def validate_previous(self, marker):
        path = Path(marker["report_path"])
        if not path.is_file():
            raise ValueError("Output-base pool prior run has no completed report: " + str(path))
        report = json.loads(path.read_text())
        shutdown = report.get("final_shutdown", [])
        if (report.get("passed") is not True
                or report.get("output_base_root") != str(self.root)
                or len(shutdown) != self.binding["fixture"]["clients"]
                or any(row.get("exit_code") != 0 or row.get("timed_out", False) for row in shutdown)):
            raise ValueError("Output-base pool prior run did not finish successfully: " + str(path))

    def require_stopped(self):
        for index in range(self.binding["fixture"]["clients"]):
            path = self.root / ("o%02d" % index) / "server/server.pid.txt"
            if not path.exists():
                continue
            pid = int(path.read_text().strip())
            if pid <= 0 or process_alive(pid):
                raise ValueError("Output-base pool still has a live or invalid daemon PID: " + str(path))

    def close(self):
        if self.lock is not None:
            self.lock.close()
            self.lock = None

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
