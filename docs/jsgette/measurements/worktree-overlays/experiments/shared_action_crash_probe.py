"""Kill an active coordinator, verify restart publication and test an orphaned child."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

from shared_action import SharedActionServer, open_request


KEY = hashlib.sha256(b"crash-gated-action-v1").hexdigest()
COMPLETE = b"complete replacement result\n"


def wait_file(path, timeout=20):
    end = time.monotonic() + timeout
    while not path.is_file():
        if time.monotonic() >= end:
            raise TimeoutError(f"Timed out waiting for {path}")
        time.sleep(0.02)


def orphan(root, payload):
    (root / "ready").touch()
    wait_file(root / "restart-active")
    (root / "overlap").touch()
    wait_file(root / "release")
    (payload / "old-child-file").write_bytes(b"old producer")
    (root / "old-done").touch()


def failing_owner(root, external):
    def produce(key, payload):
        (payload / "partial").write_bytes(b"partial")
        if external:
            subprocess.run([sys.executable, "-B", __file__, "--writer", str(root), str(payload)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=True)
        else:
            (root / "ready").touch()
            threading.Event().wait(25)

    with SharedActionServer(root / "store", 1, produce) as server:
        (root / "endpoint").write_text(json.dumps((server.address, server.token)), encoding="utf-8")
        threading.Event().wait(30)


def probe(root, external):
    root.mkdir()
    store = root / "store"
    old = subprocess.Popen([sys.executable, "-B", __file__, "--owner", str(root), str(int(external))])
    try:
        wait_file(root / "endpoint")
        address, token = json.loads((root / "endpoint").read_text(encoding="utf-8"))
        with open_request(tuple(address), token, KEY) as client:
            wait_file(root / "ready")
            try:
                with SharedActionServer(store, 1, lambda *args: None):
                    raise AssertionError("Second writable coordinator entered the store")
            except sqlite3.OperationalError as exc:
                if exc.sqlite_errorcode != sqlite3.SQLITE_BUSY:
                    raise
            staged = [p for p in store.glob("stage-*/*/partial") if p.read_bytes() == b"partial"]
            assert len(staged) == 1 and not (store / "results" / KEY).exists()
            old.kill()
            old.wait(timeout=10)
            try:
                client.wait()
                raise AssertionError("A killed coordinator delivered a successful result")
            except (ConnectionError, OSError):
                pass
        assert staged[0].exists() and not (store / "results" / KEY).exists()

        def replacement(key, payload):
            (root / "restart-active").touch()
            if external:
                wait_file(root / "overlap")
            (payload / "complete").write_bytes(COMPLETE)

        with SharedActionServer(store, 1, replacement) as worker:
            with open_request(worker.address, worker.token, KEY) as subscriber:
                reply = subscriber.wait()
            assert reply["status"] == "ok"
            output = Path(reply["path"])
            assert output == store / "results" / KEY
            assert sorted(p.name for p in output.iterdir()) == ["complete"]
            assert (output / "complete").read_bytes() == COMPLETE
            counts = worker.snapshot()
            assert counts["starts"] == 1 and not counts["active"]
        (root / "release").touch()
        if external:
            wait_file(root / "old-done")
            assert (staged[0].parent / "old-child-file").exists()
        assert sorted(p.name for p in output.iterdir()) == ["complete"]
        with SharedActionServer(store, 1, lambda *args: None) as late:
            with open_request(late.address, late.token, KEY) as subscriber:
                assert subscriber.wait() == reply
            assert late.snapshot()["hits"] == 1 and late.snapshot()["starts"] == 0
        return dict(second_owner_sqlite_code=sqlite3.SQLITE_BUSY, staged_partial_files=len(staged),
                    published_at_crash=False, replacement_starts=counts["starts"],
                    replacement_sha256=hashlib.sha256(COMPLETE).hexdigest(), late_disk_hits=1,
                    old_external_child_ran_during_replacement=external)
    finally:
        (root / "restart-active").touch()
        (root / "release").touch()
        if old.poll() is None:
            old.kill()
            old.wait(timeout=10)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--owner", nargs=2)
    cli.add_argument("--writer", nargs=2)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    if args.owner:
        failing_owner(Path(args.owner[0]), bool(int(args.owner[1])))
    elif args.writer:
        orphan(*map(Path, args.writer))
    else:
        if not __debug__:
            cli.error("run without Python -O so verification assertions are active")
        with tempfile.TemporaryDirectory(prefix="broker-crash-") as temp:
            root = Path(temp)
            report = dict(host=platform.platform(), key=KEY,
                          in_process_producer=probe(root / "thread", False),
                          subprocess_producer=probe(root / "child", True))
        result = json.dumps(report, indent=2) + "\n"
        if args.output:
            args.output.write_text(result, encoding="utf-8")
        else:
            print(result, end="")


if __name__ == "__main__":
    main()
