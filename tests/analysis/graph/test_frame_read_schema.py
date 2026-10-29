"""Logical read schema preserves exact slots without acquiring data authority."""

import operator
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.core.data_schema import (
    BuiltinCalendarData,
    CalendarDataAxis,
    DataAxis,
    DataSchema,
    FrozenCalendarData,
    FrozenIntervalData,
    IntervalDataAxis,
    PairedDataAxis,
)
from marivo.analysis.core.data_types import (
    DataColumn,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
from marivo.analysis.core.frame_data import CompletedFrameData, CompletedFrameResult
from marivo.analysis.core.frame_schema import (
    CoordinateExportColumn,
    FieldExportColumn,
    UnresolvedDataType,
    capture_schema,
    export_layout,
)
from marivo.analysis.core.frame_structure import (
    PendingDataAxis,
    PendingDataColumn,
    PendingDataSchema,
)
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_sources import values
from marivo.analysis.materialization import frame_evaluation, terminal_store
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.graph_reading import FrameReader, OutputRead
from marivo.analysis.materialization.pandas_export import export_frame
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession


@pytest.fixture
def local(tmp_path: Path) -> LocalFrameServices:
    return DatasetRuntime.create(tmp_path, "frame-read-schema", timezone="UTC").local


def _metadata() -> FrameResultMetadata:
    return FrameResultMetadata(
        "read-schema-result",
        FrameAudit((), EvaluationRecord(datetime(2026, 10, 28, tzinfo=timezone.utc), (), ())),
    )


def test_pending_schema_retains_known_kinds_complete_keys_and_one_slot_directory() -> None:
    source = PendingDataSchema(
        (
            PendingDataAxis(
                "customer",
                "customer",
                "identity",
                (
                    PendingDataColumn("tenant", "tenant", "string", False),
                    PendingDataColumn("id", "id", "numeric", False),
                ),
            ),
        ),
        (
            PendingDataColumn("sales", "prior.revenue", "numeric", None, "USD"),
            PendingDataColumn("ratio", "ratio", "numeric", None, "1"),
        ),
        value_paths=(("prior", "revenue"), ("ratio",)),
    )
    schema = capture_schema("exact-pending", source)
    assert schema.input_id == "exact-pending"
    assert tuple(schema.values) == ("prior.revenue", "ratio")
    assert schema.values["prior.revenue"].dtype == UnresolvedDataType("numeric")
    assert schema.values["prior.revenue"].nullable is None
    assert schema.values["prior.revenue"].unit == "USD"
    assert schema.values["ratio"].unit == "1"
    assert schema.axes[0].role == "identity"
    assert tuple(field.name for field in schema.axes[0].keys) == ("tenant", "id")
    assert schema.slots["sales"] is schema.values["prior.revenue"]
    assert schema.slots["tenant"] is schema.axes[0].keys[0]
    assert schema.slots["tenant"].unit is None
    assert schema.layout == (
        CoordinateExportColumn("customer", "customer", "self", "key"),
        FieldExportColumn("prior.revenue", "sales"),
        FieldExportColumn("ratio", "ratio"),
    )
    with pytest.raises(TypeError):
        operator.setitem(schema.values, "wrong", schema.values["ratio"])
    with pytest.raises(FrozenInstanceError):
        schema.axes[0].keys[0].name = "changed"


def _time_axis(
    token: str, kind: Literal["calendar", "interval"]
) -> CalendarDataAxis | IntervalDataAxis:
    key = DataColumn(f"{token}.key", "tick", ScalarDataType("int64"), False)
    if kind == "calendar":
        return CalendarDataAxis(
            token,
            (key,),
            name=token,
            timezone="UTC",
            frozen=FrozenCalendarData(BuiltinCalendarData("day", 1), f"{token}.directory"),
        )
    return IntervalDataAxis(
        token, (key,), name=token, frozen=FrozenIntervalData(f"{token}.directory"), timezone="UTC"
    )


@pytest.mark.parametrize("kind", ["calendar", "interval"])
def test_time_and_paired_export_layout_preserves_complete_axis_identity(
    kind: Literal["calendar", "interval"],
) -> None:
    left, right = _time_axis("left", kind), _time_axis("right", kind)
    key = DataColumn("pair.key", "pair_id", ScalarDataType("int64"), False)
    pair = PairedDataAxis("pair", (key,), name="period", left=left, right=right, mapping_name="map")
    schema = capture_schema("paired-input", DataSchema((pair,), ()))
    assert schema.axes[0].role == "paired"
    assert schema.axes[0].left.timezone == "UTC"
    assert schema.axes[0].right.timezone == "UTC"
    assert schema.axes[0].left.kind == kind
    assert schema.axes[0].right.kind == kind
    assert tuple(field.slot_id for field in schema.axes[0].keys) == ("pair.key",)
    expected_parts = ("start", "end")
    assert tuple(column.part for column in schema.layout if column.kind == "coordinate") == (
        *expected_parts,
        *expected_parts,
    )
    assert {column.axis_token for column in schema.layout if column.kind == "coordinate"} == {
        "pair"
    }
    assert tuple(column.endpoint for column in schema.layout if column.kind == "coordinate") == (
        *(("left",) * len(expected_parts)),
        *(("right",) * len(expected_parts)),
    )


def test_default_layout_collision_does_not_block_schema_and_alias_repairs_export() -> None:
    key = DataColumn("key", "customer_id", ScalarDataType("string"), False)
    value = DataColumn("value", "customer", ScalarDataType("int64"), False)
    source = DataSchema(
        (DataAxis("axis", (key,), name="customer"),),
        (value,),
        value_paths=(("customer",),),
    )
    schema = capture_schema("collision", source)
    assert tuple(column.name for column in schema.layout) == ("customer", "customer")
    rows = pd.DataFrame({"key": ["B", "A"], "value": [20, 10]})
    with pytest.raises(CoreRuleError, match="unique flat export") as failure:
        export_frame(source, rows)
    assert failure.value.location == "frame.to_pandas"
    with pytest.raises(CoreRuleError) as page_failure:
        export_layout(source, validate_names=True, member="read_page")
    assert page_failure.value.location == "frame.read_page"
    exported = export_frame(source, rows, axis_names=("customer_key",), include_attrs=False)
    assert exported.columns.tolist() == ["customer_key", "customer"]
    assert exported.to_dict("list") == {"customer_key": ["B", "A"], "customer": [20, 10]}
    assert exported.attrs == {}
    assert tuple(column.name for column in schema.layout) == ("customer", "customer")


def test_projection_layout_and_table_keep_requested_order_and_exact_types() -> None:
    key = DataColumn("key", "customer_id", ScalarDataType("string"), False)
    revenue = DataColumn("value", "revenue", DecimalDataType(20, 2), True, "USD")
    elapsed = DataColumn("elapsed", "elapsed", DurationDataType("ns"), True)
    present = DataColumn("present", "right_present", ScalarDataType("boolean"), False)
    source = DataSchema(
        (DataAxis("axis", (key,), name="customer"),),
        (revenue, elapsed),
        (present,),
        value_paths=(("revenue",), ("elapsed",)),
        presence_paths=(("right_present",),),
    )
    rows = pd.DataFrame(
        {
            "key": pd.Series(["B", "A"], dtype="string[pyarrow]"),
            "value": pd.Series(
                [Decimal("123456789012345.67"), None], dtype=pd.ArrowDtype(pa.decimal128(20, 2))
            ),
            "elapsed": pd.Series([1, None], dtype=pd.ArrowDtype(pa.duration("ns"))),
            "present": pd.Series([True, False], dtype="bool[pyarrow]"),
        }
    )
    exported = export_frame(source, rows, columns=("right_present", "revenue"), include_attrs=False)
    assert exported.columns.tolist() == ["customer", "right_present", "revenue"]
    assert exported["revenue"].dtype == pd.ArrowDtype(pa.decimal128(20, 2))
    assert exported["revenue"].tolist() == [Decimal("123456789012345.67"), pd.NA]
    assert exported["right_present"].tolist() == [True, False]
    assert exported.attrs == {}
    assert tuple(column.name for column in export_layout(source, columns=())) == ("customer",)
    for columns in (("missing",), ("revenue", "revenue")):
        with pytest.raises(CoreRuleError) as failure:
            export_layout(source, columns=columns)
        assert "revenue" in str(failure.value)
        assert "right_present" in str(failure.value)
    schema = capture_schema("precise", source)
    assert schema.values["revenue"].dtype == DecimalDataType(20, 2)
    assert schema.values["elapsed"].dtype == DurationDataType("ns")


def test_type_result_protocol_preserves_timestamp_facts_and_bounded_representation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    dtype = TimestampDataType("ns", None)
    source = DataSchema(
        (),
        (DataColumn("time", "time", dtype, True),),
        value_paths=(("time",),),
    )
    field = capture_schema("time-input", source).values["time"]
    assert field.dtype is dtype
    assert dtype.unit == "ns" and dtype.timezone is None
    for value in (dtype, DecimalDataType(38, 7), DurationDataType("us"), ScalarDataType("date")):
        assert "use .show()" in repr(value)
        assert "\n" not in repr(value)
        value.show()
    assert "timezone=None" in capsys.readouterr().out


def test_public_schema_uses_captured_facts_for_every_carrier_without_io(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = values(local, columns={"revenue": "USD"}, rows=(((), {"revenue": 7}),))
    memory = pending.compute()
    saved = memory.persist()
    backing = local.open_frame(saved.artifact_id)
    reopened = AnalysisFrame._from_stored(
        local,
        backing.data,
        artifact_id=backing.artifact_id,
        metadata=backing.metadata,
        method_facts=backing.method_facts,
    )
    assert reopened.schema.input_id == saved.schema.input_id
    assert isinstance(pending.schema.values["revenue"].dtype, UnresolvedDataType)
    assert memory.schema.values["revenue"].dtype == ScalarDataType("int64")
    frames = (pending, memory, saved, reopened)
    expected = tuple(frame.schema for frame in frames)
    # A captured description remains readable after its result file disappears.
    payload = backing.data.rows
    (local.store.project_root / payload.receipt.project_relative_path / "data.parquet").unlink()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("schema read attempted execution, Store access or payload consumption")

    monkeypatch.setattr(Session, "catalog", property(forbidden))
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    monkeypatch.setattr(frame_evaluation, "evaluate_frame", forbidden)
    monkeypatch.setattr(SessionStore, "_read", forbidden)
    monkeypatch.setattr(OutputRead, "_open", forbidden)
    monkeypatch.setattr(FrameReader, "data", forbidden)
    monkeypatch.setattr(FrameReader, "complete", forbidden)
    monkeypatch.setattr(FrameReader, "method_facts", forbidden)
    monkeypatch.setattr(terminal_store, "confirm_records", forbidden)
    monkeypatch.setattr(terminal_store, "complete_metadata", forbidden)
    for frame, captured in zip(frames, expected, strict=True):
        assert frame.schema == captured
        assert frame.lineage.input_id == captured.input_id
        assert frame.schema.fixed_axes == ()
        assert frame.schema.comparisons == ()
        assert frame.schema.decompositions == ()
        frame.schema.show()
        frame.lineage.show()
        field = frame.schema.values["revenue"]
        assert frame.schema.slots[field.slot_id] == field
        assert field.unit == "USD"


def test_public_pages_preserve_reordered_presence_and_values_across_pages(
    local: LocalFrameServices,
) -> None:
    key = DataColumn("key", "customer_id", ScalarDataType("string"), False)
    revenue = DataColumn("revenue", "revenue", ScalarDataType("int64"), True, "USD")
    flag = DataColumn("present", "right_present", ScalarDataType("boolean"), False)
    schema = DataSchema(
        (DataAxis("customers", (key,), name="customer"),),
        (revenue,),
        (flag,),
        value_paths=(("revenue",),),
        presence_paths=(("right_present",),),
    )
    keys = ("B", "A", "C", "E", "D")
    flags = (True, True, False, True, False)
    amounts = (10, None, None, 40, None)
    rows = pd.DataFrame(
        {
            "key": pd.Series(keys, dtype=pd.ArrowDtype(pa.string())),
            "revenue": pd.Series(amounts, dtype=pd.ArrowDtype(pa.int64())),
            "present": pd.Series(flags, dtype=pd.ArrowDtype(pa.bool_())),
        }
    )
    memory = AnalysisFrame._from_memory(
        local, CompletedFrameResult(CompletedFrameData(schema, rows), _metadata())
    )
    expected = pd.DataFrame(
        {
            "member": pd.Series(keys, dtype=pd.ArrowDtype(pa.string())),
            "right_present": pd.Series(flags, dtype=pd.ArrowDtype(pa.bool_())),
            "revenue": pd.Series(amounts, dtype=pd.ArrowDtype(pa.int64())),
        }
    )
    for frame in (memory, memory.persist()):
        pages: list[pd.DataFrame] = []
        coordinates: list[str] = []
        cursor = None
        while True:
            page = frame.read_page(
                columns=("right_present", "revenue"),
                limit=2,
                cursor=cursor,
                axis_names={frame.axes[0].token: "member"},
            )
            assert page.input_id == frame.schema.input_id
            assert page.total_count == 5 and page.limit == 2
            assert tuple(column.slot_id for column in page.layout if column.kind == "field") == (
                "present",
                "revenue",
            )
            table = page.to_pandas()
            assert table.attrs == {}
            assert table["member"].tolist() == list(page.coordinates["key"])
            pages.append(table)
            coordinates.extend(table["member"].tolist())
            if not page.has_more:
                assert len(page) == 1 and page.next_cursor is None
                break
            assert len(page) == 2 and page.next_cursor is not None
            cursor = page.next_cursor
        pd.testing.assert_frame_equal(pd.concat(pages, ignore_index=True), expected)
        assert tuple(coordinates) == keys


@pytest.mark.parametrize("count", (0, 1))
def test_public_zero_axis_zero_value_page_retains_empty_or_unit_row(
    local: LocalFrameServices, count: int
) -> None:
    memory = AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(
            CompletedFrameData(DataSchema((), ()), pd.DataFrame(index=pd.RangeIndex(count))),
            _metadata(),
        ),
    )
    for frame in (memory, memory.persist()):
        page = frame.read_page()
        assert page.input_id == frame.schema.input_id
        assert len(page) == page.total_count == count
        assert page.coordinates == {} and page.layout == ()
        assert page.to_pandas().shape == (count, 0)
        assert page.to_pandas().attrs == {}
        assert not page.has_more and page.next_cursor is None
        assert frame.schema.axes == () and frame.schema.slots == {}


def test_interpretation_records_validate_closed_fields_and_bound_display(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo.analysis.core.frame_interpretation import (
        FrameAxisTime,
        FrameObservationContext,
        FrameStatisticContext,
        FrameTrailingPeriodsWindow,
    )
    from marivo.analysis.core.lineage import SemanticObjectIdentity

    subject = SemanticObjectIdentity("metric", "sales.revenue", "definition")
    context = FrameObservationContext(subject, FrameAxisTime("day"), FrameTrailingPeriodsWindow(7))
    assert "observation" in repr(context) and "sales.revenue" in repr(context)
    assert ".show()" in repr(context) and len(repr(context)) < 300
    context.show(max_output_bytes=300)
    shown = capsys.readouterr().out
    assert len(shown.encode()) <= 300
    assert "observation" in shown
    with pytest.raises(CoreRuleError, match="positive number"):
        FrameTrailingPeriodsWindow(0)
    with pytest.raises(CoreRuleError, match="captured output time basis"):
        FrameObservationContext(subject, None, FrameTrailingPeriodsWindow(7))
    assert "mean" in repr(FrameStatisticContext("mean", ()))
    with pytest.raises(FrozenInstanceError):
        context.subject = None
