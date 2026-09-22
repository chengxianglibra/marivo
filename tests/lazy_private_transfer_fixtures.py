"""Journey guard for source-private batches crossing into independent parts."""

from __future__ import annotations

from collections.abc import Iterator

import ibis.expr.types as ir
import pyarrow as pa
import pytest

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.execution import ExecutionAdapter

_PRIVATE_COLUMNS = frozenset(
    {"__mv_distinct_key", "__mv_distribution_value", "__mv_distribution_frequency"}
)


def guard_private_batches(runtime: DatasetRuntime, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Assert private columns appear only in the independent part stream."""
    original = runtime._batches
    seen: list[str] = []

    def guarded(
        backend: ExecutionAdapter, expression: ir.Table, batch_rows: int, *, role: str = "primary"
    ) -> Iterator[pa.RecordBatch]:
        for batch in original(backend, expression, batch_rows, role=role):
            if _PRIVATE_COLUMNS.intersection(batch.schema.names):
                assert role.startswith("part.")
                seen.append(role)
            yield batch

    monkeypatch.setattr(runtime, "_batches", guarded)
    return seen
