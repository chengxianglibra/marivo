"""Store15 Frame protocol, source-free lifecycle, sequence and atomic publication."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
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
from marivo.analysis.dsl.population import Population
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
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import DslCaseFactory


@pytest.fixture
def local(tmp_path: Path) -> LocalFrameServices:
    return DatasetRuntime.create(tmp_path, "saved-frames", timezone="UTC").local


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
            "nominal_ordinal": pd.Series(
                [point.date().toordinal() - 1 for point in starts], dtype="int64[pyarrow]"
            ),
            **{
                name: pd.Series(
                    [point.isoformat(timespec="microseconds") for point in values],
                    dtype="string[pyarrow]",
                )
                for name, values in (
                    ("nominal_start_civil", starts),
                    ("nominal_end_civil", ends),
                    ("actual_start_civil", starts),
                    ("actual_end_civil", ends),
                )
            },
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
    directory = directory.loc[
        :, [column.slot_id for column in calendar_directory_schema(axis).columns]
    ]
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


def period_endpoints(local: LocalFrameServices) -> tuple[AnalysisFrame, AnalysisFrame]:
    from marivo._temporal import builtin_grain
    from marivo.analysis.core.frame_calendar import freeze_calendar
    from marivo.analysis.dsl.temporal import exact_scope

    current = exact_scope("2027-01-01", "2027-03-01", "UTC").by(builtin_grain("month"))
    baseline = current.shift(CalendarCorrespondence("years", -1))
    frames = []
    for axis, amounts, identity in (
        (current, (180, 30), "period-current"),
        (baseline, (120, 0), "period-baseline"),
    ):
        frozen = freeze_calendar(
            axis._binding,
            token="period",
            name="month",
            key=DataColumn("bucket", "bucket", ScalarDataType("int64"), False),
        )
        value = DataColumn("amount", "amount", ScalarDataType("int64"), False, "CNY")
        schema = DataSchema((frozen.axis,), (value,), value_paths=(("amount",),))
        rows = pd.DataFrame(
            {
                "bucket": pd.Series(range(2), dtype="int64[pyarrow]"),
                "amount": pd.Series(amounts, dtype="int64[pyarrow]"),
            }
        )
        frames.append(
            AnalysisFrame._from_memory(
                local,
                CompletedFrameResult(
                    CompletedFrameData(schema, rows, frozen.attachments),
                    replace(metadata(), result_id=identity),
                ),
            )
        )
    return frames[0], frames[1]


def test_comparison_preview_closes_direct_and_fact_references(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.core.frame_structure import coordinate_attachments

    current, baseline = period_endpoints(local)
    report = current.compare({"base": baseline}).compute()
    literal = report.select({"constant": 1}).compute()
    assert literal._schema.comparisons[0].values == ()
    nested = report.align(report).compute()
    assert all(fact.namespace for fact in nested._schema.comparisons)
    for memory in (baseline, report, literal, nested):
        saved = memory.persist()
        value = manifest(saved)
        assert decode(encode(value), ArtifactManifest) == value
        prefix = local.preview(saved._backing, 1)
        assert len(prefix.rows) == 1
        assert {part.name for part in prefix.attachments} == set(
            coordinate_attachments(saved._schema)
        )
        assert prefix.schema.comparisons == memory._schema.comparisons
        exported = saved.to_pandas()
        assert exported.attrs["comparisons"] == memory.to_pandas().attrs["comparisons"]
        if exported.attrs["comparisons"]:
            exported.attrs["comparisons"][0]["alias"] = "changed"
            assert saved.to_pandas().attrs["comparisons"][0]["alias"] == "base"

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("zero preview read a required payload")

    with monkeypatch.context() as patch:
        patch.setattr(OutputRead, "_open", forbidden)
        assert local.preview(saved._backing, 0).attachments == ()
        saved.show(n=0)


def test_required_comparison_map_rejects_corrupt_payload(local: LocalFrameServices) -> None:
    from marivo.analysis.core.artifact_data import ParquetHandle
    from marivo.analysis.core.data_schema import CalendarComparisonBinding
    from marivo.analysis.materialization.contracts import FileEntry
    from marivo.analysis.materialization.storage import _manifest_bytes, payload_digest
    from marivo.analysis.materialization.terminal_storage import physical_schema

    current, baseline = period_endpoints(local)
    saved = current.compare({"base": baseline}).persist()
    value = manifest(saved)
    binding = value.data.schema.comparisons[0].binding
    assert isinstance(binding, CalendarComparisonBinding)
    part = next(part for part in value.data.attachments if part.name == binding.mapping_name)
    directory = local.store.project_root / part.rows.receipt.project_relative_path
    rows = pq.read_table(directory / "data.parquet").to_pandas(types_mapper=pd.ArrowDtype)
    rows[part.schema.values[0].slot_id] = pd.Series([999, 1], dtype="int64[pyarrow]")
    pq.write_table(
        pa.Table.from_pandas(
            rows, schema=physical_schema(part.schema), preserve_index=False
        ).replace_schema_metadata(None),
        directory / "data.parquet",
        version="2.6",
    )
    entry = FileEntry("data.parquet", (directory / "data.parquet").stat().st_size)
    file_manifest = _manifest_bytes((entry,))
    (directory / "manifest.json").write_bytes(file_manifest)
    receipt = replace(
        part.rows.receipt,
        file_manifest=(entry,),
        realized_byte_count=entry.size_bytes + len(file_manifest),
    )
    handle = ParquetHandle(receipt, payload_digest(local.store.project_root, directory, receipt))
    corrupt = replace(
        value,
        data=replace(
            value.data,
            attachments=tuple(
                replace(item, rows=handle) if item.name == part.name else item
                for item in value.data.attachments
            ),
        ),
    )
    with local.store._write() as conn:
        conn.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (encode(corrupt), value.artifact_id),
        )
    backing = local.open_frame(value.artifact_id)
    for consume in (lambda: local.read_complete(backing), lambda: local.preview(backing, 1)):
        with pytest.raises(AnalysisError, match="targets resolving in the baseline directory"):
            consume()
    assert local.preview(backing, 0).attachments == ()
    local.artifact(value.artifact_id).show()


def counts(local: LocalFrameServices) -> tuple[int, int]:
    with local.store._read() as conn:
        return (
            conn.execute("SELECT count(*) FROM analysis_action_runs").fetchone()[0],
            conn.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0],
        )


def test_correlation_is_ordinary_typed_frame_after_cold_recovery(local: LocalFrameServices) -> None:
    """C10: only complete keys and ordinary float/int/NA outputs survive cold read."""
    import subprocess
    import sys

    group = DataColumn("group", "group", ScalarDataType("string"), False)
    sample = DataColumn("sample", "sample", ScalarDataType("int64"), False)
    schema = DataSchema(
        (DataAxis("group", (group,), name="group"), DataAxis("sample", (sample,), name="sample")),
        (
            DataColumn("x", "x", ScalarDataType("int64"), True),
            DataColumn("y", "y", ScalarDataType("int64"), True),
        ),
        value_paths=(("x",), ("y",)),
    )
    frame = AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(
            CompletedFrameData(
                schema,
                pd.DataFrame(
                    {
                        "group": ["B", "A", "B"],
                        "sample": [1, 2, 3],
                        "x": [1, None, 2],
                        "y": [1, 3, 2],
                    },
                    dtype=object,
                ),
            ),
            metadata(),
        ),
    )
    result = frame.correlate(
        frame["x"], frame["y"], method="pearson", by=(frame.axes[0],)
    ).compute()
    saved = result.persist()
    assert saved.output_names == ("coefficient", "n_pairs")
    assert saved.presence_names == ()
    assert tuple(column.dtype for column in saved._schema.values) == (
        ScalarDataType("float64"),
        ScalarDataType("int64"),
    )
    assert manifest(saved).data.schema == saved._schema
    script = """
import os, sys
from unittest.mock import patch
import pandas as pd
import pyarrow as pa
import marivo.analysis as mv
from marivo.analysis.materialization import frame_evaluation
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
os.environ["MARIVO_PROJECT_ROOT"] = sys.argv[1]
def forbidden(*args, **kwargs):
    raise AssertionError("cold correlation result attempted a source/model read")
with patch.object(Session, "catalog", property(forbidden)), patch.object(SourceSession, "__init__", forbidden), patch.object(frame_evaluation, "open_query_sources", forbidden):
    session = mv.session.resume(sys.argv[2])
    saved = session.persisted_frame(sys.argv[3])
    assert saved.output_names == ("coefficient", "n_pairs")
    assert saved.presence_names == ()
    table = saved.to_pandas()
    assert len(table.columns) == 3
    assert table.iloc[:, 0].tolist() == ["A", "B"]
    assert pa.array(table["value", "coefficient"]).type == pa.float64()
    assert pa.array(table["value", "n_pairs"]).type == pa.int64()
    assert table["value", "coefficient"].isna().tolist() == [True, False]
    assert abs(table["value", "coefficient"].iloc[1] - 1.0) < 1e-12
    assert table["value", "n_pairs"].tolist() == [0, 2]
    before = len(session.runs().items)
    filtered = saved.where(saved["n_pairs"] >= 2)
    derived = filtered.select({"twice": filtered["coefficient"] * 2}).compute()
    assert abs(derived.to_pandas()["value", "twice"].iloc[0] - 2.0) < 1e-12
    copied = derived.persist()
    pd.testing.assert_frame_equal(copied.to_pandas(), derived.to_pandas())
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
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
    )
    assert cold.returncode == 0, cold.stdout + cold.stderr


def test_sole_protocol_and_exact_closed_fields(local: LocalFrameServices) -> None:
    saved = ordinary(local).persist()
    value = manifest(saved)
    assert FORMAT == "marivo.frame_output/v4"
    assert local.store.layout.generation == 15
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
        .align(calendar(local, 2, offset=10), match=CalendarCorrespondence("ordinal"), how="outer")
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


@pytest.mark.parametrize("generation", [11, 12, 13, 14])
def test_old_only_store_rejected_without_any_byte_changes(tmp_path: Path, generation: int) -> None:
    old = tmp_path / f".marivo/analysis/generations/v{generation}/session_store.db"
    old.parent.mkdir(parents=True)
    with sqlite3.connect(old) as conn:
        conn.execute(f"PRAGMA user_version={generation}")
    before = old.read_bytes()
    for constructor in (
        lambda: SessionStore(tmp_path),
        lambda: SessionStore.open_existing(tmp_path),
    ):
        with pytest.raises(IntegrityError, match="generation"):
            constructor()
    assert old.read_bytes() == before
    assert not (old.parent.parent / "v15").exists()


def test_missing_unconsumed_endpoint_is_not_a_preview_read(local: LocalFrameServices) -> None:
    paired = (
        calendar(local, 3)
        .align(calendar(local, 2), match=CalendarCorrespondence("ordinal"), how="outer")
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
        .align(calendar(local, 2), match=CalendarCorrespondence("ordinal"), how="outer")
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


@pytest.mark.parametrize("generation", [11, 12, 13, 14])
def test_every_old_generation_entry_preserves_old_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generation: int
) -> None:
    import marivo.analysis as mv

    monkeypatch.chdir(tmp_path)
    old = tmp_path / f".marivo/analysis/generations/v{generation}/session_store.db"
    old.parent.mkdir(parents=True)
    with sqlite3.connect(old) as conn:
        conn.execute(f"PRAGMA user_version={generation}")
    payload = old.parent / "sessions/original/artifacts/original/primary/data.parquet"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"immutable old-generation payload")
    before = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    attempts = (
        lambda: mv.session.create("original", timezone="UTC"),
        lambda: mv.session.create("renamed", timezone="UTC"),
        lambda: mv.session.resume("original"),
        lambda: mv.session.resume("session_original"),
        lambda: mv.session.inspect("original"),
        lambda: mv.session.reconcile_run(session_id="session_original", run_id="run_original"),
        lambda: SessionStore.open_existing(tmp_path),
    )
    for attempt in attempts:
        with pytest.raises(AnalysisError):
            attempt()
    assert mv.session.recent().items == ()
    after = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not (old.parent.parent / "v15").exists()


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
        saved.compare({"base": saved}, on={"base": ((saved.axes[0], saved.axes[0]),)})
        if kind == "ordinary"
        else saved.compare({"base": saved}, match={"base": CalendarCorrespondence("ordinal")})
    ).compute()
    assert compared.to_pandas()[("value", "base.absolute.amount")].tolist() == (
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
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import frame_evaluation
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
os.environ["MARIVO_PROJECT_ROOT"] = sys.argv[1]
def forbidden(*args, **kwargs):
    raise AssertionError("cold stored derive attempted source/model/calendar reconstruction")
with patch.object(Session, "catalog", property(forbidden)), patch.object(SourceSession, "__init__", forbidden), patch.object(frame_calendar, "freeze_calendar", forbidden), patch.object(frame_evaluation, "open_query_sources", forbidden):
    session = mv.session.resume(sys.argv[2])
    saved = session.persisted_frame(sys.argv[3])
    before = len(session.runs().items)
    derived = saved.where(saved["amount"] > 0)
    result = derived.top(2, by=derived["amount"]).compute()
    assert result.to_pandas()[("value", "amount")].tolist() == [2, 1]
    compared = (saved.compare({"base": saved}, on={"base": ((saved.axes[0], saved.axes[0]),)})
                if sys.argv[4] == "ordinary" else saved.compare({"base": saved}, match={"base": mv.ordinal()})).compute()
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
    from marivo._definition.graph import DefinitionNode
    from marivo._definition.observations import ObservationBinding
    from marivo.analysis.core.frame_source_facts import captured_source_scopes
    from marivo.analysis.dsl import frame as frame_module
    from marivo.analysis.materialization import frame_evaluation
    from marivo.analysis.session.core import Session
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    region = case.catalog.fields.get("sales.customer.region")
    source = case.session.observe(
        {"sales": case.catalog.metrics.get("sales.revenue")},
        where=region == "east",
        by=mv.product(
            case.session.time_scope(start="2026-08-01", end="2026-09-01"),
            mv.coordinates(by=(region,), keys=(("east",), ("south",), ("west",))),
        ),
    )
    raw_scopes = captured_source_scopes(source._backing.root.graph)
    assert all(
        value in " ".join(raw_scopes)
        for value in ("sales.customer.region", "east", "south", "west")
    )
    memory = source.compute()
    saved = memory.persist()
    fixed_pending = tuple(fixed.select({"fixed": fixed["sales"]}) for fixed in (memory, saved))
    deep = source
    for _ in range(1100):
        deep = deep.select({"sales": deep["sales"]})
    shared = deep.align(deep, on=tuple((axis, axis) for axis in deep.axes))
    calls: list[int] = []
    capture = frame_module.scope_summary

    def observed(root: DefinitionNode, observation: ObservationBinding | None = None) -> str:
        calls.append(id(root))
        return capture(root, observation)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("pending metadata show read source/Catalog/schema/rows/history")

    monkeypatch.setattr(frame_module, "scope_summary", observed)
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
        assert "scope:" in card and "PopulationProduct factors=2; joint tuples preserved" in card
        assert all(value in card for value in ("sales.customer.region", "east", "south", "west"))
        assert "period=[" in card and "2026-08-01" in card and "2026-09-01" in card
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
    from marivo._definition.graph import DefinitionNode
    from marivo._definition.observations import ObservationBinding
    from marivo.analysis.core.frame_source_facts import captured_source_scopes
    from marivo.analysis.dsl import frame as frame_module
    from marivo.analysis.materialization import frame_evaluation
    from marivo.analysis.session.core import Session
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    region = case.catalog.fields.get("sales.customer.region")
    members = case.session.members(
        case.catalog.entities.get("sales.customer"), where=region == "east"
    )
    read = case.session.read(members, {"region": region})
    selected = read.select({"selected_region": read["region"]})
    instant = case.session.instant("2026-08-01T00:00:00Z")
    anchored = case.session.frame(
        case.session.members(case.catalog.entities.get("sales.customer"), at=instant)
    ).reduce({"rows": mv.row_count()})

    capture = frame_module.scope_summary
    graph = read._backing.root.graph
    raw_scopes = captured_source_scopes(graph)
    read_scope = capture(graph.root, graph.signature.observation)
    observed: list[str] = []

    def capture_current(root: DefinitionNode, observation: ObservationBinding | None = None) -> str:
        summary = capture(root, observation)
        observed.append(summary)
        return summary

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("pending metadata show prepared or executed a source")

    monkeypatch.setattr(frame_module, "scope_summary", capture_current)
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
    assert observed == [read_scope, read_scope, read_scope]
    assert capture(graph.root, graph.signature.observation) == read_scope
    assert captured_source_scopes(graph) == raw_scopes
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


def interval(
    local: LocalFrameServices, start: int, end: int, *, zone: str = "UTC"
) -> AnalysisFrame:
    from marivo._definition.types import IntervalCoordinate
    from marivo._temporal import anchor_from_datetime
    from marivo.analysis.core.frame_calendar import freeze_interval

    frozen = freeze_interval(
        IntervalCoordinate(
            "interval",
            anchor_from_datetime(datetime(2024, 1, start, tzinfo=timezone.utc)),
            anchor_from_datetime(datetime(2024, 1, end, tzinfo=timezone.utc)),
            zone,
        ),
        token="interval",
        name="interval",
        key=DataColumn("key", "interval", ScalarDataType("int64"), False),
    )
    schema = DataSchema(
        (frozen.axis,),
        (DataColumn("amount", "amount", ScalarDataType("int64"), True),),
        value_paths=(("amount",),),
    )
    rows = pd.DataFrame(
        {
            "key": pd.Series([0], dtype="int64[pyarrow]"),
            "amount": pd.Series([5], dtype="int64[pyarrow]"),
        }
    )
    return AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(CompletedFrameData(schema, rows, (frozen.directory,)), metadata()),
    )


def test_interval_directory_presence_and_pairing_cold_recovery(local: LocalFrameServices) -> None:
    import subprocess
    import sys

    first, second = interval(local, 1, 4), interval(local, 2, 5)
    before = counts(local)
    outer = first.align(second, how="outer").compute()
    assert counts(local) == before
    selected = outer.where(outer["left.amount"] > 0)
    sparse = selected.select({"amount": selected["left.amount"]}).compute().persist()
    saved = first.persist()
    paired = first.align(second, match=CalendarCorrespondence("ordinal")).compute().persist()
    assert len(sparse._backing.data.attachments) == 1
    assert len(paired._backing.data.attachments) == 3
    script = """
import os, sys
from unittest.mock import patch
import marivo.analysis as mv
from marivo.analysis.core import frame_calendar
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import frame_evaluation
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
os.environ["MARIVO_PROJECT_ROOT"] = sys.argv[1]
def forbidden(*args, **kwargs):
    raise AssertionError("cold interval recovery attempted source/model reconstruction")
with patch.object(Session, "catalog", property(forbidden)), patch.object(SourceSession, "__init__", forbidden), patch.object(frame_calendar, "freeze_interval", forbidden), patch.object(frame_evaluation, "open_query_sources", forbidden):
    session = mv.session.resume(sys.argv[2])
    sparse, original, paired = (session.persisted_frame(value) for value in sys.argv[3:])
    before = len(session.runs().items)
    for frame, expected in ((sparse, 2), (original, 1), (paired, 1)):
        table = frame.to_pandas()
        assert len(table) == 1
        assert len(table.attrs["frozen_coordinates"][0]["rows"]) == expected
        frame.show(n=1)
    assert sparse.axes[0].role == "interval" and paired.axes[0].role == "paired"
    assert paired.to_pandas()[("presence", "left_present")].tolist() == [True]
    assert paired.to_pandas()[("presence", "right_present")].tolist() == [True]
    assert len(sparse.align(original).compute().to_pandas()) == 1
    assert len(original.align(original, match=mv.ordinal()).compute().to_pandas()) == 1
    try:
        sparse.align(original, match=mv.ordinal()).compute()
    except AnalysisError:
        pass
    else:
        raise AssertionError("ordinal admitted the filtered two-interval directory")
    assert len(session.runs().items) == before
"""
    cold = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(local.store.project_root),
            local.session_ref,
            sparse.artifact.id,
            saved.artifact.id,
            paired.artifact.id,
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
    )
    assert cold.returncode == 0, cold.stdout + cold.stderr


def test_interval_attachment_rejects_invalid_bounds_after_receipt_validation(
    local: LocalFrameServices,
) -> None:
    from marivo.analysis.core.artifact_data import ParquetHandle
    from marivo.analysis.materialization.contracts import FileEntry
    from marivo.analysis.materialization.storage import _manifest_bytes, payload_digest
    from marivo.analysis.materialization.terminal_storage import physical_schema

    saved = interval(local, 1, 4).persist()
    value = manifest(saved)
    part = value.data.attachments[0]
    directory = local.store.project_root / part.rows.receipt.project_relative_path
    rows = pq.read_table(directory / "data.parquet").to_pandas(types_mapper=pd.ArrowDtype)
    rows["end"] = rows["start"]
    pq.write_table(
        pa.Table.from_pandas(
            rows, schema=physical_schema(part.schema), preserve_index=False
        ).replace_schema_metadata(None),
        directory / "data.parquet",
        version="2.6",
    )
    entry = FileEntry("data.parquet", (directory / "data.parquet").stat().st_size)
    file_manifest = _manifest_bytes((entry,))
    (directory / "manifest.json").write_bytes(file_manifest)
    receipt = replace(
        part.rows.receipt,
        file_manifest=(entry,),
        realized_byte_count=entry.size_bytes + len(file_manifest),
    )
    handle = ParquetHandle(receipt, payload_digest(local.store.project_root, directory, receipt))
    corrupt = replace(value, data=replace(value.data, attachments=(replace(part, rows=handle),)))
    with local.store._write() as conn:
        conn.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (encode(corrupt), value.artifact_id),
        )
    backing = local.open_frame(value.artifact_id)
    with pytest.raises(AnalysisError, match="nonempty half-open intervals"):
        local.read_complete(backing)
    with pytest.raises(AnalysisError, match="nonempty half-open intervals"):
        local.preview(backing, 1)


def test_interval_never_squeezes_or_bridges_calendar_and_scalar(local: LocalFrameServices) -> None:
    scope = interval(local, 1, 2)
    daily = calendar(local, 1)
    from marivo.analysis.dsl.column import row_count

    scalar = scope.reduce({"n": row_count()}).compute()
    assert len(scope.axes) == 1 and len(scope.to_pandas()) == 1
    assert scalar.axes == ()
    with pytest.raises(AnalysisError):
        scope.align(scalar)
    for match in (None, CalendarCorrespondence("ordinal")):
        with pytest.raises(AnalysisError):
            scope.align(daily, on=((scope.axes[0], daily.axes[0]),), match=match)


def test_stored_interval_reads_and_matches_without_current_timezone_rules(
    local: LocalFrameServices,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from zoneinfo import ZoneInfoNotFoundError

    from marivo import _timezones

    first = interval(local, 1, 4, zone="Asia/Shanghai").persist()
    second = interval(local, 2, 5, zone="Asia/Shanghai").persist()

    def unavailable(key: str) -> None:
        raise ZoneInfoNotFoundError(f"tzdata unavailable: {key}")

    monkeypatch.setattr(_timezones, "ZoneInfo", unavailable)
    rows = first.to_pandas()
    assert rows[("value", "amount")].tolist() == [5]
    directory = rows.attrs["frozen_coordinates"][0]["rows"]
    assert directory["timezone"].tolist() == ["Asia/Shanghai"]
    assert directory["start"].tolist() == [datetime(2024, 1, 1, tzinfo=timezone.utc)]
    assert directory["end"].tolist() == [datetime(2024, 1, 4, tzinfo=timezone.utc)]
    first.show(n=1)
    assert "Asia/Shanghai" in capsys.readouterr().out
    assert len(first.align(first).compute().to_pandas()) == 1
    paired = first.compare(
        {"base": second}, match={"base": CalendarCorrespondence("ordinal")}
    ).compute()
    assert paired.to_pandas()[("value", "base.absolute.amount")].tolist() == [0]
    assert paired.to_pandas()[("presence", "base.present")].tolist() == [True]


@pytest.fixture
def attribute_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    import duckdb

    import marivo.analysis as mv

    database = tmp_path / "method-facts.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE facts(id BIGINT, phase VARCHAR, region VARCHAR, channel VARCHAR, amount BIGINT)"
        )
        connection.execute(
            "INSERT INTO facts VALUES (1,'base','east','A',100),(2,'base','east','B',100),(3,'current','east','A',160),(4,'current','east','B',80)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table('facts'), primary_key=['id'])
phase = ms.dimension_column(name='phase', entity=facts, column='phase')
region = ms.dimension_column(name='region', entity=facts, column='region')
channel = ms.dimension_column(name='channel', entity=facts, column='channel')
amount = ms.measure_column(name='amount', entity=facts, column='amount', unit='USD', additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.create("method-facts", timezone="UTC")


def attribute_request(
    session: Session, *, axis: str = "channel", parents: Population | None = None
) -> AnalysisFrame:
    from marivo.analysis.dsl.attribute import attribute_endpoint

    amount = session.catalog.fields.get("sales.facts.amount")
    phase = session.catalog.fields.get("sales.facts.phase")
    return session.attribute(
        attribute_endpoint({"q": amount.sum()}, where=phase == "current"),
        attribute_endpoint({"q": amount.sum()}, where=phase == "base"),
        by=(session.catalog.fields.get("sales.facts." + axis),),
        parents=parents,
        contribution=True,
    )


def test_real_method_facts_propagate_through_fixed_and_pending_inputs(
    attribute_session: Session,
) -> None:
    pending = attribute_request(attribute_session)
    with pytest.raises(AnalysisError, match="compute"):
        _ = pending.method_facts
    memory = pending.compute()
    original = memory.method_facts
    assert len(original) == 1
    assert original[0].origin_result_id == memory._backing.metadata.result_id
    assert original[0].parents[0].metrics[0].method.change.value == 40
    saved = memory.persist()
    assert saved.method_facts == original
    assert saved.compute().method_facts == original
    assert saved._backing.metadata == memory._backing.metadata
    before = counts(memory._local)
    for fixed in (memory, saved):
        assert fixed.to_pandas()[("value", "contribution.q")].tolist() == [60, -20]
        total = fixed.reduce({"total": fixed["contribution.q"].sum()}).compute()
        positive = fixed.where(fixed["contribution.q"] > 0).compute()
        subtotal = positive.reduce({"total": positive["contribution.q"].sum()}).compute()
        assert total.to_pandas()[("value", "total")].tolist() == [40]
        assert subtotal.to_pandas()[("value", "total")].tolist() == [60]
        assert positive.to_pandas()[("value", "contribution_share.q")].tolist() == [1.5]
        renamed = fixed.select({"doubled": fixed["contribution.q"] * 2})
        empty = renamed.top(0, by=renamed["doubled"]).compute()
        combined = fixed.align(fixed, on=((fixed.axes[0], fixed.axes[0]),)).compute()
        compared = renamed.compare(
            {"base": renamed}, on={"base": ((renamed.axes[0], renamed.axes[0]),)}
        ).compute()
        for derived in (total, positive, subtotal, empty, combined, compared):
            assert derived.method_facts == original
            assert derived._backing.metadata.result_id != original[0].origin_result_id
        assert total.axes == () and total.presence_names == ()
        assert empty.to_pandas().empty
    assert counts(memory._local) == before
    derived_pending = pending.select({"kept": pending["contribution.q"]}).compute()
    assert (
        derived_pending.method_facts[0].origin_result_id
        != derived_pending._backing.metadata.result_id
    )
    shared = pending.align(pending, on=((pending.axes[0], pending.axes[0]),)).compute()
    assert len(shared.method_facts) == 1


def test_expression_history_is_folded_at_each_producer_and_source_parameter(
    attribute_session: Session,
) -> None:
    session = attribute_session
    region = session.catalog.fields.get("sales.facts.region")
    b = attribute_request(session, axis="region").compute()
    d = attribute_request(session, axis="region").compute()

    def next_request(fixed: AnalysisFrame) -> AnalysisFrame:
        selected = fixed.where(fixed["contribution.q"] > 0)
        parents = session.population_from(selected, on=((selected.axes[0], region),))
        return attribute_request(session, parents=parents)

    a, c = next_request(b), next_request(d)
    joined = a.align(c, on=tuple(zip(a.axes, c.axes, strict=True))).compute()
    facts = joined.method_facts
    assert len(facts) == 4
    assert facts[1] == b.method_facts[0]
    assert facts[3] == d.method_facts[0]
    assert len({item.origin_result_id for item in facts}) == 4
    assert all(item.origin_result_id != joined._backing.metadata.result_id for item in facts)
    shared = a.align(a, on=tuple(zip(a.axes, a.axes, strict=True))).compute()
    assert len(shared.method_facts) == 2 and shared.method_facts[1] == b.method_facts[0]
    ancestor = attribute_request(session, axis="region")
    selected = ancestor.where(ancestor["contribution.q"] > 0)
    domain = session.population_from(selected, on=((selected.axes[0], region),))
    observed = session.observe(
        {"q": session.catalog.fields.get("sales.facts.amount").sum()}, by=domain
    ).compute()
    assert len(observed.method_facts) == 1
    assert observed.method_facts[0].origin_result_id != observed._backing.metadata.result_id
    # A pending producer may itself consume a pending ancestor; collection order is not execution order.
    nested = next_request(ancestor).compute()
    assert len(nested.method_facts) == 2
    assert nested.method_facts[0].origin_result_id == nested._backing.metadata.result_id


def test_method_only_reads_and_lazy_data_paths_have_independent_damage_boundaries(
    attribute_session: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    saved = attribute_request(attribute_session).persist()
    local = saved._local
    full = saved.method_facts
    descriptor = manifest(saved).method_facts[0]
    original = OutputRead.read

    def forbid_method(self, layout, request):
        if layout.handle == descriptor.payload.rows:
            raise AssertionError("unexpected historical method payload read")
        return original(self, layout, request)

    with monkeypatch.context() as patch:
        patch.setattr(OutputRead, "read", forbid_method)
        saved.show(n=0)
        saved.show(n=1)
        saved.artifact.show()
        assert local.open_frame(saved.artifact.id) == saved._backing
        assert saved.persist() is saved
        assert len(saved.to_pandas()) == 2
        _ = saved.select({"q": saved["contribution.q"]})
    assert ".method_facts" in capsys.readouterr().out
    primary = manifest(saved).data.rows

    def forbid_primary(self, layout, request):
        if layout.handle == primary:
            raise AssertionError("method-only getter read current rows")
        return original(self, layout, request)

    with monkeypatch.context() as patch:
        patch.setattr(OutputRead, "read", forbid_primary)
        assert saved.method_facts == full
    target = (
        local.store.project_root
        / descriptor.payload.rows.receipt.project_relative_path
        / "data.parquet"
    )
    target.unlink()
    assert len(saved.to_pandas()) == 2
    saved.show(n=0)
    assert saved.persist() is saved
    for consume in (
        lambda: saved.method_facts,
        saved.compute,
        lambda: saved.top(0, by=saved["contribution.q"]).compute(),
    ):
        with pytest.raises(AnalysisError):
            consume()


def test_equal_origin_requires_full_typed_payload_equality(attribute_session: Session) -> None:
    from marivo.analysis.core.frame_method_facts import FrameLinearMethod, FrameLinearTerm

    memory = attribute_request(attribute_session).compute()
    facts = memory.method_facts[0]
    parent = facts.parents[0]
    metric = parent.metrics[0]
    method = metric.method
    # This is structurally and numerically valid but conflicts with the same completed origin.
    changed = replace(
        facts,
        parents=(
            replace(
                parent,
                metrics=(
                    replace(
                        metric,
                        method=FrameLinearMethod(
                            "linear",
                            method.left,
                            method.right,
                            method.change,
                            (FrameLinearTerm(1, method),),
                        ),
                    ),
                ),
            ),
        ),
    )
    other = AnalysisFrame._from_memory(
        memory._local,
        CompletedFrameResult(memory._backing.data, memory._backing.metadata, (changed,)),
    )
    with pytest.raises(AnalysisError, match="identical complete"):
        memory.align(other, on=((memory.axes[0], other.axes[0]),)).compute()


def explained_calendar(
    local: LocalFrameServices, count: int, *, interval_axis: bool = False
) -> AnalysisFrame:
    """Seed only the downstream historical-coordinate storage contract."""
    from marivo.analysis.core.frame_method_facts import (
        FrameAdditiveMethod,
        FrameMethodCoordinate,
        FrameMethodFacts,
        FrameMethodMetric,
        FrameMethodParent,
        FrameMethodSupport,
        FrameMethodValue,
    )

    current = interval(local, 1, 4) if interval_axis else calendar(local, count)
    columns = tuple(
        DataColumn(
            role,
            role + ".q",
            ScalarDataType("int64") if role != "contribution_share" else ScalarDataType("float64"),
            False,
            "1" if role == "contribution_share" else "USD",
        )
        for role in ("left", "right", "absolute", "contribution", "contribution_share")
    )
    presence = tuple(
        DataColumn(role, role, ScalarDataType("boolean"), False)
        for role in ("left_present", "right_present")
    )
    schema = DataSchema(
        current._schema.axes,
        columns,
        presence,
        value_paths=tuple(
            (role, "q")
            for role in ("left", "right", "absolute", "contribution", "contribution_share")
        ),
        presence_paths=(("left_present",), ("right_present",)),
    )
    rows = current._backing.data.copy_rows().drop(columns=["amount"])
    for column, value in zip(columns, (240, 200, 40, 40, 1.0), strict=True):
        rows[column.slot_id] = pd.Series(
            [value] * count,
            dtype="double[pyarrow]" if column.slot_id == "contribution_share" else "int64[pyarrow]",
        )
    for column in presence:
        rows[column.slot_id] = pd.Series([True] * count, dtype="bool[pyarrow]")
    part = current._backing.data.copy_attachments()[0]
    arrays = []
    for column in part.schema.columns:
        array = pa.array(part.rows[column.slot_id])
        if isinstance(column.dtype, (TimestampDataType, DurationDataType)):
            array = array.cast(pa.int64())
        arrays.append(array.to_pylist())
    snapshot = FrameMethodCoordinate(part.name, part.schema, tuple(zip(*arrays, strict=True)))
    method = FrameAdditiveMethod(
        "additive",
        FrameMethodValue(columns[0], 240),
        FrameMethodValue(columns[1], 200),
        FrameMethodValue(columns[2], 40),
        (FrameMethodSupport("r", "rows", 2, 2, 2, 2),),
    )
    key_column = schema.axes[0].columns[0]
    parents = tuple(
        FrameMethodParent(
            (FrameMethodValue(key_column, key),), (FrameMethodMetric("q", method, "defined"),)
        )
        for key in sorted(rows[key_column.slot_id].tolist())
    )
    facts = FrameMethodFacts("attribute", "calendar-original", schema, (snapshot,), 1, parents)
    return AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(
            CompletedFrameData(schema, rows, (part,)),
            replace(metadata(), result_id="calendar-original"),
            (facts,),
        ),
    )


def test_method_manifest_is_bounded_and_zero_parent_payload_is_valid(
    local: LocalFrameServices,
) -> None:
    from marivo.analysis.core.frame_method_facts import FrameMethodFacts

    small = explained_calendar(local, 2).persist()
    large = explained_calendar(local, 128).persist()
    for saved, size in ((small, 2), (large, 128)):
        value = manifest(saved)
        description = value.method_facts[0]
        assert description.parent_count == description.coordinate_row_count == size
        assert not hasattr(description, "parents") and not hasattr(description, "coordinates")
        assert description.payload.rows.receipt.realized_row_count == size + 1
        encoded = encode(value)
        assert "FrameMethodParent" not in encoded and "FrameMethodCoordinate" not in encoded
        assert saved.method_facts[0].coordinates[0].rows
    assert abs(len(encode(manifest(large))) - len(encode(manifest(small)))) < 1024
    original = explained_calendar(local, 0)
    # No selected parents is not the singleton key=() overall-parent case.
    assert original.method_facts[0].parents == ()
    saved = original.persist()
    assert saved.method_facts == original.method_facts
    assert saved.method_facts[0].coordinates[0].rows == ()
    with pytest.raises(AnalysisError):
        replace(saved._backing, method_facts=(original.method_facts[0],))
    with pytest.raises(AnalysisError):
        replace(original._backing.result, method_facts=[original.method_facts[0]])
    assert type(original.method_facts[0]) is FrameMethodFacts


@pytest.mark.parametrize("interval_axis", [False, True])
def test_reduced_saved_method_payload_is_owned_after_detached_cold_reopen(
    local: LocalFrameServices,
    interval_axis: bool,
) -> None:
    import os
    import subprocess
    import sys

    size = 1 if interval_axis else 3
    original = explained_calendar(local, size, interval_axis=interval_axis).persist()
    facts = original.method_facts
    reduced = original.reduce({"total": original["contribution.q"].sum()}).persist()
    assert reduced.axes == () and reduced.presence_names == ()
    assert reduced.method_facts == facts
    assert reduced.to_pandas()[("value", "total")].tolist() == [40 * size]
    old, new = manifest(original), manifest(reduced)
    assert (
        old.method_facts[0].payload.rows.receipt.project_relative_path
        != new.method_facts[0].payload.rows.receipt.project_relative_path
    )
    for handle in (
        old.data.rows,
        *(part.rows for part in old.data.attachments),
        *(fact.payload.rows for fact in old.method_facts),
    ):
        (local.store.project_root / handle.receipt.project_relative_path / "data.parquet").unlink()
    script = """
import sys
from pathlib import Path
from unittest.mock import patch
import marivo
import marivo.analysis as mv
from marivo.analysis.core import frame_calendar
from marivo.analysis.materialization import frame_evaluation
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
assert Path(marivo.__file__).resolve().parents[1] == Path(sys.argv[4])
def forbidden(*args, **kwargs):
    raise AssertionError('cold facts continuation attempted source/model/calendar rebuild')
with patch.object(Session, 'catalog', property(forbidden)), patch.object(SourceSession, '__init__', forbidden), patch.object(frame_calendar, 'freeze_calendar', forbidden), patch.object(frame_calendar, 'freeze_interval', forbidden), patch.object(frame_evaluation, 'open_query_sources', forbidden):
    session = mv.session.resume(sys.argv[2])
    saved = session.persisted_frame(sys.argv[3])
    facts = saved.method_facts
    assert len(facts[0].parents) == int(sys.argv[5]) and len(facts[0].coordinates[0].rows) == int(sys.argv[5])
    assert saved.axes == () and saved.presence_names == ()
    assert saved.to_pandas()[('value','total')].tolist() == [40 * int(sys.argv[5])]
    derived = saved.select({'twice': saved['total'] * 2}).compute()
    assert derived.method_facts == facts
    copied = derived.persist()
    assert copied.method_facts == facts
    assert copied.to_pandas()[('value','twice')].tolist() == [80 * int(sys.argv[5])]
print('cold-method-facts-owned')
"""
    worktree = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(local.store.project_root),
            local.session_ref,
            reduced.artifact.id,
            str(worktree),
            str(size),
        ],
        cwd=worktree,
        env={
            **os.environ,
            "PYTHONPATH": str(worktree),
            "MARIVO_PROJECT_ROOT": str(local.store.project_root),
        },
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cold-method-facts-owned" in result.stdout


def test_equal_origin_digest_does_not_skip_consumed_input_receipts(
    attribute_session: Session,
) -> None:
    memory = attribute_request(attribute_session).compute()
    first, second = memory.persist(), memory.persist()
    first_saved, second_saved = manifest(first), manifest(second)
    assert first_saved.method_facts[0].facts_digest == second_saved.method_facts[0].facts_digest
    part = second_saved.method_facts[0].payload
    (
        memory._local.store.project_root / part.rows.receipt.project_relative_path / "data.parquet"
    ).unlink()
    assert first.method_facts == memory.method_facts
    assert len(second.to_pandas()) == 2
    with pytest.raises(AnalysisError):
        first.align(second, on=((first.axes[0], second.axes[0]),)).compute()
