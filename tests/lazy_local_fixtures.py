"""Minimal real-source fixtures for local Metric row continuations."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pyarrow as pa

from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.local import (
    collect_primary,
    to_local_frame,
)
from marivo.analysis.observation.contracts import MetricPayload, RetainedRowsPayload
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.analysis.operators import registry as implementations
from marivo.analysis.operators.registry import ImplementationRegistration
from marivo.analysis.operators.row import RowCall
from marivo.analysis.session._lazy_sources import LazySources
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database

REVENUE = ref.metric("sales.revenue")
COUNT = ref.metric("sales.order_count")


@contextmanager
def pandas_methods(*operator_ids: str) -> Iterator[None]:
    """Preselect registered pandas methods by making their source lowerers absent."""
    original = implementations.implementation

    def selected(dataset: LogicalDataset) -> ImplementationRegistration:
        value = original(dataset)
        return replace(value, backends=()) if value.operator_id in operator_ids else value

    with patch.object(implementations, "implementation", selected):
        yield


def setup_local(
    project: Path, *, event: Callable[[str], None] | None = None
) -> tuple[DatasetRuntime, LazySources, Path]:
    database = project / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(project, "local-execution", event=event)
    return runtime, runtime.sources(semantic_registry=registry, sidecar=sidecar), database


def row_call(value: LogicalMetricDataset) -> RowCall:
    assert isinstance(value._root, LogicalRootHandle)
    payload = value._root.payload
    assert isinstance(payload, (MetricPayload, RetainedRowsPayload))
    source = value._inputs[0]
    return RowCall(
        value._root.operator_id,
        source.row_contract,
        source.row_set_contract,
        value.row_contract,
        value.row_set_contract,
        payload.predicate,
        payload.rank,
        payload.limit_count,
    )


def primary_frame(source: LogicalMetricDataset, values: list[float | None]) -> pd.DataFrame:
    table = pa.table(
        {
            "entity_identity": pa.array(
                [{"id": i + 1} for i in range(len(values))], type=pa.struct([("id", pa.int64())])
            ),
            "revenue": pa.array(values, type=pa.float64()),
        }
    )
    complete = collect_primary(table.to_batches(), source.row_contract, source.row_set_contract)
    return to_local_frame(complete, source.row_contract)
