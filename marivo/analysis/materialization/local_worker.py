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
from threading import Event, Lock, Thread

import pyarrow as pa

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.datasets.descriptors import DatasetRowContract, DatasetRowSetContract
from marivo.analysis.materialization.contracts import LocalReceipt, RetainedPart
from marivo.analysis.materialization.errors import MaterializationError, RecoveryPendingError
from marivo.analysis.materialization.local import (
    LocalBudget,
    LocalPolicy,
    PartCollector,
    collect_part,
    collect_primary,
    execute_retained_suffix,
    fail,
    frame_to_arrow,
    to_local_frame,
    to_part_frame,
)
from marivo.analysis.materialization.retained import checked_component_batches
from marivo.analysis.materialization.storage import (
    ReadPolicy,
    _normalize_batch,
    _read,
    read_part_batches,
)
from marivo.analysis.materialization.worker_lifetime import (
    WorkerReservation,
    acquire_worker_lifetime,
    validate_worker_lifetime,
)
from marivo.analysis.operators.row import PartFrame, RowCall


@dataclass(frozen=True, slots=True, repr=False)
class StreamInput:
    row: DatasetRowContract
    rows: DatasetRowSetContract
    wide_parts: bool = False


@dataclass(frozen=True, slots=True, repr=False)
class LocalPartInput:
    role: str
    contract_id: str
    contract_version: int
    schema: pa.Schema
    keys: tuple[str, ...]
    receipt: LocalReceipt | None = None


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
    parts: tuple[LocalPartInput, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class LocalPartResult:
    role: str
    contract_id: str
    contract_version: int
    table: pa.Table


@dataclass(frozen=True, slots=True, repr=False)
class LocalResult:
    table: pa.Table
    handoffs: tuple[tuple[int, int], ...]
    input_rows: int
    input_bytes: int
    peak_rss: int
    worker_pid: int
    parts: tuple[LocalPartResult, ...] = ()


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


def _part_to_arrow(part: PartFrame) -> pa.Table:
    arrays: list[pa.Array | pa.ChunkedArray] = []
    for column in part.schema:
        values = part.frame[column.name]
        if pa.types.is_struct(column.type):
            names = [field.name for field in column.type]
            arrays.append(
                pa.array(
                    [dict(zip(names, value, strict=True)) for value in values], type=column.type
                )
            )
        else:
            arrays.append(pa.array(values, type=column.type, from_pandas=True, safe=True))
    return pa.Table.from_arrays(arrays, schema=part.schema)


def worker_entry() -> None:
    connection = Connection(int(sys.argv[1]))
    lifetime_fd = int(sys.argv[2])
    try:
        validate_worker_lifetime(lifetime_fd, Path(sys.argv[4]), sys.argv[3])
        request: object = connection.recv()
        if not isinstance(request, LocalRequest) or not request.calls:
            fail(
                "one registered suffix request",
                "invalid worker request",
                "implementation_registration",
            )
        policy = request.policy
        if len({part.role for part in request.parts}) != len(request.parts):
            fail("one input per selected retained role", "duplicate part input")
        policy.__post_init__()
        budget = LocalBudget(policy, request.deadline)
        selected = request.input
        read_policy = ReadPolicy(
            max_rows=policy.max_input_rows,
            max_decoded_bytes=min(policy.max_input_bytes, policy.max_intermediate_bytes),
            max_batch_bytes=policy.max_batch_bytes,
            deadline_seconds=max(0.001, request.deadline - time.monotonic()),
        )
        wide = isinstance(selected, StreamInput) and selected.wide_parts
        collectors = tuple(PartCollector(part.schema, part.keys, budget) for part in request.parts)
        if isinstance(selected, ArtifactInput):
            table = _read(
                project_root=selected.project_root,
                receipt=selected.receipt,
                row_contract=selected.row,
                row_set_contract=selected.rows,
                preview=False,
                policy=read_policy,
            )
            table = collect_primary(table.to_batches(), selected.row, selected.rows, budget)
        else:

            def primary_batches() -> Iterable[pa.RecordBatch]:
                for incoming in _batches(connection):
                    if wide:
                        incoming = _normalize_batch(incoming, policy.max_batch_bytes)
                        for part, collector in zip(request.parts, collectors, strict=True):
                            for part_batch in checked_component_batches(
                                (incoming.select(part.schema.names),), selected.row, part.role
                            ):
                                collector.accept(part_batch)
                        yield incoming.select([field.name for field in selected.row.schema.columns])
                    else:
                        yield incoming

            table = collect_primary(primary_batches(), selected.row, selected.rows, budget)
        frame = to_local_frame(table, selected.row, budget)
        schema = table.schema
        count = table.num_rows
        del table
        part_frames: list[PartFrame] = []
        for part, collector in zip(request.parts, collectors, strict=True):
            if wide:
                part_table = collector.finish()
            elif part.receipt is not None:
                if not isinstance(selected, ArtifactInput):
                    fail("a local project for retained part receipts", "invalid part input")
                incoming_part: Iterable[pa.RecordBatch] = read_part_batches(
                    selected.project_root,
                    RetainedPart(part.role, part.contract_id, part.contract_version, part.receipt),
                    expected_schema=part.schema,
                    policy=read_policy,
                )
                part_table = collect_part(
                    incoming_part
                    if part.role == "population_sampling_state"
                    else checked_component_batches(incoming_part, selected.row, part.role),
                    part.schema,
                    part.keys,
                    budget,
                )
            else:
                incoming_part = _batches(connection)
                part_table = collect_part(
                    incoming_part
                    if part.role == "population_sampling_state"
                    else checked_component_batches(incoming_part, selected.row, part.role),
                    part.schema,
                    part.keys,
                    budget,
                )
            part_frames.append(
                PartFrame(
                    part.role,
                    part.contract_id,
                    part.contract_version,
                    part.schema,
                    part.keys,
                    to_part_frame(part_table, selected.row, budget),
                )
            )
            del part_table
        for collector in collectors:
            collector.retained.clear()
            collector.seen.clear()
        collectors = ()
        incoming_frames = [frame]
        del frame
        frame, output_parts, handoffs = execute_retained_suffix(
            incoming_frames.pop(),
            tuple(part_frames.pop(0) for _ in range(len(part_frames))),
            request.calls,
            budget,
        )
        budget.allocation(budget.live_bytes * 2)
        result = frame_to_arrow(frame, request.calls[-1].output_row, schema)
        completed_parts: list[LocalPartResult] = []
        total_output_bytes = result.nbytes
        for output_part in output_parts:
            part_table = _part_to_arrow(output_part)
            total_output_bytes += part_table.nbytes
            completed_parts.append(
                LocalPartResult(
                    output_part.role,
                    output_part.contract_id,
                    output_part.contract_version,
                    part_table,
                )
            )
            if output_part.role != "population_sampling_state":
                tuple(
                    checked_component_batches(
                        part_table.to_batches(), request.calls[-1].output_row, output_part.role
                    )
                )
                if len(output_part.frame) != len(frame):
                    fail(
                        "one retained state row per output row",
                        "part row count differs",
                        "output_validation",
                    )
                for name in part_table.column_names:
                    if name not in output_part.keys:
                        if name in result.column_names:
                            fail(
                                "independent retained state field names",
                                "duplicate retained output field",
                                "output_validation",
                            )
                        result = result.append_column(
                            part_table.schema.field(name), part_table[name]
                        )
        if total_output_bytes > policy.max_output_bytes:
            fail("bounded decoded Arrow output", "local Arrow output overflow")
        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if sys.platform != "darwin":
            peak *= 1024
        if peak > policy.max_worker_rss:
            fail("bounded worker peak RSS", "worker memory overflow")
        budget.check()
        connection.send(
            LocalResult(
                result,
                handoffs,
                count,
                budget.input_bytes,
                peak,
                os.getpid(),
                tuple(completed_parts),
            )
        )
    except Exception as error:
        if isinstance(error, MaterializationError):
            failure = LocalFailure(
                error.expected or "valid local input",
                error.received or "local failure",
                error.hint or "Inspect the local input contract.",
                "transfer_guard" if error.stage in ("collection", "storage_read") else error.stage,
            )
        elif isinstance(error, DatasetCompilationError):
            failure = LocalFailure(
                error.expected or "valid retained Metric state",
                error.received or "invalid retained fold",
                "Inspect the exact retained state; author a fresh observation at the target coordinates when the fold is unsupported.",
                "output_validation",
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
        # The operating system closes the inherited lifetime at process exit.
        # Bootstrap cleanup and interpreter finalization remain covered too.


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


class _LifetimeDescriptors:
    """Start-race-safe references: a late thread cannot borrow a recycled fd."""

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._closed = False
        self._lock = Lock()

    def borrow(self) -> int:
        with self._lock:
            if self._closed:
                raise RuntimeError("worker supervision already ended")
            return os.dup(self._fd)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            os.close(self._fd)


def supervise(
    request: LocalRequest,
    batches: Iterable[pa.RecordBatch],
    *,
    cancel_source: Callable[[], None],
    lifetime: WorkerReservation,
    terminal: Callable[[], None],
    worker_code: str = _WORKER_CODE,
    part_batches: tuple[Iterable[pa.RecordBatch], ...] = (),
) -> LocalResult:
    """Run one suffix; prove worker and input feeder termination before discharge."""
    if os.name != "posix":
        fail("a registered POSIX terminable worker", "unsupported platform", "execution_boundary")
    lifetime_fd = acquire_worker_lifetime(lifetime)
    descriptors = _LifetimeDescriptors(lifetime_fd)
    try:
        return _supervise(
            request,
            batches,
            cancel_source=cancel_source,
            terminal=terminal,
            worker_code=worker_code,
            part_batches=part_batches,
            lifetime=lifetime,
            lifetime_fd=lifetime_fd,
            descriptors=descriptors,
        )
    finally:
        descriptors.close()


def _supervise(
    request: LocalRequest,
    batches: Iterable[pa.RecordBatch],
    *,
    cancel_source: Callable[[], None],
    terminal: Callable[[], None],
    worker_code: str,
    part_batches: tuple[Iterable[pa.RecordBatch], ...],
    lifetime: WorkerReservation,
    lifetime_fd: int,
    descriptors: _LifetimeDescriptors,
) -> LocalResult:
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
            [
                sys.executable,
                "-B",
                "-c",
                worker_code,
                str(child.fileno()),
                str(lifetime_fd),
                lifetime.execution.ownership_nonce,
                str(lifetime.path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(child.fileno(), lifetime_fd),
            env=environment,
        )
        child.close()

        def feed() -> None:
            feeder_fd: int | None = None
            try:
                feeder_fd = descriptors.borrow()
                parent.send(request)
                if isinstance(request.input, StreamInput):
                    for batch in batches:
                        parent.send(batch)
                    parent.send(None)
                if not (isinstance(request.input, StreamInput) and request.input.wide_parts):
                    expected = sum(part.receipt is None for part in request.parts)
                    if len(part_batches) != expected:
                        fail(
                            "one stream for each selected nonlocal part",
                            "part stream count differs",
                        )
                    for stream in part_batches:
                        for batch in stream:
                            parent.send(batch)
                        parent.send(None)
            except BaseException as error:
                feed_errors.append(error)
            finally:
                if feeder_fd is not None:
                    os.close(feeder_fd)

        feeder = Thread(target=feed, name="marivo-local-input", daemon=True)
        feeder.start()

        # One reader and one writer use opposite directions of the duplex pipe.
        # Receive independently: a guard can reject an early batch, and a partial
        # response must not block the supervising deadline or RSS checks.
        def receive() -> None:
            receiver_fd: int | None = None
            try:
                receiver_fd = descriptors.borrow()
                response.append(parent.recv())
            except Exception:
                # Missing or malformed IPC is reported through the bounded error below.
                pass
            finally:
                complete.set()
                if receiver_fd is not None:
                    os.close(receiver_fd)

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
        if receiver is not None and receiver.ident is not None:
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
