"""Withdraw the first of four real Bazel subscribers during a direct compiler action."""

import json
import time

import psutil


def events(path):
    if not path.exists():
        return []
    # The worker appends newline-terminated records. A concurrent last append
    # can be incomplete while this separate process inspects the stream.
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.endswith("\n")]


def wait_for(read, seconds, description):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = read()
        if result is not None:
            return result
        time.sleep(.04)
    raise TimeoutError(description)


def withdraw(root, client_ids, clients, kill_tree):
    path = root / "events.jsonl"
    worker_pid = json.loads((root / "ready.json").read_text(encoding="utf-8"))["pid"]
    wanted = set(client_ids)

    def attached():
        entries = [e for e in events(path) if e.get("mnemonic") == "GoStdlib"]
        for admission in (e for e in entries if e["event"] == "admit"):
            group = [e for e in entries if e.get("operation") == admission["operation"]]
            if any(e["event"] in ("finish", "process_exit", "detach", "error") for e in group):
                continue
            joins = [e for e in group if e["event"] in ("admit", "join")]
            if {e["invocation"] for e in joins} != wanted or joins[-1]["subscribers"] != len(wanted):
                continue
            running = next((e for e in group if e["event"] == "running"), None)
            if running and psutil.pid_exists(running["pid"]):
                return admission, running, group
        return None

    admission, running, before = wait_for(attached, 420, "four live subscribers on one running GoStdlib")
    origin = client_ids.index(admission["invocation"])
    proc, compiler = clients[origin], psutil.Process(running["pid"])
    owners = psutil.Process(worker_pid).children(recursive=True)
    victims = psutil.Process(proc.pid).children(recursive=True)
    if compiler not in owners or compiler in victims or proc.poll() is not None:
        raise RuntimeError("Physical compiler must belong to the live worker and survive client termination")
    kill_tree(proc)

    def detached():
        return next((e for e in events(path) if e.get("operation") == admission["operation"]
                     and e["event"] == "detach" and e["invocation"] == admission["invocation"]
                     and e.get("subscribers") == len(client_ids) - 1), None)

    after = wait_for(detached, 12, "cancelled origin to detach with three remaining subscribers")
    if not compiler.is_running():
        raise RuntimeError("Original compiler was no longer running after origin detach")
    return {"origin": origin, "invocation": admission["invocation"], "operation": admission["operation"],
            "digest": admission["digest"], "worker_pid": worker_pid, "compiler_pid": compiler.pid,
            "killed_pids": [proc.pid, *(p.pid for p in victims)], "compiler_survived_detach": True,
            "before": before, "after": after}
