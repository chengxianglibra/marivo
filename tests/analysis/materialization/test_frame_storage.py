"""Store24 Frame protocol, source-free lifecycle, sequence and atomic publication."""

from __future__ import annotations

import gc
import sqlite3
import weakref
from collections.abc import Callable
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
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
    PairedDataAxis,
    SavedFrameMethodFacts,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    ExpressionFrameBacking,
    MemoryFrameBacking,
    SourceFrameBinding,
    StoredFrameBacking,
)
from marivo.analysis.core.frame_metadata import (
    EvaluationRecord,
    FrameResultMetadata,
    StoredFrameMetadata,
)
from marivo.analysis.core.frame_page import FramePeriodPage
from marivo.analysis.core.frozen_calendar import (
    FrozenKey,
    calendar_directory_schema,
    frozen_key_rows,
    read_calendar_directory,
    read_calendar_records,
)
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_sources import values
from marivo.analysis.dsl.population import Population
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import terminal_publication, terminal_store
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
    RecoveryPendingError,
)
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.graph_reading import (
    ExportRead,
    FrameReader,
    OutputRead,
    PreviewRead,
    ReadLayout,
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
from tests.shared_fixtures import DslCaseFactory, catalog_value, terminal_run, terminal_runs

# Keep operation-specific extrema/order/reduce/selection checks on one native
# timestamp unit, one duration unit, and date32. All temporal units still have
# dedicated minimum/null cold-read and export/restore coverage below.
_EXTREME_TYPED_KEY_DTYPES = (
    TimestampDataType("ns", "UTC"),
    DurationDataType("ns"),
    ScalarDataType("date"),
)


@pytest.fixture
def local(tmp_path: Path) -> LocalFrameServices:
    return DatasetRuntime.create(tmp_path, "saved-frames", timezone="UTC").local


def metadata() -> FrameResultMetadata:
    return FrameResultMetadata(
        "completed-before-publication",
        EvaluationRecord(datetime(2020, 1, 1, tzinfo=timezone.utc), (), ("literal",)),
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


def _memory_backing(frame: AnalysisFrame) -> MemoryFrameBacking:
    assert isinstance(frame._backing, MemoryFrameBacking)
    return frame._backing


def _stored_backing(frame: AnalysisFrame) -> StoredFrameBacking:
    assert isinstance(frame._backing, StoredFrameBacking)
    return frame._backing


def _stored_description(metadata: FrameResultMetadata, input_id: str) -> StoredFrameMetadata:
    described = terminal_store.describe(metadata)
    return replace(described, lineage=replace(described.lineage, input_id=input_id))


def _complete_schema(frame: AnalysisFrame) -> DataSchema:
    assert isinstance(frame._schema, DataSchema)
    return frame._schema


def manifest(frame: AnalysisFrame) -> ArtifactManifest:
    return open_manifest(
        frame._local.store, frame._local.session_ref, _stored_backing(frame).artifact_id
    )


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
            key=DataColumn("bucket", "bucket", TimestampDataType("us", "UTC"), False),
        )
        value = DataColumn("amount", "amount", ScalarDataType("int64"), False, "CNY")
        schema = DataSchema((frozen.axis,), (value,), value_paths=(("amount",),))
        rows = pd.DataFrame(
            {
                "bucket": frozen.directory.rows["bucket"],
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
    report = current.compare({"base": mv.baseline(baseline)}).compute()
    literal = report.select({"constant": 1}).compute()
    assert literal.schema.comparisons == ()
    nested = report.align(report).compute()
    assert all(fact.namespace for fact in nested._schema.comparisons)
    for memory in (baseline, report, literal, nested):
        saved = memory.persist()
        value = manifest(saved)
        assert decode(encode(value), ArtifactManifest) == value
        prefix = local.preview(_stored_backing(saved), 1)
        assert len(prefix.rows) == 1
        assert {part.name for part in prefix.attachments} <= set(
            coordinate_attachments(saved._schema)
        )
        assert all(len(part.rows) <= 2 for part in prefix.attachments)
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
        assert local.preview(_stored_backing(saved), 0).attachments == ()
        saved.show(n=0)


def test_required_comparison_map_rejects_corrupt_payload(local: LocalFrameServices) -> None:
    from marivo.analysis.core.artifact_data import ParquetHandle
    from marivo.analysis.core.data_schema import CalendarComparisonBinding
    from marivo.analysis.materialization.contracts import FileEntry
    from marivo.analysis.materialization.storage import _manifest_bytes, payload_digest
    from marivo.analysis.materialization.terminal_storage import physical_schema

    current, baseline = period_endpoints(local)
    saved = current.compare({"base": mv.baseline(baseline)}).persist()
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
    reopened = AnalysisFrame._from_stored(
        local,
        backing.data,
        artifact_id=backing.artifact_id,
        metadata=backing.metadata,
        method_facts=backing.method_facts,
    )
    for consume in (
        lambda: local.read_complete(backing),
        lambda: local.preview(backing, 1),
        lambda: _period_page(reopened, limit=1, comparison=True),
    ):
        with pytest.raises(AnalysisError, match="resolv"):
            consume()
    assert local.preview(backing, 0).attachments == ()
    AnalysisFrame._from_stored(
        local,
        backing.data,
        artifact_id=backing.artifact_id,
        metadata=backing.metadata,
        method_facts=backing.method_facts,
    ).show(n=0)


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
    assert tuple(column.dtype for column in _complete_schema(saved).values) == (
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
from tests.shared_fixtures import terminal_runs
from marivo.analysis.materialization import frame_source_preparation
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
os.environ["MARIVO_PROJECT_ROOT"] = sys.argv[1]
def forbidden(*args, **kwargs):
    raise AssertionError("cold correlation result attempted a source/model read")
with patch.object(Session, "catalog", property(forbidden)), patch.object(SourceSession, "__init__", forbidden), patch.object(frame_source_preparation, "open_query_sources", forbidden):
    session = mv.session.resume(sys.argv[2])
    saved = session.persisted_frame(sys.argv[3])
    assert saved.output_names == ("coefficient", "n_pairs")
    assert saved.presence_names == ()
    table = saved.to_pandas()
    assert len(table.columns) == 3
    assert table.iloc[:, 0].tolist() == ["A", "B"]
    assert pa.array(table["coefficient"]).type == pa.float64()
    assert pa.array(table["n_pairs"]).type == pa.int64()
    assert table["coefficient"].isna().tolist() == [True, False]
    assert abs(table["coefficient"].iloc[1] - 1.0) < 1e-12
    assert table["n_pairs"].tolist() == [0, 2]
    before = len(terminal_runs(session))
    filtered = saved.where(saved["n_pairs"] >= 2)
    derived = filtered.select({"twice": filtered["coefficient"] * 2}).compute()
    assert abs(derived.to_pandas()["twice"].iloc[0] - 2.0) < 1e-12
    copied = derived.persist()
    pd.testing.assert_frame_equal(copied.to_pandas(), derived.to_pandas())
    assert len(terminal_runs(session)) == before + 1
"""
    cold = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(local.store.project_root),
            local.session_ref,
            saved.artifact_id,
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
    )
    assert cold.returncode == 0, cold.stdout + cold.stderr


def test_sole_protocol_and_exact_closed_fields(local: LocalFrameServices) -> None:
    saved = ordinary(local).persist()
    value = manifest(saved)
    assert FORMAT == "marivo.frame_output/v13"
    assert local.store.layout.generation == 24
    assert decode(encode(value), ArtifactManifest) == value
    assert value.result_id == value.metadata.result_id
    restored = terminal_store.complete_metadata(value)
    assert replace(restored, lineage=None) == metadata()
    assert value.metadata.lineage == saved.lineage
    assert tuple(field.name for field in fields(InvocationInput)) == ("result_id",)
    run = terminal_run(local, value.run_id)
    assert run.dataset_input == InvocationInput(metadata().result_id)
    assert value.metadata.evaluated_at < datetime.fromisoformat(run.admitted_at)
    assert counts(local) == (1, 1)
    assert "display_order" not in encode(value)


@pytest.mark.parametrize("row_count", (0, 1))
def test_zero_axis_empty_select_preserves_cardinality_through_storage(
    local: LocalFrameServices, row_count: int
) -> None:
    source = values(local, columns={"value": None}, rows=(((), {"value": 7}),))
    if row_count == 0:
        source = source.where(source["value"] < 0)
    projected = source.select({})
    completed = projected.compute()
    assert completed.axes == () and completed.output_names == ()
    assert completed.presence_names == ()
    assert completed.to_pandas().shape == (row_count, 0)
    saved = completed.persist()
    backing = local.open_frame(saved.artifact_id)
    reopened = AnalysisFrame._from_stored(
        local,
        backing.data,
        artifact_id=backing.artifact_id,
        metadata=backing.metadata,
    )
    for frame in (saved, reopened, reopened.compute()):
        assert frame.to_pandas().shape == (row_count, 0)
        counted = frame.reduce(mv.row_count()).compute().to_pandas()
        assert counted["row_count"].tolist() == [row_count]


def test_memory_stored_lifecycle_and_detached_copy(local: LocalFrameServices) -> None:
    memory = ordinary(local)
    assert memory.compute() is memory
    saved = memory.persist()
    second = memory.persist()
    assert _stored_backing(saved).artifact_id != _stored_backing(second).artifact_id
    source_metadata = _memory_backing(memory).metadata
    assert _stored_backing(saved).metadata == _stored_description(source_metadata, saved._input_id)
    assert _stored_backing(second).metadata == _stored_description(
        source_metadata, second._input_id
    )
    assert _stored_backing(saved).metadata.lineage.input_id == saved._input_id
    assert _stored_backing(second).metadata.lineage.input_id == second._input_id
    assert saved.persist() is saved
    reopened = AnalysisFrame._from_stored(
        local,
        _stored_backing(saved).data,
        artifact_id=_stored_backing(saved).artifact_id,
        metadata=_stored_backing(saved).metadata,
    )
    assert reopened._input_id == saved._input_id
    loaded = saved.compute()
    assert loaded._input_id != saved._input_id
    assert isinstance(loaded._backing, MemoryFrameBacking)
    assert _stored_description(_memory_backing(loaded).metadata, saved._input_id) == (
        _stored_backing(saved).metadata
    )
    pd.testing.assert_frame_equal(saved.to_pandas(), loaded.to_pandas())
    old = manifest(saved)
    target = local.store.project_root / old.data.rows.receipt.project_relative_path / "data.parquet"
    target.unlink()
    assert saved.persist() is saved
    loaded.to_pandas()
    loaded.persist().to_pandas()
    derived = loaded.select({"twice": loaded["amount"] * 2}).compute()
    assert derived.to_pandas()["twice"].tolist() == [pd.NA, 2, 4]
    assert _complete_schema(derived).axes == _complete_schema(loaded).axes
    published = derived.persist()
    pd.testing.assert_frame_equal(published.to_pandas(), derived.to_pandas())
    with pytest.raises(AnalysisError):
        saved.compute()
    assert counts(local) == (4, 4)


def test_exports_and_attrs_are_isolated(local: LocalFrameServices) -> None:
    memory = calendar(local, 3)
    saved = memory.persist()
    for frame in (memory, saved):
        exported = frame.to_pandas()
        exported.loc[0, "amount"] = 999
        coordinates = exported.attrs["frozen_coordinates"][0]["rows"]
        assert isinstance(coordinates, pa.Table)
        copied = coordinates.to_pandas(types_mapper=pd.ArrowDtype)
        copied.iloc[0, 1] = "changed"
        exported.attrs["frozen_coordinates"][0]["rows"] = pa.table({"changed": [True]})
        again = frame.to_pandas()
        assert again.loc[0, "amount"] != 999
        assert (
            again.attrs["frozen_coordinates"][0]["rows"]
            .to_pandas(types_mapper=pd.ArrowDtype)
            .iloc[0, 1]
            == "2024-01-01"
        )
        branch = frame.select({"twice": frame["amount"] * 2}).compute()
        sibling = frame.where(frame["amount"] > 0).compute()
        exported_branch = branch.to_pandas()
        exported_branch.loc[0, "twice"] = 999
        exported_branch.attrs["frozen_coordinates"][0]["rows"] = pa.table({"changed": [True]})
        assert branch.to_pandas()["twice"].tolist() == [0, 2, 4]
        assert sibling.to_pandas()["amount"].tolist() == [1, 2]
        for independent in (frame, branch, sibling):
            exported_again = independent.to_pandas()
            assert exported_again.iloc[0, -1] != 999
            assert (
                exported_again.attrs["frozen_coordinates"][0]["rows"]
                .to_pandas(types_mapper=pd.ArrowDtype)
                .iloc[0, 1]
                == "2024-01-01"
            )


@pytest.mark.parametrize("n", [True, -1, 1.5, "1"])
def test_invalid_preview_precedes_any_metadata(
    local: LocalFrameServices, n: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = ordinary(local).persist()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid preview touched metadata")

    monkeypatch.setattr(FrameReader, "manifest", forbidden)
    with pytest.raises(AnalysisError):
        # The runtime preview value intentionally violates the declared int contract.
        saved.show(n=cast("int", n))


def test_zero_preview_and_audit_do_not_open_payloads(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = calendar(local).persist()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("metadata operation opened Parquet")

    monkeypatch.setattr(pq, "ParquetFile", forbidden)
    saved.show(n=0)
    saved.persist()
    assert saved.source_reads == ()


def test_prefix_resolves_sparse_original_keys_and_only_selected_records(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    saved = calendar(local, 128).persist()
    calls: list[int] = []
    original = OutputRead.select

    def selected(self: OutputRead, layout: ReadLayout, keys: set[FrozenKey]) -> pd.DataFrame:
        result = original(self, layout, keys)
        calls.append(len(result))
        return result

    monkeypatch.setattr(OutputRead, "select", selected)
    prefix = local.preview(_stored_backing(saved), 2)
    assert prefix.rows["bucket"].tolist() == [902159, 902142]
    assert prefix.attachments[0].rows["ordinal"].tolist() == [126, 127]
    assert calls == [2]
    saved.show(n=1)
    assert "2024-05-07" in capsys.readouterr().out
    assert counts(local) == (1, 1)


def test_sparse_records_are_not_complete_directories(local: LocalFrameServices) -> None:
    frame = calendar(local, 128)
    axis = frame._schema.axes[0]
    assert isinstance(axis, CalendarDataAxis)
    part = _memory_backing(frame).data.copy_attachments()[0]
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
        .align(calendar(local, 2, offset=10), match=CalendarCorrespondence("ordinal"))
        .compute()
    )
    saved = paired.persist()
    value = manifest(saved)
    assert decode(encode(value), ArtifactManifest) == value
    pd.testing.assert_frame_equal(
        _memory_backing(paired).data.copy_rows(), _memory_backing(saved.compute()).data.copy_rows()
    )
    prefix = local.preview(_stored_backing(saved), 1)
    assert len(prefix.rows) == 1
    assert all(len(part.rows) <= 1 for part in prefix.attachments)
    saved.show(n=1)
    assert len(saved.to_pandas().attrs["frozen_coordinates"]) == 3


def _assert_valid_minimum_temporal_ticks_survive_export_and_restore(
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
            coordinate = exported.iloc[0, 0]
            assert isinstance(coordinate, tuple)
            scalar = coordinate[0]
            assert isinstance(scalar, pa.Scalar)
            assert scalar.is_valid
            assert scalar.cast(pa.int64()).as_py() == -(2**63)
        else:
            array = pa.array(exported.iloc[:, 0])
            assert array.null_count == 1
            assert array.cast(pa.int64()).to_pylist() == [-(2**63), None]
        values = pa.array(exported["observed"])
        assert values.null_count == 1
        assert values.cast(pa.int64()).to_pylist() == [-(2**63), None]
        first_page = frame.read_page(limit=1)
        exact_key = first_page.coordinates["time"][0]
        assert isinstance(exact_key, (pa.TimestampScalar, pa.DurationScalar))
        assert exact_key.type == arrow and exact_key.cast(pa.int64()).as_py() == -(2**63)
        next_page = frame.read_page(limit=1, cursor=first_page.next_cursor)
        assert next_page.coordinates["time"] == (None,)
        assert pa.array(first_page.to_pandas()["observed"]).cast(pa.int64()).to_pylist() == [
            -(2**63)
        ]
        frame.show(n=2)


@pytest.mark.parametrize(
    "dtype",
    [
        TimestampDataType(unit, "UTC") if timestamp else DurationDataType(unit)
        for timestamp in (True, False)
        for unit in ("s", "ms", "us", "ns")
    ],
)
def test_valid_minimum_temporal_ticks_survive_export_and_restore(
    local: LocalFrameServices, dtype: TimestampDataType | DurationDataType
) -> None:
    for composite in (False, True):
        _assert_valid_minimum_temporal_ticks_survive_export_and_restore(local, dtype, composite)


@pytest.mark.parametrize(
    "point",
    [
        "publication_admitted",
        "terminal_primary_written",
        "terminal_files_published",
        "insert_artifact",
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
    assert any(run.lifecycle == "failed" for run in terminal_runs(local))


def test_lost_commit_ack_returns_original_without_evaluation(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = ordinary(local)

    def fault(event: str) -> None:
        if event == "after_commit":
            raise OSError("lost acknowledgement")

    monkeypatch.setattr(terminal_publication, "_event", fault)
    saved = memory.persist(description="Original purpose despite lost acknowledgement")
    assert saved.description == "Original purpose despite lost acknowledgement"
    assert local.open_frame(saved.artifact_id).description == saved.description
    assert counts(local) == (1, 1)
    assert _stored_backing(saved).metadata == _stored_description(
        _memory_backing(memory).metadata, saved._input_id
    )
    assert local.store.resources(local.session_ref) == ()


@pytest.mark.parametrize("commit_point", ("before_commit", "after_commit"))
def test_unknown_outcome_requires_recovery_then_explicit_resubmission(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch, commit_point: str
) -> None:
    import marivo.analysis as mv

    monkeypatch.chdir(local.store.project_root)
    store = local.store
    original = store._read
    memory = ordinary(local)

    def unavailable() -> None:
        raise OSError("readback unavailable")

    def fault(event: str) -> None:
        if event == commit_point:
            monkeypatch.setattr(store, "_read", unavailable)
            raise OSError("acknowledgement unknown")

    monkeypatch.setattr(terminal_publication, "_event", fault)
    with pytest.raises(RecoveryPendingError) as caught:
        memory.persist()
    error = caught.value
    assert error.stage == "terminal_commit_unknown"
    assert not hasattr(error, "run_ref") and not hasattr(error, "artifact_id")
    assert error.repair is not None
    assert f"mv.session.resume({local.session_ref!r})" in error.repair.action
    assert "duplicate" in error.repair.action and "changed source data" in error.repair.action
    monkeypatch.setattr(store, "_read", original)
    monkeypatch.setattr(terminal_publication, "_event", lambda event: None)
    committed_before = int(commit_point == "after_commit")
    assert counts(local) == (1, committed_before)
    if not committed_before:
        assert store.resources(local.session_ref)
    monkeypatch.setattr(Session, "_sources", lambda _: pytest.fail("recovery accessed sources"))
    recovered = mv.session.resume(local.session_ref)
    assert recovered.id == local.session_ref
    assert counts(local) == (1, committed_before)
    assert store.resources(local.session_ref) == ()
    saved = memory.persist()
    assert counts(local) == (2, committed_before + 1)
    assert _stored_backing(saved).metadata == _stored_description(
        _memory_backing(memory).metadata, saved._input_id
    )
    pd.testing.assert_frame_equal(saved.to_pandas(), memory.to_pandas())


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


@pytest.mark.parametrize("generation", [11, 12, 13, 14, 16, 23])
def test_old_store_coexists_without_any_byte_changes(tmp_path: Path, generation: int) -> None:
    old = tmp_path / f".marivo/analysis/generations/v{generation}/session_store.db"
    old.parent.mkdir(parents=True)
    with sqlite3.connect(old) as conn:
        conn.execute(f"PRAGMA user_version={generation}")
    before = old.read_bytes()
    with pytest.raises(IntegrityError, match="absent"):
        SessionStore.open_existing(tmp_path)
    assert not (old.parent.parent / "v24").exists()
    store = SessionStore(tmp_path)
    assert SessionStore.open_existing(tmp_path).db_path == store.db_path
    assert old.read_bytes() == before


def test_missing_unconsumed_endpoint_is_not_a_preview_read(local: LocalFrameServices) -> None:
    paired = (
        calendar(local, 3)
        .align(calendar(local, 2), match=CalendarCorrespondence("ordinal"))
        .compute()
        .persist()
    )
    axis = paired._schema.axes[0]
    assert isinstance(axis, PairedDataAxis)
    assert axis.right.frozen is not None
    right_name = axis.right.frozen.directory_name
    right_part = next(part for part in manifest(paired).data.attachments if part.name == right_name)
    (
        local.store.project_root / right_part.rows.receipt.project_relative_path / "data.parquet"
    ).unlink()
    # Left rows are reversed: the first row is the unmatched original ordinal 2.
    prefix = local.preview(_stored_backing(paired), 1)
    assert len(prefix.rows) == 1
    assert not next(part for part in prefix.attachments if part.name == right_name).rows.shape[0]
    paired.show(n=0)
    paired.show(n=0)
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
        .align(calendar(local, 2), match=CalendarCorrespondence("ordinal"))
        .compute()
    )
    prefix = local.preview(_stored_backing(memory.persist()), 1)
    axis = prefix.schema.axes[0]
    assert isinstance(axis, PairedDataAxis)
    assert isinstance(axis.left, CalendarDataAxis) and axis.left.frozen is not None
    assert isinstance(axis.right, CalendarDataAxis) and axis.right.frozen is not None
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


def test_temporal_cold_reader_keeps_physical_ticks_nulls_and_detaches(
    local: LocalFrameServices,
) -> None:
    import json
    import subprocess
    import sys

    temporal_values: list[tuple[DataColumn, bool, str, pa.DataType]] = []
    for timestamp in (True, False):
        for unit in ("s", "ms", "us", "ns"):
            name = f"{'timestamp' if timestamp else 'duration'}_{unit}"
            dtype = TimestampDataType(unit, "UTC") if timestamp else DurationDataType(unit)
            physical = pa.timestamp(unit, "UTC") if timestamp else pa.duration(unit)
            temporal_values.append(
                (
                    DataColumn(name, name, dtype, True),
                    timestamp,
                    unit,
                    physical,
                )
            )
    key = DataColumn("key", "key", ScalarDataType("string"), False)
    values = tuple(column for column, _, _, _ in temporal_values)
    schema = DataSchema(
        (DataAxis("axis", (key,), name="item"),),
        values,
        value_paths=tuple((column.name,) for column in values),
    )
    rows = pd.DataFrame({"key": pd.Series(["b", "a"], dtype="string[pyarrow]")})
    for column, _, _, physical in temporal_values:
        rows[column.slot_id] = pd.Series(
            pd.arrays.ArrowExtensionArray(
                pa.array([-(2**63), None], type=pa.int64()).cast(physical)
            )
        )
    saved = AnalysisFrame._from_memory(
        local, CompletedFrameResult(CompletedFrameData(schema, rows), metadata())
    ).persist()
    expected_values = [
        (column.slot_id, timestamp, unit) for column, timestamp, unit, _ in temporal_values
    ]
    script = """
import json, sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import pyarrow as pa
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization import terminal_store
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.analysis.core import frame_calendar

def forbidden(*args, **kwargs):
    raise AssertionError("cold data read attempted model/source/calendar reconstruction")
store = SessionStore.open_existing(Path(sys.argv[1]))
local = LocalFrameServices(store, sys.argv[2])
with patch.object(Session, "catalog", property(forbidden)), patch.object(SourceSession, "__init__", forbidden), patch.object(frame_calendar, "freeze_calendar", forbidden):
    specs = json.loads(sys.argv[4])
    backing = local.open_frame(sys.argv[3])
    result = local.read_complete(backing)
    rows = result.data.copy_rows()
    assert rows["key"].tolist() == ["b", "a"]
    for name, timestamp, unit in specs:
        physical = pa.timestamp(unit, "UTC") if timestamp else pa.duration(unit)
        array = pa.array(rows[name])
        assert array.type == physical
        assert array.null_count == 1
        assert array.cast(pa.int64()).to_pylist() == [-(2**63), None]
    (store.project_root / backing.data.rows.receipt.project_relative_path / "data.parquet").unlink()
    copied = local.publish(result)
    expected = terminal_store.describe(result.metadata)
    expected = replace(expected, lineage=replace(expected.lineage, input_id=copied.metadata.lineage.input_id))
    assert copied.metadata == expected
    copied_rows = local.read_complete(copied).data.copy_rows()
    for name, timestamp, unit in specs:
        physical = pa.timestamp(unit, "UTC") if timestamp else pa.duration(unit)
        copied_values = pa.array(copied_rows[name])
        assert copied_values.type == physical
        assert copied_values.null_count == 1
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
            saved.artifact_id,
            json.dumps(expected_values),
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
    import subprocess
    import sys

    saved = ordinary(local).persist()
    rows = _memory_backing(saved.compute()).data.copy_rows()
    rows["key"] = pd.Series([2, 2, 0], dtype="int64[pyarrow]")
    backing = rewrite_primary(saved, rows)
    assert len(local.preview(backing, 1).rows) == 1
    reopened = AnalysisFrame._from_stored(
        local, backing.data, artifact_id=backing.artifact_id, metadata=backing.metadata
    )
    assert reopened.read_page(limit=1).coordinates["key"] == (2,)
    with pytest.raises(MaterializationError, match="unique stage key"):
        reopened.read_page(limit=2)
    with pytest.raises(MaterializationError, match="unique stage key"):
        local.read_complete(backing)
    script = """
import sys
from pathlib import Path
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.store import SessionStore
local = LocalFrameServices(SessionStore.open_existing(Path(sys.argv[1])), sys.argv[2])
backing = local.open_frame(sys.argv[3])
try:
    local.read_complete(backing)
except MaterializationError as error:
    assert "unique stage key" in str(error)
    print("cold-duplicate-rejected")
else:
    raise AssertionError("cold storage input inherited a stale uniqueness proof")
"""
    cold = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(local.store.project_root),
            local.session_ref,
            saved.artifact_id,
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
    )
    assert cold.returncode == 0, cold.stdout + cold.stderr
    assert "cold-duplicate-rejected" in cold.stdout


@pytest.mark.parametrize("generation", [11, 12, 13, 14, 16, 23])
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
        for path in old.parent.rglob("*")
        if path.is_file()
    }
    attempts = (
        lambda: mv.session.resume("original"),
        lambda: mv.session.resume("session_original"),
        lambda: mv.session.inspect("original"),
        lambda: SessionStore.open_existing(tmp_path),
    )
    for attempt in attempts:
        with pytest.raises(AnalysisError):
            attempt()
    with pytest.warns(UserWarning, match=f"v{generation}"):
        assert mv.session.recent().items == ()
    original = mv.session.get_or_create("original", timezone="UTC")
    renamed = mv.session.get_or_create("renamed", timezone="UTC")
    assert original.id != renamed.id
    assert mv.session.resume(original.id).id == original.id
    after = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in old.parent.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert (old.parent.parent / "v24").exists()


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
        lambda: local.read_complete(_stored_backing(saved)),
        lambda: local.preview(_stored_backing(saved), 0),
        lambda: saved.persist(),
        lambda: local.committed_at(_stored_backing(saved)),
        lambda: saved.show(n=0),
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
    assert isinstance(axis, CalendarDataAxis) and axis.frozen is not None
    named = replace(
        axis,
        frozen=replace(
            axis.frozen, definition=NamedCalendarData("business", "snapshot-exact", "period")
        ),
    )
    schema = replace(_complete_schema(original), axes=(named,))
    part = _memory_backing(original).data.copy_attachments()[0]
    directory = part.rows.copy()
    directory["nominal_label"] = pd.Series(['"P1"', '"P2"', '"P3"'], dtype="string[pyarrow]")
    fixed = AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(
            CompletedFrameData(
                schema,
                _memory_backing(original).data.copy_rows(),
                (OutputAttachment(part.name, calendar_directory_schema(named), directory),),
            ),
            metadata(),
        ),
    )
    assert fixed.to_pandas(time_details=True)["day.label"].tolist() == ["P3", "P2", "P1"]
    saved = fixed.persist()
    assert saved.to_pandas(time_details=True)["day.label"].tolist() == ["P3", "P2", "P1"]
    value = decode(encode(manifest(saved)), ArtifactManifest)
    saved_axis = value.data.schema.axes[0]
    assert isinstance(saved_axis, CalendarDataAxis) and saved_axis.frozen is not None
    assert saved_axis.frozen.definition == NamedCalendarData("business", "snapshot-exact", "period")
    loaded = saved.compute()
    pd.testing.assert_frame_equal(
        _memory_backing(loaded).data.copy_attachments()[0].rows,
        _memory_backing(fixed).data.copy_attachments()[0].rows,
    )
    loaded_metadata = _memory_backing(loaded).metadata
    fixed_metadata = _memory_backing(fixed).metadata
    assert replace(loaded_metadata, lineage=fixed_metadata.lineage) == fixed_metadata
    assert replace(loaded.lineage, input_id=fixed.lineage.input_id) == fixed.lineage
    assert loaded.lineage.input_id == loaded._input_id
    assert local.preview(_stored_backing(saved), 1).attachments[0].rows[
        "nominal_label"
    ].tolist() == ['"P3"']

    mixed_directory = directory.copy()
    mixed_directory["nominal_label"] = pd.Series(["1", "true", '"1"'], dtype="string[pyarrow]")
    mixed = AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(
            CompletedFrameData(
                schema,
                _memory_backing(original).data.copy_rows(),
                (OutputAttachment(part.name, calendar_directory_schema(named), mixed_directory),),
            ),
            metadata(),
        ),
    ).to_pandas(time_details=True)["day.label"]
    assert mixed.tolist() == ["1", True, 1]
    assert [type(value) for value in mixed] == [str, bool, int]


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
        assert error.repair is not None
        target = error.repair.help_target
        assert target.canonical_id == canonical
        assert callable(marivo.help)
        marivo.help(f"{target.surface}.{target.canonical_id}")
        assert f"Target: analysis.{canonical}" in capsys.readouterr().out


def test_inherited_presence_rejects_full_and_consumed_prefix_only(
    local: LocalFrameServices,
) -> None:
    original = ordinary(local)
    schema = _complete_schema(original)
    flags = tuple(
        DataColumn(name, name, ScalarDataType("boolean"), False)
        for name in ("left_present", "left.left_present")
    )
    schema = replace(
        schema, presence=flags, presence_paths=(("left_present",), ("left", "left_present"))
    )
    rows = _memory_backing(original).data.copy_rows()
    rows["left_present"] = pd.Series([True, False, True], dtype="bool[pyarrow]")
    rows["left.left_present"] = pd.Series([True, False, False], dtype="bool[pyarrow]")
    # Row0's value is NA but its row and inherited row both exist.
    valid = AnalysisFrame._from_memory(
        local, CompletedFrameResult(CompletedFrameData(schema, rows), metadata())
    )
    saved = valid.persist()
    assert len(local.read_complete(_stored_backing(saved)).data.copy_rows()) == 3
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
    CompletedFrameData(reduced_schema, _memory_backing(original).data.copy_rows())


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
        coordinate = frame.to_pandas().iloc[0, 0]
        assert isinstance(coordinate, tuple)
        scalar = coordinate[0]
        assert isinstance(scalar, pa.Scalar)
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
        assert ("\n()\n" if count else "empty result") in output
        frame.show(n=0)
        assert "omitted by n=0" in capsys.readouterr().out


@pytest.mark.parametrize(
    "dtype",
    _EXTREME_TYPED_KEY_DTYPES,
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
    restored = saved.compute()
    for frame in (reduced, restored):
        data = _memory_backing(frame).data.copy_rows()
        values = pa.array(data["key"])
        assert values.type == physical
        assert values.cast(integer).to_pylist() == [None, minimum]
        exported = frame.to_pandas()
        assert exported["rows"].tolist() == [1, 2]
        assert exported["total"].tolist() == [pd.NA, 10]
    stored_export = saved.to_pandas()
    assert stored_export["rows"].tolist() == [1, 2]
    assert stored_export["total"].tolist() == [pd.NA, 10]

    if composite:
        import subprocess
        import sys

        script = """
import sys
from pathlib import Path
import pyarrow as pa
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization import terminal_store
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
                saved.artifact_id,
                str(minimum),
            ],
            cwd=Path(__file__).resolve().parents[3],
            text=True,
            capture_output=True,
        )
        assert cold.returncode == 0, cold.stdout + cold.stderr


@pytest.mark.parametrize(
    "dtype",
    _EXTREME_TYPED_KEY_DTYPES,
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
        restored = saved.compute()
        for frame in (top, stored_top, restored):
            data = _memory_backing(frame).data.copy_rows()
            actual = pa.array(data["key"])
            assert actual.type == physical
            assert actual.cast(integer).to_pylist() == keys
            assert data["other"].tolist() == others
            assert pa.array(data["amount"]).to_pylist() == scores
        cutoff = memory.top(2, by=memory["amount"], descending=descending).compute()
        cutoff_rows = _memory_backing(cutoff).data.copy_rows()
        assert pa.array(cutoff_rows["key"]).cast(integer).to_pylist() == keys[:2]
        assert cutoff_rows["other"].tolist() == others[:2]
        saved_cases.append((saved.artifact_id, keys, others, scores))
    script = """
import json, sys
from pathlib import Path
import pyarrow as pa
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization import terminal_store
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


def test_stored_two_selects_keep_data_and_remove_duplicate_fixed_adaptation(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.core.frame_metadata import StoredInput
    from marivo.analysis.core.unique_keys import UNKNOWN_UNIQUE_KEYS, UniqueKeys

    saved = ordinary(local).persist()
    first = saved.select({"twice": saved["amount"] * 2})
    pending = first.select({"adjusted": first["twice"] + 1})
    calls = {"construct": 0, "copy_rows": 0, "read_complete": 0}
    construct = CompletedFrameData.__init__
    copy_rows = CompletedFrameData.copy_rows
    read_complete = LocalFrameServices.read_complete

    def initialized(
        data: CompletedFrameData,
        schema: DataSchema,
        rows: pd.DataFrame,
        attachments: tuple[OutputAttachment, ...] = (),
        *,
        unique_keys: UniqueKeys = UNKNOWN_UNIQUE_KEYS,
    ) -> None:
        calls["construct"] += 1
        construct(data, schema, rows, attachments, unique_keys=unique_keys)

    def copied(data: CompletedFrameData) -> pd.DataFrame:
        calls["copy_rows"] += 1
        return copy_rows(data)

    def read(self: LocalFrameServices, backing: StoredFrameBacking) -> CompletedFrameResult:
        calls["read_complete"] += 1
        return read_complete(self, backing)

    with monkeypatch.context() as capture:
        capture.setattr(CompletedFrameData, "__init__", initialized)
        capture.setattr(CompletedFrameData, "copy_rows", copied)
        capture.setattr(LocalFrameServices, "read_complete", read)
        result = pending.compute()
    # Accepted D4's independently measured ordinary two-select route used three
    # complete-data constructions and two copy_rows calls per single stored read.
    assert 1 <= calls["construct"] < 3
    assert calls["copy_rows"] < 2
    assert calls["read_complete"] == 1
    rows = result.to_pandas()
    assert rows.iloc[:, 0].tolist() == [2, 1, 0]
    assert rows["adjusted"].tolist() == [pd.NA, 3, 5]
    assert str(rows["adjusted"].dtype) == "int64[pyarrow]"
    assert _complete_schema(result) == DataSchema(
        _complete_schema(saved).axes,
        (DataColumn("value.0", "adjusted", ScalarDataType("int64"), True, "USD"),),
        value_paths=(("adjusted",),),
    )
    assert _complete_schema(result).key_fields == ("key",)
    metadata_result = _memory_backing(result).metadata
    assert metadata_result.evaluation.operations == ("select", "select")
    assert metadata_result.evaluation.inputs == (
        StoredInput(
            saved._input_id,
            _stored_backing(saved).metadata.result_id,
            local.store.store_id,
            local.session_ref,
            saved.artifact_id,
        ),
    )
    assert metadata_result.source_reads == metadata().source_reads
    assert counts(local) == (1, 1)


@pytest.mark.release
@pytest.mark.parametrize("kind", ["ordinary", "calendar", "named_calendar"])
def test_actual_stored_inputs_derive_without_sources_and_republish_detached(
    local: LocalFrameServices,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    from marivo._definition.correspondence import CalendarCorrespondence
    from marivo._definition.query import TopValues
    from marivo.analysis.compiler.graph_plan import FrameCompute, LocalCompute
    from marivo.analysis.compiler.query_program import FrameProgram
    from marivo.analysis.core import frame_calendar
    from marivo.analysis.core.artifact_data import NamedCalendarData
    from marivo.analysis.core.frame_metadata import StoredInput
    from marivo.analysis.materialization import frame_source_preparation, graph_local_execution
    from marivo.analysis.materialization.graph_exchange import StageOutput
    from marivo.analysis.materialization.graph_local_execution import LocalKernel
    from marivo.analysis.session.core import Session
    from marivo.datasource.adapters import SourceSession

    memory = ordinary(local) if kind == "ordinary" else calendar(local, 3)
    if kind == "named_calendar":
        axis = memory._schema.axes[0]
        assert isinstance(axis, CalendarDataAxis) and axis.frozen is not None
        named = replace(
            axis,
            frozen=replace(
                axis.frozen, definition=NamedCalendarData("business", "snapshot-exact", "period")
            ),
        )
        part = _memory_backing(memory).data.copy_attachments()[0]
        directory = part.rows.copy()
        directory["nominal_label"] = pd.Series(['"P1"', '"P2"', '"P3"'], dtype="string[pyarrow]")
        memory = AnalysisFrame._from_memory(
            local,
            CompletedFrameResult(
                CompletedFrameData(
                    replace(_complete_schema(memory), axes=(named,)),
                    _memory_backing(memory).data.copy_rows(),
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

    monkeypatch.setattr(frame_source_preparation, "open_query_sources", forbidden)
    monkeypatch.setattr(Session, "catalog", property(forbidden))
    monkeypatch.setattr(SourceSession, "__init__", forbidden)
    monkeypatch.setattr(frame_calendar, "freeze_calendar", forbidden)
    before = counts(local)
    loaded = saved.compute()
    assert (
        _stored_description(_memory_backing(loaded).metadata, saved._input_id)
        == _stored_backing(saved).metadata
    )
    read_calls = 0
    row_references: list[weakref.ReferenceType[pd.DataFrame]] = []
    release_checked = False
    read_complete = LocalFrameServices.read_complete
    run_local = graph_local_execution.run_local

    def read(self: LocalFrameServices, backing: StoredFrameBacking) -> CompletedFrameResult:
        nonlocal read_calls
        result = read_complete(self, backing)
        read_calls += 1
        row_references.append(weakref.ref(result.data._rows))
        return result

    def local_kernel(
        stage: LocalCompute | FrameCompute,
        inputs: tuple[StageOutput, ...],
        kernel: LocalKernel | None = None,
    ) -> StageOutput:
        nonlocal release_checked
        if (
            isinstance(stage, FrameCompute)
            and isinstance(stage.program, FrameProgram)
            and isinstance(stage.program.operation, TopValues)
        ):
            # The first select already consumed the fixed input; observe the original
            # deserialized pandas wrapper, independently from scheduler StageOutputs.
            gc.collect()
            assert row_references and all(reference() is None for reference in row_references)
            release_checked = True
        return run_local(stage, inputs, kernel)

    if kind == "ordinary":
        monkeypatch.setattr(LocalFrameServices, "read_complete", read)
        monkeypatch.setattr(graph_local_execution, "run_local", local_kernel)
    derived = saved.select({"twice": saved["amount"] * 2})
    result = derived.top(2, by=derived["twice"]).compute()
    if kind == "ordinary":
        assert read_calls == 1 and release_checked
    assert result.to_pandas()["twice"].tolist() == [4, 2]
    captured = _memory_backing(result).metadata.evaluation.inputs
    assert captured == (
        StoredInput(
            saved._input_id,
            _stored_backing(saved).metadata.result_id,
            local.store.store_id,
            local.session_ref,
            saved.artifact_id,
        ),
    )
    assert _memory_backing(result).metadata.result_id != _stored_backing(saved).metadata.result_id
    positive = saved.where(saved["amount"] > 0).compute()
    assert positive.to_pandas()["amount"].tolist() == [1, 2]
    read_calls = 0
    compared = (
        saved.compare({"base": mv.baseline(saved, on=((saved.axes[0], saved.axes[0]),))})
        if kind == "ordinary"
        else saved.compare({"base": mv.baseline(saved, match=CalendarCorrespondence("ordinal"))})
    ).compute()
    assert compared.to_pandas()["base.absolute.amount"].tolist() == (
        [pd.NA, 0, 0] if kind == "ordinary" else [0, 0, 0]
    )
    if kind == "ordinary":
        assert read_calls == 1
        reopened_backing = local.open_frame(saved.artifact_id)
        reopened = AnalysisFrame._from_stored(
            local,
            reopened_backing.data,
            artifact_id=reopened_backing.artifact_id,
            metadata=reopened_backing.metadata,
        )
        assert reopened._input_id == saved._input_id
        read_calls = 0
        shared = saved.compare(
            {"base": mv.baseline(reopened, on=((saved.axes[0], reopened.axes[0]),))}
        ).compute()
        assert read_calls == 1
        pd.testing.assert_frame_equal(shared.to_pandas(), compared.to_pandas())
        # Ancestor and sibling values remain independent of the completed branch.
        exported = result.to_pandas()
        exported.loc[0, "amount"] = 999
        assert result.to_pandas()["twice"].tolist() == [4, 2]
        assert positive.to_pandas()["amount"].tolist() == [1, 2]
        pd.testing.assert_frame_equal(loaded.to_pandas(), memory.to_pandas())
    assert counts(local) == before
    import subprocess
    import sys

    script = """
import os, sys
from dataclasses import replace
from unittest.mock import patch
import marivo.analysis as mv
from tests.shared_fixtures import terminal_runs
from marivo.analysis.core import frame_calendar
from marivo.analysis.materialization import terminal_store
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import frame_source_preparation
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
os.environ["MARIVO_PROJECT_ROOT"] = sys.argv[1]
def forbidden(*args, **kwargs):
    raise AssertionError("cold stored derive attempted source/model/calendar reconstruction")
with patch.object(Session, "catalog", property(forbidden)), patch.object(SourceSession, "__init__", forbidden), patch.object(frame_calendar, "freeze_calendar", forbidden), patch.object(frame_source_preparation, "open_query_sources", forbidden):
    session = mv.session.resume(sys.argv[2])
    saved = session.persisted_frame(sys.argv[3])
    before = len(terminal_runs(session))
    derived = saved.where(saved["amount"] > 0)
    result = derived.top(2, by=derived["amount"]).compute()
    assert result.to_pandas()["amount"].tolist() == [2, 1]
    compared = (saved.compare({"base": mv.baseline(saved, on=((saved.axes[0], saved.axes[0]),))})
                if sys.argv[4] == "ordinary" else saved.compare({"base": mv.baseline(saved, match=mv.ordinal())})).compute()
    assert len(compared.to_pandas()) == 3
    assert len(terminal_runs(session)) == before
    copied = result.persist()
    expected = terminal_store.describe(result._backing.metadata)
    expected = replace(expected, lineage=replace(expected.lineage, input_id=copied._input_id))
    assert copied._backing.metadata == expected
    assert copied.to_pandas()["amount"].tolist() == [2, 1]
    assert len(terminal_runs(session)) == before + 1
"""
    cold = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(local.store.project_root),
            local.session_ref,
            saved.artifact_id,
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
    assert _stored_backing(copied).metadata == _stored_description(
        _memory_backing(result).metadata, copied._input_id
    )
    pd.testing.assert_frame_equal(copied.to_pandas(), result.to_pandas())
    old = manifest(saved)
    (
        local.store.project_root / old.data.rows.receipt.project_relative_path / "data.parquet"
    ).unlink()
    assert saved.persist() is saved
    saved_again = result.persist()
    assert _stored_backing(saved_again).metadata == _stored_description(
        _memory_backing(result).metadata, saved_again._input_id
    )
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
    from marivo.analysis._scope import scope_summary
    from marivo.analysis.dsl import frame as frame_module
    from marivo.analysis.materialization import frame_evaluation, frame_source_preparation
    from marivo.analysis.session.core import Session
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    region = catalog_value(case.catalog, "sales.customer.region", kind="field")
    source = case.session.observe(
        {"sales": case.catalog.get("metric:sales.revenue")},
        where=region == "east",
        by=mv.product(
            case.session.time_scope(start="2026-08-01", end="2026-09-01"),
            mv.coordinates(by=(region,), keys=(("east",), ("south",), ("west",))),
        ),
    )
    assert isinstance(source._backing, ExpressionFrameBacking)
    assert isinstance(source._backing.root, SourceFrameBinding)
    memory = source.compute()
    saved = memory.persist()
    fixed_pending = tuple(fixed.select({"fixed": fixed["sales"]}) for fixed in (memory, saved))
    deep = source
    for _ in range(1100):
        deep = deep.select({"sales": deep["sales"]})
    shared = deep.align(deep, on=tuple((axis, axis) for axis in deep.axes))
    calls: list[int] = []
    capture = scope_summary

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
    monkeypatch.setattr(frame_source_preparation, "open_query_sources", forbidden)
    for name in ("copy_rows", "preview_rows", "copy_attachments"):
        monkeypatch.setattr(CompletedFrameData, name, forbidden)
    for name in ("open_frame", "read_complete", "preview"):
        monkeypatch.setattr(LocalFrameServices, name, forbidden)
    before = tuple(terminal_runs(case.session))
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
    assert tuple(terminal_runs(case.session)) == before


def test_pending_read_label_and_derived_anchor_are_captured_without_execution(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import marivo.analysis as mv
    from marivo._definition.graph import DefinitionNode
    from marivo._definition.observations import ObservationBinding
    from marivo.analysis._scope import scope_summary
    from marivo.analysis.dsl import frame as frame_module
    from marivo.analysis.materialization import frame_evaluation, frame_source_preparation
    from marivo.analysis.session.core import Session
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    region = catalog_value(case.catalog, "sales.customer.region", kind="field")
    members = case.session.members(
        case.catalog.get("entity:sales.customer"), where=region == "east"
    )
    read = case.session.read(members, {"region": region})
    selected = read.select({"selected_region": read["region"]})
    instant = case.session.instant("2026-08-01T00:00:00Z")
    anchored = case.session.frame(
        case.session.members(case.catalog.get("entity:sales.customer"), at=instant)
    ).reduce({"rows": mv.row_count()})

    capture = scope_summary
    assert isinstance(read._backing, ExpressionFrameBacking)
    assert isinstance(read._backing.root, SourceFrameBinding)
    graph = read._backing.root.graph
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
    monkeypatch.setattr(frame_source_preparation, "open_query_sources", forbidden)
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
    anchored.show(n=0)
    card = capsys.readouterr().out
    assert "anchors:" in card and "2026-08-01T00:00:00+00:00" in card
    assert "reduce" in card and "pending" in card
    assert terminal_runs(case.session) == ()


def test_attachment_preview_filters_arrow_before_python_key_materialization(
    local: LocalFrameServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.materialization import graph_reading

    saved = calendar(local, 128).persist()
    attachment = _stored_backing(saved).data.attachments[0]
    converted: list[int] = []
    decoded: list[int] = []
    original_open = OutputRead._open
    original_keys = frozen_key_rows
    original_normalize = graph_reading.normalize_complete_frame

    class ObservedParquet:
        def __init__(self, parquet: pq.ParquetFile) -> None:
            self.parquet = parquet

        @property
        def num_row_groups(self) -> int:
            group_count: int = self.parquet.num_row_groups
            return group_count

        def iter_batches(self, *, batch_size: int) -> object:
            return self.parquet.iter_batches(batch_size=batch_size)

        def read_row_group(self, index: int, columns: list[str] | None = None) -> None:
            raise AssertionError("attachment selection decoded an entire row group")

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

    def observed_normalize(
        rows: pd.DataFrame, columns: tuple[DataColumn, ...], keys: tuple[str, ...]
    ) -> pd.DataFrame:
        converted.append(len(rows))
        return original_normalize(rows, columns, keys)

    monkeypatch.setattr(OutputRead, "_open", observed_open)
    monkeypatch.setattr(graph_reading, "frozen_key_rows", observed_keys)
    monkeypatch.setattr(graph_reading, "normalize_complete_frame", observed_normalize)
    prefix = local.preview(_stored_backing(saved), 1)
    assert prefix.attachments[0].rows["ordinal"].tolist() == [127]
    assert converted and max(converted) == 1
    assert decoded and max(decoded) == 1
    assert counts(local) == (1, 1)


@pytest.mark.parametrize(
    "dtype",
    _EXTREME_TYPED_KEY_DTYPES,
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
        ReadLayout(schema, _stored_backing(saved).data.rows), keys
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
    rows = _memory_backing(saved.compute()).data.copy_rows()
    schema = _stored_backing(saved).data.schema
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
    backing = _stored_backing(saved).data
    handle = backing.rows if missing == "primary" else backing.attachments[0].rows
    (local.store.project_root / handle.receipt.project_relative_path / "data.parquet").unlink()
    before = counts(local)
    for read in (lambda: saved.show(n=1), saved.compute, saved.to_pandas):
        with pytest.raises(MaterializationError) as caught:
            read()
        assert isinstance(caught.value.__cause__, FileNotFoundError)
        assert caught.value.stage == "frame.read"
        assert caught.value.repair is not None
        assert caught.value.repair.help_target.canonical_id == "analysis_frame.compute"
        assert "original data files and exact receipts" in caught.value.repair.action
    saved.show(n=0)
    saved.show(n=0)
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
    outer = first.align(second).compute()
    assert counts(local) == before
    selected = outer.where(outer["left.amount"] > 0)
    sparse = selected.select({"amount": selected["left.amount"]}).compute().persist()
    saved = first.persist()
    paired = first.align(second, match=CalendarCorrespondence("ordinal")).compute().persist()
    assert len(_stored_backing(sparse).data.attachments) == 1
    assert len(_stored_backing(paired).data.attachments) == 3
    script = """
import os, sys
from unittest.mock import patch
import marivo.analysis as mv
from tests.shared_fixtures import terminal_runs
from marivo.analysis.core import frame_calendar
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import frame_source_preparation
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
os.environ["MARIVO_PROJECT_ROOT"] = sys.argv[1]
def forbidden(*args, **kwargs):
    raise AssertionError("cold interval recovery attempted source/model reconstruction")
with patch.object(Session, "catalog", property(forbidden)), patch.object(SourceSession, "__init__", forbidden), patch.object(frame_calendar, "freeze_interval", forbidden), patch.object(frame_source_preparation, "open_query_sources", forbidden):
    session = mv.session.resume(sys.argv[2])
    sparse, original, paired = (session.persisted_frame(value) for value in sys.argv[3:])
    before = len(terminal_runs(session))
    for frame, expected in ((sparse, 2), (original, 1), (paired, 1)):
        table = frame.to_pandas()
        assert len(table) == 1
        assert len(table.attrs["frozen_coordinates"][0]["rows"]) == expected
        frame.show(n=1)
    assert sparse.axes[0].role == "interval" and paired.axes[0].role == "paired"
    assert paired.to_pandas()["left_present"].tolist() == [True]
    assert paired.to_pandas()["right_present"].tolist() == [True]
    assert len(sparse.align(original).compute().to_pandas()) == 1
    assert len(original.align(original, match=mv.ordinal()).compute().to_pandas()) == 1
    try:
        sparse.align(original, match=mv.ordinal()).compute()
    except AnalysisError:
        pass
    else:
        raise AssertionError("ordinal admitted the filtered two-interval directory")
    assert len(terminal_runs(session)) == before
"""
    cold = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(local.store.project_root),
            local.session_ref,
            sparse.artifact_id,
            saved.artifact_id,
            paired.artifact_id,
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
    assert rows["amount"].tolist() == [5]
    directory = rows.attrs["frozen_coordinates"][0]["rows"].to_pandas(types_mapper=pd.ArrowDtype)
    assert directory["timezone"].tolist() == ["Asia/Shanghai"]
    assert directory["start"].tolist() == [datetime(2024, 1, 1, tzinfo=timezone.utc)]
    assert directory["end"].tolist() == [datetime(2024, 1, 4, tzinfo=timezone.utc)]
    first.show(n=1)
    assert "+08:00" in capsys.readouterr().out
    assert len(first.align(first).compute().to_pandas()) == 1
    paired = first.compare(
        {"base": mv.baseline(second, match=CalendarCorrespondence("ordinal"))}
    ).compute()
    assert paired.to_pandas()["base.absolute.amount"].tolist() == [0]
    assert paired.to_pandas()["base.present"].tolist() == [True]


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
    return mv.session.get_or_create("method-facts", timezone="UTC")


def attribute_request(
    session: Session, *, axis: str = "channel", parents: Population | None = None
) -> AnalysisFrame:
    from marivo.analysis.binding.frame_attribute import attribute

    amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
    phase = catalog_value(session.catalog, "sales.facts.phase", kind="field")
    parent_axes = () if parents is None else parents
    current = session.observe({"q": amount.sum()}, by=parent_axes, where=phase == "current")
    baseline = session.observe({"q": amount.sum()}, by=parent_axes, where=phase == "base")
    compared = current.compare(
        {"base": mv.baseline(baseline, on=tuple(zip(current.axes, baseline.axes, strict=True)))}
    )
    selected = compared.select({"q": compared["base.absolute.q"]})
    return attribute(
        selected, by=(catalog_value(session.catalog, "sales.facts." + axis, kind="field"),)
    )


@pytest.mark.release
def test_real_method_facts_propagate_through_fixed_and_pending_inputs(
    attribute_session: Session,
) -> None:
    pending = attribute_request(attribute_session)
    with pytest.raises(AnalysisError, match="compute"):
        _ = pending.method_facts
    memory = pending.compute()
    original = memory.method_facts
    assert len(original) == 1
    assert (
        original[0].origin_result_id
        == terminal_store.describe(_memory_backing(memory).metadata).result_id
    )
    assert original[0].parents[0].metrics[0].method.change.value == 40
    saved = memory.persist()
    assert saved.method_facts == original
    assert saved.compute().method_facts == original
    assert _stored_backing(saved).metadata == _stored_description(
        _memory_backing(memory).metadata, saved._input_id
    )
    before = counts(memory._local)
    for fixed in (memory, saved):
        assert fixed.to_pandas()["contribution.q"].tolist() == [60, -20]
        total = fixed.reduce({"total": fixed["contribution.q"].sum()}).compute()
        positive = fixed.where(fixed["contribution.q"] > 0).compute()
        subtotal = positive.reduce({"total": positive["contribution.q"].sum()}).compute()
        partitioned = fixed.select(
            {
                "total": fixed.over(fixed["contribution.q"].sum()),
                "share": fixed["contribution.q"].share(along=fixed.axes),
            }
        ).compute()
        assert total.to_pandas()["total"].tolist() == [40]
        assert subtotal.to_pandas()["total"].tolist() == [60]
        assert positive.to_pandas()["contribution_share.q"].tolist() == [1.5]
        assert partitioned.to_pandas()["share"].tolist() == [1.5, -0.5]
        renamed = fixed.select({"doubled": fixed["contribution.q"] * 2})
        empty = renamed.top(0, by=renamed["doubled"]).compute()
        combined = fixed.align(fixed, on=((fixed.axes[0], fixed.axes[0]),)).compute()
        compared = renamed.compare(
            {"base": mv.baseline(renamed, on=((renamed.axes[0], renamed.axes[0]),))}
        ).compute()
        for derived in (total, positive, subtotal, partitioned, empty, combined, compared):
            assert derived.method_facts == original
            assert _memory_backing(derived).metadata.result_id != original[0].origin_result_id
        assert total.axes == () and total.presence_names == ()
        assert empty.to_pandas().empty
    assert counts(memory._local) == before
    derived_pending = pending.select({"kept": pending["contribution.q"]}).compute()
    assert (
        derived_pending.method_facts[0].origin_result_id
        != _memory_backing(derived_pending).metadata.result_id
    )
    shared = pending.align(pending, on=((pending.axes[0], pending.axes[0]),)).compute()
    assert len(shared.method_facts) == 1


@pytest.mark.release
def test_expression_history_is_folded_at_each_producer_and_source_parameter(
    attribute_session: Session,
) -> None:
    session = attribute_session
    region = catalog_value(session.catalog, "sales.facts.region", kind="field")
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
    assert all(
        item.origin_result_id != _memory_backing(joined).metadata.result_id for item in facts
    )
    shared = a.align(a, on=tuple(zip(a.axes, a.axes, strict=True))).compute()
    assert len(shared.method_facts) == 2 and shared.method_facts[1] == b.method_facts[0]
    ancestor = attribute_request(session, axis="region")
    selected = ancestor.where(ancestor["contribution.q"] > 0)
    domain = session.population_from(selected, on=((selected.axes[0], region),))
    observed = session.observe(
        {"q": catalog_value(session.catalog, "sales.facts.amount", kind="field").sum()}, by=domain
    ).compute()
    assert len(observed.method_facts) == 1
    assert observed.method_facts[0].origin_result_id != _memory_backing(observed).metadata.result_id
    # A pending producer may itself consume a pending ancestor; collection order is not execution order.
    nested = next_request(ancestor).compute()
    assert len(nested.method_facts) == 2
    assert nested.method_facts[0].origin_result_id == _memory_backing(nested).metadata.result_id


def test_method_only_reads_and_lazy_data_paths_have_independent_damage_boundaries(
    attribute_session: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    saved = attribute_request(attribute_session).persist()
    local = saved._local
    full = saved.method_facts
    descriptor = manifest(saved).method_facts[0]
    original = OutputRead.read

    def forbid_method(
        self: OutputRead, layout: ReadLayout, request: PreviewRead | ExportRead
    ) -> pd.DataFrame:
        if layout.handle == descriptor.payload.rows:
            raise AssertionError("unexpected historical method payload read")
        return original(self, layout, request)

    with monkeypatch.context() as patch:
        patch.setattr(OutputRead, "read", forbid_method)
        saved.show(n=0)
        saved.show(n=1)
        saved.show(n=0)
        assert local.open_frame(saved.artifact_id) == _stored_backing(saved)
        assert saved.persist() is saved
        _ = saved.select({"q": saved["contribution.q"]})
    assert ".method_facts" in capsys.readouterr().out
    primary = manifest(saved).data.rows

    def forbid_primary(
        self: OutputRead, layout: ReadLayout, request: PreviewRead | ExportRead
    ) -> pd.DataFrame:
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
        CompletedFrameResult(
            _memory_backing(memory).data, _memory_backing(memory).metadata, (changed,)
        ),
    )
    with pytest.raises(AnalysisError, match="identical complete"):
        memory.align(other, on=((memory.axes[0], other.axes[0]),)).compute()


def explained_calendar(
    local: LocalFrameServices, count: int, *, interval_axis: bool = False
) -> AnalysisFrame:
    """Seed only the downstream historical-coordinate storage contract."""
    from marivo.analysis.core.frame_method_facts import (
        FrameMethodCoordinate,
        FrameMethodFacts,
        FrameMethodParent,
        FrameMethodValue,
    )
    from tests.analysis.materialization.terminal_recovery_worker import method_result

    current = interval(local, 1, 4) if interval_axis else calendar(local, count)
    seed = method_result(current=240, baseline=200, unit="USD")
    columns = seed.data.schema.values
    presence = seed.data.schema.presence
    schema = replace(seed.data.schema, axes=_complete_schema(current).axes)
    rows = _memory_backing(current).data.copy_rows().drop(columns=["amount"])
    for column, value in zip(columns, (40, 40, 40, 1.0, 240, 200, 40), strict=True):
        rows[column.slot_id] = pd.Series(
            [value] * count,
            dtype="double[pyarrow]" if column.slot_id == "contribution_share" else "int64[pyarrow]",
        )
    for column in presence:
        rows[column.slot_id] = pd.Series([True] * count, dtype="bool[pyarrow]")
    part = _memory_backing(current).data.copy_attachments()[0]
    arrays = []
    for column in part.schema.columns:
        array = pa.array(part.rows[column.slot_id])
        if isinstance(column.dtype, (TimestampDataType, DurationDataType)):
            array = array.cast(pa.int64())
        arrays.append(array.to_pylist())
    snapshot = FrameMethodCoordinate(part.name, part.schema, tuple(zip(*arrays, strict=True)))
    key_column = schema.axes[0].columns[0]
    parents = tuple(
        FrameMethodParent(
            (FrameMethodValue(key_column, key),), seed.method_facts[0].parents[0].metrics
        )
        for key in sorted(rows[key_column.slot_id].tolist())
    )
    facts = FrameMethodFacts(
        "attribute",
        "calendar-original",
        schema,
        (snapshot,),
        1,
        parents,
        DataSchema(schema.axes[:1], ()),
        seed.method_facts[0].targets,
    )
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
        # A complete method payload intentionally violates the stored descriptor type.
        replace(
            _stored_backing(saved),
            method_facts=(cast("SavedFrameMethodFacts", original.method_facts[0]),),
        )
    with pytest.raises(AnalysisError):
        # A mutable list intentionally violates the immutable tuple payload contract.
        replace(
            _memory_backing(original).result,
            method_facts=cast("tuple[FrameMethodFacts, ...]", [original.method_facts[0]]),
        )
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
    assert reduced.to_pandas()["total"].tolist() == [40 * size]
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
from marivo.analysis.materialization import frame_source_preparation
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
assert Path(marivo.__file__).resolve().parents[1] == Path(sys.argv[4])
def forbidden(*args, **kwargs):
    raise AssertionError('cold facts continuation attempted source/model/calendar rebuild')
with patch.object(Session, 'catalog', property(forbidden)), patch.object(SourceSession, '__init__', forbidden), patch.object(frame_calendar, 'freeze_calendar', forbidden), patch.object(frame_calendar, 'freeze_interval', forbidden), patch.object(frame_source_preparation, 'open_query_sources', forbidden):
    session = mv.session.resume(sys.argv[2])
    saved = session.persisted_frame(sys.argv[3])
    facts = saved.method_facts
    assert len(facts[0].parents) == int(sys.argv[5]) and len(facts[0].coordinates[0].rows) == int(sys.argv[5])
    assert saved.axes == () and saved.presence_names == ()
    assert saved.to_pandas()['total'].tolist() == [40 * int(sys.argv[5])]
    derived = saved.select({'twice': saved['total'] * 2}).compute()
    assert derived.method_facts == facts
    copied = derived.persist()
    assert copied.method_facts == facts
    assert copied.to_pandas()['twice'].tolist() == [80 * int(sys.argv[5])]
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
            reduced.artifact_id,
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


def test_source_report_and_observed_parent_reopen_without_declarations(
    attribute_session: Session,
) -> None:
    """The real producer's facts survive process exit while source qualification does not."""
    import subprocess
    import sys

    session = attribute_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
    channel = catalog_value(session.catalog, "sales.facts.channel", kind="field")
    observed = session.observe({"q": amount.sum()}).compute().persist()
    report = observed.attribute(by=(channel,)).compute().persist()
    assert observed._derivation is not None
    script = """
import os, sys
from unittest.mock import patch
import marivo.analysis as mv
from marivo.analysis.session.core import Session
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
os.environ['MARIVO_PROJECT_ROOT'] = sys.argv[1]
def forbidden(*args, **kwargs):
    raise AssertionError('cold report attempted source/semantic loading')
with patch.object(Session, 'catalog', property(forbidden)), patch.object(SourceSession, '__init__', forbidden), patch.object(SemanticProject, 'load', forbidden):
    session = mv.session.resume(sys.argv[2])
    observed = session.persisted_frame(sys.argv[3])
    report = session.persisted_frame(sys.argv[4])
    assert observed._derivation is None
    assert observed.compute()._derivation is None
    try:
        from ibis import _
        observed.attribute(by=(_.channel,))
    except AnalysisError as error:
        assert 'retained source declaration' in error.expected
    else:
        raise AssertionError('reopened parent recovered source qualification')
    target = report.method_facts[0].parents[0].metrics[0]
    assert target.kind == 'level'
    assert target.total.value == target.observed_total.value == 440
    assert len(report.to_pandas()) == 2
    copied = report.select({'twice': report['contribution.q'] * 2}).compute().persist()
    assert copied.method_facts == report.method_facts
    assert copied.to_pandas()['twice'].sum() == 880
"""
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(session._runtime.store.project_root),
            session.id,
            observed.artifact_id,
            report.artifact_id,
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
    )
    assert process.returncode == 0, process.stdout + process.stderr


def test_readable_calendar_projection_uses_sparse_keys_and_current_order(
    local: LocalFrameServices, capsys: pytest.CaptureFixture[str]
) -> None:
    root = calendar(local, 3)
    fixed = root.top(2, by=root["amount"]).compute()
    for frame in (fixed, fixed.persist()):
        rows = frame.to_pandas(axis_names={frame.axes[0].token: "date"}, time_details=True)
        assert rows.columns.tolist() == [
            "date.start",
            "date.end",
            "date.label",
            "date.nominal_start",
            "date.nominal_end",
            "amount",
        ]
        assert rows["date.label"].tolist() == ["2024-01-01", "2024-01-02"]
        assert rows["amount"].tolist() == [2, 1]
        assert rows["date.start"].tolist() == [
            datetime(2024, 1, day, tzinfo=timezone.utc) for day in (1, 2)
        ]
        assert rows["date.end"].tolist() == [
            datetime(2024, 1, day, tzinfo=timezone.utc) for day in (2, 3)
        ]
        assert str(rows["date.start"].dtype) == "timestamp[us, tz=UTC][pyarrow]"
        frame.show(n=1)
        preview = capsys.readouterr().out.split("preview:", 1)[1]
        assert "2024-01-01" in preview and "900000" not in preview
    empty = root.top(0, by=root["amount"]).compute().to_pandas(time_details=True)
    assert empty.empty and empty.columns.tolist() == [
        "day.start",
        "day.end",
        "day.label",
        "day.nominal_start",
        "day.nominal_end",
        "amount",
    ]


def test_readable_paired_calendar_keeps_endpoint_mapping_separate_from_row_presence(
    local: LocalFrameServices,
) -> None:
    root = calendar(local, 3)
    paired = root.align(calendar(local, 2), match=CalendarCorrespondence("ordinal")).compute()
    rows = paired.to_pandas(time_details=True)
    assert rows["day.left.label"].tolist() == ["2024-01-03", "2024-01-02", "2024-01-01"]
    assert rows["day.right.label"].tolist() == [pd.NA, "2024-01-02", "2024-01-01"]
    assert rows["day.right.start"].isna().tolist() == [True, False, False]
    assert rows["left_present"].tolist() == [True, True, True]
    assert rows["right_present"].tolist() == [False, True, True]
    pd.testing.assert_frame_equal(rows, paired.persist().compute().to_pandas(time_details=True))


def test_readable_partial_calendar_retains_actual_bounds(local: LocalFrameServices) -> None:
    root = calendar(local, 1)
    backing = _memory_backing(root)
    part = backing.data.copy_attachments()[0]
    directory = part.rows.copy()
    actual = datetime(2024, 1, 1, 6, tzinfo=timezone.utc)
    directory["actual_start"] = pd.Series([actual], dtype="timestamp[us, tz=UTC][pyarrow]")
    directory["actual_start_civil"] = pd.Series(
        [actual.isoformat(timespec="microseconds")], dtype="string[pyarrow]"
    )
    frame = AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(
            CompletedFrameData(
                backing.data.schema, backing.data.copy_rows(), (replace(part, rows=directory),)
            ),
            backing.metadata,
        ),
    )
    rows = frame.to_pandas(time_details=True)
    assert rows["day.label"].tolist() == ["2024-01-01"]
    assert rows["day.start"].tolist() == [actual]
    assert rows["day.end"].tolist() == [datetime(2024, 1, 2, tzinfo=timezone.utc)]


def test_data_pages_preserve_projection_order_coordinates_and_cold_continuation(
    local: LocalFrameServices,
) -> None:
    memory = ordinary(local, 5)
    for frame in (memory, memory.persist()):
        page = frame.read_page(
            columns=("amount",), limit=2, axis_names={frame.axes[0].token: "member"}
        )
        assert page.input_id == frame.schema.input_id
        assert page.total_count == 5 and page.limit == 2 and len(page) == 2
        assert page.coordinates["key"] == (4, 3)
        assert page.to_pandas().columns.tolist() == ["member", "amount"]
        assert page.to_pandas()["amount"].tolist() == [pd.NA, 1]
        first = page.to_pandas()
        first.loc[0, "amount"] = 999
        assert page.to_pandas()["amount"].isna().tolist() == [True, False]
        assert page.to_pandas().attrs == {}
        chunks = [page.to_pandas()]
        while page.has_more:
            if isinstance(frame._backing, StoredFrameBacking):
                reopened_local = LocalFrameServices(
                    SessionStore.open_existing(local.store.project_root), local.session_ref
                )
                backing = reopened_local.open_frame(frame.artifact_id)
                frame = AnalysisFrame._from_stored(
                    reopened_local,
                    backing.data,
                    artifact_id=backing.artifact_id,
                    metadata=backing.metadata,
                    method_facts=backing.method_facts,
                )
            page = frame.read_page(
                columns=("amount",),
                limit=2,
                cursor=page.next_cursor,
                axis_names={frame.axes[0].token: "member"},
            )
            chunks.append(page.to_pandas())
        combined = pd.concat(chunks, ignore_index=True)
        expected = frame.to_pandas(axis_names={frame.axes[0].token: "member"})
        expected.attrs = {}
        pd.testing.assert_frame_equal(combined, expected)
        assert len(page) == 1 and page.next_cursor is None


def test_data_page_request_mismatch_precedes_payload_reads(
    local: LocalFrameServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = ordinary(local, 3).persist()
    page = saved.read_page(limit=1)
    other = ordinary(local, 3).persist()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid page request reached payload")

    monkeypatch.setattr(OutputRead, "_open", forbidden)
    for kwargs in (
        {"limit": True},
        {"limit": 10001},
        {"columns": ("missing",)},
        {"columns": ("amount", "amount")},
        {"columns": ["amount"]},
        {"cursor": "not a cursor"},
        {"cursor": page.next_cursor, "limit": 2},
        {"cursor": page.next_cursor, "limit": 1, "columns": ()},
        {"cursor": page.next_cursor, "limit": 1, "axis_names": {saved.axes[0].token: "alias"}},
    ):
        with pytest.raises(AnalysisError):
            saved.read_page(**kwargs)
    with pytest.raises(AnalysisError):
        other.read_page(limit=1, cursor=page.next_cursor)


def test_data_page_zero_axis_empty_projection_and_empty_result(local: LocalFrameServices) -> None:
    column = DataColumn("amount", "amount", ScalarDataType("int64"), False)
    schema = DataSchema((), (column,), value_paths=(("amount",),))
    for count in (0, 1):
        memory = AnalysisFrame._from_memory(
            local,
            CompletedFrameResult(
                CompletedFrameData(
                    schema, pd.DataFrame({"amount": pd.Series([7] * count, dtype="int64[pyarrow]")})
                ),
                metadata(),
            ),
        )
        for frame in (memory, memory.persist()):
            page = frame.read_page(columns=())
            assert len(page) == count and page.total_count == count
            assert page.coordinates == {} and page.layout == ()
            assert page.to_pandas().shape == (count, 0)
            assert not page.has_more and page.next_cursor is None


def test_calendar_and_paired_pages_keep_full_keys_and_only_page_coordinates(
    local: LocalFrameServices,
) -> None:
    root = calendar(local, 4)
    paired = root.align(calendar(local, 3), match=CalendarCorrespondence("ordinal")).compute()
    current, baseline = period_endpoints(local)
    compared = current.compare({"base": mv.baseline(baseline)}).compute()
    first_interval, second_interval = interval(local, 1, 4), interval(local, 2, 5)
    paired_interval = first_interval.align(
        second_interval, match=CalendarCorrespondence("ordinal")
    ).compute()
    interval_comparison = first_interval.compare(
        {"base": mv.baseline(second_interval, match=CalendarCorrespondence("ordinal"))}
    ).compute()
    for memory in (root, paired, compared, first_interval, paired_interval, interval_comparison):
        for frame in (memory, memory.persist()):
            page = frame.read_page(columns=(), limit=1)
            assert set(page.coordinates) == {
                key.slot_id for axis in frame.schema.axes for key in axis.keys
            }
            assert all(len(values) == 1 for values in page.coordinates.values())
            assert all(column.kind == "coordinate" for column in page.layout)
            expected = frame.to_pandas().iloc[:1, : len(page.layout)].reset_index(drop=True)
            expected.attrs = {}
            pd.testing.assert_frame_equal(page.to_pandas(), expected)


def test_small_page_detaches_large_primary_and_scans_coordinates_in_fixed_batches(
    local: LocalFrameServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.core.frame_page import detached_rows
    from marivo.analysis.materialization import graph_reading

    key = DataColumn("key", "key", ScalarDataType("int64"), False)
    text = DataColumn("text", "text", ScalarDataType("string"), False)
    amount = DataColumn("amount", "amount", ScalarDataType("int64"), False)
    schema = DataSchema(
        (DataAxis("axis", (key,), name="member"),),
        (text, amount),
        value_paths=(("text",), ("amount",)),
    )
    count = 1000
    memory = AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(
            CompletedFrameData(
                schema,
                pd.DataFrame(
                    {
                        "key": pd.Series(range(count), dtype="int64[pyarrow]"),
                        "text": pd.Series(
                            [str(i) + "x" * 1000 for i in range(count)], dtype="string[pyarrow]"
                        ),
                        "amount": pd.Series(range(count), dtype="int64[pyarrow]"),
                    }
                ),
            ),
            metadata(),
        ),
    )
    saved = memory.persist()
    monkeypatch.setattr(graph_reading, "POLICY", replace(POLICY, batch_rows=11))
    seen: list[tuple[str, ...]] = []
    original_batches = pq.ParquetFile.iter_batches

    def batches(self: pq.ParquetFile, *args: object, **kwargs: object) -> object:
        columns = kwargs.get("columns")
        if isinstance(columns, list):
            seen.append(tuple(columns))
        return original_batches(self, *args, **kwargs)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("page read decoded an entire row group")

    monkeypatch.setattr(pq.ParquetFile, "iter_batches", batches)
    monkeypatch.setattr(pq.ParquetFile, "read_row_group", forbidden)
    for frame in (memory, saved):
        page = frame.read_page(columns=("text",), limit=1)
        assert len(page) == 1 and page.to_pandas()["text"].tolist() == ["0" + "x" * 1000]
        retained = pa.array(page._rows["text"])
        assert retained.get_total_buffer_size() < 2000
        assert page.read_stats.data_rows_read == (1 if frame is memory else 11)
        assert page.read_stats.coordinate_rows_read == 0
    assert seen == [("key", "text")]
    assert (
        saved.read_page(columns=("amount",), limit=1).read_stats.receipt_bytes_hashed
        == (
            local.store.project_root
            / _stored_backing(saved).data.rows.receipt.project_relative_path
            / "data.parquet"
        )
        .stat()
        .st_size
    )
    directory_count = 10001
    complete = _memory_backing(calendar(local, directory_count))
    sparse = AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(
            CompletedFrameData(
                complete.data.schema,
                complete.data.preview_rows(1),
                complete.data.copy_attachments(),
            ),
            complete.metadata,
        ),
    )
    for frame in (sparse, sparse.persist()):
        page = frame.read_page(limit=1, time_details=True)
        assert page.total_count == 1 and page.read_stats.coordinate_rows_read == directory_count
        assert page.read_stats.data_rows_read == 1
        assert len(page) == 1 and len(page.coordinates["bucket"]) == 1
        assert page.to_pandas()["day.label"].tolist() == [
            (datetime(2024, 1, 1) + timedelta(days=directory_count - 1)).date().isoformat()
        ]
    # A new copy also owns page-sized Arrow storage, rather than a parent view.
    assert pa.array(detached_rows(page.to_pandas())["amount"]).get_total_buffer_size() <= 16


def test_data_pages_preserve_decimal_and_extreme_date_keys(local: LocalFrameServices) -> None:
    from decimal import Decimal

    from marivo.analysis.core.data_types import DecimalDataType

    date_key = DataColumn("day", "day", ScalarDataType("date"), True)
    other_key = DataColumn("part", "part", ScalarDataType("string"), False)
    value = DataColumn("amount", "amount", DecimalDataType(38, 18), True, "USD")
    schema = DataSchema(
        (DataAxis("joint", (date_key, other_key), name="joint"),),
        (value,),
        value_paths=(("amount",),),
    )
    amount = Decimal("99999999999999999999.999999999999999999")
    rows = pd.DataFrame(
        {
            "day": pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array([-(2**31), None], type=pa.int32()).cast(pa.date32())
                )
            ),
            "part": pd.Series(["a", "b"], dtype="string[pyarrow]"),
            "amount": pd.Series(
                pd.arrays.ArrowExtensionArray(pa.array([amount, None], type=pa.decimal128(38, 18)))
            ),
        }
    )
    memory = AnalysisFrame._from_memory(
        local, CompletedFrameResult(CompletedFrameData(schema, rows), metadata())
    )
    for frame in (memory, memory.persist()):
        page = frame.read_page(limit=1)
        key = page.coordinates["day"][0]
        assert isinstance(key, pa.Date32Scalar) and key.cast(pa.int32()).as_py() == -(2**31)
        assert page.to_pandas()["amount"].tolist() == [amount]
        assert page.to_pandas()["joint"][0][1] == "a"
        tail = frame.read_page(limit=1, cursor=page.next_cursor)
        assert tail.coordinates["day"] == (None,)
        assert tail.to_pandas()["amount"].isna().tolist() == [True]


def test_data_page_cursor_continues_in_an_independent_process(local: LocalFrameServices) -> None:
    import json
    import subprocess
    import sys

    saved = ordinary(local, 5).persist()
    first = saved.read_page(columns=("amount",), limit=2)
    script = """
import json, sys
from pathlib import Path
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.dsl.frame import AnalysisFrame
local = LocalFrameServices(SessionStore.open_existing(Path(sys.argv[1])), sys.argv[2])
backing = local.open_frame(sys.argv[3])
frame = AnalysisFrame._from_stored(local, backing.data, artifact_id=backing.artifact_id, metadata=backing.metadata, method_facts=backing.method_facts)
page = frame.read_page(columns=("amount",), limit=2, cursor=sys.argv[4])
assert page.input_id == frame.schema.input_id == sys.argv[5]
print(json.dumps({"keys": page.coordinates["key"], "values": page.to_pandas()["amount"].tolist(), "total": page.total_count, "more": page.has_more}))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(local.store.project_root),
            local.session_ref,
            saved.artifact_id,
            first.next_cursor,
            first.input_id,
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"keys": [2, 1], "values": [2, 3], "total": 5, "more": True}


def test_calendar_export_unifies_grains_and_preserves_clipped_timezone_bounds() -> None:
    from marivo._temporal import CalendarAxis, _BuiltinGrain
    from marivo._temporal_grid import bind_grid
    from marivo.analysis.core.frame_calendar import freeze_calendar
    from marivo.analysis.dsl.temporal import exact_scope
    from marivo.analysis.materialization.pandas_export import export_frame

    for unit, count in (("day", 1), ("minute", 5)):
        scope = exact_scope("2026-10-25T00:02:00", "2026-10-25T04:00:00", "Europe/Berlin")
        grid = bind_grid(
            scope, _BuiltinGrain(unit=unit, count=count), report_timezone="Europe/Berlin"
        )
        frozen = freeze_calendar(
            CalendarAxis(scope, grid)._binding,
            token="axis.0",
            name="report.time",
            key=DataColumn("bucket", "bucket", TimestampDataType("us", "UTC"), False),
        )
        schema = DataSchema((frozen.axis,), ())
        keys = frozen.directory.rows.loc[:, ["bucket"]]
        rows = export_frame(schema, keys, frozen.attachments)
        assert rows.columns.tolist() == ["time.start", "time.end"]
        assert str(rows["time.start"].dtype) == "timestamp[us, tz=Europe/Berlin][pyarrow]"
        assert rows["time.start"].iloc[0].isoformat() == "2026-10-25T00:02:00+02:00"
        assert rows["time.end"].iloc[-1].isoformat() == "2026-10-25T04:00:00+01:00"
        details = export_frame(schema, keys, frozen.attachments, time_details=True)
        assert details.columns.tolist() == [
            "time.start",
            "time.end",
            "time.label",
            "time.nominal_start",
            "time.nominal_end",
        ]
        assert details["time.nominal_start"].iloc[0].isoformat() == "2026-10-25T00:00:00+02:00"
        pd.testing.assert_frame_equal(rows, details.loc[:, rows.columns])
        empty = export_frame(schema, keys.iloc[:0], frozen.attachments)
        assert empty.empty and empty.dtypes.equals(rows.dtypes)


def test_calendar_page_details_match_export_and_bind_cursor(local: LocalFrameServices) -> None:
    from marivo.analysis.core.model import CoreRuleError

    frame = calendar(local, 3)
    for fixed in (frame, frame.persist()):
        page = fixed.read_page(limit=1, time_details=True)
        expected = fixed.to_pandas(time_details=True)
        expected.attrs = {}
        pd.testing.assert_frame_equal(page.to_pandas(), expected.iloc[:1].reset_index(drop=True))
        assert [column.name for column in page.layout] == expected.columns.tolist()
        with pytest.raises(CoreRuleError, match="cursor"):
            fixed.read_page(limit=1, cursor=page.next_cursor, time_details=False)
        chunks = [page.to_pandas()]
        if isinstance(fixed._backing, StoredFrameBacking):
            backing = local.open_frame(fixed.artifact_id)
            fixed = AnalysisFrame._from_stored(
                local,
                backing.data,
                artifact_id=backing.artifact_id,
                metadata=backing.metadata,
                method_facts=backing.method_facts,
            )
        while page.has_more:
            page = fixed.read_page(limit=1, cursor=page.next_cursor, time_details=True)
            chunks.append(page.to_pandas())
        pd.testing.assert_frame_equal(pd.concat(chunks, ignore_index=True), expected)


def test_merged_interval_export_uses_common_utc_carrier(local: LocalFrameServices) -> None:
    first = interval(local, 1, 2, zone="Asia/Shanghai")
    second = interval(local, 2, 3, zone="America/New_York")
    fixed = first.align(second).compute()
    for frame in (fixed, fixed.persist()):
        rows = frame.to_pandas()
        assert rows.columns.tolist()[:2] == ["interval.start", "interval.end"]
        assert str(rows["interval.start"].dtype) == "timestamp[us, tz=UTC][pyarrow]"
        assert rows["interval.start"].tolist() == [
            datetime(2024, 1, day, tzinfo=timezone.utc) for day in (1, 2)
        ]
        first_page = frame.read_page(limit=1)
        assert first_page.to_pandas()["interval.start"].dtype == rows["interval.start"].dtype
        assert first_page.next_cursor is not None
        second_page = frame.read_page(limit=1, cursor=first_page.next_cursor)
        assert second_page.to_pandas()["interval.start"].dtype == rows["interval.start"].dtype
        expected = rows.copy()
        expected.attrs = {}
        pd.testing.assert_frame_equal(
            pd.concat([first_page.to_pandas(), second_page.to_pandas()], ignore_index=True),
            expected,
        )
        assert second_page.next_cursor is None
        assert rows.attrs["frozen_coordinates"][0]["rows"].to_pandas(types_mapper=pd.ArrowDtype)[
            "timezone"
        ].tolist() == [
            "Asia/Shanghai",
            "America/New_York",
        ]


@pytest.mark.parametrize(
    ("zone", "offset_seconds", "export_zone"),
    (
        ("UTC+08:00:30", 28830, "UTC"),
        ("UTC-00:00:30", -30, "UTC"),
        ("UTC+08:00:00", 28800, "+08:00"),
        ("UTC-03:30:00", -12600, "-03:30"),
    ),
)
def test_fixed_offset_exports_are_readable_and_keep_exact_instants(
    local: LocalFrameServices,
    capsys: pytest.CaptureFixture[str],
    zone: str,
    offset_seconds: int,
    export_zone: str,
) -> None:
    from marivo._temporal import CalendarAxis, _BuiltinGrain
    from marivo._temporal_grid import bind_grid
    from marivo.analysis.core.frame_calendar import freeze_calendar
    from marivo.analysis.dsl.temporal import exact_scope

    scope = exact_scope("2024-01-01T00:02:00", "2024-01-03T00:00:00", zone)
    grid = bind_grid(scope, _BuiltinGrain(unit="day", count=1), report_timezone=zone)
    frozen = freeze_calendar(
        CalendarAxis(scope, grid)._binding,
        token="day",
        name="day",
        key=DataColumn("bucket", "bucket", TimestampDataType("us", "UTC"), False),
    )
    daily = AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(
            CompletedFrameData(
                DataSchema((frozen.axis,), ()),
                frozen.directory.rows.loc[:, ["bucket"]],
                frozen.attachments,
            ),
            metadata(),
        ),
    )
    boundary_zone = timezone(timedelta(seconds=offset_seconds))
    for root, name, start, end in (
        (
            daily,
            "day",
            datetime(2024, 1, 1, 0, 2, tzinfo=boundary_zone),
            datetime(2024, 1, 3, tzinfo=boundary_zone),
        ),
        (
            interval(local, 1, 3, zone=zone),
            "interval",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 3, tzinfo=timezone.utc),
        ),
    ):
        for frame in (root, root.persist()):
            rows = frame.to_pandas(time_details=True)
            assert rows[f"{name}.start"].dtype == pd.ArrowDtype(pa.timestamp("us", tz=export_zone))
            assert rows[f"{name}.start"].tolist()[0] == start
            assert rows[f"{name}.end"].iloc[-1] == end
            assert rows.attrs["axes"][0]["descriptor"]["timezone"] == zone
            if name == "day":
                assert rows["day.nominal_start"].iloc[0] == datetime(
                    2024, 1, 1, tzinfo=boundary_zone
                )
            chunks = []
            cursor = None
            while True:
                page = frame.read_page(limit=1, cursor=cursor, time_details=True)
                chunks.append(page.to_pandas())
                cursor = page.next_cursor
                if cursor is None:
                    break
            rows.attrs = {}
            pd.testing.assert_frame_equal(pd.concat(chunks, ignore_index=True), rows)
            frame.show(n=1)
            assert rows[f"{name}.start"].iloc[0].isoformat(sep=" ") in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["calendar", "interval", "paired"])
def test_exported_frozen_metadata_allows_pandas_selection_and_concat(
    local: LocalFrameServices, kind: str
) -> None:
    frame = calendar(local, 3)
    if kind == "interval":
        frame = interval(local, 1, 4)
    elif kind == "paired":
        frame = frame.compare(calendar(local, 3, offset=1)).compute()
    for fixed in (frame, frame.persist()):
        rows = fixed.to_pandas()
        assert all(isinstance(part["rows"], pa.Table) for part in rows.attrs["frozen_coordinates"])
        assert isinstance(rows.attrs == rows.copy(deep=True).attrs, bool)
        assert rows.attrs == rows.copy(deep=True).attrs
        assert rows.nlargest(2, "amount")["amount"].tolist() == (
            [5] if kind == "interval" else [2, 1]
        )
        assert rows.sort_values("amount")["amount"].tolist() == (
            [5] if kind == "interval" else [0, 1, 2]
        )
        joined = pd.concat([rows.iloc[:1], rows.iloc[-1:]], ignore_index=True)
        assert joined["amount"].tolist() == ([5, 5] if kind == "interval" else [0, 2])


def test_exported_coordinate_tables_keep_exact_carriers_and_nulls() -> None:
    from decimal import Decimal

    from marivo.analysis.core.data_types import DecimalDataType
    from marivo.analysis.materialization.pandas_export import export_frame

    columns = (
        DataColumn("key", "key", ScalarDataType("int64"), True),
        DataColumn("decimal", "decimal", DecimalDataType(18, 2), True),
        DataColumn("stamp", "stamp", TimestampDataType("us", "UTC"), True),
    )
    directory = pd.DataFrame(
        {
            "key": pd.Series([2**53 + 1, None], dtype="int64[pyarrow]"),
            "decimal": pd.Series(
                [Decimal("1234567890123456.78"), None], dtype=pd.ArrowDtype(pa.decimal128(18, 2))
            ),
            "stamp": pd.Series(
                pa.array([2**53 + 1, None], type=pa.timestamp("us", tz="UTC")),
                dtype=pd.ArrowDtype(pa.timestamp("us", tz="UTC")),
            ),
        }
    )
    attachment = OutputAttachment(
        "exact", DataSchema((), columns, value_paths=tuple((c.name,) for c in columns)), directory
    )
    rows = export_frame(DataSchema((), ()), pd.DataFrame(index=range(2)), (attachment,))
    table = rows.attrs["frozen_coordinates"][0]["rows"]
    assert table["key"].to_pylist() == [2**53 + 1, None]
    assert table["decimal"].to_pylist() == [Decimal("1234567890123456.78"), None]
    assert table["stamp"].type == pa.timestamp("us", tz="UTC")
    assert table["stamp"].cast(pa.int64()).to_pylist() == [2**53 + 1, None]
    assert rows.attrs == rows.copy(deep=True).attrs


def _period_page(
    frame: AnalysisFrame,
    *,
    limit: int = 1000,
    cursor: str | None = None,
    comparison: bool = False,
) -> FramePeriodPage:
    target = frame.schema.comparisons[0] if comparison else frame.axes[0]
    return frame.read_periods(target, limit=limit, cursor=cursor)


def test_period_pages_keep_sparse_partial_directory_and_original_order(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.core.frame_page import FrameCalendarPeriod

    original = calendar(local, 4)
    backing = _memory_backing(original)
    part = backing.data.copy_attachments()[0]
    directory = part.rows.copy()
    directory.loc[0, "actual_start"] = datetime(2024, 1, 1, 12, tzinfo=timezone.utc)
    directory.loc[0, "actual_start_civil"] = "2024-01-01T12:00:00.000000+00:00"
    # Valid input attachment order is canonicalized once at completion.
    directory = directory.iloc[[2, 0, 3, 1]].reset_index(drop=True)
    sparse = AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(
            CompletedFrameData(
                backing.data.schema,
                backing.data.copy_rows().iloc[:1],
                (replace(part, rows=directory),),
            ),
            backing.metadata,
        ),
    )
    assert _memory_backing(sparse).data._attachments[0].rows["ordinal"].tolist() == [0, 1, 2, 3]
    stored = sparse.persist()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("period page consumed primary data or method payloads")

    monkeypatch.setattr(FrameReader, "data", forbidden)
    monkeypatch.setattr(FrameReader, "method_facts", forbidden)
    for frame in (sparse, stored):
        page = _period_page(frame, limit=2)
        assert page.input_id == frame.schema.input_id
        assert page.target == frame.schema.axes[0]
        assert len(page) == 2 and page.total_count == 4 and page.has_more
        assert [item.key for item in page.items] == [(900_000,), (900_017,)]
        first = page.items[0]
        assert isinstance(first, FrameCalendarPeriod)
        assert first.label == "2024-01-01" and first.ordinal == 0
        assert first.start == datetime(2024, 1, 1, 12, tzinfo=timezone.utc)
        assert first.nominal_start == datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert first.actual_start_civil == "2024-01-01T12:00:00.000000+00:00"
        assert "FrameCalendarPeriod key=(900000)" in repr(first)
        assert ".show()" in repr(first) and "\n" not in repr(first)
        assert page.read_stats.data_rows_read == 0
        tail = _period_page(frame, limit=2, cursor=page.next_cursor)
        assert [item.key for item in tail.items] == [(900_034,), (900_051,)]
        assert not tail.has_more and tail.next_cursor is None
        # The sole represented row joins to the last frozen period, not page-local row 0.
        row_key = frame.read_page(limit=1).coordinates["bucket"][0]
        assert row_key == tail.items[1].key[0] == 900_051


def test_period_pages_preserve_complete_typed_keys(local: LocalFrameServices) -> None:
    from decimal import Decimal

    from marivo.analysis.core.data_types import DecimalDataType

    original = calendar(local, 2)
    source = _memory_backing(original).data
    axis = source.schema.axes[0]
    assert isinstance(axis, CalendarDataAxis)
    columns = (
        DataColumn("stamp", "stamp", TimestampDataType("ns", "UTC"), False),
        DataColumn("day", "day", ScalarDataType("date"), False),
        DataColumn("elapsed", "elapsed", DurationDataType("ns"), False),
        DataColumn("part", "part", DecimalDataType(20, 2), False),
    )
    keys = pd.DataFrame(
        {
            "stamp": pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array([2**53 + 1, -(2**63)], type=pa.int64()).cast(pa.timestamp("ns", "UTC"))
                )
            ),
            "day": pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array([-(2**31), 1], type=pa.int32()).cast(pa.date32())
                )
            ),
            "elapsed": pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array([-(2**63), 2**53 + 1], type=pa.int64()).cast(pa.duration("ns"))
                )
            ),
            "part": pd.Series(
                [Decimal("123456789012345678.91"), Decimal("-0.01")],
                dtype=pd.ArrowDtype(pa.decimal128(20, 2)),
            ),
        }
    )
    directory = pd.concat([keys, source.copy_attachments()[0].rows.drop(columns="bucket")], axis=1)
    target_axis = replace(axis, columns=columns)
    rows = keys.iloc[::-1].reset_index(drop=True)
    rows["amount"] = source.copy_rows()["amount"]
    memory = AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(
            CompletedFrameData(
                replace(source.schema, axes=(target_axis,)),
                rows,
                (
                    OutputAttachment(
                        "calendar.period", calendar_directory_schema(target_axis), directory
                    ),
                ),
            ),
            metadata(),
        ),
    )
    for frame in (memory, memory.persist()):
        period = _period_page(frame, limit=1).items[0]
        stamp, day, elapsed, part = period.key
        assert isinstance(stamp, pa.TimestampScalar) and stamp.value == 2**53 + 1
        assert stamp.type == pa.timestamp("ns", "UTC")
        assert isinstance(day, pa.Date32Scalar) and day.value == -(2**31)
        assert isinstance(elapsed, pa.DurationScalar) and elapsed.value == -(2**63)
        assert part == Decimal("123456789012345678.91")
        assert "date32[day]:-2147483648" in repr(period)
        assert len(repr(period)) < 320
        data = frame.read_page(limit=2)
        joined = tuple(data.coordinates[c.slot_id][1] for c in columns)
        assert period.key == joined


def test_period_pages_return_paired_endpoints_and_saved_comparison_absence(
    local: LocalFrameServices,
) -> None:
    from marivo.analysis.core.frame_page import FrameComparisonPeriod, FramePairedPeriod

    current, baseline = calendar(local, 4), calendar(local, 3)
    paired = current.align(baseline, match=CalendarCorrespondence("ordinal")).compute()
    compared = current.compare(
        {"base": mv.baseline(baseline, match=CalendarCorrespondence("ordinal"))}
    ).compute()
    for memory, comparison in ((paired, False), (compared, True)):
        for frame in (memory, memory.persist()):
            page = _period_page(frame, limit=3, comparison=comparison)
            assert page.total_count == 4 and page.read_stats.data_rows_read == 0
            tail = _period_page(frame, limit=3, cursor=page.next_cursor, comparison=comparison)
            assert len(tail) == 1
            item = tail.items[0]
            if comparison:
                assert isinstance(item, FrameComparisonPeriod)
                assert item.key == (900_051,) and item.current.key == (900_051,)
                assert item.baseline is None and item.absent_reason == "unmatched"
            else:
                assert isinstance(item, FramePairedPeriod)
                assert item.key == (3,)
                assert item.left is not None and item.left.key == (900_051,)
                assert item.right is None
            assert not tail.has_more


def test_period_pages_keep_endpoint_timezones_without_reconstruction(
    local: LocalFrameServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo import _timezones
    from marivo.analysis.core.frame_page import FrameIntervalPeriod, FramePairedPeriod

    left = interval(local, 1, 2, zone="Asia/Shanghai")
    right = interval(local, 3, 4, zone="America/New_York")
    # Equality alignment retains two distinct interval identities in saved order.
    memory = left.align(right).compute()
    stored = memory.persist()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("saved period reader resolved current timezone rules")

    monkeypatch.setattr(_timezones, "ZoneInfo", forbidden)
    for frame in (memory, stored):
        page = _period_page(frame)
        assert [item.timezone for item in page.items if isinstance(item, FrameIntervalPeriod)] == [
            "Asia/Shanghai",
            "America/New_York",
        ]
        assert [item.start for item in page.items if isinstance(item, FrameIntervalPeriod)] == [
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 3, tzinfo=timezone.utc),
        ]
        assert all(not isinstance(item, FramePairedPeriod) for item in page.items)


def test_interval_period_pages_keep_saved_pairs_and_comparison_endpoints(
    local: LocalFrameServices,
) -> None:
    from marivo.analysis.core.frame_page import FrameComparisonPeriod, FramePairedPeriod

    current, baseline = interval(local, 1, 4), interval(local, 2, 5)
    paired = current.align(baseline, match=CalendarCorrespondence("ordinal")).compute()
    compared = current.compare(
        {"base": mv.baseline(baseline, match=CalendarCorrespondence("ordinal"))}
    ).compute()
    for memory, comparison in ((paired, False), (compared, True)):
        for frame in (memory, memory.persist()):
            page = _period_page(frame, comparison=comparison)
            assert page.total_count == len(page) == 1 and not page.has_more
            item = page.items[0]
            assert item.key == (0,)
            if isinstance(item, FrameComparisonPeriod):
                assert comparison and item.baseline is not None
                assert item.current.start == datetime(2024, 1, 1, tzinfo=timezone.utc)
                assert item.baseline.start == datetime(2024, 1, 2, tzinfo=timezone.utc)
                assert item.absent_reason is None
            else:
                assert isinstance(item, FramePairedPeriod) and not comparison
                assert item.left is not None and item.right is not None
                assert item.left.start == datetime(2024, 1, 1, tzinfo=timezone.utc)
                assert item.right.end == datetime(2024, 1, 5, tzinfo=timezone.utc)


def test_named_period_pages_decode_captured_labels_and_keep_identity(
    local: LocalFrameServices,
) -> None:
    from marivo.analysis.core.data_schema import NamedCalendarData
    from marivo.analysis.core.frame_page import FrameCalendarPeriod
    from marivo.analysis.core.lineage import SemanticObjectIdentity

    original = calendar(local, 3)
    source = _memory_backing(original).data
    axis = source.schema.axes[0]
    assert isinstance(axis, CalendarDataAxis) and axis.frozen is not None
    ref = SemanticObjectIdentity("period_calendar", "business", "definition-exact")
    named = replace(
        axis,
        frozen=replace(
            axis.frozen, definition=NamedCalendarData("business", "snapshot", "period", ref)
        ),
    )
    part = source.copy_attachments()[0]
    directory = part.rows.copy()
    directory["nominal_label"] = pd.Series(['"P1"', "true", "1"], dtype="string[pyarrow]")
    memory = AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(
            CompletedFrameData(
                replace(source.schema, axes=(named,)),
                source.copy_rows(),
                (OutputAttachment(part.name, calendar_directory_schema(named), directory),),
            ),
            metadata(),
        ),
    )
    for frame in (memory, memory.persist()):
        page = _period_page(frame)
        labels = [item.label for item in page.items if isinstance(item, FrameCalendarPeriod)]
        assert labels == ["P1", True, 1]
        assert [type(label) for label in labels] == [str, bool, int]
        assert page.target == frame.schema.axes[0]
        assert page.items[2].key == (900_034,)


def test_period_page_retains_only_selected_records_and_uses_bounded_batches(
    local: LocalFrameServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.materialization import graph_reading

    memory = calendar(local, 1001)
    stored = memory.persist()
    monkeypatch.setattr(graph_reading, "POLICY", replace(POLICY, batch_rows=7))
    selected = _period_page(stored, limit=1)
    assert len(selected.items) == 1 and selected.items[0].key == (900_000,)
    assert selected.total_count == 1001
    assert selected.read_stats.data_rows_read == 0
    assert selected.read_stats.coordinate_rows_read == 7
    assert not any(
        isinstance(getattr(selected, item.name), (pd.DataFrame, FrameReader, AnalysisFrame))
        for item in fields(selected)
    )
    directory = _memory_backing(memory).data._attachments[0].rows
    reference = weakref.ref(directory)
    detached = _period_page(memory, limit=1)
    del directory, memory
    gc.collect()
    assert reference() is None
    assert detached.items[0].key == (900_000,)


@pytest.mark.parametrize("case", ["bool", "zero", "large", "cursor", "limit", "other", "ordinary"])
def test_period_page_request_rejection_precedes_payload_reads(
    local: LocalFrameServices,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    from marivo.analysis.materialization.frame_paging import read_frame_periods

    frame = calendar(local, 3).persist()
    page = _period_page(frame, limit=1)
    target = frame.schema.axes[0]
    limit: int = 1
    cursor = None
    if case == "bool":
        limit = True
    elif case == "zero":
        limit = 0
    elif case == "large":
        limit = 10001
    elif case == "cursor":
        cursor = "invalid"
    elif case == "limit":
        limit, cursor = 2, page.next_cursor
    elif case == "other":
        frame = calendar(local, 3).persist()
        target, cursor = frame.schema.axes[0], page.next_cursor
    else:
        frame = ordinary(local).persist()
        target = frame.schema.axes[0]

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid period request opened a payload")

    monkeypatch.setattr(OutputRead, "_open", forbidden)
    with pytest.raises(AnalysisError):
        read_frame_periods(
            input_id=frame._input_id,
            schema=_complete_schema(frame),
            backing=_stored_backing(frame),
            local=local,
            target=target,
            limit=limit,
            cursor=cursor,
        )
