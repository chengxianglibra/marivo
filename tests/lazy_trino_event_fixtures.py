"""Disposable Iceberg Event sources shared by journey and Lifecycle acceptance."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from marivo.datasource.ir import TableSourceIR
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry
from tests.lazy_event_fixtures import make_event_registry
from tests.multisource_environment import trino_analysis as trino


@contextmanager
def event_source_tables() -> Iterator[dict[str, str]]:
    names = {
        logical: "c9_" + logical + "_" + uuid4().hex
        for logical in ("customers", "started_rows", "finished_rows")
    }
    with trino.connection(admin=True) as admin:
        cursor = admin.cursor()
        try:
            cursor.execute(
                f"CREATE TABLE {names['customers']} (id BIGINT, region VARCHAR)"
            ).fetchall()
            for logical in ("started_rows", "finished_rows"):
                cursor.execute(
                    f"CREATE TABLE {names[logical]} (occurrence_id BIGINT, customer_id BIGINT, occurred_at TIMESTAMP(6))"
                ).fetchall()
            cursor.execute(
                f"INSERT INTO {names['customers']} VALUES (1,'EU'),(2,'US'),(3,'EU')"
            ).fetchall()
            cursor.execute(
                f"INSERT INTO {names['started_rows']} VALUES (21,2,TIMESTAMP '2026-02-01 00:00:00.000001'),(11,1,TIMESTAMP '2026-02-01 00:00:00.000001'),(31,3,TIMESTAMP '2026-02-02 00:00:00')"
            ).fetchall()
            cursor.execute(
                f"INSERT INTO {names['finished_rows']} VALUES (22,2,TIMESTAMP '2026-02-03 00:00:00'),(12,1,TIMESTAMP '2026-02-01 00:00:00.000003')"
            ).fetchall()
            yield names
        finally:
            for name in names.values():
                cursor.execute(f"DROP TABLE IF EXISTS {name}").fetchall()
            cursor.close()


def event_registry(names: dict[str, str]) -> tuple[Registry, CompiledExpressionSidecar]:
    registry, sidecar = make_event_registry(Path("unused.duckdb"))
    entities = dict(registry.entities)
    for logical, table in names.items():
        entity = entities["sales." + logical]
        assert isinstance(entity.source, TableSourceIR)
        entities["sales." + logical] = replace(
            entity, source=replace(entity.source, table=table, database="analysis")
        )
    registry = replace(
        registry,
        entities=entities,
        datasources={
            name: replace(
                value,
                backend_type="trino",
                fields={
                    "host": "127.0.0.1",
                    "port": 18080,
                    "catalog": "iceberg",
                    "schema": "analysis",
                    "user": "analysis_reader",
                },
                env_refs={},
            )
            for name, value in registry.datasources.items()
        },
    )
    registry.freeze()
    return registry, sidecar
