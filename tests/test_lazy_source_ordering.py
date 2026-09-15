"""Independent rank/limit semantics through source compilation and v3 recovery."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Literal

import duckdb
import pytest

from marivo.analysis import grain
from marivo.analysis.compiler import compile_dataset
from marivo.analysis.datasets.descriptors import (
    _generated_identity,
    _make_field,
    _make_field_id,
    _make_row_contract,
    _make_schema,
    _OrderedOrdering,
    _StaticRowBound,
)
from marivo.analysis.datasets.errors import DatasetConstructionError, DatasetOwnershipError
from marivo.analysis.datasets.handles import LogicalRootHandle, MaterializedScanLeafHandle
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset
from marivo.analysis.observation.predicates import gte, lte
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from tests.lazy_execution_fixtures import (
    ExecutionFixture,
    execution_fixture,
    make_execution_registry,
    seed_execution_database,
)
from tests.lazy_observation_fixtures import NoIoActionPort, make_sources

pytestmark = pytest.mark.runtime

REVENUE = ref.metric("sales.revenue")
REGION = ref.dimension("sales.customers.region")
CHANNEL = ref.dimension("sales.orders.channel")
DAY = ref.time_dimension("sales.orders.order_time")


def test_rank_family_rejects_a_different_generated_producer_identity() -> None:
    source = make_sources().observe(REVENUE)
    ranked = source.rank(source.fields.metric(REVENUE))
    generated = ranked.schema.columns[-1]
    corrupt = _make_field(
        field_id=generated.field_id,
        name=generated.name,
        role_id=generated.role_id,
        identity=_generated_identity(_make_field_id("generated.other@v1")),
        derivation_identity=generated.derivation_identity,
        logical_type_id=generated.logical_type_id,
        physical_type_state=generated.physical_type_state,
        nullable=generated.nullable,
        ids=ranked._registration.ids,
    )
    row = _make_row_contract(
        schema_version=1,
        shape_id=ranked.row_contract.shape_id,
        schema=_make_schema((*ranked.schema.columns[:-1], corrupt)),
        coordinate_field_ids=ranked.row_contract.coordinate_field_ids,
        key_field_ids=ranked.row_contract.key_field_ids,
        family_semantics=ranked.row_contract.family_semantics,
    )
    with pytest.raises(DatasetConstructionError, match="generated rank"):
        ranked._registration.row_validator(row, ranked.row_set_contract)


def _rank_rows(fixture: ExecutionFixture) -> LogicalMetricDataset:
    fixture.backend.raw_sql("DELETE FROM orders")
    # Physical insertion order disagrees with both numeric rank and identity order.
    fixture.backend.raw_sql(
        "INSERT INTO orders (id, amount) VALUES "
        "(9, 30), (7, '-Infinity'::DOUBLE), (3, 10), (1, 20), "
        "(8, 10), (5, 'NaN'::DOUBLE), (4, NULL), (2, 20), (6, 'Infinity'::DOUBLE)"
    )
    return fixture.sources.observe(REVENUE)


def _rows(fixture: ExecutionFixture, dataset: LogicalMetricDataset) -> list[dict[str, object]]:
    recipe = compile_dataset(dataset, fixture.tables(dataset))
    for validation in recipe.validations:
        assert validation.expression.to_pyarrow()["violations"][0].as_py() == 0, validation.name
    return recipe.expression.select(*recipe.primary_columns).to_pyarrow().to_pylist()


@pytest.mark.parametrize("order", ["ascending", "descending"])
@pytest.mark.parametrize("ties", ["ordinal", "dense", "min", "max"])
def test_rank_ties_directions_and_nonfinite_rows_have_independent_expected_values(
    tmp_path: Path,
    order: Literal["ascending", "descending"],
    ties: Literal["ordinal", "dense", "min", "max"],
) -> None:
    expected_ids = {
        "ascending": [3, 8, 1, 2, 9, 4, 5, 6, 7],
        "descending": [9, 1, 2, 3, 8, 4, 5, 6, 7],
    }
    expected_ranks = {
        "ascending": {
            "ordinal": [1, 2, 3, 4, 5],
            "dense": [1, 1, 2, 2, 3],
            "min": [1, 1, 3, 3, 5],
            "max": [2, 2, 4, 4, 5],
        },
        "descending": {
            "ordinal": [1, 2, 3, 4, 5],
            "dense": [1, 2, 2, 3, 3],
            "min": [1, 2, 2, 4, 4],
            "max": [1, 3, 3, 5, 5],
        },
    }
    with execution_fixture(tmp_path) as fixture:
        source = _rank_rows(fixture)
        ranked = source.rank(source.fields.metric(REVENUE), order=order, ties=ties)
        rows = _rows(fixture, ranked)
        assert [row["entity_identity"]["id"] for row in rows] == expected_ids[order]
        assert [row["rank"] for row in rows] == [
            *expected_ranks[order][ties],
            None,
            None,
            None,
            None,
        ]
        assert ranked.row_contract.key_field_ids == source.row_contract.key_field_ids
        assert ranked.row_contract.coordinate_field_ids == source.row_contract.coordinate_field_ids
        assert ranked.row_contract.family_semantics == source.row_contract.family_semantics
        assert ranked.row_set_contract.cardinality == source.row_set_contract.cardinality
        generated = ranked.schema.columns[-1]
        assert (generated.field_id.value, generated.name, generated.role_id) == (
            "generated.rank@v1",
            "rank",
            "rank",
        )
        assert generated.logical_type_id == "int64" and generated.nullable
        assert [
            row["entity_identity"]["id"] for row in _rows(fixture, ranked.limit(3))
        ] == expected_ids[order][:3]


def test_rank_partition_order_uses_canonical_coordinates_and_exact_current_groups(
    tmp_path: Path,
) -> None:
    with execution_fixture(tmp_path) as fixture:
        fixture.backend.raw_sql(
            "UPDATE orders SET channel = CASE id WHEN 1 THEN 'a' WHEN 2 THEN 'b' "
            "WHEN 3 THEN 'b' WHEN 4 THEN 'a' WHEN 5 THEN 'c' ELSE 'a' END"
        )
        fixture.backend.raw_sql("UPDATE orders SET day = DATE '2026-02-02' WHERE id = 5")
        source = (
            fixture.sources.observe(REVENUE)
            .with_dimensions(CHANNEL, REGION)
            .with_time_axis(DAY, grain=grain("day"))
            .aggregate()
        )
        by = source.fields.metric(REVENUE)
        region, day = source.fields.dimension(REGION), source.fields.dimension(DAY)
        ranked = source.rank(by, partition_by=(day, region))
        same = source.rank(by, partition_by=(region, day))
        assert ranked.definition_fingerprint == same.definition_fingerprint
        ordering = ranked.row_set_contract.ordering
        assert isinstance(ordering, _OrderedOrdering)
        names = {field.field_id: field.name for field in ranked.schema.columns}
        assert [names[term.field_id] for term in ordering.terms] == [
            "region",
            "order_time",
            "rank",
            "channel",
        ]
        assert all(
            term.direction == "ascending" and term.nulls == "last" for term in ordering.terms
        )
        rows = _rows(fixture, ranked)
        assert [
            (row["region"], str(row["order_time"]), row["channel"], row["rank"]) for row in rows
        ] == [
            ("EU", "2026-02-02", "a", 1),
            ("EU", "2026-02-02", "c", 2),
            ("EU", "2026-02-03", "b", 1),
            ("EU", "None", "a", 1),
            (None, "2026-02-02", "b", 1),
            (None, "2026-02-03", "a", None),
        ]


def test_composite_identity_is_the_ordinal_tie_breaker(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        original = fixture.registry.metrics[REVENUE.path]
        path = "sales.composite_revenue"
        metric = replace(
            original,
            semantic_id=path,
            name="composite_revenue",
            entities=("sales.composite",),
            measure="sales.composite.amount",
            aggregation_target="sales.composite.amount",
        )
        registry = replace(fixture.registry, metrics={**fixture.registry.metrics, path: metric})
        registry.freeze()
        sources = make_lazy_sources(
            semantic_registry=registry,
            sidecar=fixture.sidecar,
            action_port=NoIoActionPort(),
            session_id="composite-ordering",
            store_id="composite-ordering",
        )
        fixture.backend.raw_sql("UPDATE composite SET amount = 10")
        reference = ref.metric(path)
        source = sources.observe(reference)
        ranked = source.rank(source.fields.metric(reference))
        rows = _rows(fixture, ranked)
        assert [row["entity_identity"] for row in rows] == [
            {"tenant": "a", "id": 1},
            {"tenant": "a", "id": 2},
            {"tenant": "b", "id": 1},
        ]
        assert [row["rank"] for row in rows] == [1, 2, 3]


def test_rank_where_limit_preserves_authored_selection_order_and_the_generated_field(
    tmp_path: Path,
) -> None:
    with execution_fixture(tmp_path) as fixture:
        source = _rank_rows(fixture)
        ranked = source.rank(source.fields.metric(REVENUE))
        selected = ranked.where(gte(ranked.fields.get("rank"), 2)).limit(2)
        rows = _rows(fixture, selected)
        assert [row["entity_identity"]["id"] for row in rows] == [1, 2]
        assert [row["rank"] for row in rows] == [2, 3]
        assert selected.row_contract == ranked.row_contract
        assert selected.row_set_contract.ordering == ranked.row_set_contract.ordering
        assert _rows(fixture, selected.limit(10)) == rows
        narrower = selected.limit(1)
        assert _rows(fixture, narrower) == rows[:1]
        assert isinstance(narrower.row_set_contract.cardinality.row_bound, _StaticRowBound)
        assert narrower.row_set_contract.cardinality.row_bound.max_rows == 1
        assert isinstance(narrower._root, LogicalRootHandle)
        assert narrower._root.inputs[0].root is selected._root


def test_rank_admission_rejects_unregistered_shapes_and_invalid_selectors_without_io() -> None:
    sources = make_sources()
    source = sources.observe(REVENUE)
    selector = source.fields.metric(REVENUE)
    assert source.rank(selector).kind == "metric"
    rejected = (
        source.aggregate(),
        source.with_dimensions(REGION),
        source.with_time_axis(DAY, grain=grain("day")),
        source.with_dimensions(REGION).with_time_axis(DAY, grain=grain("day")),
    )
    for dataset in rejected:
        with pytest.raises(DatasetConstructionError, match="shape"):
            dataset.rank(dataset.fields.metric(REVENUE))
    with pytest.raises(DatasetConstructionError, match="unordered"):
        source.limit(1)
    ranked = source.rank(selector)
    with pytest.raises(DatasetConstructionError, match="repeated rank"):
        ranked.rank(ranked.fields.get("rank"))
    for order, ties in (("sideways", "ordinal"), ("descending", "average")):
        with pytest.raises(DatasetConstructionError):
            source.rank(selector, order=order, ties=ties)
    for partitions in (
        [source.fields.get("entity_identity")],
        (selector,),
        (source.fields.get("entity_identity"),) * 2,
    ):
        with pytest.raises(DatasetConstructionError):
            source.rank(selector, partition_by=partitions)
    grouped = source.with_dimensions(REGION).aggregate()
    with pytest.raises(DatasetConstructionError):
        grouped.rank(grouped.fields.dimension(REGION))
    with pytest.raises(DatasetConstructionError):
        source.rank(REVENUE)
    foreign = make_sources(session_id="foreign-rank").observe(REVENUE)
    with pytest.raises(DatasetOwnershipError):
        source.rank(foreign.fields.metric(REVENUE))
    original = sources._owner.semantic_registry
    changed = replace(
        original,
        metrics={
            **original.metrics,
            REVENUE.path: replace(original.metrics[REVENUE.path], aggregation="mean"),
        },
    )
    changed.freeze()
    current = make_lazy_sources(
        semantic_registry=changed,
        sidecar=sources._owner.sidecar,
        action_port=NoIoActionPort(),
        session_id=sources._owner.session_id,
        store_id=sources._owner.store_id,
    ).observe(REVENUE)
    with pytest.raises(DatasetConstructionError):
        current.rank(selector)


@pytest.mark.parametrize("count", [True, False, 0, -1, 100001, 1.0, "1", None])
def test_limit_rejects_non_exact_or_unbounded_counts(count: object) -> None:
    source = make_sources().observe(REVENUE)
    with pytest.raises(DatasetConstructionError, match="limit count"):
        source.rank(source.fields.metric(REVENUE)).limit(count)


def test_empty_rank_keeps_nullable_rank_schema_and_its_bound(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        source = fixture.sources.observe(REVENUE).where(gte(REVENUE, 1000))
        ranked = source.rank(source.fields.metric(REVENUE)).limit(100000)
        assert _rows(fixture, ranked) == []
        assert ranked.schema.columns[-1].logical_type_id == "int64"
        assert ranked.schema.columns[-1].nullable
        assert ranked.row_set_contract.cardinality.row_bound.max_rows == 100000


_COLD_READ = """
import json
import sys
from pathlib import Path
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.observation.metric import MaterializedMetricDataset

def forbidden(*args, **kwargs):
    raise AssertionError("cold recovery must not perform source or compiler work")

for name in ("compile_dataset", "require_profile_for_backend_type", "_effective_kwargs", "_build_backend_from_effective"):
    setattr(admission, name, forbidden)
runtime = DatasetRuntime.open(Path(sys.argv[1]), sys.argv[2])
artifact = runtime.artifact(sys.argv[3])
assert isinstance(artifact, MaterializedMetricDataset)
rows = artifact.to_pandas()
print(json.dumps({"identities": rows["entity_identity"].tolist(), "ranks": rows["rank"].tolist(), "bound": artifact.row_set_contract.cardinality.row_bound.max_rows, "order": [term.field_id.value for term in artifact.row_set_contract.ordering.terms], "queries": runtime.statistics.primary_queries, "events": runtime.statistics.events}))
"""


def test_ranked_prefix_publishes_v3_and_cold_recovers_without_any_source_work(
    tmp_path: Path,
) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(tmp_path, "ordering-acceptance")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    source = sources.observe(REVENUE)
    # Retained construction is a scan leaf even though its execution belongs to 3b/4a.
    checkpoint = source.execute()
    retained_rank = checkpoint.rank(checkpoint.fields.metric(REVENUE))
    assert isinstance(retained_rank._root, LogicalRootHandle)
    assert retained_rank._root.inputs[0].root is checkpoint._root
    assert isinstance(checkpoint._root, MaterializedScanLeafHandle)
    ranked = source.rank(source.fields.metric(REVENUE))
    limited = ranked.where(lte(ranked.fields.get("rank"), 3)).limit(2)
    result = limited.execute()
    assert isinstance(result, MaterializedMetricDataset)
    assert result.row_contract == limited.row_contract
    assert result.row_set_contract == limited.row_set_contract
    frame = result.to_pandas()
    assert frame["entity_identity"].tolist() == [(3,), (2,)]
    assert frame["rank"].tolist() == [1, 2]
    assert runtime.statistics.primary_queries == 1
    assert runtime.statistics.transferred_rows == 2
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.evidence.finding_count == 0
    assert record.descriptor.storage_receipt.realized_row_count == 2
    assert len(record.descriptor.retained_parts) == 1
    assert record.descriptor.retained_parts[0].storage_receipt.realized_row_count == 2
    run = runtime.store.run(record.producing_run_ref)
    assert run is not None and run.lifecycle == "succeeded"
    with sqlite3.connect(runtime.store.db_path.as_uri() + "?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
    retained_limit = result.limit(1)
    assert isinstance(retained_limit._root, LogicalRootHandle)
    assert retained_limit._root.inputs[0].root is result._root
    assert retained_limit.row_contract == result.row_contract
    database.rename(tmp_path / "warehouse.unavailable")
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            _COLD_READ,
            str(tmp_path),
            runtime.session_ref,
            result.state.artifact_ref.ref,
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    recovered = json.loads(completed.stdout)
    assert recovered == {
        "identities": [[3], [2]],
        "ranks": [1, 2],
        "bound": 2,
        "order": ["generated.rank@v1", "identity.entity_identity@v1"],
        "queries": 0,
        "events": {},
    }


@pytest.mark.parametrize("shape", ["dimension", "time"])
def test_reduced_dimension_and_time_shapes_use_rank_order(tmp_path: Path, shape: str) -> None:
    with execution_fixture(tmp_path) as fixture:
        source = fixture.sources.observe(REVENUE)
        if shape == "dimension":
            source = source.with_dimensions(REGION).aggregate()
            expected = [(None, 100.0, 1), ("EU", 47.0, 2)]
            coordinate = "region"
        else:
            source = source.with_time_axis(DAY, grain=grain("day")).aggregate()
            expected = [
                ("2026-02-02", 110.0, 1),
                ("2026-02-03", 30.0, 2),
                (None, 7.0, 3),
                ("2026-02-04", 0.0, 4),
            ]
            coordinate = "order_time"
        ranked = source.rank(source.fields.metric(REVENUE))
        rows = _rows(fixture, ranked)
        assert [
            (
                str(row[coordinate]) if row[coordinate] is not None else None,
                row["revenue"],
                row["rank"],
            )
            for row in rows
        ] == expected


def test_high_cardinality_rank_limit_transfers_only_the_ordered_source_prefix(
    tmp_path: Path,
) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    connection = duckdb.connect(str(database))
    try:
        connection.execute("DELETE FROM orders")
        connection.execute(
            "INSERT INTO orders (id, amount) SELECT i, i::DOUBLE FROM range(1, 100006) AS ids(i)"
        )
    finally:
        connection.close()
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(tmp_path, "high-cardinality-ordering")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    source = sources.observe(REVENUE)
    result = source.rank(source.fields.metric(REVENUE)).limit(3).execute()
    assert runtime.statistics.primary_queries == 1
    assert runtime.statistics.transferred_rows == 3
    frame = result.to_pandas()
    assert frame["entity_identity"].tolist() == [(100005,), (100004,), (100003,)]
    assert frame["rank"].tolist() == [1, 2, 3]
