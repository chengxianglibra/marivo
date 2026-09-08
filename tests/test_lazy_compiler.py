"""Independent numerical and graph-order acceptance for the source compiler."""

from pathlib import Path

import pyarrow as pa
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.compiler import compile_dataset
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.observation.predicates import eq, gt
from marivo.refs import ref
from tests.lazy_execution_fixtures import assert_compiled_validations, execution_fixture

METRICS = tuple(
    ref.metric(f"sales.{name}")
    for name in (
        "revenue",
        "order_count",
        "mean_amount",
        "weighted_amount",
        "conversion_rate",
        "cross_root_ratio",
    )
)


def test_independent_components_share_population_without_fanout(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        population = fixture.sources.population(ref.entity("sales.customers"))
        dataset = fixture.sources.observe(METRICS, population=population)
        compiled = compile_dataset(dataset, fixture.tables(dataset))
        assert_compiled_validations(compiled.validations)
        rows = compiled.expression.select(*compiled.primary_columns).to_pyarrow().to_pylist()
        assert [row["entity_identity"] for row in rows] == [
            {"id": number} for number in (1, 2, 3, 4)
        ]
        assert rows[0] == {
            "entity_identity": {"id": 1},
            "revenue": 40,
            "order_count": 2,
            "mean_amount": 20,
            "weighted_amount": 25,
            "conversion_rate": 20,
            "cross_root_ratio": 15 / 40,
        }
        assert rows[1]["cross_root_ratio"] == 0.5
        assert rows[2]["weighted_amount"] is None
        assert rows[2]["cross_root_ratio"] is None
        assert rows[3]["revenue"] is None and rows[3]["order_count"] == 0
        assert len(compiled.retained_parts) == 6
        assert all(part.column_names[0] == "entity_identity" for part in compiled.retained_parts)
        aggregate = dataset.aggregate()
        total = (
            compile_dataset(aggregate, fixture.tables(aggregate))
            .expression.to_pyarrow()
            .to_pylist()[0]
        )
        assert total["revenue"] == 147
        assert total["order_count"] == 5
        assert total["mean_amount"] == pytest.approx(147 / 5)
        assert total["weighted_amount"] == 50
        assert total["cross_root_ratio"] == pytest.approx(65 / 147)
        compiled_aggregate = compile_dataset(aggregate, fixture.tables(aggregate))
        reader = fixture.backend.to_pyarrow_batches(compiled_aggregate.expression)
        try:
            batch = reader.read_next_batch()
            assert batch.schema.field("order_count").type == pa.int64()
            assert all(
                field.type == pa.int64() for field in batch.schema if field.name.endswith("count")
            )
        finally:
            reader.close()


def test_where_preserves_exact_states_before_reduction_and_projection(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        dataset = fixture.sources.observe(
            METRICS, population=fixture.sources.population(ref.entity("sales.customers"))
        )
        filtered = dataset.where(gt(ref.metric("sales.revenue"), 10)).aggregate()
        compiled = compile_dataset(filtered, fixture.tables(filtered))
        result = compiled.expression.to_pyarrow().to_pylist()[0]
        assert result["mean_amount"] == pytest.approx(140 / 3)
        assert result["weighted_amount"] == 50
        assert result["cross_root_ratio"] == pytest.approx(65 / 140)
        projected = filtered.metric(ref.metric("sales.cross_root_ratio"))
        result2 = compile_dataset(projected, fixture.tables(projected))
        assert result2.primary_columns == ("cross_root_ratio",)
        assert len(result2.retained_parts) == 1


@pytest.mark.parametrize(
    "dimensions,time_axis,reduced",
    [(d, t, r) for d in (False, True) for t in (False, True) for r in (False, True)],
)
def test_eight_shapes_keep_null_coordinate_spines(
    tmp_path: Path, dimensions: bool, time_axis: bool, reduced: bool
) -> None:
    with execution_fixture(tmp_path) as fixture:
        dataset = fixture.sources.observe(ref.metric("sales.revenue"))
        if dimensions:
            dataset = dataset.with_dimensions(ref.dimension("sales.customers.region"))
        if time_axis:
            dataset = dataset.with_time_axis(
                ref.time_dimension("sales.orders.order_time"), grain=grain("day")
            )
        if reduced:
            dataset = dataset.aggregate()
        compiled = compile_dataset(dataset, fixture.tables(dataset))
        rows = compiled.expression.to_pyarrow().to_pylist()
        assert sum(row["revenue"] or 0 for row in rows) == 147
        assert compiled.primary_columns == tuple(field.name for field in dataset.schema.columns)
        if dimensions:
            assert any(row["region"] is None for row in rows)
        if time_axis:
            assert any(row["order_time"] is None for row in rows)


def test_population_filter_and_version_resolution(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        population = fixture.sources.population(ref.entity("sales.orders")).where(
            eq(ref.dimension("sales.customers.region"), "EU")
        )
        sources: tuple[LogicalDataset, ...] = (
            population,
            fixture.sources.population(ref.entity("sales.composite")),
            fixture.sources.population(
                ref.entity("sales.snapshots"),
                time_scope=time_scope(start="2026-02-01", end="2026-03-01"),
            ),
            fixture.sources.population(
                ref.entity("sales.validity"),
                time_scope=time_scope(start="2026-02-01", end="2026-03-01"),
            ),
        )
        expected = (
            [{"id": i} for i in (1, 2, 5, 6)],
            [{"tenant": "a", "id": 1}, {"tenant": "a", "id": 2}, {"tenant": "b", "id": 1}],
            [{"id": 1}, {"id": 2}],
            [{"id": 1}, {"id": 2}],
        )
        for dataset, identities in zip(sources, expected, strict=True):
            compiled = compile_dataset(dataset, fixture.tables(dataset))
            assert_compiled_validations(compiled.validations)
            assert compiled.expression.to_pyarrow()["entity_identity"].to_pylist() == identities


@pytest.mark.parametrize(
    "entity,mutation,expected",
    [
        ("orders", "INSERT INTO orders (id) VALUES (1)", "source_row_unique"),
        ("orders", "INSERT INTO orders (id) VALUES (NULL)", "identity_non_null"),
        (
            "snapshots",
            "INSERT INTO snapshots (id, day) VALUES (1, DATE '2026-02-28')",
            "source_row_unique",
        ),
        (
            "snapshots",
            "DELETE FROM snapshots WHERE day = DATE '2026-02-28'",
            "exact_snapshot_available",
        ),
        (
            "validity",
            "INSERT INTO validity (id, start, \"end\") VALUES (1, DATE '2026-02-11', DATE '2026-02-20')",
            "validity_non_overlapping",
        ),
        (
            "validity",
            "INSERT INTO validity (id, start, \"end\") VALUES (3, DATE '2026-02-20', DATE '2026-02-01')",
            "validity_well_formed",
        ),
    ],
)
def test_invalid_identity_and_temporal_sources_have_named_preflights(
    tmp_path: Path, entity: str, mutation: str, expected: str
) -> None:
    with execution_fixture(tmp_path) as fixture:
        fixture.backend.raw_sql(mutation)
        dataset = fixture.sources.population(
            ref.entity(f"sales.{entity}"),
            time_scope=(
                time_scope(start="2026-02-01", end="2026-03-01") if entity != "orders" else None
            ),
        )
        compiled = compile_dataset(dataset, fixture.tables(dataset))
        failures = [
            validation.name
            for validation in compiled.validations
            if validation.expression.to_pyarrow()["violations"][0].as_py() > 0
        ]
        assert f"sales.{entity}.{expected}" in failures


def test_membership_and_observation_windows_are_independent(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        population = fixture.sources.population(
            ref.entity("sales.orders"), time_scope=time_scope(start="2026-02-02", end="2026-02-04")
        )
        dataset = fixture.sources.observe(
            ref.metric("sales.revenue"),
            population=population,
            time_scope=time_scope(start="2026-02-03", end="2026-02-05"),
        )
        rows = compile_dataset(dataset, fixture.tables(dataset)).expression.to_pyarrow().to_pylist()
        assert [row["entity_identity"] for row in rows] == [{"id": i} for i in (1, 2, 3, 4)]
        assert [row["revenue"] for row in rows] == [None, 30, None, None]


def test_empty_population_has_zero_rows_and_scalar_has_one_null_row(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        population = fixture.sources.population(ref.entity("sales.orders")).where(
            eq(ref.dimension("sales.customers.region"), "missing")
        )
        empty = fixture.sources.observe(
            (ref.metric("sales.revenue"), ref.metric("sales.order_count")), population=population
        )
        assert compile_dataset(empty, fixture.tables(empty)).expression.to_pyarrow().num_rows == 0
        scalar = empty.aggregate()
        row = compile_dataset(scalar, fixture.tables(scalar)).expression.to_pyarrow().to_pylist()[0]
        assert row["revenue"] is None and row["order_count"] == 0
        assert all(value == 0 for name, value in row.items() if name.endswith("count"))


def test_compile_is_pure_after_declared_table_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with execution_fixture(tmp_path) as fixture:
        dataset = (
            fixture.sources.observe(ref.metric("sales.revenue"))
            .with_dimensions(ref.dimension("sales.customers.region"))
            .where(gt(ref.metric("sales.revenue"), 2))
            .aggregate()
        )
        tables = fixture.tables(dataset)

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("compilation must not execute source or row work")

        monkeypatch.setattr(fixture.backend, "raw_sql", forbidden)
        monkeypatch.setattr(fixture.backend, "execute", forbidden)
        monkeypatch.setattr(fixture.backend, "to_pyarrow", forbidden)
        compiled = compile_dataset(dataset, tables)
        assert compiled.primary_columns == ("region", "revenue")


@pytest.mark.parametrize("population_name", ["snapshots", "validity"])
def test_version_selected_population_filters_fact_contributions(
    tmp_path: Path, population_name: str
) -> None:
    from dataclasses import replace

    from marivo.analysis.session._lazy_sources import make_lazy_sources
    from tests.lazy_observation_fixtures import NoIoActionPort

    with execution_fixture(tmp_path) as fixture:
        relation = replace(
            fixture.registry.relationships["sales.order_customer"],
            semantic_id="sales.version_member",
            to_entity=f"sales.{population_name}",
        )
        registry = replace(
            fixture.registry,
            relationships={**fixture.registry.relationships, relation.semantic_id: relation},
        )
        registry.freeze()
        sources = make_lazy_sources(
            semantic_registry=registry,
            sidecar=fixture.sidecar,
            action_port=NoIoActionPort(),
            session_id="session-version",
            store_id="store-version",
        )
        population = sources.population(
            ref.entity(f"sales.{population_name}"),
            time_scope=time_scope(start="2026-02-01", end="2026-03-01"),
        )
        dataset = sources.observe(
            ref.metric("sales.revenue"),
            population=population,
            time_scope=time_scope(start="2026-02-02", end="2026-02-03"),
        )
        compiled = compile_dataset(dataset, fixture.tables(dataset))
        assert_compiled_validations(compiled.validations)
        rows = compiled.expression.to_pyarrow().to_pylist()
        assert [row["entity_identity"] for row in rows] == [{"id": 1}, {"id": 2}]
        assert [row["revenue"] for row in rows] == [10, 100]


def test_recursive_ratio_recomputes_from_leaf_components(tmp_path: Path) -> None:
    from dataclasses import replace

    from marivo.analysis.session._lazy_sources import make_lazy_sources
    from marivo.semantic.ir import RatioComposition
    from tests.lazy_observation_fixtures import NoIoActionPort

    with execution_fixture(tmp_path) as fixture:
        metric = replace(
            fixture.registry.metrics["sales.cross_root_ratio"],
            semantic_id="sales.recursive_ratio",
            name="recursive_ratio",
            composition=RatioComposition("sales.conversion_rate", "sales.cross_root_ratio"),
        )
        registry = replace(
            fixture.registry, metrics={**fixture.registry.metrics, metric.semantic_id: metric}
        )
        registry.freeze()
        sources = make_lazy_sources(
            semantic_registry=registry,
            sidecar=fixture.sidecar,
            action_port=NoIoActionPort(),
            session_id="session-recursive",
            store_id="store-recursive",
        )
        dataset = sources.observe(
            ref.metric("sales.recursive_ratio"),
            population=sources.population(ref.entity("sales.customers")),
        ).aggregate()
        compiled = compile_dataset(dataset, fixture.tables(dataset))
        assert compiled.expression.to_pyarrow()["recursive_ratio"][0].as_py() == pytest.approx(
            (147 / 5) / (65 / 147)
        )


def test_unregistered_time_representation_fails_during_pure_compilation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    import ibis

    from marivo.analysis.compiler.errors import DatasetCompilationError
    from marivo.analysis.compiler.normalize import required_entities
    from marivo.analysis.session._lazy_sources import make_lazy_sources
    from marivo.datasource.ir import TableSourceIR
    from tests.lazy_observation_fixtures import NoIoActionPort

    with execution_fixture(tmp_path) as fixture:
        entity = fixture.registry.entities["sales.orders"]
        assert isinstance(entity.source, TableSourceIR)
        source = replace(
            entity.source,
            columns=tuple(
                (name, replace(binding, data_type="string") if name == "day" else binding)
                for name, binding in entity.source.columns
            ),
        )
        registry = replace(
            fixture.registry,
            entities={
                **fixture.registry.entities,
                entity.semantic_id: replace(entity, source=source),
            },
        )
        registry.freeze()
        sources = make_lazy_sources(
            semantic_registry=registry,
            sidecar=fixture.sidecar,
            action_port=NoIoActionPort(),
            session_id="session-temporal",
            store_id="store-temporal",
        )
        dataset = sources.observe(
            ref.metric("sales.revenue"), time_scope=time_scope(start="2026-02-01", end="2026-03-01")
        )
        tables = {
            entity.ref.path: ibis.table(dict(entity.columns), name=entity.ref.path)
            for entity in required_entities(dataset)
        }
        with pytest.raises(
            DatasetCompilationError, match="unsupported temporal source representation"
        ):
            compile_dataset(dataset, tables)
