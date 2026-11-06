"""Declared source-time authority, schema admission and native certification."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import ibis
import ibis.expr.types as ibis_ir
import pandas as pd
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.semantic._source_timezone import effective_source_timezone
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticLoadFailed, SourceTimezoneMissing
from marivo.semantic.validator import normalize_target_dimension, normalize_target_entity
from tests.shared_fixtures import catalog_entry

if TYPE_CHECKING:
    from tests.conftest import _SemanticProjectFactory


def _project(
    tmp_path: Path,
    semantic_project_factory: _SemanticProjectFactory,
    *,
    default: str | None = None,
    fields: str = "",
    version: str = "",
) -> tuple[SemanticCatalog, Path]:
    database = tmp_path / "source-time.duckdb"
    backend = ibis.duckdb.connect(str(database))
    backend.raw_sql(
        "CREATE TABLE events(id INTEGER, day DATE, day_end DATE, clock TIMESTAMP, instant TIMESTAMPTZ, encoded VARCHAR, hh VARCHAR)"
    )
    backend.raw_sql(
        "INSERT INTO events VALUES (1, DATE '2026-03-08', DATE '2026-03-09', TIMESTAMP '2026-03-08 02:30:00', TIMESTAMPTZ '2026-03-08 07:30:00+00', '20260308023000', '02')"
    )
    backend.disconnect()
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r}, wall_clock_timezone={default!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Data',default=True)\n",
            "sales/events.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nfrom datetime import date\n"
            + f"events=ms.entity(name='events',datasource=ms.ref.datasource('warehouse'),source=md.table('events'),primary_key=['id']{version})\n"
            + fields,
        }
    )
    return SemanticCatalog(project), database


def test_date_and_date_only_parse_normalize_source_timezone() -> None:
    assert ms.date(timezone="Etc/UTC").timezone == "UTC"
    assert ms.strptime("%Y%m%d", timezone="+08:00").timezone == "UTC+08:00"
    with pytest.raises(TypeError):
        ms.validity(
            valid_from=ms.ref.time_dimension("sales.events.day"),
            valid_to=ms.ref.time_dimension("sales.events.day_end"),
            interval="closed_open",
            open_end=(None,),
            timezone="UTC",
        )  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        ms.period_calendar(
            name="calendar",
            date=ms.ref.time_dimension("sales.events.day"),
            boundary_timezone="UTC",
            coverage=(
                __import__("datetime").date(2026, 1, 1),
                __import__("datetime").date(2027, 1, 1),
            ),
            levels={},
        )  # type: ignore[call-arg]


def test_hour_prefix_recursively_inherits_declared_origin(
    tmp_path: Path, semantic_project_factory: _SemanticProjectFactory
) -> None:
    catalog, _ = _project(
        tmp_path,
        semantic_project_factory,
        default="Asia/Tokyo",
        fields=(
            "day=ms.time_dimension_column(name='day',entity=events,column='day',parse=ms.date(timezone='Etc/UTC'))\n"
            "hour=ms.time_dimension_column(name='hour',entity=events,column='hh',parse=ms.hour_prefix(day))\n"
            "clock=ms.time_dimension_column(name='clock',entity=events,column='clock')\n"
        ),
    )
    registry = catalog._reg
    hour = effective_source_timezone(
        registry, normalize_target_dimension(registry, "sales.events.hour")
    )
    assert hour is not None
    assert (hour.timezone, hour.origin, hour.inherited_from) == ("UTC", "field", "sales.events.day")
    clock = effective_source_timezone(
        registry, normalize_target_dimension(registry, "sales.events.clock")
    )
    assert clock is not None and (clock.timezone, clock.origin) == ("Asia/Tokyo", "datasource")
    preview = catalog.preview(
        catalog.get("time_dimension:sales.events.hour"),
        scope=md.unpruned(max_rows=5, timeout_seconds=10),
    )
    assert preview.timezones["hour"]["inherited_from"] == "sales.events.day"
    assert "inherited from sales.events.day" in preview.render()
    assert "UTC (field)" in catalog.get("time_dimension:sales.events.hour").render()


def test_encoded_missing_timezone_is_readiness_blocker(
    tmp_path: Path, semantic_project_factory: _SemanticProjectFactory
) -> None:
    catalog, _ = _project(
        tmp_path,
        semantic_project_factory,
        fields="clock=ms.time_dimension_column(name='clock',entity=events,column='encoded',parse=ms.strptime('%Y%m%d%H%M%S'))\n",
    )
    report = catalog.readiness([catalog.get("time_dimension:sales.events.clock")])
    assert report.status == "blocked"
    assert any(issue.kind == "source_timezone_missing" for issue in report.blockers)


@pytest.mark.parametrize("column", ["clock", "day"])
def test_native_missing_timezone_rejects_before_business_read(
    tmp_path: Path,
    semantic_project_factory: _SemanticProjectFactory,
    monkeypatch: pytest.MonkeyPatch,
    column: str,
) -> None:
    catalog, _ = _project(
        tmp_path,
        semantic_project_factory,
        fields=f"axis=ms.time_dimension_column(name='axis',entity=events,column={column!r})\n",
    )
    from marivo.datasource.runtime import DatasourceConnectionService

    monkeypatch.setattr(
        DatasourceConnectionService,
        "collect_source",
        lambda *args, **kwargs: pytest.fail("business read before timezone validation"),
    )
    with pytest.raises(SourceTimezoneMissing) as captured:
        catalog.preview(
            catalog.get("time_dimension:sales.events.axis"),
            scope=md.unpruned(max_rows=5, timeout_seconds=10),
        )
    assert captured.value.details["query_executed"] is False
    assert "md.duckdb" in str(captured.value)
    assert (
        "ms.date" in str(captured.value)
        if column == "day"
        else "ms.timestamp" in str(captured.value)
    )


@pytest.mark.parametrize("parser", ["None", "ms.timestamp()"])
def test_aware_preview_uses_physical_authority_and_ignores_host_tz(
    tmp_path: Path,
    semantic_project_factory: _SemanticProjectFactory,
    monkeypatch: pytest.MonkeyPatch,
    parser: str,
) -> None:
    catalog, _ = _project(
        tmp_path,
        semantic_project_factory,
        default="Asia/Tokyo",
        fields=f"axis=ms.time_dimension_column(name='axis',entity=events,column='instant',parse={parser})\n",
    )
    field = catalog.get("time_dimension:sales.events.axis")
    scope = md.unpruned(max_rows=5, timeout_seconds=10)
    monkeypatch.setenv("TZ", "America/New_York")
    first = catalog.preview(field, scope=scope)
    monkeypatch.setenv("TZ", "UTC+08:00")
    second = catalog.preview(field, scope=scope)
    assert first.rows == second.rows
    assert first.timezones == {"axis": {"kind": "instant"}}


def test_date_validity_derives_common_normalized_timezone(
    tmp_path: Path, semantic_project_factory: _SemanticProjectFactory
) -> None:
    catalog, _ = _project(
        tmp_path,
        semantic_project_factory,
        default="UTC",
        version=",versioning=ms.validity(valid_from=ms.ref.time_dimension('sales.events.day'),valid_to=ms.ref.time_dimension('sales.events.day_end'),interval='closed_open',open_end=(None,))",
        fields=(
            "day=ms.time_dimension_column(name='day',entity=events,column='day',parse=ms.date(timezone='Etc/UTC'))\n"
            "day_end=ms.time_dimension_column(name='day_end',entity=events,column='day_end',parse=ms.date())\n"
        ),
    )
    version = normalize_target_entity(catalog._reg, "sales.events").version
    assert version is not None and version.timezone == "UTC"


def test_date_snapshot_contradiction_is_structured(
    tmp_path: Path, semantic_project_factory: _SemanticProjectFactory
) -> None:
    with pytest.raises(SemanticLoadFailed) as captured:
        catalog, _ = _project(
            tmp_path,
            semantic_project_factory,
            default="UTC",
            version=",versioning=ms.snapshot(partition_field=ms.ref.time_dimension('sales.events.day'),timezone='Asia/Tokyo')",
            fields="day=ms.time_dimension_column(name='day',entity=events,column='day',parse=ms.date())\n",
        )
        catalog.get("entity:sales.events")
    assert any(
        error.kind == "declared_timezone_contradiction" and error.details["query_executed"] is False
        for error in captured.value.errors
    )


@pytest.mark.parametrize(
    "clock,encoded,end",
    [
        ("2026-03-08 02:30:00", "20260308023000", "2026-03-08 08:30:00+00"),
        ("2026-11-01 01:30:00", "20261101013000", "2026-11-01 08:30:00+00"),
    ],
)
def test_native_certification_resolves_dst_in_one_source_read(
    tmp_path: Path,
    semantic_project_factory: _SemanticProjectFactory,
    monkeypatch: pytest.MonkeyPatch,
    clock: str,
    encoded: str,
    end: str,
) -> None:
    catalog, _ = _project(
        tmp_path,
        semantic_project_factory,
        default="America/New_York",
        fields=(
            "identifier=ms.dimension_column(name='id',entity=events,column='id')\n"
            "start=ms.time_dimension_column(name='start',entity=events,column='encoded',parse=ms.strptime('%Y%m%d%H%M%S'))\n"
            "end=ms.time_dimension_column(name='end',entity=events,column='instant')\n"
            "periods=ms.temporal_set(name='periods',occurrence_id=identifier,start=start,end=end,boundary_timezone='America/New_York',coverage=(date(2026,1,1),date(2027,1,1)))\n"
        ),
    )
    # Native DuckDB maps the gap to 07:30Z; make the existing aware endpoint later.
    backend = ibis.duckdb.connect(str(tmp_path / "source-time.duckdb"))
    backend.raw_sql(f"UPDATE events SET encoded='{encoded}',instant=TIMESTAMPTZ '{end}'")
    expected = (
        backend.sql(f"SELECT timezone('America/New_York', TIMESTAMP '{clock}') AS start")
        .execute()
        .iloc[0, 0]
    )
    backend.disconnect()
    from marivo.datasource.runtime import DatasourceConnectionService

    collect = DatasourceConnectionService.collect_source
    reads: list[str] = []

    def capture(
        self: DatasourceConnectionService,
        name: str,
        expression: ibis_ir.Table,
        *,
        purpose: str,
        max_rows: int,
    ) -> pd.DataFrame:
        reads.append(purpose)
        return collect(self, name, expression, purpose=purpose, max_rows=max_rows)

    monkeypatch.setattr(DatasourceConnectionService, "collect_source", capture)
    entry = catalog_entry(catalog, "sales.periods", kind="temporal_set")
    catalog.preview(entry, scope=md.unpruned(max_rows=5, timeout_seconds=10))
    occurrence = entry.occurrence(1)
    assert occurrence.start == expected
    assert reads == ["semantic.certified_preview"]
    declaration = tmp_path / "models/datasources/warehouse.py"
    declaration.write_text(declaration.read_text().replace("America/New_York", "UTC"))
    changed = ms.load(workspace_dir=tmp_path)
    assert (
        catalog_entry(changed, "sales.periods", kind="temporal_set").definition.snapshot_status
        == "stale"
    )
    assert reads == ["semantic.certified_preview"]


def test_source_declaration_fingerprints_use_normalized_timezone(
    tmp_path: Path, semantic_project_factory: _SemanticProjectFactory
) -> None:
    from marivo.semantic._physical_identity import entity_dependency_fingerprint

    catalog, _ = _project(
        tmp_path,
        semantic_project_factory,
        default="UTC",
        fields="day=ms.time_dimension_column(name='day',entity=events,column='day')\n",
    )
    first_definition = catalog.definition_fingerprint
    first_physical = entity_dependency_fingerprint(catalog._reg, "sales.events")
    declaration = tmp_path / "models/datasources/warehouse.py"
    declaration.write_text(declaration.read_text().replace("'UTC'", "'Etc/UTC'"))
    alias = ms.load(workspace_dir=tmp_path)
    assert alias.definition_fingerprint == first_definition
    assert entity_dependency_fingerprint(alias._reg, "sales.events") == first_physical
    declaration.write_text(declaration.read_text().replace("'Etc/UTC'", "'UTC+01:00'"))
    changed = ms.load(workspace_dir=tmp_path)
    assert changed.definition_fingerprint != first_definition
    assert entity_dependency_fingerprint(changed._reg, "sales.events") != first_physical


def test_certification_preserves_distinct_declarations_on_one_source_column(
    tmp_path: Path, semantic_project_factory: _SemanticProjectFactory
) -> None:
    catalog, _ = _project(
        tmp_path,
        semantic_project_factory,
        fields=(
            "identifier=ms.dimension_column(name='id',entity=events,column='id')\n"
            "start=ms.time_dimension_column(name='start',entity=events,column='clock',parse=ms.timestamp(timezone='UTC'))\n"
            "end=ms.time_dimension_column(name='end',entity=events,column='clock',parse=ms.timestamp(timezone='America/New_York'))\n"
            "periods=ms.temporal_set(name='periods',occurrence_id=identifier,start=start,end=end,boundary_timezone='UTC',coverage=(date(2026,1,1),date(2027,1,1)))\n"
        ),
    )
    entry = catalog_entry(catalog, "sales.periods", kind="temporal_set")
    catalog.preview(entry, scope=md.unpruned(max_rows=5, timeout_seconds=10))
    occurrence = entry.occurrence(1)
    assert occurrence.start.isoformat() == "2026-03-08T02:30:00+00:00"
    assert occurrence.end.isoformat() == "2026-03-08T07:30:00+00:00"


def test_hour_prefix_inherits_datasource_from_derived_date_field(
    tmp_path: Path, semantic_project_factory: _SemanticProjectFactory
) -> None:
    from marivo.semantic._source_timezone import source_timezone_axis

    catalog, _ = _project(
        tmp_path,
        semantic_project_factory,
        default="Asia/Tokyo",
        fields=(
            "@ms.time_dimension(entity=events,parse=ms.date())\n"
            "def day(rows):\n    return rows.day\n"
            "hour=ms.time_dimension_column(name='hour',entity=events,column='hh',parse=ms.hour_prefix(day))\n"
        ),
    )
    declaration = effective_source_timezone(
        catalog._reg, source_timezone_axis(catalog._reg, "sales.events.hour")
    )
    assert declaration is not None
    assert (declaration.timezone, declaration.origin, declaration.inherited_from) == (
        "Asia/Tokyo",
        "datasource",
        "sales.events.day",
    )
    assert catalog.readiness([catalog.get("time_dimension:sales.events.hour")]).status == "ready"
