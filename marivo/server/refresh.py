"""Request-owned subprocess execution and publication of endpoint references."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
import struct
import sys
from pathlib import Path
from uuid import uuid4

from marivo.server.endpoints import EndpointScripts, endpoint_lock, parse_reference, publish_current
from marivo.server.wire import HELP, STATUS, Budget, ServiceError


def _failure() -> ServiceError:
    return ServiceError(
        "refresh_failed",
        "The script worker did not return a complete result.",
        "refresh",
        "Inspect service logs and read the current reference before retrying.",
        HELP,
    )


def _worker_error(value: object) -> ServiceError:
    if isinstance(value, dict):
        for code in STATUS:
            if value.get("code") != code:
                continue
            message, location, repair, target = (
                value.get(name) for name in ("message", "location", "repair", "target")
            )
            if (
                isinstance(message, str)
                and isinstance(location, str)
                and isinstance(repair, str)
                and (target is None or isinstance(target, str))
            ):
                return ServiceError(code, message, location, repair, target)
    return _failure()


async def _receive(channel: socket.socket, size: int) -> bytes:
    loop = asyncio.get_running_loop()
    data = bytearray()
    while len(data) < size:
        chunk = await loop.sock_recv(channel, min(65536, size - len(data)))
        if not chunk:
            raise _failure()
        data.extend(chunk)
    return bytes(data)


def _kill(process: asyncio.subprocess.Process) -> None:
    # Subprocesses sharing the refresh's process group have the same lifetime.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)


class Refreshes:
    def __init__(self, root: Path, maximum: int) -> None:
        self.root = root
        self.scripts = EndpointScripts(root)
        self.maximum = maximum
        self.active = 0
        self.processes: set[asyncio.subprocess.Process] = set()
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        processes = list(self.processes)
        for process in processes:
            _kill(process)
        await asyncio.gather(*(process.wait() for process in processes))

    async def run(self, endpoint: str, budget: Budget) -> bytes:
        self.scripts.require(endpoint)
        with endpoint_lock(self.root, endpoint) as descriptor:
            if self.closed or self.active >= self.maximum:
                raise ServiceError(
                    "service_unavailable",
                    "No refresh execution slot is available.",
                    "refresh",
                    "Wait for an active refresh to finish; ordinary saved reads remain available.",
                    HELP,
                )
            self.active += 1
            try:
                return await self._run(endpoint, descriptor, budget)
            finally:
                self.active -= 1

    async def _run(self, endpoint: str, descriptor: int, budget: Budget) -> bytes:
        parent, child = socket.socketpair()
        parent.setblocking(False)
        process: asyncio.subprocess.Process | None = None
        env = dict(os.environ)
        env.pop("MARIVO_API_TOKEN", None)
        env["MARIVO_PROJECT_ROOT"] = str(self.root)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # A fresh cache prefix also prevents timestamp/size collisions in project helper .pyc files.
        env["PYTHONPYCACHEPREFIX"] = str(
            self.root / ".marivo" / "server" / (".bytecode-" + uuid4().hex)
        )
        spawn = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, sys.argv.pop(1)); import runpy; runpy.run_module('marivo.server.refresh_worker', run_name='__main__')",
                str(Path(__file__).resolve().parents[2]),
                str(self.root),
                endpoint,
                str(child.fileno()),
                str(budget.limits.mib),
                str(budget.limits.seconds),
                str(budget.deadline),
                pass_fds=(child.fileno(), descriptor),
                start_new_session=True,
                stdin=asyncio.subprocess.DEVNULL,
                # Logs go directly to service stderr. Descendants retaining this
                # descriptor must not make worker exit or cancellation await EOF.
                stdout=sys.stderr,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.root,
                env=env,
            )
        )
        try:
            try:
                process = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                process = await spawn
                raise
            self.processes.add(process)
            if self.closed:
                raise _failure()
            child.close()
            length = struct.unpack("!I", await _receive(parent, 4))[0]
            if length > 64 * 1024:
                raise _failure()
            header: object = json.loads(await _receive(parent, length))
            if not isinstance(header, dict):
                raise _failure()
            if "error" in header:
                raise _worker_error(header["error"])
            reference = parse_reference(header.get("reference"))
            size = header.get("size")
            if type(size) is not int or size < 0:
                raise _failure()
            if size > budget.limits.size:
                raise budget.limits.too_large("refresh")
            body = await _receive(parent, size)
            if await process.wait() != 0:
                raise _failure()
            _kill(process)
            budget.check()
            if self.closed:
                raise _failure()
            publish_current(self.root, endpoint, reference)
            return body
        except (OSError, ValueError):
            raise _failure() from None
        finally:
            child.close()
            # Close the lifetime channel first; the worker also watches for parent loss.
            parent.close()
            if process is not None:
                _kill(process)
                await process.wait()
                self.processes.discard(process)
