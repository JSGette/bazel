"""Emit ONE N2 diagnostic arm from copied C: evidence; never launch or inspect R.

Run on Windows after the candidate's build/acceptance and final guard succeed.
The output directory is new and contains exact command/manifest bytes plus a
five-file harness snapshot. Stage explicitly; run_auxiliary remains the launcher.
"""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile


E = Path(__file__).resolve().parents[1]
WORK = E.parents[4]
R = PurePosixPath("/mnt/shared-evaluation-20261107")
HELPERS = ("native_comparison.py", "m1_resources.py", "fixture.py", "baseline.py", "output_bases.py")
PINS = {
    "evidence/repository-cache-v7-seed.json": "f88890095d847d2832d5de4cc9565050a730d3423e4f057de543fd0d63415142",
    "evidence/m0-v7b-01/i10-ref-run.tar.gz.json": "603b2bf3c92e50710b53ab4103e0dae2e8dbd6012cb35df0aff41df8754e69a5",
    "evidence/m0-v7b-01/i10-ref-run.tar.gz": "30686e640a2de2d69c2d628e0aa9b11e3533a09bebb9ff91d6e92be23c63ef2b",
    "acceptance-metrics.md": "5476f3e1a536d8e4ca3d54760ad5fb31da8481a885ef045ebb46c8d63b2bb7b5",
}
M0_REPORT_SHA256 = "7e6b14e43b72e5a762b5956913c6c094999e4227912fa880bf43e87bb226de85"
SOURCE_COMMIT = "4862608d940b49293c68c014e413ecc9dc98aa8e"
SOURCE_TREE = "a28c113856ec48838041bba3b9591ab9ea67e4ac"
HISTORY = {"pool_run": 1, "preparations_per_engine": 1, "identity_calls_per_preparation": 2}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def c_file(value):
    path = Path(value).resolve(strict=True)
    require(path.drive.upper() == "C:" and path.is_file(), "Inputs must be explicit copied C: files")
    return path


def document(value):
    path = c_file(value)
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8-sig")), {"path": str(path), "sha256": sha(raw)}


def linux_path(value):
    path = PurePosixPath(value)
    require(path.is_absolute() and str(path) == value and ".." not in path.parts
            and "\\" not in value and "\x00" not in value, "Noncanonical Linux path")
    return path


def wsl_path(value):
    path = Path(value).resolve()
    require(path.drive.upper() == "C:", "Only C: copies are supported")
    return "/mnt/c/" + path.as_posix()[3:]


def successful_guard(guard):
    require(guard.get("status") == "completed" and guard.get("exit_code") == 0
            and guard.get("final_scope_cleanup", {}).get("verified") is True
            and guard.get("final_scope_cleanup", {}).get("signalled") is False
            and guard.get("final_scope_cleanup", {}).get("present") is False,
            "Successful final external guard with unsignalled absence proof is required")


def candidate_provenance(candidate, guard):
    """Validate copied build evidence; preserve base pins plus test-only qualification.

    This does not independently verify live source or binary bytes. The final
    guarded report is pinned separately, and the arm hashes its candidate again.
    """
    successful_guard(guard)
    require(candidate.get("status") == "build_and_focused_tests_passed_no_gate_verdict"
            and candidate.get("unchanged_admission_cap") == 2
            and candidate.get("source_commit") == SOURCE_COMMIT and candidate.get("source_tree") == SOURCE_TREE,
            "Candidate source/build/cap pin differs from accepted cap2 experiment")
    require(all(any(row.get("name") == name and row.get("returncode") == 0
                    for row in candidate["commands"]) for name in ("build", "tests", "shutdown")),
            "Candidate requires successful build, focused tests and shutdown")
    require(candidate["scope_before"]["name"] == candidate["scope_after"]["name"]
            and candidate["scope_before"]["name"].endswith("/" + guard["scope"]["unit"]),
            "Candidate build/guard scope mismatch")
    root = linux_path(candidate["root"])
    path = linux_path(candidate["candidate"]["destination"])
    require(root.is_relative_to(R) and root != R and path == root / "candidate/bazel-native-cap2",
            "Unexpected candidate location")
    for value in (candidate["candidate"]["sha256"], candidate["source_archive_sha256"]):
        require(re.fullmatch("[0-9a-f]{64}", value), "Candidate digest missing")
    binary = {"path": str(path), "sha256": candidate["candidate"]["sha256"],
              "source_commit": SOURCE_COMMIT, "source_tree_sha256": candidate["source_archive_sha256"],
              "source_tree_sha256_kind": "git-archive-tar-bytes", "source_git_tree_sha1": SOURCE_TREE}
    patched = candidate.get("source_is_base_plus_qualification_test_patch", False)
    require(type(patched) is bool, "Qualification patch declaration must be boolean")
    patch = candidate.get("qualification_test_patch")
    require((patched and isinstance(patch, dict)) or (not patched and patch is None),
            "Qualification patch must be explicitly declared and described")
    if not patched:
        return binary, None

    require(patch.get("path") == "src/test/shell/integration/shared_server_test.sh"
            and patch.get("base_source_fields_exclude_this_patch") is True,
            "Qualification patch must identify the test and exclude itself from base source pins")
    for key in ("old_sha256", "new_sha256"):
        require(isinstance(patch.get(key), str) and re.fullmatch("[0-9a-f]{64}", patch[key]),
                "Qualification test digest missing")
    require(patch["old_sha256"] != patch["new_sha256"], "Qualification patch did not change the test")
    require(candidate.get("product_binary_sha256_before") == candidate.get("product_binary_sha256_after")
            == binary["sha256"], "Qualification changed the product binary or lacks unchanged-binary proof")
    state = linux_path(candidate["build_state_root"])
    prior = candidate["prior_report"]
    require(state.is_relative_to(R) and state != R and state != root
            and linux_path(prior["path"]) == state / "report.json"
            and re.fullmatch("[0-9a-f]{64}", prior["sha256"]), "Qualification prior-report lineage missing")
    before, after = (candidate.get(key, {}) for key in
                     ("base_source_verification", "qualified_source_verification"))
    for record in (before, after):
        require(type(record.get("members_verified")) is int and record["members_verified"] > 0
                and type(record.get("archived_bytes")) is int and record["archived_bytes"] > 0
                and isinstance(record.get("tracked_content_sha256"), str)
                and re.fullmatch("[0-9a-f]{64}", record["tracked_content_sha256"]),
                "Qualification source verification missing")
    require(all(before[key] == after[key] for key in ("members_verified", "archived_bytes"))
            and before["tracked_content_sha256"] != after["tracked_content_sha256"],
            "Qualification source verification coverage changed or patch was not observed")
    for key in ("launcher_sha256", "build_helper_sha256"):
        require(isinstance(candidate.get(key), str) and re.fullmatch("[0-9a-f]{64}", candidate[key]),
                "Qualification helper pin missing")
    binary.update(source_tree_sha256_kind="base-git-archive-tar-bytes",
                  source_is_base_plus_qualification_test_patch=True,
                  qualification_patch=dict(patch), product_binary_unchanged_by_qualification=True)
    provenance = {key: candidate[key] for key in
                  ("build_state_root", "prior_report", "base_source_verification", "qualified_source_verification",
                   "product_binary_sha256_before", "product_binary_sha256_after", "launcher_sha256", "build_helper_sha256")}
    return binary, provenance


def archived_inputs():
    for name, expected in PINS.items():
        require(sha(c_file(E / name).read_bytes()) == expected, "Archived pin changed: " + name)
    seed, _ = document(E / "evidence/repository-cache-v7-seed.json")
    metadata, _ = document(E / "evidence/m0-v7b-01/i10-ref-run.tar.gz.json")
    require(metadata["passed"] and metadata["run_state"] == "completed", "M0 reference was incomplete")
    with tarfile.open(E / "evidence/m0-v7b-01/i10-ref-run.tar.gz") as archive:
        members = [entry for entry in archive.getmembers() if entry.name == "report.json"]
        require(len(members) == 1 and members[0].isfile() and members[0].size <= 2 * 1024**2,
                "Expected one bounded archived report")
        raw = archive.extractfile(members[0]).read()
    require(sha(raw) == M0_REPORT_SHA256, "Archived M0 report changed")
    fixture = json.loads(raw)["fixture"]
    expected = {key: fixture[key] for key in
                ("source_revision", "projection_archive_sha256", "private_tree", "target")}
    expected["inputs"] = {key: fixture["worktrees"][0][key] for key in
                          ("variant", "behavior_variant", "compilation_mode", "source_sha256", "build_sha256", "extra_targets")}
    require(seed["verified"] and seed["files"] == len(seed["members"]) == 1458,
            "Archived download seed is incomplete")
    files = {}
    for member in seed["members"]:
        name = "content_addressable/" + member["path"]
        relative = PurePosixPath(name)
        require(not relative.is_absolute() and str(relative) == name and ".." not in relative.parts
                and "\\" not in name and name not in files
                and re.fullmatch("[0-9a-f]{64}", member["sha256"]), "Invalid archived seed member")
        files[name] = member["sha256"]
    return expected, {"root": str(R / "repository-cache-v7"), "files": files}


def check_fixture(report, expected):
    actual = report["fixture"]
    require(all(actual.get(key) == value for key, value in expected.items() if key != "inputs"),
            "Reference differs from the C-archived M0 fixture")
    require(actual["clients"] == len(actual["worktrees"]) == 2 and actual["scenario"] == "identical",
            "Expected exactly two identical reference inputs")
    for row in actual["worktrees"]:
        require(all(row.get(key) == value for key, value in expected["inputs"].items()),
                "Reference source/BUILD/configuration variant differs from M0")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("reference", "private", "native"), required=True)
    parser.add_argument("--series", required=True, help="Fresh R child and RunId prefix, e.g. native-n2-probe01")
    parser.add_argument("--source-repository", required=True, help="Canonical Linux path to original Datadog Git repository")
    parser.add_argument("--candidate-report", required=True, type=Path,
                        help="Copied completed native_source_build or requalify_native_cap2 report")
    parser.add_argument("--candidate-guard-result", required=True, type=Path)
    parser.add_argument("--harness-source", type=Path, default=E / "harness")
    parser.add_argument("--reference-report", type=Path)
    parser.add_argument("--reference-guard-result", type=Path)
    parser.add_argument("--reference-manifest", type=Path, help="Exact C manifest emitted for this series' reference")
    parser.add_argument("--output", required=True, type=Path, help="New C: directory below work/")
    args = parser.parse_args()
    require(Path.cwd().drive.upper() == "C:", "This preparation tool only runs against C: copies")
    require(re.fullmatch("[A-Za-z0-9][A-Za-z0-9-]{0,31}", args.series), "Invalid fresh series name")
    output = args.output.resolve()
    require(output.is_relative_to(WORK) and output != WORK and not output.exists(),
            "Output must be a NEW directory below work/")
    source = linux_path(args.source_repository)
    expected_fixture, seed = archived_inputs()
    candidate, candidate_proof = document(args.candidate_report)
    candidate_guard, candidate_guard_proof = document(args.candidate_guard_result)
    binary, qualification = candidate_provenance(candidate, candidate_guard)
    bundle = {name: c_file(args.harness_source / name).read_bytes() for name in HELPERS}
    hashes = {name: sha(raw) for name, raw in bundle.items()}
    task_root = R / args.series
    role_name = "ref" if args.role == "reference" else args.role
    run_id = args.series + "-" + role_name
    run_root = task_root / role_name
    config_path = task_root / "config" / (role_name + ".json")
    guard_evidence = PurePosixPath(wsl_path(E)) / "evidence/auxiliary" / run_id / "guard"
    argv = ["python3", "-B", str(task_root / "harness/native_comparison.py"), "--manifest", str(config_path)]
    if args.role == "reference":
        argv.append("--reference-only")
    command = encoded(argv)  # No manifest hash in argv: command bytes are known first.
    config = {
        "schema_version": 1, "task_root": str(task_root), "run_root": str(run_root),
        "hosting": "native" if args.role == "native" else "private", "clients": 2,
        "state": "restart", "scenario": "identical", "shared_name": run_id,
        "source_repository": str(source), "repository_seed": seed,
        "binary": binary,
        "guard_evidence": str(guard_evidence), "guard_command_sha256": sha(command), "launch_argv": argv,
        "host_preflight": {"path": str(guard_evidence.parent / "host-budget-preflight.json")},
        "sample_interval_seconds": 0.5, "timeout_seconds": 1800, "history": HISTORY,
        "preparation_provenance": {"candidate_report": candidate_proof, "candidate_guard": candidate_guard_proof,
            "archived_input_sha256": PINS, "archived_m0_report_sha256": M0_REPORT_SHA256,
            "expected_fixture": expected_fixture, "harness_sha256": hashes,
            "scope": "N2 diagnostic only; no acceptance/performance gate verdict"},
    }
    if qualification is not None:
        config["preparation_provenance"]["candidate_requalification"] = qualification
    refs = (args.reference_report, args.reference_guard_result, args.reference_manifest)
    if args.role == "reference":
        require(not any(refs), "Reference creation cannot consume an oracle")
    else:
        require(all(refs), "Comparison requires copied report, final guard and exact reference manifest")
        reference, reference_proof = document(args.reference_report)
        guard, guard_proof = document(args.reference_guard_result)
        guard_policy, guard_policy_proof = document(args.reference_guard_result.parent / "policy.json")
        reference_config, _ = document(args.reference_manifest)
        successful_guard(guard)
        require(reference.get("passed") is True and reference.get("reference_only") is True
                and reference.get("performance_eligible") is False and reference.get("hosting") == "private"
                and reference.get("owned_daemons_absent") is True and reference.get("source_and_binary_unchanged") is True
                and reference.get("binary_sha256") == config["binary"]["sha256"]
                and reference.get("run_root") == str(task_root / "ref"), "Reference is not a completed matching private oracle")
        require(reference["manifest_sha256"] == sha(encoded(reference_config)), "Copied reference manifest differs from report")
        require(guard["scope"]["unit"] == reference["scope"]["identity"]["Id"]
                and guard["scope"]["invocation_id"] == reference["scope"]["identity"]["InvocationID"]
                and guard_policy["scope"]["unit"] == guard["scope"]["unit"]
                and guard_policy["command_sha256"] == reference_config["guard_command_sha256"]
                and guard_policy_proof["sha256"] == reference["scope"]["guard_policy_sha256"],
                "Reference guard identity/command differs")
        for key in ("binary", "source_repository", "repository_seed", "sample_interval_seconds", "history", "state", "scenario", "clients"):
            require(reference_config[key] == config[key], "Reference contract changed: " + key)
        require(reference_config["preparation_provenance"]["harness_sha256"] == hashes,
                "Comparison must use the exact reference harness bundle")
        require(all(reference["harness_sha256"].get(name) == value for name, value in hashes.items()
                    if name != "output_bases.py"), "Reported reference helper bytes differ")
        check_fixture(reference, expected_fixture)
        config["oracle_report"] = {"path": str(task_root / "ref/report.json"), "sha256": reference_proof["sha256"],
            "guard_result": {"path": str(guard_evidence.parent.parent / (args.series + "-ref") / "guard/result.json"),
                             "sha256": guard_proof["sha256"]}}
        config["preparation_provenance"]["reference_copies"] = [reference_proof, guard_proof, guard_policy_proof]
    # All evidence validation precedes the first write. No R path is opened.
    output.mkdir()
    (output / "harness").mkdir()
    for name, raw in bundle.items():
        (output / "harness" / name).write_bytes(raw)
    (output / "command.json").write_bytes(command)
    (output / "manifest.json").write_bytes(encoded(config))
    (output / "stage.json").write_bytes(encoded({"task_root": str(task_root), "config_path": str(config_path),
        "run_id": run_id, "role": args.role, "manifest_sha256": sha(encoded(config)), "harness_sha256": hashes,
        "command_sha256": sha(command), "outer_guard_timeout_seconds": 7200,
        "note": "C-only preparation. No arm launched; root must accept candidate and review before staging."}))
    print(json.dumps({"output": str(output), "run_id": run_id, "manifest_sha256": sha(encoded(config)),
                      "command_sha256": sha(command), "launched": False}))


if __name__ == "__main__":
    main()
