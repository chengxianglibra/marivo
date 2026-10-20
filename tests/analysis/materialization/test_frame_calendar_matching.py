"""Frozen calendar correspondence through the single Frame execution path."""

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo._definition.correspondence import CalendarCorrespondence
from marivo.analysis.compiler.frame_plan import CompiledFramePlan
from marivo.analysis.compiler.graph_lowering import LoweredRead
from marivo.analysis.compiler.graph_plan import FrameCompute, FrameInput, LocalCompute
from marivo.analysis.compiler.query_program import FrameCombineProgram
from marivo.analysis.core.artifact_data import OutputAttachment
from marivo.analysis.core.data_schema import (
    BuiltinCalendarData,
    CalendarDataAxis,
    DataAxis,
    DataSchema,
    FrozenCalendarData,
    NamedCalendarData,
    PairedDataAxis,
)
from marivo.analysis.core.data_types import (
    DataColumn,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    ExpressionFrameBacking,
    MemoryFrameBacking,
)
from marivo.analysis.core.frame_structure import PendingDataSchema
from marivo.analysis.core.frozen_calendar import calendar_directory_schema
from marivo.analysis.dsl.column import row_count
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import PreparedSources
from tests.shared_fixtures import DslCaseFactory


@pytest.fixture
def local(tmp_path: Path) -> LocalFrameServices:
    return DatasetRuntime.create(tmp_path, "frame-calendars", timezone="UTC").local


def _memory_backing(frame: AnalysisFrame) -> MemoryFrameBacking:
    assert isinstance(frame._backing, MemoryFrameBacking)
    return frame._backing


def _complete_schema(frame: AnalysisFrame) -> DataSchema:
    assert isinstance(frame._schema, DataSchema)
    return frame._schema


def memory(
    local: LocalFrameServices,
    labels: tuple[str, ...],
    keys: tuple[int, ...],
    values: tuple[int | None, ...],
    *,
    regions: tuple[str, ...] | None = None,
    unit: Literal["day", "month"] = "day",
    ends: tuple[str, ...] | None = None,
    actual_start: datetime | None = None,
) -> AnalysisFrame:
    key = DataColumn("bucket", "bucket", ScalarDataType("int64"), False)
    axis = CalendarDataAxis(
        "axis.0",
        (key,),
        "UTC",
        name="period",
        frozen=FrozenCalendarData(BuiltinCalendarData(unit, 1), "calendar.axis.0"),
    )
    starts = tuple(
        datetime.combine(date.fromisoformat(label), datetime.min.time(), timezone.utc)
        for label in labels
    )
    stops = (
        tuple(
            datetime.combine(date.fromisoformat(label), datetime.min.time(), timezone.utc)
            for label in ends
        )
        if ends
        else tuple(start + timedelta(days=1) for start in starts)
    )
    directory = pd.DataFrame(
        {
            "bucket": pd.Series(range(len(labels)), dtype="int64[pyarrow]"),
            "nominal_label": pd.Series(labels, dtype="string[pyarrow]"),
            "ordinal": pd.Series(range(len(labels)), dtype="int64[pyarrow]"),
            **{
                name: pd.Series(items, dtype="timestamp[us, tz=UTC][pyarrow]")
                for name, items in (
                    ("nominal_start", starts),
                    ("nominal_end", stops),
                    ("actual_start", (actual_start,) if actual_start is not None else starts),
                    ("actual_end", stops),
                )
            },
        }
    )
    axes: tuple[DataAxis, ...] = (axis,)
    rows = pd.DataFrame({"bucket": keys, "amount": values}, dtype=object)
    if regions is not None:
        axes += (
            DataAxis(
                "axis.1",
                (DataColumn("region", "region", ScalarDataType("string"), False),),
                name="region",
            ),
        )
        rows["region"] = regions
    schema = DataSchema(
        axes,
        (DataColumn("amount", "amount", ScalarDataType("int64"), True, "USD"),),
        value_paths=(("amount",),),
    )
    rows = rows.loc[:, [column.slot_id for column in schema.columns]]
    complete = CompletedFrameData(
        schema,
        rows,
        (OutputAttachment("calendar.axis.0", calendar_directory_schema(axis), directory),),
    )
    metadata = FrameResultMetadata(
        "literal",
        FrameAudit((), EvaluationRecord(datetime(2026, 10, 19, tzinfo=timezone.utc), (), ())),
    )
    return AnalysisFrame._from_memory(local, CompletedFrameResult(complete, metadata))


def test_sole_calendar_automatic_equality_and_explicit_map_agree(local: LocalFrameServices) -> None:
    left = memory(local, ("2026-08-01", "2026-08-02"), (1, 0), (20, 10))
    right = memory(local, ("2026-08-01", "2026-08-02"), (0, 1), (2, 4))
    automatic = left.compare(right).compute()
    explicit = left.compare(right, on=((left.axes[0], right.axes[0]),)).compute()
    pd.testing.assert_frame_equal(
        _memory_backing(automatic).data.copy_rows(), _memory_backing(explicit).data.copy_rows()
    )
    assert isinstance(automatic._schema.axes[0], CalendarDataAxis)
    assert _memory_backing(automatic).data.copy_rows()["bucket"].tolist() == [1, 0]
    assert _memory_backing(automatic).data.copy_rows()[
        automatic._schema.values[2].slot_id
    ].tolist() == [
        16,
        8,
    ]


def test_frozen_leap_directory_only_entry_does_not_fail_actual_exact(
    local: LocalFrameServices,
) -> None:
    left = memory(local, ("2024-02-28", "2024-02-29", "2024-03-01"), (2, 0), (30, 10))
    right = memory(local, ("2023-02-28", "2023-03-01"), (0, 1), (2, 3))
    result = left.compare(right, match=CalendarCorrespondence("previous_year")).compute()
    assert isinstance(result._schema.axes[0], PairedDataAxis)
    rows = _memory_backing(result).data.copy_rows()
    assert rows[result._schema.values[2].slot_id].tolist() == [27, 8]
    assert len(rows) == 2
    attachments = _memory_backing(result).data.copy_attachments()
    assert len({attachment.name for attachment in attachments}) == 3
    paired = result._schema.axes[0]
    mapping = next(
        attachment for attachment in attachments if attachment.name == paired.mapping_name
    )
    assert mapping.rows["left.0"].tolist() == [0, 1, 2]
    assert mapping.rows["right.0"].tolist() == [0, pd.NA, 1]


def test_paired_region_reduce_does_not_reconstruct_map_from_presence(
    local: LocalFrameServices,
) -> None:
    left = memory(local, ("2026-08-01",), (0, 0), (10, 20), regions=("A", "B"))
    right = memory(local, ("2026-07-01",), (0,), (2,), regions=("A",))
    pairs = tuple(zip(left.axes, right.axes, strict=True))
    compared = left.compare(
        right, on=pairs, match=CalendarCorrespondence("ordinal"), how="outer"
    ).compute()
    assert _memory_backing(compared).data.copy_rows()[
        compared._schema.presence[1].slot_id
    ].tolist() == [
        True,
        False,
    ]
    reduced = compared.reduce(by=(compared.axes[0],), values={"n": row_count()}).compute()
    assert reduced.presence_names == ()
    assert _memory_backing(reduced).data.row_count == 1
    assert len(_memory_backing(reduced).data.copy_attachments()) == 3
    scalar = reduced.reduce(values={"n": row_count()}).compute()
    assert _memory_backing(scalar).data.copy_attachments() == ()
    with pytest.raises(AnalysisError, match="without paired"):
        compared.align(compared, on=())


def test_corrupt_directory_rejected_by_complete_memory_constructor(
    local: LocalFrameServices,
) -> None:
    frame = memory(local, ("2026-08-01", "2026-08-02"), (0,), (1,))
    data = _memory_backing(frame).data
    (attachment,) = data.copy_attachments()
    attachment.rows.loc[1, "ordinal"] = 7
    with pytest.raises(AnalysisError, match="original ordinal"):
        CompletedFrameData(data.schema, data.copy_rows(), (replace(attachment),))


def test_actual_leap_row_fails_exact_before_projection_and_outer_retains_order(
    local: LocalFrameServices,
) -> None:
    left = memory(local, ("2024-02-28", "2024-02-29", "2024-03-01"), (2, 1, 0), (30, 20, 10))
    right = memory(local, ("2023-02-28", "2023-03-01"), (0, 1), (2, 3))
    combined = left.compare(right, match=CalendarCorrespondence("previous_year"))
    for hidden in (
        combined,
        combined.select({"one": 1}),
        combined.top(0, by=combined["left.amount"]),
    ):
        with pytest.raises(AnalysisError, match="equal actual complete"):
            hidden.compute()
    output = left.compare(
        right, match=CalendarCorrespondence("previous_year"), how="outer"
    ).compute()
    rows = _memory_backing(output).data.copy_rows()
    assert rows[_complete_schema(output).key_fields[0]].tolist() == [2, 1, 0]
    assert rows[output._schema.values[1].slot_id].tolist() == [3, pd.NA, 2]
    assert rows[output._schema.presence[1].slot_id].tolist() == [True, False, True]


def test_sparse_ordinal_and_unobserved_31st_bucket_use_original_directory(
    local: LocalFrameServices,
) -> None:
    july, june = (
        tuple(f"2026-07-{i:02}" for i in range(1, 32)),
        tuple(f"2026-06-{i:02}" for i in range(1, 31)),
    )
    left = memory(local, july, (2,), (20,))
    right = memory(local, june, (2,), (5,))
    output = left.compare(right, match=CalendarCorrespondence("ordinal")).compute()
    assert _memory_backing(output).data.copy_rows()[
        _complete_schema(output).key_fields[0]
    ].tolist() == [2]
    pair_axis = output._schema.axes[0]
    assert isinstance(pair_axis, PairedDataAxis)
    mapping = next(
        a
        for a in _memory_backing(output).data.copy_attachments()
        if a.name == pair_axis.mapping_name
    )
    assert len(mapping.rows) == 31
    assert mapping.rows["right.0"].iloc[-1] is pd.NA
    tail = memory(local, july, (30, 2), (30, 20))
    with pytest.raises(AnalysisError, match="equal actual complete"):
        tail.compare(right, match=CalendarCorrespondence("ordinal")).compute()
    outer = tail.compare(right, match=CalendarCorrespondence("ordinal"), how="outer").compute()
    assert _memory_backing(outer).data.copy_rows()[
        _complete_schema(outer).key_fields[0]
    ].tolist() == [30, 2]


@pytest.mark.parametrize("kind", ["ordinal", "previous_year", "period_offset"])
@pytest.mark.parametrize("how", ["exact", "outer"])
def test_filter_cannot_remove_partial_month_admission(
    local: LocalFrameServices, kind: str, how: Literal["exact", "outer"]
) -> None:
    partial = memory(
        local,
        ("2026-08-01",),
        (0,),
        (8,),
        unit="month",
        ends=("2026-09-01",),
        actual_start=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )
    complete = memory(local, ("2026-08-01",), (0,), (4,), unit="month", ends=("2026-09-01",))
    filtered = partial.where(partial["amount"] < 0).compute()
    assert _memory_backing(filtered).data.row_count == 0
    rule = (
        CalendarCorrespondence("ordinal")
        if kind == "ordinal"
        else CalendarCorrespondence("previous_year")
        if kind == "previous_year"
        else CalendarCorrespondence("period_offset", -1)
    )
    combined = filtered.compare(complete, match=rule, how=how)
    with pytest.raises(AnalysisError, match="complete actual"):
        combined.top(0, by=combined["left.amount"]).compute()


def test_calendar_region_joint_domain_and_mapping_are_separate(local: LocalFrameServices) -> None:
    left = memory(local, ("2026-08-01", "2026-08-02"), (1, 0), (8, None), regions=("B", "A"))
    right = memory(local, ("2026-07-01", "2026-07-02"), (0, 1), (3, 4), regions=("B", "A"))
    with pytest.raises(AnalysisError, match="complete explicit"):
        left.compare(right, match=CalendarCorrespondence("ordinal"))
    pairs = tuple(zip(left.axes, right.axes, strict=True))
    with pytest.raises(AnalysisError, match="equal actual complete"):
        left.compare(right, on=pairs, match=CalendarCorrespondence("ordinal")).compute()
    output = left.compare(
        right, on=pairs, match=CalendarCorrespondence("ordinal"), how="outer"
    ).compute()
    rows = _memory_backing(output).data.copy_rows()
    assert list(zip(rows[_complete_schema(output).key_fields[0]], rows["region"], strict=True)) == [
        (1, "B"),
        (0, "A"),
        (0, "B"),
        (1, "A"),
    ]
    assert rows[output._schema.presence[0].slot_id].tolist() == [True, True, False, False]
    assert rows[output._schema.presence[1].slot_id].tolist() == [False, False, True, True]
    assert (
        len(
            next(
                a
                for a in _memory_backing(output).data.copy_attachments()
                if a.name.startswith("mapping")
            ).rows
        )
        == 2
    )


def test_equality_merges_complete_directory_without_renumbering_and_can_recombine(
    local: LocalFrameServices,
) -> None:
    labels = ("2026-08-01", "2026-08-02")
    left, right = memory(local, labels, (1,), (8,)), memory(local, labels, (0,), (4,))
    output = left.align(right, how="outer").compute()
    assert isinstance(output._schema.axes[0], CalendarDataAxis)
    assert _memory_backing(output).data.copy_rows()["bucket"].tolist() == [1, 0]
    (directory,) = _memory_backing(output).data.copy_attachments()
    assert directory.rows["ordinal"].tolist() == [0, 1]
    recombined = output.align(output).compute()
    assert _memory_backing(recombined).data.copy_rows()["bucket"].tolist() == [1, 0]
    conflicting = memory(local, ("2026-08-02",), (0,), (1,))
    with pytest.raises(AnalysisError, match="directory conflicts"):
        left.align(conflicting, how="outer").select({"one": 1}).compute()


def test_ordinary_equality_never_guesses_calendar_rule(local: LocalFrameServices) -> None:
    july = memory(local, ("2026-07-01",), (0,), (8,))
    august = memory(local, ("2026-08-01",), (0,), (4,))
    with pytest.raises(AnalysisError, match="directory conflicts"):
        august.compare(july).compute()
    matched = august.compare(july, match=CalendarCorrespondence("period_offset", -31)).compute()
    assert _memory_backing(matched).data.copy_rows()[
        matched._schema.values[2].slot_id
    ].tolist() == [-4]


def test_calendar_metadata_show_and_completed_match_need_no_source_or_timezone(
    local: LocalFrameServices,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    left = memory(local, ("2024-03-01",), (0,), (8,))
    right = memory(local, ("2023-03-01",), (0,), (2,))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("completed calendar operation consulted source or timezone")

    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_evaluation.open_query_sources", forbidden
    )
    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_evaluation.prepare_source_free_populations",
        forbidden,
    )
    monkeypatch.setattr("marivo.analysis.session.core.Session.catalog", property(forbidden))
    monkeypatch.setattr("marivo.analysis.core.frame_calendar.parse_timezone", forbidden)
    pending = left.compare(right, match=CalendarCorrespondence("previous_year"))
    with monkeypatch.context() as context:
        context.setattr(CompletedFrameData, "copy_rows", forbidden)
        context.setattr(CompletedFrameData, "copy_attachments", forbidden)
        pending.show(n=0)
    assert "previous_year" in capsys.readouterr().out
    result = pending.compute()
    assert _memory_backing(result).data.copy_rows()[result._schema.values[2].slot_id].tolist() == [
        6
    ]


@pytest.mark.parametrize("consumer", ["select", "where", "top"])
def test_mutated_complete_attachment_is_checked_before_any_consumer(
    local: LocalFrameServices,
    consumer: str,
) -> None:
    left = memory(local, ("2026-08-01",), (0,), (8,))
    right = memory(local, ("2026-07-01",), (0,), (2,))
    output = left.compare(right, match=CalendarCorrespondence("ordinal")).compute()
    paired = output._schema.axes[0]
    assert isinstance(paired, PairedDataAxis)
    mapping = next(
        a for a in _memory_backing(output).data._attachments if a.name == paired.mapping_name
    )
    mapping.rows.loc[0, "right.0"] = 99
    hidden = (
        output.select({"one": 1})
        if consumer == "select"
        else output.where(output["left.amount"] < 0)
        if consumer == "where"
        else output.top(0, by=output["left.amount"])
    )
    with pytest.raises(AnalysisError, match="resolving"):
        hidden.compute()


def test_source_plus_fixed_calendar_match_runs_one_existing_schedule(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marivo.analysis.materialization.frame_evaluation as evaluator
    from marivo.analysis.dsl import frame_sources

    session = analysis_dsl_case_factory("j2").session
    calendar = session.time_scope(start="2026-08-01", end="2026-09-01").by(mv.grain("day"))
    source = frame_sources.observe(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        {"amount": session.catalog.metrics.get("sales.revenue")},
        by=(calendar,),
    )
    source = source.where(source["amount"] > 0)
    fixed = source.compute()
    schedules: list[CompiledFramePlan] = []
    from marivo.analysis.materialization.graph_source_execution import run_frame_plan as original

    def run(
        compiled: CompiledFramePlan,
        *,
        source_prepared: Mapping[str, PreparedSources],
        read_fixed: Callable[[FrameInput], CompletedFrameData],
        adapt_source: Callable[[str, StageOutput], StageOutput],
        observe_read: Callable[[LoweredRead, StageOutput], None] | None = None,
        observe_local: Callable[[LocalCompute | FrameCompute, StageOutput], None] | None = None,
    ) -> StageOutput:
        schedules.append(compiled)
        return original(
            compiled,
            source_prepared=source_prepared,
            read_fixed=read_fixed,
            adapt_source=adapt_source,
            observe_read=observe_read,
            observe_local=observe_local,
        )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("captured source calendar was reconstructed")

    monkeypatch.setattr(evaluator, "run_frame_plan", run)
    monkeypatch.setattr(frame_sources, "freeze_calendar", forbidden)
    pending = source.compare(fixed, match=CalendarCorrespondence("ordinal"))
    result = pending.compute()
    assert isinstance(pending._schema, PendingDataSchema)
    pending._schema.complete(_complete_schema(result))
    assert len(schedules) == 1
    stages = schedules[0].lowered.plan.stages
    assert any(isinstance(stage, FrameInput) for stage in stages)
    assert (
        sum(
            isinstance(stage, FrameCompute) and isinstance(stage.program, FrameCombineProgram)
            for stage in stages
        )
        == 1
    )
    rows = _memory_backing(result).data.copy_rows()
    assert rows[result._schema.values[0].slot_id].tolist() == [60, 120]
    assert rows[result._schema.values[2].slot_id].tolist() == [0, 0]
    assert rows[result._schema.presence[1].slot_id].tolist() == [True, True]
    assert session.runs().items == ()


def test_generated_pair_tokens_and_names_avoid_independent_input_names(
    local: LocalFrameServices,
) -> None:
    left = memory(local, ("2026-08-01",), (0,), (8,), regions=("A",))
    right = memory(local, ("2026-07-01",), (0,), (2,), regions=("A",))

    def collision(frame: AnalysisFrame) -> AnalysisFrame:
        data = _memory_backing(frame).data
        ordinary = data.schema.axes[1]
        axis = replace(
            ordinary,
            token="frame.paired.0",
            columns=(replace(ordinary.columns[0], slot_id="frame.paired.0.1.key"),),
        )
        schema = replace(data.schema, axes=(data.schema.axes[0], axis))
        rows = data.copy_rows().rename(columns={"region": "frame.paired.0.1.key"})
        result = CompletedFrameResult(
            CompletedFrameData(schema, rows, data.copy_attachments()),
            _memory_backing(frame).metadata,
        )
        return AnalysisFrame._from_memory(local, result)

    left, right = collision(left), collision(right)
    output = left.compare(
        right,
        on=tuple(zip(left.axes, right.axes, strict=True)),
        match=CalendarCorrespondence("ordinal"),
    ).compute()
    paired = output._schema.axes[0]
    assert isinstance(paired, PairedDataAxis)
    assert paired.token == "frame.paired.0.2"
    assert (
        len({paired.token, paired.left.token, paired.right.token, output._schema.axes[1].token})
        == 4
    )
    assert len({a.name for a in _memory_backing(output).data.copy_attachments()}) == 3


def test_named_ordinal_is_source_free_and_level_mismatch_precedes_values(
    local: LocalFrameServices,
) -> None:
    def named(frame: AnalysisFrame, level: str) -> AnalysisFrame:
        data = _memory_backing(frame).data
        endpoint = data.schema.axes[0]
        assert isinstance(endpoint, CalendarDataAxis) and endpoint.frozen is not None
        endpoint = replace(
            endpoint,
            frozen=replace(
                endpoint.frozen, definition=NamedCalendarData("fiscal", "captured-digest", level)
            ),
        )
        (attachment,) = data.copy_attachments()
        label = attachment.rows["nominal_label"].iloc[0]
        attachment.rows["nominal_label"] = pd.Series([f'"{label}"'], dtype="string[pyarrow]")
        attachment = replace(attachment, schema=calendar_directory_schema(endpoint))
        return AnalysisFrame._from_memory(
            local,
            CompletedFrameResult(
                CompletedFrameData(
                    replace(data.schema, axes=(endpoint,)), data.copy_rows(), (attachment,)
                ),
                _memory_backing(frame).metadata,
            ),
        )

    left = named(memory(local, ("2026-08-01",), (0,), (8,)), "month")
    right = named(memory(local, ("2026-07-01",), (0,), (2,)), "month")
    automatic = left.compare(right, match=CalendarCorrespondence("ordinal")).compute()
    explicit = left.compare(
        right, on=((left.axes[0], right.axes[0]),), match=CalendarCorrespondence("ordinal")
    ).compute()
    pd.testing.assert_frame_equal(
        _memory_backing(automatic).data.copy_rows(), _memory_backing(explicit).data.copy_rows()
    )
    assert _memory_backing(automatic).data.copy_rows()[
        automatic._schema.values[2].slot_id
    ].tolist() == [6]
    with pytest.raises(AnalysisError, match="supported builtin"):
        left.compare(right, match=CalendarCorrespondence("period_offset", -1))
    different = named(memory(local, ("2026-07-01",), (0,), (2,)), "quarter")
    different = different.select({"other_name": different["amount"]}).compute()
    with pytest.raises(AnalysisError, match="compatible frozen"):
        left.compare(different, match=CalendarCorrespondence("ordinal"))


def test_select_filter_and_top_keep_complete_pair_interpretation(local: LocalFrameServices) -> None:
    left = memory(local, ("2026-08-01", "2026-08-02"), (1, 0), (8, 4))
    right = memory(local, ("2026-07-01", "2026-07-02"), (0, 1), (2, 3))
    output = left.compare(right, match=CalendarCorrespondence("ordinal")).compute()
    selected = output.select({"copied": output["absolute.amount"]})
    filtered = output.where(output["absolute.amount"] > 2)
    top = output.top(1, by=output["absolute.amount"])
    for derived in (selected, filtered, top):
        result = derived.compute()
        assert result.presence_names == ("left_present", "right_present")
        assert tuple(len(a.rows) for a in _memory_backing(result).data.copy_attachments()) == (
            2,
            2,
            2,
        )
        with pytest.raises(AnalysisError, match="without paired"):
            result.align(result, how="outer")
    assert selected.output_names == ("copied",)
    assert _memory_backing(top.compute()).data.copy_rows()[
        _complete_schema(output).key_fields[0]
    ].tolist() == [1]


@pytest.mark.parametrize("dtype", [DurationDataType("ns"), TimestampDataType("ns", "UTC")])
def test_paired_mapping_preserves_valid_minimum_temporal_endpoint_keys(
    local: LocalFrameServices,
    dtype: DurationDataType | TimestampDataType,
) -> None:
    def temporal(frame: AnalysisFrame) -> AnalysisFrame:
        data = _memory_backing(frame).data
        endpoint = data.schema.axes[0]
        assert isinstance(endpoint, CalendarDataAxis)
        endpoint = replace(endpoint, columns=(replace(endpoint.columns[0], dtype=dtype),))
        carrier = (
            pa.duration("ns") if isinstance(dtype, DurationDataType) else pa.timestamp("ns", "UTC")
        )
        keys = pd.Series(
            pd.arrays.ArrowExtensionArray(pa.array([-(2**63)], type=pa.int64()).cast(carrier))
        )
        rows = data.copy_rows()
        rows["bucket"] = keys
        (attachment,) = data.copy_attachments()
        attachment.rows["bucket"] = keys
        attachment = replace(attachment, schema=calendar_directory_schema(endpoint))
        return AnalysisFrame._from_memory(
            local,
            CompletedFrameResult(
                CompletedFrameData(replace(data.schema, axes=(endpoint,)), rows, (attachment,)),
                _memory_backing(frame).metadata,
            ),
        )

    left = temporal(memory(local, ("2026-08-01",), (0,), (8,)))
    right = temporal(memory(local, ("2026-07-01",), (0,), (2,)))
    output = left.compare(right, match=CalendarCorrespondence("ordinal")).compute()
    pair = output._schema.axes[0]
    assert isinstance(pair, PairedDataAxis)
    mapping = next(
        a for a in _memory_backing(output).data.copy_attachments() if a.name == pair.mapping_name
    )
    assert pa.array(mapping.rows["left.0"]).cast(pa.int64()).to_pylist() == [-(2**63)]
    assert pa.array(mapping.rows["right.0"]).cast(pa.int64()).to_pylist() == [-(2**63)]
    assert _memory_backing(output).data.copy_rows()[output._schema.values[2].slot_id].tolist() == [
        6
    ]


def test_source_completion_rejects_unused_corrupt_directory_before_top_zero(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from marivo.analysis.core.frame_data import SourceFrameBinding
    from marivo.analysis.dsl import frame_sources

    session = analysis_dsl_case_factory("j2").session
    source = frame_sources.observe(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        {"amount": session.catalog.metrics.get("sales.revenue")},
        by=(session.time_scope(start="2026-08-01", end="2026-09-01").by(mv.grain("day")),),
    )
    assert isinstance(source._backing, ExpressionFrameBacking)
    binding = source._backing.root
    assert isinstance(binding, SourceFrameBinding)
    binding.calendars[0].directory.rows.loc[30, "ordinal"] = 99
    with pytest.raises(AnalysisError, match="original ordinal"):
        source.top(0, by=source["amount"]).compute()
    assert session.runs().items == ()


def test_empty_actual_domains_keep_nominal_map_without_synthetic_rows(
    local: LocalFrameServices,
) -> None:
    left = memory(local, ("2026-08-01",), (), ())
    right = memory(local, ("2026-07-01",), (), ())
    result = left.compare(right, match=CalendarCorrespondence("ordinal")).compute()
    assert _memory_backing(result).data.row_count == 0
    assert tuple(len(a.rows) for a in _memory_backing(result).data.copy_attachments()) == (1, 1, 1)


@pytest.mark.parametrize("fault", ["grain", "timezone", "hour_rule"])
def test_captured_source_calendar_errors_precede_values_for_every_carrier(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    from marivo._definition.types import CalendarCoordinate
    from marivo.analysis.core.frame_calendar import FrozenSourceCalendar
    from marivo.analysis.dsl import frame_sources

    session = analysis_dsl_case_factory("j2").session
    metric = session.catalog.metrics.get("sales.revenue")
    frozen = 0
    from marivo.analysis.core.frame_calendar import freeze_calendar as original

    def freeze(
        coordinate: CalendarCoordinate, *, token: str, name: str, key: DataColumn
    ) -> FrozenSourceCalendar:
        nonlocal frozen
        frozen += 1
        return original(coordinate, token=token, name=name, key=key)

    monkeypatch.setattr(frame_sources, "freeze_calendar", freeze)

    def source(grain: Literal["day", "hour", "month"], zone: str) -> AnalysisFrame:
        return frame_sources.observe(
            session._runtime.local,
            session.catalog._state,
            session._runtime.connection_config,
            {"amount": metric},
            by=(
                session.time_scope(
                    start="2026-08-01",
                    end="2026-08-01T02:00:00+00:00" if fault == "hour_rule" else "2026-09-01",
                    timezone=zone,
                ).by(mv.grain(grain)),
            ),
        )

    left = source("hour" if fault == "hour_rule" else "day", "UTC")
    right = source(
        "hour" if fault == "hour_rule" else "month" if fault == "grain" else "day",
        "Asia/Shanghai" if fault == "timezone" else "UTC",
    )
    right = right.select({"other": right["amount"]})
    fixed_left, fixed_right = left.compute(), right.compute()
    assert frozen == 2

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("construction read beyond the current captured structure")

    monkeypatch.setattr(frame_sources, "freeze_calendar", forbidden)
    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_evaluation.open_query_sources", forbidden
    )
    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_evaluation.prepare_source_free_populations",
        forbidden,
    )
    monkeypatch.setattr("marivo.analysis.session.core.Session.catalog", property(forbidden))
    monkeypatch.setattr("marivo.analysis.core.frame_calendar.parse_timezone", forbidden)
    monkeypatch.setattr(CompletedFrameData, "copy_rows", forbidden)
    monkeypatch.setattr(CompletedFrameData, "copy_attachments", forbidden)
    expected = "supported builtin" if fault == "hour_rule" else "compatible frozen"
    for a, b in (
        (left, right),
        (left, fixed_right),
        (fixed_left, right),
        (fixed_left, fixed_right),
    ):
        with pytest.raises(AnalysisError, match=expected):
            a.compare(b, match=CalendarCorrespondence("previous_year"))
    assert frozen == 2


def test_pending_unary_shared_and_paired_axes_retain_exact_frozen_facts(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.dsl import frame_sources

    session = analysis_dsl_case_factory("j2").session
    source = frame_sources.observe(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        {"amount": session.catalog.metrics.get("sales.revenue")},
        by=(session.time_scope(start="2026-08-01", end="2026-09-01").by(mv.grain("day")),),
    )
    assert isinstance(source._schema, PendingDataSchema)
    captured = source._schema.axes[0]
    assert isinstance(captured, CalendarDataAxis)
    selected = source.select({"amount": source["amount"]})
    filtered = selected.where(selected["amount"] > 0)
    top = filtered.top(2, by=filtered["amount"])
    reduced = top.reduce({"amount": top["amount"].sum()}, by=top.axes)
    for pending in (selected, filtered, top, reduced):
        assert pending._schema.axes[0] is captured

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("source calendar was frozen more than once")

    monkeypatch.setattr(frame_sources, "freeze_calendar", forbidden)
    shared = reduced.align(reduced)
    paired = reduced.compare(reduced, match=CalendarCorrespondence("ordinal"))
    assert isinstance(shared._schema.axes[0], CalendarDataAxis)
    assert shared._schema.axes[0] == captured
    pair = paired._schema.axes[0]
    assert isinstance(pair, PairedDataAxis)
    assert pair.left.frozen is not None and pair.right.frozen is not None
    assert captured.frozen is not None
    assert pair.left.frozen.definition == pair.right.frozen.definition == captured.frozen.definition
    grouped = paired.reduce({"amount": paired["absolute.amount"].sum()}, by=paired.axes)
    assert grouped._schema.axes[0] is pair
    assert grouped.presence_names == ()
    for pending in (shared, paired, grouped):
        result = pending.compute()
        assert isinstance(pending._schema, PendingDataSchema)
        pending._schema.complete(_complete_schema(result))
        assert pending._schema.axes == result._schema.axes
    with pytest.raises(AnalysisError, match="without paired"):
        grouped.compare(grouped)


@pytest.mark.parametrize("fault", ["level", "digest"])
def test_named_source_pending_interpretation_is_checked_before_value_names(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    import marivo.semantic as ms
    from marivo._temporal import PeriodCalendarSnapshotV1, certify_period_calendar, semantic_grain
    from marivo.analysis.core.frame_data import SourceFrameBinding
    from marivo.analysis.dsl import frame_sources

    session = analysis_dsl_case_factory("j2").session
    metric = session.catalog.metrics.get("sales.revenue")
    start, end = date(2026, 8, 1), date(2026, 8, 9)
    calendar = ms.ref.period_calendar("sales.fiscal")

    def snapshot(prefix: str) -> PeriodCalendarSnapshotV1:
        return certify_period_calendar(
            calendar_ref=calendar,
            boundary_timezone="UTC",
            coverage=(start, end),
            rows=(
                {"date": start + timedelta(days=i), "short": i // 2, "long": f"{prefix}{i // 4}"}
                for i in range(8)
            ),
            levels={"short": "short", "long": "long"},
        )

    first = snapshot("P")
    second = first if fault == "level" else snapshot("Q")

    def source(level: str, captured: PeriodCalendarSnapshotV1) -> AnalysisFrame:
        return frame_sources.observe(
            session._runtime.local,
            session.catalog._state,
            session._runtime.connection_config,
            {"amount": metric},
            calendar_snapshots=(captured,),
            by=(
                session.time_scope(start=start, end=end).by(
                    semantic_grain(calendar=calendar, level=level, snapshot=captured)
                ),
            ),
        )

    left = source("short" if fault == "level" else "long", first)
    right = source("long", second)
    endpoint = right._schema.axes[0]
    assert isinstance(endpoint, CalendarDataAxis)
    assert isinstance(right._backing, ExpressionFrameBacking)
    binding = right._backing.root
    assert isinstance(binding, SourceFrameBinding)
    schema = DataSchema(
        (endpoint,),
        (
            DataColumn(
                "value.0", "amount", ScalarDataType("int64"), True, right._schema.values[0].unit
            ),
        ),
        value_paths=(("amount",),),
    )
    frozen = AnalysisFrame._from_memory(
        session._runtime.local,
        CompletedFrameResult(
            CompletedFrameData(
                schema,
                pd.DataFrame({endpoint.columns[0].slot_id: [0], "value.0": [2]}),
                tuple(calendar.directory for calendar in binding.calendars),
            ),
            FrameResultMetadata(
                "named-literal",
                FrameAudit(
                    (), EvaluationRecord(datetime(2026, 10, 19, tzinfo=timezone.utc), (), ())
                ),
            ),
        ),
    )
    frozen = frozen.select({"other": frozen["amount"]}).compute()
    right = right.select({"other": right["amount"]})
    assert isinstance(left._schema.axes[0], CalendarDataAxis)
    assert isinstance(right._schema.axes[0], CalendarDataAxis)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("named construction consulted source state or rebuilt its calendar")

    monkeypatch.setattr(frame_sources, "freeze_calendar", forbidden)
    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_evaluation.open_query_sources", forbidden
    )
    monkeypatch.setattr("marivo.analysis.session.core.Session.catalog", property(forbidden))
    monkeypatch.setattr(CompletedFrameData, "copy_rows", forbidden)
    monkeypatch.setattr(CompletedFrameData, "copy_attachments", forbidden)
    for baseline in (right, frozen):
        with pytest.raises(AnalysisError, match="compatible frozen"):
            left.compare(baseline, match=CalendarCorrespondence("ordinal"))
