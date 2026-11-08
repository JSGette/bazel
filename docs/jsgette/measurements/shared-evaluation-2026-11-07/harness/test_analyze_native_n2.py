"""Offline checks for bound arithmetic and preserved unknowns; no Bazel/runtime."""
import copy
import unittest

import analyze_native_n2 as analysis


def sample():
    return {"eligible": True, "expected_engine_count": 2, "valid_engine_count": 2,
            "identity_continuity_valid": True, "membership_continuity_valid": True,
            "cgroup": {"eligible": True}, "pss_bytes": 400,
            "engines": {str(i): {"pid": 10, "start_ticks": 20, "errors": {}} for i in range(2)},
            "processes": [{"pid": 10, "start_ticks": 20, "pss_bytes": 400, "errors": {},
                           "engines": ["0", "1"]}],
            "sample_duration_ns": 1000}


def results():
    return [{"index": i, "exit_code": 0, "timed_out": False, "behavior": {"passed": True},
             "policy_verified": True, "digest_oracle": {"required": True, "matches": True},
             "started_ns": 10, "exited_ns": 1010, "output": {"sha256": "a" * 64},
             "bep_identity": {"index": i, "build_uuid": f"00000000-0000-0000-0000-{i:012d}",
                              "workspace": f"/worktree/{i}", "server_pid": 10,
                              "start_time_millis": 100 + i, "finish_time_millis": 200 + i}}
            for i in range(2)]


class AnalysisTests(unittest.TestCase):
    def test_ratio_uses_candidate_upper_and_baseline_lower(self):
        ratio = analysis.ratio_bounds([6, 8], [10, 12])
        self.assertEqual(ratio["status"], "known")
        self.assertEqual(ratio["bounds"], [0.5, 0.8])

    def test_ratio_preserves_unknown_invalid_and_zero_denominators(self):
        for candidate, baseline in ((None, [1, 2]), ([1, 2], [0, 2]), ([2, 1], [1, 2]),
                                    ([1, True], [1, 2]), ([1, float("nan")], [1, 2]),
                                    ([-1, 2], [1, 2]), ([1, 2], [None, None])):
            with self.subTest(candidate=candidate, baseline=baseline):
                self.assertIsNone(analysis.ratio_bounds(candidate, baseline)["bounds"])
        self.assertEqual(analysis.ratio_bounds([0, 0], [1, 2])["bounds"], [0, 0])

    def test_shared_pid_pss_is_counted_once_with_two_engine_mappings(self):
        result = analysis.sample_coverage([sample()], 2, "native")
        self.assertEqual(result["verified_peak_pss_bytes"], 400)
        self.assertEqual(result["complete_unique_process_samples"], 1)
        self.assertEqual(result["invalid_sample_indices"], [])
        for mutate in (lambda s: s["processes"].append(dict(s["processes"][0])),
                       lambda s: s.update(pss_bytes=800),
                       lambda s: s["processes"][0].update(pss_bytes=None),
                       lambda s: s.update(identity_continuity_valid=False)):
            invalid = sample()
            mutate(invalid)
            self.assertEqual(analysis.sample_coverage([invalid], 2, "native")["invalid_sample_indices"], [0])

    def test_pending_startup_is_visible_but_missing_after_complete_is_invalid(self):
        pending = sample()
        pending.update(eligible=False, valid_engine_count=0, processes=[], pss_bytes=None)
        pending["engines"] = {str(i): {"errors": {"mapping": "MissingEngine"}} for i in range(2)}
        result = analysis.sample_coverage([pending, sample()], 2, "native")
        self.assertEqual(result["pending_startup_samples"], 1)
        self.assertEqual(result["complete_unique_process_samples"], 1)
        self.assertEqual(result["invalid_sample_indices"], [])
        self.assertEqual(analysis.sample_coverage([sample(), pending], 2, "native")["invalid_sample_indices"], [1])

    def test_private_pss_rejects_unused_process_and_requires_one_to_one_engines(self):
        contradictory = sample()
        contradictory["processes"].append({"pid": 11, "start_ticks": 21, "pss_bytes": 600,
                                            "errors": {}, "engines": []})
        contradictory["pss_bytes"] = 1000
        rejected = analysis.sample_coverage([contradictory], 2, "private")
        self.assertEqual(rejected["invalid_sample_indices"], [0])
        self.assertIsNone(rejected["verified_peak_pss_bytes"])
        # Omitted reciprocal lists must not weaken identity-set equality.
        for process in contradictory["processes"]:
            del process["engines"]
        self.assertEqual(analysis.sample_coverage([contradictory], 2, "private")["invalid_sample_indices"], [0])
        private = copy.deepcopy(contradictory)
        private["engines"]["1"].update(pid=11, start_ticks=21)
        for index, process in enumerate(private["processes"]):
            process["engines"] = [str(index)]
        accepted = analysis.sample_coverage([private], 2, "private")
        self.assertEqual(accepted["invalid_sample_indices"], [])
        self.assertEqual(accepted["verified_peak_pss_bytes"], 1000)
        private["processes"][0]["engines"] = ["1"]
        self.assertEqual(analysis.sample_coverage([private], 2, "private")["invalid_sample_indices"], [0])

    def test_native_pss_requires_reciprocal_shared_engine_list_when_present(self):
        for names in (["0"], ["0", "0", "1"], ["0", "unknown"], None):
            invalid = sample()
            invalid["processes"][0]["engines"] = names
            with self.subTest(names=names):
                self.assertEqual(analysis.sample_coverage([invalid], 2, "native")["invalid_sample_indices"], [0])

    def test_pressure_missing_and_counter_reset_are_unknown_not_zero(self):
        before = {"eligible": True, "directory_identity": {"inode": 1},
                  "memory.events": {"max": 5}, "memory.pressure": {"some": {"total": 100}}}
        after = copy.deepcopy(before)
        after["memory.events"]["max"] = 7
        after["memory.pressure"]["some"]["total"] = 125
        result = analysis.pressure_summary(before, after)
        self.assertEqual(result["counter_deltas"]["memory.events"]["max"], 2)
        self.assertEqual(result["pressure"]["memory.pressure"]["some"]["total_stall_usec_delta"], 25)
        self.assertIsNone(result["pressure"]["memory.pressure"]["full"]["total_stall_usec_delta"])
        after["memory.events"]["max"] = 4
        self.assertIsNone(analysis.pressure_summary(before, after)["counter_deltas"]["memory.events"]["max"])

    def test_output_oracle_and_actual_overlap_required(self):
        self.assertEqual(analysis.result_proof(results(), 2, False)["all_clients_overlap_seconds"], 0.099)
        for change in (lambda rows: rows[1]["bep_identity"].update(build_uuid=rows[0]["bep_identity"]["build_uuid"]),
                       lambda rows: rows[1]["bep_identity"].update(start_time_millis=201, finish_time_millis=300),
                       lambda rows: rows[1]["digest_oracle"].update(matches=False)):
            rows = results()
            change(rows)
            with self.assertRaises(ValueError):
                analysis.result_proof(rows, 2, False)

    def test_bep_private_pids_must_match_each_engine_not_just_process_union(self):
        intervals = [row["bep_identity"] for row in results()]
        intervals[1]["server_pid"] = 11
        fixture_rows = {i: {"workspace": f"/worktree/{i}"} for i in range(2)}
        final = {"processes": [{"pid": 10}, {"pid": 11}],
                 "engines": {"0": {"pid": 10}, "1": {"pid": 11}}}
        analysis.validate_result_bindings(intervals, fixture_rows, final)
        swapped = copy.deepcopy(intervals)
        swapped[0]["server_pid"], swapped[1]["server_pid"] = 11, 10
        with self.assertRaises(ValueError):
            analysis.validate_result_bindings(swapped, fixture_rows, final)
        final["processes"].pop()
        with self.assertRaises(ValueError):
            analysis.validate_result_bindings(intervals, fixture_rows, final)

    def test_failed_or_missing_role_blocks_all_ratios(self):
        rows = [{"role": role, "diagnostic_verified": False} for role in analysis.ROLES]
        result = analysis.comparisons(rows)
        self.assertIsNotNone(result["blocked_reason"])
        self.assertNotIn("non_harness_cpu_native_over_private", result)


if __name__ == "__main__":
    unittest.main()
