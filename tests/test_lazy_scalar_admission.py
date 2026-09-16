"""Independent pure admission boundaries for MySQL, SQLite, Trino and ClickHouse Group A."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from marivo.analysis import engine_sample
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.placement import SourceStep, place
from marivo.analysis.operators.registry import backend_execution, implementation
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from tests.lazy_observation_fixtures import NoIoActionPort
from tests.lazy_scalar_source_fixtures import registry_for


@pytest.mark.parametrize("engine", ["mysql", "sqlite", "trino", "clickhouse"])
@pytest.mark.parametrize(
    "unsupported",
    [None, "mean", "projected_mean", "relationship", "timestamp", "decimal_generic", "sampling"],
)
def test_complete_closure(
    engine: Literal["mysql", "sqlite", "trino", "clickhouse"], unsupported: str | None
) -> None:
    registry, sidecar = registry_for(Path("must-not-open.sqlite"), engine=engine)
    if unsupported in {"timestamp", "decimal_generic"}:
        entities = dict(registry.entities)
        entity = entities["sales.orders"]
        dtype = "timestamp" if unsupported == "timestamp" else "decimal"
        entities["sales.orders"] = replace(
            entity,
            source=replace(
                entity.source,
                columns=tuple(
                    (name, replace(binding, data_type=dtype) if name == "weight" else binding)
                    for name, binding in entity.source.columns
                ),
            ),
        )
        registry = replace(registry, entities=entities)
        registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="admission",
        store_id="admission",
    )
    revenue = ref.metric("sales.revenue")
    if unsupported in {"mean", "projected_mean"}:
        target = sources.observe([revenue, ref.metric("sales.mean_amount")])
        if unsupported == "projected_mean":
            target = target.metric(revenue)
    elif unsupported == "relationship":
        target = sources.observe(revenue).with_dimensions(ref.dimension("sales.customers.region"))
    elif unsupported == "sampling":
        target = sources.population(ref.entity("sales.orders")).sample(
            engine_sample(target_rows=2, seed=1)
        )
    else:
        target = (
            sources.observe(revenue)
            .with_dimensions(ref.dimension("sales.orders.channel"))
            .aggregate()
        )
    registration = implementation(target).for_backend(engine)
    if unsupported not in {None, "mean", "projected_mean", "relationship"}:
        assert registration is None
        with pytest.raises(DatasetCompilationError):
            place(target)
    else:
        assert registration is not None and registration.source
        execution = backend_execution(engine)
        assert execution is not None and not execution.retained_import
        graph = place(target)
        assert len(graph.steps) == 1 and isinstance(graph.steps[0], SourceStep)
        assert graph.steps[0].binding.adapter == engine


@pytest.mark.parametrize(
    "kind,engines",
    [
        ("int8", {"postgres", "mysql", "clickhouse"}),
        ("int16", {"postgres", "mysql", "clickhouse"}),
        ("boolean", {"postgres"}),
        ("timestamp", {"postgres"}),
        ("decimal", {"postgres", "mysql", "trino", "clickhouse"}),
        ("decimal(38, 6)", {"postgres", "mysql", "trino", "clickhouse"}),
        ("int64", {"postgres", "mysql", "sqlite", "trino", "clickhouse"}),
        ("float64", {"postgres", "mysql", "sqlite", "trino", "clickhouse"}),
        ("decimal(39,0)", set()),
        ("decimal(4,5)", set()),
        ("uint64", set()),
        ("timestamp('UTC')", set()),
    ],
)
def test_backend_type_boundaries_remain_distinct(kind: str, engines: set[str]) -> None:
    from marivo.analysis.operators import (
        clickhouse_support,
        mysql_support,
        postgres_support,
        sqlite_support,
        trino_support,
    )

    predicates = {
        "postgres": postgres_support.supported_type,
        "mysql": mysql_support.supported_type,
        "sqlite": sqlite_support.supported_type,
        "trino": trino_support.supported_type,
        "clickhouse": clickhouse_support.supported_type,
    }
    assert {engine for engine, predicate in predicates.items() if predicate(kind)} == engines
