"""Captured consumed clocks survive fixed operations without retaining source authority."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
from marivo.analysis.core.frame_interpretation import (
    FrameCivilDateSourceTime,
    FrameInstantSourceTime,
    FrameWallClockSourceTime,
    merge_source_times,
)
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_get, catalog_value


@pytest.fixture
def temporal_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> mv.Session:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    database = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE events(id BIGINT, amount BIGINT, naive TIMESTAMP, aware TIMESTAMPTZ, day DATE, dt VARCHAR, hh BIGINT)"
        )
        connection.execute(
            "INSERT INTO events VALUES (1, 7, '2026-08-01 12:00:00', '2026-08-01 04:00:00+00', '2026-08-01', '20260801', 12)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            f"md.duckdb(name='warehouse', path={str(database)!r}, wall_clock_timezone='Asia/Shanghai')\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Test', default=True)\n",
            "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            "events=ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), source=md.table('events'), primary_key=['id'])\n"
            "profiles=ms.entity(name='profiles', datasource=ms.ref.datasource('warehouse'), source=md.table('events'), primary_key=['id'], versioning=ms.snapshot(partition_field=ms.ref.time_dimension('sales.profiles.partition'), timezone='UTC'))\n"
            "partition=ms.time_dimension_column(name='partition', entity=profiles, column='day', parse=ms.date(timezone='UTC'))\n"
            "amount=ms.measure_column(name='amount', entity=events, column='amount', additivity=ms.additive_all())\n"
            "naive=ms.time_dimension_column(name='naive', entity=events, column='naive', parse=None)\n"
            "aware=ms.time_dimension_column(name='aware', entity=events, column='aware', parse=None)\n"
            "day=ms.time_dimension_column(name='day', entity=events, column='day', parse=ms.date(timezone='UTC'))\n"
            "prefix=ms.time_dimension_column(name='prefix', entity=events, column='dt', parse=ms.strptime('%Y%m%d', timezone='UTC'))\n"
            "hour=ms.time_dimension_column(name='hour', entity=events, column='hh', parse=ms.hour_prefix(prefix))\n"
            "hour_value=hour\n"
            "@ms.metric()\ndef hourly_total():\n    return amount.sum(time=hour_value)\n"
            "amount_value=amount\nnaive_value=naive\naware_value=aware\nday_value=day\n"
            "@ms.metric()\ndef naive_total():\n    return amount_value.sum(time=naive_value)\n"
            "@ms.metric()\ndef aware_total():\n    return amount_value.sum(time=aware_value)\n"
            "@ms.metric()\ndef day_total():\n    return amount_value.sum(time=day_value)\n",
        }
    )
    return mv.session.get_or_create("captured-clocks", timezone="Asia/Tokyo")


def test_source_time_facts_are_resolved_only_on_compute_and_preserved_on_fixed_operations(
    temporal_session: mv.Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = temporal_session
    scope = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    pending = session.observe(
        {
            name: catalog_get(session.catalog, f"metric:sales.{name}_total", kind="metric")
            for name in ("naive", "aware", "day")
        },
        by=(scope,),
    )
    with monkeypatch.context() as no_source:

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("metadata display prepared a source")

        no_source.setattr(SourceSession, "__enter__", forbidden)
        assert pending.schema.source_times == ()
        pending.schema.show()
        pending.show(n=0)
    fixed = pending.compute()
    facts = fixed.schema.source_times
    assert tuple((fact.axis, fact.kind, fact.consumption_timezone) for fact in facts) == (
        ("sales.events.aware", "instant", "Asia/Tokyo"),
        ("sales.events.day", "civil_date", "Asia/Tokyo"),
        ("sales.events.naive", "wall_clock", "Asia/Tokyo"),
    )
    assert isinstance(facts[0], FrameInstantSourceTime)
    assert isinstance(facts[1], FrameCivilDateSourceTime)
    assert facts[1].timezone == "UTC" and facts[1].origin == "field"
    assert isinstance(facts[2], FrameWallClockSourceTime)
    assert facts[2].timezone == "Asia/Shanghai" and facts[2].origin == "datasource"
    assert facts[2].inherited_from is None
    assert all(fact.physical_type for fact in facts)
    with pytest.raises(FrozenInstanceError):
        facts[0].__setattr__("axis", "changed")
    renamed = fixed.select({"renamed": fixed["naive"] + 1})
    filtered = renamed.where(renamed["renamed"] > 0)
    reduced = filtered.reduce({"total": filtered["renamed"].sum()})
    assert (
        renamed.schema.source_times
        == filtered.schema.source_times
        == reduced.schema.source_times
        == facts
    )
    assert reduced.compute().schema.source_times == facts
    assert fixed.align(fixed).compute().schema.source_times == facts
    assert fixed.compare(fixed).compute().schema.source_times == facts
    fixed.show(n=0)
    output = capsys.readouterr().out
    assert "sales.events.day: civil_date UTC (field)" in output
    assert "assigned by business-day start" in output
    assert "cannot rebuild event-day totals" in output
    assert fixed.persist().schema.source_times == facts


def test_full_record_merge_preserves_changed_same_path_interpretations() -> None:
    original = FrameWallClockSourceTime("sales.clock", "timestamp(6)", "UTC", "Asia/Tokyo", "field")
    report_change = replace(original, consumption_timezone="Asia/Shanghai")
    declaration_change = replace(original, timezone="UTC", origin="datasource")
    merged = merge_source_times((original, report_change), (original, declaration_change))
    assert len(merged) == 3
    assert set(merged) == {original, report_change, declaration_change}
    assert merge_source_times(tuple(reversed(merged))) == merged


def test_hour_prefix_records_the_effective_declaration_origin(temporal_session: mv.Session) -> None:
    session = temporal_session
    result = session.observe(
        {"total": catalog_get(session.catalog, "metric:sales.hourly_total", kind="metric")},
        by=(session.time_scope(start="2026-08-01", end="2026-08-03"),),
    ).compute()
    assert tuple(fact.axis for fact in result.schema.source_times) == (
        "sales.events.hour",
        "sales.events.prefix",
    )
    fact = result.schema.source_times[0]
    assert isinstance(fact, FrameWallClockSourceTime)
    assert fact.timezone == "UTC" and fact.origin == "field"
    assert fact.inherited_from == "sales.events.prefix"
    assert fact.consumption_timezone == "Asia/Tokyo"


def test_source_projection_captures_session_consumer_timezone(temporal_session: mv.Session) -> None:
    session = temporal_session
    members = session.members(catalog_get(session.catalog, "entity:sales.events", kind="entity"))
    projected = session.read(
        members,
        {
            name: catalog_value(session.catalog, "sales.events." + name, kind="field")
            for name in ("naive", "day")
        },
    ).compute()
    facts = projected.schema.source_times
    assert tuple(fact.axis for fact in facts) == ("sales.events.day", "sales.events.naive")
    assert all(fact.consumption_timezone == "Asia/Tokyo" for fact in facts)
    assert isinstance(facts[0], FrameCivilDateSourceTime) and facts[0].timezone == "UTC"
    assert isinstance(facts[1], FrameWallClockSourceTime) and facts[1].timezone == "Asia/Shanghai"


def test_membership_version_clock_is_captured_without_direct_observation(
    temporal_session: mv.Session,
) -> None:
    session = temporal_session
    members = session.members(
        catalog_get(session.catalog, "entity:sales.profiles", kind="entity"),
        at=session.instant("2026-08-01T12:00:00+00:00"),
    )
    result = session.frame(members).compute()
    assert result.to_pandas().iloc[:, 0].tolist() == [1]
    assert result.schema.source_times == (
        FrameCivilDateSourceTime("sales.profiles.partition", "date", "UTC", "UTC", "field"),
    )
