"""SQLite direct-axis combinations through real capture and offline recovery."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Generator, Iterator
from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import pyarrow as pa
import pytest

import marivo
import marivo.analysis as mv
import marivo.datasource.domain_snapshot as snapshots
import marivo.semantic as ms
from marivo.analysis.core.graph import MethodNode, topology
from marivo.analysis.core.rules import FunnelAxesPrepare, FunnelReduce
from marivo.analysis.errors import AnalysisError
from marivo.analysis.methods.physical import Qualified, SourceShape, TimeShape
from marivo.analysis.methods.registry import REGISTRY
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceBatchStream, SourceSession
from marivo.refs import DimensionKind, Ref
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.shared_fixtures import run_ids
from tests.support.documentation import _example
from tests.support.json import Json, encode, obj, read
from tests.support.paths import PROJECT_ROOT

START = datetime(2026, 8, 1, tzinfo=timezone.utc)
BASELINE = START - timedelta(days=3)
LARGE = 2**53 + 1
LONG_AXIS_COLUMNS = tuple(("region", "code", "channel")[i % 3] for i in range(17))
COUNTS = (
    "cohort_count",
    "resolved_cohort_count",
    "entry_count",
    "resolved_entry_count",
    "reached_count",
    "lost_count",
    "coverage_censored_count",
)
SUBJECTS = (
    (1, "a", "x", LARGE),
    (2, "a", "y", LARGE + 1),
    (3, None, "x", None),
    (4, "Other", "Other", LARGE + 3),
)


def _steps() -> tuple[mv.PatternStep, mv.PatternStep]:
    steps = tuple(
        mv.step(
            participant=ms.participant_role(event=ms.ref.event("sales." + name), name="subject"),
            key=name,
        )
        for name in ("start", "end")
    )
    return steps[0], steps[1]


def _build(
    root: Path,
    factory: Callable[[dict[str, str]], SemanticProject],
    *,
    axis_columns: tuple[str, ...] = (),
    history: bool = False,
) -> tuple[Session, Callable[[datetime], mv.LogicalJourneyResult], Path]:
    database = root / "source.sqlite"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "CREATE TABLE subjects (id BIGINT, region VARCHAR, channel VARCHAR, code BIGINT, amount DOUBLE)"
        )
        connection.executemany("INSERT INTO subjects VALUES (?, ?, ?, ?, 1.5)", SUBJECTS)
        connection.execute(
            "CREATE TABLE events (id BIGINT, owner BIGINT, kind VARCHAR, happened TIMESTAMP)"
        )
        events: list[tuple[int, int, str, str]] = []
        for beginning, completed in ((START, (1, 4)), (BASELINE, (2,))):
            for identity, *_ in SUBJECTS:
                for kind in ("start", "end") if identity in completed else ("start",):
                    events.append(
                        (
                            LARGE + len(events),
                            identity,
                            kind,
                            (beginning + timedelta(seconds=10 if kind == "end" else 0)).strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                        )
                    )
        connection.executemany("INSERT INTO events VALUES (?, ?, ?, ?)", events)
    history_model = ""
    if history:
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                "CREATE TABLE snapshots (id BIGINT, code BIGINT, region VARCHAR, day DATE)"
            )
            connection.execute(
                "CREATE TABLE validity (id BIGINT, leaf_id BIGINT, zone VARCHAR, day DATE, finish DATE)"
            )
            connection.execute("CREATE TABLE leaf (id BIGINT, channel VARCHAR)")
            for identity, region, channel, code in SUBJECTS:
                connection.execute("INSERT INTO leaf VALUES (?, ?)", (identity, channel))
                for beginning in (BASELINE, START):
                    connection.execute(
                        "INSERT INTO snapshots VALUES (?, ?, ?, ?)",
                        (
                            identity,
                            code,
                            region if beginning == START else "old",
                            beginning.date().isoformat(),
                        ),
                    )
                connection.execute(
                    "INSERT INTO validity VALUES (?, ?, ?, ?, ?)",
                    (
                        identity,
                        identity,
                        "old",
                        BASELINE.date().isoformat(),
                        START.date().isoformat(),
                    ),
                )
                connection.execute(
                    "INSERT INTO validity VALUES (?, ?, ?, ?, NULL)",
                    (identity, identity, "new", START.date().isoformat()),
                )
        history_model = """
snapshots=ms.entity(name='snapshots',datasource=ms.ref.datasource('warehouse'),source=md.table('snapshots'),primary_key=['id'],versioning=ms.snapshot(partition_field=ms.ref.time_dimension('sales.snapshots.day'),grain='day',timezone='UTC'))
snapshot_id=ms.dimension_column(name='id',entity=snapshots,column='id')
snapshot_code=ms.dimension_column(name='code',entity=snapshots,column='code')
snapshot_region=ms.dimension_column(name='region',entity=snapshots,column='region')
snapshot_day=ms.time_dimension_column(name='day',entity=snapshots,column='day',granularity='day')
validity=ms.entity(name='validity',datasource=ms.ref.datasource('warehouse'),source=md.table('validity'),primary_key=['id'],versioning=ms.validity(valid_from=ms.ref.time_dimension('sales.validity.day'),valid_to=ms.ref.time_dimension('sales.validity.finish'),interval='closed_open',open_end=(None,)))
validity_id=ms.dimension_column(name='id',entity=validity,column='id')
validity_leaf=ms.dimension_column(name='leaf_id',entity=validity,column='leaf_id')
validity_zone=ms.dimension_column(name='zone',entity=validity,column='zone')
validity_day=ms.time_dimension_column(name='day',entity=validity,column='day',granularity='day',parse=ms.datetime(timezone='UTC'))
validity_finish=ms.time_dimension_column(name='finish',entity=validity,column='finish',granularity='day',parse=ms.datetime(timezone='UTC'))
leaf=ms.entity(name='leaf',datasource=ms.ref.datasource('warehouse'),source=md.table('leaf'),primary_key=['id'])
leaf_id=ms.dimension_column(name='id',entity=leaf,column='id')
leaf_channel=ms.dimension_column(name='channel',entity=leaf,column='channel')
subject_snapshot=ms.relationship(name='subject_snapshot',from_entity=subjects,to_entity=snapshots,keys=[ms.join_on(subject_id,snapshot_id)])
snapshot_validity=ms.relationship(name='snapshot_validity',from_entity=snapshots,to_entity=validity,keys=[ms.join_on(snapshot_id,validity_id)])
validity_leaf_link=ms.relationship(name='validity_leaf_link',from_entity=validity,to_entity=leaf,keys=[ms.join_on(validity_leaf,leaf_id)])
"""
    factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            f"md.sqlite(name='warehouse',path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\n"
            "ms.domain(name='sales',owner='Analytics',default=True)\n",
            "sales/objects.py": """import marivo.datasource as md
import marivo.semantic as ms
subjects=ms.entity(name='subjects',datasource=ms.ref.datasource('warehouse'),source=md.table('subjects'),primary_key=['id'])
events=ms.entity(name='events',datasource=ms.ref.datasource('warehouse'),source=md.table('events'),primary_key=['id'])
subject_id=ms.dimension_column(name='id',entity=subjects,column='id')
region=ms.dimension_column(name='region',entity=subjects,column='region')
channel=ms.dimension_column(name='channel',entity=subjects,column='channel')
code=ms.dimension_column(name='code',entity=subjects,column='code')
amount=ms.dimension_column(name='amount',entity=subjects,column='amount')
event_id=ms.dimension_column(name='id',entity=events,column='id')
owner=ms.dimension_column(name='owner',entity=events,column='owner')
kind=ms.dimension_column(name='kind',entity=events,column='kind')
instant=ms.time_dimension_column(name='instant',entity=events,column='happened',granularity='second',parse=ms.timestamp(timezone='UTC'))
event_subject=ms.relationship(name='event_subject',from_entity=events,to_entity=subjects,keys=[ms.join_on(owner,subject_id)])
@ms.event(name='start',identity=(event_id,),occurred_at=instant,participants=(ms.participant(name='subject',path=(event_subject,),cardinality='one'),))
def start(rows):
    return ms.bind(kind,rows)=='start'
@ms.event(name='end',identity=(event_id,),occurred_at=instant,participants=(ms.participant(name='subject',path=(event_subject,),cardinality='one'),))
def end(rows):
    return ms.bind(kind,rows)=='end'
"""
            + history_model
            + "".join(
                f"axis_{i}=ms.dimension_column(name='axis_{i}',entity=subjects,column={column!r})\n"
                for i, column in enumerate(axis_columns)
            ),
        }
    )
    session = mv.session.get_or_create("o2b-direct-axes", report_timezone="UTC")
    population = session.members(ms.ref.entity("sales.subjects"))
    pattern = mv.sequence(*_steps())
    completeness = mv.BoundedCompletenessDeclarationV1(
        inputs=(ms.ref.event("sales.start"), ms.ref.event("sales.end")),
        complete_from=BASELINE,
        complete_through=START + timedelta(days=10),
        rationale="All fixture occurrences are explicitly retained.",
    )

    def journeys(beginning: datetime) -> mv.LogicalJourneyResult:
        return session.events.match(
            pattern,
            population=population,
            cohort_window=mv.time_scope(start=beginning, end=beginning + timedelta(days=1)),
            completion_through=beginning + timedelta(days=2),
            matching=mv.first_per_subject(),
            completeness=(completeness,),
        )

    return session, journeys, database


def _axes(names: tuple[str, ...]) -> tuple[Ref[DimensionKind], ...]:
    return tuple(ms.ref.dimension("sales.subjects." + name) for name in names)


@pytest.mark.runtime
@pytest.mark.parametrize(
    "names",
    (
        ("region", "id"),
        ("region", "channel"),
        ("id", "code"),
        ("region", "code", "channel"),
        pytest.param(tuple(f"axis_{i}" for i in range(17)), id="long-direct"),
    ),
)
def test_sqlite_direct_axis_tuples(
    names: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    axis_columns = LONG_AXIS_COLUMNS if names[0] == "axis_0" else ()
    session, journeys, _ = _build(tmp_path, semantic_project_factory, axis_columns=axis_columns)
    axes = _axes(names)
    logical = journeys(START).funnel(axes=axes)
    # Use actual graph signatures, not a hand-built consumer-only invocation.
    for node in topology(logical._node.root):
        if not isinstance(node, MethodNode) or not isinstance(
            node.parameters, (FunnelAxesPrepare, FunnelReduce)
        ):
            continue
        registration = REGISTRY.lookup(node.method)
        declaration = next(
            item
            for item in registration.implementations
            if item.key.shape
            == SourceShape("sqlite", "table", "native", TimeShape("instant", "us", "UTC"))
            and item.key.input_domains
            == tuple(edge.node.signature.domain.kind for edge in node.inputs)
        )
        selected = REGISTRY.select(
            declaration.key, tuple(edge.node.signature for edge in node.inputs), node.parameters
        )
        assert selected.implementation == declaration
        assert isinstance(selected.implementation.qualification, Qualified)
        assert (
            selected.implementation.qualification.consumer_id
            == "analysis.materialization.funnel_execution"
        )
    fixed = logical.execute()
    frame = fixed.to_pandas()
    expected: dict[tuple[object, ...], tuple[int, ...]] = {}
    indices = {"id": 0, "region": 1, "channel": 2, "code": 3}
    indices.update({f"axis_{i}": indices[column] for i, column in enumerate(axis_columns)})
    for subject in SUBJECTS:
        coordinate = tuple(subject[indices[name]] for name in names)
        expected[(0, *coordinate)] = (1, 1, 1, 1, 1, 0, 0)
        reached = int(subject[0] in (1, 4))
        expected[(1, *coordinate)] = (1, 1, 1, 1, reached, 1 - reached, 0)
    observed: dict[tuple[object, ...], tuple[int, ...]] = {}
    for row in frame.to_dict("records"):
        coordinate = tuple(None if pd.isna(row[a.path]) else row[a.path] for a in axes)
        observed[(row["step"], *coordinate)] = tuple(row[name] for name in COUNTS)
        if row["step"] == 0:
            assert row["loss_rate_from_previous__cell_tag"] == "undefined"
            assert row["loss_rate_from_previous__cell_reason"] == "initial_step"
    assert len(frame) == len(observed) == 8
    assert observed == expected
    assert frame.groupby("step")[list(COUNTS)].sum().values.tolist() == [
        [4, 4, 4, 4, 4, 0, 0],
        [4, 4, 4, 4, 2, 2, 0],
    ]
    empty = journeys(START + timedelta(days=3)).funnel(axes=axes).execute().to_pandas()
    assert empty.empty and all(a.path in empty.columns for a in axes)
    assert session._runtime.store.resources(session._runtime.session_ref) == ()
    contract = fixed.contract()
    contract.show()
    assert len(capsys.readouterr().out) <= 8192
    help_fn = marivo.help
    assert callable(help_fn)
    help_fn(mv.LogicalJourneyResult.funnel)
    help_text = capsys.readouterr().out
    assert "string/int64" in help_text and len(help_text) <= 16384


@pytest.mark.runtime
def test_sqlite_direct_axis_rejection_and_capture_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    session, journeys, _ = _build(tmp_path, semantic_project_factory)
    before = run_ids(session)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Invalid axes submitted a business read")

    with monkeypatch.context() as rejection:
        rejection.setattr(SourceSession, "batches", forbidden)
        with pytest.raises(AnalysisError, match="unique exact Dimensions"):
            journeys(START).funnel(axes=_axes(("region", "region"))).execute()
        with pytest.raises(AnalysisError, match="string/int64 entry-axis path"):
            journeys(START).funnel(axes=_axes(("amount",))).execute()
        with pytest.raises(AnalysisError, match=r"qualified exact key for funnel\.reduce"):
            journeys(START).funnel().execute()
    assert run_ids(session) == before
    before_files = set(tmp_path.rglob("*.parquet"))
    streams: list[SourceBatchStream] = []
    iterate = SourceBatchStream._iterate

    def cancel_after_batch(stream: SourceBatchStream) -> Iterator[pa.RecordBatch]:
        batches = iterate(stream)
        assert isinstance(batches, Generator)
        with closing(batches):
            for batch in batches:
                streams.append(stream)
                assert batch.num_rows > 0
                batches.throw(KeyboardInterrupt("cancelled direct-axis capture"))
                yield batch

    with monkeypatch.context() as cancellation:
        cancellation.setattr(SourceBatchStream, "_iterate", cancel_after_batch)
        cancellation.setattr(
            snapshots, "TemporaryDirectory", partial(TemporaryDirectory, dir=tmp_path)
        )
        with pytest.raises(KeyboardInterrupt, match="direct-axis capture"):
            journeys(START).funnel(axes=_axes(("region", "code", "channel"))).execute()
    assert streams and all(stream._closed for stream in streams)
    assert all(stream._submission.state == "failed" for stream in streams)
    assert all(stream._submission.cursor_state == "closed" for stream in streams)
    assert set(tmp_path.rglob("*.parquet")) == before_files
    assert session._runtime.store.resources(session._runtime.session_ref) == ()
    assert not list(tmp_path.glob("marivo-sqlite-capture-*"))
    assert session._runtime.last_run_ref is not None
    run = session._runtime.store._graph_run(session._runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed"
    with session._runtime.store._connection() as connection:
        for table in ("dataset_artifacts", "findings", "dataset_evidence"):
            assert connection.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0


@pytest.mark.runtime
@pytest.mark.parametrize("history", [False, True])
def test_sqlite_three_axis_independent_recovery(
    history: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    session, journeys, database = _build(tmp_path, semantic_project_factory, history=history)
    examples = [_example(locale, "direct-funnel-axes") for locale in ("en", "zh")]
    assert examples[0] == examples[1]
    if history:
        axes = tuple(
            ms.ref.dimension(name)
            for name in ("sales.subjects.region", "sales.snapshots.code", "sales.leaf.channel")
        )
        namespace: dict[str, object] = {
            "mv": mv,
            "ms": ms,
            "journeys": journeys(START),
            "baseline_journeys": journeys(BASELINE),
        }
        code = _example("en", "historical-funnel-axes")
        exec(compile(code, "latest-history-axes-example", "exec"), namespace)
        current, baseline, change = (
            namespace["history_current"],
            namespace["history_baseline"],
            namespace["history_change"],
        )
    else:
        namespace = {
            "ms": ms,
            "journeys": journeys(START),
            "baseline_journeys": journeys(BASELINE),
        }
        exec(compile(examples[0], "latest-sqlite-direct-axes-example", "exec"), namespace)
        current, baseline, change = (
            namespace["current"],
            namespace["baseline"],
            namespace["three_axis_change"],
        )
        axes = _axes(("region", "code", "channel"))
    assert isinstance(current, mv.MaterializedFunnelResult)
    assert isinstance(baseline, mv.MaterializedFunnelResult)
    assert isinstance(change, mv.MaterializedFunnelComparisonResult)
    assert change.evidence_digest().finding_count > 0
    manifest: dict[str, Json] = {
        "session": session.id,
        "axes": [axis.path for axis in axes],
        "inputs": {
            "current": snapshot(current),
            "baseline": snapshot(baseline),
            "change": snapshot(change),
        },
    }
    (tmp_path / "direct-axes.json").write_bytes(encode(manifest))
    shutil.rmtree(tmp_path / "models")
    database.unlink()
    reports: list[dict[str, Json]] = []
    for phase in ("fixed", "cold"):
        report = tmp_path / (phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.journey.sqlite_funnel_direct_axes_worker",
                str(tmp_path),
                phase,
                str(report),
            ],
            cwd=PROJECT_ROOT,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT), "MARIVO_TELEMETRY": "off"},
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(obj(read(report)))
    assert len({os.getpid(), reports[0]["pid"], reports[1]["pid"]}) == 3
    assert reports[0]["outputs"] == reports[1]["outputs"]
    assert reports[1]["new_hit_runs"] == 0
    assert reports[1]["new_allocation_runs"] == 1


@pytest.mark.runtime
def test_sqlite_history_routes_assemble_exact_tuples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    session, journeys, _ = _build(tmp_path, semantic_project_factory, history=True)
    axes = tuple(
        ms.ref.dimension(name)
        for name in (
            "sales.snapshots.region",
            "sales.snapshots.code",
            "sales.validity.zone",
            "sales.leaf.channel",
            "sales.subjects.id",
        )
    )
    for beginning, completed in ((START, (1, 4)), (BASELINE, (2,))):
        rows = journeys(beginning).funnel(axes=axes).execute().to_pandas()
        expected: dict[tuple[object, ...], bool] = {
            (
                region if beginning == START else "old",
                code,
                "new" if beginning == START else "old",
                channel,
                identity,
            ): identity in completed
            for identity, region, channel, code in SUBJECTS
        }
        assert len(rows) == 8
        for row in rows.to_dict("records"):
            key = tuple(None if pd.isna(row[axis.path]) else row[axis.path] for axis in axes)
            reached = 1 if row["step"] == 0 else int(expected[key])
            assert tuple(row[name] for name in COUNTS) == (1, 1, 1, 1, reached, 1 - reached, 0)
        assert (
            rows.loc[rows.step == 0, "loss_rate_from_previous__cell_reason"].tolist()
            == ["initial_step"] * 4
        )
    assert session._runtime.store.resources(session.id) == ()


@pytest.mark.parametrize("fault", ["missing", "duplicate", "foreign"])
def test_entry_mappings_require_complete_occurrence_identity(fault: str) -> None:
    from marivo.analysis.core.domain_captures import DomainPreparationError
    from marivo.analysis.materialization.funnel_execution import assemble_axes

    base = pa.table({"key_0": ["start", "start"], "key_1": [1, 2], "key_2": [7, 7]})
    mapping = pa.table(
        {
            "key_0": ["start", "start"],
            "key_1": [2, 1],
            "key_2": [7, 7],
            "axis_0": pa.array([None, LARGE], type=pa.int64()),
        }
    )
    actual = assemble_axes(base, (mapping,))
    assert actual["axis_0"].to_pylist() == [LARGE, None]
    invalid = (
        mapping.slice(0, 1)
        if fault == "missing"
        else pa.concat_tables([mapping, mapping.slice(0, 1)])
        if fault == "duplicate"
        else mapping.set_column(1, "key_1", pa.array([2, 3]))
    )
    with pytest.raises(DomainPreparationError, match="path mapping"):
        assemble_axes(base, (invalid,))


@pytest.mark.runtime
@pytest.mark.parametrize("fault", ["missing", "duplicate", "overlap"])
def test_sqlite_history_capture_failure_is_atomic(
    fault: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    session, journeys, database = _build(tmp_path, semantic_project_factory, history=True)
    with closing(sqlite3.connect(database)) as connection, connection:
        if fault == "missing":
            connection.execute("DELETE FROM snapshots WHERE id=4 AND day='2026-08-01'")
        elif fault == "duplicate":
            connection.execute(
                "INSERT INTO snapshots SELECT * FROM snapshots WHERE id=4 AND day='2026-08-01'"
            )
        else:
            connection.execute(
                "INSERT INTO validity SELECT id,leaf_id,zone,'2026-07-31',NULL FROM validity WHERE id=4 AND finish IS NULL"
            )
    before = set(tmp_path.rglob("*.parquet"))
    axes = tuple(ms.ref.dimension(name) for name in ("sales.snapshots.code", "sales.leaf.channel"))
    with pytest.raises(AnalysisError):
        journeys(START).funnel(axes=axes).execute()
    assert set(tmp_path.rglob("*.parquet")) == before
    assert session._runtime.store.resources(session.id) == ()
    with session._runtime.store._connection() as connection:
        assert connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 0
