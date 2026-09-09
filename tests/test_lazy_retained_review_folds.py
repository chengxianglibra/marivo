"""Runtime evidence for unequal-support means and authored allocation facts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

import duckdb
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.reads import part_schema, read_part_batches
from marivo.analysis.observation.contracts import EntityReducedMetricSemantics
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.observation.predicates import eq, gt
from marivo.refs import ref
from tests.lazy_retained_fixtures import setup_retained

pytestmark = pytest.mark.runtime

MEAN = ref.metric("sales.mean_amount")
DAY = ref.time_dimension("sales.orders.order_time")
WINDOW = time_scope(start="2026-02-02", end="2026-02-05")


def _single_state(runtime: DatasetRuntime, value: MaterializedMetricDataset) -> dict[str, object]:
    record = runtime.store.artifact(value.state.artifact_ref.ref)
    assert record is not None and len(record.descriptor.retained_parts) == 1
    part = record.descriptor.retained_parts[0]
    schema = part_schema(runtime.store.project_root, part)
    rows: list[dict[str, object]] = [
        row
        for batch in read_part_batches(runtime.store.project_root, part, expected_schema=schema)
        for row in batch.to_pylist()
    ]
    assert len(rows) == 1
    return rows[0]


@pytest.mark.parametrize("kind", ["local", "engine"])
@pytest.mark.parametrize("axis", ["entity", "time"])
def test_unequal_mean_support_merges_exact_numerator_and_denominator(
    tmp_path: Path,
    kind: Literal["local", "engine"],
    axis: Literal["entity", "time"],
) -> None:
    fixture = setup_retained(tmp_path, kind)
    with duckdb.connect(str(fixture.database)) as database:
        database.execute("DELETE FROM orders")
        database.execute(
            "INSERT INTO orders (id,customer_id,amount,day) VALUES (1,1,10,DATE '2026-02-02'), (2,1,30,DATE '2026-02-03'), (3,1,NULL,DATE '2026-02-04'), (4,2,90,DATE '2026-02-02')"
        )
    source = fixture.sources.observe(
        MEAN,
        population=fixture.sources.population(ref.entity("sales.customers")),
        time_scope=WINDOW,
    )
    if axis == "time":
        source = source.with_time_axis(DAY, grain=grain("day")).aggregate()
    logical_total = source.aggregate() if axis == "entity" else source.rollup(drop_time=True)
    assert logical_total.execute().to_pandas()["mean_amount"].tolist() == pytest.approx([130 / 3])
    checkpoint = source.execute()
    assert checkpoint.to_pandas()["mean_amount"].dropna().tolist() == (
        [20, 90] if axis == "entity" else [50, 30]
    )
    fixture.database.rename(tmp_path / "origin-offline.duckdb")
    result = (
        checkpoint.aggregate() if axis == "entity" else checkpoint.rollup(drop_time=True)
    ).execute()
    result_frame = result.to_pandas()
    assert result_frame["mean_amount"].tolist() == pytest.approx([130 / 3])
    assert result_frame["mean_amount"][0] != (55 if axis == "entity" else 40)
    semantics = result.row_contract.family_semantics
    assert isinstance(semantics, EntityReducedMetricSemantics)
    names = dict(semantics.metric_folds[0].components[0].state_columns)
    state = _single_state(fixture.runtime, result)
    assert state[names["sum"]] == 130
    assert state[names["non_null_count"]] == 3
    assert state[names["row_count"]] == 4
    if axis == "time":
        selected = checkpoint.where(gt(MEAN, 40)).rollup(drop_time=True).execute()
        assert selected.to_pandas()["mean_amount"].tolist() == [50]
        selected_state = _single_state(fixture.runtime, selected)
        assert selected_state[names["sum"]] == 100
        assert selected_state[names["non_null_count"]] == 2
    assert fixture.runtime.statistics.events.get("profile_resolution", 0) == 0
    assert fixture.runtime.statistics.events.get("credential_resolution", 0) == 0
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


@pytest.mark.parametrize("kind", ["local", "engine"])
def test_authored_conserving_allocation_folds_disjoint_contribution_rows(
    tmp_path: Path, kind: Literal["local", "engine"]
) -> None:
    fixture = setup_retained(tmp_path, kind)
    with duckdb.connect(str(fixture.database)) as database:
        database.execute("DELETE FROM orders")
        database.execute("DELETE FROM lines")
        database.execute("INSERT INTO orders(id,customer_id,amount) VALUES (1,1,100)")
        database.execute(
            "INSERT INTO lines(id,order_id,amount,weight,channel) SELECT allocation.id,orders.id,orders.amount*allocation.weight,allocation.weight,allocation.tag FROM orders CROSS JOIN (VALUES (1,0.4,'a'),(2,0.6,'b')) AS allocation(id,weight,tag)"
        )
        assert database.execute("SELECT SUM(weight),SUM(amount) FROM lines").fetchone() == (1, 100)
    original = fixture.sources._owner.semantic_registry
    allocated = ref.metric("sales.allocated_order_value")
    tag = ref.dimension("sales.lines.tag")
    registry = replace(
        original,
        metrics={
            **original.metrics,
            allocated.path: replace(
                original.metrics["sales.line_revenue"],
                semantic_id=allocated.path,
                name="allocated_order_value",
            ),
        },
        dimensions={
            **original.dimensions,
            tag.path: replace(
                original.dimensions["sales.orders.channel"],
                semantic_id=tag.path,
                entity="sales.lines",
                name="tag",
            ),
        },
    )
    registry.freeze()
    sources = fixture.runtime.sources(
        semantic_registry=registry, sidecar=fixture.sources._owner.sidecar
    )
    source = sources.observe(allocated, population=sources.population(ref.entity("sales.orders")))
    assert source.aggregate().execute().to_pandas()["allocated_order_value"].tolist() == [100]
    tagged = source.with_dimensions(tag).aggregate()
    semantics = tagged.row_contract.family_semantics
    assert isinstance(semantics, EntityReducedMetricSemantics)
    assert dict(semantics.metric_folds[0].axis_partitions)[tag.path] == "disjoint"
    assert tagged.rollup(drop_dimensions=(tag,)).execute().to_pandas()[
        "allocated_order_value"
    ].tolist() == [100]
    checkpoint = tagged.execute()
    assert checkpoint.to_pandas().to_dict("records") == [
        {"tag": "a", "allocated_order_value": 40},
        {"tag": "b", "allocated_order_value": 60},
    ]
    fixture.database.rename(tmp_path / "origin-offline.duckdb")
    assert checkpoint.rollup(drop_dimensions=(tag,)).execute().to_pandas()[
        "allocated_order_value"
    ].tolist() == [100]
    selected = checkpoint.where(eq(tag, "a")).rollup(drop_dimensions=(tag,)).execute()
    assert selected.to_pandas()["allocated_order_value"].tolist() == [40]
    assert fixture.runtime.statistics.events.get("profile_resolution", 0) == 0
    assert fixture.runtime.statistics.events.get("credential_resolution", 0) == 0
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()
