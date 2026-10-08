"""Native SQLite C18 opportunity truth and elapsed/calendar deadline probes."""

import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import CompiledRead, SourceBatchStream, SourceSession
from marivo.refs import MetricKind, Ref
from marivo.semantic.runtime_metric import RuntimeMetricExpr
from tests.analysis.journey.retention_fixtures import build_retention
from tests.analysis.lifecycle.lifecycle_fixtures import START, build_lifecycle_public
from tests.support.documentation import _blocks
from tests.support.json import Json
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
from marivo.datasource.adapters import CompiledRead, SourceBatchStream, SourceSession
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
from marivo.datasource.adapters import CompiledRead, SourceBatchStream, SourceSession
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


@pytest.mark.runtime
def test_sqlite_anchor_components_and_offline_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import shutil
    import sqlite3
    from contextlib import closing

    from tests.analysis.materialization.domain_recovery_worker import snapshot

    monkeypatch.chdir(tmp_path)
    build_lifecycle_public(
        tmp_path,
        backend_name="sqlite",
        observations=True,
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
    database = tmp_path / "source.sqlite"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("ALTER TABLE facts ADD large BIGINT")
        connection.execute("UPDATE facts SET large=?", (2**53 + 1,))
        connection.execute(
            "CREATE TABLE other (id BIGINT, sid BIGINT, amount DOUBLE, instant TIMESTAMP)"
        )
        connection.executemany(
            "INSERT INTO other VALUES (?, ?, ?, ?)",
            [
                (
                    1,
                    2**53 + 1,
                    20.0,
                    (START + timedelta(seconds=1)).replace(tzinfo=None).isoformat(sep=" "),
                ),
                (
                    2,
                    2**53 + 2,
                    50.0,
                    (START + timedelta(seconds=1)).replace(tzinfo=None).isoformat(sep=" "),
                ),
            ],
        )
    model = tmp_path / "models/semantic/commerce/objects.py"
    model.write_text(
        model.read_text()
        + """
large=ms.measure_column(name='large',entity=facts,column='large',additivity=ms.additive_all(),unit='1')
large_sum=ms.aggregate(name='large_sum',measure=large,agg='sum',time=instant,empty=ms.empty.zero())
nullable=ms.aggregate(name='nullable',measure=amount,agg='sum',time=instant,empty=ms.empty.null())
other=ms.entity(name='other',datasource=ms.ref.datasource('warehouse'),source=md.table('other'),primary_key=['id'])
other_subject=ms.dimension_column(name='sid',entity=other,column='sid')
other_time=ms.time_dimension_column(name='instant',entity=other,column='instant',granularity='second',parse=ms.timestamp(timezone='UTC'),is_default=True)
other_amount=ms.measure_column(name='amount',entity=other,column='amount',additivity=ms.additive_all(),unit='USD')
other_sum=ms.aggregate(name='other_sum',measure=other_amount,agg='sum',time=other_time,empty=ms.empty.zero())
other_participant=ms.relationship(name='other_participant',from_entity=other,to_entity=subjects,keys=[ms.join_on(other_subject,subject_sid)])
"""
    )
    ms.load(workspace_dir=tmp_path)
    session = mv.session.get_or_create("anchor-components", report_timezone="UTC")
    anchors = session.anchors(
        ms.participant_role(event=ms.ref.event("commerce.started"), name="subject"),
        population=session.members(ms.ref.entity("commerce.subjects")),
        during=mv.time_scope(start=START, end=START + timedelta(seconds=100)),
        business_order=ms.ref.business_order("commerce.order"),
    )
    namespace = {"mv": mv, "ms": ms, "anchors": anchors}
    code = next(
        block
        for block in _blocks("en", "analysis-workflow")
        if block.startswith("filtered_revenue =")
    )
    exec(compile(code, "latest-filtered-anchor-example", "exec"), namespace)
    documented = namespace["filtered_anchor"]
    assert isinstance(documented, mv.MaterializedNumericRelation)
    assert documented.to_pandas().value.tolist() == [6.0, 9.0, 0.0, 0.0]
    revenue = ms.ref.metric("commerce.revenue")
    large = ms.ref.metric("commerce.large_sum")
    count = ms.ref.metric("commerce.fact_count")
    ratio = mv.runtime_metric.ratio(large, count, label="per_fact")
    linear = mv.runtime_metric.linear(
        add=[revenue], subtract=[ms.ref.metric("commerce.other_sum")], label="net"
    )
    variants: list[tuple[Ref[MetricKind] | RuntimeMetricExpr, list[int | float | None]]] = [
        (count, [3, 2, 1, 0]),
        (large, [(2**53 + 1) * n for n in (3, 2, 1, 0)]),
        (ms.ref.metric("commerce.nullable"), [9.0, 9.0, 2.0, None]),
        (
            mv.runtime_metric.slice(
                revenue,
                by={ms.ref.dimension("commerce.facts.kind"): "finished"},
                label="finished_only",
            ),
            [6.0, 9.0, 0.0, 0.0],
        ),
        # The exact rate is 2**53+1. Native division consumes each represented sum.
        (ratio, [float((2**53 + 1) * n) / n for n in (3, 2, 1)] + [None]),
        (linear, [-11.0, 9.0, -48.0, 0.0]),
    ]
    inputs: dict[str, Json] = {}
    manifest: dict[str, Json] = {"session": session.id, "inputs": inputs}
    for index, (metric, expected) in enumerate(variants):
        via = (
            mv.routes(
                mv.route(
                    ms.ref.entity("commerce.facts"),
                    through=(ms.ref.relationship("commerce.participant"),),
                ),
                mv.route(
                    ms.ref.entity("commerce.other"),
                    through=(ms.ref.relationship("commerce.other_participant"),),
                ),
            )
            if metric is linear
            else ms.ref.relationship("commerce.participant")
        )
        logical = anchors.observe(metric, within=mv.elapsed(mv.duration(seconds=10)), via=via)
        fixed = logical.execute()
        frame = fixed.to_pandas()
        for actual, value in zip(frame.value, expected, strict=True):
            assert pd.isna(actual) if value is None else actual == value, (
                index,
                actual,
                value,
                frame.cell_tag.tolist(),
            )
        if metric is ratio:
            assert frame.cell_tag.tolist() == ["defined"] * 3 + ["undefined"]
            assert frame.cell_reason.iloc[-1] == "zero_denominator"
        if metric is linear or metric is ratio:
            assert fixed._dataset is not None
            retained = next(
                part for part in fixed._dataset.verified().parts if part.role == "anchor"
            )
            assert [len(pool) for pool in retained.table["anchor__uses_0"].to_pylist()] == [
                3,
                2,
                1,
                0,
            ]
            assert [len(pool) for pool in retained.table["anchor__uses_1"].to_pylist()] == (
                [1, 0, 1, 0] if metric is linear else [3, 2, 1, 0]
            )
        inputs[str(index)] = snapshot(fixed)
    before_files = set(tmp_path.rglob("*.parquet"))
    batches = SourceSession.batches
    interrupted = []

    def cancel_component(
        self: SourceSession, read: CompiledRead, *, chunk_size: int
    ) -> SourceBatchStream:
        if read.purpose == "analysis.domain.prepare" and '"other"' in read.sql:
            interrupted.append(read.source_identity)
            raise KeyboardInterrupt("cancelled independent Anchor component")
        return batches(self, read, chunk_size=chunk_size)

    with monkeypatch.context() as cancellation:
        cancellation.setattr(SourceSession, "batches", cancel_component)
        with pytest.raises(KeyboardInterrupt, match="independent Anchor component"):
            anchors.observe(linear, within=mv.elapsed(mv.duration(seconds=11)), via=via).execute()
    assert interrupted
    assert set(tmp_path.rglob("*.parquet")) == before_files
    assert session._runtime.store.resources(session.id) == ()
    import pyarrow as pa

    from marivo.analysis.core.graph import MethodNode
    from marivo.analysis.materialization import anchor_execution
    from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline
    from marivo.analysis.materialization.graph_exchange import ExchangeResult

    original_observe = anchor_execution.observe

    def expire(
        node: MethodNode, selected: ExchangeResult, candidates: pa.Table, binding: str
    ) -> ExchangeResult:
        CURRENT.set(ExecuteDeadline(0, clock=lambda: 601))
        return original_observe(node, selected, candidates, binding)

    with monkeypatch.context() as expiry:
        expiry.setattr(anchor_execution, "observe", expire)
        with pytest.raises(AnalysisError, match="execute_timeout"):
            anchors.observe(linear, within=mv.elapsed(mv.duration(seconds=12)), via=via).execute()
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("UPDATE facts SET large=?", (2**63 - 1,))
    with pytest.raises(AnalysisError):
        anchors.observe(
            large,
            within=mv.elapsed(mv.duration(seconds=10)),
            via=ms.ref.relationship("commerce.participant"),
        ).execute()
    assert set(tmp_path.rglob("*.parquet")) == before_files
    assert session._runtime.store.resources(session.id) == ()
    (tmp_path / "anchor-components.json").write_text(json.dumps(manifest))
    shutil.rmtree(tmp_path / "models")
    database.unlink()
    for phase in ("fixed", "cold"):
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.journey.sqlite_anchor_components_worker",
                str(tmp_path),
                phase,
            ],
            cwd=PROJECT_ROOT,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert process.returncode == 0, process.stdout + process.stderr
    assert session._runtime.store.resources(session.id) == ()
