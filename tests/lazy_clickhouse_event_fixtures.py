"""Disposable MergeTree Event sources shared by journey and Lifecycle acceptance."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from marivo.datasource.ir import TableSourceIR
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry
from tests.lazy_event_fixtures import make_event_registry
from tests.lazy_event_runtime_fixtures import END, OCCURRENCE_CANARY, START, THROUGH
from tests.multisource_environment import clickhouse_analysis as ch
from tests.multisource_environment.credentials import password


@contextmanager
def event_source_tables() -> Iterator[dict[str, str]]:
    names = {
        logical: "c9_" + logical + "_" + uuid4().hex
        for logical in ("customers", "started_rows", "finished_rows")
    }
    with ch.connection(admin=True) as admin:
        try:
            admin.command(
                f"CREATE TABLE {names['customers']} (id Nullable(Int64), region String) ENGINE=MergeTree ORDER BY tuple()"
            )
            for logical in ("started_rows", "finished_rows"):
                admin.command(
                    f"CREATE TABLE {names[logical]} (occurrence_id Nullable(Int64), customer_id Nullable(Int64), occurred_at Nullable(DateTime64(6, 'UTC'))) ENGINE=MergeTree ORDER BY tuple()"
                )
            admin.insert(names["customers"], [(1, "EU"), (2, "US"), (3, "EU")])
            admin.insert(
                names["started_rows"],
                [
                    (OCCURRENCE_CANARY + 1, 2, START),
                    (OCCURRENCE_CANARY, 1, START),
                    (OCCURRENCE_CANARY + 2, 3, END),
                ],
            )
            admin.insert(
                names["finished_rows"],
                [(OCCURRENCE_CANARY + 11, 2, THROUGH), (OCCURRENCE_CANARY + 10, 1, START)],
            )
            yield names
        finally:
            for name in names.values():
                admin.command(f"DROP TABLE IF EXISTS {name} SYNC")


def event_registry(
    names: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> tuple[Registry, CompiledExpressionSidecar]:
    registry, sidecar = make_event_registry(Path("unused.duckdb"))
    monkeypatch.setenv("MARIVO_TEST_CLICKHOUSE_PASSWORD", password())
    entities = dict(registry.entities)
    for logical, table in names.items():
        entity = entities["sales." + logical]
        assert isinstance(entity.source, TableSourceIR)
        entities["sales." + logical] = replace(
            entity, source=replace(entity.source, table=table, database="qualification")
        )
    registry = replace(
        registry,
        entities=entities,
        datasources={
            name: replace(
                value,
                backend_type="clickhouse",
                fields={
                    "host": "127.0.0.1",
                    "port": 18123,
                    "database": "qualification",
                    "user": "analysis_reader",
                },
                env_refs={"password": "MARIVO_TEST_CLICKHOUSE_PASSWORD"},
            )
            for name, value in registry.datasources.items()
        },
    )
    registry.freeze()
    return registry, sidecar
