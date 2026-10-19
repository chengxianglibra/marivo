"""Store12 Frame protocol, source-free lifecycle, sequence and atomic publication."""

from __future__ import annotations

import sqlite3
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from marivo._definition.correspondence import CalendarCorrespondence
from marivo.analysis.core.artifact_data import (
    ArtifactManifest,
    BuiltinCalendarData,
    CalendarDataAxis,
    DataAxis,
    DataColumn,
    DataSchema,
    DurationDataType,
    FrozenCalendarData,
    OutputAttachment,
    OutputEvidence,
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
from marivo.analysis.core.frozen_calendar import (
    calendar_directory_schema,
    read_calendar_directory,
    read_calendar_records,
)
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import terminal_publication
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
    RecoveryPendingError,
)
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.graph_reading import (
    FrameReader,
    OutputRead,
    TerminalReader,
    open_manifest,
)
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.terminal_protocol import (
    FORMAT,
    InvocationInput,
    decode,
    encode,
)
from marivo.analysis.materialization.terminal_storage import POLICY
from tests.shared_fixtures import DslCaseFactory


@pytest.fixture
def local(tmp_path: Path) -> LocalFrameServices:
    return DatasetRuntime.create(tmp_path, "saved-frames", report_timezone="UTC").local


def metadata() -> FrameResultMetadata:
    return FrameResultMetadata(
        "completed-before-publication",
        FrameAudit(
            (OutputEvidence("read", "Exact original source fact", (("scope", "unaltered"),)),),
            EvaluationRecord(datetime(2020, 1, 1, tzinfo=timezone.utc), (), ("literal",)),
        ),
    )


def ordinary(local: LocalFrameServices, count: int = 3) -> AnalysisFrame:
    key = DataColumn("key", "key", ScalarDataType("int64"), False)
    value = DataColumn("amount", "amount", ScalarDataType("int64"), True, "USD")
    schema = DataSchema(
        (DataAxis("axis", (key,), name="customer", kind="member"),),
        (value,),
        value_paths=(("amount",),),
    )
    rows = pd.DataFrame(
        {
            "key": pd.Series(list(range(count))[::-1], dtype="int64[pyarrow]"),
            "amount": pd.Series([None, *range(1, count)] if count else [], dtype="int64[pyarrow]"),
        }
    )
    return AnalysisFrame._from_memory(
        local, CompletedFrameResult(CompletedFrameData(schema, rows), metadata())
    )


def calendar(local: LocalFrameServices, count: int = 128, *, offset: int = 0) -> AnalysisFrame:
    key = DataColumn("bucket", "bucket", ScalarDataType("int64"), False)
    axis = CalendarDataAxis(
        "period",
        (key,),
        "UTC",
        name="day",
        frozen=FrozenCalendarData(BuiltinCalendarData("day", 1), "calendar.period"),
    )
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    starts = tuple(start + timedelta(days=i) for i in range(count))
    ends = tuple(point + timedelta(days=1) for point in starts)
    keys = [900_000 + i * 17 for i in range(count)]
    directory = pd.DataFrame(
        {
            "bucket": pd.Series(keys, dtype="int64[pyarrow]"),
            "nominal_label": pd.Series(
                [point.date().isoformat() for point in starts], dtype="string[pyarrow]"
            ),
            "ordinal": pd.Series(range(count), dtype="int64[pyarrow]"),
            **{
                name: pd.Series(values, dtype="timestamp[us, tz=UTC][pyarrow]")
                for name, values in (
                    ("nominal_start", starts),
                    ("nominal_end", ends),
                    ("actual_start", starts),
                    ("actual_end", ends),
                )
            },
        }
    )
    value = DataColumn("amount", "amount", ScalarDataType("int64"), False)
    schema = DataSchema((axis,), (value,), value_paths=(("amount",),))
    return AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(
            CompletedFrameData(
                schema,
                pd.DataFrame(
                    {
                        "bucket": pd.Series(keys[::-1], dtype="int64[pyarrow]"),
                        "amount": pd.Series(
                            [i + offset for i in range(count)], dtype="int64[pyarrow]"
                        ),
                    }
                ),
                (OutputAttachment("calendar.period", calendar_directory_schema(axis), directory),),
            ),
            metadata(),
        ),
    )


def manifest(frame: AnalysisFrame) -> ArtifactManifest:
    return open_manifest(frame._local.store, frame._local.session_ref, frame._backing.artifact_id)


def counts(local: LocalFrameServices) -> tuple[int, int]:
    with local.store._read() as conn:
        return (
            conn.execute("SELECT count(*) FROM analysis_action_runs").fetchone()[0],
            conn.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0],
        )


def test_sole_protocol_and_exact_closed_fields(local: LocalFrameServices) -> None:
    saved = ordinary(local).persist()
    value = manifest(saved)
    assert FORMAT == "marivo.frame_output/v1"
    assert local.store.layout.generation == 12
    assert decode(encode(value), ArtifactManifest) == value
    assert value.result_id == value.metadata.result_id
    assert value.metadata == metadata()
    assert tuple(field.name for field in fields(InvocationInput)) == ("result_id",)
    run = local.get_run(value.run_id)
    assert run.dataset_input == InvocationInput(metadata().result_id)
    assert value.metadata.evaluated_at < run.admitted_at
    assert counts(local) == (1, 1)
    assert "display_order" not in encode(value)


def test_memory_stored_lifecycle_and_detached_copy(local: LocalFrameServices) -> None:
    memory = ordinary(local)
    assert memory.compute() is memory
    saved = memory.persist()
    second = memory.persist()
    assert saved._backing.artifact_id != second._backing.artifact_id
    assert saved._backing.metadata == second._backing.metadata == memory._backing.metadata
    assert saved.persist() is saved
    reopened = AnalysisFrame._from_stored(
        local,
        saved._backing.data,
        artifact_id=saved._backing.artifact_id,
        metadata=saved._backing.metadata,
    )
    assert reopened._input_id == saved._input_id
    loaded = saved.compute()
    assert loaded._input_id != saved._input_id
    assert isinstance(loaded._backing, MemoryFrameBacking)
    assert loaded._backing.metadata == saved._backing.metadata
    pd.testing.assert_frame_equal(saved.to_pandas(), loaded.to_pandas())
    old = manifest(saved)
    target = local.store.project_root / old.data.rows.receipt.project_relative_path / "data.parquet"
    target.unlink()
    assert saved.persist() is saved
    loaded.to_pandas()
    loaded.persist().to_pandas()
    with pytest.raises(AnalysisError):
        saved.compute()
    assert counts(local) == (3, 3)


def test_exports_and_attrs_are_isolated(local: LocalFrameServices) -> None:
    memory = calendar(local, 3)
    saved = memory.persist()
    for frame in (memory, saved):
        exported = frame.to_pandas()
        exported.iloc[0, 1] = 999
        exported.attrs["frozen_coordinates"][0]["rows"].iloc[0, 1] = "changed"
        again = frame.to_pandas()
        assert again.iloc[0, 1] != 999
        assert again.attrs["frozen_coordinates"][0]["rows"].iloc[0, 1] == "2024-01-01"


@pytest.mark.parametrize("n", [True, -1, 1.5, "1"])
def test_invalid_preview_precedes_any_metadata(
    local: LocalFrameServices, n: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = ordinary(local).persist()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid preview touched metadata")

    monkeypatch.setattr(FrameReader, "manifest", forbidden)
    with pytest.raises(AnalysisError):
        saved.show(n=n)


def test_zero_preview_and_audit_do_not_open_payloads(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = calendar(local).persist()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("metadata operation opened Parquet")

    monkeypatch.setattr(pq, "ParquetFile", forbidden)
    saved.show(n=0)
    saved.persist()
    reader = TerminalReader(local.store)
    reader.show(manifest(saved))
    assert reader.findings(manifest(saved), limit=20, cursor=None).items == ()
    assert reader.evidence_digest(manifest(saved)).items == metadata().audit.source_facts


def test_prefix_resolves_sparse_original_keys_and_only_selected_records(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    saved = calendar(local, 128).persist()
    calls: list[int] = []
    original = OutputRead.select

    def selected(self: OutputRead, *args: object, **kwargs: object) -> pd.DataFrame:
        result = original(self, *args, **kwargs)
        calls.append(len(result))
        return result

    monkeypatch.setattr(OutputRead, "select", selected)
    prefix = local.preview(saved._backing, 2)
    assert prefix.rows["bucket"].tolist() == [902159, 902142]
    assert prefix.attachments[0].rows["ordinal"].tolist() == [126, 127]
    assert calls == [2]
    saved.show(n=1)
    assert "2024-05-07" in capsys.readouterr().out
    assert counts(local) == (1, 1)


def test_sparse_records_are_not_complete_directories(local: LocalFrameServices) -> None:
    frame = calendar(local, 128)
    axis = frame._schema.axes[0]
    part = frame._backing.data.copy_attachments()[0]
    subset = part.rows.iloc[-1:]
    records = read_calendar_records(axis, part.schema, subset)
    assert records[0].ordinal == 127
    with pytest.raises(AnalysisError, match="complete original ordinal sequence"):
        read_calendar_directory(axis, part.schema, subset)
    subset = subset.copy()
    subset["ordinal"] = pd.Series([-1], index=subset.index, dtype="int64[pyarrow]")
    with pytest.raises(AnalysisError, match="nonnegative original"):
        read_calendar_records(axis, part.schema, subset)


def test_paired_codec_and_prefix_keep_complete_mapping(local: LocalFrameServices) -> None:
    paired = (
        calendar(local, 3)
        .compare(
            calendar(local, 2, offset=10), match=CalendarCorrespondence("ordinal"), how="outer"
        )
        .compute()
    )
    saved = paired.persist()
    value = manifest(saved)
    assert decode(encode(value), ArtifactManifest) == value
    pd.testing.assert_frame_equal(
        paired._backing.data.copy_rows(), saved.compute()._backing.data.copy_rows()
    )
    prefix = local.preview(saved._backing, 1)
    assert len(prefix.rows) == 1
    assert all(len(part.rows) <= 1 for part in prefix.attachments)
    saved.show(n=1)
    assert len(saved.to_pandas().attrs["frozen_coordinates"]) == 3


@pytest.mark.parametrize(
    "dtype",
    [
        kind(unit, "UTC") if kind is TimestampDataType else kind(unit)
        for kind in (TimestampDataType, DurationDataType)
        for unit in ("s", "ms", "us", "ns")
    ],
)
@pytest.mark.parametrize("composite", [False, True])
def test_valid_minimum_temporal_ticks_survive_export_and_restore(
    local: LocalFrameServices, dtype: TimestampDataType | DurationDataType, composite: bool
) -> None:
    arrow = (
        pa.timestamp(dtype.unit, tz=dtype.timezone)
        if isinstance(dtype, TimestampDataType)
        else pa.duration(dtype.unit)
    )
    key = DataColumn("time", "time", dtype, True)
    columns = (
        (key, DataColumn("other", "other", ScalarDataType("string"), False))
        if composite
        else (key,)
    )
    rows = pd.DataFrame(
        {
            "time": pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array([-(2**63), None], type=pa.int64()).cast(arrow)
                )
            )
        }
    )
    if composite:
        rows["other"] = pd.Series(["x", "y"], dtype="string[pyarrow]")
    rows["observed"] = rows["time"].copy()
    schema = DataSchema(
        (DataAxis("time-axis", columns, name="time"),),
        (DataColumn("observed", "observed", dtype, True),),
        value_paths=(("observed",),),
    )
    memory = AnalysisFrame._from_memory(
        local, CompletedFrameResult(CompletedFrameData(schema, rows), metadata())
    )
    saved = memory.persist()
    for frame in (memory, saved, saved.compute()):
        exported = frame.to_pandas()
        if composite:
            scalar = exported.iloc[0, 0][0]
            assert scalar.is_valid
            assert scalar.cast(pa.int64()).as_py() == -(2**63)
        else:
            array = pa.array(exported.iloc[:, 0])
            assert array.null_count == 1
            assert array.cast(pa.int64()).to_pylist() == [-(2**63), None]
        values = pa.array(exported[("value", "observed")])
        assert values.null_count == 1
        assert values.cast(pa.int64()).to_pylist() == [-(2**63), None]
        frame.show(n=2)


@pytest.mark.parametrize(
    "point",
    [
        "publication_admitted",
        "terminal_primary_written",
        "terminal_files_published",
        "insert_artifact",
        "insert_evidence",
        "insert_findings",
        "insert_terminal",
        "before_commit",
    ],
)
def test_publication_fault_is_atomic_and_preserves_original_memory(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    memory = ordinary(local)
    previous = memory.persist()
    before = previous.to_pandas()

    def fault(event: str) -> None:
        if event == point:
            raise OSError(point)

    monkeypatch.setattr(terminal_publication, "_event", fault)
    with pytest.raises(MaterializationError):
        memory.persist()
    assert counts(local) == (2, 1)
    assert local.store.resources(local.session_ref) == ()
    pd.testing.assert_frame_equal(previous.to_pandas(), before)
    pd.testing.assert_frame_equal(memory.to_pandas(), before)
    assert any(run.lifecycle == "failed" for run in local.runs().items)


def test_lost_commit_ack_returns_original_without_evaluation(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = ordinary(local)

    def fault(event: str) -> None:
        if event == "after_commit":
            raise OSError("lost acknowledgement")

    monkeypatch.setattr(terminal_publication, "_event", fault)
    saved = memory.persist()
    assert counts(local) == (1, 1)
    assert saved._backing.metadata == memory._backing.metadata
    assert local.store.resources(local.session_ref) == ()


def test_unknown_outcome_preserves_original_until_readback(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = local.store
    original = store._read

    def unavailable() -> None:
        raise OSError("readback unavailable")

    def fault(event: str) -> None:
        if event == "before_commit":
            monkeypatch.setattr(store, "_read", unavailable)
            raise OSError("acknowledgement unknown")

    monkeypatch.setattr(terminal_publication, "_event", fault)
    with pytest.raises(RecoveryPendingError):
        ordinary(local).persist()
    monkeypatch.setattr(store, "_read", original)
    assert counts(local) == (1, 0)
    assert store.resources(local.session_ref)
    terminal_publication.reconcile(store, local.session_ref, lambda event: None)
    assert counts(local) == (1, 0)
    assert store.resources(local.session_ref) == ()


def test_sequence_spans_batches_and_row_groups(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization import terminal_storage

    monkeypatch.setattr(terminal_storage, "POLICY", replace(POLICY, row_group_rows=3, batch_rows=2))
    frame = ordinary(local, 11)
    saved = frame.persist()
    assert saved.to_pandas().iloc[:, 0].tolist() == list(range(11))[::-1]
    with pq.ParquetFile(
        local.store.project_root
        / manifest(saved).data.rows.receipt.project_relative_path
        / "data.parquet"
    ) as parquet:
        assert parquet.num_row_groups == 4


def test_old_only_store_rejected_without_any_byte_changes(tmp_path: Path) -> None:
    old = tmp_path / ".marivo/analysis/generations/v11/session_store.db"
    old.parent.mkdir(parents=True)
    with sqlite3.connect(old) as conn:
        conn.execute("PRAGMA user_version=11")
    before = old.read_bytes()
    for constructor in (
        lambda: SessionStore(tmp_path),
        lambda: SessionStore.open_existing(tmp_path),
    ):
        with pytest.raises(IntegrityError, match="generation"):
            constructor()
    assert old.read_bytes() == before
    assert not (old.parent.parent / "v12").exists()


def test_missing_unconsumed_endpoint_is_not_a_preview_read(local: LocalFrameServices) -> None:
    paired = (
        calendar(local, 3)
        .compare(calendar(local, 2), match=CalendarCorrespondence("ordinal"), how="outer")
        .compute()
        .persist()
    )
    axis = paired._schema.axes[0]
    right_name = axis.right.frozen.directory_name
    right_part = next(part for part in manifest(paired).data.attachments if part.name == right_name)
    (
        local.store.project_root / right_part.rows.receipt.project_relative_path / "data.parquet"
    ).unlink()
    # Left rows are reversed: the first row is the unmatched original ordinal 2.
    prefix = local.preview(paired._backing, 1)
    assert len(prefix.rows) == 1
    assert not next(part for part in prefix.attachments if part.name == right_name).rows.shape[0]
    paired.show(n=0)
    paired.artifact.show()
    with pytest.raises(AnalysisError):
        paired.compute()


def test_paired_partial_records_reject_missing_and_false_presence(
    local: LocalFrameServices,
) -> None:
    from marivo.analysis.core.frozen_calendar import (
        validate_paired_presence,
        validate_paired_records,
    )

    memory = (
        calendar(local, 3)
        .compare(calendar(local, 2), match=CalendarCorrespondence("ordinal"), how="outer")
        .compute()
    )
    prefix = local.preview(memory.persist()._backing, 1)
    axis = prefix.schema.axes[0]
    parts = {part.name: part for part in prefix.attachments}
    mapping = parts[axis.mapping_name]
    left = parts[axis.left.frozen.directory_name]
    right = parts[axis.right.frozen.directory_name]
    left_records = read_calendar_records(axis.left, left.schema, left.rows)
    right_records = read_calendar_records(axis.right, right.schema, right.rows)
    validate_paired_records(axis, mapping.schema, mapping.rows, left_records, right_records)
    with pytest.raises(AnalysisError, match="endpoint keys resolving"):
        validate_paired_records(axis, mapping.schema, mapping.rows, (), right_records)
    invalid = prefix.rows.copy()
    right_presence = prefix.schema.presence[prefix.schema.presence_paths.index(("right_present",))]
    invalid[right_presence.slot_id] = pd.Series([True], dtype="bool[pyarrow]")
    with pytest.raises(AnalysisError, match="presence consistent"):
        validate_paired_presence(axis, prefix.schema, invalid, mapping.schema, mapping.rows)


def test_inherited_coordinates_fail_before_run(local: LocalFrameServices) -> None:
    from marivo.analysis.core.artifact_data import OutputCoordinate

    frame = ordinary(local)
    fact = OutputEvidence("read", "Invalid source scope", coordinates=(OutputCoordinate("key", 1),))
    invalid = replace(
        frame._backing.metadata, audit=replace(frame._backing.metadata.audit, source_facts=(fact,))
    )
    with pytest.raises(IntegrityError, match="source facts"):
        local.publish(CompletedFrameResult(frame._backing.data, invalid))
    assert counts(local) == (0, 0)
    assert local.store.resources(local.session_ref) == ()


def test_temporal_cold_reader_keeps_physical_ticks_nulls_and_detaches(
    local: LocalFrameServices,
) -> None:
    import json
    import subprocess
    import sys

    saved_ids = []
    for kind in (TimestampDataType, DurationDataType):
        for unit in ("s", "ms", "us", "ns"):
            dtype = kind(unit, "UTC") if kind is TimestampDataType else kind(unit)
            physical = pa.timestamp(unit, "UTC") if kind is TimestampDataType else pa.duration(unit)
            key = DataColumn("key", "key", ScalarDataType("string"), False)
            value = DataColumn("value", "value", dtype, True)
            schema = DataSchema(
                (DataAxis("axis", (key,), name="item"),), (value,), value_paths=(("value",),)
            )
            rows = pd.DataFrame(
                {
                    "key": pd.Series(["b", "a"], dtype="string[pyarrow]"),
                    "value": pd.Series(
                        pd.arrays.ArrowExtensionArray(
                            pa.array([-(2**63), None], type=pa.int64()).cast(physical)
                        )
                    ),
                }
            )
            frame = AnalysisFrame._from_memory(
                local, CompletedFrameResult(CompletedFrameData(schema, rows), metadata())
            ).persist()
            saved_ids.append(frame.artifact.id)
    script = """
import json, sys
from pathlib import Path
from unittest.mock import patch
import pyarrow as pa
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.analysis.core import frame_calendar

def forbidden(*args, **kwargs):
    raise AssertionError("cold data read attempted model/source/calendar reconstruction")
store = SessionStore.open_existing(Path(sys.argv[1]))
local = LocalFrameServices(store, sys.argv[2])
with patch.object(Session, "catalog", property(forbidden)), patch.object(SourceSession, "__init__", forbidden), patch.object(frame_calendar, "freeze_calendar", forbidden):
    for artifact_id in json.loads(sys.argv[3]):
        backing = local.open_frame(artifact_id)
        result = local.read_complete(backing)
        rows = result.data.copy_rows()
        array = pa.array(rows["value"])
        assert array.null_count == 1
        assert array.cast(pa.int64()).to_pylist() == [-(2**63), None]
        assert rows["key"].tolist() == ["b", "a"]
        (store.project_root / backing.data.rows.receipt.project_relative_path / "data.parquet").unlink()
        copied = local.publish(result)
        assert copied.metadata == result.metadata
        copied_values = pa.array(local.read_complete(copied).data.copy_rows()["value"])
        assert copied_values.cast(pa.int64()).to_pylist() == [-(2**63), None]
print("cold-temporal-exact")
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(local.store.project_root),
            local.session_ref,
            json.dumps(saved_ids),
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cold-temporal-exact" in result.stdout


def rewrite_primary(saved: AnalysisFrame, rows: pd.DataFrame) -> StoredFrameBacking:
    from marivo.analysis.core.artifact_data import ParquetHandle
    from marivo.analysis.materialization.contracts import FileEntry
    from marivo.analysis.materialization.storage import _manifest_bytes, payload_digest
    from marivo.analysis.materialization.terminal_storage import physical_schema

    local = saved._local
    value = manifest(saved)
    directory = local.store.project_root / value.data.rows.receipt.project_relative_path
    pq.write_table(
        pa.Table.from_pandas(
            rows, schema=physical_schema(value.data.schema), preserve_index=False
        ).replace_schema_metadata(None),
        directory / "data.parquet",
        version="2.6",
    )
    entry = FileEntry("data.parquet", (directory / "data.parquet").stat().st_size)
    file_manifest = _manifest_bytes((entry,))
    (directory / "manifest.json").write_bytes(file_manifest)
    receipt = replace(
        value.data.rows.receipt,
        file_manifest=(entry,),
        realized_byte_count=entry.size_bytes + len(file_manifest),
    )
    handle = ParquetHandle(receipt, payload_digest(local.store.project_root, directory, receipt))
    corrupt = replace(value, data=replace(value.data, rows=handle))
    with local.store._write() as conn:
        conn.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (encode(corrupt), value.artifact_id),
        )
    return local.open_frame(value.artifact_id)


def test_full_read_rejects_duplicate_keys_beyond_a_valid_prefix(local: LocalFrameServices) -> None:
    saved = ordinary(local).persist()
    rows = saved.compute()._backing.data.copy_rows()
    rows["key"] = pd.Series([2, 2, 0], dtype="int64[pyarrow]")
    backing = rewrite_primary(saved, rows)
    assert len(local.preview(backing, 1).rows) == 1
    with pytest.raises(MaterializationError, match="unique stage key"):
        local.read_complete(backing)


def test_every_old_generation_entry_preserves_old_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis as mv

    monkeypatch.chdir(tmp_path)
    old = tmp_path / ".marivo/analysis/generations/v11/session_store.db"
    old.parent.mkdir(parents=True)
    with sqlite3.connect(old) as conn:
        conn.execute("PRAGMA user_version=11")
    payload = old.parent / "sessions/original/artifacts/original/primary/data.parquet"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"immutable old-generation payload")
    before = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    attempts = (
        lambda: mv.session.get_or_create("original", report_timezone="UTC"),
        lambda: mv.session.get_or_create("renamed", report_timezone="UTC"),
        lambda: mv.session.resume("original", by="name"),
        lambda: mv.session.resume("session_original", by="id"),
        lambda: mv.session.inspect("original"),
        lambda: mv.session.abandon_run(session_id="session_original", run_id="run_original"),
        lambda: SessionStore.open_existing(tmp_path),
    )
    for attempt in attempts:
        with pytest.raises(AnalysisError):
            attempt()
    assert mv.session.current() is None
    assert mv.session.recent().items == ()
    after = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not (old.parent.parent / "v12").exists()


def test_old_manifest_cannot_enter_any_current_read(local: LocalFrameServices) -> None:
    from marivo.analysis.materialization import terminal_protocol
    from marivo.analysis.materialization.graph_reading import selected_manifest

    saved = ordinary(local).persist()
    current = manifest(saved)
    frozen = Path(__file__).with_name("terminal_v1_frozen.json").read_text().strip()
    with local.store._write() as conn:
        conn.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (frozen, current.artifact_id),
        )
    operations = (
        lambda: local.open_frame(current.artifact_id),
        lambda: local.read_complete(saved._backing),
        lambda: local.preview(saved._backing, 0),
        lambda: saved.persist(),
        lambda: local.artifact(current.artifact_id),
        lambda: TerminalReader(local.store).show(current),
        lambda: TerminalReader(local.store).findings(current, limit=1, cursor=None),
        lambda: TerminalReader(local.store).evidence_digest(current),
        lambda: selected_manifest(local.store, current),
        lambda: terminal_protocol.decode(frozen, ArtifactManifest),
    )
    before = counts(local)
    for operation in operations:
        with pytest.raises(IntegrityError):
            operation()
    assert counts(local) == before
    with local.store._read() as conn:
        assert (
            conn.execute(
                "SELECT descriptor_payload FROM dataset_artifacts WHERE artifact_ref=?",
                (current.artifact_id,),
            ).fetchone()[0]
            == frozen
        )


def test_named_calendar_snapshot_and_level_roundtrip(local: LocalFrameServices) -> None:
    from marivo.analysis.core.artifact_data import NamedCalendarData

    original = calendar(local, 3)
    axis = original._schema.axes[0]
    named = replace(
        axis,
        frozen=replace(
            axis.frozen, definition=NamedCalendarData("business", "snapshot-exact", "period")
        ),
    )
    schema = replace(original._schema, axes=(named,))
    part = original._backing.data.copy_attachments()[0]
    directory = part.rows.copy()
    directory["nominal_label"] = pd.Series(['"P1"', '"P2"', '"P3"'], dtype="string[pyarrow]")
    fixed = AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(
            CompletedFrameData(
                schema,
                original._backing.data.copy_rows(),
                (OutputAttachment(part.name, calendar_directory_schema(named), directory),),
            ),
            metadata(),
        ),
    )
    saved = fixed.persist()
    value = decode(encode(manifest(saved)), ArtifactManifest)
    assert value.data.schema.axes[0].frozen.definition == NamedCalendarData(
        "business", "snapshot-exact", "period"
    )
    loaded = saved.compute()
    pd.testing.assert_frame_equal(
        loaded._backing.data.copy_attachments()[0].rows,
        fixed._backing.data.copy_attachments()[0].rows,
    )
    assert loaded._backing.metadata == fixed._backing.metadata
    assert local.preview(saved._backing, 1).attachments[0].rows["nominal_label"].tolist() == [
        '"P3"'
    ]


def test_storage_error_help_targets_resolve(capsys: pytest.CaptureFixture[str]) -> None:
    import marivo
    from marivo.analysis.materialization.errors import SessionBusyError, StorageAccessError
    from marivo.analysis.materialization.store import _generation_error

    errors = (
        MaterializationError(
            expected="checked numeric output", received="invalid", repair="Repair input."
        ),
        SessionBusyError(session_ref="session"),
        StorageAccessError("missing"),
        _generation_error(11),
    )
    expected = (
        "analysis_frame.compute",
        "analysis_frame.persist",
        "session.persisted_frame",
        "session.resume",
    )
    for error, canonical in zip(errors, expected, strict=True):
        target = error.repair.help_target
        assert target.canonical_id == canonical
        marivo.help(f"{target.surface}.{target.canonical_id}")
        assert f"Target: analysis.{canonical}" in capsys.readouterr().out


def test_inherited_presence_rejects_full_and_consumed_prefix_only(
    local: LocalFrameServices,
) -> None:
    original = ordinary(local)
    schema = original._schema
    flags = tuple(
        DataColumn(name, name, ScalarDataType("boolean"), False)
        for name in ("left_present", "left.left_present")
    )
    schema = replace(
        schema, presence=flags, presence_paths=(("left_present",), ("left", "left_present"))
    )
    rows = original._backing.data.copy_rows()
    rows["left_present"] = pd.Series([True, False, True], dtype="bool[pyarrow]")
    rows["left.left_present"] = pd.Series([True, False, False], dtype="bool[pyarrow]")
    # Row0's value is NA but its row and inherited row both exist.
    valid = AnalysisFrame._from_memory(
        local, CompletedFrameResult(CompletedFrameData(schema, rows), metadata())
    )
    saved = valid.persist()
    assert len(local.read_complete(saved._backing).data.copy_rows()) == 3
    corrupt = rows.copy()
    corrupt.loc[1, "left.left_present"] = True
    backing = rewrite_primary(saved, corrupt)
    assert len(local.preview(backing, 1).rows) == 1
    with pytest.raises(AnalysisError, match="inherited true presence"):
        local.preview(backing, 2)
    with pytest.raises(AnalysisError, match="inherited true presence"):
        local.read_complete(backing)
    # Removing presence, as reduce does, leaves no inferred hidden obligations.
    reduced_schema = replace(schema, presence=(), presence_paths=())
    CompletedFrameData(reduced_schema, original._backing.data.copy_rows())


def test_date32_extreme_composite_coordinate_preserves_exact_value(
    local: LocalFrameServices,
) -> None:
    day = DataColumn("day", "day", ScalarDataType("date"), False)
    kind = DataColumn("kind", "kind", ScalarDataType("string"), False)
    schema = DataSchema((DataAxis("date-kind", (day, kind), name="date-kind"),), ())
    rows = pd.DataFrame(
        {
            "day": pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array([-(2**31)], type=pa.int32()).cast(pa.date32())
                )
            ),
            "kind": pd.Series(["x"], dtype="string[pyarrow]"),
        }
    )
    memory = AnalysisFrame._from_memory(
        local, CompletedFrameResult(CompletedFrameData(schema, rows), metadata())
    )
    saved = memory.persist()
    for frame in (memory, saved, saved.compute()):
        scalar = frame.to_pandas().iloc[0, 0][0]
        assert scalar.is_valid and scalar.cast(pa.int32()).as_py() == -(2**31)
        frame.show(n=1)


@pytest.mark.parametrize("count", [0, 1])
def test_zero_column_preview_preserves_empty_vs_singleton(
    local: LocalFrameServices, count: int, capsys: pytest.CaptureFixture[str]
) -> None:
    memory = AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(
            CompletedFrameData(DataSchema((), ()), pd.DataFrame(index=pd.RangeIndex(count))),
            metadata(),
        ),
    )
    for frame in (memory, memory.persist()):
        assert frame.to_pandas().shape == (count, 0)
        frame.show(n=1)
        output = capsys.readouterr().out
        assert ("one row: ()" if count else "empty result") in output
        frame.show(n=0)
        assert "omitted by n=0" in capsys.readouterr().out


@pytest.mark.parametrize(
    "dtype",
    [TimestampDataType(unit, "UTC") for unit in ("s", "ms", "us", "ns")]
    + [DurationDataType(unit) for unit in ("s", "ms", "us", "ns")]
    + [ScalarDataType("date")],
)
@pytest.mark.parametrize("composite", [False, True])
def test_reduce_retains_exact_extreme_and_null_complete_keys(
    local: LocalFrameServices,
    dtype: TimestampDataType | DurationDataType | ScalarDataType,
    composite: bool,
) -> None:
    from marivo.analysis.dsl.column import row_count

    if isinstance(dtype, TimestampDataType):
        physical, integer, minimum = pa.timestamp(dtype.unit, dtype.timezone), pa.int64(), -(2**63)
    elif isinstance(dtype, DurationDataType):
        physical, integer, minimum = pa.duration(dtype.unit), pa.int64(), -(2**63)
    else:
        physical, integer, minimum = pa.date32(), pa.int32(), -(2**31)
    key = DataColumn("key", "key", dtype, True)
    other = DataColumn("other", "other", ScalarDataType("string"), False)
    region = DataColumn("region", "region", ScalarDataType("string"), False)
    keys = (key, other) if composite else (key,)
    schema = DataSchema(
        (DataAxis("typed", keys, name="typed"), DataAxis("region", (region,), name="region")),
        (DataColumn("amount", "amount", ScalarDataType("int64"), True),),
        value_paths=(("amount",),),
    )
    rows = pd.DataFrame(
        {
            "key": pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array([minimum, None, minimum], type=integer).cast(physical)
                )
            ),
            **({"other": pd.Series(["x", "x", "x"], dtype="string[pyarrow]")} if composite else {}),
            "region": pd.Series(["east", "east", "west"], dtype="string[pyarrow]"),
            "amount": pd.Series([4, None, 6], dtype="int64[pyarrow]"),
        }
    )
    memory = AnalysisFrame._from_memory(
        local, CompletedFrameResult(CompletedFrameData(schema, rows), metadata())
    )
    reduced = memory.reduce(
        {"rows": row_count(), "total": memory["amount"].sum()}, by=(memory.axes[0],)
    ).compute()
    saved = reduced.persist()
    for frame in (reduced, saved, saved.compute()):
        data = frame.compute()._backing.data.copy_rows()
        values = pa.array(data["key"])
        assert values.type == physical
        assert values.cast(integer).to_pylist() == [None, minimum]
        assert frame.to_pandas()[("value", "rows")].tolist() == [1, 2]
        assert frame.to_pandas()[("value", "total")].tolist() == [pd.NA, 10]

    if composite:
        import subprocess
        import sys

        script = """
import sys
from pathlib import Path
import pyarrow as pa
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.store import SessionStore
local = LocalFrameServices(SessionStore.open_existing(Path(sys.argv[1])), sys.argv[2])
result = local.read_complete(local.open_frame(sys.argv[3]))
rows = result.data.copy_rows()
key = pa.array(rows["key"])
integer = pa.int32() if pa.types.is_date32(key.type) else pa.int64()
assert key.cast(integer).to_pylist() == [None, int(sys.argv[4])]
assert rows["other"].tolist() == ["x", "x"]
values = result.data.schema.values
assert pa.array(rows[values[0].slot_id]).to_pylist() == [1, 2]
assert pa.array(rows[values[1].slot_id]).to_pylist() == [None, 10]
"""
        cold = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(local.store.project_root),
                local.session_ref,
                saved.artifact.id,
                str(minimum),
            ],
            cwd=Path(__file__).resolve().parents[3],
            text=True,
            capture_output=True,
        )
        assert cold.returncode == 0, cold.stdout + cold.stderr


@pytest.mark.parametrize(
    "dtype",
    [TimestampDataType(unit, "UTC") for unit in ("s", "ms", "us", "ns")]
    + [DurationDataType(unit) for unit in ("s", "ms", "us", "ns")]
    + [ScalarDataType("date")],
)
def test_top_orders_exact_extreme_null_composite_keys_and_scores(
    local: LocalFrameServices,
    dtype: TimestampDataType | DurationDataType | ScalarDataType,
) -> None:
    import json
    import subprocess
    import sys

    if isinstance(dtype, TimestampDataType):
        physical, integer, minimum = pa.timestamp(dtype.unit, dtype.timezone), pa.int64(), -(2**63)
    elif isinstance(dtype, DurationDataType):
        physical, integer, minimum = pa.duration(dtype.unit), pa.int64(), -(2**63)
    else:
        physical, integer, minimum = pa.date32(), pa.int32(), -(2**31)
    key = DataColumn("key", "key", dtype, True)
    other = DataColumn("other", "other", ScalarDataType("string"), False)
    schema = DataSchema(
        (DataAxis("typed", (key, other), name="typed"),),
        (DataColumn("amount", "amount", ScalarDataType("int64"), True),),
        value_paths=(("amount",),),
    )
    rows = pd.DataFrame(
        {
            "key": pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array([minimum, None, minimum, 0, 1, None], type=integer).cast(physical)
                )
            ),
            "other": pd.Series(["z", "b", "a", "x", "x", "a"], dtype="string[pyarrow]"),
            "amount": pd.Series([3, 3, 3, 1, None, None], dtype="int64[pyarrow]"),
        }
    )
    memory = AnalysisFrame._from_memory(
        local, CompletedFrameResult(CompletedFrameData(schema, rows), metadata())
    )
    expected = {
        True: (
            [None, minimum, minimum, 0, None, 1],
            ["b", "a", "z", "x", "a", "x"],
            [3, 3, 3, 1, None, None],
        ),
        False: (
            [0, None, minimum, minimum, None, 1],
            ["x", "b", "a", "z", "a", "x"],
            [1, 3, 3, 3, None, None],
        ),
    }
    stored_input = memory.persist()
    saved_cases = []
    for descending, (keys, others, scores) in expected.items():
        top = memory.top(6, by=memory["amount"], descending=descending).compute()
        saved = top.persist()
        stored_top = stored_input.top(6, by=stored_input["amount"], descending=descending).compute()
        for frame in (top, stored_top, saved, saved.compute()):
            data = frame.compute()._backing.data.copy_rows()
            actual = pa.array(data["key"])
            assert actual.type == physical
            assert actual.cast(integer).to_pylist() == keys
            assert data["other"].tolist() == others
            assert pa.array(data["amount"]).to_pylist() == scores
        cutoff = memory.top(2, by=memory["amount"], descending=descending).compute()
        cutoff_rows = cutoff._backing.data.copy_rows()
        assert pa.array(cutoff_rows["key"]).cast(integer).to_pylist() == keys[:2]
        assert cutoff_rows["other"].tolist() == others[:2]
        saved_cases.append((saved.artifact.id, keys, others, scores))
    script = """
import json, sys
from pathlib import Path
import pyarrow as pa
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.store import SessionStore
local = LocalFrameServices(SessionStore.open_existing(Path(sys.argv[1])), sys.argv[2])
for artifact_id, keys, others, scores in json.loads(sys.argv[3]):
    result = local.read_complete(local.open_frame(artifact_id))
    rows = result.data.copy_rows()
    key = pa.array(rows["key"])
    integer = pa.int32() if pa.types.is_date32(key.type) else pa.int64()
    assert key.cast(integer).to_pylist() == keys
    assert rows["other"].tolist() == others
    assert pa.array(rows["amount"]).to_pylist() == scores
"""
    cold = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(local.store.project_root),
            local.session_ref,
            json.dumps(saved_cases),
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
    )
    assert cold.returncode == 0, cold.stdout + cold.stderr


@pytest.mark.parametrize("kind", ["ordinary", "calendar", "named_calendar"])
def test_actual_stored_inputs_derive_without_sources_and_republish_detached(
    local: LocalFrameServices,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    from marivo._definition.correspondence import CalendarCorrespondence
    from marivo.analysis.core import frame_calendar
    from marivo.analysis.core.artifact_data import NamedCalendarData
    from marivo.analysis.core.frame_audit import StoredInput
    from marivo.analysis.materialization import frame_evaluation
    from marivo.analysis.session.core import Session
    from marivo.datasource.adapters import SourceSession

    memory = ordinary(local) if kind == "ordinary" else calendar(local, 3)
    if kind == "named_calendar":
        axis = memory._schema.axes[0]
        named = replace(
            axis,
            frozen=replace(
                axis.frozen, definition=NamedCalendarData("business", "snapshot-exact", "period")
            ),
        )
        part = memory._backing.data.copy_attachments()[0]
        directory = part.rows.copy()
        directory["nominal_label"] = pd.Series(['"P1"', '"P2"', '"P3"'], dtype="string[pyarrow]")
        memory = AnalysisFrame._from_memory(
            local,
            CompletedFrameResult(
                CompletedFrameData(
                    replace(memory._schema, axes=(named,)),
                    memory._backing.data.copy_rows(),
                    (OutputAttachment(part.name, calendar_directory_schema(named), directory),),
                ),
                metadata(),
            ),
        )
    saved = memory.persist()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "fixed-only stored evaluation attempted source/model/calendar reconstruction"
        )

    monkeypatch.setattr(frame_evaluation, "open_query_sources", forbidden)
    monkeypatch.setattr(Session, "catalog", property(forbidden))
    monkeypatch.setattr(SourceSession, "__init__", forbidden)
    monkeypatch.setattr(frame_calendar, "freeze_calendar", forbidden)
    before = counts(local)
    loaded = saved.compute()
    assert loaded._backing.metadata == saved._backing.metadata
    derived = saved.select({"twice": saved["amount"] * 2})
    result = derived.top(2, by=derived["twice"]).compute()
    assert result.to_pandas()[("value", "twice")].tolist() == [4, 2]
    captured = result._backing.metadata.audit.evaluation.inputs
    assert captured == (
        StoredInput(
            saved._input_id,
            saved._backing.metadata.result_id,
            local.store.store_id,
            local.session_ref,
            saved.artifact.id,
        ),
    )
    assert result._backing.metadata.result_id != saved._backing.metadata.result_id
    positive = saved.where(saved["amount"] > 0).compute()
    assert positive.to_pandas()[("value", "amount")].tolist() == [1, 2]
    compared = (
        saved.compare(saved, on=((saved.axes[0], saved.axes[0]),))
        if kind == "ordinary"
        else saved.compare(saved, match=CalendarCorrespondence("ordinal"))
    ).compute()
    assert compared.to_pandas()[("value", "absolute.amount")].tolist() == (
        [pd.NA, 0, 0] if kind == "ordinary" else [0, 0, 0]
    )
    assert counts(local) == before
    import subprocess
    import sys

    script = """
import os, sys
from unittest.mock import patch
import marivo.analysis as mv
from marivo.analysis.core import frame_calendar
from marivo.analysis.materialization import frame_evaluation
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
os.environ["MARIVO_PROJECT_ROOT"] = sys.argv[1]
def forbidden(*args, **kwargs):
    raise AssertionError("cold stored derive attempted source/model/calendar reconstruction")
with patch.object(Session, "catalog", property(forbidden)), patch.object(SourceSession, "__init__", forbidden), patch.object(frame_calendar, "freeze_calendar", forbidden), patch.object(frame_evaluation, "open_query_sources", forbidden):
    session = mv.session.resume(sys.argv[2], by="id")
    saved = session.persisted_frame(sys.argv[3])
    before = len(session.runs().items)
    derived = saved.where(saved["amount"] > 0)
    result = derived.top(2, by=derived["amount"]).compute()
    assert result.to_pandas()[("value", "amount")].tolist() == [2, 1]
    compared = (saved.compare(saved, on=((saved.axes[0], saved.axes[0]),))
                if sys.argv[4] == "ordinary" else saved.compare(saved, match=mv.ordinal())).compute()
    assert len(compared.to_pandas()) == 3
    assert len(session.runs().items) == before
    copied = result.persist()
    assert copied._backing.metadata == result._backing.metadata
    assert copied.to_pandas()[("value", "amount")].tolist() == [2, 1]
    assert len(session.runs().items) == before + 1
"""
    cold = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(local.store.project_root),
            local.session_ref,
            saved.artifact.id,
            kind,
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
    )
    assert cold.returncode == 0, cold.stdout + cold.stderr
    after_cold = counts(local)
    assert after_cold == (before[0] + 1, before[1] + 1)
    copied = result.persist()
    assert copied._backing.metadata == result._backing.metadata
    pd.testing.assert_frame_equal(copied.to_pandas(), result.to_pandas())
    old = manifest(saved)
    (
        local.store.project_root / old.data.rows.receipt.project_relative_path / "data.parquet"
    ).unlink()
    assert saved.persist() is saved
    assert result.persist()._backing.metadata == result._backing.metadata
    with pytest.raises(AnalysisError):
        saved.where(saved["amount"] > 0).compute()
    assert counts(local) == (after_cold[0] + 2, after_cold[1] + 2)


def test_pending_show_keeps_deep_shared_scope_and_stops_at_fixed_inputs(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import marivo.analysis as mv
    from marivo.analysis.core.graph import BoundGraph
    from marivo.analysis.dsl import frame as frame_module
    from marivo.analysis.materialization import frame_evaluation
    from marivo.analysis.session.core import Session
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    region = case.catalog.fields.get("sales.customer.region")
    source = case.session.observe(
        {"sales": case.catalog.metrics.get("sales.revenue")},
        where=region == "east",
        by=mv.coordinates(by=(region,), keys=(("east",), ("south",), ("west",))),
        during=case.session.time_scope(start="2026-08-01", end="2026-09-01"),
    )
    memory = source.compute()
    saved = memory.persist()
    fixed_pending = tuple(fixed.select({"fixed": fixed["sales"]}) for fixed in (memory, saved))
    deep = source
    for _ in range(1100):
        deep = deep.select({"sales": deep["sales"]})
    shared = deep.align(deep, on=((deep.axes[0], deep.axes[0]),))
    calls: list[int] = []
    capture = frame_module.captured_source_scopes

    def observed(graph: BoundGraph) -> tuple[str, ...]:
        calls.append(id(graph))
        return capture(graph)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("pending metadata show read source/Catalog/schema/rows/history")

    monkeypatch.setattr(frame_module, "captured_source_scopes", observed)
    monkeypatch.setattr(Session, "catalog", property(forbidden))
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    monkeypatch.setattr(frame_evaluation, "evaluate_frame", forbidden)
    monkeypatch.setattr(frame_evaluation, "open_query_sources", forbidden)
    for name in ("copy_rows", "preview_rows", "copy_attachments"):
        monkeypatch.setattr(CompletedFrameData, name, forbidden)
    for name in ("open_frame", "read_complete", "preview"):
        monkeypatch.setattr(LocalFrameServices, name, forbidden)
    before = tuple(case.session.runs().items)
    for pending in (deep, shared):
        calls.clear()
        pending.show(n=20)
        card = capsys.readouterr().out
        assert len(calls) == 1
        assert all(value in card for value in ("sales.customer.region", "east", "south", "west"))
        assert "period:" in card and "2026-08-01" in card and "2026-09-01" in card
        assert "pending; no input rows read" in card
        assert len(card.encode()) <= 8193
        pending.show(n=20)
        assert capsys.readouterr().out == card
    for pending in fixed_pending:
        calls.clear()
        pending.show(n=20)
        card = capsys.readouterr().out
        assert calls == []
        assert "scope:" not in card and "period:" not in card
        assert "east" not in card
        assert "memory" in card or "stored" in card
    assert tuple(case.session.runs().items) == before


def test_pending_read_label_and_derived_anchor_are_captured_without_execution(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import marivo.analysis as mv
    import marivo.semantic as ms
    from marivo.analysis.core.graph import BoundGraph
    from marivo.analysis.dsl import frame as frame_module
    from marivo.analysis.materialization import frame_evaluation
    from marivo.analysis.session.core import Session
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    region = case.catalog.fields.get("sales.customer.region")
    members = case.session.members(ms.ref.entity("sales.customer"), where=region == "east")
    read = case.session.read(members, {"region": region})
    selected = read.select({"selected_region": read["region"]})
    instant = case.session.instant("2026-08-01T00:00:00Z")
    anchored = case.session.frame(
        case.session.members(ms.ref.entity("sales.customer"), at=instant)
    ).reduce({"rows": mv.row_count()})

    capture = frame_module.captured_source_scopes
    read_scopes = capture(read._backing.root.graph)
    observed: list[tuple[str, ...]] = []

    def capture_current(graph: BoundGraph) -> tuple[str, ...]:
        scopes = capture(graph)
        observed.append(scopes)
        return scopes

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("pending metadata show prepared or executed a source")

    monkeypatch.setattr(frame_module, "captured_source_scopes", capture_current)
    monkeypatch.setattr(Session, "catalog", property(forbidden))
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    monkeypatch.setattr(frame_evaluation, "evaluate_frame", forbidden)
    monkeypatch.setattr(frame_evaluation, "open_query_sources", forbidden)
    monkeypatch.setattr(LocalFrameServices, "read_complete", forbidden)
    monkeypatch.setattr(CompletedFrameData, "copy_rows", forbidden)
    read.show(n=0)
    card = capsys.readouterr().out
    assert "operation: read" in card and "inputs: source" in card
    assert "scope:" in card and "sales.customer.region" in card
    assert "east" in card or "omitted" in card
    assert len(card.encode()) <= 8193
    selected.show(n=0)
    card = capsys.readouterr().out
    assert "select" in card and "scope:" in card and "sales.customer.region" in card
    assert len(card.encode()) <= 8193
    selected.show(n=0)
    assert capsys.readouterr().out == card
    assert observed == [read_scopes, read_scopes, read_scopes]
    assert capture(read._backing.root.graph) == read_scopes
    anchored.show(n=0)
    card = capsys.readouterr().out
    assert "anchors:" in card and "2026-08-01T00:00:00+00:00" in card
    assert "reduce" in card and "pending" in card
    assert case.session.runs().items == ()


def test_attachment_preview_filters_arrow_before_python_key_materialization(
    local: LocalFrameServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.materialization import graph_reading

    saved = calendar(local, 128).persist()
    attachment = saved._backing.data.attachments[0]
    converted: list[int] = []
    decoded: list[int] = []
    original_open = OutputRead._open
    original_keys = graph_reading.frozen_key_rows

    class ObservedTable:
        def __init__(self, table: pa.Table) -> None:
            self.table = table

        @property
        def num_rows(self) -> int:
            return self.table.num_rows

        def cast(self, schema: pa.Schema, *, safe: bool) -> ObservedTable:
            return ObservedTable(self.table.cast(schema, safe=safe))

        def column(self, name: str) -> pa.ChunkedArray:
            return self.table.column(name)

        def filter(self, mask: pa.Array | pa.ChunkedArray) -> ObservedTable:
            return ObservedTable(self.table.filter(mask))

        def take(self, indices: pa.Array) -> pa.Table:
            return self.table.take(indices)

        def to_pandas(self, **kwargs: object) -> pd.DataFrame:
            converted.append(self.table.num_rows)
            return self.table.to_pandas(**kwargs)

    class ObservedParquet:
        def __init__(self, parquet: pq.ParquetFile) -> None:
            self.parquet = parquet

        @property
        def num_row_groups(self) -> int:
            return self.parquet.num_row_groups

        def read_row_group(self, index: int, columns: list[str] | None = None) -> ObservedTable:
            return ObservedTable(self.parquet.read_row_group(index, columns=columns))

        def __enter__(self) -> ObservedParquet:
            return self

        def __exit__(self, *args: object) -> None:
            self.parquet.close()

    def observed_open(self: OutputRead, layout: graph_reading.ReadLayout) -> object:
        parquet = original_open(self, layout)
        return ObservedParquet(parquet) if layout.handle == attachment.rows else parquet

    def observed_keys(rows: pd.DataFrame, columns: tuple[DataColumn, ...]) -> object:
        decoded.append(len(rows))
        return original_keys(rows, columns)

    monkeypatch.setattr(OutputRead, "_open", observed_open)
    monkeypatch.setattr(graph_reading, "frozen_key_rows", observed_keys)
    prefix = local.preview(saved._backing, 1)
    assert prefix.attachments[0].rows["ordinal"].tolist() == [127]
    assert converted == [1]
    assert decoded and max(decoded) == 1
    assert counts(local) == (1, 1)


@pytest.mark.parametrize(
    "dtype",
    [TimestampDataType(unit, "UTC") for unit in ("s", "ms", "us", "ns")]
    + [DurationDataType(unit) for unit in ("s", "ms", "us", "ns")]
    + [ScalarDataType("date")],
)
def test_arrow_attachment_selection_keeps_exact_nullable_composite_keys(
    local: LocalFrameServices,
    dtype: TimestampDataType | DurationDataType | ScalarDataType,
) -> None:
    from marivo.analysis.core.frozen_calendar import frozen_key_rows
    from marivo.analysis.materialization.graph_reading import ReadLayout

    if isinstance(dtype, TimestampDataType):
        physical, integer, minimum = pa.timestamp(dtype.unit, dtype.timezone), pa.int64(), -(2**63)
    elif isinstance(dtype, DurationDataType):
        physical, integer, minimum = pa.duration(dtype.unit), pa.int64(), -(2**63)
    else:
        physical, integer, minimum = pa.date32(), pa.int32(), -(2**31)
    columns = (
        DataColumn("key", "key", dtype, True),
        DataColumn("other", "other", ScalarDataType("string"), True),
    )
    schema = DataSchema(
        (DataAxis("typed", columns, name="typed"),),
        (DataColumn("value", "value", ScalarDataType("int64"), False),),
        value_paths=(("value",),),
    )
    rows = pd.DataFrame(
        {
            "key": pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array([minimum, None, 0, minimum], type=integer).cast(physical)
                )
            ),
            "other": pd.Series(["a", None, "a", "z"], dtype="string[pyarrow]"),
            "value": pd.Series([10, 20, 30, 40], dtype="int64[pyarrow]"),
        }
    )
    saved = AnalysisFrame._from_memory(
        local, CompletedFrameResult(CompletedFrameData(schema, rows), metadata())
    ).persist()
    keys = set(frozen_key_rows(rows.take([3, 1]), columns))
    selected = OutputRead(local.store.project_root).select(
        ReadLayout(schema, saved._backing.data.rows), keys
    )
    assert pa.array(selected["key"]).type == physical
    assert pa.array(selected["key"]).cast(integer).to_pylist() == [None, minimum]
    assert pa.array(selected["other"]).to_pylist() == [None, "z"]
    assert selected["value"].tolist() == [20, 40]


def test_arrow_selection_rejects_missing_and_duplicate_requested_keys(
    local: LocalFrameServices,
) -> None:
    from marivo.analysis.core.frozen_calendar import frozen_key_rows
    from marivo.analysis.materialization.graph_reading import ReadLayout

    saved = ordinary(local).persist()
    rows = saved.compute()._backing.data.copy_rows()
    schema = saved._backing.data.schema
    columns = schema.axes[0].columns
    rows["key"] = pd.Series([2, 2, 0], dtype="int64[pyarrow]")
    backing = rewrite_primary(saved, rows)
    reader = OutputRead(local.store.project_root)
    layout = ReadLayout(schema, backing.data.rows)
    with pytest.raises(AnalysisError, match="duplicate selected"):
        reader.select(layout, set(frozen_key_rows(rows.take([0]), columns)))
    with pytest.raises(AnalysisError, match="does not resolve"):
        reader.select(layout, {((columns[0].dtype, 99),)})


@pytest.mark.parametrize("missing", ["primary", "attachment"])
def test_missing_preview_payload_has_typed_cause_and_never_affects_memory_or_audit(
    local: LocalFrameServices,
    missing: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    memory = calendar(local, 128)
    saved = memory.persist()
    backing = saved._backing.data
    handle = backing.rows if missing == "primary" else backing.attachments[0].rows
    (local.store.project_root / handle.receipt.project_relative_path / "data.parquet").unlink()
    before = counts(local)
    for read in (lambda: saved.show(n=1), saved.compute, saved.to_pandas):
        with pytest.raises(MaterializationError) as caught:
            read()
        assert isinstance(caught.value.__cause__, FileNotFoundError)
        assert caught.value.stage == "frame.read"
        assert caught.value.repair.help_target.canonical_id == "analysis_frame.compute"
        assert "original data files and exact receipts" in caught.value.repair.action
    saved.show(n=0)
    saved.artifact.show()
    assert saved.persist() is saved
    assert memory.compute() is memory
    assert len(memory.to_pandas()) == 128
    memory.show(n=1)
    assert "omitted by n=0" in capsys.readouterr().out
    assert counts(local) == before
