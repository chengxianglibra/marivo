"""Round-one public literal, canonical order and terminal calendar regressions."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.core.artifact_data import CalendarDataAxis, TimestampDataType
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.contracts import canonical_json
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_reading import ExportRead, OutputRead, PreviewRead
from marivo.analysis.materialization.pandas_export import export_frame
from marivo.analysis.materialization.terminal_protocol import decode, encode
from marivo.analysis.materialization.terminal_storage import read_output
from marivo.datasource.adapters import SourceSession
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.shared_fixtures import DslCaseFactory, run_ids
from tests.support.paths import PROJECT_ROOT


def test_calendar_products_keep_each_axis_descriptor_receipt_and_cold_metadata(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    region = session.catalog.fields.get("sales.customer.region")
    regions = mv.coordinates(by=(region,), keys=(("A",), ("B",)))
    march = session.time_scope(start="2026-03-28", end="2026-03-31", timezone="Europe/Paris").by(
        mv.grain("day")
    )
    february = session.time_scope(start="2026-02-01", end="2026-02-03", timezone="Asia/Tokyo").by(
        mv.grain("day")
    )
    outputs = {
        "field_calendar": session.query(mv.product(regions, march)).execute(),
        "two_calendars": session.query(mv.product(march, february)).execute(),
        "source_calendar": session.observe(
            {"value": session.catalog.metrics.get("sales.revenue")}, during=march
        ).execute(),
    }
    march_starts = [
        pd.Timestamp("2026-03-27 23:00Z"),
        pd.Timestamp("2026-03-28 23:00Z"),
        pd.Timestamp("2026-03-29 22:00Z"),
    ]
    february_starts = [pd.Timestamp("2026-01-31 15:00Z"), pd.Timestamp("2026-02-01 15:00Z")]
    frames = {name: artifact.to_pandas() for name, artifact in outputs.items()}
    field = frames["field_calendar"]
    assert field.iloc[:, 0].tolist() == ["A"] * 3 + ["B"] * 3
    assert field.iloc[:, 1].tolist() == march_starts * 2
    assert field.attrs["marivo"]["axes"][0]["kind"] == "field"
    assert field.attrs["marivo"]["axes"][1]["kind"] == "calendar"
    paired = frames["two_calendars"]
    assert paired.iloc[:, 0].tolist() == [point for point in march_starts for _ in range(2)]
    assert paired.iloc[:, 1].tolist() == february_starts * 3
    assert [axis["timezone"] for axis in paired.attrs["marivo"]["axes"]] == [
        "Europe/Paris",
        "Asia/Tokyo",
    ]
    assert [len(axis["bounds"]) for axis in paired.attrs["marivo"]["axes"]] == [3, 2]
    march_bounds = paired.attrs["marivo"]["axes"][0]["bounds"]
    assert march_bounds[1]["actual_end"] - march_bounds[1]["actual_start"] == timedelta(hours=23)
    assert frames["source_calendar"].iloc[:, 0].tolist() == march_starts
    assert frames["source_calendar"][("value", "value")].tolist() == [0, 0, 0]

    saved: dict[str, object] = {}
    for name, artifact in outputs.items():
        manifest = artifact._manifest
        data = manifest.data
        calendars = tuple(axis for axis in data.schema.axes if isinstance(axis, CalendarDataAxis))
        assert {part.name for part in data.attachments} == {
            f"calendar_bounds.{axis.token}" for axis in calendars
        }
        for axis in calendars:
            part = next(
                part for part in data.attachments if part.name == f"calendar_bounds.{axis.token}"
            )
            assert part.schema.axes == (axis,)
            assert part.schema.key_fields == tuple(column.slot_id for column in axis.columns)
            bounds = read_output(case.root, part.schema, part.rows)
            assert bounds[part.schema.key_fields[0]].tolist() == list(range(len(bounds)))
            assert tuple(bounds.columns[1:]) == (
                "nominal_start",
                "nominal_end",
                "actual_start",
                "actual_end",
            )
        assert decode(encode(manifest), type(manifest)) == manifest
        saved[name] = {
            "artifact": artifact.id,
            "snapshot": snapshot(artifact, frame=frames[name]),
            "metadata": json.dumps(frames[name].attrs["marivo"], sort_keys=True, default=str),
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
from tests.shared_fixtures import run_ids
root = Path(sys.argv[1]); os.environ['MARIVO_PROJECT_ROOT'] = str(root)
assert not list(root.glob('*.duckdb')) and not (root/'models').exists()
state = json.loads((root/'calendar-round1.json').read_text())
def forbidden(*args, **kwargs):
    raise AssertionError('cold terminal calendar consulted source or semantics')
with pytest.MonkeyPatch.context() as patch:
    for owner, name in ((duckdb,'connect'), (ibis.duckdb,'connect'), (SourceSession,'__init__'), (SourceSession,'execute'), (SemanticProject,'load')):
        patch.setattr(owner,name,forbidden)
    patch.setattr(mv.Session,'catalog',property(forbidden))
    session=mv.session.resume(state['session'],by='id'); before=run_ids(session)
    for receipt in state['outputs'].values():
        artifact=session.artifact(receipt['artifact']); frame=artifact.to_pandas()
        assert snapshot(artifact,frame=frame)==receipt['snapshot']
        assert json.dumps(frame.attrs['marivo'],sort_keys=True,default=str)==receipt['metadata']
        artifact.show(n=2)
    assert run_ids(session)==before and not session._runtime.store.resources(session.id)
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


def test_calendar_descriptor_and_boundary_receipt_are_validated(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    first = session.time_scope(start="2026-01-01", end="2026-01-03").by(mv.grain("day"))
    second = session.time_scope(start="2026-02-01", end="2026-02-03").by(mv.grain("day"))
    artifact = session.query(mv.product(first, second)).execute()
    manifest = artifact._manifest
    part0, part1 = manifest.data.attachments
    for broken in (
        replace(part0, name="calendar_bounds"),
        replace(part0, schema=part1.schema),
        replace(
            part0,
            schema=replace(
                part0.schema,
                values=(
                    replace(part0.schema.values[0], dtype=TimestampDataType("s", "UTC")),
                    *part0.schema.values[1:],
                ),
            ),
        ),
    ):
        changed = replace(manifest, data=replace(manifest.data, attachments=(broken, part1)))
        with pytest.raises(MaterializationError, match="boundary attachment"):
            export_frame(changed, OutputRead(case.root), ExportRead())
    envelope = json.loads(encode(manifest))
    envelope["format"] = "marivo.terminal_output/v0"
    with pytest.raises(MaterializationError, match="obsolete"):
        decode(canonical_json(envelope), type(manifest))
    boundary_file = case.root / part1.rows.receipt.project_relative_path / "data.parquet"
    boundary_file.write_bytes(boundary_file.read_bytes() + b"corrupt")
    with pytest.raises(MaterializationError, match="digest"):
        artifact.to_pandas()


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
    query = session.observe({"v": session.catalog.metrics.get("sales.revenue")})
    before = run_ids(session)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("select literal admission opened a source")

    with monkeypatch.context() as patch:
        patch.setattr(SourceSession, "__enter__", forbidden)
        patch.setattr(SourceSession, "execute", forbidden)
        with pytest.raises(AnalysisError) as caught:
            query.select({"stamp": datetime(2026, 1, 1)})
    assert caught.value.location == "Query.select" and caught.value.expected
    assert caught.value.received and caught.value.repair
    assert run_ids(session) == before
    frame = query.select({"day": date(2026, 1, 1), "stamp": aware}).execute().to_pandas()
    assert frame[("value", "day")].tolist() == [date(2026, 1, 1)]
    assert frame[("value", "stamp")].iloc[0] == pd.Timestamp("2026-01-01T00:00Z")
    literal = session.values(by=(), columns={"stamp": None}, rows=(((), {"stamp": aware}),))
    assert (
        literal.execute().to_pandas()[("value", "stamp")].iloc[0]
        == frame[("value", "stamp")].iloc[0]
    )
    compared = literal.select({"same": literal["stamp"] == aware}).execute().to_pandas()
    assert compared[("value", "same")].tolist() == [True]


def test_top_uses_null_first_complete_keys_but_keeps_score_na_last(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    region = session.catalog.fields.get("sales.customer.region")
    channel = session.catalog.fields.get("sales.order.channel")
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
    frame = equal.top(2, by=equal["v"]).execute().to_pandas()
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
    scores = different.select({"score": different["v"].divide(different["v"]) * different["v"]})
    saved = scores.top(3, by=scores["score"]).execute()
    exported = saved.to_pandas()
    assert pd.isna(exported.iloc[0, 0]) and exported.iloc[1:, 0].tolist() == ["<null>", "A"]
    assert pd.isna(exported[("value", "score")].iloc[0])
    preview = export_frame(saved._manifest, OutputRead(case.root), PreviewRead(2))
    assert preview.iloc[:, 0].tolist() == ["A", "<null>"]
    assert preview[("value", "score")].tolist() == [2.0, 1.0]
    assert scores.top(2, by=scores["score"]).execute().to_pandas().iloc[:, 0].tolist() == [
        "<null>",
        "A",
    ]
    scalar = session.values(by=(), columns={"v": None}, rows=(((), {"v": 1}),))
    assert scalar.top(1, by=scalar["v"]).execute().to_pandas()[("value", "v")].tolist() == [1]


def test_literal_population_failures_use_structured_public_errors_without_reads(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = analysis_dsl_case_factory("j1").session
    entity = session.catalog.entities.get("sales.customer")
    region = session.catalog.fields.get("sales.customer.region")
    before = run_ids(session)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("literal Population validation consulted a source")

    with monkeypatch.context() as patch:
        patch.setattr(SourceSession, "__enter__", forbidden)
        patch.setattr(SourceSession, "execute", forbidden)
        cases: tuple[tuple[Callable[[], mv.Population], str, str], ...] = (
            (lambda: mv.identities(entity, keys=((None,),)), "cannot be None", "identities"),
            (lambda: mv.identities(entity, keys=(("A",), ("B", "C"))), "same number", "identities"),
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
    assert run_ids(session) == before
    assert isinstance(mv.coordinates(by=(region,), keys=((None,), ("A",))), mv.Population)
