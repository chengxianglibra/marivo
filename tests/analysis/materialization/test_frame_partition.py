"""Partition reductions use original rows, full keys and existing numeric owners."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.core.data_schema import DataAxis, DataColumn, DataSchema
from marivo.analysis.core.data_types import (
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
from marivo.analysis.core.frame_data import CompletedFrameData, CompletedFrameResult
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.frame_services import LocalFrameServices
from tests.shared_fixtures import DslCaseFactory

INT64 = ScalarDataType("int64")


@pytest.fixture
def local(tmp_path: Path) -> LocalFrameServices:
    return DatasetRuntime.create(tmp_path, "frame-partition", timezone="UTC").local


def _frame(
    local: LocalFrameServices,
    values: tuple[object, ...],
    *,
    dtype: DataType = INT64,
    unit: str | None = "USD",
) -> AnalysisFrame:
    key = DataColumn("sample", "sample", ScalarDataType("int64"), False)
    schema = DataSchema(
        (DataAxis("sample", (key,), name="sample"),),
        (DataColumn("x", "x", dtype, True, unit),),
        value_paths=(("x",),),
    )
    rows = pd.DataFrame({"sample": list(range(len(values))), "x": list(values)}, dtype=object)
    return _memory(local, schema, rows)


def _memory(local: LocalFrameServices, schema: DataSchema, rows: pd.DataFrame) -> AnalysisFrame:
    metadata = FrameResultMetadata(
        "partition-input",
        FrameAudit((), EvaluationRecord(datetime(2026, 10, 21, tzinfo=timezone.utc), (), ())),
    )
    return AnalysisFrame._from_memory(
        local, CompletedFrameResult(CompletedFrameData(schema, rows), metadata)
    )


def test_nested_over_keeps_repeated_values_and_sample_masks(local: LocalFrameServices) -> None:
    frame = _frame(local, (1, 3, None))
    x = frame["x"]
    total = frame.over(x.sum())
    rows = (
        frame.select(
            {
                "total": total,
                "repeated_sum": frame.over(total.sum()),
                "repeated_mean": frame.over(total.mean()),
                "row_count": frame.over(mv.row_count()),
                "pair_count": frame.over(x.pair_count(x)),
                "corr": frame.over(x.corr(x, method="pearson")),
            }
        )
        .compute()
        .to_pandas()
    )
    assert rows[("coordinate", "sample")].tolist() == [0, 1, 2]
    assert rows[("value", "total")].tolist() == [4, 4, 4]
    assert rows[("value", "repeated_sum")].tolist() == [12, 12, 12]
    assert rows[("value", "repeated_mean")].tolist() == [4, 4, 4]
    assert rows[("value", "row_count")].tolist() == [3, 3, 3]
    assert rows[("value", "pair_count")].tolist() == [2, 2, 2]
    assert rows[("value", "corr")].tolist() == pytest.approx([1, 1, 1])
    all_axes = frame.select({"total": frame.over(x.sum(), by=frame.axes)}).compute().to_pandas()
    assert all_axes[("value", "total")].iloc[:2].tolist() == [1, 3]
    assert pd.isna(all_axes[("value", "total")].iloc[2])


def test_over_uses_complete_null_safe_large_integer_keys(local: LocalFrameServices) -> None:
    large = 2**53
    keys = (
        DataColumn("entity.a", "a", ScalarDataType("int64"), True),
        DataColumn("entity.b", "b", ScalarDataType("string"), True),
    )
    sample = DataColumn("sample", "sample", ScalarDataType("int64"), False)
    schema = DataSchema(
        (DataAxis("entity", keys, name="entity"), DataAxis("sample", (sample,), name="sample")),
        (DataColumn("x", "x", ScalarDataType("int64"), False, "USD"),),
        value_paths=(("x",),),
    )
    rows = pd.DataFrame(
        {
            "entity.a": [large, large + 1, None, large, None, large],
            "entity.b": ["a", "a", "a", "b", "a", "a"],
            "sample": [5, 4, 3, 2, 1, 0],
            "x": [1, 2, 3, 4, 5, 6],
        },
        dtype=object,
    )
    frame = _memory(local, schema, rows)
    result = frame.select({"total": frame.over(frame["x"].sum(), by=(frame.axes[0],))})
    output = result.compute().to_pandas()
    assert output[("coordinate", "sample")].tolist() == [5, 4, 3, 2, 1, 0]
    assert output[("value", "total")].tolist() == [7, 2, 8, 4, 8, 7]


@pytest.mark.parametrize("carrier", ("memory", "pending", "stored"))
def test_share_uses_current_fixed_data_on_all_carriers(
    local: LocalFrameServices, carrier: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = _frame(local, (-1, -3, None))
    if carrier == "pending":
        frame = frame.select({"x": frame["x"]})
    elif carrier == "stored":
        frame = frame.persist()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("fixed partition operations must not prepare sources")

    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_source_preparation.open_query_sources", forbidden
    )
    share = frame["x"].share(along=frame.axes)
    result = frame.select({"share": share}).compute()
    rows = result.to_pandas()
    assert rows[("value", "share")].iloc[:2].tolist() == [Decimal("0.25"), Decimal("0.75")]
    assert pd.isna(rows[("value", "share")].iloc[2])
    assert result._schema.values[0].unit == "1"


def test_share_filter_stage_and_signed_zero_total(local: LocalFrameServices) -> None:
    frame = _frame(local, (1, 3))
    selected = frame.select({"share": frame["x"].share(along=frame.axes)})
    after = selected.where(selected["share"] > Decimal("0.5")).compute().to_pandas()
    assert after[("value", "share")].tolist() == [Decimal("0.75")]
    filtered = frame.where(frame["x"] > 1)
    before = filtered.select({"share": filtered["x"].share(along=filtered.axes)})
    assert before.compute().to_pandas()[("value", "share")].tolist() == [1]
    cancelled = _frame(local, (1, -1))
    output = cancelled.select({"share": cancelled["x"].share(along=cancelled.axes)})
    assert output.compute().to_pandas()[("value", "share")].isna().all()
    signed = _frame(local, (3, -1))
    output = signed.select({"share": signed["x"].share(along=signed.axes)})
    assert output.compute().to_pandas()[("value", "share")].tolist() == [
        Decimal("1.5"),
        Decimal("-.5"),
    ]


def test_empty_partition_does_not_create_rows(local: LocalFrameServices) -> None:
    frame = _frame(local, ())
    output = frame.select(
        {"count": frame.over(mv.row_count()), "share": frame["x"].share(along=frame.axes)}
    ).compute()
    assert output.to_pandas().empty
    assert len(output.axes) == 1


def test_partition_division_masks_zeros_before_reduction(local: LocalFrameServices) -> None:
    frame = _frame(local, (0, 2))
    divided = frame["x"] / frame["x"]
    output = frame.select({"total": frame.over(divided.sum())})
    assert output.compute().to_pandas()[("value", "total")].tolist() == [1, 1]
    assert output.top(0, by=output["total"]).compute().to_pandas().empty
    cancelled = _frame(local, (1, -1))
    output = cancelled.select({"share": cancelled["x"].share(along=cancelled.axes)})
    assert output.compute().to_pandas()[("value", "share")].isna().all()


def test_over_rejects_different_exact_input_and_session(
    local: LocalFrameServices, tmp_path: Path
) -> None:
    frame = _frame(local, (1, 3))
    other = frame.select({"x": frame["x"]})
    with pytest.raises(AnalysisError):
        frame.over(other["x"].sum())
    with pytest.raises(AnalysisError):
        frame.over(frame["x"].sum(), by=other.axes)
    foreign = _frame(
        DatasetRuntime.create(tmp_path / "other", "other", timezone="UTC").local, (1, 3)
    )
    statistic = replace(foreign["x"].sum(), _input_id=frame._input_id)
    with pytest.raises(AnalysisError):
        frame.over(statistic)


def test_duration_share_and_unknown_unit_follow_existing_division(
    local: LocalFrameServices,
) -> None:
    frame = _frame(
        local, (timedelta(seconds=1), timedelta(seconds=3)), dtype=DurationDataType("us"), unit=None
    )
    share = frame["x"].share(along=frame.axes)
    output = frame.select({"share": share, "pairs": frame.over(share.pair_count(share))}).compute()
    assert output.to_pandas()[("value", "share")].tolist() == [0.25, 0.75]
    assert output.to_pandas()[("value", "pairs")].tolist() == [2, 2]
    assert output._schema.values[0].unit is None
    assert isinstance(output._schema, DataSchema)
    assert output._schema.values[0].dtype == ScalarDataType("float64")


def test_public_pending_values_partition_and_reopened_source_free_share(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    pending = case.session.values(
        by=(case.catalog.fields.get("sales.customer.customer_id"),),
        columns={"x": None},
        rows=((("A",), {"x": Decimal("1.25")}), (("B",), {"x": Decimal("3.75")})),
    )
    selected = pending.select(
        {"total": pending.over(pending["x"].sum()), "share": pending["x"].share(along=pending.axes)}
    ).compute()
    assert isinstance(selected._schema, DataSchema)
    assert selected._schema.values[0].dtype == DecimalDataType(38, 2)
    assert selected._schema.values[1].dtype == ScalarDataType("float64")
    assert selected._schema.values[1].unit is None
    assert selected.to_pandas()[("value", "total")].tolist() == [Decimal("5.00"), Decimal("5.00")]
    saved = pending.persist()
    reopened = case.session.persisted_frame(saved.artifact.id)
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("reopened partitions must not access source or Catalog")

    monkeypatch.setattr(type(case.session), "_sources", forbidden)
    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_source_preparation.open_query_sources", forbidden
    )
    result = reopened.select({"share": reopened["x"].share(along=reopened.axes)}).compute()
    assert result.to_pandas()[("value", "share")].tolist() == [0.25, 0.75]


def test_empty_scalar_over_and_all_null_statistic_keep_domains(
    local: LocalFrameServices, tmp_path: Path
) -> None:
    from marivo.analysis.session.core import Session

    session = Session._from_runtime(
        DatasetRuntime.create(tmp_path / "scalar", "scalar", timezone="UTC")
    )
    scalar = session.values(columns={"x": None}, rows=(((), {"x": 2}),))
    selected = scalar.select(
        {"count": scalar.over(mv.row_count()), "total": scalar.over(scalar["x"].sum())}
    )
    assert selected.compute().to_pandas()[("value", "total")].tolist() == [2]
    empty = scalar.top(0, by=scalar["x"])
    assert empty.select({"total": empty.over(empty["x"].sum())}).compute().to_pandas().empty
    nulls = _frame(local, (None, None))
    result = (
        nulls.select({"total": nulls.over(nulls["x"].sum()), "mean": nulls.over(nulls["x"].mean())})
        .compute()
        .to_pandas()
    )
    assert result[("value", "total")].isna().all()
    assert result[("value", "mean")].isna().all()


def test_over_keeps_interval_and_paired_current_axis_facts(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from marivo._definition.correspondence import CalendarCorrespondence
    from marivo.analysis.core.data_schema import IntervalDataAxis, PairedDataAxis

    case = analysis_dsl_case_factory("j2")
    scope = case.session.time_scope(start="2026-08-01", end="2026-08-03")
    interval = case.session.observe({"x": case.catalog.metrics.get("sales.revenue")}, by=(scope,))
    assert isinstance(interval._schema.axes[0], IntervalDataAxis)
    grouped = interval.select({"rows": interval.over(mv.row_count(), by=interval.axes)})
    assert grouped._schema.axes == interval._schema.axes
    assert grouped.compute().to_pandas()[("value", "rows")].tolist() == [1]
    daily = case.session.observe(
        {"x": case.catalog.metrics.get("sales.revenue")}, by=(scope.by(mv.grain("day")),)
    )
    paired = daily.align(daily, match=CalendarCorrespondence("ordinal"))
    assert isinstance(paired._schema.axes[0], PairedDataAxis)
    output = paired.select(
        {"rows": paired.over(mv.row_count(), by=paired.axes), "x": paired["left.x"]}
    )
    assert output._schema.axes == paired._schema.axes
    assert output.presence_names == paired.presence_names
    result = output.compute()
    assert result._schema.axes == paired._schema.axes
    assert result.to_pandas()[("value", "rows")].tolist() == [1, 1]


def test_over_preserves_wide_integer_sum_type(local: LocalFrameServices) -> None:
    frame = _frame(local, (2**63 - 1, 1))
    result = frame.select({"total": frame.over(frame["x"].sum())}).compute()
    assert isinstance(result._schema, DataSchema)
    assert result._schema.values[0].dtype == DecimalDataType(38, 0)
    assert result._schema.values[0].unit == "USD"
    assert result.to_pandas()[("value", "total")].tolist() == [Decimal(2**63), Decimal(2**63)]


def test_share_along_subset_uses_complement_partitions_and_normalizes_axis_order(
    local: LocalFrameServices,
) -> None:
    schema = DataSchema(
        tuple(
            DataAxis(
                name,
                (DataColumn(name, name, ScalarDataType("string"), name == "region"),),
                name=name,
            )
            for name in ("month", "region", "category")
        ),
        (DataColumn("x", "x", INT64, False, "USD"),),
        value_paths=(("x",),),
    )
    frame = _memory(
        local,
        schema,
        pd.DataFrame(
            {
                "month": ["Jan", "Jan", "Jan", "Jan", "Jan", "Feb"],
                "region": ["N", "S", None, "N", "S", "N"],
                "category": ["A", "A", "A", "B", "B", "A"],
                "x": [1, 3, 4, 2, 6, 8],
            },
            dtype=object,
        ),
    )
    _, region, category = frame.axes
    result = (
        frame.select(
            {
                "region_share": frame["x"].share(along=(region,)),
                "month_share": frame["x"].share(along=(region, category)),
                "reversed": frame["x"].share(along=(category, region)),
            }
        )
        .compute()
        .to_pandas()
    )
    assert result[("coordinate", "month")].tolist() == ["Jan"] * 5 + ["Feb"]
    assert result[("coordinate", "region")].isna().tolist() == [
        False,
        False,
        True,
        False,
        False,
        False,
    ]
    assert result[("value", "region_share")].tolist() == [0.125, 0.375, 0.5, 0.25, 0.75, 1]
    assert result[("value", "month_share")].tolist() == [0.0625, 0.1875, 0.25, 0.125, 0.375, 1]
    assert result[("value", "reversed")].tolist() == result[("value", "month_share")].tolist()


def test_public_month_region_share_journey_stays_fixed_after_source_offline(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    observed = session.observe(
        {"revenue": case.catalog.metrics.get("sales.revenue")},
        by=(
            session.time_scope(start="2026-07-01", end="2026-10-01").by(mv.grain("month")),
            case.catalog.fields.get("sales.customer.region"),
        ),
    ).compute()
    month = next(axis for axis in observed.axes if axis.role == "calendar")
    region = next(axis for axis in observed.axes if axis.name == "sales.customer.region")
    original = observed.to_pandas()
    east = original.loc[original[("coordinate", region.token)] == "east"]
    assert east[("value", "revenue")].tolist() == [200, 180, 230]
    east_months = east[("coordinate", month.token)].tolist()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("fixed monthly share journey must not read source or Catalog")

    monkeypatch.setattr(type(session), "_sources", forbidden)
    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_source_preparation.open_query_sources", forbidden
    )
    revenue = observed["revenue"]
    selected = observed.select(
        {
            "revenue": revenue,
            "total": observed.over(revenue.sum(), by=(month,)),
            "share": revenue.share(along=(region,)),
        }
    )
    selected_rows = selected.compute().to_pandas()
    selected_east = selected_rows.loc[selected_rows[("coordinate", region.token)] == "east"]
    assert selected_east[("value", "total")].tolist() == [250, 180, 230]
    assert selected_east[("value", "share")].tolist() == [0.8, 1, 1]
    filtered = selected.where(selected["share"] > 0.8)
    fixed = filtered.compute().to_pandas()
    assert fixed[("coordinate", month.token)].tolist() == east_months[1:]
    assert fixed[("coordinate", region.token)].tolist() == ["east", "east"]
    assert fixed[("value", "total")].tolist() == [180, 230]
    assert fixed[("value", "share")].tolist() == [1, 1]
    saved = filtered.persist()
    reopened = session.persisted_frame(saved.artifact.id)
    assert saved.to_pandas().equals(fixed)
    assert reopened.to_pandas().equals(fixed)
    reopened_month = next(axis for axis in reopened.axes if axis.role == "calendar")
    reopened_region = next(axis for axis in reopened.axes if axis.name == "sales.customer.region")
    continued = (
        reopened.select(
            {
                "original_total": reopened["total"],
                "original_share": reopened["share"],
                "total": reopened.over(reopened["revenue"].sum(), by=(reopened_month,)),
                "share": reopened["revenue"].share(along=(reopened_region,)),
            }
        )
        .compute()
        .to_pandas()
    )
    assert continued[("value", "original_total")].tolist() == [180, 230]
    assert continued[("value", "original_share")].tolist() == [1, 1]
    assert continued[("value", "total")].tolist() == [180, 230]
    assert continued[("value", "share")].tolist() == [1, 1]


def test_partition_evaluation_reuses_typed_results_without_sorting() -> None:
    from unittest.mock import patch

    from marivo._definition.query import (
        ReduceValues,
        SelectValues,
        Statistic,
        ValueBinary,
        ValuePartition,
        ValueSlot,
    )
    from marivo.analysis.compiler.frame_plan import frame_program
    from marivo.analysis.methods import frame_values

    group = DataColumn("group", "group", INT64, True)
    row = DataColumn("row", "row", INT64, False)
    schema = DataSchema(
        (DataAxis("group", (group,), name="group"), DataAxis("row", (row,), name="row")),
        (DataColumn("x", "x", INT64, True),),
        value_paths=(("x",),),
    )
    source = pd.DataFrame(
        {"group": [2, 1, 2, None], "row": [0, 1, 2, 3], "x": [10, 20, 30, 40]}, dtype=object
    )
    total = ValuePartition(Statistic("sum", ValueSlot(0)), (0,))
    # Separately constructed equal expressions also share the completed result.
    denominator = ValuePartition(Statistic("sum", ValueSlot(0)), (0,))
    program = frame_program(
        schema,
        SelectValues((("total", total), ("share", ValueBinary("div", ValueSlot(0), denominator)))),
    )
    with patch.object(
        frame_values, "partition_series", wraps=frame_values.partition_series
    ) as partition:
        with patch.object(
            pd.DataFrame, "sort_values", side_effect=AssertionError("broadcast sorted")
        ):
            result = frame_values.evaluate_frame(program, (source,))
        assert partition.call_count == 1
        assert result[program.output_schema.values[0].slot_id].tolist() == [40, 20, 40, 40]
        assert result[program.output_schema.values[1].slot_id].tolist() == [0.25, 1, 0.75, 1]
        changed = source.copy()
        changed["x"] = [1, 2, 3, 4]
        again = frame_values.evaluate_frame(program, (changed,))
        assert partition.call_count == 2
        assert again[program.output_schema.values[0].slot_id].tolist() == [4, 2, 4, 4]
    reduced = frame_values.evaluate_frame(
        frame_program(schema, ReduceValues((("sum", Statistic("sum", ValueSlot(0))),), (0,))),
        (source,),
    )
    assert pd.isna(reduced["group"].iloc[0])
    assert reduced["group"].iloc[1:].tolist() == [1, 2]


def test_partition_cache_distinguishes_literal_carriers(local: LocalFrameServices) -> None:
    frame = _frame(local, (1, 3))
    x = frame["x"]
    rows = (
        frame.select(
            {
                "integer": frame.over((x + 1).sum()),
                "float": frame.over((x + 1.0).sum()),
                "decimal": frame.over((x + Decimal("1")).sum()),
            }
        )
        .compute()
        .to_pandas()
    )
    assert rows[("value", "integer")].tolist() == [Decimal(6), Decimal(6)]
    assert rows[("value", "float")].tolist() == [6.0, 6.0]
    assert "decimal" in str(rows[("value", "integer")].dtype)
    assert "double" in str(rows[("value", "float")].dtype)
    assert "decimal" in str(rows[("value", "decimal")].dtype)


def test_top_zero_checks_score_without_sorting(local: LocalFrameServices) -> None:
    from unittest.mock import patch

    frame = _frame(local, (1, 3, None))
    with patch.object(pd.DataFrame, "sort_values", side_effect=AssertionError("empty Top sorted")):
        result = frame.top(0, by=frame["x"]).compute().to_pandas()
    assert result.empty
    assert str(result[("value", "x")].dtype) == "int64[pyarrow]"
