"""Small native temporal sources owned by each backend's reader fixture."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.datasource.ir import TableSourceIR
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import TimestampParse
from marivo.semantic.validator import Registry
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_scalar_source_fixtures import registry_for
from tests.lazy_scalar_type_fixtures import Engine, source_writer
from tests.lazy_temporal_fixtures import AXIS

ENGINES = ("duckdb", "sqlite", "postgres", "mysql", "clickhouse", "trino")


@contextmanager
def temporal_source(
    engine: Engine,
    path: Path,
    monkeypatch: pytest.MonkeyPatch,
    values: tuple[str | None, ...],
    *,
    zone: str | None = "UTC",
    aware: bool = False,
    precision: int = 6,
    physical_zone: str = "UTC",
) -> Iterator[tuple[Registry, CompiledExpressionSidecar]]:
    if (
        engine not in {"duckdb", "sqlite"}
        and os.environ.get(f"MARIVO_{engine.upper()}_ANALYSIS_TEST") != "1"
    ):
        pytest.skip(f"opt-in {engine} service")
    name = "c3a_" + uuid4().hex
    database = path / "source.db"
    if engine == "duckdb":
        registry, sidecar = make_execution_registry(database)
    elif engine == "postgres":
        from tests.lazy_postgres_fixtures import registry_for as pg_registry

        registry, sidecar = pg_registry(name, monkeypatch)
    else:
        registry, sidecar = registry_for(database, engine=engine, table=name)
        if engine in {"mysql", "clickhouse"}:
            from tests.multisource_environment.credentials import password

            monkeypatch.setenv(f"MARIVO_TEST_{engine.upper()}_PASSWORD", password())
    physical = {
        "duckdb": "TIMESTAMPTZ"
        if aware
        else {0: "TIMESTAMP_S", 3: "TIMESTAMP_MS", 6: "TIMESTAMP"}[precision],
        "postgres": f"TIMESTAMP({precision})" + (" WITH TIME ZONE" if aware else ""),
        "mysql": f"{'TIMESTAMP' if aware else 'DATETIME'}({precision})",
        "sqlite": "TIMESTAMP",
        "trino": f"TIMESTAMP({precision})",
        "clickhouse": f"DateTime64({precision}, '{physical_zone}')",
    }[engine]
    if engine == "clickhouse" and None in values:
        physical = f"Nullable({physical})"
    logical = f"timestamp('{physical_zone}', {precision})" if aware else f"timestamp({precision})"
    entity = registry.entities["sales.orders"]
    assert isinstance(entity.source, TableSourceIR)
    entity = replace(
        entity,
        source=replace(
            entity.source,
            table=name,
            columns=tuple(
                (
                    key,
                    replace(
                        binding,
                        data_type=logical
                        if key == "day"
                        else "int64"
                        if key == "amount"
                        else binding.data_type,
                    ),
                )
                for key, binding in entity.source.columns
            ),
        ),
    )
    axis = replace(
        registry.dimensions[AXIS],
        parse=None if aware or zone is None else TimestampParse(timezone=zone),
        granularity="second",
    )
    stamp = replace(
        registry.dimensions["sales.orders.channel"],
        semantic_id="sales.orders.stamp",
        name="stamp",
        source_column="day",
    )
    registry = replace(
        registry,
        entities={**registry.entities, entity.semantic_id: entity},
        dimensions={**registry.dimensions, AXIS: axis, "sales.orders.stamp": stamp},
    )
    registry.freeze()
    suffix = " ENGINE=MergeTree ORDER BY tuple()" if engine == "clickhouse" else ""
    try:
        with source_writer(engine, database) as execute:
            if engine == "mysql":
                execute("SET time_zone = '+00:00'")
            execute(f"CREATE TABLE {name} (id BIGINT, day {physical}, amount BIGINT){suffix}")
            rows = []
            for i, value in enumerate(values, 1):
                if value is None:
                    rows.append(f"({i},NULL,{i + 1})")
                    continue
                encoded = value + "+00:00" if aware and engine in {"duckdb", "postgres"} else value
                literal = f"'{encoded}'"
                if engine == "trino":
                    literal = "TIMESTAMP " + literal
                rows.append(f"({i},{literal},{i + 1})")
            execute(f"INSERT INTO {name} VALUES " + ",".join(rows))
        yield registry, sidecar
    finally:
        with source_writer(engine, database) as execute:
            execute(f"DROP TABLE IF EXISTS {name}")


def save_receipt(
    engine: Engine,
    case: str,
    runtime: DatasetRuntime,
    expected: dict[str, int],
    captured: list[dict[str, object]],
) -> None:
    """Save opt-in execution evidence without modifying persistent runtime authority."""
    import json
    from dataclasses import asdict

    directory = os.environ.get("MARIVO_C3A_RECEIPTS")
    if directory is None:
        return
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{engine}-{case}.json").write_text(
        json.dumps(
            {
                "engine": engine,
                "case": case,
                "expected": expected,
                "primary_queries": runtime.statistics.primary_queries,
                "validation_queries": runtime.statistics.validation_queries,
                "submissions": [asdict(item) for item in runtime.statistics.submissions],
                "adapter_cursor_sql": [item["sql"] for item in captured],
                "native_driver_capture": "not_captured",
                "capture_boundary": "adapter cursor execute; not a native-driver trace"
                if engine in {"sqlite", "mysql", "clickhouse", "trino"}
                else "adapter submission only; no second capture for this journey",
                "excluded": "driver-internal transaction, fetch, catalog initialization and transport protocol operations",
            },
            indent=2,
        )
        + "\n"
    )
