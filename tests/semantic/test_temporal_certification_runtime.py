"""Native temporal certification shares each source backend's DST interpretation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo._compat import UTC
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.catalog import SemanticCatalog, TemporalSet
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import Case, SourceData, source_case, source_params

pytestmark = pytest.mark.runtime

_CASES = [
    ("postgres", "table"),
    ("mysql", "innodb-table"),
    ("trino", "iceberg"),
    ("clickhouse", "mergetree"),
]
_ROWS = [
    {"id": 1, "starts": "2026-03-08 02:30:00", "ends": "2026-03-08 04:30:00"},
    {"id": 2, "starts": "2026-11-01 01:30:00", "ends": "2026-11-01 03:30:00"},
]
_DATA = SourceData(
    "id BIGINT, starts VARCHAR(32), ends VARCHAR(32)",
    "(1,'2026-03-08 02:30:00','2026-03-08 04:30:00'),"
    "(2,'2026-11-01 01:30:00','2026-11-01 03:30:00')",
    "id Int64, starts String, ends String",
    _ROWS,
)


def _catalog(
    case: Case,
    backend: str,
    factory: Callable[[dict[str, str]], SemanticProject],
    monkeypatch: pytest.MonkeyPatch,
) -> SemanticCatalog:
    assert isinstance(case.source, TableSourceIR)
    arguments = {
        **case.session.datasource.fields,
        **{key + "_env": value for key, value in case.session.datasource.env_refs.items()},
    }
    # The provider fixture's private transport setting is not a public Trino input.
    arguments.pop("timezone", None)
    if "user" in arguments:
        monkeypatch.setenv("MARIVO_CERTIFICATION_READER", str(arguments.pop("user")))
        arguments["user_env"] = "MARIVO_CERTIFICATION_READER"
    datasource = (
        "import marivo.datasource as md\n"
        + f"md.{backend}(name='warehouse',wall_clock_timezone='America/New_York',"
        + ",".join(f"{key}={value!r}" for key, value in arguments.items())
        + ")\n"
    )
    project = factory(
        {
            "datasources/warehouse.py": datasource,
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Data',default=True)\n",
            "sales/events.py": (
                "from datetime import date\nimport marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"events=ms.entity(name='events',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'])\n"
                + "identifier=ms.dimension_column(name='identifier',entity=events,column='id')\n"
                + "start=ms.time_dimension_column(name='start',entity=events,column='starts',parse=ms.strptime('%Y-%m-%d %H:%M:%S'))\n"
                + "end=ms.time_dimension_column(name='end',entity=events,column='ends',parse=ms.strptime('%Y-%m-%d %H:%M:%S'))\n"
                + "periods=ms.temporal_set(name='periods',occurrence_id=identifier,start=start,end=end,boundary_timezone='America/New_York',coverage=(date(2026,1,1),date(2027,1,1)))\n"
            ),
        }
    )
    return SemanticCatalog(project)


def _native_epoch_expression(backend: str, column: str) -> str:
    """Independent native SQL; no production parser/localizer builds this oracle."""
    if backend == "postgres":
        return f"CAST(EXTRACT(EPOCH FROM (CAST({column} AS timestamp) AT TIME ZONE 'America/New_York')) * 1000000 AS BIGINT)"
    if backend == "mysql":
        return f"TIMESTAMPDIFF(MICROSECOND,'1970-01-01 00:00:00',CONVERT_TZ(STR_TO_DATE({column},'%Y-%m-%d %H:%i:%s'),'America/New_York','+00:00'))"
    if backend == "trino":
        return f"CAST(to_unixtime(with_timezone(date_parse({column},'%Y-%m-%d %H:%i:%s'),'America/New_York')) * 1000000 AS BIGINT)"
    return f"toUnixTimestamp64Micro(toDateTime64({column},6,'America/New_York'))"


def _epoch_us(value: datetime) -> int:
    return (value - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)


@pytest.mark.parametrize("backend,profile", source_params(_CASES))
def test_certification_matches_native_gap_and_fold_in_one_read(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    with source_case(backend, profile, tmp_path, monkeypatch, _DATA) as case:
        assert isinstance(case.source, TableSourceIR)
        database = case.source.database
        namespace = database if isinstance(database, tuple) else ((database,) if database else ())
        table = ".".join((*namespace, case.source.table))
        # UUID-owned fixture identifiers and fixed namespaces need no user-input quoting.
        sql = (
            "SELECT id, "
            + _native_epoch_expression(backend, "starts")
            + " AS start_us, "
            + _native_epoch_expression(backend, "ends")
            + f" AS end_us FROM {table} ORDER BY id"
        )
        native = case.session._backend.raw_sql(sql)
        try:
            native_rows = native.result_rows if backend == "clickhouse" else native.fetchall()
            expected = {
                int(identity): (int(start_us), int(end_us))
                for identity, start_us, end_us in native_rows
            }
        finally:
            close = getattr(native, "close", None)
            if callable(close):
                close()
        assert set(expected) == {1, 2}
        catalog = _catalog(case, backend, semantic_project_factory, monkeypatch)
        entry = catalog.get("temporal_set:sales.periods")
        assert isinstance(entry, TemporalSet)
        reads: list[CompiledRead] = []
        execute = SourceSession.execute

        def captured(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
            reads.append(read)
            return execute(owner, read)

        monkeypatch.setattr(SourceSession, "execute", captured)
        catalog.preview(entry, scope=md.unpruned(max_rows=5, timeout_seconds=30))
        for identity, bounds in expected.items():
            occurrence = entry.occurrence(identity)
            assert (_epoch_us(occurrence.start), _epoch_us(occurrence.end)) == bounds
        assert [read.purpose for read in reads] == ["semantic.certified_preview"]
        assert entry.definition.snapshot_status == "current"
        declaration = tmp_path / "models/datasources/warehouse.py"
        declaration.write_text(declaration.read_text().replace("America/New_York", "UTC"))
        changed = ms.load(workspace_dir=tmp_path)
        assert changed.get("temporal_set:sales.periods").definition.snapshot_status == "stale"
        assert len(reads) == 1
