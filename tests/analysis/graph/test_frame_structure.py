"""Current Frame structures bind handles to exact inputs without production state."""

import gc
import weakref
from dataclasses import fields, replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from marivo._definition.query import LiteralValues, ResultQuery
from marivo.analysis.core.artifact_data import (
    ArtifactData,
    BuiltinCalendarData,
    CalendarDataAxis,
    DataAxis,
    DataColumn,
    DataSchema,
    FrozenCalendarData,
    OutputAttachment,
    PairedDataAxis,
    ParquetHandle,
)
from marivo.analysis.core.data_schema import (
    CalendarComparisonBinding,
    ComparisonValueBinding,
    DataComparison,
    EndpointPresence,
    OrdinaryComparisonBinding,
)
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    ExpressionFrameBacking,
    LiteralFrameBinding,
    MemoryFrameBacking,
    required_attachments,
)
from marivo.analysis.core.frame_metadata import EvaluationRecord, FrameResultMetadata
from marivo.analysis.core.frame_structure import (
    PendingDataAxis,
    PendingDataColumn,
    PendingDataSchema,
    require_complete_structure,
    value_name,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.lineage import empty as empty_lineage
from marivo.analysis.core.model import BoundSignature, CoreRuleError, ResultQuantity
from marivo.analysis.dsl.column import row_count
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import FileEntry, LocalReceipt
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.terminal_store import describe

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def metadata(result_id: str) -> FrameResultMetadata:
    return FrameResultMetadata(result_id, EvaluationRecord(NOW, (), ()))


@pytest.fixture
def local(tmp_path: Path) -> LocalFrameServices:
    return DatasetRuntime.create(tmp_path, "frame-structure", timezone="UTC").local


def schema(*, duplicate_names: bool = False, composite: bool = False) -> DataSchema:
    key = DataColumn("key.0", "customer", ScalarDataType("string"), False)
    key2 = DataColumn("key.1", "region", ScalarDataType("string"), False)
    axes = (
        (DataAxis("axis.0", (key, key2), name="customer", kind="member"),)
        if composite
        else (
            DataAxis("axis.0", (key,), name="customer", kind="member"),
            *((DataAxis("axis.1", (key2,), name="customer"),) if duplicate_names else ()),
        )
    )
    return DataSchema(
        axes,
        (DataColumn("value.0", "revenue", ScalarDataType("int64"), True, "USD"),),
        (DataColumn("presence.0", "right_present", ScalarDataType("boolean"), False),),
        value_paths=(("revenue",),),
        presence_paths=(("right_present",),),
    )


def data(structure: DataSchema | None = None) -> CompletedFrameData:
    structure = structure or schema()
    rows: dict[str, list[str] | list[int] | list[bool]] = {
        column.slot_id: ["B", "A"] for axis in structure.axes for column in axis.columns
    }
    rows["value.0"] = [20, 10]
    rows["presence.0"] = [True, False]
    return CompletedFrameData(structure, pd.DataFrame(rows))


@pytest.mark.parametrize("value", [float("inf"), -float("inf")])
@pytest.mark.parametrize("role", ["value", "coordinate"])
def test_complete_data_preserves_native_infinite_values_and_checks_coordinate_keys(
    value: float, role: str
) -> None:
    column = DataColumn("value.0", "value", ScalarDataType("float64"), True)
    if role == "value":
        structure = DataSchema((), (column,), value_paths=(("value",),))
        rows = pd.DataFrame({column.slot_id: [value]})
        attachments: tuple[OutputAttachment, ...] = ()
    elif role == "coordinate":
        structure = DataSchema((DataAxis("axis.0", (column,), name="value"),), ())
        rows = pd.DataFrame({column.slot_id: [value]})
        attachments = ()
    else:
        raise AssertionError(role)
    if role == "coordinate":
        return
    complete = CompletedFrameData(structure, rows, attachments)
    retained = complete.copy_rows()
    assert retained[column.slot_id].iloc[0] == value


def test_complete_data_keeps_nullable_float_na_and_finite_values() -> None:
    key = DataColumn("key.0", "key", ScalarDataType("int64"), False)
    value = DataColumn("value.0", "value", ScalarDataType("float64"), True)
    structure = DataSchema(
        (DataAxis("axis.0", (key,), name="key"),),
        (value,),
        value_paths=(("value",),),
    )
    complete = CompletedFrameData(
        structure,
        pd.DataFrame(
            {"key.0": [0, 1], "value.0": pd.Series([None, 1.5], dtype="float64[pyarrow]")}
        ),
    ).copy_rows()
    assert complete["value.0"].isna().tolist() == [True, False]
    assert complete["value.0"].iloc[1] == 1.5


def memory(local: LocalFrameServices, structure: DataSchema | None = None) -> AnalysisFrame:
    return AnalysisFrame._from_memory(
        local, CompletedFrameResult(data(structure), metadata("result_1"))
    )


@pytest.mark.parametrize(
    "name", ["", "a.b", "a\\b", "a\x00b", "a\x85b", "left_present", "right_present"]
)
def test_user_names_require_one_nonreserved_segment(name: str) -> None:
    assert value_name("revenue", location="test") == ("revenue",)


def test_new_roots_require_concrete_schema_and_named_axes(local: LocalFrameServices) -> None:
    pending = PendingDataSchema(
        (
            PendingDataAxis(
                "axis.0",
                "customer",
                "identity",
                (PendingDataColumn("key.0", "customer", "string", False),),
            ),
        ),
        (PendingDataColumn("value.0", "revenue", "numeric", None, "USD"),),
        (("revenue",),),
    )
    assert pending.values[0].nullable is None
    with pytest.raises(CoreRuleError):
        # Exercise the runtime rejection of an intentionally invalid schema type.
        call_attribute = "__call__"
        validate: object = getattr(require_complete_structure, call_attribute)
        assert callable(validate)
        validate(pending)


def test_paths_own_lookup_and_order_without_graph_scans(local: LocalFrameServices) -> None:
    structure = replace(
        schema(),
        values=(
            DataColumn("value.0", "left.revenue", ScalarDataType("int64"), True, "USD"),
            DataColumn("value.1", "right.revenue", ScalarDataType("int64"), True, "USD"),
        ),
        presence=(
            DataColumn("presence.0", "left_present", ScalarDataType("boolean"), False),
            DataColumn("presence.1", "left.right_present", ScalarDataType("boolean"), False),
        ),
        value_paths=(("left", "revenue"), ("right", "revenue")),
        presence_paths=(("left_present",), ("left", "right_present")),
    )
    owned = CompletedFrameData(
        structure,
        pd.DataFrame(
            {
                "key.0": ["A"],
                "value.0": [1],
                "value.1": [2],
                "presence.0": [True],
                "presence.1": [False],
            }
        ),
    )
    frame = AnalysisFrame._from_memory(local, CompletedFrameResult(owned, metadata("r")))
    assert frame.output_names == ("left.revenue", "right.revenue")
    assert frame.presence_names == ("left_present", "left.right_present")
    assert frame["left.right_present"]._kind == "boolean"


def test_equal_structures_are_independent_inputs_and_old_handles_fail(
    local: LocalFrameServices,
) -> None:
    first = memory(local)
    assert isinstance(first._backing, MemoryFrameBacking)
    second = AnalysisFrame._from_memory(
        local, CompletedFrameResult(first._backing.data, metadata("result_1"))
    )
    assert isinstance(second._backing, MemoryFrameBacking)
    assert first._schema == second._schema
    assert first._input_id != second._input_id
    assert second._bind_statistic(row_count())._input_id == second._input_id
    assert first._bind_statistic(row_count())._input_id == first._input_id
    assert second._backing.metadata.result_id == first._backing.metadata.result_id
    assert second._backing.metadata.evaluated_at == NOW


def test_axis_lookup_is_exact_ambiguous_and_complete(local: LocalFrameServices) -> None:
    frame = memory(local)
    axis = frame.axis("customer")
    assert (axis.token, axis.name, axis.role, axis.key_names) == (
        "axis.0",
        "customer",
        "identity",
        ("customer",),
    )
    assert frame.coord(axis)._kind == "string"
    ambiguous = memory(local, schema(duplicate_names=True))
    assert ambiguous.coord(ambiguous.axes[1])._kind == "string"
    composite = memory(local, schema(composite=True))


def test_handles_keep_current_input_alive_without_runtime(
    local: LocalFrameServices, capsys: pytest.CaptureFixture[str]
) -> None:
    frame = memory(local)
    reference = weakref.ref(frame)
    column = -(frame["revenue"] + 1)
    statistic = column.mean()
    axis = frame.axes[0]
    del frame, column
    gc.collect()
    assert reference() is not None
    assert statistic._frame is axis._frame
    axis.show()
    axis._frame["revenue"].show()
    assert tuple(field.name for field in fields(local)) == ("store", "session_ref")
    assert isinstance(axis._frame._backing, MemoryFrameBacking)
    del axis, statistic
    gc.collect()
    assert reference() is None


def test_completed_rows_and_attrs_are_isolated(local: LocalFrameServices) -> None:
    rows = pd.DataFrame({"key.0": ["A"], "value.0": [3], "presence.0": [True]})
    rows.attrs = {"nested": {"labels": ["original"]}}
    owned = CompletedFrameData(schema(), rows)
    rows.loc[0, "value.0"] = 99
    rows.attrs["nested"]["labels"].append("changed")
    exported = owned.copy_rows()
    assert exported["value.0"].tolist() == [3]
    assert exported.attrs == {}
    exported.loc[0, "value.0"] = 100
    exported.attrs["nested"] = {"labels": ["exported"]}
    assert owned.copy_rows()["value.0"].tolist() == [3]
    assert owned.copy_rows().attrs == {}


def test_producer_authority_is_not_imported_through_attrs(local: LocalFrameServices) -> None:
    class Producer:
        pass

    producer = Producer()
    reference = weakref.ref(producer)
    rows = pd.DataFrame({"key.0": ["A"], "value.0": [3], "presence.0": [True]})
    rows.attrs["producer"] = producer
    rows.attrs["callback"] = lambda value=producer: value
    owned = CompletedFrameData(schema(), rows)
    del rows, producer
    gc.collect()
    assert reference() is None
    assert owned.copy_rows().attrs == {}


def test_stored_bindings_are_stable_only_for_exact_store_session_artifact(
    local: LocalFrameServices, tmp_path: Path
) -> None:
    # No payload exists: acquiring current handles must use metadata only.
    saved = ArtifactData(
        schema(),
        ParquetHandle(LocalReceipt(".marivo/nonexistent", (FileEntry("data.parquet", 1),), 2, 1)),
    )

    def reopen(service: LocalFrameServices, artifact_id: str) -> AnalysisFrame:
        return AnalysisFrame._from_stored(
            service,
            saved,
            artifact_id=artifact_id,
            metadata=describe(replace(metadata("r"), lineage=empty_lineage(saved.schema))),
        )

    first, second = reopen(local, "artifact_1"), reopen(local, "artifact_1")
    assert first is not second and first._input_id == second._input_id
    second._require_column(first["revenue"])
    second._bind_statistic(first["revenue"].sum())
    assert second.coord(first.axes[0])._input_id == second._input_id
    other_session = DatasetRuntime.create(tmp_path, "stored-other", timezone="UTC").local
    other_store = DatasetRuntime.create(
        tmp_path / "other-store", "frame-structure", timezone="UTC"
    ).local
    for different in (
        reopen(local, "artifact_2"),
        reopen(other_session, "artifact_1"),
        reopen(other_store, "artifact_1"),
    ):
        assert different._input_id != first._input_id
    loaded = memory(local)


def test_completion_preserves_captured_types_and_inert_time_anchor() -> None:
    concrete = replace(schema(), axes=(replace(schema().axes[0], at=NOW),))
    pending = PendingDataSchema(
        (
            PendingDataAxis(
                "axis.0",
                "customer",
                "identity",
                (PendingDataColumn("key.0", "customer", "string", False),),
                at=NOW,
            ),
        ),
        (PendingDataColumn("value.0", "revenue", "numeric", None, "USD"),),
        (("revenue",),),
        (PendingDataColumn("presence.0", "right_present", "boolean", False),),
        (("right_present",),),
    )
    assert pending.complete(concrete) is concrete
    assert data(concrete).schema.axes[0].at == NOW


def test_equal_source_fingerprints_do_not_authorize_exchanged_handles(
    local: LocalFrameServices,
) -> None:
    graph = BoundGraph(
        ResultQuery(LiteralValues(("revenue",), (((), (3,)),)), (), local.owner),
        BoundSignature(local.owner, (ResultQuantity("revenue", "USD"),)),
    )
    pending = PendingDataSchema(
        (), (PendingDataColumn("value.0", "revenue", "numeric", None, "USD"),), (("revenue",),)
    )
    first = AnalysisFrame._from_expression(local, pending, LiteralFrameBinding(graph))
    second = AnalysisFrame._from_expression(local, pending, LiteralFrameBinding(graph))
    assert isinstance(first._backing, ExpressionFrameBacking)
    assert isinstance(second._backing, ExpressionFrameBacking)
    assert isinstance(first._backing.root, LiteralFrameBinding)
    assert isinstance(second._backing.root, LiteralFrameBinding)
    assert first._backing.root.graph.fingerprint == second._backing.root.graph.fingerprint
    assert first._input_id != second._input_id
    assert first.output_names == ("revenue",) and first.axes == ()


@pytest.mark.parametrize("composite", [False, True])
def test_complete_identity_requires_nonnullable_components_and_nonnull_rows(
    composite: bool,
) -> None:
    columns = (
        DataColumn("key.0", "customer", ScalarDataType("string"), False),
        *((DataColumn("key.1", "region", ScalarDataType("string"), False),) if composite else ()),
    )
    axis = DataAxis("axis.0", columns, name="customer", kind="member")
    invalid_axis = replace(axis, columns=(*columns[:-1], replace(columns[-1], nullable=True)))
    with pytest.raises(MaterializationError) as caught:
        CompletedFrameData(
            DataSchema((axis,), ()), pd.DataFrame({c.slot_id: [None] for c in columns})
        )
    assert type(caught.value) is MaterializationError
    assert caught.value.expected == "key.0: a nonnullable column without NA"
    assert caught.value.received == "1 NA values"
    assert caught.value.stage == "stage_output"
    assert caught.value.location == "dataset.stage_output"
    assert caught.value.repair is not None
    assert caught.value.repair.kind == "retry"
    assert caught.value.repair.action == (
        "Correct the source field type or stage layout; Marivo does not cast lossy values back."
    )
    assert caught.value.repair.help_target.surface == "analysis"
    assert caught.value.repair.help_target.canonical_id == "analysis_frame.compute"
    pending = tuple(PendingDataColumn(c.slot_id, c.name, "string", False) for c in columns)
    assert PendingDataAxis(
        "axis.0", "customer", "identity", tuple(replace(c, nullable=None) for c in pending)
    )


def test_nullable_field_coordinates_remain_valid_categories() -> None:
    key = DataColumn("key.0", "region", ScalarDataType("string"), True)
    axis = DataAxis("axis.0", (key,), name="region")
    owned = CompletedFrameData(DataSchema((axis,), ()), pd.DataFrame({"key.0": [None, "West"]}))
    assert owned.row_count == 2 and owned.copy_rows()["key.0"].isna().tolist() == [True, False]
    assert PendingDataAxis(
        "axis.0", "region", "field", (PendingDataColumn("key.0", "region", "string", True),)
    )


def _paired_axes() -> tuple[PairedDataAxis, PairedDataAxis]:
    left = CalendarDataAxis(
        "endpoint.left",
        (DataColumn("left.key", "month", ScalarDataType("int64"), False),),
        "UTC",
        name="month",
        frozen=FrozenCalendarData(BuiltinCalendarData("month", 1), "left.directory"),
    )
    right = replace(
        left,
        token="endpoint.right",
        columns=(replace(left.columns[0], slot_id="right.key"),),
        frozen=FrozenCalendarData(BuiltinCalendarData("month", 1), "right.directory"),
    )
    right2 = replace(
        right,
        token="endpoint.right2",
        columns=(replace(right.columns[0], slot_id="right2.key"),),
        frozen=FrozenCalendarData(BuiltinCalendarData("month", 1), "right2.directory"),
    )
    paired = PairedDataAxis(
        "paired.0",
        (replace(left.columns[0], slot_id="paired.key.0"),),
        left,
        right,
        "paired.mapping.0",
        name="month",
    )
    return paired, replace(
        paired,
        token="paired.1",
        columns=(replace(left.columns[0], slot_id="paired.key.1"),),
        right=right2,
        mapping_name="paired.mapping.1",
    )


def test_reachable_tokens_reject_conflicts_and_allow_exact_calendar_reuse() -> None:
    first, second = _paired_axes()
    structure = DataSchema((first, second), ())
    require_complete_structure(structure)
    assert required_attachments(structure) == (
        "left.directory",
        "right.directory",
        "paired.mapping.0",
        "right2.directory",
        "paired.mapping.1",
    )
    ordinary = DataAxis(
        first.left.token,
        (DataColumn("ordinary.key", "region", ScalarDataType("string"), False),),
        name="region",
    )
    conflicting = replace(second, left=replace(second.left, name="other month"))


def test_named_comparison_facts_bind_exact_paths_in_pending_and_complete_schemas() -> None:
    base = schema()
    fact = DataComparison(
        "reference",
        (ComparisonValueBinding("current", ("original", "revenue"), ("revenue",)),),
        OrdinaryComparisonBinding(),
        EndpointPresence(("right_present",), ()),
        EndpointPresence(("right_present",), ()),
    )
    complete = replace(base, comparisons=(fact, replace(fact, alias="other")))
    pending = PendingDataSchema(
        (
            PendingDataAxis(
                "axis.0",
                "customer",
                "identity",
                (PendingDataColumn("key.0", "customer", "string", False),),
            ),
        ),
        (PendingDataColumn("value.0", "revenue", "numeric", True, "USD"),),
        (("revenue",),),
        (PendingDataColumn("presence.0", "right_present", "boolean", False),),
        (("right_present",),),
        comparisons=complete.comparisons,
    )
    assert pending.complete(complete) is complete
    assert data(complete).schema.comparisons == complete.comparisons


def test_comparison_attachment_ownership_is_exact_and_current_axis_is_authoritative() -> None:
    paired, other = _paired_axes()
    assert isinstance(paired.left, CalendarDataAxis) and isinstance(paired.right, CalendarDataAxis)
    assert isinstance(other.right, CalendarDataAxis)
    current = paired.left
    fact = DataComparison(
        "one",
        (ComparisonValueBinding("current", ("revenue",), ("revenue",)),),
        CalendarComparisonBinding(current.token, paired.right, "compare.one"),
        EndpointPresence(("right_present",), ()),
        EndpointPresence(("right_present",), ()),
    )
    second = replace(
        fact,
        alias="two",
        binding=CalendarComparisonBinding(current.token, other.right, "compare.two"),
    )
    structure = DataSchema(
        (current,),
        schema().values,
        schema().presence,
        value_paths=(("revenue",),),
        presence_paths=schema().presence_paths,
        comparisons=(fact, second),
    )
    assert required_attachments(structure) == (
        "left.directory",
        "right.directory",
        "compare.one",
        "right2.directory",
        "compare.two",
    )
    assert isinstance(second.binding, CalendarComparisonBinding)
    conflict = replace(second, binding=replace(second.binding, mapping_name="compare.one"))


@pytest.mark.parametrize("mismatch", ["grain", "timezone"])
def test_calendar_comparison_facts_require_matching_frozen_authority(mismatch: str) -> None:
    paired, _ = _paired_axes()
    assert isinstance(paired.left, CalendarDataAxis) and isinstance(paired.right, CalendarDataAxis)
    current = paired.left
    baseline = replace(paired.right, token="comparison.reference.baseline", name="baseline month")
    fact = DataComparison(
        "reference",
        (ComparisonValueBinding("current", ("revenue",), ("revenue",)),),
        CalendarComparisonBinding(current.token, baseline, "comparison.reference.map"),
        EndpointPresence(("right_present",), ()),
        EndpointPresence(("right_present",), ()),
    )
    structure = DataSchema(
        (current,),
        schema().values,
        schema().presence,
        value_paths=(("revenue",),),
        presence_paths=schema().presence_paths,
        comparisons=(fact,),
    )
    require_complete_structure(structure)
    if mismatch == "grain":
        assert current.frozen is not None
        current = replace(
            current, frozen=replace(current.frozen, definition=BuiltinCalendarData("day", 1))
        )
    else:
        current = replace(current, timezone="Europe/Berlin")
