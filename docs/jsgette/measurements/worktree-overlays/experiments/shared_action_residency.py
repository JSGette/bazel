"""Measure the owner's live Python allocations as history and fan-in grow."""

import argparse
import gc
import hashlib
import json
import multiprocessing
from pathlib import Path
import platform
import tempfile
import threading
import time
import tracemalloc

from shared_action import SharedActionServer, open_request


def key(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


GATED = key("hold for subscribers")


def serve(store, pipe):
    released = threading.Event()

    def produce(action, stage):
        if action == GATED and not released.wait(60):
            raise TimeoutError("fan-in gate expired")
        (stage / "result").write_text(action, encoding="ascii")

    tracemalloc.start()
    with SharedActionServer(store, 2, produce) as server:
        pipe.send((server.address, server.token))
        while True:
            command = pipe.recv()
            if command == "stop":
                released.set()
                break
            if command == "release":
                released.set()
            state = server.snapshot()
            gc.collect()
            state["python_traced_bytes"] = tracemalloc.get_traced_memory()[0]
            if command == "sites":
                largest = tracemalloc.take_snapshot().statistics("lineno")[:5]
                state["largest_allocation_sites"] = [
                    {"file": Path(s.traceback[0].filename).name, "line": s.traceback[0].lineno,
                     "bytes": s.size, "allocations": s.count} for s in largest
                ]
                del largest
            pipe.send(state)


def receive(pipe):
    if not pipe.poll(60):
        raise TimeoutError("owner did not respond")
    return pipe.recv()


def sample(pipe, command="sample"):
    pipe.send(command)
    return receive(pipe)


def verify(subscription, action):
    reply = subscription.wait()
    if reply["status"] != "ok" or (Path(reply["path"]) / "result").read_text() != action:
        raise AssertionError(reply)


def experiment(store, history, subscribers):
    # Spawn a fresh interpreter even on Linux so allocator history from the
    # client and its open sockets never enters the owner measurement.
    ctx = multiprocessing.get_context("spawn")
    report = dict(platform=platform.platform(), history=history, fan_in=subscribers)
    for restart in (False, True):
        pipe, child = ctx.Pipe()
        owner = ctx.Process(target=serve, args=(store, child))
        owner.start()
        child.close()
        subscriptions = []
        try:
            address, token = receive(pipe)
            if restart:
                report["restarted_with_history"] = sample(pipe)
                for action in (key(0), key(history-1), GATED):
                    with open_request(address, token, action) as sub:
                        verify(sub, action)
                report["three_reopened_results"] = sample(pipe)
                if report["three_reopened_results"]["hits"] != 3 or report["three_reopened_results"]["starts"]:
                    raise AssertionError("restarted owner reran previously published work")
                continue
            report["empty"] = sample(pipe)
            for i in range(history):
                action = key(i)
                with open_request(address, token, action) as sub:
                    verify(sub, action)
            report["after_history"] = sample(pipe)
            if report["after_history"]["active"] or report["after_history"]["starts"] != history:
                raise AssertionError("finished history still active or counts disagree")
            for _ in range(subscribers):
                subscriptions.append(open_request(address, token, GATED))
            deadline = time.monotonic() + 30
            while True:
                state = sample(pipe)
                if state["active"].get(GATED) == subscribers:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError("subscribers did not all reach the producer")
            report["while_attached"] = sample(pipe, "sites")
            sample(pipe, "release")
            for sub in subscriptions:
                verify(sub, GATED)
            report["after_fan_in"] = sample(pipe)
            if report["after_fan_in"]["joins"] != subscribers - 1 or report["after_fan_in"]["active"]:
                raise AssertionError("fan-in did not complete as one producer")
            for sub in subscriptions:
                sub.close()
            # Record an idle sample separately from delivery, when the peer may
            # still hold a socket buffer while its TCP close is in progress.
            time.sleep(.25)
            report["after_client_close"] = sample(pipe)
        finally:
            for sub in subscriptions:
                sub.close()
            pipe.send("stop")
            owner.join(30)
            if owner.is_alive():
                owner.kill()
                owner.join()
            pipe.close()
            if owner.exitcode != 0:
                raise RuntimeError(f"owner exited {owner.exitcode}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=int, default=2048)
    parser.add_argument("--subscribers", type=int, default=128)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.history < 2 or args.subscribers < 1:
        parser.error("history must be at least two and subscribers positive")
    with tempfile.TemporaryDirectory(prefix="action-residency-") as store:
        result = experiment(store, args.history, args.subscribers)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
