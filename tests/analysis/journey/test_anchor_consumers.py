"""DuckDB-native opportunity and component execution checks."""

import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import CompiledRead, SourceBatchStream, SourceSession
from marivo.refs import MetricKind, Ref
from marivo.semantic.runtime_metric import RuntimeMetricExpr
from tests.analysis.journey.retention_fixtures import build_retention
from tests.analysis.lifecycle.lifecycle_fixtures import START, build_lifecycle_public
from tests.support.json import Json
from tests.support.paths import PROJECT_ROOT


def test_anchor_observation_overlap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    build_retention(
        tmp_path,
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


@pytest.mark.slow
def test_anchor_components_and_offline_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import shutil

    import duckdb

    from tests.analysis.materialization.domain_recovery_worker import snapshot

    monkeypatch.chdir(tmp_path)
    build_lifecycle_public(
        tmp_path,
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
    database = tmp_path / "source.duckdb"
    with duckdb.connect(str(database)) as connection:
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
    revenue = ms.ref.metric("commerce.revenue")
    large = ms.ref.metric("commerce.large_sum")
    linear = mv.runtime_metric.linear(
        add=[revenue], subtract=[ms.ref.metric("commerce.other_sum")], label="net"
    )
    variants: list[tuple[Ref[MetricKind] | RuntimeMetricExpr, list[int | float | None]]] = [
        (large, [(2**53 + 1) * n for n in (3, 2, 1, 0)]),
        (linear, [-11.0, 9.0, -48.0, 0.0]),
    ]
    inputs: dict[str, Json] = {}
    manifest: dict[str, Json] = {"session": session.id, "inputs": inputs}
    for index, (metric, expected) in enumerate(variants):
        via = (
            (
                mv.path(ms.ref.relationship("commerce.participant")),
                mv.path(ms.ref.relationship("commerce.other_participant")),
            )
            if metric is linear
            else ms.ref.relationship("commerce.participant")
        )
        logical = anchors.observe(metric, within=mv.elapsed(mv.duration(seconds=10)), via=via)
        fixed = logical.execute()
        frame = fixed.to_pandas()
        assert sorted(frame.value.tolist()) == sorted(expected), index
        if metric is linear:
            assert fixed._dataset is not None
            retained = next(
                part for part in fixed._dataset.verified().parts if part.role == "anchor"
            )
            uses = retained.table["anchor__uses_0"].to_pylist()
            assert sorted(len(pool) for pool in uses) == [0, 1, 2, 3]
            assert sorted(len(pool) for pool in retained.table["anchor__uses_1"].to_pylist()) == [
                0,
                0,
                1,
                1,
            ]
            assert any(
                left == right
                for index, left_pool in enumerate(uses)
                for right_pool in uses[index + 1 :]
                for left in left_pool
                for right in right_pool
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
    with duckdb.connect(str(database)) as connection:
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
                "tests.analysis.journey.anchor_components_worker",
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
