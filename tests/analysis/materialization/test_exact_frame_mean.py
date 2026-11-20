"""Current-row statistics keep their native numeric carriers through completion."""

from datetime import timedelta
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
from tests.shared_fixtures import DslCase, DslCaseFactory, catalog_get, catalog_value, terminal_runs
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
        with pytest.raises(AnalysisError):
            total.compute()
    else:
        assert total.compute().to_pandas()["sum"].tolist() == [expected_sum]


def test_reduction_preserves_numeric_carriers_on_pending_fixed_and_reopened_inputs(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    ticks = 2**53 + 1
    literal = case.session.values(
        by=(catalog_value(case.catalog, "sales.customer.customer_id", kind="field"),),
        columns={"x": None, "exact": "CNY", "elapsed": None},
        rows=tuple(
            (
                (key,),
                {"x": 2**63 - 1, "exact": Decimal(exact), "elapsed": timedelta(microseconds=1)},
            )
            for key, exact in (("1", "999.98"), ("2", "999.99"))
        ),
    )
    pending = literal.select(
        {"x": literal["x"], "exact": literal["exact"], "elapsed": literal["elapsed"] * ticks}
    )
    memory = pending.compute()
    stored = memory.persist()
    reopened = case.session.persisted_frame(stored.artifact_id)
    assert isinstance(memory._backing, MemoryFrameBacking)
    published_runs = terminal_runs(case.session)

    def reduced(frame: AnalysisFrame) -> AnalysisFrame:
        return frame.reduce(
            {
                "integer_mean": frame["x"].mean(),
                "integer_sum": frame["x"].sum(),
                "decimal_mean": frame["exact"].mean(),
                "duration_mean": frame["elapsed"].mean(),
                "duration_sum": frame["elapsed"].sum(),
            }
        ).compute()

    for frame in (pending, memory, stored, reopened):
        result = reduced(frame)
        values = result.to_pandas()
        assert values["integer_mean"].tolist() == [float(2**63 - 1)]
        assert values["integer_sum"].tolist() == [-2]
        assert values["decimal_mean"].tolist() == [Decimal("999.99")]
        duration_mean = pa.array(values["duration_mean"])
        duration_sum = pa.array(values["duration_sum"])
        assert duration_mean.type == duration_sum.type == pa.duration("us")
        assert duration_mean.cast(pa.int64()).to_pylist() == [ticks]
        assert duration_sum.cast(pa.int64()).to_pylist() == [2 * ticks]
        assert isinstance(result._backing, MemoryFrameBacking)
        assert result._backing.metadata.source_reads == memory._backing.metadata.source_reads
    assert terminal_runs(case.session) == published_runs
    result = reduced(memory)
    saved_reduction = result.persist()
    pd.testing.assert_frame_equal(
        case.session.persisted_frame(saved_reduction.artifact_id).to_pandas(), result.to_pandas()
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
    all_null = nullable.where(nullable["x"].is_null())
    empty = nullable.top(0, by=nullable["y"])
    for frame, expected_count in ((all_null, 2), (empty, 0)):
        scalar = (
            frame.reduce(
                {"mean": frame["x"].mean(), "sum": frame["x"].sum(), "rows": mv.row_count()}
            )
            .compute()
            .to_pandas()
        )
        assert len(scalar) == 1 and pd.isna(scalar["mean"].iloc[0])
        assert scalar["rows"].tolist() == [expected_count]
        if expected_count:
            assert pd.isna(scalar["sum"].iloc[0])
        else:
            assert scalar["sum"].tolist() == [0]
    grouped = empty.reduce({"mean": empty["x"].mean()}, by=(empty.axes[0],)).compute()
    assert grouped.to_pandas().empty


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
        case.session.members(
            catalog_get(case.session.catalog, "entity:sales.order", kind="entity")
        ),
        {"x": catalog_value(case.catalog, "sales.order.amount", kind="field")},
    )
    trace = capture_source(monkeypatch)
    fixed = source.compute()
    assert trace.returned_rows == [2]
    assert isinstance(fixed._backing, MemoryFrameBacking)
    assert fixed._backing.metadata.source_reads
    mean = _mean(fixed)
    assert mean.to_pandas()["mean"].tolist() == [float(Fraction(2**63, 2))]
    assert isinstance(mean._backing, MemoryFrameBacking)
    assert mean._backing.metadata.source_reads == fixed._backing.metadata.source_reads
    for frame in (source, fixed):
        total = frame.reduce({"sum": frame["x"].sum()})
        assert total.compute().to_pandas()["sum"].tolist() == [-(2**63)]
        assert total.top(0, by=total["sum"]).compute().to_pandas().empty
    assert trace.returned_rows == [2, 2, 2]
    assert all(owner._closed for owner in trace.owners)


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
