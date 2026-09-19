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
from marivo.semantic.ir import DateParse, HourPrefixParse, StrptimeParse, TimestampParse
from marivo.semantic.validator import Registry
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_scalar_source_fixtures import registry_for
from tests.lazy_scalar_type_fixtures import Engine, source_writer
from tests.lazy_temporal_fixtures import AXIS

ENGINES = ("duckdb", "sqlite", "postgres", "mysql", "clickhouse", "trino")

# Physical text types. MySQL's adapter admits only the binary comparison
# collation, so a text source must declare it explicitly.
TEXT_PHYSICAL = {
    "duckdb": "VARCHAR",
    "postgres": "VARCHAR(40)",
    "mysql": "VARCHAR(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin",
    "sqlite": "VARCHAR(40)",
    "trino": "VARCHAR(40)",
    "clickhouse": "String",
}

DATE_PHYSICAL = {
    "duckdb": "DATE",
    "postgres": "DATE",
    "mysql": "DATE",
    "sqlite": "DATE",
    "trino": "DATE",
    "clickhouse": "Date",
}


def _opt_in(engine: Engine) -> None:
    if (
        engine not in {"duckdb", "sqlite"}
        and os.environ.get(f"MARIVO_{engine.upper()}_ANALYSIS_TEST") != "1"
    ):
        pytest.skip(f"opt-in {engine} service")


def _declared_source(
    engine: Engine, path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> tuple[Registry, CompiledExpressionSidecar]:
    """Declare one UUID-suffixed table through the engine's own reader fixture."""
    database = path / "source.db"
    if engine == "duckdb":
        return make_execution_registry(database)
    if engine == "postgres":
        from tests.lazy_postgres_fixtures import registry_for as pg_registry

        return pg_registry(name, monkeypatch)
    registry, sidecar = registry_for(database, engine=engine, table=name)
    if engine in {"mysql", "clickhouse"}:
        from tests.multisource_environment.credentials import password

        monkeypatch.setenv(f"MARIVO_TEST_{engine.upper()}_PASSWORD", password())
    return registry, sidecar


@contextmanager
def _written_source(
    engine: Engine,
    path: Path,
    name: str,
    columns: tuple[tuple[str, str], ...],
    rows: tuple[str, ...],
) -> Iterator[None]:
    """Create one UUID table of raw driver literals and always drop it again."""
    database = path / "source.db"
    suffix = " ENGINE=MergeTree ORDER BY tuple()" if engine == "clickhouse" else ""
    declarations = ",".join(f"{column} {physical}" for column, physical in columns)
    try:
        with source_writer(engine, database) as execute:
            if engine == "mysql":
                execute("SET time_zone = '+00:00'")
            execute(f"CREATE TABLE {name} ({declarations}){suffix}")
            execute(f"INSERT INTO {name} VALUES " + ",".join(rows))
        yield
    finally:
        with source_writer(engine, database) as execute:
            execute(f"DROP TABLE IF EXISTS {name}")


def _row_text(value: str | int | None, engine: Engine) -> str:
    """Render one text, integer or date cell as a driver literal for *engine*."""
    if value is None:
        return "NULL"
    if not isinstance(value, str):
        return str(value)
    literal = repr(value)
    return "DATE " + literal if engine == "trino" else literal


@contextmanager
def strptime_source(
    engine: Engine,
    path: Path,
    monkeypatch: pytest.MonkeyPatch,
    values: tuple[str | None, ...],
    *,
    fmt: str = "%Y-%m-%d",
    timezone: str | None = None,
) -> Iterator[tuple[Registry, CompiledExpressionSidecar]]:
    """Declare ``order_time`` as authored text read through *engine*'s parser."""
    _opt_in(engine)
    # A parse without a declared zone falls back to the reader timezone, so pin
    # the process zone rather than let the machine's zone decide.
    monkeypatch.setenv("TZ", "UTC")
    name = "c3b_" + uuid4().hex
    registry, sidecar = _declared_source(engine, path, monkeypatch, name)
    physical = TEXT_PHYSICAL[engine]
    if engine == "clickhouse" and None in values:
        physical = f"Nullable({physical})"
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
                    replace(binding, data_type="string")
                    if key == "day"
                    else replace(binding, data_type="int64")
                    if key == "amount"
                    else binding,
                )
                for key, binding in entity.source.columns
            ),
        ),
    )
    axis = replace(
        registry.dimensions[AXIS],
        parse=StrptimeParse(fmt, timezone=timezone),
        granularity="second" if "%H" in fmt else "day",
    )
    registry = replace(
        registry,
        entities={**registry.entities, entity.semantic_id: entity},
        dimensions={**registry.dimensions, AXIS: axis},
    )
    registry.freeze()
    rows = tuple(
        f"({index},{_row_text(value, engine)},{index + 1})" for index, value in enumerate(values, 1)
    )
    with _written_source(
        engine, path, name, (("id", "BIGINT"), ("day", physical), ("amount", "BIGINT")), rows
    ):
        yield registry, sidecar


@contextmanager
def hour_prefix_source(
    engine: Engine,
    path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prefix_values: tuple[str | None, ...],
    hour_values: tuple[str | int | None, ...],
    *,
    hour_physical: str | None = None,
) -> Iterator[tuple[Registry, CompiledExpressionSidecar]]:
    """Declare ``sales.orders.hour`` as a composite date-prefix + hour axis.

    The prefix stays a native civil date column and the hour column holds the
    raw driver literal, so the 0-23 value-domain contract is judged on exactly
    the bytes the server stores.
    """
    _opt_in(engine)
    # A composite axis is a naive wall clock until a reader timezone is known,
    # so pin the process zone rather than let the machine's zone decide.
    monkeypatch.setenv("TZ", "UTC")
    name = "c3b_" + uuid4().hex
    registry, sidecar = _declared_source(engine, path, monkeypatch, name)
    hour = "sales.orders.hour"
    integer_hours = hour_physical is not None
    physical = hour_physical or TEXT_PHYSICAL[engine]
    if engine == "clickhouse" and None in hour_values:
        physical = f"Nullable({physical})"
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
                    replace(binding, data_type="date")
                    if key == "day"
                    else replace(binding, data_type="int64")
                    if key == "channel" and integer_hours
                    else replace(binding, data_type="string")
                    if key == "channel"
                    else replace(binding, data_type="int64")
                    if key == "amount"
                    else binding,
                )
                for key, binding in entity.source.columns
            ),
        ),
    )
    registry = replace(
        registry,
        entities={**registry.entities, entity.semantic_id: entity},
        dimensions={
            **registry.dimensions,
            AXIS: replace(
                registry.dimensions[AXIS], parse=DateParse(), granularity="day", is_default=False
            ),
            hour: replace(
                registry.dimensions[AXIS],
                semantic_id=hour,
                name="hour",
                is_default=True,
                granularity="hour",
                parse=HourPrefixParse(AXIS),
                source_column="channel",
            ),
        },
    )
    registry.freeze()
    rows = tuple(
        f"({index},{_row_text(prefix, engine)},{_row_text(value, engine)},{index + 1})"
        for index, (prefix, value) in enumerate(
            zip(prefix_values, hour_values, strict=True), start=1
        )
    )
    with _written_source(
        engine,
        path,
        name,
        (
            ("id", "BIGINT"),
            ("day", DATE_PHYSICAL[engine]),
            ("channel", physical),
            ("amount", "BIGINT"),
        ),
        rows,
    ):
        yield registry, sidecar


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
    _opt_in(engine)
    name = "c3a_" + uuid4().hex
    database = path / "source.db"
    registry, sidecar = _declared_source(engine, path, monkeypatch, name)
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
