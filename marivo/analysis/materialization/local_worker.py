"""Terminable private suffix worker with bounded IPC and parent-owned supervision."""

from __future__ import annotations

import os
import resource
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from multiprocessing import Pipe
from multiprocessing.connection import Connection
from pathlib import Path
from threading import Event, Thread

import pyarrow as pa

from marivo.analysis.datasets.descriptors import DatasetRowContract, DatasetRowSetContract
from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.materialization.errors import MaterializationError, RecoveryPendingError
from marivo.analysis.materialization.local import (
    LocalBudget,
    LocalPolicy,
    collect_primary,
    execute_suffix,
    fail,
    frame_to_arrow,
    to_local_frame,
)
from marivo.analysis.materialization.storage import ReadPolicy, _read
from marivo.analysis.operators.row import RowCall


@dataclass(frozen=True, slots=True, repr=False)
class StreamInput:
    row: DatasetRowContract
    rows: DatasetRowSetContract


@dataclass(frozen=True, slots=True, repr=False)
class ArtifactInput:
    project_root: Path
    receipt: LocalReceipt
    row: DatasetRowContract
    rows: DatasetRowSetContract


@dataclass(frozen=True, slots=True, repr=False)
class LocalRequest:
    input: StreamInput | ArtifactInput
    calls: tuple[RowCall, ...]
    policy: LocalPolicy
    deadline: float


@dataclass(frozen=True, slots=True, repr=False)
class LocalResult:
    table: pa.Table
    handoffs: tuple[tuple[int, int], ...]
    input_rows: int
    input_bytes: int
    peak_rss: int
    worker_pid: int


@dataclass(frozen=True, slots=True, repr=False)
class LocalFailure:
    expected: str
    received: str
    repair: str
    stage: str


def _batches(connection: Connection) -> Iterable[pa.RecordBatch]:
    while True:
        value: object = connection.recv()
        if value is None:
            return
        if not isinstance(value, pa.RecordBatch):
            fail("private Arrow input batches", "invalid worker input", "transfer_guard")
        yield value


def worker_entry() -> None:
    connection = Connection(int(sys.argv[1]))
    try:
        request: object = connection.recv()
        if not isinstance(request, LocalRequest) or not request.calls:
            fail(
                "one registered suffix request",
                "invalid worker request",
                "implementation_registration",
            )
        policy = request.policy
        policy.__post_init__()
        budget = LocalBudget(policy, request.deadline)
        selected = request.input
        if isinstance(selected, ArtifactInput):
            table = _read(
                project_root=selected.project_root,
                receipt=selected.receipt,
                row_contract=selected.row,
                row_set_contract=selected.rows,
                preview=False,
                policy=ReadPolicy(
                    max_rows=policy.max_input_rows,
                    max_decoded_bytes=min(policy.max_input_bytes, policy.max_intermediate_bytes),
                    max_batch_bytes=policy.max_batch_bytes,
                    deadline_seconds=max(0.001, request.deadline - time.monotonic()),
                ),
            )
            table = collect_primary(table.to_batches(), selected.row, selected.rows, budget)
        else:
            table = collect_primary(_batches(connection), selected.row, selected.rows, budget)
        frame = to_local_frame(table, selected.row, budget)
        schema = table.schema
        count, byte_count = table.num_rows, table.nbytes
        del table
        frame, handoffs = execute_suffix(frame, request.calls, budget)
        budget.allocation(int(frame.memory_usage(index=True, deep=True).sum()) * 2)
        result = frame_to_arrow(frame, request.calls[-1].output_row, schema)
        if result.nbytes > policy.max_output_bytes:
            fail("bounded decoded Arrow output", "local Arrow output overflow")
        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if sys.platform != "darwin":
            peak *= 1024
        if peak > policy.max_worker_rss:
            fail("bounded worker peak RSS", "worker memory overflow")
        budget.check()
        connection.send(LocalResult(result, handoffs, count, byte_count, peak, os.getpid()))
    except Exception as error:
        if isinstance(error, MaterializationError):
            failure = LocalFailure(
                error.expected or "valid local input",
                error.received or "local failure",
                error.hint or "Inspect the local input contract.",
                "transfer_guard" if error.stage in ("collection", "storage_read") else error.stage,
            )
        else:
            failure = LocalFailure(
                "a complete registered local calculation",
                "local worker failed",
                "Inspect the selected method and exact input contracts.",
                "stage_execution",
            )
        connection.send(failure)
    finally:
        connection.close()


_WORKER_CODE = (
    "from marivo.analysis.materialization.local_worker import worker_entry; worker_entry()"
)


def _rss(pid: int) -> int:
    value = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)],
        capture_output=True,
        timeout=0.5,
        check=False,
    )
    return int(value.stdout.strip() or b"0") * 1024


def supervise(
    request: LocalRequest,
    batches: Iterable[pa.RecordBatch],
    *,
    cancel_source: Callable[[], None],
    terminal: Callable[[], None],
    worker_code: str = _WORKER_CODE,
) -> LocalResult:
    """Run one suffix; prove worker and input feeder termination before discharge."""
    if os.name != "posix":
        fail("a registered POSIX terminable worker", "unsupported platform", "execution_boundary")
    parent, child = Pipe(duplex=True)
    process: subprocess.Popen[bytes] | None = None
    feeder: Thread | None = None
    receiver: Thread | None = None
    complete = Event()
    response: list[object] = []
    feed_errors: list[BaseException] = []
    try:
        environment = os.environ.copy()
        environment["MARIVO_TELEMETRY"] = "off"
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", worker_code, str(child.fileno())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(child.fileno(),),
            env=environment,
        )
        child.close()

        def feed() -> None:
            try:
                parent.send(request)
                if isinstance(request.input, StreamInput):
                    for batch in batches:
                        parent.send(batch)
                    parent.send(None)
            except BaseException as error:
                feed_errors.append(error)

        feeder = Thread(target=feed, name="marivo-local-input", daemon=True)
        feeder.start()

        # One reader and one writer use opposite directions of the duplex pipe.
        # Receive independently: a guard can reject an early batch, and a partial
        # response must not block the supervising deadline or RSS checks.
        def receive() -> None:
            try:
                response.append(parent.recv())
            except Exception:
                # Missing or malformed IPC is reported through the bounded error below.
                pass
            finally:
                complete.set()

        receiver = Thread(target=receive, name="marivo-local-result", daemon=True)
        receiver.start()
        while not complete.wait(0.02):
            if feed_errors and not isinstance(feed_errors[0], (BrokenPipeError, EOFError)):
                break
            if time.monotonic() > request.deadline:
                fail(
                    "complete transfer and calculation before deadline", "worker deadline exceeded"
                )
            if _rss(process.pid) > request.policy.max_worker_rss:
                fail("bounded worker RSS during calculation", "worker memory overflow")
        result = response[0] if response else None
        if result is None:
            if feed_errors:
                if isinstance(feed_errors[0], MaterializationError):
                    raise feed_errors[0]
                fail(
                    "a complete private Arrow transfer", "worker transfer failed", "transfer_guard"
                )
            fail(
                "one complete worker response", "worker exited without a result", "stage_execution"
            )
        process.wait(timeout=max(0.001, request.deadline - time.monotonic()))
        if process.returncode != 0 or time.monotonic() > request.deadline:
            fail("successful worker termination within deadline", "worker did not complete")
        if isinstance(result, LocalFailure):
            raise MaterializationError(
                expected=result.expected,
                received=result.received,
                repair=result.repair,
                stage=result.stage,
            )
        if not isinstance(result, LocalResult):
            fail("a closed complete local result", "invalid worker result")
        return result
    finally:
        child.close()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        parent.close()
        if feeder is not None and feeder.is_alive():
            cancel_source()
            feeder.join(timeout=1)
        if receiver is not None:
            receiver.join(timeout=1)
        if (feeder is not None and feeder.is_alive()) or (
            receiver is not None and receiver.is_alive()
        ):
            raise RecoveryPendingError(
                expected="worker and input/result transfer termination proof",
                received="input or result transfer termination is unresolved",
                repair="Restore execution termination proof before Session recovery.",
                stage="reconciliation",
            )
        if process is None or process.poll() is not None:
            terminal()
