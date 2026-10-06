"""Observe public R9 costs without changing execution or retaining input buffers."""

from __future__ import annotations

import os
import resource
import subprocess
import sys
import threading
import time
import weakref
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack
from dataclasses import asdict
from functools import cache
from pathlib import Path
from types import CodeType, FrameType
from typing import Literal

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
from ibis.backends import BaseBackend

import marivo.datasource.adapters as adapters
from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.materialization.graph_exchange import ExchangeResult
from marivo.datasource.adapters import SourceBatchStream, SourceSession, _Cursor
from tests.r95_driver_audit import DriverAudit, native_audit, serialized_submission

_PHASES = {
    ("datasource/manage.py", "_connect"): "connect",
    ("datasource/manage.py", "_connect_internal"): "connect",
    ("datasource/adapters.py", "bind"): "bind",
    ("datasource/adapters.py", "compile"): "compile",
    ("datasource/adapters.py", "close"): "close",
    ("materialization/graph_preflight.py", "preflight_entities"): "metadata",
    ("materialization/graph_preparation.py", "execute"): "prepare",
    ("materialization/graph_source_execution.py", "_check"): "check",
    ("materialization/graph_exchange.py", "from_arrow"): "validate",
    ("materialization/graph_exchange.py", "collect"): "validate",
    ("materialization/graph_storage.py", "read_table"): "read",
    ("materialization/graph_storage.py", "write_table"): "write",
    ("materialization/graph_store.py", "publish"): "publish",
    ("materialization/graph_local_execution.py", "execute_verified_fixed"): "fixed_kernel",
    ("materialization/graph_source_execution.py", "execute_source_graph"): "source_execution",
    ("materialization/graph_publication.py", "_execute"): "execution",
    ("materialization/graph_dataset.py", "to_pandas"): "terminal",
}


@cache
def _phase(code: CodeType) -> str | None:
    path = code.co_filename.replace("\\", "/")
    name = code.co_name
    for (suffix, function), phase in _PHASES.items():
        if name == function and path.endswith(suffix):
            return phase
    if "/ibis/backends/" in path and name in (
        "get_schema",
        "_metadata",
        "_get_schema_using_query",
        "_post_connect",
    ):
        return "metadata"
    if "/marivo/analysis/methods/" in path and name in (
        "score",
        "score_spearman",
        "fit",
        "forecast",
        "reduce",
        "run",
    ):
        return "kernel"
    return None


def _ranges(value: object) -> tuple[tuple[int, int], ...]:
    """Inspect existing allocations; never convert a value to inspect its memory."""
    if isinstance(value, (pa.Table, pa.RecordBatch)):
        arrays = (
            (chunk for column in value.columns for chunk in column.chunks)
            if isinstance(value, pa.Table)
            else iter(value.columns)
        )
        return tuple(
            (buffer.address, buffer.address + buffer.size)
            for array in arrays
            for buffer in array.buffers()
            if buffer is not None and buffer.size
        )
    if isinstance(value, np.ndarray) and value.size:
        start = int(value.__array_interface__["data"][0])
        low = sum(
            min(0, (size - 1) * stride)
            for size, stride in zip(value.shape, value.strides, strict=True)
        )
        high = sum(
            max(0, (size - 1) * stride)
            for size, stride in zip(value.shape, value.strides, strict=True)
        )
        return ((start + low, start + high + value.itemsize),)
    if isinstance(value, pd.DataFrame):
        return tuple(
            bound
            for block in value._mgr.blocks
            for bound in _ranges(getattr(block.values, "_pa_array", block.values))
        )
    if isinstance(value, pa.ChunkedArray):
        return tuple(
            (buffer.address, buffer.address + buffer.size)
            for chunk in value.chunks
            for buffer in chunk.buffers()
            if buffer is not None and buffer.size
        )
    if isinstance(value, ExchangeResult):
        return tuple(
            bound
            for table in (value.primary, *(part.table for part in value.parts))
            for bound in _ranges(table)
        )
    return ()


def _union_size(ranges: Sequence[tuple[int, int]]) -> int:
    total = 0
    last = 0
    for start, end in sorted(ranges):
        total += max(0, end - max(start, last))
        last = max(last, end)
    return total


def _shared_bytes(left: object, right: object) -> int:
    overlaps = tuple(
        (max(a, c), min(b, d))
        for a, b in _ranges(left)
        for c, d in _ranges(right)
        if max(a, c) < min(b, d)
    )
    return _union_size(overlaps)


def _peak_rss() -> int:
    highwater = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(highwater if sys.platform == "darwin" else highwater * 1024)


def _integer(value: object) -> int:
    assert isinstance(value, int)
    return value


def _rss() -> int | None:
    try:
        if sys.platform.startswith("linux"):
            fields = Path("/proc/self/statm").read_text().split()
            return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
        observed = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
        return int(observed.stdout.strip()) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


class _FetchWitness:
    def __init__(self, cursor: _Cursor, observer: CostObserver):
        self.native = cursor
        self.observer = observer

    def __getattr__(self, name: str) -> object:
        return getattr(self.native, name)

    def fetchmany(self, size: int) -> Sequence[Sequence[object]]:
        started = time.monotonic()
        try:
            rows = self.native.fetchmany(size)
        except BaseException as error:
            self.observer.fetches.append(
                {
                    "requested_rows": size,
                    "state": "failed",
                    "error_type": type(error).__name__,
                    "seconds": time.monotonic() - started,
                }
            )
            raise
        self.observer.fetches.append(
            {
                "requested_rows": size,
                "rows": len(rows),
                "state": "succeeded",
                "seconds": time.monotonic() - started,
            }
        )
        return rows

    def close(self) -> None:
        self.native.close()


class CostObserver:
    """Scoped native, Arrow, storage, conversion, phase and process observations.

    Use ``with CostObserver("duckdb") as observer: result = relation.execute()``
    and serialize ``observer.snapshot()`` after exit. Timings include observer
    overhead; nested phase durations are inclusive and must not be added together.
    Pointer ranges describe observed buffers, not an exact total-memory bound.
    """

    def __init__(self, backend: str, *, sample_interval: float = 0.1):
        if sample_interval <= 0:
            raise ValueError("RSS sampling interval must be positive")
        self.backend = backend
        self.sample_interval = sample_interval
        self.batches: list[dict[str, object]] = []
        self.fetches: list[dict[str, object]] = []
        self.phases: list[dict[str, object]] = []
        self.conversions: Counter[str] = Counter()
        self.conversion_seconds: dict[str, float] = {}
        self.ownership: list[dict[str, object]] = []
        self.buffers: list[dict[str, object]] = []
        self.storage: list[dict[str, object]] = []
        self.sessions: list[dict[str, object]] = []
        self.executions: list[dict[str, object]] = []
        self.rss_samples: list[dict[str, object]] = []
        self._owners: weakref.WeakKeyDictionary[SourceSession, int] = weakref.WeakKeyDictionary()
        self._starts: dict[int, tuple[str, float]] = {}
        self._cstarts: dict[tuple[int, str], float] = {}
        self._stack = ExitStack()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._audit: DriverAudit | None = None
        self._prior_profile: (
            Callable[
                [FrameType, Literal["call", "return", "c_call", "c_return", "c_exception"], object],
                object,
            ]
            | None
        ) = None
        self._entered = False
        self._started = 0.0
        self._elapsed: float | None = None

    def __enter__(self) -> CostObserver:
        if self._entered:
            raise RuntimeError("A cost observer is single use")
        self._entered = True
        self._started = time.monotonic()
        patch = self._stack.enter_context(pytest.MonkeyPatch.context())
        try:
            self._audit = self._stack.enter_context(native_audit(patch, self.backend))
        except BaseException:
            self._stack.close()
            raise
        native = adapters._native_cursor
        iterate = SourceBatchStream._iterate

        def cursor(owner: BaseBackend, name: str, sql: str) -> _Cursor:
            return _FetchWitness(native(owner, name, sql), self)

        def batches(stream: SourceBatchStream) -> Iterator[pa.RecordBatch]:
            for batch in iterate(stream):
                self.batches.append(
                    {
                        "purpose": stream._submission.purpose,
                        "source_identity": stream._submission.source_identity,
                        "rows": batch.num_rows,
                        "columns": batch.num_columns,
                        "arrow_nbytes": batch.nbytes,
                        "schema": [[field.name, str(field.type)] for field in batch.schema],
                    }
                )
                yield batch

        patch.setattr(adapters, "_native_cursor", cursor)
        patch.setattr(SourceBatchStream, "_iterate", batches)
        self._prior_profile = sys.getprofile()
        sys.setprofile(self._profile)
        self._thread = threading.Thread(target=self._sample_rss, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        sys.setprofile(self._prior_profile)
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self._elapsed = time.monotonic() - self._started
        self._stack.close()

    def _sample_rss(self) -> None:
        while True:
            self.rss_samples.append(
                {"seconds": time.monotonic() - self._started, "rss_bytes": _rss()}
            )
            if self._stop.wait(self.sample_interval):
                return

    def _session(self, owner: SourceSession) -> None:
        if owner not in self._owners:
            self._owners[owner] = len(self.sessions)
            self.sessions.append({})
        external_disconnect = self.sessions[self._owners[owner]].get(
            "external_disconnect_observed", False
        )
        self.sessions[self._owners[owner]] = {
            "backend": owner.provider.name,
            "closed": owner._closed,
            "backend_disconnected": owner._backend_disconnected or external_disconnect is True,
            "adapter_disconnection_acknowledged": owner._backend_disconnected,
            "external_disconnect_observed": external_disconnect,
            "active_readers": len(owner._streams),
            "staged_relations": len(owner._staged_relations),
            "cancel_control_released": owner._cancel_control_released,
            "submissions": [asdict(item) for item in owner.submissions],
        }

    def _buffer_sample(self, frame: FrameType, phase: str, result: object) -> None:
        ranges = tuple(
            bound for value in (*frame.f_locals.values(), result) for bound in _ranges(value)
        )
        self.buffers.append(
            {
                "phase": phase,
                "seconds": time.monotonic() - self._started,
                "observed_unique_buffer_bytes": _union_size(ranges),
            }
        )

    def _profile(self, frame: FrameType, event: str, result: object) -> None:
        path = frame.f_code.co_filename.replace("\\", "/")
        name = frame.f_code.co_name
        if event in ("c_call", "c_return", "c_exception"):
            operation = str(getattr(result, "__name__", ""))
            owner = getattr(result, "__self__", None)
            kind = (
                (
                    "arrow_to_pandas"
                    if operation == "to_pandas"
                    else "arrow_to_python"
                    if operation == "to_pylist"
                    else "arrow_to_numpy"
                    if operation == "to_numpy"
                    else None
                )
                if isinstance(owner, (pa.Table, pa.Array, pa.ChunkedArray, pa.RecordBatch))
                else None
            )
            if kind is not None:
                key = (id(frame), kind)
                if event == "c_call":
                    self.conversions[kind] += 1
                    self._cstarts[key] = time.monotonic()
                elif key in self._cstarts:
                    self.conversion_seconds[kind] = (
                        self.conversion_seconds.get(kind, 0.0)
                        + time.monotonic()
                        - self._cstarts.pop(key)
                    )
            return
        if event not in ("call", "return"):
            return
        phase = _phase(frame.f_code)
        pandas_conversion = (
            "/pandas/" in path
            and name in ("to_numpy", "copy")
            and isinstance(frame.f_locals.get("self"), pd.DataFrame)
        )
        arrow_pandas = path.endswith("pyarrow/pandas_compat.py") and name == "table_to_dataframe"
        if event == "call":
            if phase is not None:
                self._starts[id(frame)] = (phase, time.monotonic())
            elif pandas_conversion:
                self._starts[id(frame)] = ("pandas_" + name, time.monotonic())
            elif arrow_pandas:
                self._starts[id(frame)] = ("arrow_to_pandas", time.monotonic())
            return
        if path.endswith("datasource/runtime.py") and name == "_disconnect" and result is True:
            backend = frame.f_locals.get("backend")
            for owner, index in self._owners.items():
                if owner._backend is backend:
                    self.sessions[index]["external_disconnect_observed"] = True
                    self.sessions[index]["backend_disconnected"] = True
        if path.endswith("datasource/adapters.py"):
            owner = frame.f_locals.get("self")
            if isinstance(owner, SourceSession) and name in (
                "__enter__",
                "batches",
                "close",
                "mark_backend_disconnected",
            ):
                self._session(owner)
        if pandas_conversion:
            owner = frame.f_locals.get("self")
            if isinstance(owner, pd.DataFrame):
                kind = "pandas_to_numpy" if name == "to_numpy" else "pandas_copy"
                self.conversions[kind] += 1
                self.ownership.append(
                    {
                        "operation": kind,
                        "shared_buffer_bytes": _shared_bytes(owner, result),
                        "input_buffer_bytes": _union_size(_ranges(owner)),
                        "output_buffer_bytes": _union_size(_ranges(result)),
                        "numpy_owns_data": bool(result.flags.owndata)
                        if isinstance(result, np.ndarray)
                        else None,
                    }
                )
        if arrow_pandas:
            self.conversions["arrow_to_pandas"] += 1
            table = frame.f_locals.get("table")
            self.ownership.append(
                {
                    "operation": "arrow_to_pandas",
                    "shared_buffer_bytes": _shared_bytes(table, result),
                    "input_buffer_bytes": _union_size(_ranges(table)),
                    "output_buffer_bytes": _union_size(_ranges(result)),
                }
            )
        if phase == "terminal" and isinstance(result, pd.DataFrame):
            table = frame.f_locals.get("table")
            intermediate = frame.f_locals.get("frame")
            self.ownership.append(
                {
                    "operation": "public_to_pandas",
                    "arrow_to_intermediate_shared_bytes": _shared_bytes(table, intermediate),
                    "intermediate_to_isolated_result_shared_bytes": _shared_bytes(
                        intermediate, result
                    ),
                    "arrow_to_result_shared_bytes": _shared_bytes(table, result),
                }
            )
        if phase in ("read", "write"):
            receipt = result if isinstance(result, LocalReceipt) else frame.f_locals.get("receipt")
            table = result if isinstance(result, pa.Table) else frame.f_locals.get("table")
            if isinstance(receipt, LocalReceipt) and isinstance(table, pa.Table):
                self.storage.append(
                    {
                        "operation": phase,
                        "receipt": asdict(receipt),
                        "arrow_nbytes": table.nbytes,
                        "parquet_bytes": sum(entry.size_bytes for entry in receipt.file_manifest),
                        "schema": [[field.name, str(field.type)] for field in table.schema],
                    }
                )
        if phase == "execution":
            self.executions.append(
                {
                    "source": frame.f_locals.get("source_only"),
                    "exact_hit": bool(frame.f_locals.get("hits")),
                    "run_ref": getattr(result, "producing_run_ref", None),
                    "artifact_ref": getattr(result, "artifact_ref", None),
                }
            )
        started = self._starts.pop(id(frame), None)
        if started is not None:
            observed_phase, before = started
            elapsed = time.monotonic() - before
            if observed_phase.startswith(("pandas_", "arrow_")):
                self.conversion_seconds[observed_phase] = (
                    self.conversion_seconds.get(observed_phase, 0.0) + elapsed
                )
            else:
                self.phases.append(
                    {
                        "phase": observed_phase,
                        "owner": path.split("/marivo/", 1)[-1] + ":" + name,
                        "seconds": elapsed,
                        "returned_type": type(result).__name__,
                    }
                )
                self._buffer_sample(frame, observed_phase, result)

    def snapshot(self) -> dict[str, object]:
        native = [] if self._audit is None else self._audit.submissions
        if native and self._audit is not None:
            self._audit.assert_classified()
        source_submissions = []
        for session in self.sessions:
            submissions = session["submissions"]
            assert isinstance(submissions, list)
            source_submissions.extend(submissions)
        return {
            "seconds": self._elapsed
            if self._elapsed is not None
            else time.monotonic() - self._started,
            "native_submissions": [serialized_submission(item) for item in native],
            "query_counts": dict(Counter(item.category for item in native)),
            "purpose_counts": dict(Counter(item.purpose for item in native)),
            "source_sessions": self.sessions,
            "source_submissions": source_submissions,
            "resource_closed": all(
                session["closed"] is True
                and session["backend_disconnected"] is True
                and session["active_readers"] == 0
                and session["staged_relations"] == 0
                and session["cancel_control_released"] is True
                for session in self.sessions
            ),
            "arrow_exchange": {
                "batches": self.batches,
                "batch_count": len(self.batches),
                "rows": sum(_integer(item["rows"]) for item in self.batches),
                "arrow_nbytes": sum(_integer(item["arrow_nbytes"]) for item in self.batches),
                "wire_bytes": None,
            },
            "driver_fetches": self.fetches,
            "phases": self.phases,
            "phase_calls": dict(Counter(str(item["phase"]) for item in self.phases)),
            "phase_boundary": "inclusive monotonic durations; nested phases overlap; observer overhead included",
            "conversions": {
                "counts": dict(self.conversions),
                "seconds": dict(self.conversion_seconds),
                "ownership": self.ownership,
                "boundary": "existing buffer address overlap; object payloads and noncontiguous gaps are not exact allocations",
                "arrow_to_python_call_count": None,
                "arrow_to_numpy_call_count": None,
                "native_boundary": "PyArrow Cython to_pylist/to_numpy methods are not visible to CPython profiling; their counts remain unobserved",
            },
            "storage": self.storage,
            "storage_writes": [item for item in self.storage if item["operation"] == "write"],
            "executions": self.executions,
            "workspace": {
                "buffer_samples": self.buffers,
                "rss_samples": self.rss_samples,
                "sampled_rss_peak_bytes": max(
                    (
                        _integer(item["rss_bytes"])
                        for item in self.rss_samples
                        if item["rss_bytes"] is not None
                    ),
                    default=None,
                ),
                "process_lifetime_rss_peak_bytes": _peak_rss(),
                "sampling_interval_seconds": self.sample_interval,
                "boundary": "RSS includes Python and native; visible buffer samples are lower bounds, not exact simultaneous residence upper bounds",
                "python_native_split": None,
                "server_memory": None,
                "server_termination": "unobserved unless SourceSubmission carries independent evidence",
            },
        }
