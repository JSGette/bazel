"""Pure validation/command tests; never launch Bazel or contact a cache."""

import json
from pathlib import Path
import tempfile
import unittest

import compare_datadog as subject


class DatadogComparisonTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output_base = self.root / "output-base"
        self.output_base.mkdir()

    def bep(self, stream):
        path = self.root / "events.jsonl"
        path.write_text("".join(json.dumps(event) + "\n" for event in stream))
        return path

    def successful_stream(self):
        stream = []
        for index, target in enumerate(subject.TARGETS):
            name = target.rsplit(":", 1)[1]
            output_name = f"cmd/{name}/{name}_/{name}"
            path = self.output_base / output_name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
            stream.extend([
                {"id": {"namedSet": {"id": str(index)}},
                 "namedSetOfFiles": {"files": [{"name": output_name, "uri": path.as_uri(),
                                                 "digest": subject.digest(path)}]}},
                {"id": {"targetCompleted": {"label": target, "configuration": {"id": "cfg"}}},
                 "completed": {"success": True, "outputGroup": [
                     {"name": "default", "fileSets": [{"id": str(index)}]}]}}])
        stream.append({"id": {"buildFinished": {}},
                       "finished": {"exitCode": {"name": "SUCCESS"}}, "lastMessage": True})
        return stream

    def test_all_arms_have_identical_controlled_execution_flags(self):
        for arm in subject.ARMS:
            with self.subTest(arm=arm):
                command = subject.command(Path("/binary"), arm, self.root, self.root / "run", [],
                                          self.root / "cache" if arm == "fork-on" else None)
                for flag in ("--batch", "--ignore_all_rc_files", "--host_jvm_args=-Xmx6g",
                             "--jobs=6", "--lockfile_mode=off", "--symlink_prefix=/",
                             "--remote_upload_local_results=false", "--remote_accept_cached",
                             "--remote_download_outputs=toplevel", "--remote_executor=",
                             "--bes_backend=", "--disk_cache=", "--experimental_remote_analysis_cache_mode=off"):
                    self.assertIn(flag, command)
                self.assertFalse(any(flag.startswith("--server_javabase") for flag in command))
                self.assertNotIn("--nobuild", command)
                self.assertEqual(command[command.index("--") + 1:], list(subject.TARGETS))

    def test_vanilla_has_no_fork_flags_and_same_binary_can_disable_cache(self):
        vanilla = subject.command(Path("/binary"), "vanilla", self.root, self.root / "v", [], None)
        disabled = subject.command(Path("/binary"), "fork-off", self.root, self.root / "o", [], None)
        self.assertFalse(any("skycache" in flag or "idle_retention" in flag for flag in vanilla))
        self.assertIn("--experimental_local_skycache=", disabled)
        self.assertIn("--experimental_remote_skycache=", disabled)

    def test_shared_repository_cache_is_explicit_and_same_for_every_arm(self):
        shared = Path("/shared-download-cache")
        for arm in subject.ARMS:
            command = subject.command(Path("/binary"), arm, self.root, self.root / arm, [], None, shared)
            self.assertEqual([flag for flag in command if flag.startswith("--repository_cache=")],
                             [f"--repository_cache={shared}"])
            self.assertIn(f"--repo_contents_cache={self.root / 'repository-contents'}", command)

    def test_canonical_replay_removes_old_controls_but_keeps_expanded_settings(self):
        options = [{"optionName": name, "combinedForm": flag, "optionValue": value}
                   for name, flag, value in (
                       ("config", "--config=release", "release"),
                       ("jobs", "--jobs=16", "16"),
                       ("credential_helper", "--credential_helper=private", "private"),
                       ("override_repository", "--override_repository=bazel_tools=/old", "bazel_tools=/old"),
                       ("//:release", "--//:release=true", "true"),
                       ("repo_env", "--repo_env=GOPROXY", "GOPROXY"),
                       ("host_linkopt", "--host_linkopt=-lstdc++", "-lstdc++"))]
        stream = [{"structuredCommandLine": {"commandLineLabel": "canonical", "sections": [
            {"sectionLabel": "command options", "optionList": {"option": options}}]}}]
        self.assertEqual(subject.canonical_flags(self.bep(stream)),
                         ["--//:release=true", "--repo_env=GOPROXY", "--host_linkopt=-lstdc++"])

    def test_canonical_replay_rejects_unreviewed_literal_environment_values(self):
        stream = [{"structuredCommandLine": {"commandLineLabel": "canonical", "sections": [
            {"sectionLabel": "command options", "optionList": {"option": [
                {"optionName": "repo_env", "optionValue": "PRIVATE=secret",
                 "combinedForm": "--repo_env=PRIVATE=secret"}]}}]}}]
        with self.assertRaisesRegex(ValueError, "unreviewed literal"):
            subject.canonical_flags(self.bep(stream))

    def test_oracle_hashes_all_six_downloaded_executables(self):
        result = subject.output_manifest(self.bep(self.successful_stream()), self.output_base)
        self.assertEqual(set(result["outputs"]), subject.PRIMARY_OUTPUTS)
        self.assertEqual(result["targets"], sorted(subject.TARGETS))

    def test_oracle_rejects_missing_target(self):
        stream = self.successful_stream()
        del stream[1]
        with self.assertRaisesRegex(ValueError, "all six targets"):
            subject.output_manifest(self.bep(stream), self.output_base)

    def test_oracle_rejects_analysis_only_and_unsuccessful_targets(self):
        stream = self.successful_stream()
        for event in stream:
            if "completed" in event:
                event["completed"]["success"] = False
        with self.assertRaisesRegex(ValueError, "successfully build"):
            subject.output_manifest(self.bep(stream), self.output_base)

    def test_oracle_rejects_non_downloaded_cas_uri(self):
        stream = self.successful_stream()
        stream[0]["namedSetOfFiles"]["files"][0]["uri"] = "bytestream://cache/blobs/hash/4"
        with self.assertRaisesRegex(ValueError, "not downloaded"):
            subject.output_manifest(self.bep(stream), self.output_base)

    def test_oracle_rejects_file_outside_output_base(self):
        stream = self.successful_stream()
        outside = self.root / "outside"
        outside.write_bytes(b"outside")
        stream[0]["namedSetOfFiles"]["files"][0]["uri"] = outside.as_uri()
        with self.assertRaisesRegex(ValueError, "escapes owned directory"):
            subject.output_manifest(self.bep(stream), self.output_base)

    def test_oracle_rejects_digest_mismatch(self):
        stream = self.successful_stream()
        stream[0]["namedSetOfFiles"]["files"][0]["digest"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "digest disagrees"):
            subject.output_manifest(self.bep(stream), self.output_base)

    def test_oracle_rejects_extra_default_output(self):
        stream = self.successful_stream()
        extra = self.output_base / "extra"
        extra.write_bytes(b"extra")
        stream[0]["namedSetOfFiles"]["files"].append({"name": "extra", "uri": extra.as_uri()})
        with self.assertRaisesRegex(ValueError, "default output set differs"):
            subject.output_manifest(self.bep(stream), self.output_base)

    def test_oracle_ignores_unrequested_file_sets_and_placeholder_completion(self):
        stream = self.successful_stream()
        stream.insert(0, {"id": {"targetCompleted": {"label": subject.TARGETS[0],
                                                    "configuration": {"id": "none"}}}, "aborted": {}})
        stream.insert(0, {"id": {"namedSet": {"id": "unrequested"}}, "namedSetOfFiles": {
            "files": [{"name": "outside-default-group", "uri": "bytestream://not-requested"}]}})
        result = subject.output_manifest(self.bep(stream), self.output_base)
        self.assertEqual(len(result["ignored_unconfigured_placeholders"]), 1)
        self.assertEqual(set(result["outputs"]), subject.PRIMARY_OUTPUTS)

    def test_oracle_rejects_duplicate_target_and_incomplete_bep(self):
        stream = self.successful_stream()
        stream.insert(2, stream[1])
        with self.assertRaisesRegex(ValueError, "duplicate configured"):
            subject.output_manifest(self.bep(stream), self.output_base)
        stream = self.successful_stream()
        stream[-1].pop("lastMessage")
        with self.assertRaisesRegex(ValueError, "lastMessage"):
            subject.output_manifest(self.bep(stream), self.output_base)

    def test_restored_consumer_may_publish_nothing_but_must_prove_hits(self):
        stats = {"failed": False, "published": False, "writes": 0, "reads": 7}
        self.assertEqual(subject.validate_skycache(stats, True, "CONFIGURED_TARGET: 5/9"), 5)
        with self.assertRaisesRegex(ValueError, "did not prove"):
            subject.validate_skycache(stats, True, "no hits")

    def test_producer_requires_publication_and_every_cache_run_requires_diagnostics(self):
        with self.assertRaisesRegex(ValueError, "producer"):
            subject.validate_skycache({"failed": False, "published": False}, False, "")
        with self.assertRaisesRegex(ValueError, "missing or report failure"):
            subject.validate_skycache({}, True, "CONFIGURED_TARGET: 5/9")


if __name__ == "__main__":
    unittest.main()
