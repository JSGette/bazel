"""Count scheduler records in two synthetic build shapes. Uses only Python's stdlib."""

import argparse
from collections import deque
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile


def requests(views, width):
    # Each view requests the first width leaves of a much larger namespace.
    # Each view changes the last leaf differently. An integer models its entire input.
    for index in range(1, width + 1):
        for view in views:
            yield view, index + (view if index == width else 0)


def evaluate(views, width, window, eager, db):
    source = requests(views, width)
    queue = deque(source) if eager else None
    remaining = len(views) * width
    sums = dict.fromkeys(views, 0)
    stats = dict(root_accumulators=len(views), requested_leaves=remaining, executions=0, joined=0,
                 result_lookups=0, result_hits=0, rounds=0,
                 peak_queued=remaining if eager else 0, peak_subscribers=0,
                 peak_flights=0, peak_queued_plus_subscribers=remaining if eager else 0)

    while remaining:
        # The window limits outstanding subscriptions, including duplicate waiters.
        # The complete operation key is (square-v1, input integer). The table
        # namespace fixes the operation version; only this exact pure function runs.
        flights = {}
        subscribers = 0
        for _ in range(min(window, remaining)):
            view, value = queue.popleft() if eager else next(source)
            if value in flights:
                flights[value].append(view)
                stats["joined"] += 1
                subscribers += 1
                continue
            if db is not None:
                stats["result_lookups"] += 1
                row = db.execute("SELECT result FROM square_v1 WHERE input=?", (value,)).fetchone()
                if row is not None:
                    sums[view] += row[0]
                    stats["result_hits"] += 1
                    continue
            flights[value] = [view]
            subscribers += 1
        remaining -= min(window, remaining)
        queued = len(queue) if eager else 0
        stats["peak_queued_plus_subscribers"] = max(stats["peak_queued_plus_subscribers"],
                                                    queued + subscribers)
        stats["peak_subscribers"] = max(stats["peak_subscribers"], subscribers)
        stats["peak_flights"] = max(stats["peak_flights"], len(flights))
        stats["rounds"] += 1
        for value, subscribers_for_value in flights.items():
            result = value * value
            if db is not None:
                db.execute("INSERT INTO square_v1 VALUES (?,?)", (value, result))
            stats["executions"] += 1
            for view in subscribers_for_value:
                sums[view] += result
        # Every in-flight record is discarded before discovery of the next window.
    if db is not None:
        db.commit()
    square_sum = width * (width + 1) * (2 * width + 1) // 6
    expected = {v: square_sum + 2 * width * v + v * v for v in views}
    if sums != expected:
        raise AssertionError((sums, expected))
    return {**stats, "result_sums": sums}


def fan_in(folder, count, width, window, eager, durable):
    path = folder / "results.sqlite"
    phases = {}
    for phase, views in (("overlap", range(count)), ("late_request", range(1))):
        if durable:
            # Reopen the file between phases. The caller retains no hot result map.
            with closing(sqlite3.connect(path)) as db:
                db.execute("PRAGMA cache_size=-256")
                db.execute("CREATE TABLE IF NOT EXISTS square_v1(input INTEGER PRIMARY KEY, result INTEGER)")
                phases[phase] = evaluate(views, width, window, eager, db)
                phases[phase]["result_rows_on_disk"] = db.execute("SELECT count(*) FROM square_v1").fetchone()[0]
        else:
            phases[phase] = evaluate(views, width, window, eager, None)
            phases[phase]["result_rows_on_disk"] = 0
    return {"requests": count, "width": width, "window": window, "discovery": "eager" if eager else "cursor",
            "retention": "sqlite" if durable else "in_flight_only", **phases}


def deep_chain(folder, depth, spill):
    # Each parent must resume after its only child. Model a generic return
    # continuation even though this particular recurrence admits a closed form.
    with closing(sqlite3.connect(folder / "continuations.sqlite")) as db:
        db.execute("PRAGMA cache_size=-256")
        db.execute("CREATE TABLE parked(frame INTEGER PRIMARY KEY)")
        stack = []
        for frame in range(1, depth + 1):
            if spill:
                db.execute("INSERT INTO parked VALUES (?)", (frame,))
            else:
                stack.append(frame)
        db.commit()
        on_disk = db.execute("SELECT count(*) FROM parked").fetchone()[0]
        in_stack = len(stack)
        value = 0
        for _ in range(depth):
            if spill:
                frame = db.execute("SELECT frame FROM parked ORDER BY frame DESC LIMIT 1").fetchone()[0]
                db.execute("DELETE FROM parked WHERE frame=?", (frame,))
            else:
                frame = stack.pop()
            value = frame - value
        db.commit()
    expected = (depth + 1) // 2 if depth % 2 else -(depth // 2)
    if value != expected:
        raise AssertionError((value, expected))
    return {"depth": depth, "storage": "sqlite" if spill else "stack",
            "expanded_parents": depth, "resumed_parents": depth, "active_limit": 1,
            "peak_parked_in_stack": in_stack, "peak_parked_rows_on_disk": on_disk,
            "stack_result": value}


def waiter_fanout(folder, count, spill):
    # One unfinished operation, a burst of callers, and one resume token per caller.
    with closing(sqlite3.connect(folder / "waiters.sqlite")) as db:
        db.execute("PRAGMA cache_size=-256")
        db.execute("CREATE TABLE waiters(subscriber INTEGER PRIMARY KEY)")
        waiters = []
        for subscriber in range(count):
            if spill:
                db.execute("INSERT INTO waiters VALUES (?)", (subscriber,))
            else:
                waiters.append(subscriber)
        db.commit()
        on_disk = db.execute("SELECT count(*) FROM waiters").fetchone()[0]
        waiting = len(waiters)
        shared_value = 100 * 100
        recipients = (row[0] for row in db.execute("SELECT subscriber FROM waiters")) if spill else iter(waiters)
        deliveries = 0
        checksum = 0
        for subscriber in recipients:
            deliveries += 1
            checksum += subscriber + shared_value
        if deliveries != count or checksum != count * shared_value + count * (count - 1) // 2:
            raise AssertionError((deliveries, checksum))
    return {"requests": count, "peak_active_operations": 1, "executions": 1,
            "peak_waiters_in_list": waiting, "peak_waiter_rows_on_disk": on_disk,
            "deliveries": deliveries, "checksum": checksum}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write results as JSON instead of stdout")
    parser.add_argument("--requests", type=int, default=8, help="Concurrent root requests in the fan-in fixture")
    parser.add_argument("--windows", type=int, nargs="+", default=[4, 16], help="Outstanding subscriptions per wave")
    args = parser.parse_args()
    if args.requests < 1 or min(args.windows) < 1:
        parser.error("requests and windows must all be positive")
    results = {"potential_leaf_names_per_view": 1_000_000_000,
               "scheduling": "deterministic interleaving, no parallel execution",
               "fan_in": [], "chain": [], "waiter_fanout": []}
    for width in (64, 4096):
        for window in args.windows:
            for eager, durable in ((True, True), (False, True), (False, False)):
                with tempfile.TemporaryDirectory() as tmp:
                    results["fan_in"].append(fan_in(Path(tmp), args.requests, width, window, eager, durable))
    for depth in (64, 4096):
        for spill in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                results["chain"].append(deep_chain(Path(tmp), depth, spill))
    for spill in (False, True):
        with tempfile.TemporaryDirectory() as tmp:
            results["waiter_fanout"].append(waiter_fanout(Path(tmp), 4096, spill))
    data = json.dumps(results, indent=2) + "\n"
    if args.output:
        args.output.write_text(data, encoding="utf-8")
    else:
        print(data, end="")


if __name__ == "__main__":
    main()
