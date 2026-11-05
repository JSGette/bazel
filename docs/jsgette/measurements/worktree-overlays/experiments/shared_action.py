"""Local experiment in coordinator-owned actions. Producers resolve trusted keys
against their own immutable inputs and write outputs into the supplied directory.
One service owns a private store. Completed entries survive service restarts.
"""

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sqlite3
import tempfile
import threading


KEY = re.compile(r"[0-9a-f]{64}")


def packet(message):
    return (json.dumps(message) + "\n").encode("utf-8")


class Subscription:
    """One connection is one subscriber. Closing it detaches only this caller."""

    def __init__(self, sock):
        self.sock = sock

    def wait(self):
        with self.sock.makefile("rb") as stream:
            line = stream.readline(65536)
        if not line.endswith(b"\n"):
            raise ConnectionError("Coordinator disconnected before sending a result")
        return json.loads(line)

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def open_request(address, token, key):
    sock = socket.create_connection(address, timeout=10)
    try:
        sock.settimeout(None)
        sock.sendall(packet({"token": token, "key": key}))
        return Subscription(sock)
    except BaseException:
        sock.close()
        raise


@dataclass
class Flight:
    task: asyncio.Task
    subscribers: int = 0


class SharedActionServer:
    def __init__(self, store, limit, produce, *, token=None):
        if limit < 1:
            raise ValueError("A positive producer limit is required")
        self.store = Path(store).resolve()
        self.results = self.store / "results"
        self.limit = limit
        self.produce = produce
        self.token = token or secrets.token_hex(32)
        self.flights = {}
        self.counts = dict(starts=0, joins=0, hits=0, busy=0, peak_active=0)

    def __enter__(self):
        self.results.mkdir(parents=True, exist_ok=True)
        self.ready = Future()
        self.thread = threading.Thread(target=self._run, name="action coordinator")
        self.thread.start()
        self.address = self.ready.result()
        return self

    def __exit__(self, *_):
        self.loop.call_soon_threadsafe(self.stopping.set)
        self.thread.join()

    def snapshot(self):
        answer = Future()

        def collect():
            answer.set_result({**self.counts,
                               "active": {k: f.subscribers for k, f in self.flights.items()}})

        self.loop.call_soon_threadsafe(collect)
        return answer.result(timeout=10)

    def _run(self):
        try:
            # The owner thread holds the lock through drain, even if the
            # caller is interrupted while waiting for shutdown.
            with closing(sqlite3.connect(self.store / "owner.sqlite", timeout=0)) as lock:
                lock.execute("BEGIN IMMEDIATE")
                with tempfile.TemporaryDirectory(prefix="stage-", dir=self.store) as scratch:
                    self.staging = Path(scratch)
                    with ThreadPoolExecutor(max_workers=self.limit) as self.pool:
                        asyncio.run(self._serve())
        except BaseException as exc:
            if self.ready.done():
                raise
            self.ready.set_exception(exc)

    async def _serve(self):
        self.loop = asyncio.get_running_loop()
        self.stopping = asyncio.Event()
        self.handlers = set()
        server = await asyncio.start_server(self._handle, "127.0.0.1", 0, limit=4096)
        async with server:
            self.ready.set_result(server.sockets[0].getsockname())
            await self.stopping.wait()
        # Admitted work continues through publication, including zero-subscriber work.
        await asyncio.gather(*(f.task for f in self.flights.values()))
        if self.handlers:
            await asyncio.gather(*self.handlers)

    def _produce(self, key, destination):
        # pathlib can intern path components. Reuse at most one staging name
        # per pool thread and keep high-cardinality result keys as plain strings.
        payload = self.staging / str(threading.get_ident())
        payload.mkdir()
        try:
            self.produce(key, payload)
            os.replace(payload, destination)
        except BaseException:
            shutil.rmtree(payload)
            raise

    async def _finish(self, key, destination):
        try:
            await self.loop.run_in_executor(self.pool, self._produce, key, destination)
            return {"status": "ok", "path": destination}
        except Exception as exc:
            return {"status": "error", "message": f"{type(exc).__name__}: {exc}"[:2048]}
        finally:
            del self.flights[key]

    async def _subscribe(self, key, reader):
        if self.stopping.is_set():
            return {"status": "busy"}
        flight = self.flights.get(key)
        if flight is None:
            destination = os.path.join(self.results, key)
            if os.path.isdir(destination):
                self.counts["hits"] += 1
                return {"status": "ok", "path": destination}
            if len(self.flights) >= self.limit:
                self.counts["busy"] += 1
                return {"status": "busy"}
            flight = Flight(asyncio.create_task(self._finish(key, destination)))
            self.flights[key] = flight
            self.counts["starts"] += 1
            self.counts["peak_active"] = max(self.counts["peak_active"], len(self.flights))
        else:
            self.counts["joins"] += 1
        flight.subscribers += 1
        disconnect = asyncio.create_task(reader.read(1))
        try:
            done, _ = await asyncio.wait((flight.task, disconnect),
                                         return_when=asyncio.FIRST_COMPLETED)
            if flight.task in done:
                return flight.task.result()
            return None
        finally:
            flight.subscribers -= 1
            disconnect.cancel()
            await asyncio.gather(disconnect, return_exceptions=True)

    async def _handle(self, reader, writer):
        handler = asyncio.current_task()
        self.handlers.add(handler)
        try:
            request = json.loads(await asyncio.wait_for(reader.readline(), timeout=5))
            token, key = request["token"], request["key"]
            if not hmac.compare_digest(token.encode(), self.token.encode()) or not KEY.fullmatch(key):
                reply = {"status": "error", "message": "Invalid credentials or action key"}
            else:
                reply = await self._subscribe(key, reader)
            if reply is not None:
                writer.write(packet(reply))
                await writer.drain()
        except (KeyError, AttributeError, TypeError, ValueError, OSError, TimeoutError):
            # Malformed input or a client that withdrew before delivery.
            pass
        finally:
            self.handlers.discard(handler)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
