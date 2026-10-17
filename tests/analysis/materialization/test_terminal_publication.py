"""R2.5 terminal output codecs, publication visibility and recovery."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

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
from marivo.analysis.dsl.artifact import Artifact
from marivo.analysis.errors import AnalysisStageUnavailableError
from marivo.analysis.evidence._dataset_types import AssociationFindingValueV1, NoAssociationLagV1
from marivo.analysis.materialization import terminal_store
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
    RecoveryPendingError,
)
from marivo.analysis.materialization.resources import reserve_output
from marivo.analysis.materialization.store import SessionStore, _new_run_ref
from marivo.analysis.materialization.terminal_protocol import decode, encode
from marivo.analysis.materialization.terminal_publication import reconcile
from marivo.analysis.materialization.terminal_storage import POLICY, read_output, write_output
from marivo.analysis.session.core import Session
from tests.shared_fixtures import numeric_literal_node, numeric_program_query


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("terminal", report_timezone="UTC")


def constant(session: Session, value: int = 7):
    return numeric_program_query(session, {"answer": numeric_literal_node(value)})


def stored_frame(session: Session, artifact: Artifact) -> pd.DataFrame:
    store = session._runtime.store
    with store._read() as conn:
        manifest = terminal_store.manifest(store, conn, artifact.id)
    assert manifest == artifact._manifest
    return read_output(store.project_root, manifest.data.schema, manifest.data.rows)


def records(session: Session, artifact: Artifact, kind="evidence"):
    items = []
    cursor = None
    while True:
        page, cursor = terminal_store.records_page(
            session._runtime.store, artifact.id, kind=kind, limit=1, cursor=cursor
        )
        items.extend(page)
        if cursor is None:
            return tuple(items)


def test_public_constant_executes_each_invocation_and_reads_committed_output(session: Session):
    query = constant(session)
    first, second = query.execute(), query.execute()
    assert first.id != second.id
    assert "Artifact" in repr(first) and "\n" not in repr(first)
    assert stored_frame(session, first)["value.0"].tolist() == [7]
    assert len(session.runs().items) == 2
    assert all(run.lifecycle == "succeeded" for run in session.runs().items)
    assert session.get_run(first._manifest.run_id).dataset_input.kind == "query"
    assert records(session, first, "finding") == ()
    assert all(item.kind != "read" for item in records(session, first))
    for artifact in (first, session.artifact(first.id)):
        assert artifact.to_pandas()[("value", "answer")].tolist() == [7]
        artifact.show()
        assert artifact.findings().items == ()
        assert not any(item.kind == "read" for item in artifact.evidence_digest().items)
    graph = session.graph()
    assert len(graph.artifacts) == 2 and len(graph.edges) == 2
    assert all(item.family_id == "terminal_output" for item in graph.artifacts)
    assert not hasattr(graph.artifacts[0], "definition_fingerprint")
    assert session._catalog_value is None and session._sources_value is None


def test_storage_wide_complete_keys_exact_dtypes_and_nullable_values(tmp_path: Path):
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
        (DataAxis("region", (keys[0],)), DataAxis("customer", (keys[1],))), values, presence
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
        expected = data.sort_values(["k0", "k1"], na_position="last", kind="stable").reset_index(
            drop=True
        )
        pd.testing.assert_frame_equal(output, expected, check_dtype=False)
        assert tuple(output.columns) == tuple(column.slot_id for column in schema.columns)
        for column in schema.columns:
            assert isinstance(output[column.slot_id].dtype, pd.ArrowDtype)
        manifest = ArtifactManifest("artifact", "session", "run", ArtifactData(schema, handle))
        from marivo.analysis.materialization.graph_reading import ExportRead, OutputRead
        from marivo.analysis.materialization.pandas_export import export_frame

        exported = export_frame(manifest, OutputRead(tmp_path), ExportRead())
        assert exported.columns.names == ["kind", "name"]
        assert list(exported.index) == list(range(len(data)))
        for column in values:
            pd.testing.assert_series_equal(
                exported[("value", column.name)], expected[column.slot_id], check_names=False
            )
        assert exported[("presence", ("present",))].tolist() == expected["p0"].tolist()
        assert exported.attrs["marivo"]["quantities"][1]["unit"] == "CNY"

        assert decode(encode(manifest), ArtifactManifest) == manifest
    with pq.ParquetFile(tmp_path / "saved-3" / "data.parquet") as parquet:
        assert parquet.metadata.format_version == "2.6"
        assert parquet.metadata.row_group(0).column(0).compression == "ZSTD"


@pytest.mark.parametrize("count", (0, 1, 2))
def test_zero_axis_zero_value_unit_rows(tmp_path: Path, count: int):
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
    from marivo.analysis.materialization.graph_reading import ExportRead, OutputRead
    from marivo.analysis.materialization.pandas_export import export_frame

    manifest = ArtifactManifest(
        "artifact", "session", "run", ArtifactData(DataSchema((), ()), handle)
    )
    exported = export_frame(manifest, OutputRead(tmp_path), ExportRead())
    assert exported.shape == (count, 0)
    assert exported.columns.names == ["kind", "name"]
    assert exported.attrs["marivo"]["quantities"] == ()


def test_records_codec_and_transaction_are_self_contained(session: Session):
    store = session._runtime.store
    ref = _new_run_ref()
    terminal_store.admit(store, session.id, ref)
    nonce = "a" * 32
    artifact = "artifact_" + nonce
    staging, final, resources = reserve_output(
        store, run_ref=ref, session_ref=session.id, artifact_ref=artifact, nonce=nonce
    )
    schema = DataSchema((), (DataColumn("v0", "correlation", ScalarDataType("float64"), True),))
    primary = write_output(
        store.project_root,
        staging / "primary",
        final / "primary",
        schema,
        pd.DataFrame({"v0": [0.5]}),
    )
    final.parent.mkdir(parents=True, exist_ok=True)
    os.rename(staging, final)
    manifest = ArtifactManifest(artifact, session.id, ref, ArtifactData(schema, primary))
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
    terminal_store.publish(store, manifest, saved_findings, evidence, resources, lambda point: None)
    from marivo.analysis.materialization.graph_reading import TerminalReader

    artifact_handle = Artifact._from_manifest(manifest, TerminalReader(store))
    assert records(session, artifact_handle, "finding") == saved_findings
    assert records(session, artifact_handle) == evidence
    public = session.artifact(artifact)
    assert public.evidence_digest().items == evidence
    assert public.evidence_digest().evidence_count == 205
    page = public.findings(limit=1)
    assert page.items[0].value == finding.value
    assert page.items[0].coordinates == finding.coordinates
    assert not hasattr(page.items[0], "derivation")
    assert not hasattr(page.items[0], "subject")
    assert "coefficient: 0.5" in page.items[0].render()
    assert "v0" in page.items[0].render()
    page_two = public.findings(limit=1, cursor=page.next_cursor)
    page_three = public.findings(limit=1, cursor=page_two.next_cursor)
    assert [item.finding_id for item in (*page.items, *page_two.items, *page_three.items)] == [
        item.finding_id for item in saved_findings
    ]
    assert not page_three.has_more and page_three.next_cursor is None
    foreign = constant(session).execute()
    with pytest.raises(IntegrityError):
        foreign.findings(limit=1, cursor=page.next_cursor)
    public.evidence_digest().show()

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
        "query_admitted",
        "terminal_primary_written",
        "terminal_files_published",
        "insert_artifact",
        "insert_evidence",
        "insert_findings",
        "insert_terminal",
        "before_commit",
    ),
)
def test_faults_never_expose_partial_output_or_delete_prior_commit(session: Session, point: str):
    saved = constant(session).execute()

    def fail(current):
        if current == point:
            raise OSError(point)

    session._runtime._hook = fail
    with pytest.raises(MaterializationError):
        constant(session, 8).execute()
    session._runtime._hook = None
    assert stored_frame(session, saved)["value.0"].tolist() == [7]
    assert {run.lifecycle for run in session.runs().items} == {"succeeded", "failed"}
    assert session._runtime.store.resources(session.id) == ()
    with session._runtime.store._read() as conn:
        assert conn.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM dataset_evidence").fetchone()[0] == 1
    assert len(list(session._runtime.store.layout.session_dir(session.id).glob("artifacts/*"))) == 1


def test_lost_commit_ack_returns_original_without_reexecution(session: Session):
    calls = []

    def fail(point):
        calls.append(point)
        if point == "after_commit":
            raise OSError("lost acknowledgement")

    session._runtime._hook = fail
    artifact = constant(session).execute()
    assert calls.count("query_admitted") == 1 and calls.count("terminal_primary_written") == 1
    assert len(session.runs().items) == 1
    assert stored_frame(session, artifact)["value.0"].tolist() == [7]


@pytest.fixture
def source_session(semantic_project_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
stamp = ms.time_dimension_column(name="stamp",entity=orders,column="stamp",granularity="day",is_default=True)
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
    return mv.session.get_or_create("source", report_timezone="UTC")


def test_real_source_repeat_execution_and_nullable_local_combination(source_session: Session):
    session = source_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    assert isinstance(amount, Deferred) and isinstance(region, Deferred)
    query = session.observe(
        {"total": amount.sum(), "mean": amount.mean(), "ratio": amount.sum() / (amount.sum() * 0)},
        by=(region,),
    )
    first = query.execute()
    frame = stored_frame(session, first)
    assert frame["value.0"].tolist() == [9007199254740993, 5]
    assert frame["key.0"].tolist() == ["A", pd.NA]
    assert frame["value.2"].isna().all()
    facts = records(session, first)
    reads = [dict(item.values) for item in facts if item.kind == "read"]
    assert reads and all(item["time_constraint"] == "none added" for item in reads)
    with duckdb.connect(str(session.project_root / "warehouse.duckdb")) as conn:
        conn.execute("UPDATE orders SET amount=10 WHERE id=1")
    second = query.execute()
    assert second.id != first.id
    assert stored_frame(session, second)["value.0"].tolist() == [10, 5]
    assert stored_frame(session, first)["value.0"].tolist() == [9007199254740993, 5]
    assert records(session, second, "finding") == ()


def test_calendar_bounds_are_explicit_output_attachment(source_session: Session):
    session = source_session
    metric = session.catalog.metrics.get("sales.revenue")
    axis = session.time_scope(start="2026-01-01", end="2026-01-05").by(mv.grain("day"))
    artifact = session.observe({"revenue": metric}, during=axis).execute()
    (attachment,) = artifact._manifest.data.attachments
    assert attachment.name == "calendar_bounds"
    frame = read_output(session.project_root, attachment.schema, attachment.rows)
    assert len(frame) == 4 and frame["actual_start"].iloc[0] == pd.Timestamp("2026-01-01", tz="UTC")
    assert len(stored_frame(session, artifact)) == 4


@pytest.mark.parametrize("zone", ("Europe/Paris", "UTC+05:30"))
@pytest.mark.parametrize("from_source", (False, True))
def test_calendar_timezone_survives_private_cold_read(
    source_session: Session, zone: str, from_source: bool
):
    session = source_session
    metric = (
        session.catalog.metrics.get("sales.revenue") if from_source else numeric_literal_node(7)
    )
    axis = session.time_scope(start="2026-03-28", end="2026-03-31", timezone=zone).by(
        mv.grain("day")
    )
    artifact = (
        session.observe({"value": metric}, during=axis)
        if from_source
        else numeric_program_query(session, {"value": metric}, during=axis)
    ).execute()
    if not from_source:
        assert session._catalog_value is None
        assert not any(item.kind == "read" for item in records(session, artifact))
    script = """from pathlib import Path
import sys
import pandas as pd
from marivo.analysis.core.artifact_data import CalendarDataAxis, TimestampDataType
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization import terminal_store
from marivo.analysis.materialization.terminal_storage import read_output
store = SessionStore.open_existing(Path(sys.argv[1]))
with store._read() as conn:
    manifest = terminal_store.manifest(store, conn, sys.argv[2])
attachment, = manifest.data.attachments
zone = sys.argv[3]
assert type(manifest.data.schema.axes[0]) is CalendarDataAxis
assert manifest.data.schema.axes[0].timezone == zone
assert type(attachment.schema.axes[0]) is CalendarDataAxis
assert attachment.schema.axes[0].timezone == zone
assert attachment.schema.axes[0].token == manifest.data.schema.axes[0].token
assert all(column.dtype == TimestampDataType('us', 'UTC') for column in attachment.schema.values)
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
        [sys.executable, "-c", script, str(session.project_root), artifact.id, zone],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_read_evidence_preserves_nested_predicate_groups(source_session: Session):
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
    expected_filters = (
        "(((sales.orders.region == 'A') | sales.orders.region.isnull()) & (sales.orders.amount < 10))",
        "((sales.orders.region == 'A') | (sales.orders.region.isnull() & (sales.orders.amount < 10)))",
        "(~((sales.orders.region == 'A') | sales.orders.region.isnull()) & (sales.orders.amount < 10))",
    )
    for predicate, expected, total in zip(predicates, expected_filters, (5, 15, 0), strict=True):
        artifact = session.observe({"total": amount.sum()}, where=predicate).execute()
        assert stored_frame(session, artifact)["value.0"].tolist() == [total]
        facts = records(session, artifact)
        assert [dict(item.values)["filters"] for item in facts if item.kind == "read"] == [expected]


def test_old_store_rejection_preserves_bytes_and_name_cannot_bypass(tmp_path: Path):
    old = tmp_path / ".marivo/analysis/generations/v9/session_store.db"
    old.parent.mkdir(parents=True)
    with sqlite3.connect(old) as conn:
        conn.execute("PRAGMA user_version=9")
    before = old.read_bytes()
    for name in ("original", "renamed"):
        with pytest.raises(IntegrityError, match="user_version=9"):
            DatasetRuntime.create(tmp_path, name)
        with pytest.raises(IntegrityError, match="user_version=9"):
            SessionStore.open_existing(tmp_path)
    assert old.read_bytes() == before
    assert not (old.parent.parent / "v10").exists()


def test_private_cold_roundtrip_does_not_need_producer_or_catalog(session: Session, tmp_path: Path):
    artifact = constant(session).execute()
    script = """from pathlib import Path
import sys
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization import terminal_store
from marivo.analysis.materialization.terminal_storage import read_output
store=SessionStore.open_existing(Path(sys.argv[1]))
with store._read() as conn:
    manifest=terminal_store.manifest(store,conn,sys.argv[2])
assert read_output(store.project_root,manifest.data.schema,manifest.data.rows)["value.0"].tolist()==[7]
assert terminal_store.records_page(store,sys.argv[2],kind="finding")[0]==()
assert terminal_store.records_page(store,sys.argv[2],kind="evidence")[0]
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), artifact.id],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "point",
    (
        "query_admitted",
        "terminal_primary_written",
        "terminal_files_published",
        "insert_evidence",
        "before_commit",
        "after_commit",
    ),
)
def test_process_loss_reconciles_original_without_replay(session: Session, point: str):
    store = session._runtime.store
    saved = constant(session).execute()
    command = [
        sys.executable,
        "-m",
        "tests.analysis.materialization.terminal_recovery_worker",
        str(store.project_root),
        session.name,
        point,
    ]
    result = subprocess.run(
        command, cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True
    )
    assert result.returncode == 37, result.stderr
    with store._read() as conn:
        count_before = conn.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0]
    assert count_before == (2 if point == "after_commit" else 1)
    reopened = DatasetRuntime.create(store.project_root, session.name, report_timezone="UTC")
    assert len(reopened.runs().items) == 2
    assert reopened.runs().items[0].lifecycle == (
        "succeeded" if point == "after_commit" else "failed"
    )
    assert reopened.store.resources(session.id) == ()
    assert stored_frame(session, saved)["value.0"].tolist() == [7]
    with store._read() as conn:
        assert conn.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == count_before


def test_process_writer_contention_does_not_admit_contender(session: Session):
    from marivo.analysis.materialization.writer_guard import session_writer_guard

    store = session._runtime.store
    command = [
        sys.executable,
        "-m",
        "tests.analysis.materialization.terminal_recovery_worker",
        str(store.project_root),
        session.name,
        "none",
    ]
    with session_writer_guard(store.layout.lock_path(session.id), session_ref=session.id):
        result = subprocess.run(
            command, cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True
        )
    assert result.returncode == 38, result.stderr
    assert session.runs().items == ()
    result = subprocess.run(
        command, cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert len(session.runs().items) == 1


def test_unknown_commit_keeps_files_and_journal_until_reconciliation(
    session: Session, monkeypatch: pytest.MonkeyPatch
):
    store = session._runtime.store
    original_read = store._read

    def fail(point):
        if point == "before_commit":

            def unavailable():
                raise OSError("read-back unavailable")

            monkeypatch.setattr(store, "_read", unavailable)
            raise OSError("commit outcome unknown")

    session._runtime._hook = fail
    with pytest.raises(RecoveryPendingError) as caught:
        constant(session).execute()
    assert caught.value.stage == "terminal_commit_unknown"
    monkeypatch.setattr(store, "_read", original_read)
    session._runtime._hook = None
    assert store.resources(session.id)
    assert len(list(store.layout.session_dir(session.id).glob("artifacts/*"))) == 1
    reconcile(store, session.id, lambda point: None)
    assert store.resources(session.id) == ()
    assert session.runs().items[0].lifecycle == "failed"


def test_obsolete_output_envelope_is_rejected_without_rewrite():
    from marivo.analysis.materialization.contracts import canonical_json

    text = canonical_json({"format": "marivo.graph/v10", "value": {}})
    with pytest.raises(IntegrityError):
        decode(text, ArtifactManifest)
    assert text == canonical_json({"format": "marivo.graph/v10", "value": {}})


def test_large_output_uses_bounded_row_groups(tmp_path: Path):
    schema = DataSchema(
        (DataAxis("key", (DataColumn("k0", "key", ScalarDataType("int64"), False),)),),
        (DataColumn("v0", "value", ScalarDataType("int64"), True),),
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


def test_pure_source_publication_has_no_synthetic_local_check(source_session: Session):
    metric = source_session.catalog.metrics.get("sales.revenue")
    artifact = source_session.observe({"revenue": metric}).execute()
    assert stored_frame(source_session, artifact)["value.0"].tolist() == [9007199254740998]
    facts = records(source_session, artifact)
    assert sum(item.kind == "read" for item in facts) == 1
    assert not any("Local numeric" in item.summary for item in facts)
    assert all(
        dict(item.values)["datasource"] == "warehouse" for item in facts if item.kind == "read"
    )


def test_explicit_numeric_error_fails_original_run_without_publication(source_session: Session):
    amount = source_session.catalog.fields.get("sales.orders.amount")
    assert isinstance(amount, Deferred)
    total = amount.sum()
    with pytest.raises(MaterializationError) as caught:
        source_session.observe(
            {"ratio": total.divide(total * 0, zero_denominator="error")}
        ).execute()
    assert caught.value.run_ref is not None
    assert source_session.get_run(caught.value.run_ref).lifecycle == "failed"
    with source_session._runtime.store._read() as conn:
        assert conn.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 0


def test_preparation_failure_creates_no_run(source_session: Session):
    region = source_session.catalog.fields.get("sales.orders.region")
    metric = source_session.catalog.metrics.get("sales.revenue")
    with duckdb.connect(str(source_session.project_root / "warehouse.duckdb")) as conn:
        conn.execute("ALTER TABLE orders ALTER region TYPE UBIGINT USING 1")
    with pytest.raises(MaterializationError) as caught:
        source_session.observe({"revenue": metric}, by=(region,)).execute()
    assert caught.value.run_ref is None
    assert source_session.runs().items == ()


def test_legacy_publisher_is_rejected_before_admission(session: Session):
    from marivo.analysis.materialization.graph_publication import execute

    with pytest.raises(AnalysisStageUnavailableError, match="retired graph"):
        execute(session._runtime, object(), ())
    assert session.runs().items == ()


def test_source_disconnect_failure_prevents_publication(
    source_session: Session, monkeypatch: pytest.MonkeyPatch
):
    from marivo.datasource._lifetime import BackendLease

    original = BackendLease.close

    def unconfirmed(lease, *, suppress_errors=True):
        assert original(lease, suppress_errors=suppress_errors)
        return False

    monkeypatch.setattr(BackendLease, "close", unconfirmed)
    metric = source_session.catalog.metrics.get("sales.revenue")
    with pytest.raises(MaterializationError) as caught:
        source_session.observe({"revenue": metric}).execute()
    assert caught.value.run_ref is not None
    assert source_session.get_run(caught.value.run_ref).lifecycle == "failed"
    assert source_session._runtime.store.resources(source_session.id) == ()
    with source_session._runtime.store._read() as conn:
        assert conn.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 0


def test_lost_ack_with_missing_records_is_recovery_pending(session: Session):
    store = session._runtime.store

    def corrupt(point):
        if point == "after_commit":
            with store._write() as conn:
                conn.execute("DELETE FROM output_evidence")
            raise OSError("lost acknowledgement with contradictory records")

    session._runtime._hook = corrupt
    with pytest.raises(RecoveryPendingError):
        constant(session).execute()
    assert session.runs().items[0].lifecycle == "succeeded"
    assert len(list(store.layout.session_dir(session.id).glob("artifacts/*"))) == 1


@pytest.mark.parametrize("unit", ("s", "ms", "us", "ns"))
def test_time_units_roundtrip_exactly(tmp_path: Path, unit: str):
    schema = DataSchema(
        (),
        (
            DataColumn("v0", "instant", TimestampDataType(unit, "UTC"), True),
            DataColumn("v1", "duration", DurationDataType(unit), True),
        ),
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
    source_session: Session, monkeypatch, capsys
):
    session = source_session
    amount = session.catalog.fields.get("sales.orders.amount")
    artifact = session.observe({"total": amount.sum()}).execute()

    def forbidden(*args, **kwargs):
        pytest.fail("metadata and record reads must not consume Parquet or current semantics")

    monkeypatch.setattr(pq, "ParquetFile", forbidden)
    monkeypatch.setattr(Session, "catalog", property(forbidden))
    reopened = session.artifact(artifact.id)
    assert reopened.id == artifact.id and "Artifact" in repr(reopened)
    reopened.show(n=0)
    assert "rows" in capsys.readouterr().out
    assert reopened.findings().items == ()
    facts = reopened.evidence_digest().items
    assert any(item.kind == "read" for item in facts)
    assert session.graph().artifacts[0].artifact_ref.ref == artifact.id
    assert session.get_run(artifact._manifest.run_id).lifecycle == "succeeded"


def test_public_read_isolated_copy_and_historical_values(source_session: Session):
    session = source_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    artifact = session.observe(
        {"total": amount.sum(), "mean": amount.mean()}, by=(region,)
    ).execute()
    before = artifact.to_pandas()
    assert before[("value", "total")].tolist() == [9007199254740993, 5]
    assert pd.isna(before[("coordinate", "axis.0")].iloc[-1])
    assert before.attrs["marivo"]["axes"][0]["kind"] == "field"
    changed = artifact.to_pandas()
    changed.iloc[0, 1] = 0
    changed.attrs["marivo"]["axes"][0]["label"] = "changed"
    changed.attrs["marivo"]["quantities"][0]["dtype"]["name"] = "changed"
    pd.testing.assert_frame_equal(session.artifact(artifact.id).to_pandas(), before)
    assert session.artifact(artifact.id).to_pandas().attrs == before.attrs


def test_preview_decodes_only_requested_rows_and_skips_unused_parts(
    session: Session, monkeypatch, capsys
):
    from marivo.analysis.core.artifact_data import ArtifactPublication, OutputAttachment
    from marivo.analysis.materialization import terminal_publication
    from marivo.analysis.materialization.graph_reading import OutputRead

    count = POLICY.row_group_rows + 9
    key = DataColumn("key.0", "group", ScalarDataType("int64"), False)
    value = DataColumn("value.0", "amount", ScalarDataType("int64"), False)
    schema = DataSchema((DataAxis("axis.0", (key,)),), (value,))
    rows = pd.DataFrame({"key.0": range(count), "value.0": range(count)})
    original = terminal_publication._publication

    def output(graph, stage, artifact_id, run_id, evidence):
        return ArtifactPublication(
            artifact_id,
            session.id,
            run_id,
            schema,
            rows,
            attachments=(OutputAttachment("unused", schema, rows.iloc[:1]),),
            evidence=evidence,
        )

    monkeypatch.setattr(terminal_publication, "_publication", output)
    artifact = constant(session).execute()
    monkeypatch.setattr(terminal_publication, "_publication", original)
    (unused,) = artifact._manifest.data.attachments
    (session.project_root / unused.rows.receipt.project_relative_path / "data.parquet").unlink()
    sizes = []
    open_parquet = OutputRead._open

    class TrackedParquet:
        def __init__(self, parquet):
            self.parquet = parquet
            self.num_row_groups = parquet.num_row_groups
            self.reader = parquet.reader

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.parquet.close()

        def iter_batches(self, **kwargs):
            for batch in self.parquet.iter_batches(**kwargs):
                sizes.append(batch.num_rows)
                yield batch

    monkeypatch.setattr(
        OutputRead, "_open", lambda self, layout: TrackedParquet(open_parquet(self, layout))
    )
    for requested in (0, 20, POLICY.batch_rows + 1, POLICY.row_group_rows + 1):
        artifact.show(n=requested)
        assert sum(sizes) == requested
        assert all(size <= POLICY.batch_rows for size in sizes)
        assert "amount" in capsys.readouterr().out
        sizes.clear()
    frame = session.artifact(artifact.id).to_pandas()
    assert len(frame) == count and sum(sizes) == count
    assert frame[("value", "amount")].iloc[-1] == count - 1
    primary = (
        session.project_root
        / artifact._manifest.data.rows.receipt.project_relative_path
        / "data.parquet"
    )
    with primary.open("r+b") as stream:
        stream.seek(-5, os.SEEK_END)
        stream.write(b"!")
    with pytest.raises(IntegrityError):
        artifact.to_pandas()
    # Record reads remain independent even after both data files become unreadable.
    assert artifact.findings().items == ()
    artifact.evidence_digest()


def test_wide_artifact_truncation_routes_to_callable_reads(session: Session, capsys):
    artifact = numeric_program_query(
        session,
        {f"quantity_{index}_" + "x" * 150: numeric_literal_node(index) for index in range(60)},
    ).execute()
    artifact.show()
    text = capsys.readouterr().out
    assert "output truncated" in text
    assert "max_output_bytes" not in text
    assert ".to_pandas()" in text and ".evidence_digest()" in text
    assert len(text.encode()) <= 8193
    assert artifact.to_pandas().shape == (1, 60)
    assert artifact.evidence_digest().items


@pytest.mark.parametrize("zone", ("Europe/Paris", "UTC+05:30"))
def test_public_calendar_uses_saved_instants_and_boundaries(
    session: Session, zone: str, monkeypatch
):
    from marivo.analysis.materialization.graph_reading import OutputRead

    axis = session.time_scope(start="2026-03-28T06:00", end="2026-03-31", timezone=zone).by(
        mv.grain("day")
    )
    artifact = numeric_program_query(
        session, {"value": numeric_literal_node(7)}, during=axis
    ).execute()
    exported = session.artifact(artifact.id).to_pandas()
    (description,) = exported.attrs["marivo"]["axes"]
    assert description["kind"] == "calendar" and description["timezone"] == zone
    assert description["key_components"][0]["dtype"] == {"unit": "us", "timezone": "UTC"}
    assert len(description["bounds"]) == 3
    assert exported[("coordinate", "axis.0")].tolist() == [
        pd.Timestamp(cell.original_start) for cell in axis.grid.cells
    ]
    assert description["bounds"][0]["actual_start"] > description["bounds"][0]["nominal_start"]
    original = OutputRead.read
    consumed = []

    def tracked(self, layout, request):
        result = original(self, layout, request)
        consumed.append(len(result))
        return result

    monkeypatch.setattr(OutputRead, "read", tracked)
    artifact.show(n=1)
    assert consumed == [1, 1]


def test_public_produce_and_cold_read_are_independent_processes(source_session: Session):
    import shutil

    root = source_session.project_root
    worker = Path(__file__).with_name("terminal_reading_worker.py")
    produced = subprocess.run(
        [sys.executable, str(worker), str(root), "public-cold", "produce"],
        text=True,
        capture_output=True,
        check=True,
    )
    artifact_id = produced.stdout.strip().splitlines()[-1]
    (root / "warehouse.duckdb").unlink()
    shutil.rmtree(root / "models")
    reopened = subprocess.run(
        [sys.executable, str(worker), str(root), "public-cold", "read", artifact_id],
        text=True,
        capture_output=True,
        check=True,
    )
    assert "public-cold-ok" in reopened.stdout


def test_open_missing_and_foreign_ids_and_invalid_evidence_arguments(session: Session):
    from marivo.analysis.errors import ArtifactNotFoundError

    artifact = constant(session).execute()
    other = mv.session.get_or_create("another", report_timezone="UTC")
    with pytest.raises(ArtifactNotFoundError):
        other.artifact(artifact.id)
    with pytest.raises(ArtifactNotFoundError):
        session.artifact("missing")
    with pytest.raises(TypeError):
        artifact.evidence_digest(limit=1)


def test_composite_coordinates_remain_one_ordered_tuple(tmp_path: Path):
    from marivo.analysis.materialization.graph_reading import ExportRead, OutputRead
    from marivo.analysis.materialization.pandas_export import export_frame

    keys = (
        DataColumn("a", "account", ScalarDataType("int64"), False),
        DataColumn("b", "region", ScalarDataType("string"), True),
    )
    schema = DataSchema((DataAxis("axis.0", keys),), ())
    rows = pd.DataFrame(
        {
            "a": pd.Series([9007199254740993, 9007199254740993], dtype="int64[pyarrow]"),
            "b": pd.Series(["A", None], dtype="string[pyarrow]"),
        }
    )
    handle = write_output(tmp_path, tmp_path / "staging", tmp_path / "saved", schema, rows)
    os.rename(tmp_path / "staging", tmp_path / "saved")
    manifest = ArtifactManifest("artifact", "session", "run", ArtifactData(schema, handle))
    exported = export_frame(manifest, OutputRead(tmp_path), ExportRead())
    assert exported[("coordinate", "axis.0")].tolist() == [
        (9007199254740993, "A"),
        (9007199254740993, None),
    ]
    assert len(exported.attrs["marivo"]["axes"][0]["key_components"]) == 2
