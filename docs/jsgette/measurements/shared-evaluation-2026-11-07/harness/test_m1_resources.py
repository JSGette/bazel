"""Offline fake-proc tests; no Linux runtime inspection, processes or builds.

python -B -m unittest discover -s <harness> -p test_m1_resources.py
"""

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from m1_resources import (EngineMapping, ProcessIdentity, ResourceSampler,
                          read_cgroup_snapshot, read_process_identity)


class ResourceTests(unittest.TestCase):
    def setUp(self):
        # Keep every fixture C-backed when run from the repository on Windows.
        parent = Path(__file__).resolve().parent
        self.temporary = tempfile.TemporaryDirectory(prefix=".m1-resources-test-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.proc, self.group_root = self.root / "proc", self.root / "cgroup"
        self.group = self.group_root / "task.scope"
        self.proc.mkdir()
        self.group.mkdir(parents=True)
        for name, contents in {
            "memory.current": "16384\n", "memory.peak": "20480\n", "memory.swap.current": "0\n",
            "cpu.stat": "usage_usec 700\nuser_usec 500\nsystem_usec 200\nnr_throttled 2\nthrottled_usec 35\n",
            "memory.stat": "anon 4096\nfile 8192\nkernel 4096\npgscan 14\npgsteal 7\npgscan_direct 3\n",
            "memory.events": "low 0\nhigh 1\nmax 2\noom 0\noom_kill 0\n",
            "memory.events.local": "low 0\nhigh 1\nmax 2\noom 0\noom_kill 0\n",
        }.items():
            self.write(self.group / name, contents)
        for name in ("memory.pressure", "cpu.pressure", "io.pressure"):
            self.write(self.group / name,
                       "some avg10=1.25 avg60=0.50 avg300=0.10 total=125\n"
                       "full avg10=0.00 avg60=0.00 avg300=0.00 total=15\n")

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def stat(self, pid, start=42, ticks=100, state="S"):
        fields = ["0"] * 20
        fields[0], fields[1] = state, "1"
        fields[11], fields[12], fields[19] = str(ticks - 10), "10", str(start)
        self.write(self.proc / str(pid) / "stat", f"{pid} (java ) worker) " + " ".join(fields) + "\n")

    def process(self, pid, start=42, ticks=100, pss_kib=8, group="/task.scope"):
        self.stat(pid, start, ticks)
        self.write(self.proc / str(pid) / "smaps_rollup", f"Rss: 999 kB\nPss: {pss_kib} kB\n")
        self.write(self.proc / str(pid) / "cgroup", "0::" + group + "\n")
        return ProcessIdentity(pid, start)

    def mapping(self, index, identity, pid_file=None):
        pid_file = pid_file or self.root / f"o{index}" / "server/server.pid.txt"
        self.write(pid_file, str(identity.pid) + "\n")
        return EngineMapping(str(self.root / f"o{index}"), pid_file, identity)

    def sampler(self, mappings, **kwargs):
        return ResourceSampler({key: value.output_base for key, value in mappings.items()},
                               proc_root=self.proc, cgroup_root=self.group_root,
                               expected_cgroup="/task.scope", clock_ticks_per_second=100, **kwargs)

    def test_ten_engines_one_pid_count_once_and_measure_reader_cost(self):
        identity = self.process(100)
        pid_file = self.root / "control/server/server.pid.txt"
        mappings = {str(i): self.mapping(i, identity, pid_file) for i in range(10)}
        wall, cpu = iter((1000, 1500)), iter((100, 175))
        sampler = self.sampler(mappings, monotonic_ns=lambda: next(wall), thread_cpu_ns=lambda: next(cpu))
        reads, original = [], Path.read_text

        def count_read(path, *args, **kwargs):
            reads.append(path)
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", count_read):
            row = sampler.sample(mappings)
        self.assertTrue(row["eligible"])
        self.assertEqual(row["valid_engine_count"], 10)
        self.assertEqual(row["sampled_process_count"], 1)
        self.assertEqual(row["pss_bytes"], 8192)
        self.assertEqual(row["cpu_ticks"], 100)
        self.assertEqual(row["cpu_lifetime_seconds"], 1.0)
        self.assertEqual(reads.count(self.proc / "100/smaps_rollup"), 1)
        self.assertEqual(reads.count(self.proc / "100/stat"), 2)
        self.assertEqual(reads.count(pid_file), 2)
        self.assertEqual(reads.count(self.proc / "100/cgroup"), 2)
        self.assertEqual(row["sample_thread_cpu_ns"], 75)
        self.assertEqual(row["sample_duration_ns"], 500)
        self.assertEqual(row["cgroup"]["memory.stat"]["pgscan_direct"], 3)
        self.assertEqual(row["cgroup"]["memory.events"]["max"], 2)
        self.assertEqual(row["cgroup"]["cpu.stat"]["throttled_usec"], 35)
        self.assertEqual(row["cgroup"]["memory.pressure"]["some"]["total"], 125)
        json.dumps(row)  # Reports contain only JSON-serializable values.

    def test_distinct_processes_sum_separately(self):
        mappings = {"a": self.mapping(0, self.process(100)),
                    "b": self.mapping(1, self.process(101, ticks=250, pss_kib=12))}
        row = self.sampler(mappings).sample(mappings)
        self.assertTrue(row["eligible"])
        self.assertEqual(row["sampled_process_count"], 2)
        self.assertEqual(row["pss_bytes"], 20 * 1024)
        self.assertEqual(row["cpu_ticks"], 350)

    def test_pid_reuse_is_ineligible_and_cannot_be_rebound_midwindow(self):
        mappings = {"a": self.mapping(0, self.process(100))}
        sampler = self.sampler(mappings)
        self.assertTrue(sampler.sample(mappings)["eligible"])
        self.stat(100, start=99)
        row = sampler.sample(mappings)
        self.assertFalse(row["eligible"])
        self.assertFalse(row["identity_continuity_valid"])
        self.assertIsNone(row["pss_bytes"])
        self.assertEqual(row["engines"]["a"]["errors"]["identity"], "ProcessReplaced")
        mappings["a"] = self.mapping(0, ProcessIdentity(100, 99))
        self.assertFalse(sampler.sample(mappings)["eligible"])

    def test_missing_engine_does_not_pass_because_shared_pid_exists(self):
        identity = self.process(100)
        mappings = {str(i): self.mapping(i, identity) for i in range(10)}
        sampler = self.sampler(mappings)
        del mappings["9"]
        row = sampler.sample(mappings)
        self.assertFalse(row["eligible"])
        self.assertEqual(row["valid_engine_count"], 9)
        self.assertEqual(row["engines"]["9"]["errors"]["mapping"], "MissingEngine")
        self.assertIsNone(row["pss_bytes"])
        self.assertIsNone(row["cpu_ticks"])

    def test_missing_pss_is_unknown_not_zero(self):
        mappings = {"a": self.mapping(0, self.process(100))}
        (self.proc / "100/smaps_rollup").unlink()
        row = self.sampler(mappings).sample(mappings)
        self.assertFalse(row["eligible"])
        self.assertIsNone(row["pss_bytes"])
        self.assertIsNone(row["processes"][0]["pss_bytes"])
        self.assertIn("pss", row["engines"]["a"]["errors"])

    def test_unknown_pss_does_not_allow_observed_identity_to_be_replaced(self):
        mappings = {"a": self.mapping(0, self.process(100))}
        sampler = self.sampler(mappings)
        (self.proc / "100/smaps_rollup").unlink()
        self.assertFalse(sampler.sample(mappings)["eligible"])
        mappings["a"] = self.mapping(0, self.process(100, start=99))
        row = sampler.sample(mappings)
        self.assertFalse(row["eligible"])
        self.assertFalse(row["identity_continuity_valid"])

    def test_missing_optional_pressure_and_required_counters_remain_unknown(self):
        mappings = {"a": self.mapping(0, self.process(100))}
        sampler = self.sampler(mappings)
        (self.group / "memory.pressure").unlink()
        row = sampler.sample(mappings)
        self.assertTrue(row["eligible"])
        self.assertIsNone(row["cgroup"]["memory.pressure"])
        self.assertIn("memory.pressure", row["cgroup"]["read_errors"])
        (self.group / "cpu.stat").unlink()
        row = sampler.sample(mappings)
        self.assertFalse(row["eligible"])
        self.assertIsNone(row["cgroup"]["cpu.stat"])

    def test_wrong_scope_prefix_is_rejected_but_child_scope_is_valid(self):
        mappings = {"a": self.mapping(0, self.process(100, group="/task.scope-other"))}
        sampler = self.sampler(mappings)
        self.assertFalse(sampler.sample(mappings)["eligible"])
        self.write(self.proc / "100/cgroup", "0::/task.scope/child\n")
        self.assertFalse(sampler.sample(mappings)["eligible"])
        self.assertTrue(self.sampler(mappings).sample(mappings)["eligible"])

    def test_pid_reuse_during_pss_read_does_not_mix_processes(self):
        mappings = {"a": self.mapping(0, self.process(100))}
        original = Path.read_text

        def replace_during_read(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == self.proc / "100/smaps_rollup":
                self.stat(100, start=99)
            return value

        with patch.object(Path, "read_text", replace_during_read):
            row = self.sampler(mappings).sample(mappings)
        self.assertFalse(row["eligible"])
        self.assertIsNone(row["processes"][0]["pss_bytes"])
        self.assertIsNone(row["processes"][0]["cpu_ticks"])

    def test_mapping_path_pidfile_and_unexpected_engine_checks(self):
        mappings = {"a": self.mapping(0, self.process(100))}
        sampler = self.sampler(mappings)
        wrong = EngineMapping("/wrong-base", mappings["a"].pid_file, mappings["a"].identity)
        self.assertFalse(sampler.sample({"a": wrong})["eligible"])
        self.assertFalse(sampler.sample({**mappings, "unexpected": mappings["a"]})["eligible"])
        mappings["a"].pid_file.unlink()
        row = sampler.sample(mappings)
        self.assertFalse(row["eligible"])
        self.assertIn("pid_file", row["engines"]["a"]["errors"])

    def test_identity_probe_handles_complex_comm_missing_and_zombie(self):
        self.process(100)
        self.assertEqual(read_process_identity(100, self.proc), ProcessIdentity(100, 42))
        self.assertIsNone(read_process_identity(101, self.proc))
        self.stat(100, state="Z")
        self.assertIsNone(read_process_identity(100, self.proc))

    def test_pidfile_replacement_and_counter_regression_are_ineligible(self):
        mappings = {"a": self.mapping(0, self.process(100))}
        sampler = self.sampler(mappings)
        self.assertTrue(sampler.sample(mappings)["eligible"])
        self.write(mappings["a"].pid_file, "101\n")
        row = sampler.sample(mappings)
        self.assertFalse(row["eligible"])
        self.assertEqual(row["engines"]["a"]["errors"]["identity"], "PidFileChanged")

        mappings["a"] = self.mapping(0, self.process(100))
        original = Path.read_text

        def regress_during_read(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == self.proc / "100/smaps_rollup":
                self.stat(100, ticks=90)
            return value

        with patch.object(Path, "read_text", regress_during_read):
            row = self.sampler(mappings).sample(mappings)
        self.assertFalse(row["eligible"])
        self.assertEqual(row["engines"]["a"]["errors"]["identity"], "CpuCounterRegressed")

    def test_cgroup_unknown_required_key_and_malformed_pressure_are_not_zero(self):
        self.write(self.group / "cpu.stat", "nr_throttled 2\n")
        self.write(self.group / "memory.pressure", "some avg10=nan avg60=0 avg300=0 total=1\n")
        row = read_cgroup_snapshot(cgroup_root=self.group_root, expected_cgroup="/task.scope")
        self.assertFalse(row["eligible"])
        self.assertNotIn("usage_usec", row["cpu.stat"])
        self.assertEqual(row["read_errors"]["cpu.stat.usage_usec"], "MissingCounter")
        self.assertIsNone(row["memory.pressure"])

    def test_exited_process_after_memory_read_does_not_publish_stale_pss(self):
        mappings = {"a": self.mapping(0, self.process(100))}
        original = Path.read_text

        def exit_during_read(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == self.proc / "100/smaps_rollup":
                (self.proc / "100/stat").unlink()
            return value

        with patch.object(Path, "read_text", exit_during_read):
            row = self.sampler(mappings).sample(mappings)
        self.assertFalse(row["eligible"])
        self.assertIsNone(row["processes"][0]["pss_bytes"])
        self.assertIsNone(row["processes"][0]["cpu_ticks"])

    def test_pidfile_change_during_pss_read_invalidates_continuity(self):
        mappings = {"a": self.mapping(0, self.process(100))}
        sampler = self.sampler(mappings)
        original = Path.read_text

        def change_pidfile(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == self.proc / "100/smaps_rollup":
                self.write(mappings["a"].pid_file, "101\n")
            return value

        with patch.object(Path, "read_text", change_pidfile):
            row = sampler.sample(mappings)
        self.assertFalse(row["eligible"])
        self.assertFalse(row["identity_continuity_valid"])
        self.assertEqual(row["engines"]["a"]["pid_file_value_before"], 100)
        self.assertEqual(row["engines"]["a"]["pid_file_value_after"], 101)
        self.assertEqual(row["engines"]["a"]["errors"]["identity"], "PidFileChangedDuringRead")
        self.write(mappings["a"].pid_file, "100\n")
        self.assertFalse(sampler.sample(mappings)["eligible"])

    def test_membership_change_within_owned_subtree_during_read_is_rejected(self):
        mappings = {"a": self.mapping(0, self.process(100, group="/task.scope/one"))}
        sampler = self.sampler(mappings)
        original = Path.read_text

        def move_process(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == self.proc / "100/smaps_rollup":
                self.write(self.proc / "100/cgroup", "0::/task.scope/two\n")
            return value

        with patch.object(Path, "read_text", move_process):
            row = sampler.sample(mappings)
        self.assertFalse(row["eligible"])
        self.assertFalse(row["membership_continuity_valid"])
        self.assertEqual(row["processes"][0]["cgroup_before"], "/task.scope/one")
        self.assertEqual(row["processes"][0]["cgroup_after"], "/task.scope/two")
        self.assertEqual(row["engines"]["a"]["errors"]["membership"], "MembershipChangedDuringRead")
        self.assertIsNone(row["pss_bytes"])
        self.assertFalse(sampler.sample(mappings)["eligible"])

    def test_membership_change_between_samples_is_rejected(self):
        mappings = {"a": self.mapping(0, self.process(100))}
        sampler = self.sampler(mappings)
        self.assertTrue(sampler.sample(mappings)["eligible"])
        self.write(self.proc / "100/cgroup", "0::/task.scope/child\n")
        row = sampler.sample(mappings)
        self.assertFalse(row["eligible"])
        self.assertEqual(row["engines"]["a"]["errors"]["membership"], "MembershipChanged")

    def test_matching_before_stat_is_pinned_when_after_stat_fails(self):
        mappings = {"a": self.mapping(0, self.process(100))}
        sampler = self.sampler(mappings)
        original = Path.read_text

        def remove_stat(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == self.proc / "100/smaps_rollup":
                (self.proc / "100/stat").unlink()
            return value

        with patch.object(Path, "read_text", remove_stat):
            self.assertFalse(sampler.sample(mappings)["eligible"])
        mappings["a"] = self.mapping(0, self.process(100, start=99))
        row = sampler.sample(mappings)
        self.assertFalse(row["eligible"])
        self.assertEqual(row["engines"]["a"]["errors"]["identity"], "ProcessReplaced")

    def replace_group(self):
        old = self.group_root / "old.scope"
        self.group.rename(old)
        shutil.copytree(old, self.group)
        return old

    def test_cgroup_replacement_between_boundary_reads_is_sticky(self):
        mappings = {"a": self.mapping(0, self.process(100))}
        sampler = self.sampler(mappings)
        first = sampler.cgroup_snapshot()
        self.assertTrue(first["eligible"])
        self.assertEqual(first["directory"], str(self.group))
        self.replace_group()
        row = sampler.cgroup_snapshot()
        self.assertFalse(row["eligible"])
        self.assertFalse(row["identity_continuity_valid"])
        self.assertNotEqual(row["directory_identity"], first["directory_identity"])
        self.assertIsNone(row["cpu.stat"])
        self.assertFalse(sampler.sample(mappings)["eligible"])

    def test_cgroup_replacement_during_counter_read_discards_counters(self):
        original = Path.read_text
        replaced = False

        def replace_during_read(path, *args, **kwargs):
            nonlocal replaced
            value = original(path, *args, **kwargs)
            if path == self.group / "cpu.stat" and not replaced:
                self.replace_group()
                replaced = True
            return value

        with patch.object(Path, "read_text", replace_during_read):
            row = read_cgroup_snapshot(cgroup_root=self.group_root, expected_cgroup="/task.scope")
        self.assertFalse(row["eligible"])
        self.assertFalse(row["binding_valid"])
        self.assertNotEqual(row["directory_identity"], row["directory_identity_after"])
        self.assertIsNone(row["cpu.stat"])
        self.assertIsNone(row["memory.stat"])

    def test_cgroup_replacement_during_process_read_is_rejected(self):
        mappings = {"a": self.mapping(0, self.process(100))}
        sampler = self.sampler(mappings)
        original = Path.read_text

        def replace_during_read(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == self.proc / "100/smaps_rollup":
                self.replace_group()
            return value

        with patch.object(Path, "read_text", replace_during_read):
            row = sampler.sample(mappings)
        self.assertFalse(row["eligible"])
        self.assertNotEqual(row["cgroup"]["directory_identity_at_sample_start"],
                            row["cgroup"]["directory_identity"])
        self.assertIsNone(row["cgroup"]["cpu.stat"])

    def test_aliased_directory_is_rejected_without_reading_counter_files(self):
        # Simulate a resolved directory alias without requiring Windows symlink privileges.
        original_resolve, original_read = Path.resolve, Path.read_text
        reads = []

        def resolve_alias(path, *args, **kwargs):
            if path == self.group:
                return self.group_root / "unrelated.scope"
            return original_resolve(path, *args, **kwargs)

        def count_read(path, *args, **kwargs):
            reads.append(path)
            return original_read(path, *args, **kwargs)

        with patch.object(Path, "resolve", resolve_alias), patch.object(Path, "read_text", count_read):
            row = read_cgroup_snapshot(cgroup_root=self.group_root, expected_cgroup="/task.scope")
        self.assertFalse(row["eligible"])
        self.assertFalse(row["binding_valid"])
        self.assertIsNone(row["directory_identity"])
        self.assertEqual(reads, [])

    def test_free_cgroup_reader_checks_supplied_identity_and_derives_path(self):
        first = read_cgroup_snapshot(cgroup_root=self.group_root, expected_cgroup="/task.scope")
        self.replace_group()
        second = read_cgroup_snapshot(cgroup_root=self.group_root, expected_cgroup="/task.scope",
                                      expected_identity=first["directory_identity"])
        self.assertFalse(second["eligible"])
        with self.assertRaises(ValueError):
            read_cgroup_snapshot(cgroup_root=self.group_root, expected_cgroup="/task.scope/../other")


if __name__ == "__main__":
    unittest.main()
