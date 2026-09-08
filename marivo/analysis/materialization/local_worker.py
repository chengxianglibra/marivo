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

import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.datasets.descriptors import DatasetRowContract, DatasetRowSetContract
from marivo.analysis.materialization.attribution_publication import AttributionSourceSummary
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
    frame_bytes,
    frame_to_arrow,
    to_local_frame,
    to_part_frame,
    validate_frame,
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
from marivo.analysis.operators.attribution_contracts import AttributeSpecV1
from marivo.analysis.operators.contracts import CompareSpecV1
from marivo.analysis.operators.errors import AttributionError, ComparisonError, RowValueError
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
    attribution_summary: AttributionSourceSummary | None = None


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


@dataclass(frozen=True, slots=True, repr=False)
class LocalBoundary:
    output: int
    input: StreamInput | ArtifactInput
    parts: tuple[LocalPartInput, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class LocalStage:
    output: int
    inputs: tuple[int, ...]
    call: RowCall | CompareSpecV1 | AttributeSpecV1


@dataclass(frozen=True, slots=True, repr=False)
class LocalGraphRequest:
    boundaries: tuple[LocalBoundary, ...]
    stages: tuple[LocalStage, ...]
    primary_output: int
    policy: LocalPolicy
    deadline: float


@dataclass(frozen=True, slots=True, repr=False)
class LocalInputStreams:
    batches: Iterable[pa.RecordBatch]
    parts: tuple[Iterable[pa.RecordBatch], ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class _Frames:
    frame: pd.DataFrame
    parts: tuple[PartFrame, ...]
    schema: pa.Schema

    @property
    def size(self) -> int:
        return frame_bytes(self.frame) + sum(frame_bytes(part.frame) for part in self.parts)


def _collect_input(
    connection: Connection,
    selected: StreamInput | ArtifactInput,
    parts: tuple[LocalPartInput, ...],
    budget: LocalBudget,
) -> tuple[_Frames, int]:
    if len({part.role for part in parts}) != len(parts):
        fail("one input per selected retained role", "duplicate part input")
    policy = budget.policy
    read_policy = ReadPolicy(
        max_rows=policy.max_input_rows,
        max_decoded_bytes=min(policy.max_input_bytes, policy.max_intermediate_bytes),
        max_batch_bytes=policy.max_batch_bytes,
        deadline_seconds=max(0.001, budget.deadline - time.monotonic()),
    )
    wide = isinstance(selected, StreamInput) and selected.wide_parts
    collectors = tuple(PartCollector(part.schema, part.keys, budget) for part in parts)
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
                    for part, collector in zip(parts, collectors, strict=True):
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
    for part, collector in zip(parts, collectors, strict=True):
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
    return _Frames(frame, tuple(part_frames), schema), count


def _execute_graph(
    connection: Connection, request: LocalGraphRequest, budget: LocalBudget
) -> tuple[
    _Frames, tuple[tuple[int, int], ...], int, DatasetRowContract, AttributionSourceSummary | None
]:
    from marivo.analysis.operators.attribute_values import execute_attribute
    from marivo.analysis.operators.compare import execute_compare
    from marivo.analysis.operators.delta_state import execute_compare_parts, validate_delta_parts
    from marivo.analysis.operators.rollup import validate_parts

    values: dict[int, _Frames] = {}
    total_rows = 0
    for boundary in request.boundaries:
        if boundary.output in values:
            fail("unique physical boundary identities", "duplicate graph input")
        value, count = _collect_input(connection, boundary.input, boundary.parts, budget)
        total_rows += count
        values[boundary.output] = value
    # Every boundary and required role is complete before any method is invoked.
    users: dict[int, int] = {}
    for stage in request.stages:
        for key in stage.inputs:
            users[key] = users.get(key, 0) + 1
    users[request.primary_output] = users.get(request.primary_output, 0) + 1
    handoffs: list[tuple[int, int]] = []
    output_row: DatasetRowContract | None = None
    attribution_summary: AttributionSourceSummary | None = None
    for stage in request.stages:
        budget.check()
        if stage.output in values or any(key not in values for key in stage.inputs):
            fail("a complete ordered local dependency graph", "invalid stage dependencies")
        incoming = tuple(values[key] for key in stage.inputs)
        call = stage.call
        if isinstance(call, RowCall):
            if len(incoming) != 1:
                fail("one row method operand", "invalid row method arity")
            source = incoming[0]
            result, parts, transfers = execute_retained_suffix(
                source.frame, source.parts, (call,), budget
            )
            # The suffix accounts for replacement; shared graph inputs remain alive.
            budget.live_bytes += source.size
            value = _Frames(result, parts, source.schema)
            del parts
            handoffs.extend(transfers)
            output_row = call.output_row
            del source
        elif isinstance(call, CompareSpecV1):
            if len(incoming) != 2:
                fail("ordered current and baseline operands", "invalid comparison arity")
            current, baseline = incoming
            validate_frame(current.frame, call.current_row, call.current_rows)
            validate_frame(baseline.frame, call.baseline_row, call.baseline_rows)
            count = (
                len(current.frame)
                + len(baseline.frame)
                + sum(len(part.frame) for value in incoming for part in value.parts)
            )
            if count > budget.policy.max_method_rows:
                fail("bounded complete comparison problem size", "method size overflow")
            budget.allocation((current.size + baseline.size) * 6 + count * 1024)
            from marivo.analysis.compiler.lowering import retained_part_specs

            for value, row in ((current, call.current_row), (baseline, call.baseline_row)):
                required = {part.role for part in retained_part_specs(row)}
                if not required.issubset(part.role for part in value.parts):
                    fail(
                        "complete comparison side component roles",
                        "missing side components",
                        "transfer_guard",
                    )
                validate_parts(value.frame, value.parts, row)
            result = execute_compare(current.frame, baseline.frame, call)
            parts = execute_compare_parts(
                current.frame, baseline.frame, call, current.parts, baseline.parts, result
            )
            budget.check()
            result_size = frame_bytes(result) + sum(frame_bytes(part.frame) for part in parts)
            if (
                len(result) > budget.policy.max_output_rows
                or result_size > budget.policy.max_output_bytes
            ):
                fail("complete comparison output within budgets", "local output overflow")
            budget.allocation(result_size)
            validate_frame(result, call.output_row, call.output_rows)
            budget.live_bytes += result_size
            fields: list[pa.Field] = []
            for field in call.output_row.schema.columns:
                dtype = result[field.name].dtype
                if not isinstance(dtype, pd.ArrowDtype):
                    fail(
                        "exact Arrow comparison result types",
                        "untyped comparison output",
                        "output_validation",
                    )
                fields.append(pa.field(field.name, dtype.pyarrow_dtype, field.nullable))
            schema = pa.schema(fields)
            value = _Frames(result, parts, schema)
            handoffs.extend((id(item.frame), id(result)) for item in incoming)
            output_row = call.output_row
            del current, baseline
        else:
            if len(incoming) != 1 or call.expanded_compare is not None:
                fail("one complete retained Attribution input", "source-required axis expansion")
            source = incoming[0]
            validate_frame(source.frame, call.input_row, call.input_rows)
            count = len(source.frame) + sum(len(part.frame) for part in source.parts)
            projected_rows = len(source.frame) * (
                len(call.axis_fields) if call.mode == "hierarchy" else 1
            )
            if (
                count > budget.policy.max_method_rows
                or projected_rows > budget.policy.max_output_rows
            ):
                fail(
                    "bounded complete Attribution partitions and resolutions",
                    "method size overflow",
                )
            budget.allocation(source.size * 8 + projected_rows * 2048)
            validate_delta_parts(source.frame, source.parts, call.input_row)
            result = execute_attribute(source.frame, call, parts=source.parts)
            budget.check()
            result_size = frame_bytes(result)
            if (
                len(result) > budget.policy.max_output_rows
                or result_size > budget.policy.max_output_bytes
            ):
                fail("complete Attribution output within budgets", "local output overflow")
            budget.allocation(result_size)
            validate_frame(result, call.output_row, call.output_rows)
            from marivo.analysis.materialization.attribution_publication import (
                summarize_attribution_frame,
            )

            attribution_summary = summarize_attribution_frame(result, call.output_row)
            budget.live_bytes += result_size
            fields = []
            for field in call.output_row.schema.columns:
                dtype = result[field.name].dtype
                if not isinstance(dtype, pd.ArrowDtype):
                    fail(
                        "exact Arrow Attribution result types",
                        "untyped Attribution output",
                        "output_validation",
                    )
                fields.append(pa.field(field.name, dtype.pyarrow_dtype, field.nullable))
            value = _Frames(result, (), pa.schema(fields))
            handoffs.append((id(source.frame), id(result)))
            output_row = call.output_row
            del source
        values[stage.output] = value
        for key in stage.inputs:
            users[key] -= 1
            if users[key] == 0:
                budget.live_bytes -= values.pop(key).size
        del incoming
    if output_row is None or request.primary_output not in values:
        fail("one complete local graph result", "missing graph output")
    return (
        values[request.primary_output],
        tuple(handoffs),
        total_rows,
        output_row,
        attribution_summary,
    )


def worker_entry() -> None:
    connection = Connection(int(sys.argv[1]))
    lifetime_fd = int(sys.argv[2])
    try:
        validate_worker_lifetime(lifetime_fd, Path(sys.argv[4]), sys.argv[3])
        request: object = connection.recv()
        if not isinstance(request, (LocalRequest, LocalGraphRequest)):
            fail(
                "one registered local request",
                "invalid worker request",
                "implementation_registration",
            )
        policy = request.policy
        policy.__post_init__()
        budget = LocalBudget(policy, request.deadline)
        attribution_summary: AttributionSourceSummary | None = None
        if isinstance(request, LocalGraphRequest):
            completed, handoffs, count, output_row, attribution_summary = _execute_graph(
                connection, request, budget
            )
            frame, output_parts, schema = completed.frame, completed.parts, completed.schema
        else:
            if not request.calls:
                fail("one registered suffix request", "empty suffix", "implementation_registration")
            incoming, count = _collect_input(connection, request.input, request.parts, budget)
            schema = incoming.schema
            frame, output_parts, handoffs = execute_retained_suffix(
                incoming.frame,
                incoming.parts,
                request.calls,
                budget,
            )
            output_row = request.calls[-1].output_row
        budget.allocation(budget.live_bytes * 2)
        result = frame_to_arrow(frame, output_row, schema)
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
                    checked_component_batches(part_table.to_batches(), output_row, output_part.role)
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
                attribution_summary,
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
        elif isinstance(error, (AttributionError, ComparisonError, RowValueError)):
            failure = LocalFailure(
                error.expected or "complete registered retained inputs",
                error.received or "invalid retained values",
                error.hint
                or "Inspect the selected method and complete retained component contracts.",
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
    request: LocalRequest | LocalGraphRequest,
    batches: Iterable[pa.RecordBatch],
    *,
    cancel_source: Callable[[], None],
    lifetime: WorkerReservation,
    terminal: Callable[[], None],
    worker_code: str = _WORKER_CODE,
    part_batches: tuple[Iterable[pa.RecordBatch], ...] = (),
    input_streams: tuple[LocalInputStreams, ...] = (),
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
            input_streams=input_streams,
            lifetime=lifetime,
            lifetime_fd=lifetime_fd,
            descriptors=descriptors,
        )
    finally:
        descriptors.close()


def _supervise(
    request: LocalRequest | LocalGraphRequest,
    batches: Iterable[pa.RecordBatch],
    *,
    cancel_source: Callable[[], None],
    terminal: Callable[[], None],
    worker_code: str,
    part_batches: tuple[Iterable[pa.RecordBatch], ...],
    input_streams: tuple[LocalInputStreams, ...],
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
                if isinstance(request, LocalGraphRequest):
                    if len(input_streams) != len(request.boundaries):
                        fail("one stream bundle per graph boundary", "input stream count differs")
                    inputs = tuple(
                        (boundary.input, boundary.parts, streams)
                        for boundary, streams in zip(request.boundaries, input_streams, strict=True)
                    )
                else:
                    inputs = (
                        (request.input, request.parts, LocalInputStreams(batches, part_batches)),
                    )
                for selected, parts, streams in inputs:
                    if isinstance(selected, StreamInput):
                        for batch in streams.batches:
                            parent.send(batch)
                        parent.send(None)
                    if not (isinstance(selected, StreamInput) and selected.wide_parts):
                        expected = sum(part.receipt is None for part in parts)
                        if len(streams.parts) != expected:
                            fail(
                                "one stream for each selected nonlocal part",
                                "part stream count differs",
                            )
                        for stream in streams.parts:
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
