"""Native SQLite C18 opportunity truth and elapsed/calendar deadline probes."""

import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from tests.analysis.journey.retention_fixtures import build_retention
from tests.analysis.lifecycle.lifecycle_fixtures import START, build_lifecycle_public
from tests.support.paths import PROJECT_ROOT


@pytest.mark.runtime
def test_sqlite_anchor_omega(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    session, anchors, returning, claims = build_retention(tmp_path, backend_name="sqlite")
    retained = anchors.retention(
        returning, within=mv.elapsed(mv.duration(seconds=10)), completeness=claims
    ).execute()
    rows = retained.to_pandas()
    assert rows.cell_tag.tolist() == ["defined", "unknown", "defined", "unknown"]
    assert rows.value.tolist() == [True, None, False, None]
    assert dict(retained.contract()._facts)["omega_count"] == "4"
    assert dict(retained.contract()._facts)["deterministic_bounds"] == "[0.25,0.75]"
    for rule, expected in ((mv.any_anchor(), [True, None]), (mv.every_anchor(), [None, False])):
        assert retained.by_subject(rule=rule).execute().to_pandas().value.tolist() == expected
    with pytest.raises(AnalysisError):
        anchors.execute().retention(returning, within=mv.elapsed(mv.duration(seconds=10)))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Fixed retention read a source")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    selected = retained.known_true().execute()
    assert len(selected.to_pandas()) == 1
    assert dict(selected.contract()._facts)["omega_count"] == "4"
    assert selected.members(through=selected.subject_binding).execute().to_pandas()[
        "member"
    ].tolist() == [9007199254740993]
    script = """
import os, sys
import ibis
import marivo.analysis as mv
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
def forbidden(*args, **kwargs):
    raise AssertionError('Cold retention accessed source or semantic execution')
SourceSession.batches = forbidden
SemanticProject.load = forbidden
ibis.duckdb.connect = forbidden
ibis.sqlite.connect = forbidden
os.chdir(sys.argv[1])
session = mv.session.resume(sys.argv[2], by='id')
retained = session.artifact(sys.argv[3])
assert retained.to_pandas().value.tolist() == [True, None, False, None]
assert dict(retained.contract()._facts)['omega_count'] == '4'
for rule, expected in ((mv.any_anchor(),[True,None]),(mv.every_anchor(),[None,False])):
    assert retained.by_subject(rule=rule).execute().to_pandas().value.tolist() == expected
selected = retained.known_true().execute()
assert dict(selected.contract()._facts)['omega_count'] == '4'
assert selected.members(through=selected.subject_binding).execute().to_pandas()['member'].tolist() == [9007199254740993]
"""
    subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), session.id, retained.state.artifact_ref.ref],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )


@pytest.mark.runtime
def test_sqlite_anchor_dst_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    beginning = datetime(2026, 3, 7, 12, tzinfo=ZoneInfo("America/New_York"))
    offset = int((beginning - START).total_seconds())
    session, population, _, claims, _ = build_lifecycle_public(
        tmp_path,
        backend_name="sqlite",
        rows=[(0, "started", offset, 1), (0, "finished", offset + 23 * 3600, 2)],
    )
    started = ms.participant_role(event=ms.ref.event("commerce.started"), name="subject")
    returning = ms.participant_role(event=ms.ref.event("commerce.finished"), name="subject")
    anchors = session.anchors(
        started,
        population=population,
        during=mv.time_scope(start=beginning, end=beginning + timedelta(seconds=1)),
        business_order=ms.ref.business_order("commerce.order"),
    )
    claims = (
        replace(
            claims[0], inputs=(returning.event,), complete_through=beginning + timedelta(days=2)
        ),
    )
    for window, expected in (
        (mv.elapsed(mv.duration(hours=24)), True),
        (mv.calendar_days(1, ZoneInfo("America/New_York")), False),
    ):
        result = anchors.retention(returning, within=window, completeness=claims).execute()
        assert result.to_pandas().value.tolist() == [expected]


@pytest.mark.runtime
def test_sqlite_empty_anchor_omega(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _, anchors, returning, claims = build_retention(tmp_path, backend_name="sqlite", empty=True)
    retained = anchors.retention(
        returning, within=mv.elapsed(mv.duration(seconds=10)), completeness=claims
    ).execute()
    assert retained.to_pandas().empty
    assert dict(retained.contract()._facts)["omega_count"] == "0"
    assert "Undefined(empty_omega)" in dict(retained.contract()._facts)["deterministic_bounds"]
    assert retained.by_subject(rule=mv.any_anchor()).execute().to_pandas().empty


@pytest.mark.runtime
def test_sqlite_anchor_observation_overlap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    build_retention(
        tmp_path,
        backend_name="sqlite",
        rows=[
            (0, "started", 0, 1),
            (0, "finished", 0, 2),
            (0, "started", 8, 3),
            (0, "finished", 9, 4),
            (0, "finished", 10, 5),
            (0, "finished", 18, 6),
            (1, "started", 0, 1),
            (1, "started", 8, 2),
        ],
    )
    model = tmp_path / "models/semantic/commerce/objects.py"
    model.write_text(
        model.read_text()
        + "\nscore = ms.measure_column(name='score',entity=facts,column='seq',additivity=ms.additive_all(),unit='1')\nscore_sum = ms.aggregate(name='score_sum',measure=score,agg='sum',time=instant,nulls=ms.nulls.ignore(),empty=ms.empty.zero())\n"
    )
    ms.load(workspace_dir=tmp_path)
    session = mv.session.get_or_create("anchor-score", report_timezone="UTC")
    anchors = session.anchors(
        ms.participant_role(event=ms.ref.event("commerce.started"), name="subject"),
        population=session.members(ms.ref.entity("commerce.subjects")),
        during=mv.time_scope(start=START, end=START + timedelta(seconds=100)),
        business_order=ms.ref.business_order("commerce.order"),
    )
    result = anchors.observe(
        ms.ref.metric("commerce.score_sum"),
        within=mv.elapsed(mv.duration(seconds=10)),
        via=ms.ref.relationship("commerce.participant"),
    ).execute()
    # The contribution at second 9 is used by both overlapping windows. The
    # same-instant successor is included; each own anchor and deadline is excluded.
    expected = [9, 9, 2, 0]
    assert result.to_pandas().value.tolist() == expected
    assert result._dataset is not None
    retained = next(part for part in result._dataset.verified().parts if part.role == "anchor")
    uses = retained.table.column("anchor__uses_0").to_pylist()
    assert [len(pool) for pool in uses] == [3, 2, 1, 0]
    assert any(left == right for left in uses[0] for right in uses[1])
    calendar = anchors.observe(
        ms.ref.metric("commerce.score_sum"),
        within=mv.calendar_days(1, ZoneInfo("UTC")),
        via=ms.ref.relationship("commerce.participant"),
    ).execute()
    assert calendar.to_pandas().value.tolist() == [20, 15, 2, 0]

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Fixed Anchor observation accessed a source")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    assert result.to_pandas().value.tolist() == expected
    script = """
import os, sys
import ibis
import marivo.analysis as mv
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
def forbidden(*args, **kwargs):
    raise AssertionError('Cold Anchor observation accessed source or semantic execution')
SourceSession.batches = forbidden
SemanticProject.load = forbidden
ibis.duckdb.connect = forbidden
ibis.sqlite.connect = forbidden
os.chdir(sys.argv[1])
session = mv.session.resume(sys.argv[2], by='id')
result = session.artifact(sys.argv[3])
assert result.to_pandas().value.tolist() == [9, 9, 2, 0]
"""
    subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), session.id, result.state.artifact_ref.ref],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
