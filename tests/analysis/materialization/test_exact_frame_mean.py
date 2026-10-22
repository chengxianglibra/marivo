"""Current-row means keep exact wide intermediates until output conversion."""

from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction

import duckdb
import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo.analysis.core.data_types import DecimalDataType, ScalarDataType
from marivo.analysis.core.frame_data import MemoryFrameBacking
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.methods.numeric_state import checked_sum
from tests.shared_fixtures import DslCase, DslCaseFactory, terminal_runs
from tests.support.source_trace import capture_source


def _values(case: DslCase, values: tuple[int | float | Decimal, ...]) -> AnalysisFrame:
    return case.session.values(
        by=(case.catalog.fields.get("sales.customer.customer_id"),),
        columns={"x": "CNY"},
        rows=tuple(((str(index),), {"x": value}) for index, value in enumerate(values)),
    )


def _mean(frame: AnalysisFrame) -> AnalysisFrame:
    return frame.reduce({"mean": frame["x"].mean()}).compute()


@pytest.mark.parametrize(
    "values",
    (
        (2**62, 2**62),
        (2**63 - 1, 2**63 - 1),
        (-(2**63), -(2**63)),
        (-(2**63), 2**63 - 1),
        (2**53 + 1, 1, 1),
        (2**63 - 1, 1, -(2**63), 1),
    ),
)
def test_integer_mean_uses_exact_wide_total(
    analysis_dsl_case_factory: DslCaseFactory, values: tuple[int, ...]
) -> None:
    case = analysis_dsl_case_factory("j1")
    frame = _values(case, values).compute()
    result = _mean(frame)
    expected = float(sum(map(Fraction, values), Fraction()) / len(values))
    exported = result.to_pandas()
    assert exported["value", "mean"].tolist() == [expected]
    assert pa.array(exported["value", "mean"]).type == pa.float64()
    assert isinstance(result._backing, MemoryFrameBacking)
    assert result._backing.data.schema.values[0].dtype == ScalarDataType("float64")
    if values == (2**53 + 1, 1, 1):
        assert expected != float(sum(values)) / len(values)
    total = frame.reduce({"sum": frame["x"].sum()}).compute()
    exported_sum = total.to_pandas()["value", "sum"]
    assert exported_sum.tolist() == [Decimal(sum(values))]
    assert pa.array(exported_sum).type == pa.decimal128(38, 0)
    assert total.top(0, by=total["sum"]).compute().to_pandas().empty
    assert terminal_runs(case.session) == ()


@pytest.mark.parametrize(
    "values",
    (
        (Decimal("999.99"), Decimal("999.99")),
        (Decimal("999.98"), Decimal("999.99")),
        (Decimal("-999.98"), Decimal("-999.99")),
        (Decimal("999999999999999999999999999999999999.99"),) * 2,
    ),
)
def test_decimal_mean_finishes_only_after_exact_division(
    analysis_dsl_case_factory: DslCaseFactory, values: tuple[Decimal, ...]
) -> None:
    case = analysis_dsl_case_factory("j1")
    frame = _values(case, values).compute()
    assert isinstance(frame._backing, MemoryFrameBacking)
    dtype = frame._backing.data.schema.values[0].dtype
    assert isinstance(dtype, DecimalDataType)
    exact = sum(map(Fraction, values), Fraction()) / len(values)
    with localcontext() as context:
        context.prec = 120
        expected = (Decimal(exact.numerator) / Decimal(exact.denominator)).quantize(
            Decimal(1).scaleb(-dtype.scale), rounding=ROUND_HALF_UP
        )
    result = _mean(frame)
    assert isinstance(result._backing, MemoryFrameBacking)
    assert result._backing.data.schema.values[0].dtype == dtype
    assert result.to_pandas()["value", "mean"].tolist() == [expected]
    assert expected.as_tuple().exponent == -dtype.scale
    total = frame.reduce({"sum": frame["x"].sum()})
    if values[0].adjusted() >= 35:
        with pytest.raises(MaterializationError, match=r"precision|decimal|Decimal"):
            total.compute()
    else:
        assert total.compute().to_pandas()["value", "sum"].tolist() == [sum(values)]
        assert pa.array(total.compute().to_pandas()["value", "sum"]).type == pa.decimal128(
            38, dtype.scale
        )
    assert terminal_runs(case.session) == ()


def test_wide_mean_pending_completed_and_persisted_are_identical(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    pending = _values(case, (2**62, 2**62))
    memory = pending.compute()
    stored = memory.persist()
    reopened = case.session.persisted_frame(stored.artifact.id)
    assert isinstance(memory._backing, MemoryFrameBacking)
    published_runs = terminal_runs(case.session)
    expected = _mean(pending).to_pandas()
    for frame in (memory, stored, reopened):
        result = _mean(frame)
        pd.testing.assert_frame_equal(result.to_pandas(), expected)
        assert isinstance(result._backing, MemoryFrameBacking)
        assert (
            result._backing.metadata.audit.source_facts
            == memory._backing.metadata.audit.source_facts
        )
    assert terminal_runs(case.session) == published_runs
    saved_mean = _mean(memory).persist()
    pd.testing.assert_frame_equal(
        case.session.persisted_frame(saved_mean.artifact.id).to_pandas(), expected
    )
    assert len(terminal_runs(case.session)) == len(published_runs) + 1


@pytest.mark.parametrize("value", (2**62, Decimal("999.99")))
def test_grouped_mean_masks_na_independently_and_keeps_empty_policy(
    analysis_dsl_case_factory: DslCaseFactory,
    value: int | Decimal,
) -> None:
    case = analysis_dsl_case_factory("j1")
    base = case.session.values(
        by=(
            case.catalog.fields.get("sales.customer.region"),
            case.catalog.fields.get("sales.customer.customer_id"),
        ),
        columns={"x": None, "y": None},
        rows=tuple(
            ((group, key), {"x": value, "y": other})
            for group, key, other in (("A", "1", 1), ("A", "2", 2), ("A", "3", 3), ("B", "4", 4))
        ),
    ).compute()
    subset = base.where(base.coord(base.axes[1]) < "3")
    joined = base.align(subset, on=tuple(zip(base.axes, subset.axes, strict=True)))
    nullable = joined.select({"x": joined["right.x"], "y": joined["left.y"]}).compute()
    result = nullable.reduce(
        {"x": nullable["x"].mean(), "y": nullable["y"].mean(), "rows": mv.row_count()},
        by=(nullable.axes[0],),
    ).compute()
    exported = result.to_pandas()
    expected = float(Fraction(value)) if type(value) is int else value
    assert exported["value", "x"].iloc[0] == expected
    assert pd.isna(exported["value", "x"].iloc[1])
    assert exported["value", "y"].tolist() == [2.0, 4.0]
    assert exported["value", "rows"].tolist() == [3, 1]
    assert result.presence_names == ()
    for frame in (nullable.where(nullable["x"].is_null()), nullable.top(0, by=nullable["y"])):
        scalar = _mean(frame).to_pandas()
        assert len(scalar) == 1 and pd.isna(scalar["value", "mean"].iloc[0])
    empty = nullable.top(0, by=nullable["y"])
    grouped = empty.reduce({"mean": empty["x"].mean()}, by=(empty.axes[0],)).compute()
    assert grouped.to_pandas().empty
    assert terminal_runs(case.session) == ()


def test_source_read_wide_mean_retains_receipts_and_decimal_sum(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            'INSERT INTO "order" VALUES '
            "('1','c1','web','paid','2026-08-01',?),('2','c1','web','paid','2026-08-01',?)",
            [2**62, 2**62],
        )
    source = case.session.read(
        case.session.members(case.session.catalog.entities.get("sales.order")),
        {"x": case.catalog.fields.get("sales.order.amount")},
    )
    trace = capture_source(monkeypatch)
    fixed = source.compute()
    assert trace.returned_rows == [2]
    assert isinstance(fixed._backing, MemoryFrameBacking)
    assert fixed._backing.metadata.audit.source_facts
    mean = _mean(fixed)
    assert mean.to_pandas()["value", "mean"].tolist() == [float(Fraction(2**63, 2))]
    assert isinstance(mean._backing, MemoryFrameBacking)
    assert mean._backing.metadata.audit.source_facts == fixed._backing.metadata.audit.source_facts
    for frame in (source, fixed):
        total = frame.reduce({"sum": frame["x"].sum()})
        assert total.compute().to_pandas()["value", "sum"].tolist() == [Decimal(2**63)]
        assert total.top(0, by=total["sum"]).compute().to_pandas().empty
    assert trace.returned_rows == [2, 2, 2]
    assert all(owner._closed for owner in trace.owners)
    assert terminal_runs(case.session) == ()


def test_mean_keeps_invalid_types_and_shared_stored_state_checks_strict(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    frame = _values(case, (1, 2)).compute()
    boolean = frame.select({"flag": frame["x"] > 0})
    with pytest.raises(AnalysisError):
        boolean.reduce({"mean": boolean["flag"].mean()}).compute()
    with pytest.raises(AnalysisError):
        frame.reduce({"mean": frame.coord(frame.axes[0]).mean()}).compute()
    for values, physical in (
        ([2**62, 2**62], pa.int64()),
        ([Decimal("999.99"), Decimal("999.99")], pa.decimal128(5, 2)),
        ([True], pa.int64()),
        ([1.0], pa.int64()),
        ([Decimal("NaN")], pa.decimal128(5, 2)),
        ([float("inf")], pa.float64()),
    ):
        with pytest.raises((ValueError, OverflowError)):
            checked_sum(values, physical)
    assert terminal_runs(case.session) == ()


def test_float_mean_keeps_compensated_sum_and_finite_policy(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    frame = _values(case, (1e16, 1.0, -1e16)).compute()
    assert _mean(frame).to_pandas()["value", "mean"].tolist() == [float(Fraction(1, 3))]
    overflow = _values(case, (1e308, 1e308)).compute()
    with pytest.raises(MaterializationError, match="overflow"):
        _mean(overflow)
    assert terminal_runs(case.session) == ()


def test_wide_mean_does_not_admit_overflowed_row_expression(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    frame = _values(case, (2**62, 2**62)).compute()
    invalid = frame.select({"x": frame["x"] + frame["x"]})
    with pytest.raises(MaterializationError, match="overflow"):
        _mean(invalid)
    assert terminal_runs(case.session) == ()
