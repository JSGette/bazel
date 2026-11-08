"""C-only preparation of ONE fixed N10 restart diagnostic; never launch/read R.

Requires the successful pinned cap10 build and complete archived cap10 acceptance
before even emitting a private reference. The historical N2 emitter is unchanged.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

import prepare_native_n2 as n2


E, WORK, R = n2.E, n2.WORK, n2.R
require, sha, encoded = n2.require, n2.sha, n2.encoded
document, linux_path, successful_guard = n2.document, n2.linux_path, n2.successful_guard
Q = R / "native-cap10-build-v1"
S = R / "m1-native-build-v2"
Q2 = R / "native-cap2-requalification-v1"
BASE_ARCHIVE = "a451b8353a29f92cb852785ff57ce5143b8a7ca37d561428926bee722353e18d"
PRIOR_REPORT = "b92e756bdea41da0c30eb475a9cb7d347cd43eea3d13b044f539589e01e2f8f9"
OLD_BINARY = "b7071363aba091f922b91d73dc1f8db4169dd791c164aa10fe58f168a89e689e"
CANDIDATE_SHA256 = "8b9195a536c4718cad937bbe70045db958f50b2dd43efb7aa742f91e284cb5d0"
CANDIDATE_REPORT_SHA256 = "c58633b5467037d073733ef02b195696b9bc64f47aff46bac3daf23d46ac2e81"
CANDIDATE_BYTES = 181505965
TEST_PATH = "src/test/shell/integration/shared_server_test.sh"
OLD_TEST = "905ed0efafc77775a50fe832c5cfe70cce809b7e5788ad28820afcb2392e44ee"
NEW_TEST = "575367272b56e63e92fdd7f56605b1764fc6085e51bd1802ebe1099fe13230c5"
PRODUCT_COMMIT = "5c2d5bc56edbba9bdba7cc797658d2facb4d661e"
PRODUCT_PARENT = "9526272f1a13ca5de8dbed33b1d22824bcf0e957"
PRODUCT_TREE = "c94711de5a859024631bf7e2685ac1300fe7aaf6"
PRODUCT_PATCH = "05eeccdc12ae4dfe08d350688dd4b4bc2701fe26a155cdda9f59faa422aa16c2"
PRODUCT_FILES = {
    "src/main/java/com/google/devtools/build/lib/server/CommandServer.java":
        ("4df666a7a92a3f2041b998889fd9ca66da84f7fca3b2df091069f0beeb030d70", "bf9d93b170a319df772e4841187bd6a030a9e85b740abd2026a1c46cb4bcf166", 43449, 43689),
    "src/main/java/com/google/devtools/build/lib/runtime/BlazeServerStartupOptions.java":
        ("4a62670aae20a454490d6a18d6236cf2bb2cd2073b05024db882d5e2dd886054", "bb4bce31cdb51ad191cba4ff81c68995244da9ff9187871774dabfcf2657703c", 31219, 31307),
    "src/test/java/com/google/devtools/build/lib/server/CommandServerTest.java":
        ("e24c226c20ef102642ff15d48077c765f424075b4efaff71dade2086e281df96", "2e190903cf355acdf0a1aaef3207effb69dbf60c24bf3b2d507da0e53d98d8d9", 70754, 74107),
    "tools/multiworktree/NATIVE.md":
        ("8e7b15ce6d19f1b26cc2c6b0cb97962027f2b3ceda8d8b9c9eedf0f920c4eef9", "4bd5bd8cbabbcf2fa33539ce896dd150ed3fda4619af1bbb231baf259db2c047", 4377, 5099),
}
HELPER_SHA256 = {
    "baseline.py": "f418157ada895e181f75c43cd4c7c05e69077598013b9097420f650fa6c6f906",
    "fixture.py": "2de503bc8d4b5e7e1817b5a73e9bb78c1d7bacc56f5ae19847b8f686ebaff78a",
    "m1_resources.py": "27a4f79e6542d62bd3f117398255ea21161446b510782ec2ba53552926778dc3",
    "native_comparison.py": "02c722fd3ac7b01139adc9f0b239ab603cbd016efa4a0ee34c47118f64b4f217",
    "output_bases.py": "d63dd9b7ba66cb88b5abcf914e835a8a2bf0bad9e6aa94f6203ab884caa3bbae",
}
ACCEPTANCE_HARNESS = R / "native-acceptance-harness-v2/native_integration_test.py"
ACCEPTANCE_HARNESS_SHA = "4e01ca597caea1d77543e0ec21e15f728305565245e746fcc5898a17df6cb7ef"
ACCEPTANCE_CHECKS = frozenset((
    "admitted workspaces report one server PID", "admitted engines have distinct owned output bases",
    "engine beyond configured admission bound rejected",
    "all admitted engines overlap at execution barrier and preserve outputs",
    "divergent builds overlap at execution barrier and preserve outputs",
    "identical action digest executes once and materializes twice", "warm A-B-A retains private incremental state",
    "shared listings preserve private glob additions, entry types, and restoration",
    "environment differences remain private and execute independently",
    "configuration differences remain private and execute independently",
    "two test commands overlap at execution barrier", "ordinary failure does not cancel another engine",
    "same-engine command lock rejects nonblocking competitor",
    "cancelled waiter leaves other consumer able to complete", "cancelled producer leaves other consumer able to complete",
    "engine shutdown leaves another engine running in same PID", "closed retained engine cannot reopen before daemon restart",
    "closed retained slot does not admit a replacement", "last engine shutdown terminates daemon",
    "fresh daemon admits previously closed workspace", "restarted daemon shutdown terminates daemon",
))


def digest_value(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value)


def candidate_provenance(report, guard):
    successful_guard(guard)
    require(report.get("status") == "build_and_focused_tests_passed_no_gate_verdict"
            and report.get("source_commit") == n2.SOURCE_COMMIT and report.get("source_tree") == n2.SOURCE_TREE
            and report.get("source_archive_sha256") == BASE_ARCHIVE
            and report.get("source_is_base_plus_qualification_test_and_cap10_patch") is True
            and report.get("admission_cap") == 10 and report.get("base_admission_cap") == 2,
            "Candidate base source/cap/composition contract differs")
    require(report.get("root") == str(Q) and report.get("build_state_root") == str(S)
            and report.get("prior_report") == {"path": str(Q2 / "report.json"), "sha256": PRIOR_REPORT},
            "Candidate source-state or prior-report lineage differs")
    require(report["scope_before"]["name"] == report["scope_after"]["name"]
            and report["scope_before"]["name"].endswith("/" + guard["scope"]["unit"]), "Candidate guard scope differs")
    for name in ("patch-check", "patch-apply", "build", "tests", "shutdown"):
        rows = [row for row in report["commands"] if row.get("name") == name]
        require(len(rows) == 1 and rows[0].get("returncode") == 0, "Candidate command incomplete/duplicated: " + name)
    patch = report["qualification_test_patch"]
    require((patch.get("path"), patch.get("old_sha256"), patch.get("new_sha256")) == (TEST_PATH, OLD_TEST, NEW_TEST)
            and patch.get("base_source_fields_exclude_this_patch") is True, "Inherited test-only patch differs")
    product = report["cap10_patch"]
    require((product.get("commit"), product.get("parent"), product.get("commit_tree"), product.get("sha256"))
            == (PRODUCT_COMMIT, PRODUCT_PARENT, PRODUCT_TREE, PRODUCT_PATCH), "Product patch identity differs")
    observed = {row["path"]: tuple(row[key] for key in ("old_sha256", "new_sha256", "old_bytes", "new_bytes"))
                for row in product["files"]}
    require(len(product["files"]) == len(observed) == 4 and observed == PRODUCT_FILES,
            "Product patch must contain exactly the four pinned files")
    before, after, final = (report[key] for key in ("source_before", "source_after_patch", "source_final"))
    for value in (before, after, final):
        require(type(value.get("members_verified")) is int and value["members_verified"] > 0
                and type(value.get("archived_bytes")) is int and value["archived_bytes"] > 0
                and digest_value(value.get("tracked_content_sha256"))
                and value.get("generated_extras_not_source_verified") is True, "Source verification incomplete")
    require(before["override_paths"] == [TEST_PATH] and after["override_paths"] == sorted([TEST_PATH, *PRODUCT_FILES])
            and after == final and before["tracked_content_sha256"] != after["tracked_content_sha256"]
            and all(before[key] == after[key] for key in ("members_verified", "archived_bytes")),
            "Source override coverage or final verification differs")
    candidate = report["candidate"]
    require(candidate["destination"] == str(Q / "candidate/bazel-native-cap10") and candidate["sha256"] == CANDIDATE_SHA256
            and type(candidate.get("bytes")) is int and candidate["bytes"] == CANDIDATE_BYTES
            and report.get("product_binary_sha256_before") == OLD_BINARY and candidate["sha256"] != OLD_BINARY
            and candidate["sha256"] == report.get("product_binary_sha256_after_build")
            == report.get("product_binary_sha256_after_tests"), "Cap10 candidate identity/qualification differs")
    helpers = report["helper_sha256"]
    require(len(helpers) == 3 and all(digest_value(value) for value in helpers.values())
            and {PurePosixPath(path).name for path in helpers} == {
                "build_native_cap10.py", "native_source_build.py", "requalify_native_cap2.py"}, "Build helper pins missing")
    binary = {"path": candidate["destination"], "sha256": candidate["sha256"], "source_commit": n2.SOURCE_COMMIT,
        "source_git_tree_sha1": n2.SOURCE_TREE, "source_tree_sha256": BASE_ARCHIVE,
        "source_tree_sha256_kind": "base-git-archive-tar-bytes", "admission_cap": 10, "base_admission_cap": 2,
        "source_is_base_plus_qualification_test_and_cap10_patch": True,
        "qualification_patch": patch, "cap10_patch": product,
        "verified_source_content_sha256": final["tracked_content_sha256"],
        "source_identity_note": "Base commit/tree/archive plus declared test and four-file product patches; full product commit tree is not the build tree"}
    return binary, {key: report[key] for key in ("build_state_root", "prior_report", "helper_sha256",
        "source_before", "source_after_patch", "source_final", "product_binary_sha256_before",
        "product_binary_sha256_after_build", "product_binary_sha256_after_tests")}


def validate_acceptance(records, hashes, candidate_report_sha, binary, run_id):
    """Validate copied coordinator/archive attestations, without querying any live scope."""
    result, manifest, control, guard, policy, identity, command = (records[key] for key in
        ("results.json", "runtime.tar.gz.json", "control.json", "guard/result.json", "guard/policy.json",
         "guard/scope-identity.json", "command.json"))
    successful_guard(guard)
    require(re.fullmatch(r"native-acceptance-cap10-[0-9]{2}", run_id), "Unexpected cap10 acceptance RunId")
    root = R / run_id
    expected_command = ["python3", "-B", str(ACCEPTANCE_HARNESS), "--bazel", binary["path"], "--root", str(root),
        "--expected-engine-cap", "10", "--heap-mib", "7680", "--processors", "2", "--tmp-root", str(root / "tmp")]
    require(command == expected_command and control.get("run_id") == run_id
            and control.get("status") == "completed_no_gate_verdict" and control.get("launcher_exit_code") == 0,
            "Acceptance coordinator/command did not complete the exact cap10 protocol")
    require(control.get("memory_max_mib") == policy.get("memory_max_mib") == 10240
            and control.get("cpu_quota_percent") == policy.get("cpu_quota_percent") == 600
            and policy.get("sample_interval_ms") == 500, "Acceptance resource guard policy differs")
    require(guard["scope"]["unit"] == identity["Id"] == policy["scope"]["unit"]
            and guard["scope"]["invocation_id"] == identity["InvocationID"]
            and re.fullmatch("shared-eval-guard-" + run_id + r"-[0-9a-f]{32}\.scope", identity["Id"])
            and re.fullmatch("[0-9a-f]{32}", identity["InvocationID"])
            and identity["ControlGroup"].endswith("/" + identity["Id"])
            and policy["argv"][-len(command):] == command, "Acceptance guard identity differs")
    for key, name in (("guard_result_sha256", "guard/result.json"), ("guard_policy_sha256", "guard/policy.json"),
                      ("scope_identity_sha256", "guard/scope-identity.json"), ("command_sha256", "command.json")):
        require(manifest[key] == hashes[name], "Acceptance archive binding differs: " + name)
    require(control["command_sha256"] == policy["command_sha256"] == hashes["command.json"], "Acceptance command SHA differs")
    require(manifest.get("passed") is True and manifest.get("candidate_report_sha256") == candidate_report_sha
            and manifest.get("candidate_sha256") == result.get("binary_sha256") == binary["sha256"]
            and manifest.get("harness_sha256") == result.get("harness_sha256") == ACCEPTANCE_HARNESS_SHA
            and all(manifest.get(key) is True for key in ("final_scope_absent", "final_scope_unsignalled", "recorded_daemons_absent")),
            "Acceptance archive lacks matching candidate/harness/final absence proof")
    require(result.get("success") is True and result.get("run_completed") is True
            and result.get("cleanup_errors") == [] and result.get("run_error") is None and result.get("missing_checks") == []
            and result.get("expected_engine_cap") == 10 and result.get("heap_mib") == 7680 and result.get("processors") == 2,
            "Full cap10 acceptance did not pass")
    for values in (result["checks"], result["required_checks"], manifest["required_checks"], manifest["completed_checks"]):
        require(len(values) == len(ACCEPTANCE_CHECKS) and set(values) == ACCEPTANCE_CHECKS, "Acceptance check set is incomplete")
    require(result["temporary_root"] == str(root / "tmp") and result["repository_cache"] == str(root / "repository-cache")
            and result["startup"][0] == binary["path"], "Acceptance input/storage binding differs")
    for flag in ("--output_user_root=" + str(root / "state"), "--host_jvm_args=-Xmx7680m", "--host_jvm_args=-XX:ActiveProcessorCount=2"):
        require(result["startup"].count(flag) == 1, "Acceptance startup policy differs: " + flag)
    names = ["a", "b", *("engine-%02d" % index for index in range(2, 10))]
    expected_bases = {name: str(root / "state/shared_servers/v3/native-acceptance/engines" /
        hashlib.md5(str(root / name).encode(), usedforsecurity=False).hexdigest()) for name in names}
    require(result["output_bases"] == expected_bases, "Acceptance engine/output-view binding differs")
    daemons = result["daemon_identities"]
    require(len(daemons) == 2 and all(len(row) == 2 and all(type(v) is int and v > 0 for v in row) for row in daemons)
            and daemons[0] != daemons[1] and result["server_pid"] == daemons[0][0], "Acceptance daemon lifecycle proof differs")
    return {"run_id": run_id, "candidate_sha256": binary["sha256"], "complete_checks": len(ACCEPTANCE_CHECKS),
            "input_sha256": hashes, "scope": guard["scope"], "final_scope_absent": True, "recorded_daemons_absent": True}


def acceptance_provenance(directory, candidate_report_sha, binary):
    records, hashes = {}, {}
    for name in ("results.json", "runtime.tar.gz.json", "control.json", "guard/result.json", "guard/policy.json",
                 "guard/scope-identity.json", "command.json"):
        records[name], proof = document(directory / name)
        hashes[name] = proof["sha256"]
    manifest = records["runtime.tar.gz.json"]
    archive_path = n2.c_file(directory / "runtime.tar.gz")
    require(archive_path.stat().st_size == manifest["archive_bytes"], "Acceptance archive size differs")
    with archive_path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    require(actual == manifest["archive_sha256"], "Acceptance archive SHA differs")
    hashes["runtime.tar.gz"] = actual
    entries = {row["path"]: row for row in manifest["members"]}
    require(len(entries) == len(manifest["members"]), "Duplicate acceptance archive members")
    with tarfile.open(archive_path) as archive:
        for name, expected, limit in (("results.json", hashes["results.json"], 2 * 1024**2),
                ("executed-harness.py", ACCEPTANCE_HARNESS_SHA, 64 * 1024)):
            matches = [m for m in archive.getmembers() if m.name == name]
            require(len(matches) == 1 and matches[0].isfile() and 0 < matches[0].size <= limit,
                    "Missing/oversized acceptance archive member: " + name)
            raw = archive.extractfile(matches[0]).read()
            require(sha(raw) == expected == entries[name]["sha256"] and len(raw) == entries[name]["bytes"],
                    "Acceptance selected member differs: " + name)
            source = R / directory.name / "results.json" if name == "results.json" else ACCEPTANCE_HARNESS
            require(entries[name]["source"] == str(source), "Acceptance archived source path differs: " + name)
    proof = validate_acceptance(records, hashes, candidate_report_sha, binary, directory.name)
    return {"directory": str(directory), **proof}


def check_fixture(report, expected):
    actual = report["fixture"]
    require(all(actual.get(key) == value for key, value in expected.items() if key != "inputs")
            and actual["clients"] == len(actual["worktrees"]) == 10 and actual["scenario"] == "identical",
            "Reference differs from C-archived M0 N10 fixture")
    require(sorted(row["index"] for row in actual["worktrees"]) == list(range(10)), "Reference engine indexes differ")
    require(all(all(row.get(key) == value for key, value in expected["inputs"].items()) for row in actual["worktrees"]),
            "Reference source/BUILD/configuration variant differs from M0")


def make_manifest(role, series, source, binary, seed, provenance, evidence_root):
    require(role in ("reference", "private", "native") and re.fullmatch("[A-Za-z0-9][A-Za-z0-9-]{0,31}", series), "Invalid role/series")
    task = R / series
    name = "ref" if role == "reference" else role
    run_id, config_path = series + "-" + name, task / "config" / (name + ".json")
    guard = linux_path(evidence_root) / "evidence/auxiliary" / run_id / "guard"
    argv = ["python3", "-B", str(task / "harness/native_comparison.py"), "--manifest", str(config_path)]
    if role == "reference":
        argv.append("--reference-only")
    command = encoded(argv)
    config = {"schema_version": 1, "task_root": str(task), "run_root": str(task / name),
        "hosting": "native" if role == "native" else "private", "clients": 10, "state": "restart", "scenario": "identical",
        "shared_name": run_id, "source_repository": str(linux_path(source)), "binary": binary, "repository_seed": seed,
        "guard_evidence": str(guard), "guard_command_sha256": sha(command), "launch_argv": argv,
        "host_preflight": {"path": str(guard.parent / "host-budget-preflight.json")},
        "sample_interval_seconds": 0.5, "timeout_seconds": 1800, "history": n2.HISTORY, "preparation_provenance": provenance}
    return config, command, {"task_root": str(task), "config_path": str(config_path), "run_id": run_id, "role": role,
        "harness_sha256": HELPER_SHA256, "command_sha256": sha(command), "outer_guard_timeout_seconds": 7200,
        "note": "Three fixed N10 restart diagnostics only; no arm launched and no performance/milestone verdict"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("reference", "private", "native"), required=True)
    parser.add_argument("--series", default="native-n10-probe01")
    parser.add_argument("--source-repository", required=True)
    parser.add_argument("--candidate-report", required=True, type=Path)
    parser.add_argument("--candidate-guard-result", required=True, type=Path)
    parser.add_argument("--acceptance-directory", required=True, type=Path)
    parser.add_argument("--harness-source", type=Path, default=E / "harness")
    parser.add_argument("--reference-report", type=Path)
    parser.add_argument("--reference-guard-result", type=Path)
    parser.add_argument("--reference-manifest", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    require(Path.cwd().drive.upper() == "C:", "Only C-backed preparation is supported")
    output = args.output.resolve()
    require(output.is_relative_to(WORK) and output != WORK and not output.exists(), "Output must be a NEW work/ directory")
    expected_fixture, seed = n2.archived_inputs()
    report, report_proof = document(args.candidate_report)
    require(report_proof["sha256"] == CANDIDATE_REPORT_SHA256, "Copied cap10 build report differs from frozen successful report")
    guard, guard_proof = document(args.candidate_guard_result)
    binary, build_proof = candidate_provenance(report, guard)
    acceptance = acceptance_provenance(args.acceptance_directory.resolve(strict=True), report_proof["sha256"], binary)
    bundle = {name: n2.c_file(args.harness_source / name).read_bytes() for name in n2.HELPERS}
    require({name: sha(raw) for name, raw in bundle.items()} == HELPER_SHA256, "Measurement helpers differ from frozen N2 bytes")
    provenance = {"candidate_report": report_proof, "candidate_guard": guard_proof, "candidate_build": build_proof,
        "full_cap10_acceptance": acceptance, "archived_input_sha256": n2.PINS, "archived_m0_report_sha256": n2.M0_REPORT_SHA256,
        "expected_fixture": expected_fixture, "harness_sha256": HELPER_SHA256,
        "fixed_resources": {"cpu_quota_percent": 600, "memory_max_mib": 10240, "memory_swap_max": 0,
                            "heap_per_private_engine_mib": 768, "native_heap_mib": 7680, "active_processor_count": 2},
        "scope": "N10 identical restart diagnostic only; no acceptance/performance gate verdict"}
    config, command, stage = make_manifest(args.role, args.series, args.source_repository, binary, seed, provenance, n2.wsl_path(E))
    refs = (args.reference_report, args.reference_guard_result, args.reference_manifest)
    if args.role == "reference":
        require(not any(refs), "Reference creation cannot consume an oracle")
    else:
        require(all(refs), "Comparison requires a new N10 reference report, final guard and manifest")
        reference, reference_proof = document(args.reference_report)
        reference_config, _ = document(args.reference_manifest)
        reference_guard, reference_guard_proof = document(args.reference_guard_result)
        reference_policy, policy_proof = document(args.reference_guard_result.parent / "policy.json")
        successful_guard(reference_guard)
        require(reference.get("passed") is True and reference.get("reference_only") is True
                and reference.get("performance_eligible") is False and reference.get("hosting") == "private"
                and reference.get("owned_daemons_absent") is True and reference.get("source_and_binary_unchanged") is True
                and reference.get("binary_sha256") == binary["sha256"]
                and reference.get("run_root") == str(R / args.series / "ref"), "Independent N10 oracle did not complete")
        require(reference["manifest_sha256"] == sha(encoded(reference_config))
                and reference_guard["scope"]["unit"] == reference["scope"]["identity"]["Id"] == reference_policy["scope"]["unit"]
                and reference_guard["scope"]["invocation_id"] == reference["scope"]["identity"]["InvocationID"]
                and reference_policy["command_sha256"] == reference_config["guard_command_sha256"]
                and policy_proof["sha256"] == reference["scope"]["guard_policy_sha256"], "Reference guard/manifest binding differs")
        for key in ("binary", "source_repository", "repository_seed", "sample_interval_seconds", "history", "state", "scenario", "clients"):
            require(reference_config[key] == config[key], "Reference contract changed: " + key)
        require(reference_config["preparation_provenance"]["harness_sha256"] == HELPER_SHA256
                and reference_config["preparation_provenance"]["full_cap10_acceptance"] == acceptance
                and all(reference["harness_sha256"].get(name) == value for name, value in HELPER_SHA256.items()
                        if name != "output_bases.py"), "Reference helper/acceptance provenance differs")
        check_fixture(reference, expected_fixture)
        config["oracle_report"] = {"path": str(R / args.series / "ref/report.json"), "sha256": reference_proof["sha256"],
            "guard_result": {"path": str(PurePosixPath(config["guard_evidence"]).parent.parent / (args.series + "-ref") / "guard/result.json"),
                             "sha256": reference_guard_proof["sha256"]}}
        provenance["reference_copies"] = [reference_proof, reference_guard_proof, policy_proof]
    stage["manifest_sha256"] = sha(encoded(config))
    output.mkdir()
    (output / "harness").mkdir()
    for name, raw in bundle.items():
        (output / "harness" / name).write_bytes(raw)
    for name, raw in (("command.json", command), ("manifest.json", encoded(config)), ("stage.json", encoded(stage))):
        (output / name).write_bytes(raw)
    print(json.dumps({"output": str(output), "run_id": stage["run_id"], "manifest_sha256": stage["manifest_sha256"], "launched": False}))


if __name__ == "__main__":
    main()
