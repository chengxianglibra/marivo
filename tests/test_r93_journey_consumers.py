"""Public C11/C12 Journey multiplicity and retained consumer probes."""

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Generator, Iterator
from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

import ibis
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.datasource.domain_snapshot as snapshots
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.public_dsl import MaterializedFunnelResult
from marivo.datasource.adapters import SourceBatchStream, SourceSession
from marivo.refs import DimensionKind, Ref
from marivo.semantic.reader import SemanticProject


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend,policy,axis_name",
    [
        ("duckdb", "every", "id"),
        ("sqlite", "every", "id"),
        ("duckdb", "first", "id"),
        ("sqlite", "first", "id"),
        ("sqlite", "first", "region"),
        ("sqlite", "first", "snapshot"),
        ("sqlite", "first", "validity"),
    ],
)
def test_public_journey_multiplicity_and_unknown(
    backend: str,
    policy: str,
    axis_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / ("source." + backend)
    source = ibis.duckdb.connect(path) if backend == "duckdb" else ibis.sqlite.connect(path)
    source.raw_sql("CREATE TABLE subjects (id BIGINT, region VARCHAR)")
    source.raw_sql("INSERT INTO subjects VALUES (1,'a'), (2,'Other'), (3,NULL)")
    source.raw_sql(
        "CREATE TABLE events (id BIGINT, owner BIGINT, kind VARCHAR, happened TIMESTAMP)"
    )
    source.raw_sql(
        "INSERT INTO events VALUES "
        "(1,1,'start','2026-08-01 00:00:00'),"
        "(2,1,'start','2026-08-01 00:00:01'),"
        "(3,1,'end','2026-08-01 00:00:10'),"
        "(4,2,'start','2026-08-01 00:00:00'),"
        "(5,3,'start','2026-08-01 00:00:00'),"
        "(6,3,'end','2026-08-01 00:00:00')"
    )
    source.raw_sql("ALTER TABLE events ADD COLUMN seq BIGINT")
    source.raw_sql(
        "UPDATE events SET seq = CASE id WHEN 2 THEN 20 WHEN 3 THEN 30 WHEN 6 THEN 20 ELSE 10 END"
    )
    source.raw_sql(
        "INSERT INTO events VALUES "
        "(7,1,'start','2026-07-29 00:00:00',10),"
        "(8,2,'start','2026-07-29 00:00:00',10),"
        "(9,2,'end','2026-07-29 00:00:10',20),"
        "(10,1,'start','2026-07-26 00:00:00',10),"
        "(11,2,'start','2026-07-26 00:00:00',10),"
        "(12,2,'end','2026-07-26 00:00:10',20),"
        "(13,3,'start','2026-07-26 00:00:00',10)"
    )
    source.raw_sql("CREATE TABLE history (id BIGINT, region VARCHAR, day DATE)")
    source.raw_sql(
        "INSERT INTO history VALUES "
        "(1,'old','2026-07-26'),(2,'Other','2026-07-26'),(3,NULL,'2026-07-26'),"
        "(1,'old','2026-07-29'),(2,'Other','2026-07-29'),(3,NULL,'2026-07-29'),"
        "(1,'new','2026-08-01'),(2,'Other','2026-08-01'),(3,NULL,'2026-08-01'),"
        "(1,'future','2026-08-02'),(2,'future','2026-08-02'),(3,'future','2026-08-02')"
    )
    source.raw_sql(
        "CREATE TABLE validity_history (id BIGINT, region VARCHAR, beginning DATE, ending DATE)"
    )
    source.raw_sql(
        "INSERT INTO validity_history VALUES "
        "(1,'old','2026-07-26','2026-08-01'),(1,'new','2026-08-01',NULL),"
        "(2,'Other','2026-07-26','2026-08-01'),(2,'Other','2026-08-01',NULL),"
        "(3,NULL,'2026-07-26','2026-08-01'),(3,NULL,'2026-08-01',NULL)"
    )
    if backend == "sqlite":
        source.con.commit()
    source.disconnect()
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.{backend}(name='warehouse',path={str(path)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\n"
            "ms.domain(name='sales',owner='R9',default=True)\n",
            "sales/models.py": """import marivo.datasource as md
import marivo.semantic as ms
subjects=ms.entity(name='subjects',datasource=ms.ref.datasource('warehouse'),source=md.table('subjects'),primary_key=['id'])
events=ms.entity(name='events',datasource=ms.ref.datasource('warehouse'),source=md.table('events'),primary_key=['id'])
subject_id=ms.dimension_column(name='id',entity=subjects,column='id')
region=ms.dimension_column(name='region',entity=subjects,column='region')
history=ms.entity(name='history',datasource=ms.ref.datasource('warehouse'),source=md.table('history'),primary_key=['id'],versioning=ms.snapshot(partition_field=ms.ref.time_dimension('sales.history.day'),grain='day',timezone='UTC'))
history_id=ms.dimension_column(name='id',entity=history,column='id')
history_region=ms.dimension_column(name='region',entity=history,column='region')
history_day=ms.time_dimension_column(name='day',entity=history,column='day',granularity='day')
subject_history=ms.relationship(name='subject_history',from_entity=subjects,to_entity=history,keys=[ms.join_on(subject_id,history_id)])
validity_history=ms.entity(name='validity_history',datasource=ms.ref.datasource('warehouse'),source=md.table('validity_history'),primary_key=['id'],versioning=ms.validity(valid_from=ms.ref.time_dimension('sales.validity_history.beginning'),valid_to=ms.ref.time_dimension('sales.validity_history.ending'),interval='closed_open',open_end=(None,),timezone='UTC'))
validity_id=ms.dimension_column(name='id',entity=validity_history,column='id')
validity_region=ms.dimension_column(name='region',entity=validity_history,column='region')
validity_beginning=ms.time_dimension_column(name='beginning',entity=validity_history,column='beginning',granularity='day')
validity_ending=ms.time_dimension_column(name='ending',entity=validity_history,column='ending',granularity='day')
subject_validity=ms.relationship(name='subject_validity',from_entity=subjects,to_entity=validity_history,keys=[ms.join_on(subject_id,validity_id)])
event_id=ms.dimension_column(name='id',entity=events,column='id')
seq=ms.dimension_column(name='seq',entity=events,column='seq')
owner=ms.dimension_column(name='owner',entity=events,column='owner')
kind=ms.dimension_column(name='kind',entity=events,column='kind')
instant=ms.time_dimension_column(name='time',entity=events,column='happened',granularity='second',parse=ms.timestamp(timezone='UTC'),is_default=True)
event_subject=ms.relationship(name='event_subject',from_entity=events,to_entity=subjects,keys=[ms.join_on(owner,subject_id)])
@ms.event(name='start',identity=(event_id,),occurred_at=instant,participants=(ms.participant(name='subject',path=(event_subject,),cardinality='one'),))
def start(rows):
    return ms.bind(kind,rows)=='start'
@ms.event(name='end',identity=(event_id,),occurred_at=instant,participants=(ms.participant(name='subject',path=(event_subject,),cardinality='one'),))
def end(rows):
    return ms.bind(kind,rows)=='end'
order=ms.business_order(name='order',subject=subjects,sequences=(ms.event_sequence(start,seq,order='integer'),ms.event_sequence(end,seq,order='integer')),ai_context=ms.ai_context(business_definition='Per Subject event sequence.'))
""",
        }
    )
    session = mv.session.get_or_create("c11", report_timezone="UTC")
    start = mv.step(
        participant=ms.participant_role(event=ms.ref.event("sales.start"), name="subject"),
        key="start",
    )
    end = mv.step(
        participant=ms.participant_role(event=ms.ref.event("sales.end"), name="subject"),
        key="end",
    )
    journeys = session.events.match(
        mv.sequence(start, end),
        population=session.members(ms.ref.entity("sales.subjects")),
        cohort_window=mv.time_scope(start="2026-08-01", end="2026-08-02"),
        completion_through=datetime(2026, 8, 2, tzinfo=timezone.utc),
        matching=mv.every_start(completion_assignment="shared")
        if policy == "every"
        else mv.first_per_subject(),
        business_order=ms.ref.business_order("sales.order"),
    ).execute()
    assert len(journeys.to_pandas()) == (4 if policy == "every" else 3)
    if policy == "every":
        with pytest.raises(AnalysisError, match="full first-per-subject"):
            journeys.funnel()
    else:
        assert journeys.funnel().execute().to_pandas()["reached_count"].tolist() == [3, 2]
        claims = (
            mv.BoundedCompletenessDeclarationV1(
                inputs=(ms.ref.event("sales.start"), ms.ref.event("sales.end")),
                complete_from=datetime(2026, 7, 26, tzinfo=timezone.utc),
                complete_through=datetime(2026, 8, 3, tzinfo=timezone.utc),
                rationale="Complete independent funnel fixture periods.",
            ),
        )

        population = session.members(ms.ref.entity("sales.subjects"))

        def complete_funnel(
            beginning: datetime, axes: tuple[Ref[DimensionKind], ...] = ()
        ) -> MaterializedFunnelResult:
            logical = session.events.match(
                mv.sequence(start, end),
                population=population,
                cohort_window=mv.time_scope(start=beginning, end=beginning + timedelta(days=1)),
                completion_through=beginning + timedelta(days=2),
                matching=mv.first_per_subject(),
                business_order=ms.ref.business_order("sales.order"),
                completeness=claims,
            )
            return (
                logical.funnel(axes=axes).execute()
                if axes
                else logical.execute().funnel().execute()
            )

        current = complete_funnel(datetime(2026, 8, 1, tzinfo=timezone.utc))
        baseline = complete_funnel(datetime(2026, 7, 29, tzinfo=timezone.utc))
        assert current.to_pandas().reached_count.tolist() == [3, 2]
        assert baseline.to_pandas().reached_count.tolist() == [2, 1]
        change = current.compare(baseline).execute()
        assert change.to_pandas().loss_rate_delta.tolist()[-1] == pytest.approx(-1 / 6)
        assert change.read(change.loss_rate_delta).execute().to_pandas().value.tolist()[
            -1
        ] == pytest.approx(-1 / 6)
        comparison_script = """
import os, sys
import ibis
import marivo.analysis as mv
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
def forbidden(*args, **kwargs):
    raise AssertionError('Cold Funnel comparison accessed source or semantic execution')
SourceSession.batches = forbidden
SemanticProject.load = forbidden
ibis.duckdb.connect = forbidden
ibis.sqlite.connect = forbidden
os.chdir(sys.argv[1])
session = mv.session.resume(sys.argv[2], by='id')
change = session.artifact(sys.argv[3])
assert abs(change.to_pandas().loss_rate_delta.tolist()[-1] + 1/6) < 1e-15
assert abs(change.read(change.loss_rate_delta).execute().to_pandas().value.tolist()[-1] + 1/6) < 1e-15
assert change.evidence_digest().finding_set_digest == sys.argv[4]
"""
        subprocess.run(
            [
                sys.executable,
                "-c",
                comparison_script,
                str(tmp_path),
                session.id,
                change.state.artifact_ref.ref,
                change.evidence_digest().finding_set_digest,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        )

        axes = (
            ms.ref.dimension(
                "sales.history.region"
                if axis_name == "snapshot"
                else "sales.validity_history.region"
                if axis_name == "validity"
                else "sales.subjects." + axis_name
            ),
        )
        grouped_current = complete_funnel(datetime(2026, 8, 1, tzinfo=timezone.utc), axes)
        grouped_baseline = complete_funnel(datetime(2026, 7, 26, tzinfo=timezone.utc), axes)
        grouped_change = grouped_current.compare(grouped_baseline).execute()
        allocation = grouped_change.attribute(
            target=mv.funnel_loss_rate(step=end), axes=axes
        ).execute()
        allocated = allocation.to_pandas()
        expected_loss = (
            {1: -1 / 3, 2: 1 / 3, 3: -1 / 3}
            if axis_name == "id"
            else {"old": -1 / 3, "new": 0.0, "Other": 1 / 3, None: -1 / 3}
            if axis_name in ("snapshot", "validity")
            else {"a": -1 / 3, "Other": 1 / 3, None: -1 / 3}
        )
        loss = allocated.loc[allocated.kind == "loss"]
        observed_loss = dict(zip(loss[axes[0].path], loss.contribution, strict=True))
        assert observed_loss == pytest.approx(expected_loss)
        assert all(json.loads(mask) == [False] for mask in allocated.other_mask)
        assert allocated.loc[allocated.kind == "denominator_mix", "contribution"].tolist() == [
            0.0
        ] * len(expected_loss)
        assert allocated.contribution.sum() == pytest.approx(-1 / 3)
        assert allocation.current.to_pandas().value.sum() == pytest.approx(1 / 3)
        assert allocation.baseline.to_pandas().value.sum() == pytest.approx(2 / 3)
        allocation_script = """
import os, sys
import ibis
import marivo.analysis as mv
import marivo.semantic as ms
import json
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
def forbidden(*args, **kwargs):
    raise AssertionError('Cold Funnel allocation accessed source or semantic execution')
SourceSession.batches = forbidden
SemanticProject.load = forbidden
ibis.duckdb.connect = forbidden
ibis.sqlite.connect = forbidden
os.chdir(sys.argv[1])
session = mv.session.resume(sys.argv[2], by='id')
change = session.artifact(sys.argv[3])
end = mv.step(participant=ms.participant_role(event=ms.ref.event('sales.end'), name='subject'), key='end')
continuation = change.attribute(target=mv.funnel_loss_rate(step=end), axes=(ms.ref.dimension(sys.argv[6]),))
result = continuation.execute()
assert continuation.execute().state.artifact_ref == result.state.artifact_ref
stored = session.artifact(sys.argv[4])
assert stored.evidence_digest().finding_set_digest == sys.argv[5]
assert stored.to_pandas().contribution.tolist() == result.to_pandas().contribution.tolist()
rows = result.to_pandas()
loss = rows.loc[rows.kind == 'loss']
expected = dict(json.loads(sys.argv[7]))
observed = dict(zip(loss[sys.argv[6]], loss.contribution, strict=True))
assert observed.keys() == expected.keys()
assert all(abs(observed[key]-expected[key]) < 1e-15 for key in expected)
assert all(json.loads(mask) == [False] for mask in rows.other_mask)
assert rows.loc[rows.kind == 'denominator_mix', 'contribution'].tolist() == [0.0]*len(expected)
assert abs(result.current.to_pandas().value.sum()-1/3) < 1e-15
assert abs(result.baseline.to_pandas().value.sum()-2/3) < 1e-15
"""
        subprocess.run(
            [
                sys.executable,
                "-c",
                allocation_script,
                str(tmp_path),
                session.id,
                grouped_change.state.artifact_ref.ref,
                allocation.state.artifact_ref.ref,
                allocation.evidence_digest().finding_set_digest,
                axes[0].path,
                json.dumps(list(expected_loss.items())),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        )
        if axis_name == "validity":
            assert set(grouped_current.to_pandas()[axes[0].path].dropna()) == {"new", "Other"}
            assert set(grouped_baseline.to_pandas()[axes[0].path].dropna()) == {"old", "Other"}
            before_missing = set(tmp_path.rglob("*.parquet"))
            with sqlite3.connect(path) as writer:
                writer.execute("DELETE FROM validity_history WHERE id=1 AND beginning='2026-08-01'")
            with pytest.raises(AnalysisError, match="complete historical path"):
                complete_funnel(datetime(2026, 8, 1, tzinfo=timezone.utc), axes)
            assert set(tmp_path.rglob("*.parquet")) == before_missing
            assert session._runtime.store.resources(session._runtime.session_ref) == ()
            assert grouped_current.to_pandas()[axes[0].path].dropna().isin(["new", "Other"]).all()

            with sqlite3.connect(path) as writer:
                writer.execute("INSERT INTO validity_history VALUES (1,'new','2026-08-01',NULL)")
                writer.execute(
                    "INSERT INTO validity_history VALUES (1,'overlap','2026-07-31',NULL)"
                )
            with pytest.raises(AnalysisError, match="non-overlapping validity versions"):
                complete_funnel(datetime(2026, 8, 1, tzinfo=timezone.utc), axes)
            assert set(tmp_path.rglob("*.parquet")) == before_missing
            assert session._runtime.store.resources(session._runtime.session_ref) == ()

        if axis_name == "snapshot":
            assert set(grouped_current.to_pandas()[axes[0].path].dropna()) == {"new", "Other"}
            assert set(grouped_baseline.to_pandas()[axes[0].path].dropna()) == {"old", "Other"}
            before_missing = set(tmp_path.rglob("*.parquet"))
            with sqlite3.connect(path) as writer:
                writer.execute("DELETE FROM history WHERE id=1 AND day='2026-08-01'")
            with pytest.raises(AnalysisError, match="complete historical path"):
                complete_funnel(datetime(2026, 8, 1, tzinfo=timezone.utc), axes)
            assert set(tmp_path.rglob("*.parquet")) == before_missing
            assert session._runtime.store.resources(session._runtime.session_ref) == ()
            assert grouped_current.to_pandas()[axes[0].path].dropna().isin(["new", "Other"]).all()
            with sqlite3.connect(path) as writer:
                writer.execute("ALTER TABLE history RENAME TO previous_history")
                writer.execute("CREATE TABLE history (id VARCHAR, region VARCHAR, day DATE)")
                writer.execute(
                    "INSERT INTO history SELECT CAST(id AS TEXT),region,day FROM previous_history"
                )
            with monkeypatch.context() as mismatch:

                def no_business_rows(*args: object, **kwargs: object) -> None:
                    raise AssertionError("Incompatible historical keys submitted a business read")

                mismatch.setattr(SourceSession, "batches", no_business_rows)
                with pytest.raises(
                    AnalysisError, match="compatible declared relationship key types"
                ):
                    complete_funnel(datetime(2026, 8, 1, tzinfo=timezone.utc), axes)

        if backend == "sqlite" and axis_name == "region":
            composite_axes = (ms.ref.dimension("sales.subjects.id"), axes[0])
            composite_current = complete_funnel(
                datetime(2026, 8, 1, tzinfo=timezone.utc), composite_axes
            )
            composite_baseline = complete_funnel(
                datetime(2026, 7, 26, tzinfo=timezone.utc), composite_axes
            )
            composite_change = composite_current.compare(composite_baseline).execute()
            hierarchy = composite_change.attribute(
                target=mv.funnel_loss_rate(step=end),
                axes=composite_axes,
                mode="hierarchy",
                top_k=2,
            ).execute()
            hierarchy_rows = hierarchy.to_pandas()
            for resolution in (1, 2):
                level = hierarchy_rows.loc[hierarchy_rows.resolution == resolution]
                assert level.contribution.sum() == pytest.approx(-1 / 3)
                for identity, category, value, mask in (
                    (1, "a" if resolution == 2 else None, -1 / 3, [False, False]),
                    (2, "Other" if resolution == 2 else None, 1 / 3, [False, False]),
                    (None, None, -1 / 3, [True, False]),
                ):
                    selected = level.loc[
                        (
                            level[composite_axes[0].path].isna()
                            if identity is None
                            else level[composite_axes[0].path].eq(identity)
                        )
                        & (
                            level[composite_axes[1].path].isna()
                            if category is None
                            else level[composite_axes[1].path].eq(category)
                        )
                        & level.kind.eq("loss")
                        & level.other_mask.eq(json.dumps(mask))
                    ]
                    assert len(selected) == 1
                    assert selected.contribution.tolist() == pytest.approx([value])
                assert (
                    level.loc[level.kind.eq("denominator_mix"), "contribution"].tolist()
                    == [0.0] * 3
                )

        if backend == "sqlite" and axis_name == "region":
            hierarchy_script = """
import os, sys
import ibis
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
def forbidden(*args, **kwargs):
    raise AssertionError('Cold hierarchy accessed source or semantic execution')
SourceSession.batches = forbidden
SemanticProject.load = forbidden
ibis.duckdb.connect = forbidden
ibis.sqlite.connect = forbidden
os.chdir(sys.argv[1])
session = mv.session.resume(sys.argv[2], by='id')
change = session.artifact(sys.argv[3])
end = mv.step(participant=ms.participant_role(event=ms.ref.event('sales.end'), name='subject'), key='end')
axes = (ms.ref.dimension('sales.subjects.id'),ms.ref.dimension('sales.subjects.region'))
continuation = change.attribute(target=mv.funnel_loss_rate(step=end), axes=axes, mode='hierarchy', top_k=2)
result = continuation.execute()
assert continuation.execute().state.artifact_ref == result.state.artifact_ref
assert result.to_pandas().to_json() == sys.argv[5]
stored = session.artifact(sys.argv[4])
assert stored.to_pandas().to_json() == sys.argv[5]
assert stored.evidence_digest().finding_set_digest == sys.argv[6]
"""
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    hierarchy_script,
                    str(tmp_path),
                    session.id,
                    composite_change.state.artifact_ref.ref,
                    hierarchy.state.artifact_ref.ref,
                    hierarchy_rows.to_json(),
                    hierarchy.evidence_digest().finding_set_digest,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
            )
            runtime = session._runtime

            def publication_counts() -> tuple[int, int, int]:
                with runtime.store._connection() as connection:
                    return tuple(
                        connection.execute("SELECT count(*) FROM " + table).fetchone()[0]
                        for table in ("dataset_artifacts", "findings", "dataset_evidence")
                    )

            before_cancel = publication_counts()
            streams: list[SourceBatchStream] = []
            iterate = SourceBatchStream._iterate

            def cancel_after_native_batch(stream: SourceBatchStream) -> Iterator[pa.RecordBatch]:
                with closing(iterate(stream)) as batches:
                    assert isinstance(batches, Generator)
                    for batch in batches:
                        streams.append(stream)
                        assert batch.num_rows > 0
                        batches.throw(KeyboardInterrupt("cancelled after native Funnel batch"))
                        yield batch

            with monkeypatch.context() as cancellation:
                cancellation.setattr(SourceBatchStream, "_iterate", cancel_after_native_batch)
                cancellation.setattr(
                    snapshots, "TemporaryDirectory", partial(TemporaryDirectory, dir=tmp_path)
                )
                with pytest.raises(KeyboardInterrupt, match="native Funnel batch"):
                    complete_funnel(datetime(2026, 8, 1, tzinfo=timezone.utc), axes)
            assert len(streams) == 1
            assert streams[0]._closed
            assert streams[0]._submission.state == "failed"
            assert streams[0]._submission.cursor_state == "closed"
            assert publication_counts() == before_cancel
            assert runtime.last_run_ref is not None
            cancelled_run = runtime.store._graph_run(runtime.last_run_ref)
            assert cancelled_run is not None and cancelled_run.lifecycle == "failed"
            assert runtime.store.resources(runtime.session_ref) == ()
            assert not list(tmp_path.glob("marivo-sqlite-capture-*"))
            assert allocation.to_pandas().contribution.tolist() == allocated.contribution.tolist()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Retained Journey consumer accessed its source")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    monkeypatch.setattr(SemanticProject, "load", forbidden)
    monkeypatch.setattr(ibis.duckdb, "connect", forbidden)
    elapsed = journeys.time_to_event(from_step=start, to_step=end).execute()
    observed = elapsed.observed_duration.execute()
    assert observed.to_pandas()["cell_tag"].tolist() == (
        ["defined", "defined", "unknown", "defined"]
        if policy == "every"
        else ["defined", "unknown", "defined"]
    )
    completed = elapsed.completed().duration.execute()
    assert len(completed.members().execute().to_pandas()) == 2
    assert elapsed.completed().duration.execute().to_pandas()["value"].tolist() == (
        [timedelta(seconds=10), timedelta(seconds=9), timedelta(0)]
        if policy == "every"
        else [timedelta(seconds=10), timedelta(0)]
    )
    if policy == "every":
        with pytest.raises(AnalysisError, match="first_per_subject"):
            journeys.read(mv.dropped_before(step=end))
    else:
        assert journeys.read(mv.dropped_before(step=end)).execute().to_pandas()[
            "cell_tag"
        ].tolist() == ["defined", "unknown", "defined"]
    observed_ratio = observed.ratio(observed).execute().to_pandas()
    assert observed_ratio["cell_tag"].tolist() == (
        ["defined", "defined", "unknown", "undefined"]
        if policy == "every"
        else ["defined", "unknown", "undefined"]
    )
    assert observed_ratio.loc[observed_ratio.cell_tag == "unknown", "cell_reason"].tolist() == [
        "coverage_censored"
    ]
    ratio = completed.ratio(completed).execute()
    rows = ratio.to_pandas()
    expected_tags = (
        ["defined", "defined", "undefined"] if policy == "every" else ["defined", "undefined"]
    )
    expected_values = [1.0, 1.0] if policy == "every" else [1.0]
    expected_reasons = (
        [None, None, "zero_denominator"] if policy == "every" else [None, "zero_denominator"]
    )
    assert rows["cell_tag"].tolist() == expected_tags
    assert rows["value"].tolist()[: len(expected_values)] == expected_values
    assert rows["cell_reason"].tolist() == expected_reasons
    assert completed._dataset is not None
    script = """
import os, sys, json
import ibis
import marivo.analysis as mv
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
def forbidden(*args, **kwargs):
    raise AssertionError('Cold Duration ratio accessed source or semantic execution')
SourceSession.batches = forbidden
SemanticProject.load = forbidden
ibis.duckdb.connect = forbidden
os.chdir(sys.argv[1])
session = mv.session.resume(sys.argv[2], by='id')
duration = session.artifact(sys.argv[3])
rows = duration.ratio(duration).execute().to_pandas()
tags, values, reasons = json.loads(sys.argv[4])
assert rows['cell_tag'].tolist() == tags
assert rows['value'].tolist()[:len(values)] == values
assert rows['cell_reason'].tolist() == reasons
"""
    subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(tmp_path),
            session.id,
            completed._dataset.artifact.artifact_ref,
            json.dumps([expected_tags, expected_values, expected_reasons]),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
