"""Actual source coalition preparation with independent percentile references."""

from pathlib import Path

import ibis
import pandas as pd
import pytest

from marivo.analysis import time_scope
from marivo.analysis.compiler import compile_dataset
from marivo.analysis.compiler.placement import place
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.operators.attribution_contracts import AttributePayload
from marivo.analysis.operators.distribution_values import execute_distribution
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.semantic._quantile import QuantileMethod, quantile_metric
from tests.lazy_distribution_fixtures import (
    CHANNEL,
    METRIC,
    REGION,
    make_distribution_registry,
    seed_distribution_database,
)
from tests.lazy_execution_fixtures import ExecutionFixture, assert_compiled_validations
from tests.lazy_observation_fixtures import NoIoActionPort


@pytest.mark.parametrize("method", ["linear_interpolation@v1", "duckdb_tdigest@v1"])
@pytest.mark.parametrize("hierarchy", [False, True])
def test_complete_source_coalitions(
    tmp_path: Path, method: QuantileMethod, hierarchy: bool
) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_distribution_database(database)
    registry, sidecar = make_distribution_registry(database)
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="distribution",
        store_id="distribution",
    )
    backend = ibis.duckdb.connect(str(database))
    try:
        fixture = ExecutionFixture(database, registry, sidecar, sources, backend)
        current = (
            sources.observe(
                quantile_metric(METRIC, method=method),
                time_scope=time_scope(start="2026-02-01", end="2026-02-05"),
            )
            .with_dimensions(REGION, CHANNEL)
            .aggregate()
        )
        baseline = (
            sources.observe(
                quantile_metric(METRIC, method=method),
                time_scope=time_scope(start="2026-01-01", end="2026-01-05"),
            )
            .with_dimensions(REGION, CHANNEL)
            .aggregate()
        )
        result = current.compare(baseline).attribute(
            axes=(REGION, CHANNEL), mode="hierarchy" if hierarchy else "joint"
        )
        assert [type(step).__name__ for step in place(result).steps] == ["SourceStep", "PandasStep"]
        compiled = compile_dataset(result, fixture.tables(result))
        assert compiled.numerical_input == "distribution_coalitions"
        assert_compiled_validations(compiled.validations)
        frame = compiled.expression.to_pyarrow().to_pandas(types_mapper=pd.ArrowDtype)
        assert isinstance(result._root, LogicalRootHandle) and isinstance(
            result._root.payload, AttributePayload
        )
        output = execute_distribution(frame, result._root.payload.spec)
        assert set(output.overall_delta) == {1.0}
        for mask in ((True, False), (True, True)) if hierarchy else ((True, True),):
            selected = [
                row for row in output.to_dict("records") if tuple(row["active_axis_mask"]) == mask
            ]
            assert sum(row["contribution"] for row in selected) == pytest.approx(1.0)
            assert all(
                row["current_value"] - row["baseline_value"] == pytest.approx(row["contribution"])
                for row in selected
            )
    finally:
        backend.disconnect()


def test_logical_axis_expansion_and_original_selection(tmp_path: Path) -> None:
    from marivo.analysis.observation.predicates import eq

    database = tmp_path / "warehouse.duckdb"
    seed_distribution_database(database)
    registry, sidecar = make_distribution_registry(database)
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="expansion",
        store_id="expansion",
    )
    backend = ibis.duckdb.connect(str(database))
    try:
        fixture = ExecutionFixture(database, registry, sidecar, sources, backend)
        current = (
            sources.observe(METRIC, time_scope=time_scope(start="2026-02-01", end="2026-02-05"))
            .with_dimensions(REGION)
            .aggregate()
        )
        baseline = (
            sources.observe(METRIC, time_scope=time_scope(start="2026-01-01", end="2026-01-05"))
            .with_dimensions(REGION)
            .aggregate()
        )
        delta = current.compare(baseline)
        delta = delta.where(eq(delta.fields.get("region"), "EU"))
        result = delta.attribute(axes=(REGION, CHANNEL), top_k=1)
        compiled = compile_dataset(result, fixture.tables(result))
        assert_compiled_validations(compiled.validations)
        assert isinstance(result._root, LogicalRootHandle) and isinstance(
            result._root.payload, AttributePayload
        )
        output = execute_distribution(
            compiled.expression.to_pyarrow().to_pandas(types_mapper=pd.ArrowDtype),
            result._root.payload.spec,
        )
        assert len(output) == 1 and output.contribution.iloc[0] == pytest.approx(4.0)
    finally:
        backend.disconnect()
