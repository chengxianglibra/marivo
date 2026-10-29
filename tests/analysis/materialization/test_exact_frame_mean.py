"""Current-row means keep exact wide intermediates until output conversion."""

from decimal import Decimal
from fractions import Fraction

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pytest

import marivo.analysis as mv
from marivo.analysis.core.data_types import DecimalDataType, ScalarDataType
from marivo.analysis.core.frame_data import MemoryFrameBacking
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.errors import MaterializationError
from tests.shared_fixtures import DslCase, DslCaseFactory, catalog_value, terminal_runs
from tests.support.source_trace import capture_source


def _values(case: DslCase, values: tuple[int | float | Decimal, ...]) -> AnalysisFrame:
    return case.session.values(
        by=(catalog_value(case.catalog, "sales.customer.customer_id", kind="field"),),
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
def test_integer_mean_uses_native_arrow_accumulator(
    analysis_dsl_case_factory: DslCaseFactory, values: tuple[int, ...]
) -> None:
    case = analysis_dsl_case_factory("j1")
    frame = _values(case, values).compute()
    result = _mean(frame)
    expected = pc.mean(pa.array(values, type=pa.int64())).as_py()
    exported = result.to_pandas()
    assert exported["mean"].tolist() == [expected]
    assert pa.array(exported["mean"]).type == pa.float64()
    assert isinstance(result._backing, MemoryFrameBacking)
    assert result._backing.data.schema.values[0].dtype == ScalarDataType("float64")
    total = frame.reduce({"sum": frame["x"].sum()}).compute()
    exported_sum = total.to_pandas()["sum"]
    assert exported_sum.tolist() == [pc.sum(pa.array(values, type=pa.int64())).as_py()]
    assert pa.array(exported_sum).type == pa.int64()
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
def test_decimal_mean_uses_native_rounding_and_storage_boundary(
    analysis_dsl_case_factory: DslCaseFactory, values: tuple[Decimal, ...]
) -> None:
    case = analysis_dsl_case_factory("j1")
    frame = _values(case, values).compute()
    assert isinstance(frame._backing, MemoryFrameBacking)
    dtype = frame._backing.data.schema.values[0].dtype
    assert isinstance(dtype, DecimalDataType)
    expected = pc.mean(pa.array(values, type=pa.decimal128(dtype.precision, dtype.scale))).as_py()
    result = _mean(frame)
    assert isinstance(result._backing, MemoryFrameBacking)
    assert result._backing.data.schema.values[0].dtype == dtype
    assert result.to_pandas()["mean"].tolist() == [expected]
    assert expected.as_tuple().exponent == -dtype.scale
    total = frame.reduce({"sum": frame["x"].sum()})
    expected_sum = pc.sum(
        pa.array(values, type=pa.decimal128(dtype.precision, dtype.scale))
    ).as_py()
    try:
        pa.scalar(expected_sum, type=pa.decimal128(38, dtype.scale))
    except pa.ArrowInvalid:
        with pytest.raises(AnalysisError, match="lossless decimal"):
            total.compute()
    else:
        assert total.compute().to_pandas()["sum"].tolist() == [expected_sum]
    assert terminal_runs(case.session) == ()


def test_wide_mean_pending_completed_and_persisted_are_identical(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    pending = _values(case, (2**62, 2**62))
    memory = pending.compute()
    stored = memory.persist()
    reopened = case.session.persisted_frame(stored.artifact_id)
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
        case.session.persisted_frame(saved_mean.artifact_id).to_pandas(), expected
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
            catalog_value(case.catalog, "sales.customer.region", kind="field"),
            catalog_value(case.catalog, "sales.customer.customer_id", kind="field"),
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
    assert exported["x"].iloc[0] == expected
    assert pd.isna(exported["x"].iloc[1])
    assert exported["y"].tolist() == [2.0, 4.0]
    assert exported["rows"].tolist() == [3, 1]
    assert result.presence_names == ()
    for frame in (nullable.where(nullable["x"].is_null()), nullable.top(0, by=nullable["y"])):
        scalar = _mean(frame).to_pandas()
        assert len(scalar) == 1 and pd.isna(scalar["mean"].iloc[0])
    empty = nullable.top(0, by=nullable["y"])
    grouped = empty.reduce({"mean": empty["x"].mean()}, by=(empty.axes[0],)).compute()
    assert grouped.to_pandas().empty
    assert terminal_runs(case.session) == ()


def test_source_read_mean_retains_receipts_and_native_integer_sum(
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
        case.session.members(case.session.catalog.get("entity:sales.order")),
        {"x": catalog_value(case.catalog, "sales.order.amount", kind="field")},
    )
    trace = capture_source(monkeypatch)
    fixed = source.compute()
    assert trace.returned_rows == [2]
    assert isinstance(fixed._backing, MemoryFrameBacking)
    assert fixed._backing.metadata.audit.source_facts
    mean = _mean(fixed)
    assert mean.to_pandas()["mean"].tolist() == [float(Fraction(2**63, 2))]
    assert isinstance(mean._backing, MemoryFrameBacking)
    assert mean._backing.metadata.audit.source_facts == fixed._backing.metadata.audit.source_facts
    for frame in (source, fixed):
        total = frame.reduce({"sum": frame["x"].sum()})
        assert total.compute().to_pandas()["sum"].tolist() == [-(2**63)]
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
    assert terminal_runs(case.session) == ()


def test_float_mean_keeps_native_accumulation_and_nonfinite_results(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    frame = _values(case, (1e16, 1.0, -1e16)).compute()
    assert _mean(frame).to_pandas()["mean"].tolist() == [
        pc.mean(pa.array([1e16, 1.0, -1e16])).as_py()
    ]
    overflow = _values(case, (1e308, 1e308)).compute()
    assert _mean(overflow).to_pandas()["mean"].tolist() == [float("inf")]
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
