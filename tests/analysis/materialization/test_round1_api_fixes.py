"""Round-one public literal, canonical order and terminal calendar regressions."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.core.artifact_data import CalendarDataAxis
from marivo.analysis.core.frame_interpretation import FrameCalendarAxisSchema
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.pandas_export import export_frame
from marivo.analysis.materialization.terminal_protocol import decode, encode
from marivo.analysis.materialization.terminal_storage import read_output
from marivo.datasource.adapters import SourceSession
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.shared_fixtures import DslCaseFactory, catalog_get, catalog_value, run_ids
from tests.support.paths import PROJECT_ROOT


@pytest.mark.release
def test_calendar_products_keep_single_time_axis_descriptors_and_cold_metadata(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    region = catalog_value(session.catalog, "sales.customer.region", kind="field")
    regions = mv.coordinates(by=(region,), keys=(("A",), ("B",)))
    session = mv.session.get_or_create("paris-calendar", timezone="Europe/Paris")
    march_scope = session.time_scope(start="2026-03-28", end="2026-03-31")
    march = march_scope.by(mv.grain("day"))
    february = (
        mv.session.get_or_create("tokyo-calendar", timezone="Asia/Tokyo")
        .time_scope(start="2026-02-01", end="2026-02-03")
        .by(mv.grain("day"))
    )
    with pytest.raises(AnalysisError):
        mv.product(march, february)
    outputs = {
        "field_calendar": session.frame(mv.product(regions, march)).persist(),
        "tokyo_calendar": session.frame(
            mv.product(february, mv.coordinates(by=(), keys=((),)))
        ).persist(),
        "source_calendar": session.observe(
            {"value": catalog_get(session.catalog, "metric:sales.revenue", kind="metric")},
            by=(march_scope.by(mv.grain("day")),),
        ).persist(),
    }
    march_starts = [
        pd.Timestamp("2026-03-27 23:00Z"),
        pd.Timestamp("2026-03-28 23:00Z"),
        pd.Timestamp("2026-03-29 22:00Z"),
    ]
    february_starts = [pd.Timestamp("2026-01-31 15:00Z"), pd.Timestamp("2026-02-01 15:00Z")]
    frames = {name: value.to_pandas(time_details=True) for name, value in outputs.items()}
    field = frames["field_calendar"]
    assert field.iloc[:, 0].tolist() == ["A"] * 3 + ["B"] * 3
    assert field["calendar:day.label"].tolist() == ["2026-03-28", "2026-03-29", "2026-03-30"] * 2
    assert [axis.role for axis in outputs["field_calendar"].schema.axes] == ["field", "calendar"]
    tokyo = frames["tokyo_calendar"]
    assert tokyo["calendar:day.label"].tolist() == ["2026-02-01", "2026-02-02"]
    (tokyo_axis,) = outputs["tokyo_calendar"].schema.axes
    assert isinstance(tokyo_axis, FrameCalendarAxisSchema)
    assert tokyo_axis.timezone == "Asia/Tokyo"
    assert frames["source_calendar"]["calendar:day.label"].tolist() == [
        "2026-03-28",
        "2026-03-29",
        "2026-03-30",
    ]
    assert frames["source_calendar"]["value"].tolist() == [0, 0, 0]
    saved: dict[str, object] = {}
    for name, value in outputs.items():
        manifest = value._local._manifest(value._require_stored("artifact_id"))
        data = manifest.data
        calendars = tuple(axis for axis in data.schema.axes if isinstance(axis, CalendarDataAxis))
        assert all(axis.frozen is not None for axis in calendars)
        assert {part.name for part in data.attachments} == {
            axis.frozen.directory_name for axis in calendars if axis.frozen is not None
        }
        for axis in calendars:
            assert axis.frozen is not None
            part = next(
                part for part in data.attachments if part.name == axis.frozen.directory_name
            )
            assert part.schema.key_fields == tuple(column.slot_id for column in axis.columns)
            bounds = read_output(case.root, part.schema, part.rows)
            assert bounds[part.schema.key_fields[0]].tolist() == (
                march_starts if axis.timezone == "Europe/Paris" else february_starts
            )
            assert bounds.ordinal.tolist() == list(range(len(bounds)))
            assert tuple(bounds.columns[1:]) == (
                "nominal_label",
                "ordinal",
                "nominal_ordinal",
                "selection",
                "cutoff",
            )
            assert bounds.selection.tolist() == ["full"] * len(bounds)
            if axis.timezone == "Europe/Paris":
                positions = bounds.iloc[:, 0]
                assert positions.iloc[2] - positions.iloc[1] == timedelta(hours=23)
        assert decode(encode(manifest), type(manifest)) == manifest
        saved[name] = {
            "artifact": value.artifact_id,
            "snapshot": snapshot(value, frame=frames[name]),
        }
    state = case.root / "calendar-round1.json"
    state.write_text(json.dumps({"session": session.id, "outputs": saved}))
    case.database_path.unlink()
    shutil.rmtree(case.root / "models")
    script = """import json, os, sys
from pathlib import Path
import duckdb, ibis, pytest
import marivo.analysis as mv
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import snapshot
root = Path(sys.argv[1]); os.environ['MARIVO_PROJECT_ROOT'] = str(root)
state = json.loads((root/'calendar-round1.json').read_text())
def forbidden(*args, **kwargs):
    raise AssertionError('cold terminal calendar consulted source or semantics')
with pytest.MonkeyPatch.context() as patch:
    for owner, name in ((duckdb,'connect'), (ibis.duckdb,'connect'), (SourceSession,'__init__'), (SourceSession,'execute'), (SemanticProject,'load')):
        patch.setattr(owner,name,forbidden)
    patch.setattr(mv.Session,'catalog',property(forbidden))
    session=mv.session.resume(state['session'])
    for receipt in state['outputs'].values():
        artifact=session.persisted_frame(receipt['artifact']); frame=artifact.to_pandas(time_details=True)
        assert snapshot(artifact,frame=frame)==receipt['snapshot']
        artifact.show(n=2)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(case.root)],
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize(
    "aware",
    (
        datetime(2026, 1, 1, 6, 30, tzinfo=timezone(timedelta(hours=6, minutes=30))),
        datetime(2026, 1, 1, 5, 30, tzinfo=ZoneInfo("Asia/Kolkata")),
        datetime(2026, 1, 1, tzinfo=timezone.utc),
    ),
)
def test_select_literals_reject_naive_before_run_and_preserve_dates_and_instants(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    aware: datetime,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    query = session.observe(
        {"v": catalog_get(session.catalog, "metric:sales.revenue", kind="metric")}
    )
    before = run_ids(session)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("select literal admission opened a source")

    with monkeypatch.context() as patch:
        patch.setattr(SourceSession, "__enter__", forbidden)
        patch.setattr(SourceSession, "execute", forbidden)
        with pytest.raises(AnalysisError) as caught:
            query.select({"stamp": datetime(2026, 1, 1)})
    assert caught.value.location == "frame.select" and caught.value.expected
    assert caught.value.received and caught.value.repair
    frame = query.select({"day": date(2026, 1, 1), "stamp": aware}).persist().to_pandas()
    assert frame["day"].tolist() == [date(2026, 1, 1)]
    assert frame["stamp"].iloc[0] == pd.Timestamp("2026-01-01T00:00Z")
    literal = session.values(by=(), columns={"stamp": None}, rows=(((), {"stamp": aware}),))
    assert literal.persist().to_pandas()["stamp"].iloc[0] == frame["stamp"].iloc[0]
    compared = literal.select({"same": literal["stamp"] == aware}).persist().to_pandas()
    assert compared["same"].tolist() == [True]


def test_top_uses_null_first_complete_keys_but_keeps_score_na_last(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    region = catalog_value(session.catalog, "sales.customer.region", kind="field")
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    equal = session.values(
        by=(region, channel),
        columns={"v": None},
        rows=(
            (("A", "A"), {"v": 1}),
            ((None, "B"), {"v": 1}),
            (("<null>", "A"), {"v": 1}),
            ((None, None), {"v": 1}),
        ),
    )
    frame = equal.top(2, by=equal["v"]).persist().to_pandas()
    assert frame.iloc[:, 0].isna().all()
    assert pd.isna(frame.iloc[0, 1]) and frame.iloc[1, 1] == "B"
    different = session.values(
        by=(region,),
        columns={"v": None},
        rows=(
            ((None,), {"v": 0}),
            (("A",), {"v": 2}),
            (("<null>",), {"v": 1}),
        ),
    )
    scores = different.select({"score": different["v"] / different["v"] * different["v"]})
    saved = scores.top(3, by=scores["score"]).persist()
    exported = saved.to_pandas()
    assert exported.iloc[:2, 0].tolist() == ["A", "<null>"] and pd.isna(exported.iloc[2, 0])
    assert exported["score"].iloc[:2].tolist() == [2.0, 1.0]
    assert pd.isna(exported["score"].iloc[2])
    prefix = saved._local.preview(saved._backing, 2)
    preview = export_frame(prefix.schema, prefix.rows, prefix.attachments)
    assert preview.iloc[:, 0].tolist() == ["A", "<null>"]
    assert preview["score"].tolist() == [2.0, 1.0]
    assert scores.top(2, by=scores["score"]).persist().to_pandas().iloc[:, 0].tolist() == [
        "A",
        "<null>",
    ]
    ascending = scores.top(3, by=scores["score"], descending=False).persist().to_pandas()
    assert ascending.iloc[:2, 0].tolist() == ["<null>", "A"] and pd.isna(ascending.iloc[2, 0])
    scalar = session.values(by=(), columns={"v": None}, rows=(((), {"v": 1}),))
    assert scalar.top(1, by=scalar["v"]).persist().to_pandas()["v"].tolist() == [1]


def test_global_top_keeps_complete_key_ties_after_score_projection(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    region = catalog_value(session.catalog, "sales.customer.region", kind="field")
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    values = session.values(
        by=(region, channel),
        columns={"score": None},
        rows=(
            (("B", "C"), {"score": 30}),
            (("A", "B"), {"score": 10}),
            (("B", "A"), {"score": 20}),
            (("A", "C"), {"score": 20}),
            (("A", "A"), {"score": 10}),
        ),
    )
    ranked = values.top(2, by=values["score"], descending=True)
    artifact = ranked.select({"selected": 1}).persist()
    expected = [("B", "C"), ("A", "C")]
    for selected in (artifact, session.persisted_frame(artifact.artifact_id)):
        frame = selected.to_pandas()
        assert list(frame.iloc[:, :2].itertuples(index=False, name=None)) == expected
        assert frame["selected"].tolist() == [1] * 2


def test_literal_population_failures_use_structured_public_errors_without_reads(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    entity = catalog_get(session.catalog, "entity:sales.customer", kind="entity")
    region = catalog_value(session.catalog, "sales.customer.region", kind="field")
    before = run_ids(session)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("literal Population validation consulted a source")

    with monkeypatch.context() as patch:
        patch.setattr(SourceSession, "__enter__", forbidden)
        patch.setattr(SourceSession, "execute", forbidden)
        cases: tuple[tuple[Callable[[], mv.Population], str, str], ...] = (
            (
                lambda: mv.coordinates(by=(mv.identity(entity),), keys=(((None,),),)),
                "cannot be None",
                "coordinates",
            ),
            (
                lambda: mv.coordinates(by=(mv.identity(entity),), keys=((("A",),), (("B", "C"),))),
                "same number",
                "coordinates",
            ),
            (lambda: mv.coordinates(by=(region,), keys=(("A", "B"),)), "1 values", "coordinates"),
            (
                lambda: mv.coordinates(by=(region,), keys=((float("nan"),),)),
                "finite",
                "coordinates",
            ),
        )
        for build, reason, location in cases:
            with pytest.raises(AnalysisError) as caught:
                build()
            error = caught.value
            assert error.location == location and error.expected and error.received
            assert reason in error.received and error.repair and error.repair.action
    assert isinstance(mv.coordinates(by=(region,), keys=((None,), ("A",))), mv.Population)
