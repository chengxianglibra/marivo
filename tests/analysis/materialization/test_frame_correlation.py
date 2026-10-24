"""Current-row correlation acceptance with independent small numeric witnesses."""

from __future__ import annotations

import inspect
import math
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._definition.graph import fingerprint
from marivo.analysis.core.data_schema import DataAxis, DataSchema
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    ExpressionFrameBacking,
    MemoryFrameBacking,
)
from marivo.analysis.core.frame_structure import PendingDataSchema
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.datasource.adapters import CompiledRead, SourceSession
from tests.shared_fixtures import DslCase, DslCaseFactory, export_dsl_parquet_models, terminal_runs
from tests.support.source_trace import capture_source

Number = int | float | Decimal | None
Method = Literal["pearson", "spearman"]
INT64 = ScalarDataType("int64")


@pytest.fixture
def local(tmp_path: Path) -> LocalFrameServices:
    return DatasetRuntime.create(tmp_path, "frame-correlation", timezone="UTC").local


def _memory(local: LocalFrameServices, schema: DataSchema, rows: pd.DataFrame) -> AnalysisFrame:
    metadata = FrameResultMetadata(
        "correlation-input",
        FrameAudit((), EvaluationRecord(datetime(2026, 10, 20, tzinfo=timezone.utc), (), ())),
    )
    return AnalysisFrame._from_memory(
        local, CompletedFrameResult(CompletedFrameData(schema, rows), metadata)
    )


def _pairs(
    local: LocalFrameServices,
    x: tuple[Number, ...],
    y: tuple[Number, ...],
    *,
    x_type: DataType = INT64,
    y_type: DataType = INT64,
) -> AnalysisFrame:
    key = DataColumn("sample", "sample", ScalarDataType("int64"), False)
    schema = DataSchema(
        (DataAxis("sample", (key,), name="sample"),),
        (DataColumn("x", "x", x_type, True, "USD"), DataColumn("y", "y", y_type, True, "kg")),
        value_paths=(("x",), ("y",)),
    )
    return _memory(
        local,
        schema,
        pd.DataFrame({"sample": list(range(len(x))), "x": list(x), "y": list(y)}, dtype=object),
    )


def _composed(frame: AnalysisFrame, method: Method, *, grouped: bool = False) -> AnalysisFrame:
    x, y = frame["x"], frame["y"]
    return frame.reduce(
        {"coefficient": x.corr(y, method=method), "n_pairs": x.pair_count(y)},
        by=(frame.axes[0],) if grouped else (),
    )


def _assert_schema(frame: AnalysisFrame) -> None:
    assert type(frame) is AnalysisFrame
    assert frame.output_names == ("coefficient", "n_pairs")
    assert frame.presence_names == ()
    coefficient, count = frame._schema.values
    assert (coefficient.nullable, coefficient.unit) == (True, "1")
    assert (count.nullable, count.unit) == (False, None)
    if isinstance(frame._schema, PendingDataSchema):
        assert coefficient.kind == count.kind == "numeric"
    else:
        assert coefficient.dtype == ScalarDataType("float64")
        assert count.dtype == ScalarDataType("int64")


def test_required_method_binding_and_invalid_arguments(local: LocalFrameServices) -> None:
    """C01: both endpoints and grouping handles belong to the exact receiver."""
    frame = _pairs(local, (1, 2), (2, 3))
    other = frame.select({"x": frame["x"], "y": frame["y"]})
    for method in (AnalysisFrame.correlate, type(frame["x"]).corr):
        assert inspect.signature(method).parameters["method"].default is inspect.Parameter.empty
    assert "method" not in inspect.signature(type(frame["x"]).pair_count).parameters
    for method in ("kendall", "Pearson", "pair_count", None):
        with pytest.raises(AnalysisError):
            frame.correlate(frame["x"], frame["y"], method=method)
        with pytest.raises(AnalysisError):
            frame["x"].corr(frame["y"], method=method)
    for operand in ("x", 1, [1, 2], frame, frame.coord(frame.axes[0])._expression):
        with pytest.raises(AnalysisError):
            frame.correlate(operand, frame["y"], method="pearson")
        with pytest.raises(AnalysisError):
            frame["x"].pair_count(operand)
    with pytest.raises(AnalysisError):
        frame["x"].corr(other["y"], method="pearson")
    with pytest.raises(AnalysisError):
        frame.correlate(other["x"], other["y"], method="pearson")
    with pytest.raises(AnalysisError):
        frame.correlate(frame["x"], other["y"], method="pearson")
    with pytest.raises(AnalysisError):
        frame.correlate(frame["x"], frame["y"], method="pearson", by=other.axes)
    with pytest.raises(AnalysisError):
        other.reduce({"r": frame["x"].corr(frame["y"], method="pearson")})


def test_cross_session_pair_binding(local: LocalFrameServices, tmp_path: Path) -> None:
    """C01: even a forged equal input identity cannot cross Store/Session ownership."""
    frame = _pairs(local, (1, 2), (2, 3))
    foreign_local = DatasetRuntime.create(tmp_path / "other", "other", timezone="UTC").local
    foreign = _pairs(foreign_local, (1, 2), (2, 3))
    column = replace(foreign["y"], _input_id=frame._input_id)
    with pytest.raises(AnalysisError):
        frame["x"].pair_count(column)
    with pytest.raises(AnalysisError):
        frame.correlate(frame["x"], column, method="pearson")


@pytest.mark.parametrize("method", ("pearson", "spearman"))
def test_group_domain_masks_composite_keys_and_two_layer_equivalence(
    local: LocalFrameServices, method: Method
) -> None:
    """C02/C03/C10/C11: the design A/B/C data, complete keys, Nulls and presence."""
    country = DataColumn("country", "country", ScalarDataType("string"), True)
    city = DataColumn("city", "city", ScalarDataType("string"), False)
    sample = DataColumn("sample", "sample", ScalarDataType("int64"), False)
    schema = DataSchema(
        (
            DataAxis("region", (country, city), name="region"),
            DataAxis("sample", (sample,), name="sample"),
        ),
        tuple(DataColumn(name, name, ScalarDataType("int64"), True) for name in ("x", "y", "z")),
        (DataColumn("present", "present", ScalarDataType("boolean"), False),),
        value_paths=(("x",), ("y",), ("z",)),
        presence_paths=(("present",),),
    )
    frame = _memory(
        local,
        schema,
        pd.DataFrame(
            {
                "country": ["US"] * 4 + ["CN"] * 2 + [None] * 2 + ["US"] * 2,
                "city": ["X"] * 8 + ["Y"] * 2,
                "sample": range(10),
                "x": [1, 2, 3, None, 7, 7, 1, None, 2, 3],
                "y": [1, None, 3, 4, 10, 20, None, 4, 4, 6],
                "z": [None, 2, 3, 4, None, None, None, None, None, None],
                "present": [False] * 10,
            },
            dtype=object,
        ),
    )
    pending = frame.correlate(frame["x"], frame["y"], method=method, by=(frame.axes[0],))
    _assert_schema(pending)
    assert isinstance(pending._backing, ExpressionFrameBacking)
    result = pending.compute()
    _assert_schema(result)
    assert result._schema.axes == (schema.axes[0],)
    rows = result._backing.data.copy_rows()
    assert rows["country"].tolist() == [pd.NA, "CN", "US", "US"]
    assert rows["city"].tolist() == ["X"] * 3 + ["Y"]
    table = result.to_pandas()
    assert table["value", "n_pairs"].tolist() == [0, 2, 2, 2]
    assert table["value", "coefficient"].isna().tolist() == [True, True, False, False]
    assert table["value", "coefficient"].iloc[2] == pytest.approx(1.0, abs=1e-12)
    pd.testing.assert_frame_equal(
        _composed(frame, method, grouped=True).compute().to_pandas(), table
    )
    x, y = frame["x"], frame["y"]
    combined = (
        frame.reduce(
            {
                "xy": x.pair_count(y),
                "xx": x.pair_count(x),
                "xz": x.pair_count(frame["z"]),
                "sum": x.sum(),
                "mean_x": x.mean(),
                "mean_y": y.mean(),
                "rows": mv.row_count(),
            },
            by=(frame.axes[0],),
        )
        .compute()
        .to_pandas()
    )
    assert combined["value", "xx"].tolist() == [1, 2, 3, 2]
    assert combined["value", "xz"].tolist() == [0, 0, 2, 0]
    assert combined["value", "sum"].tolist() == [1, 14, 6, 5]
    assert combined["value", "mean_x"].tolist() == [1.0, 7.0, 2.0, 2.5]
    assert combined["value", "mean_y"].tolist() == pytest.approx([4.0, 15.0, 8 / 3, 5.0])
    assert combined["value", "rows"].tolist() == [2, 2, 4, 2]


def test_symmetry_keeps_ordered_capture_and_self_pair_count(local: LocalFrameServices) -> None:
    """C02: symmetry is a numeric property, not a normalized expression identity."""
    frame = _pairs(local, (1, 2, None, 4), (4, None, 2, 1))
    x, y = frame["x"], frame["y"]
    forward, backward = x.corr(y, method="pearson"), y.corr(x, method="pearson")
    assert fingerprint(forward._definition) != fingerprint(backward._definition)
    result = (
        frame.reduce(
            {
                "xy": forward,
                "yx": backward,
                "self": x.corr(x, method="pearson"),
                "n": x.pair_count(x),
            }
        )
        .compute()
        .to_pandas()
    )
    assert result[[("value", "xy"), ("value", "yx"), ("value", "self")]].iloc[
        0
    ].tolist() == pytest.approx([-1.0, -1.0, 1.0], abs=1e-12)
    assert result["value", "n"].tolist() == [3]


@pytest.mark.parametrize("method,expected", (("pearson", 3 / math.sqrt(21)), ("spearman", 0.5)))
def test_pair_before_rank_and_fixed_frame_continuation(
    local: LocalFrameServices, method: Method, expected: float
) -> None:
    """C04/C11: missing x ranks form a gap; fixed result has ordinary continuations."""
    frame = _pairs(local, (1, 2, 3, 4), (1, None, 3, 2))
    result = frame.correlate(frame["x"], frame["y"], method=method).compute()
    table = result.to_pandas()
    assert table["value", "coefficient"].iloc[0] == pytest.approx(expected, abs=1e-12)
    assert table["value", "n_pairs"].tolist() == [3]
    pd.testing.assert_frame_equal(_composed(frame, method).compute().to_pandas(), table)
    filtered = result.where(result["n_pairs"] >= 3)
    copied = filtered.select({"r": filtered["coefficient"]}).compute()
    assert copied.output_names == ("r",)
    assert copied.to_pandas()["value", "r"].iloc[0] == pytest.approx(expected, abs=1e-12)


def test_spearman_average_ties_and_group_local_ranks(local: LocalFrameServices) -> None:
    """C04: ties are retained and a second group's values cannot disturb local ranks."""
    frame = _pairs(local, (10, 10, 30), (1, 2, 3))
    result = frame.correlate(frame["x"], frame["y"], method="spearman").compute().to_pandas()
    assert result["value", "coefficient"].iloc[0] == pytest.approx(math.sqrt(3) / 2, abs=1e-12)
    assert result["value", "n_pairs"].tolist() == [3]
    group = DataColumn("group", "group", ScalarDataType("string"), False)
    schema = replace(
        frame._schema, axes=(DataAxis("group", (group,), name="group"), *frame._schema.axes)
    )
    grouped = _memory(
        local,
        schema,
        pd.DataFrame(
            {
                "group": ["B", "A", "B", "A", "B", "A"],
                "sample": range(6),
                "x": [2, 1, 3, 4, 6, 5],
                "y": [3, 1, 5, 2, 6, 6],
            },
            dtype=object,
        ),
    )
    result = grouped.correlate(
        grouped["x"], grouped["y"], method="spearman", by=(grouped.axes[0],)
    ).compute()
    assert result.to_pandas()["value", "coefficient"].tolist() == pytest.approx(
        [1.0, 1.0], abs=1e-12
    )


@pytest.mark.parametrize(
    "x,y,count,expected",
    (
        ((), (), 0, None),
        ((1,), (None,), 0, None),
        ((1,), (2,), 1, None),
        ((7, 7), (1, 2), 2, None),
        ((1, 2), (2, 1), 2, -1.0),
    ),
)
def test_degenerate_and_empty_scalar(
    local: LocalFrameServices,
    x: tuple[Number, ...],
    y: tuple[Number, ...],
    count: int,
    expected: float | None,
) -> None:
    """C05: an undefined coefficient never discards its actual pair count."""
    frame = _pairs(local, x, y)
    for method in ("pearson", "spearman"):
        result = frame.correlate(frame["x"], frame["y"], method=method).compute()
        _assert_schema(result)
        table = result.to_pandas()
        assert len(table) == 1 and result.axes == ()
        assert table["value", "n_pairs"].tolist() == [count]
        value = table["value", "coefficient"].iloc[0]
        assert pd.isna(value) if expected is None else value == pytest.approx(expected, abs=1e-12)
    if not x:
        grouped = frame.correlate(frame["x"], frame["y"], method="pearson", by=frame.axes).compute()
        _assert_schema(grouped)
        assert grouped.to_pandas().empty and len(grouped.axes) == 1


@pytest.mark.parametrize(
    "x,y,x_type,expected",
    (
        ((2**53, 2**53 + 1, 2**53 + 2), (1, 2, 3), ScalarDataType("int64"), 1.0),
        (
            (
                Decimal("1000000000000000000000000.00"),
                Decimal("1000000000000000000000000.01"),
                Decimal("1000000000000000000000000.02"),
            ),
            (1, 2, 3),
            DecimalDataType(27, 2),
            1.0,
        ),
        ((1.0, 1.0 + 2**-52, 1.0 + 2**-51), (1, 2, 3), ScalarDataType("float64"), 1.0),
        ((1e308, -1e308, 0.0), (1, -1, 0), ScalarDataType("float64"), 1.0),
        (
            (
                0.0,
                float.fromhex("0x0.0000000000001p-1022"),
                float.fromhex("0x0.0000000000002p-1022"),
            ),
            (1, 2, 3),
            ScalarDataType("float64"),
            1.0,
        ),
        (
            (1.3, 1.3 + 2**-52, 1.3 + 3 * 2**-52),
            (1, 2, 3),
            ScalarDataType("float64"),
            math.sqrt(27 / 28),
        ),
    ),
)
def test_numeric_precision_witnesses(
    local: LocalFrameServices,
    x: tuple[Number, ...],
    y: tuple[Number, ...],
    x_type: DataType,
    expected: float,
) -> None:
    """C06: preserve exact differences, adjacent floats and binary scaling."""
    frame = _pairs(local, x, y, x_type=x_type)
    for method, oracle in (("pearson", expected), ("spearman", 1.0)):
        result = frame.correlate(frame["x"], frame["y"], method=method).compute()
        _assert_schema(result)
        table = result.to_pandas()
        assert table["value", "n_pairs"].tolist() == [3]
        assert table["value", "coefficient"].iloc[0] == pytest.approx(oracle, abs=1e-12)


def test_float64_decimal_division_endpoints_match_explicit_select(
    local: LocalFrameServices,
) -> None:
    """C06: finish a numeric expression's float64 carrier at either paired endpoint."""
    frame = _pairs(
        local,
        (Decimal("1.00"), Decimal("2.00"), Decimal("3.00")),
        (1, 2, 3),
        x_type=DecimalDataType(8, 2),
    )
    divided = frame["x"] / 2
    selected = frame.select({"x": divided, "y": frame["y"]}).compute()
    assert selected.to_pandas()["value", "x"].tolist() == [0.5, 1.0, 1.5]
    expected = (
        selected.correlate(selected["x"], selected["y"], method="pearson").compute().to_pandas()
    )
    assert expected["value", "coefficient"].iloc[0] == pytest.approx(1.0, abs=1e-12)
    assert expected["value", "n_pairs"].tolist() == [3]
    for left, right in ((divided, frame["y"]), (frame["y"], divided)):
        direct = frame.correlate(left, right, method="pearson").compute().to_pandas()
        combined = (
            frame.reduce(
                {
                    "coefficient": left.corr(right, method="pearson"),
                    "n_pairs": left.pair_count(right),
                }
            )
            .compute()
            .to_pandas()
        )
        counted = frame.reduce({"n_pairs": left.pair_count(right)}).compute().to_pandas()
        pd.testing.assert_frame_equal(direct, expected)
        pd.testing.assert_frame_equal(combined, expected)
        pd.testing.assert_frame_equal(counted, expected[[("value", "n_pairs")]])


def test_known_duration_rejected_at_each_endpoint(local: LocalFrameServices) -> None:
    """C06: numeric ticks require the caller's explicit external conversion."""
    numeric = _pairs(local, (1, 2), (2, 3))
    schema = replace(
        numeric._schema,
        values=(
            replace(numeric._schema.values[0], dtype=DurationDataType("ns")),
            numeric._schema.values[1],
        ),
    )
    rows = numeric._backing.data.copy_rows()
    rows["x"] = pd.Series(pd.arrays.ArrowExtensionArray(pa.array([1, 2], type=pa.duration("ns"))))
    frame = _memory(local, schema, rows)
    for left, right in ((frame["x"], frame["y"]), (frame["y"], frame["x"])):
        with pytest.raises(AnalysisError, match=r"[Dd]uration") as failure:
            frame.correlate(left, right, method="pearson")
        assert failure.value.repair is not None
        with pytest.raises(AnalysisError, match=r"[Dd]uration"):
            left.pair_count(right)


@pytest.mark.parametrize("continuation", ("direct", "top_zero", "pruned", "filtered"))
def test_strict_subexpression_is_not_hidden_by_na_or_continuations(
    local: LocalFrameServices, continuation: str
) -> None:
    """C07/C11: both API layers retain child checking before the pair mask."""
    frame = _pairs(local, (1, 2), (None, None))
    strict = frame["x"] * 1e308
    candidates = (
        frame.correlate(strict, frame["y"], method="pearson"),
        frame.reduce(
            {
                "coefficient": strict.corr(frame["y"], method="pearson"),
                "n_pairs": strict.pair_count(frame["y"]),
            }
        ),
    )
    for candidate in candidates:
        if continuation == "top_zero":
            candidate = candidate.top(0, by=candidate["n_pairs"])
        elif continuation == "pruned":
            candidate = candidate.select({"constant": 1})
        elif continuation == "filtered":
            candidate = candidate.where(candidate["n_pairs"] < 0)
        with pytest.raises(MaterializationError, match="finite") as failure:
            candidate.compute()
        assert failure.value.repair is not None


def test_pair_count_does_not_compute_coefficients_or_ranks(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.methods import frame_correlation, frame_values

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("pair_count evaluated correlation or ranks")

    monkeypatch.setattr(frame_values, "correlation", forbidden)
    monkeypatch.setattr(frame_correlation, "_ranks", forbidden)
    frame = _pairs(local, (1, 2, None), (1, None, 3))
    result = frame.reduce({"n": frame["x"].pair_count(frame["y"])}).compute().to_pandas()
    assert result["value", "n"].tolist() == [1]


@pytest.mark.parametrize(
    "differences,expected",
    (((0.0, 0.0, 0.0), "zero computational scale"), ((0.0, math.inf, 1.0), "nonfinite")),
)
def test_numeric_intermediate_failures_are_structured_and_never_clipped(
    local: LocalFrameServices,
    monkeypatch: pytest.MonkeyPatch,
    differences: tuple[float, ...],
    expected: str,
) -> None:
    """C07: simulated arithmetic failure cannot become NA or a clipped coefficient."""
    from marivo.analysis.methods import frame_correlation

    def failed(*args: object) -> tuple[float, ...]:
        return differences

    monkeypatch.setattr(frame_correlation, "_differences", failed)
    frame = _pairs(local, (1, 2, 3), (2, 3, 4))
    candidate = frame.correlate(frame["x"], frame["y"], method="pearson")
    with pytest.raises(AnalysisError, match=expected) as failure:
        candidate.compute()
    assert failure.value.repair is not None
    assert failure.value.location == "frame.correlate"
    assert failure.value.received is not None
    assert "pearson" in failure.value.received
    assert "ValueSlot(index=0)" in failure.value.received
    assert "ValueSlot(index=1)" in failure.value.received


def test_complete_key_alignment_zero_and_explicit_presence(local: LocalFrameServices) -> None:
    """C08: full composite keys align samples; presence never silently filters pairs."""
    sample = DataAxis(
        "sample",
        (
            DataColumn("tenant", "tenant", ScalarDataType("string"), False),
            DataColumn("id", "id", ScalarDataType("int64"), False),
        ),
        name="sample",
    )
    schema = DataSchema(
        (sample,), (DataColumn("x", "x", ScalarDataType("int64"), True),), value_paths=(("x",),)
    )
    left = _memory(
        local,
        schema,
        pd.DataFrame({"tenant": ["B", "A", "A"], "id": [1, 2, 1], "x": [9, 1, 0]}, dtype=object),
    )
    right = _memory(
        local,
        schema,
        pd.DataFrame({"tenant": ["A", "A", "C"], "id": [1, 2, 1], "x": [0, 2, 4]}, dtype=object),
    )
    aligned = left.align(right, on=((left.axes[0], right.axes[0]),)).compute()
    result = (
        aligned.correlate(aligned["left.x"], aligned["right.x"], method="pearson")
        .compute()
        .to_pandas()
    )
    assert result["value", "coefficient"].iloc[0] == pytest.approx(1.0, abs=1e-12)
    assert result["value", "n_pairs"].tolist() == [2]
    both = aligned.where(aligned["left_present"] & aligned["right_present"])
    filtered = (
        both.correlate(both["left.x"], both["right.x"], method="pearson").compute().to_pandas()
    )
    pd.testing.assert_frame_equal(result, filtered)
    rows = aligned._backing.data.copy_rows()
    for column in aligned._schema.presence:
        rows[column.slot_id] = False
    marked = _memory(local, aligned._schema, rows)
    marked_result = (
        marked.correlate(marked["left.x"], marked["right.x"], method="pearson")
        .compute()
        .to_pandas()
    )
    assert marked_result["value", "coefficient"].iloc[0] == pytest.approx(1.0, abs=1e-12)
    assert marked_result["value", "n_pairs"].tolist() == [2]
    empty = marked.where(marked["left_present"] & marked["right_present"])
    assert (
        empty.correlate(empty["left.x"], empty["right.x"], method="pearson", by=empty.axes)
        .compute()
        .to_pandas()
        .empty
    )


def _source(case: DslCase) -> AnalysisFrame:
    amount = case.session.catalog.fields.get("sales.order.amount")
    return case.session.read(
        case.session.members(case.session.catalog.entities.get("sales.order")),
        {"x": amount, "y": amount},
    )


def test_pending_correlate_shares_one_mutable_source_batch(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """C01/C11: one business read and both outputs witness the same first batch."""
    case = analysis_dsl_case_factory("j2")
    source = _source(case)
    execute = SourceSession.execute
    reads = 0

    def mutable(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        nonlocal reads
        table = execute(owner, read)
        if read.purpose == "query.prepare":
            return table
        reads += 1
        values = ((1, 1), (2, 2), (3, 3)) if reads == 1 else ((1, 3), (2, 1))
        table = table.iloc[: len(values)].copy()
        names = tuple(name for name in table if str(name).startswith("value."))
        assert len(names) == 2
        for index, name in enumerate(names):
            table[name] = pd.Series(
                [pair[index] for pair in values], index=table.index, dtype="int64[pyarrow]"
            )
        return table

    monkeypatch.setattr(SourceSession, "execute", mutable)
    trace = capture_source(monkeypatch)
    result = source.correlate(source["x"], source["y"], method="pearson")
    _assert_schema(result)
    result.show()
    assert reads == 0 and trace.native_sql == []
    shown = capsys.readouterr().out
    assert "pearson" in shown and "coefficient" in shown and "n_pairs" in shown
    completed = result.compute()
    _assert_schema(completed)
    assert reads == 1 and len(trace.native_sql) == 1
    assert trace.returned_rows == [3]
    assert completed.to_pandas()["value", "coefficient"].iloc[0] == pytest.approx(1.0, abs=1e-12)
    assert completed.to_pandas()["value", "n_pairs"].tolist() == [3]
    assert terminal_runs(case.session) == ()


def test_effective_source_float_types_and_nonfinite_validation(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C06/C07: actual carriers replace captured input types, never output types."""
    case = analysis_dsl_case_factory("j2")
    source = _source(case)
    execute = SourceSession.execute
    nonfinite = False

    def native(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        table = execute(owner, read)
        if read.purpose != "query.prepare":
            names = tuple(name for name in table if str(name).startswith("value."))
            assert len(names) == 2
            for name in names:
                table[name] = table[name].astype("float64")
            if nonfinite:
                table.loc[table.index[0], names[0]] = math.inf
        return table

    monkeypatch.setattr(SourceSession, "execute", native)
    pending = source.correlate(source["x"], source["y"], method="pearson")
    _assert_schema(pending)
    fixed = source.compute()
    assert isinstance(fixed._backing, MemoryFrameBacking)
    assert (
        tuple(column.dtype for column in fixed._schema.values) == (ScalarDataType("float64"),) * 2
    )
    completed = pending.compute()
    _assert_schema(completed)
    pd.testing.assert_frame_equal(
        completed.to_pandas(),
        fixed.correlate(fixed["x"], fixed["y"], method="pearson").compute().to_pandas(),
    )
    nonfinite = True
    with pytest.raises(AnalysisError, match="finite admitted numeric inputs for paired statistics"):
        pending.select({"constant": 1}).compute()


def test_unresolved_source_duration_rejected_during_preparation(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """C06: deferred physical Duration checks retain the public entry and repair."""
    case = analysis_dsl_case_factory("j2")
    export_dsl_parquet_models(case, case.root)
    path = case.root / "source_files/order.parquet"
    table = pq.read_table(path)
    pq.write_table(
        table.set_column(
            table.schema.get_field_index("amount"),
            "amount",
            table["amount"].cast(pa.duration("ns")),
        ),
        path,
    )
    ms.load(workspace_dir=case.root)
    source = _source(case)
    assert source["x"]._kind == "unresolved"
    for candidate in (
        source.correlate(source["x"], source["y"], method="pearson"),
        _composed(source, "pearson"),
    ):
        with pytest.raises(AnalysisError, match=r"[Dd]uration") as failure:
            candidate.compute()
        assert failure.value.repair is not None


def test_unfinished_statistics_preserve_binding_and_reusable_row_count(
    local: LocalFrameServices, tmp_path: Path
) -> None:
    first = _pairs(local, (1, 2), (3, 4))
    second = _pairs(local, (1, 2, 3), (4, 5, 6))
    foreign = _pairs(
        DatasetRuntime.create(tmp_path / "foreign", "foreign", timezone="UTC").local, (1,), (2,)
    )
    request = mv.row_count()
    before = (request._input_id, request._definition, request.unit, request._frame)
    for frame, expected in ((first, 2), (second, 3)):
        assert frame.reduce({"rows": request}).compute().to_pandas()[
            ("value", "rows")
        ].tolist() == [expected]
        assert (request._input_id, request._definition, request.unit, request._frame) == before
    for statistic in (
        first["x"].sum(),
        first["x"].mean(),
        first["x"].corr(first["y"], method="pearson"),
        first["x"].pair_count(first["y"]),
    ):
        assert not hasattr(statistic, "input_id") and not hasattr(statistic, "definition")
        for other in (second, foreign):
            with pytest.raises(AnalysisError):
                other.reduce({"value": statistic})
