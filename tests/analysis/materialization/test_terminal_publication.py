"""R2.5 terminal output codecs, publication visibility and recovery."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Literal, NoReturn, overload

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
from marivo.analysis.core.artifact_data import (
    ArtifactData,
    ArtifactManifest,
    CalendarDataAxis,
    DataAxis,
    DataColumn,
    DataSchema,
    DecimalDataType,
    DurationDataType,
    OutputCoordinate,
    OutputEvidence,
    OutputFinding,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    MemoryFrameBacking,
    StoredFrameBacking,
)
from marivo.analysis.core.frozen_calendar import FrozenKey
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.errors import AnalysisError
from marivo.analysis.evidence._dataset_types import AssociationFindingValueV1, NoAssociationLagV1
from marivo.analysis.materialization import terminal_publication, terminal_store
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
    RecoveryPendingError,
)
from marivo.analysis.materialization.graph_reading import ReadLayout, open_manifest
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.terminal_protocol import decode, encode
from marivo.analysis.materialization.terminal_publication import reconcile
from marivo.analysis.materialization.terminal_storage import POLICY, read_output, write_output
from marivo.analysis.session.core import Session
from tests.analysis.materialization.terminal_recovery_worker import method_result
from tests.shared_fixtures import terminal_run, terminal_runs

if TYPE_CHECKING:
    from tests.conftest import _SemanticProjectFactory


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.chdir(tmp_path)
    return mv.session.create("terminal", timezone="UTC")


def constant(session: Session, value: int = 7) -> AnalysisFrame:
    return session.values(by=(), columns={"answer": None}, rows=(((), {"answer": value}),))


def committed(frame: AnalysisFrame) -> ArtifactManifest:
    assert isinstance(frame._backing, StoredFrameBacking)
    return open_manifest(frame._local.store, frame._local.session_ref, frame._backing.artifact_id)


def result_metadata(
    *, findings: tuple[OutputFinding, ...] = (), checks: tuple[OutputEvidence, ...] = ()
) -> FrameResultMetadata:
    return FrameResultMetadata(
        "existing-result",
        FrameAudit(
            (),
            EvaluationRecord(datetime(2020, 1, 1, tzinfo=timezone.utc), (), ("literal",), checks),
        ),
        findings,
    )


def calendar_constant(session: Session, calendar: mv.CalendarAxis) -> AnalysisFrame:
    from marivo._definition.types import CalendarCoordinate
    from marivo._temporal import anchor_from_datetime
    from marivo.analysis.core.frame_calendar import freeze_calendar

    grid = calendar.grid
    coordinate = CalendarCoordinate(
        grid.identity,
        grid.grain_spec,
        grid.boundary_timezone,
        grid.snapshot_digest,
        tuple(
            (
                anchor_from_datetime(cell.original_start),
                anchor_from_datetime(cell.original_end),
                anchor_from_datetime(cell.start),
                anchor_from_datetime(cell.end),
            )
            for cell in grid.cells
        ),
        grid.calendar_snapshot,
        civil_bounds=grid.civil_bounds,
    )
    frozen = freeze_calendar(
        coordinate,
        token="axis.0",
        name="period",
        key=DataColumn("key.0", "period", ScalarDataType("int64"), False),
    )
    value = DataColumn("value.0", "value", ScalarDataType("int64"), False)
    schema = DataSchema((frozen.axis,), (value,), value_paths=(("value",),))
    rows = pd.DataFrame(
        {
            "key.0": pd.Series(range(len(grid.cells)), dtype="int64[pyarrow]"),
            "value.0": pd.Series([7] * len(grid.cells), dtype="int64[pyarrow]"),
        }
    )
    return AnalysisFrame._from_memory(
        session._runtime.local,
        CompletedFrameResult(
            CompletedFrameData(schema, rows, (frozen.directory,)), result_metadata()
        ),
    )


def stored_frame(session: Session, artifact: AnalysisFrame) -> pd.DataFrame:
    store = session._runtime.store
    with store._read() as conn:
        manifest = terminal_store.manifest(store, conn, artifact.artifact.id)
    assert manifest == committed(artifact)
    return read_output(store.project_root, manifest.data.schema, manifest.data.rows)


@overload
def records(
    session: Session, artifact: AnalysisFrame, kind: Literal["evidence"] = "evidence"
) -> tuple[OutputEvidence, ...]: ...


@overload
def records(
    session: Session, artifact: AnalysisFrame, kind: Literal["finding"]
) -> tuple[OutputFinding, ...]: ...


def records(
    session: Session,
    artifact: AnalysisFrame,
    kind: Literal["finding", "evidence"] = "evidence",
) -> tuple[OutputFinding | OutputEvidence, ...]:
    items: list[OutputFinding | OutputEvidence] = []
    cursor = None
    while True:
        page, cursor = terminal_store.records_page(
            session._runtime.store, artifact.artifact.id, kind=kind, limit=1, cursor=cursor
        )
        items.extend(page)
        if cursor is None:
            return tuple(items)


def test_public_constant_executes_each_invocation_and_reads_committed_output(
    session: Session,
) -> None:
    query = constant(session)
    first, second = query.persist(), query.persist()
    assert first.artifact.id != second.artifact.id
    assert "AnalysisFrame" in repr(first) and "\n" not in repr(first)
    assert stored_frame(session, first)["value.0"].tolist() == [7]
    assert len(terminal_runs(session)) == 2
    assert all(run.lifecycle == "succeeded" for run in terminal_runs(session))
    assert (
        terminal_run(session, committed(first).run_id).dataset_input.result_id
        == committed(first).metadata.result_id
    )
    assert records(session, first, "finding") == ()
    assert all(item.kind != "read" for item in records(session, first))
    for artifact in (first, session.persisted_frame(first.artifact.id)):
        assert artifact.to_pandas()[("value", "answer")].tolist() == [7]
        artifact.show()
        assert artifact.artifact.findings().items == ()
        digest = artifact.artifact.evidence_digest()
        assert digest.digest_version == "frame-output-v4"
        assert not any(item.kind == "read" for item in digest.items)
    published = terminal_runs(session, status="succeeded")
    assert all(run.lifecycle == "succeeded" for run in published)
    assert {run.output_artifact_ref for run in published if run.lifecycle == "succeeded"} == {
        first.artifact.id,
        second.artifact.id,
    }
    assert all(
        committed(item).data.rows.receipt.realized_byte_count > 0 for item in (first, second)
    )
    assert not hasattr(first.artifact, "definition_fingerprint")
    assert session._catalog_value is None and session._sources_value is None


def test_storage_wide_complete_keys_exact_dtypes_and_nullable_values(tmp_path: Path) -> None:
    keys = (
        DataColumn("k0", "region", ScalarDataType("string"), True),
        DataColumn("k1", "customer", ScalarDataType("int64"), False),
    )
    values = (
        DataColumn("v0", "large integer", ScalarDataType("int64"), True),
        DataColumn("v1", "decimal", DecimalDataType(30, 8), True, "CNY"),
        DataColumn("v2", "timestamp", TimestampDataType("ns", "Asia/Shanghai"), True),
        DataColumn("v3", "duration", DurationDataType("ns"), True),
        DataColumn("v4", "day", ScalarDataType("date"), True),
        DataColumn("v5", "ratio", ScalarDataType("float64"), True),
    )
    presence = (DataColumn("p0", "present", ScalarDataType("boolean"), False),)
    schema = DataSchema(
        (
            DataAxis("region", (keys[0],), name="region"),
            DataAxis("customer", (keys[1],), name="customer"),
        ),
        values,
        presence,
        value_paths=tuple((v.name,) for v in values),
        presence_paths=(("present",),),
    )
    frame = pd.DataFrame(
        {
            "k0": pd.Series([None, "A", "A"], dtype="string[pyarrow]"),
            "k1": pd.Series([1, 2, 1], dtype="int64[pyarrow]"),
            "v0": pd.Series([9007199254740993, None, -(2**63)], dtype="int64[pyarrow]"),
            "v1": pd.Series(
                [Decimal("12345678901234567890.12345678"), None, Decimal("0.00000001")],
                dtype=pd.ArrowDtype(pa.decimal128(30, 8)),
            ),
            "v2": pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array(
                        [1700000000000000001, None, 1700000000000000002],
                        type=pa.timestamp("ns", "Asia/Shanghai"),
                    )
                )
            ),
            "v3": pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array([9007199254740993, None, -1], type=pa.duration("ns"))
                )
            ),
            "v4": pd.Series(
                [date(2026, 1, 1), None, date(2026, 1, 2)], dtype="date32[day][pyarrow]"
            ),
            "v5": pd.Series([None, 0.5, 0.0], dtype="double[pyarrow]"),
            "p0": pd.Series([True, False, True], dtype="bool[pyarrow]"),
        }
    )
    for data in (frame, frame.iloc[:0]):
        staging, final = tmp_path / f"staging-{len(data)}", tmp_path / f"saved-{len(data)}"
        handle = write_output(tmp_path, staging, final, schema, data)
        os.rename(staging, final)
        output = read_output(tmp_path, schema, handle)
        expected = data.reset_index(drop=True)
        pd.testing.assert_frame_equal(output, expected, check_dtype=False)
        assert tuple(output.columns) == tuple(column.slot_id for column in schema.columns)
        for column in schema.columns:
            assert isinstance(output[column.slot_id].dtype, pd.ArrowDtype)
        manifest = ArtifactManifest(
            "store", "artifact", "session", "run", ArtifactData(schema, handle), result_metadata()
        )
        from marivo.analysis.materialization.pandas_export import export_frame

        exported = export_frame(manifest.data.schema, output)
        assert exported.columns.names == ["kind", "name"]
        assert list(exported.index) == list(range(len(data)))
        for column in values:
            pd.testing.assert_series_equal(
                exported[("value", column.name)], expected[column.slot_id], check_names=False
            )
        assert exported[("presence", "present")].tolist() == expected["p0"].tolist()
        assert dict(exported.attrs["units"])["decimal"] == "CNY"

        assert decode(encode(manifest), ArtifactManifest) == manifest
    with pq.ParquetFile(tmp_path / "saved-3" / "data.parquet") as parquet:
        assert parquet.metadata.format_version == "2.6"
        assert parquet.metadata.row_group(0).column(0).compression == "ZSTD"


@pytest.mark.parametrize("count", (0, 1, 2))
def test_zero_axis_zero_value_unit_rows(tmp_path: Path, count: int) -> None:
    rows = pd.DataFrame(index=pd.RangeIndex(count))
    if count == 2:
        with pytest.raises(IntegrityError):
            write_output(
                tmp_path, tmp_path / "staging", tmp_path / "saved", DataSchema((), ()), rows
            )
        return
    handle = write_output(
        tmp_path, tmp_path / "staging", tmp_path / "saved", DataSchema((), ()), rows
    )
    os.rename(tmp_path / "staging", tmp_path / "saved")
    output = read_output(tmp_path, DataSchema((), ()), handle)
    assert output.shape == (count, 0)
    assert output.index.equals(pd.RangeIndex(count))
    from marivo.analysis.materialization.pandas_export import export_frame

    manifest = ArtifactManifest(
        "store",
        "artifact",
        "session",
        "run",
        ArtifactData(DataSchema((), ()), handle),
        result_metadata(),
    )
    exported = export_frame(manifest.data.schema, output)
    assert exported.shape == (count, 0)
    assert exported.columns.names == ["kind", "name"]
    assert exported.attrs["units"] == ()


def test_records_codec_and_transaction_are_self_contained(session: Session) -> None:
    store = session._runtime.store
    schema = DataSchema(
        (),
        (DataColumn("v0", "correlation", ScalarDataType("float64"), True),),
        value_paths=(("correlation",),),
    )
    finding = OutputFinding(
        "finding_exact",
        AssociationFindingValueV1(
            method="pearson",
            coefficient=0.5,
            input_observation_count=3,
            null_pair_count=0,
            complete_pair_count=3,
            lag=NoAssociationLagV1(),
        ),
        (OutputCoordinate("v0", Decimal("0.5")),),
    )
    evidence = tuple(
        OutputEvidence(
            "check",
            f"Check {i}",
            (
                ("result", "passed"),
                ("decimal", Decimal("1.00")),
                ("day", date(2026, 1, 1)),
                ("instant", datetime(2026, 1, 1, tzinfo=timezone.utc)),
            ),
        )
        for i in range(205)
    )
    assert decode(encode(finding), OutputFinding) == finding
    assert all(decode(encode(item), OutputEvidence) == item for item in evidence)
    saved_findings = tuple(replace(finding, finding_id=f"finding_{index}") for index in range(3))
    fixed = AnalysisFrame._from_memory(
        session._runtime.local,
        CompletedFrameResult(
            CompletedFrameData(schema, pd.DataFrame({"v0": [0.5]})),
            result_metadata(findings=saved_findings, checks=evidence),
        ),
    )
    artifact_handle = fixed.persist()
    artifact = artifact_handle.artifact.id
    assert records(session, artifact_handle, "finding") == saved_findings
    assert records(session, artifact_handle) == evidence
    public = session.persisted_frame(artifact)
    assert public.artifact.evidence_digest().items == evidence
    assert public.artifact.evidence_digest().evidence_count == 205
    page = public.artifact.findings(limit=1)
    assert page.items[0].value == finding.value
    assert page.items[0].coordinates == finding.coordinates
    assert not hasattr(page.items[0], "derivation")
    assert not hasattr(page.items[0], "subject")
    assert "coefficient: 0.5" in page.items[0].render()
    assert "v0" in page.items[0].render()
    page_two = public.artifact.findings(limit=1, cursor=page.next_cursor)
    page_three = public.artifact.findings(limit=1, cursor=page_two.next_cursor)
    assert [item.finding_id for item in (*page.items, *page_two.items, *page_three.items)] == [
        item.finding_id for item in saved_findings
    ]
    assert not page_three.has_more and page_three.next_cursor is None
    foreign = constant(session).persist()
    with pytest.raises(IntegrityError):
        foreign.artifact.findings(limit=1, cursor=page.next_cursor)
    public.artifact.evidence_digest().show()

    _, cursor = terminal_store.records_page(store, artifact, kind="evidence", limit=1)
    with pytest.raises(IntegrityError):
        terminal_store.records_page(store, artifact, kind="finding", cursor=cursor)
    with store._write() as conn:
        conn.execute(
            "UPDATE output_evidence SET body_payload=? WHERE artifact_ref=? AND evidence_ordinal=0",
            (encode(OutputEvidence("read", "altered")), artifact),
        )
    with pytest.raises(IntegrityError):
        terminal_store.records_page(store, artifact, kind="evidence")


@pytest.mark.parametrize(
    "point",
    (
        "publication_admitted",
        "terminal_primary_written",
        "terminal_method_written",
        "terminal_files_published",
        "insert_artifact",
        "insert_evidence",
        "insert_findings",
        "insert_terminal",
        "before_commit",
    ),
)
def test_faults_never_expose_partial_output_or_delete_prior_commit(
    session: Session, point: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = constant(session).persist()

    def fail(current: str) -> None:
        if current == point:
            raise OSError(point)

    monkeypatch.setattr(terminal_publication, "_event", fail)
    with pytest.raises(MaterializationError):
        AnalysisFrame._from_memory(session._runtime.local, method_result()).persist()
    monkeypatch.setattr(terminal_publication, "_event", lambda event: None)
    assert stored_frame(session, saved)["value.0"].tolist() == [7]
    assert {run.lifecycle for run in terminal_runs(session)} == {"succeeded", "failed"}
    assert session._runtime.store.resources(session.id) == ()
    with session._runtime.store._read() as conn:
        assert conn.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM dataset_evidence").fetchone()[0] == 1
    assert len(list(session._runtime.store.layout.session_dir(session.id).glob("artifacts/*"))) == 1


def test_lost_commit_ack_returns_original_without_reexecution(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def fail(point: str) -> None:
        calls.append(point)
        if point == "after_commit":
            raise OSError("lost acknowledgement")

    monkeypatch.setattr(terminal_publication, "_event", fail)
    artifact = AnalysisFrame._from_memory(session._runtime.local, method_result()).persist()
    assert artifact.method_facts == method_result().method_facts
    assert calls.count("publication_admitted") == 1 and calls.count("terminal_primary_written") == 1
    assert len(terminal_runs(session)) == 1
    assert stored_frame(session, artifact)["contribution"].tolist() == [60]


@pytest.fixture
def source_session(
    semantic_project_factory: _SemanticProjectFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(database)) as conn:
        conn.execute(
            "CREATE TABLE orders(id BIGINT, region VARCHAR, amount BIGINT, stamp TIMESTAMPTZ)"
        )
        conn.execute(
            "INSERT INTO orders VALUES (1,'A',9007199254740993,'2026-01-01 00:00:00+00'),(2,'A',NULL,'2026-01-02 00:00:00+00'),(3,NULL,5,'2026-01-03 00:00:00+00')"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse',path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
            "sales/models.py": """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders",datasource=ms.ref.datasource("warehouse"),source=md.table("orders"),primary_key=["id"])
region = ms.dimension_column(name="region",entity=orders,column="region")
amount = ms.measure_column(name="amount",entity=orders,column="amount",additivity=ms.additive_all(),unit="CNY")
stamp = ms.time_dimension_column(name="stamp",entity=orders,column="stamp",is_default=True)
finalized = ms.finalize()
a = finalized.resolve(amount)
@ms.metric()
def revenue():
    return a.sum()
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.create("source", timezone="UTC")


def test_real_source_repeat_execution_and_nullable_local_combination(
    source_session: Session,
) -> None:
    session = source_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    assert isinstance(amount, Deferred) and isinstance(region, Deferred)
    query = session.observe(
        {"total": amount.sum(), "mean": amount.mean(), "ratio": amount.sum() / (amount.sum() * 0)},
        by=(region,),
    )
    first = query.persist()
    frame = stored_frame(session, first)
    assert {
        (None if pd.isna(key) else key): value
        for key, value in zip(frame["key.0"], frame["value.0"], strict=True)
    } == {None: 5, "A": 9007199254740993}
    assert frame["value.2"].isna().all()
    facts = records(session, first)
    reads = [dict(item.values) for item in facts if item.kind == "read"]
    assert reads and all(
        item["period_start"] is None and item["period_end"] is None for item in reads
    )
    with duckdb.connect(str(session.project_root / "warehouse.duckdb")) as conn:
        conn.execute("UPDATE orders SET amount=10 WHERE id=1")
    second = query.persist()
    assert second.artifact.id != first.artifact.id
    updated = stored_frame(session, second)
    assert {
        (None if pd.isna(key) else key): value
        for key, value in zip(updated["key.0"], updated["value.0"], strict=True)
    } == {None: 5, "A": 10}
    pd.testing.assert_frame_equal(stored_frame(session, first), frame)
    assert records(session, second, "finding") == ()


def test_calendar_bounds_are_explicit_output_attachment(source_session: Session) -> None:
    session = source_session
    metric = session.catalog.metrics.get("sales.revenue")
    axis = session.time_scope(start="2026-01-01", end="2026-01-05").by(mv.grain("day"))
    artifact = session.observe({"revenue": metric}, by=(axis,)).persist()
    (attachment,) = committed(artifact).data.attachments
    calendar_axis = artifact._schema.axes[0]
    assert isinstance(calendar_axis, CalendarDataAxis)
    assert calendar_axis.frozen is not None
    assert attachment.name == calendar_axis.frozen.directory_name
    frame = read_output(session.project_root, attachment.schema, attachment.rows)
    assert len(frame) == 4 and frame["actual_start"].iloc[0] == pd.Timestamp("2026-01-01", tz="UTC")
    assert len(stored_frame(session, artifact)) == 4


@pytest.mark.parametrize("zone", ("Europe/Paris", "UTC+05:30"))
@pytest.mark.parametrize("from_source", (False, True))
def test_calendar_timezone_survives_private_cold_read(
    source_session: Session, zone: str, from_source: bool
) -> None:
    session = source_session
    axis = session.time_scope(start="2026-03-28", end="2026-03-31", timezone=zone).by(
        mv.grain("day")
    )
    if from_source:
        metric = session.catalog.metrics.get("sales.revenue")
        artifact = session.observe({"value": metric}, by=(axis,)).persist()
    else:
        artifact = calendar_constant(session, axis).persist()
    if not from_source:
        assert session._catalog_value is None
        assert not any(item.kind == "read" for item in records(session, artifact))
    script = """from pathlib import Path
import sys
import pandas as pd
from marivo.analysis.core.artifact_data import CalendarDataAxis, TimestampDataType
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization import terminal_store, terminal_publication
from marivo.analysis.materialization.terminal_storage import read_output
store = SessionStore.open_existing(Path(sys.argv[1]))
with store._read() as conn:
    manifest = terminal_store.manifest(store, conn, sys.argv[2])
attachment, = manifest.data.attachments
zone = sys.argv[3]
assert type(manifest.data.schema.axes[0]) is CalendarDataAxis
assert manifest.data.schema.axes[0].timezone == zone
assert attachment.schema.axes[0].kind == "field"
assert attachment.schema.axes[0].token == manifest.data.schema.axes[0].token
assert all(column.dtype == TimestampDataType('us', 'UTC') for column in attachment.schema.values
           if column.name in ('nominal_start', 'nominal_end', 'actual_start', 'actual_end'))
frame = read_output(store.project_root, attachment.schema, attachment.rows)
starts = pd.date_range('2026-03-28', periods=3, tz=zone).tolist()
ends = pd.date_range('2026-03-29', periods=3, tz=zone).tolist()
for name in ('nominal_start', 'actual_start'):
    assert frame[name].tolist() == starts
for name in ('nominal_end', 'actual_end'):
    assert frame[name].tolist() == ends
assert str(frame.actual_start.dtype.pyarrow_dtype.tz) == 'UTC'
assert [(end-start).total_seconds()/3600 for start,end in zip(starts,ends)] == (
    [24,23,24] if zone == 'Europe/Paris' else [24,24,24]
)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(session.project_root), artifact.artifact.id, zone],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_read_evidence_preserves_nested_predicate_groups(source_session: Session) -> None:
    session = source_session
    region = session.catalog.fields.get("sales.orders.region")
    amount = session.catalog.fields.get("sales.orders.amount")
    assert isinstance(region, Deferred) and isinstance(amount, Deferred)
    with duckdb.connect(str(session.project_root / "warehouse.duckdb")) as conn:
        conn.execute("UPDATE orders SET amount=10 WHERE id=1")
    predicates = (
        ((region == "A") | region.isnull()) & (amount < 10),
        (region == "A") | (region.isnull() & (amount < 10)),
        ~((region == "A") | region.isnull()) & (amount < 10),
    )
    expected_shapes = (
        (
            "and",
            "or",
            "equal",
            "end_call",
            "isnull",
            "end_call",
            "end_call",
            "less",
            "end_call",
            "end_call",
        ),
        (
            "or",
            "equal",
            "end_call",
            "and",
            "isnull",
            "end_call",
            "less",
            "end_call",
            "end_call",
            "end_call",
        ),
        (
            "and",
            "not",
            "or",
            "equal",
            "end_call",
            "isnull",
            "end_call",
            "end_call",
            "end_call",
            "less",
            "end_call",
            "end_call",
        ),
    )
    for predicate, expected, total in zip(predicates, expected_shapes, (5, 15, 0), strict=True):
        artifact = session.observe({"total": amount.sum()}, where=predicate).persist()
        assert stored_frame(session, artifact)["value.0"].tolist() == [total]
        facts = records(session, artifact)
        (scope,) = [dict(item.values)["filters"] for item in facts if item.kind == "read"]
        assert isinstance(scope, str)
        (captured_condition,) = json.loads(scope)
        (captured_field,) = captured_condition["inputs"]
        assert captured_condition["scope"] == captured_field["scope"] == "filter"
        operators = {"and", "or", "not", "equal", "isnull", "less", "end_call"}
        assert tuple(token for token in captured_field["tokens"] if token in operators) == expected
        assert "sales.orders.region" in scope and "sales.orders.amount" in scope


def test_old_store_rejection_preserves_bytes_and_name_cannot_bypass(tmp_path: Path) -> None:
    old = tmp_path / ".marivo/analysis/generations/v12/session_store.db"
    old.parent.mkdir(parents=True)
    with sqlite3.connect(old) as conn:
        conn.execute("PRAGMA user_version=12")
    before = old.read_bytes()
    for name in ("original", "renamed"):
        with pytest.raises(IntegrityError, match="user_version=12"):
            DatasetRuntime.create(tmp_path, name)
        with pytest.raises(IntegrityError, match="user_version=12"):
            SessionStore.open_existing(tmp_path)
    assert old.read_bytes() == before
    assert not (old.parent.parent / "v15").exists()


def test_private_cold_roundtrip_does_not_need_producer_or_catalog(
    session: Session, tmp_path: Path
) -> None:
    artifact = constant(session).persist()
    script = """from pathlib import Path
import sys
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization import terminal_store, terminal_publication
from marivo.analysis.materialization.terminal_storage import read_output
store=SessionStore.open_existing(Path(sys.argv[1]))
with store._read() as conn:
    manifest=terminal_store.manifest(store,conn,sys.argv[2])
assert read_output(store.project_root,manifest.data.schema,manifest.data.rows)["value.0"].tolist()==[7]
assert terminal_store.records_page(store,sys.argv[2],kind="finding")[0]==()
assert terminal_store.records_page(store,sys.argv[2],kind="evidence")[0]
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), artifact.artifact.id],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "point",
    (
        "publication_admitted",
        "terminal_primary_written",
        "terminal_method_written",
        "terminal_files_published",
        "insert_evidence",
        "before_commit",
        "after_commit",
    ),
)
def test_process_loss_reconciles_original_without_replay(session: Session, point: str) -> None:
    store = session._runtime.store
    saved = constant(session).persist()
    command = [
        sys.executable,
        "-m",
        "tests.analysis.materialization.terminal_recovery_worker",
        str(store.project_root),
        session.id,
        point,
    ]
    result = subprocess.run(
        command, cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True
    )
    assert result.returncode == 37, result.stderr
    with store._read() as conn:
        count_before = conn.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0]
    assert count_before == (2 if point == "after_commit" else 1)
    reopened = mv.session.resume(session.id)._runtime
    assert len(terminal_runs(reopened)) == 2
    assert terminal_runs(reopened)[0].lifecycle == (
        "succeeded" if point == "after_commit" else "failed"
    )
    assert reopened.store.resources(session.id) == ()
    assert stored_frame(session, saved)["value.0"].tolist() == [7]
    with store._read() as conn:
        assert conn.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == count_before


def test_process_writer_contention_does_not_admit_contender(session: Session) -> None:
    from marivo.analysis.materialization.writer_guard import session_writer_guard

    store = session._runtime.store
    command = [
        sys.executable,
        "-m",
        "tests.analysis.materialization.terminal_recovery_worker",
        str(store.project_root),
        session.id,
        "none",
    ]
    with session_writer_guard(store.layout.lock_path(session.id), session_ref=session.id):
        result = subprocess.run(
            command, cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True
        )
    assert result.returncode == 38, result.stderr
    assert terminal_runs(session) == ()
    result = subprocess.run(
        command, cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert len(terminal_runs(session)) == 1


def test_unknown_commit_keeps_files_and_journal_until_reconciliation(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = session._runtime.store
    original_read = store._read

    def fail(point: str) -> None:
        if point == "before_commit":

            def unavailable() -> NoReturn:
                raise OSError("read-back unavailable")

            monkeypatch.setattr(store, "_read", unavailable)
            raise OSError("commit outcome unknown")

    monkeypatch.setattr(terminal_publication, "_event", fail)
    with pytest.raises(RecoveryPendingError) as caught:
        constant(session).persist()
    assert caught.value.stage == "terminal_commit_unknown"
    monkeypatch.setattr(store, "_read", original_read)
    monkeypatch.setattr(terminal_publication, "_event", lambda event: None)
    assert store.resources(session.id)
    assert len(list(store.layout.session_dir(session.id).glob("artifacts/*"))) == 1
    reconcile(store, session.id, lambda point: None)
    assert store.resources(session.id) == ()
    assert terminal_runs(session)[0].lifecycle == "failed"


def test_obsolete_output_envelope_is_rejected_without_rewrite() -> None:
    from marivo.analysis.materialization.contracts import canonical_json

    text = canonical_json({"format": "marivo.graph/v10", "value": {}})
    with pytest.raises(IntegrityError):
        decode(text, ArtifactManifest)
    assert text == canonical_json({"format": "marivo.graph/v10", "value": {}})


def test_large_output_uses_bounded_row_groups(tmp_path: Path) -> None:
    schema = DataSchema(
        (DataAxis("key", (DataColumn("k0", "key", ScalarDataType("int64"), False),), name="key"),),
        (DataColumn("v0", "value", ScalarDataType("int64"), True),),
        value_paths=(("value",),),
    )
    count = POLICY.row_group_rows + 7
    rows = pd.DataFrame(
        {
            "k0": pd.Series(range(count), dtype="int64[pyarrow]"),
            "v0": pd.Series(range(count), dtype="int64[pyarrow]"),
        }
    )
    handle = write_output(tmp_path, tmp_path / "staging", tmp_path / "saved", schema, rows)
    os.rename(tmp_path / "staging", tmp_path / "saved")
    with pq.ParquetFile(tmp_path / "saved/data.parquet") as parquet:
        assert parquet.metadata.num_row_groups == 2
        assert parquet.metadata.row_group(0).num_rows == POLICY.row_group_rows
    pd.testing.assert_frame_equal(read_output(tmp_path, schema, handle), rows)


def test_pure_source_publication_has_no_synthetic_local_check(source_session: Session) -> None:
    metric = source_session.catalog.metrics.get("sales.revenue")
    artifact = source_session.observe({"revenue": metric}).persist()
    assert stored_frame(source_session, artifact)["value.0"].tolist() == [9007199254740998]
    facts = records(source_session, artifact)
    assert sum(item.kind == "read" for item in facts) == 1
    assert not any("Local numeric" in item.summary for item in facts)
    assert all(dict(item.values)["source"] for item in facts if item.kind == "read")


def test_explicit_numeric_error_prevents_admission_and_publication(
    source_session: Session,
) -> None:
    amount = source_session.catalog.fields.get("sales.orders.amount")
    assert isinstance(amount, Deferred)
    total = amount.sum()
    with pytest.raises(MaterializationError) as caught:
        source_session.observe(
            {"ratio": total.divide(total * 0, zero_denominator="error")}
        ).persist()
    from marivo.semantic.errors import SemanticExpressionError

    assert isinstance(caught.value.__cause__, SemanticExpressionError)
    assert caught.value.stage == "numeric_value"
    assert caught.value.repair is not None
    assert caught.value.repair.help_target.canonical_id == "analysis_frame.compute"
    assert not hasattr(caught.value, "run_ref")
    assert terminal_runs(source_session) == ()
    assert source_session._runtime.store.resources(source_session.id) == ()
    with source_session._runtime.store._read() as conn:
        assert conn.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 0


def test_preparation_failure_creates_no_run(source_session: Session) -> None:
    region = source_session.catalog.fields.get("sales.orders.region")
    metric = source_session.catalog.metrics.get("sales.revenue")
    with duckdb.connect(str(source_session.project_root / "warehouse.duckdb")) as conn:
        conn.execute("ALTER TABLE orders ALTER region TYPE UBIGINT USING 1")
    with pytest.raises(MaterializationError) as caught:
        source_session.observe({"revenue": metric}, by=(region,)).persist()
    assert not hasattr(caught.value, "run_ref")
    assert terminal_runs(source_session) == ()
    assert source_session._runtime.store.resources(source_session.id) == ()


def test_source_disconnect_failure_prevents_publication(
    source_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource._lifetime import BackendLease

    original = BackendLease.close

    def unconfirmed(lease: BackendLease, *, suppress_errors: bool = True) -> bool:
        assert original(lease, suppress_errors=suppress_errors)
        return False

    monkeypatch.setattr(BackendLease, "close", unconfirmed)
    metric = source_session.catalog.metrics.get("sales.revenue")
    with pytest.raises(MaterializationError) as caught:
        source_session.observe({"revenue": metric}).persist()
    assert not hasattr(caught.value, "run_ref")
    assert terminal_runs(source_session) == ()
    assert source_session._runtime.store.resources(source_session.id) == ()
    with source_session._runtime.store._read() as conn:
        assert conn.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 0


def test_lost_ack_with_missing_records_is_recovery_pending(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = session._runtime.store
    fixed = constant(session).compute()
    assert isinstance(fixed._backing, MemoryFrameBacking)
    assert fixed._backing.metadata.audit.evaluation.checks

    def corrupt(point: str) -> None:
        if point == "after_commit":
            with store._write() as conn:
                conn.execute("DELETE FROM output_evidence")
            raise OSError("lost acknowledgement with contradictory records")

    monkeypatch.setattr(terminal_publication, "_event", corrupt)
    with pytest.raises(RecoveryPendingError):
        fixed.persist()
    assert terminal_runs(session)[0].lifecycle == "succeeded"
    assert len(list(store.layout.session_dir(session.id).glob("artifacts/*"))) == 1


@pytest.mark.parametrize("unit", ("s", "ms", "us", "ns"))
def test_time_units_roundtrip_exactly(tmp_path: Path, unit: Literal["s", "ms", "us", "ns"]) -> None:
    schema = DataSchema(
        (),
        (
            DataColumn("v0", "instant", TimestampDataType(unit, "UTC"), True),
            DataColumn("v1", "duration", DurationDataType(unit), True),
        ),
        value_paths=(("instant",), ("duration",)),
    )
    rows = pd.DataFrame(
        {
            "v0": pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array([1700000001], type=pa.timestamp(unit, "UTC"))
                )
            ),
            "v1": pd.Series(
                pd.arrays.ArrowExtensionArray(pa.array([9007199254740993], type=pa.duration(unit)))
            ),
        }
    )
    handle = write_output(tmp_path, tmp_path / "staging", tmp_path / "saved", schema, rows)
    os.rename(tmp_path / "staging", tmp_path / "saved")
    pd.testing.assert_frame_equal(read_output(tmp_path, schema, handle), rows)


def test_metadata_and_records_need_no_parquet_or_current_model(
    source_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo.datasource.adapters import SourceSession

    session = source_session
    amount = session.catalog.fields.get("sales.orders.amount")
    artifact = session.observe({"total": amount.sum()}).persist()
    before = terminal_runs(session)

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("metadata and record reads must not consume Parquet or current semantics")

    monkeypatch.setattr(pq, "ParquetFile", forbidden)
    monkeypatch.setattr(Session, "catalog", property(forbidden))
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    reopened = session.persisted_frame(artifact.artifact.id)
    assert reopened.artifact.id == artifact.artifact.id and "AnalysisFrame" in repr(reopened)
    reopened.show(n=0)
    assert "rows" in capsys.readouterr().out
    assert reopened.artifact.findings().items == ()
    facts = reopened.artifact.evidence_digest().items
    assert any(item.kind == "read" for item in facts)
    publishing = terminal_run(session, committed(artifact).run_id)
    assert publishing.lifecycle == "succeeded"
    assert publishing.output_artifact_ref == artifact.artifact.id
    assert session.id in session.render()
    assert terminal_run(session, committed(artifact).run_id).lifecycle == "succeeded"
    assert terminal_runs(session) == before


def test_public_read_isolated_copy_and_historical_values(source_session: Session) -> None:
    session = source_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    artifact = session.observe(
        {"total": amount.sum(), "mean": amount.mean()}, by=(region,)
    ).persist()
    before = artifact.to_pandas()
    assert {
        (None if pd.isna(key) else key): value
        for key, value in zip(
            before[("coordinate", "axis.0")], before[("value", "total")], strict=True
        )
    } == {None: 5, "A": 9007199254740993}
    assert before.attrs["axes"][0]["role"] == "field"
    changed = artifact.to_pandas()
    changed.iloc[0, 1] = 0
    changed.attrs["axes"][0]["name"] = "changed"
    changed.attrs["axes"][0]["descriptor"]["columns"][0]["dtype"]["name"] = "changed"
    pd.testing.assert_frame_equal(session.persisted_frame(artifact.artifact.id).to_pandas(), before)
    assert session.persisted_frame(artifact.artifact.id).to_pandas().attrs == before.attrs


def test_preview_decodes_only_requested_rows_and_skips_unused_parts(
    session: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from marivo.analysis.materialization.graph_reading import FrameReader, OutputRead

    count = POLICY.row_group_rows + 9
    key = DataColumn("key.0", "group", ScalarDataType("int64"), False)
    value = DataColumn("value.0", "amount", ScalarDataType("int64"), False)
    schema = DataSchema(
        (DataAxis("axis.0", (key,), name="group"),), (value,), value_paths=(("amount",),)
    )
    rows = pd.DataFrame(
        {
            "key.0": pd.Series(range(count), dtype="int64[pyarrow]"),
            "value.0": pd.Series(range(count), dtype="int64[pyarrow]"),
        }
    )
    artifact = AnalysisFrame._from_memory(
        session._runtime.local,
        CompletedFrameResult(CompletedFrameData(schema, rows), result_metadata()),
    ).persist()
    sizes: list[int] = []
    open_parquet = OutputRead._open

    class TrackedParquet:
        def __init__(self, parquet: pq.ParquetFile) -> None:
            self.parquet = parquet
            self.num_row_groups = parquet.num_row_groups
            self.reader = parquet.reader

        def __enter__(self) -> TrackedParquet:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            self.parquet.close()

        def iter_batches(
            self, *, batch_size: int, row_groups: list[int]
        ) -> Iterator[pa.RecordBatch]:
            for batch in self.parquet.iter_batches(batch_size=batch_size, row_groups=row_groups):
                sizes.append(batch.num_rows)
                yield batch

    monkeypatch.setattr(
        OutputRead, "_open", lambda self, layout: TrackedParquet(open_parquet(self, layout))
    )
    assert isinstance(artifact._backing, StoredFrameBacking)
    for requested in (0, 20, POLICY.batch_rows + 1, POLICY.row_group_rows + 1):
        prefix = FrameReader(session._runtime.store, session.id).preview(
            artifact._backing, requested
        )
        assert len(prefix.rows) == requested
        assert sum(sizes) == requested and all(size <= POLICY.batch_rows for size in sizes)
        sizes.clear()
    artifact.show(n=20)
    assert "amount" in capsys.readouterr().out
    sizes.clear()
    frame = session.persisted_frame(artifact.artifact.id).to_pandas()
    assert len(frame) == count and sum(sizes) == count
    assert frame[("value", "amount")].iloc[-1] == count - 1
    primary = (
        session.project_root
        / committed(artifact).data.rows.receipt.project_relative_path
        / "data.parquet"
    )
    with primary.open("r+b") as stream:
        stream.seek(-5, os.SEEK_END)
        stream.write(b"!")
    with pytest.raises(IntegrityError):
        artifact.to_pandas()
    assert artifact.artifact.findings().items == ()
    artifact.artifact.evidence_digest()
    # Unused attachments are no longer legal: exact attachment closure is tested
    # by Store15 protocol/frozen-coordinate tests, rather than retaining a bypass.


def test_wide_artifact_truncation_routes_to_callable_reads(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    columns = {f"quantity_{i}_" + "x" * 150: None for i in range(60)}
    values = {name: i for i, name in enumerate(columns)}
    artifact = session.values(by=(), columns=columns, rows=(((), values),)).persist()
    artifact.show()
    text = capsys.readouterr().out
    assert "omitted" in text or "truncated" in text
    assert "max_output_bytes" not in text
    assert ".to_pandas()" in text and ".artifact" in text
    assert len(text.encode()) <= 8193
    assert artifact.to_pandas().shape == (1, 60)
    artifact.artifact.evidence_digest()


@pytest.mark.parametrize("zone", ("Europe/Paris", "UTC+05:30"))
def test_public_calendar_uses_saved_instants_and_boundaries(
    session: Session, zone: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization.graph_reading import OutputRead

    axis = session.time_scope(start="2026-03-28T06:00", end="2026-03-31", timezone=zone).by(
        mv.grain("day")
    )
    artifact = calendar_constant(session, axis).persist()
    exported = session.persisted_frame(artifact.artifact.id).to_pandas()
    (description,) = exported.attrs["axes"]
    assert description["role"] == "calendar" and description["descriptor"]["timezone"] == zone
    (part,) = exported.attrs["frozen_coordinates"]
    bounds = part["rows"]
    assert len(bounds) == 3
    assert exported[("coordinate", "axis.0")].tolist() == [0, 1, 2]
    assert bounds["nominal_start"].tolist() == [
        pd.Timestamp(cell.original_start) for cell in axis.grid.cells
    ]
    assert bounds["actual_start"].iloc[0] > bounds["nominal_start"].iloc[0]
    original, consumed = OutputRead.select, []

    def tracked(self: OutputRead, layout: ReadLayout, keys: set[FrozenKey]) -> pd.DataFrame:
        result = original(self, layout, keys)
        consumed.append(len(result))
        return result

    monkeypatch.setattr(OutputRead, "select", tracked)
    artifact.show(n=1)
    assert consumed == [1]


def test_public_produce_and_cold_read_are_independent_processes(source_session: Session) -> None:
    import shutil

    root = source_session.project_root
    worktree = Path(__file__).resolve().parents[3]
    produced = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.analysis.materialization.terminal_reading_worker",
            str(root),
            "public-cold",
            "produce",
        ],
        cwd=worktree,
        text=True,
        capture_output=True,
    )
    assert produced.returncode == 0, produced.stdout + produced.stderr
    session_id, artifact_id = produced.stdout.strip().splitlines()[-1].split()
    (root / "warehouse.duckdb").unlink()
    shutil.rmtree(root / "models")
    reopened = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.analysis.materialization.terminal_reading_worker",
            str(root),
            session_id,
            "read",
            artifact_id,
        ],
        cwd=worktree,
        text=True,
        capture_output=True,
    )
    assert reopened.returncode == 0, reopened.stdout + reopened.stderr
    assert "public-cold-ok" in reopened.stdout


def test_period_comparison_endpoints_and_report_cold_recovery(source_session: Session) -> None:
    import shutil

    root = source_session.project_root
    worktree = Path(__file__).resolve().parents[3]
    produced = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.analysis.materialization.terminal_reading_worker",
            str(root),
            "period-cold",
            "period-produce",
        ],
        cwd=worktree,
        text=True,
        capture_output=True,
    )
    assert produced.returncode == 0, produced.stdout + produced.stderr
    session_id, *identities = produced.stdout.strip().splitlines()[-1].split()
    assert len(identities) == 3
    (root / "warehouse.duckdb").unlink()
    shutil.rmtree(root / "models")
    reopened = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.analysis.materialization.terminal_reading_worker",
            str(root),
            session_id,
            "period-read",
            *identities,
        ],
        cwd=worktree,
        text=True,
        capture_output=True,
    )
    assert reopened.returncode == 0, reopened.stdout + reopened.stderr
    assert "period-cold-ok" in reopened.stdout


def test_open_missing_and_foreign_ids(session: Session) -> None:
    from marivo.analysis.errors import ArtifactNotFoundError

    artifact = constant(session).persist()
    other = mv.session.create("another", timezone="UTC")
    with pytest.raises(ArtifactNotFoundError):
        other.persisted_frame(artifact.artifact.id)
    with pytest.raises(ArtifactNotFoundError):
        session.persisted_frame("missing")


def test_composite_coordinates_remain_one_ordered_tuple(tmp_path: Path) -> None:
    from marivo.analysis.materialization.pandas_export import export_frame

    keys = (
        DataColumn("a", "account", ScalarDataType("int64"), False),
        DataColumn("b", "region", ScalarDataType("string"), True),
    )
    schema = DataSchema((DataAxis("axis.0", keys, name="customer"),), ())
    rows = pd.DataFrame(
        {
            "a": pd.Series([9007199254740993, 9007199254740993], dtype="int64[pyarrow]"),
            "b": pd.Series(["A", None], dtype="string[pyarrow]"),
        }
    )
    handle = write_output(tmp_path, tmp_path / "staging", tmp_path / "saved", schema, rows)
    os.rename(tmp_path / "staging", tmp_path / "saved")
    manifest = ArtifactManifest(
        "store", "artifact", "session", "run", ArtifactData(schema, handle), result_metadata()
    )
    exported = export_frame(schema, read_output(tmp_path, schema, handle))
    assert exported[("coordinate", "axis.0")].tolist() == [
        (9007199254740993, "A"),
        (9007199254740993, None),
    ]
    assert len(exported.attrs["axes"][0]["descriptor"]["columns"]) == 2


def test_operator_sequence_survives_real_source_finish_and_storage(source_session: Session) -> None:
    from marivo.analysis.dsl.column import row_count

    session = source_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    source = session.observe({"total": amount.sum()}, by=(region,))
    ranked = source.top(2, by=source["total"])
    filtered = ranked.where(ranked["total"] > 0)
    selected = filtered.select({"amount": filtered["total"] * 2})
    fixed = selected.compute()
    expected = fixed.to_pandas()
    assert expected[("coordinate", "axis.0")].tolist() == ["A", pd.NA]
    assert expected[("value", "amount")].tolist() == [18014398509481986, 10]
    pd.testing.assert_frame_equal(fixed.persist().to_pandas(), expected)
    right = fixed.select({"amount": fixed["amount"] / 2}).compute()
    for operation in ("align", "compare"):
        combined = (
            fixed.align(right, on=((fixed.axes[0], right.axes[0]),))
            if operation == "align"
            else fixed.compare(
                {"reference": right}, on={"reference": ((fixed.axes[0], right.axes[0]),)}
            )
        ).compute()
        assert combined.to_pandas()[("coordinate", "axis.0")].tolist() == ["A", pd.NA]
        pd.testing.assert_frame_equal(combined.persist().to_pandas(), combined.to_pandas())
    reduced = fixed.reduce(
        {"rows": row_count(), "total": fixed["amount"].sum()}, by=(fixed.axes[0],)
    ).compute()
    assert reduced.to_pandas()[("coordinate", "axis.0")].tolist() == [pd.NA, "A"]
    assert reduced.to_pandas()[("value", "rows")].tolist() == [1, 1]
    assert reduced.to_pandas()[("value", "total")].tolist() == [10, 18014398509481986]
    saved = reduced.persist()
    pd.testing.assert_frame_equal(saved.to_pandas(), reduced.to_pandas())
    script = """
import sys
from pathlib import Path
from unittest.mock import patch
import pyarrow as pa
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.datasource.adapters import SourceSession

def forbidden(*args, **kwargs):
    raise AssertionError('cold reduce reopened source')
local = LocalFrameServices(SessionStore.open_existing(Path(sys.argv[1])), sys.argv[2])
with patch.object(SourceSession, '__init__', forbidden):
    data = local.read_complete(local.open_frame(sys.argv[3])).data
    rows = data.copy_rows()
    assert pa.array(rows[data.schema.key_fields[0]]).to_pylist() == [None, 'A']
    assert rows[data.schema.values[0].slot_id].tolist() == [1, 1]
    assert rows[data.schema.values[1].slot_id].tolist() == [10, 18014398509481986]
print('cold-reducer-exact')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(session.project_root), session.id, saved.artifact.id],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cold-reducer-exact" in result.stdout


@pytest.mark.parametrize("empty", (False, True))
def test_method_payload_is_owned_by_same_commit_and_zero_rows_are_checked(
    session: Session, empty: bool
) -> None:
    from marivo.analysis.materialization.frame_method_facts import (
        decode_method_payload,
        method_payload,
    )
    from marivo.analysis.materialization.graph_reading import ExportRead, OutputRead, ReadLayout

    result = method_result(empty=empty)
    saved = AnalysisFrame._from_memory(session._runtime.local, result).persist()
    value = committed(saved)
    assert value.method_facts == saved._backing.method_facts
    assert value.semantics_version == "frame.v4"
    descriptor = value.method_facts[0]
    assert descriptor.parent_count == (0 if empty else 1)
    assert descriptor.coordinate_count == descriptor.coordinate_row_count == 0
    assert descriptor.payload.rows.receipt.realized_row_count == (0 if empty else 1)
    assert descriptor.payload.rows.receipt.project_relative_path.startswith(
        session._runtime.store.layout.relative_path(
            session._runtime.store.layout.artifact_dir(session.id, saved.artifact.id)
        )
        + "/method-0"
    )
    rows = OutputRead(session._runtime.store.project_root).read(
        ReadLayout(descriptor.payload.schema, descriptor.payload.rows), ExportRead()
    )
    assert decode_method_payload(descriptor, rows) == result.method_facts[0]
    pd.testing.assert_frame_equal(
        rows, method_payload(result.method_facts[0]).rows, check_dtype=False
    )
    assert saved.method_facts == result.method_facts
    payload = json.loads(encode(value))
    method = payload["value"]["fields"]["method_facts"][0]["fields"]
    assert set(method) == {
        "kind",
        "origin_result_id",
        "output_schema",
        "parent_axis_count",
        "parent_count",
        "coordinate_count",
        "coordinate_row_count",
        "facts_digest",
        "payload",
    }
    assert "parents" not in method and "coordinates" not in method
    # ExportRead validates even the legitimate zero-row method file.
    path = (
        session._runtime.store.project_root / descriptor.payload.rows.receipt.project_relative_path
    )
    (path / "data.parquet").unlink()
    assert saved.to_pandas().shape[0] == (0 if empty else 1)
    with pytest.raises((IntegrityError, MaterializationError)):
        _ = saved.method_facts


@pytest.mark.parametrize("damage", ("entry", "kind", "record", "digest", "counts"))
def test_owned_method_codec_rejects_exact_table_damage(session: Session, damage: str) -> None:
    from marivo.analysis.materialization.frame_method_facts import (
        decode_method_payload,
        method_payload,
    )

    result = method_result()
    saved = AnalysisFrame._from_memory(session._runtime.local, result).persist()
    descriptor = committed(saved).method_facts[0]
    rows = method_payload(result.method_facts[0]).rows.copy()
    if damage == "entry":
        rows.loc[0, "entry"] = 1
    elif damage == "kind":
        rows.loc[0, "kind"] = "coordinate"
    elif damage == "record":
        wire = json.loads(str(rows.loc[0, "record"]))
        wire["value"]["fields"]["unknown"] = None
        from marivo.analysis.materialization.contracts import canonical_json

        rows.loc[0, "record"] = canonical_json(wire)
    elif damage == "digest":
        descriptor = replace(descriptor, facts_digest="0" * 64)
    else:
        rows = rows.iloc[:0]
    with pytest.raises(IntegrityError):
        decode_method_payload(descriptor, rows)


def test_malformed_method_numeric_facts_fail_before_admission(session: Session) -> None:
    result = method_result()
    facts = result.method_facts[0]
    parent = facts.parents[0]
    metric = parent.metrics[0]
    wrong = replace(metric.method, change=replace(metric.method.change, value=59))
    invalid_facts = replace(
        facts, parents=(replace(parent, metrics=(replace(metric, method=wrong),)),)
    )
    frame = AnalysisFrame._from_memory(
        session._runtime.local, replace(result, method_facts=(invalid_facts,))
    )
    with pytest.raises(AnalysisError, match="saved method totals"):
        frame.persist()
    assert terminal_runs(session) == ()
    assert session._runtime.store.resources(session.id) == ()


def test_manifest_rejects_foreign_duplicate_internal_producer(session: Session) -> None:
    saved = constant(session).persist()
    store = session._runtime.store
    value = committed(saved)
    foreign = mv.session.create("foreign-producer", timezone="UTC")
    terminal_store.admit(store, foreign.id, "duplicate_producer", value.result_id)
    # Corrupt the Store outside its foreign-key-enforcing writer to test read validation.
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO analysis_action_run_terminals VALUES(?,?,?,?,?,?)",
            (
                "duplicate_producer",
                foreign.id,
                "succeeded",
                "2099-01-01T00:00:00+00:00",
                value.artifact_id,
                None,
            ),
        )
    with pytest.raises(IntegrityError, match="duplicate producer"):
        session.persisted_frame(value.artifact_id)
