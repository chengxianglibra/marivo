"""Shared declarations for isolated PostgreSQL Dataset acceptance."""

from dataclasses import replace
from pathlib import Path

import pytest

from marivo.datasource.ir import TableSourceIR
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry
from tests.lazy_execution_fixtures import make_execution_registry
from tests.multisource_environment import postgres_analysis as pg


def registry_for(
    table: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[Registry, CompiledExpressionSidecar]:
    original, sidecar = make_execution_registry(Path("unused.duckdb"))
    monkeypatch.setenv("MARIVO_TEST_POSTGRES_PASSWORD", pg.password())
    entities = dict(original.entities)
    entity = entities["sales.orders"]
    assert isinstance(entity.source, TableSourceIR)
    source = replace(
        entity.source,
        table=table,
        database="public",
        columns=tuple(
            (name, replace(binding, data_type="decimal") if name == "amount" else binding)
            for name, binding in entity.source.columns
        ),
    )
    entities["sales.orders"] = replace(entity, source=source)
    metrics = dict(original.metrics)
    for agg in ("min", "max"):
        metrics[f"sales.{agg}_amount"] = replace(
            metrics["sales.revenue"],
            semantic_id=f"sales.{agg}_amount",
            name=f"{agg}_amount",
            aggregation=agg,
        )
    registry = replace(
        original,
        entities=entities,
        metrics=metrics,
        datasources={
            name: replace(
                value,
                backend_type="postgres",
                fields={
                    "host": pg.HOST,
                    "port": pg.PORT,
                    "database": pg.DATABASE,
                    "user": pg.READER,
                },
                env_refs={"password": "MARIVO_TEST_POSTGRES_PASSWORD"},
            )
            for name, value in original.datasources.items()
        },
    )
    registry.freeze()
    return registry, sidecar
