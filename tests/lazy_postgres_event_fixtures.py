"""Read-only PostgreSQL Event journey acceptance with independent source rows."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import sql

from marivo.datasource.ir import TableSourceIR
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry
from tests.lazy_event_fixtures import make_event_registry
from tests.lazy_event_runtime_fixtures import END, OCCURRENCE_CANARY, START, THROUGH
from tests.multisource_environment import postgres_analysis as pg


@contextmanager
def event_source_tables() -> Iterator[dict[str, str]]:
    names = {
        logical: "c9_" + logical + "_" + uuid4().hex
        for logical in ("customers", "started_rows", "finished_rows")
    }
    with pg.connection(admin=True) as admin:
        try:
            admin.execute(
                sql.SQL("CREATE TABLE {} (id bigint, region text)").format(
                    sql.Identifier(names["customers"])
                )
            )
            for logical in ("started_rows", "finished_rows"):
                admin.execute(
                    sql.SQL(
                        "CREATE TABLE {} (occurrence_id bigint, customer_id bigint, "
                        "occurred_at timestamp)"
                    ).format(sql.Identifier(names[logical]))
                )
            for name in names.values():
                admin.execute(
                    sql.SQL("GRANT SELECT ON {} TO analysis_reader").format(sql.Identifier(name))
                )
            admin.execute(
                sql.SQL("INSERT INTO {} VALUES (1,'EU'),(2,'US'),(3,'EU')").format(
                    sql.Identifier(names["customers"])
                )
            )
            admin.execute(
                sql.SQL("INSERT INTO {} VALUES (%s,2,%s),(%s,1,%s),(%s,3,%s)").format(
                    sql.Identifier(names["started_rows"])
                ),
                (
                    OCCURRENCE_CANARY + 1,
                    START.replace(tzinfo=None),
                    OCCURRENCE_CANARY,
                    START.replace(tzinfo=None),
                    OCCURRENCE_CANARY + 2,
                    END.replace(tzinfo=None),
                ),
            )
            admin.execute(
                sql.SQL("INSERT INTO {} VALUES (%s,2,%s),(%s,1,%s)").format(
                    sql.Identifier(names["finished_rows"])
                ),
                (
                    OCCURRENCE_CANARY + 11,
                    THROUGH.replace(tzinfo=None),
                    OCCURRENCE_CANARY + 10,
                    START.replace(tzinfo=None),
                ),
            )
            yield names
        finally:
            for name in names.values():
                admin.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(name)))


def event_registry(
    names: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> tuple[Registry, CompiledExpressionSidecar]:
    registry, sidecar = make_event_registry(Path("unused.duckdb"))
    monkeypatch.setenv("MARIVO_TEST_POSTGRES_PASSWORD", pg.password())
    entities = dict(registry.entities)
    for logical, table in names.items():
        path = "sales." + logical
        entity = entities[path]
        assert isinstance(entity.source, TableSourceIR)
        entities[path] = replace(
            entity,
            source=replace(entity.source, table=table, database="public"),
        )
    registry = replace(
        registry,
        entities=entities,
        datasources={
            name: replace(
                datasource,
                backend_type="postgres",
                fields={
                    "host": pg.HOST,
                    "port": pg.PORT,
                    "database": pg.DATABASE,
                    "user": pg.READER,
                },
                env_refs={"password": "MARIVO_TEST_POSTGRES_PASSWORD"},
            )
            for name, datasource in registry.datasources.items()
        },
    )
    registry.freeze()
    return registry, sidecar
