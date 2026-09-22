"""Coordinate-based lags, unusable candidates and independent coefficient oracles."""

from datetime import date

import ibis
import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.compiler.correlation import lower_correlate, prepare_pairs
from marivo.analysis.operators.association_contracts import CorrelationMethod
from marivo.analysis.operators.association_values import execute_pairs, prepare_local
from marivo.analysis.operators.errors import CorrelationError
from tests.lazy_correlation_fixtures import association_spec, reference


@pytest.mark.parametrize("method", ["pearson", "spearman"])
def test_explicit_source_correlation_matches_independent_reference(
    method: CorrelationMethod,
) -> None:
    spec = association_spec(method, shape="entity")
    left = [1.0, 2.0, 2.0, 5.0, 8.0]
    right = [3.0, 1.0, 1.0, 4.0, 9.0]
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table("source_pairs", {"revenue": left, "order_count": right})
        result, _ = lower_correlate(table, spec, explicit_correlation=True)
        frame = backend.to_pyarrow(result).to_pandas(types_mapper=pd.ArrowDtype)
        assert frame.coefficient.iloc[0] == pytest.approx(reference(left, right, method))
    finally:
        backend.disconnect()


@pytest.mark.parametrize("method", ["pearson", "spearman", "kendall"])
def test_missing_buckets_are_not_row_offsets(method: CorrelationMethod) -> None:
    spec = association_spec(method, shape="time", lags=range(-2, 3))
    time = spec.time_name
    assert time is not None
    data = {
        time: [date(2026, 2, d) for d in (1, 3, 4, 5, 7)],
        "revenue": [1.0, 5.0, 3.0, 9.0, 4.0],
        "order_count": [3, 4, 7, 2, 6],
    }
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table("bucket_pairs", data)
        pairs, checks = prepare_pairs(table, spec)
        actual = execute_pairs(
            backend.to_pyarrow(pairs).to_pandas(types_mapper=pd.ArrowDtype), spec
        )
        local_input = pa.table(data).to_pandas(types_mapper=pd.ArrowDtype)
        local = execute_pairs(prepare_local(local_input, spec), spec)
        pd.testing.assert_frame_equal(
            actual.sort_values("lag_offset").reset_index(drop=True),
            local.sort_values("lag_offset").reset_index(drop=True),
            check_exact=False,
        )
        plus_one = actual.loc[actual.lag_offset == 1].iloc[0]
        assert plus_one.matched_observation_count == 2
        assert plus_one.lag_boundary_drop_count == 3
        assert plus_one.coefficient == pytest.approx(reference([5.0, 3.0], [7.0, 2.0], method))
        if method != "kendall":
            reduced, checks = lower_correlate(table, spec)
            source = (
                backend.to_pyarrow(reduced)
                .to_pandas(types_mapper=pd.ArrowDtype)
                .sort_values("lag_offset")
            )
            assert source.coefficient.tolist() == pytest.approx(
                actual.sort_values("lag_offset").coefficient.tolist()
            )
    finally:
        backend.disconnect()


def test_unusable_lags_are_preserved_and_no_valid_pair_fails() -> None:
    spec = association_spec("kendall", shape="time", lags=range(0, 5))
    name = spec.time_name
    assert name is not None
    data = pa.table(
        {
            name: [date(2026, 2, d) for d in (1, 2, 3)],
            "revenue": [1.0, 2.0, 3.0],
            "order_count": [3, 2, 1],
        }
    ).to_pandas(types_mapper=pd.ArrowDtype)
    rows = execute_pairs(prepare_local(data, spec), spec)
    assert rows.status.tolist().count("insufficient_pairs") == 3
    assert rows.selected_for_pair.sum() == 1
    assert rows.loc[rows.selected_for_pair].lag_offset.iloc[0] == 0
    constant = data.copy()
    constant["revenue"] = pd.Series([1.0, 1.0, 1.0], dtype="double[pyarrow]")
    with pytest.raises(CorrelationError, match="no valid"):
        execute_pairs(prepare_local(constant, spec), spec)


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_input_is_not_null_deletion(bad: float) -> None:
    spec = association_spec("pearson", shape="dimension")
    data = pd.DataFrame(
        {"revenue": pd.Series([1.0, bad, 3.0], dtype=object), "order_count": [1, 2, 3]}
    )
    with pytest.raises(CorrelationError, match="non-finite"):
        prepare_local(data, spec)


@pytest.mark.parametrize("calendar", [False, True])
def test_month_and_certified_calendar_coordinates(calendar: bool) -> None:
    import json
    from dataclasses import replace

    from marivo._temporal import _snapshot_json, certify_period_calendar
    from marivo.analysis.datasets.descriptors import _CORE_TOKEN
    from marivo.analysis.observation.fold_contracts import decode_fold_authority
    from marivo.refs import ref

    spec = association_spec("spearman", shape="time", lags=range(-1, 2))
    authority = decode_fold_authority(spec.semantics.fold_authority)
    if calendar:
        snapshot = certify_period_calendar(
            calendar_ref=ref.period_calendar("sales.fiscal"),
            boundary_timezone="UTC",
            coverage=(date(2026, 2, 1), date(2026, 2, 11)),
            rows=tuple(
                {"date": date(2026, 2, day), "period": (day - 1) // 2} for day in range(1, 11)
            ),
            levels={"reporting_period": "period"},
        )
        authority = authority.model_copy(
            update={
                "grain": ("semantic", "sales.fiscal", "reporting_period"),
                "calendar_json": json.dumps(_snapshot_json(snapshot)),
            }
        )
        times = [date(2026, 2, day) for day in (1, 3, 7, 9)]
    else:
        authority = authority.model_copy(update={"grain": ("builtin", "month", "1")})
        times = [date(2025, 12, 1), date(2026, 1, 1), date(2026, 3, 1), date(2026, 4, 1)]
    meaning = replace(spec.semantics, _token=_CORE_TOKEN, fold_authority=authority.to_json())
    spec = replace(
        spec, output_row=replace(spec.output_row, _token=_CORE_TOKEN, family_semantics=meaning)
    )
    assert spec.time_name is not None
    data = pa.table(
        {spec.time_name: times, "revenue": [1.0, 7.0, 2.0, 5.0], "order_count": [4, 2, 1, 7]}
    )
    local = execute_pairs(prepare_local(data.to_pandas(types_mapper=pd.ArrowDtype), spec), spec)
    assert local.matched_observation_count.tolist() == [2, 4, 2]
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table("calendar_pairs", data)
        result, _ = lower_correlate(table, spec)
        source = (
            backend.to_pyarrow(result)
            .to_pandas(types_mapper=pd.ArrowDtype)
            .sort_values("lag_offset")
        )
        assert source.coefficient.tolist() == pytest.approx(local.coefficient.tolist())
        assert source.matched_observation_count.tolist() == local.matched_observation_count.tolist()
    finally:
        backend.disconnect()


def test_independent_series_and_candidate_ceiling() -> None:
    spec = association_spec("kendall", shape="dimension-time")
    assert spec.time_name is not None
    dim = spec.dimensions[0]
    data = pa.table(
        {
            dim: [None, None, "web", "web"],
            spec.time_name: [date(2026, 2, d) for d in (1, 2, 1, 2)],
            "revenue": [1.0, 2.0, 2.0, 1.0],
            "order_count": [1, 2, 1, 2],
        }
    )
    result = execute_pairs(prepare_local(data.to_pandas(types_mapper=pd.ArrowDtype), spec), spec)
    assert sorted(result.coefficient.tolist()) == [-1.0, 1.0]
    assert result.input_observation_count.tolist() == [2, 2]
    too_many = pa.table(
        {
            dim: [str(i) for i in range(4097)],
            spec.time_name: [date(2026, 2, 1)] * 4097,
            "revenue": [1.0] * 4097,
            "order_count": [1] * 4097,
        }
    )
    assert len(prepare_local(too_many.to_pandas(types_mapper=pd.ArrowDtype), spec)) == 4097


@pytest.mark.parametrize("method", ["pearson", "spearman", "kendall"])
def test_large_integers_keep_distinct_values(method: CorrelationMethod) -> None:
    spec = association_spec(method, shape="dimension")
    xs = [2**60 + x for x in (1, 3, 2, 5)]
    ys = [1.0, 4.0, 3.0, 2.0]
    table = pa.table({"revenue": xs, "order_count": ys})
    result = execute_pairs(prepare_local(table.to_pandas(types_mapper=pd.ArrowDtype), spec), spec)
    expected = reference([1.0, 3.0, 2.0, 5.0], ys, method)
    assert result.coefficient.iloc[0] == pytest.approx(expected)
    if method != "kendall":
        backend = ibis.duckdb.connect()
        try:
            relation = backend.create_table("precise_pairs", table)
            output, _ = lower_correlate(relation, spec)
            assert backend.to_pyarrow(output).column("coefficient")[0].as_py() == pytest.approx(
                expected
            )
        finally:
            backend.disconnect()


def test_missing_prepared_candidate_and_empty_input_fail() -> None:
    spec = association_spec("kendall", shape="time", lags=range(0, 2))
    assert spec.time_name is not None
    data = pa.table(
        {
            spec.time_name: [date(2026, 2, d) for d in (1, 2, 3)],
            "revenue": [1.0, 2.0, 3.0],
            "order_count": [1, 2, 3],
        }
    ).to_pandas(types_mapper=pd.ArrowDtype)
    pairs = prepare_local(data, spec)
    with pytest.raises(CorrelationError, match="missing prepared candidate"):
        execute_pairs(pairs.loc[pairs.lag_offset == 0], spec)
    with pytest.raises(CorrelationError):
        execute_pairs(prepare_local(data.iloc[:0], spec), spec)


@pytest.mark.parametrize("method", ["pearson", "spearman", "kendall"])
def test_all_five_statuses_remain_in_the_search(method: CorrelationMethod) -> None:
    spec = association_spec(method, shape="time", lags=range(-3, 4))
    assert spec.time_name is not None
    data = pa.table(
        {
            spec.time_name: [date(2026, 2, d) for d in (1, 2, 3, 4)],
            "revenue": [1.0, 1.0, 2.0, 2.0],
            "order_count": [1, 2, 2, 2],
        }
    )
    local = execute_pairs(prepare_local(data.to_pandas(types_mapper=pd.ArrowDtype), spec), spec)
    expected = [
        "insufficient_pairs",
        "constant_a",
        "valid",
        "valid",
        "constant_b",
        "constant_both",
        "insufficient_pairs",
    ]
    assert local.status.tolist() == expected
    assert local.loc[local.status != "valid", "coefficient"].isna().all()
    if method != "kendall":
        backend = ibis.duckdb.connect()
        try:
            table = backend.create_table("statuses", data)
            result, _ = lower_correlate(table, spec)
            source = (
                backend.to_pyarrow(result)
                .to_pandas(types_mapper=pd.ArrowDtype)
                .sort_values("lag_offset")
            )
            assert source.status.tolist() == expected
        finally:
            backend.disconnect()


@pytest.mark.parametrize("method", ["pearson", "spearman", "kendall"])
def test_perfect_linear_lags_use_the_zero_lag_tie_breaker(method: CorrelationMethod) -> None:
    spec = association_spec(method, shape="time", lags=range(-1, 2))
    assert spec.time_name is not None
    data = pa.table(
        {
            spec.time_name: [date(2026, 2, d) for d in (1, 2, 3)],
            "revenue": [1.0, 2.0, 3.0],
            "order_count": [1, 2, 3],
        }
    )
    result = execute_pairs(prepare_local(data.to_pandas(types_mapper=pd.ArrowDtype), spec), spec)
    assert result.loc[result.selected_for_pair, "lag_offset"].tolist() == [0]
    if method != "kendall":
        backend = ibis.duckdb.connect()
        try:
            table = backend.create_table("linear_lags", data)
            expression, _ = lower_correlate(table, spec)
            source = backend.to_pyarrow(expression).to_pandas(types_mapper=pd.ArrowDtype)
            assert source.loc[source.selected_for_pair, "lag_offset"].tolist() == [0]
        finally:
            backend.disconnect()


@pytest.mark.parametrize("method", ["pearson", "spearman", "kendall"])
def test_source_series_count_and_null_dimension_pairs(method: CorrelationMethod) -> None:
    spec = association_spec(method, shape="dimension-time")
    assert spec.time_name is not None
    dim = spec.dimensions[0]
    data = pa.table(
        {
            dim: [None, None, "web", "web"],
            spec.time_name: [date(2026, 2, d) for d in (1, 2, 1, 2)],
            "revenue": [1.0, 2.0, 2.0, 1.0],
            "order_count": [1, 2, 1, 2],
        }
    )
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table("dimension_pairs", data)
        pairs, checks = prepare_pairs(table, spec)
        for check in checks:
            assert backend.execute(check.expression).iloc[0, 0] == 0
        local = execute_pairs(backend.to_pyarrow(pairs).to_pandas(types_mapper=pd.ArrowDtype), spec)
        assert sorted(local.coefficient.tolist()) == [-1.0, 1.0]
        if method != "kendall":
            expression, _ = lower_correlate(table, spec)
            native = backend.to_pyarrow(expression).to_pandas(types_mapper=pd.ArrowDtype)
            assert sorted(native.coefficient.tolist()) == [-1.0, 1.0]
        excessive = backend.create_table(
            "too_many_series",
            {
                dim: [str(i) for i in range(4097)],
                spec.time_name: [date(2026, 2, 1)] * 4097,
                "revenue": [1.0] * 4097,
                "order_count": [1] * 4097,
            },
        )
        _, limits = prepare_pairs(excessive, spec)
        assert all(check.name != "correlate.candidate_ceiling" for check in limits)
    finally:
        backend.disconnect()


@pytest.mark.parametrize("method", ["pearson", "spearman", "kendall"])
def test_minimum_signed_lag_remains_an_unusable_candidate(method: CorrelationMethod) -> None:
    spec = association_spec(method, shape="time", lags=range(-(2**63), 1, 2**63))
    assert spec.time_name is not None
    data = pa.table(
        {
            spec.time_name: [date(2026, 2, d) for d in (1, 2, 3)],
            "revenue": [1.0, 2.0, 3.0],
            "order_count": [1, 2, 3],
        }
    )
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table("extreme_lag", data)
        if method == "kendall":
            pairs, _ = prepare_pairs(table, spec)
            result = execute_pairs(
                backend.to_pyarrow(pairs).to_pandas(types_mapper=pd.ArrowDtype), spec
            )
        else:
            expression, checks = lower_correlate(table, spec)
            for check in checks:
                assert backend.execute(check.expression).iloc[0, 0] == 0
            result = backend.to_pyarrow(expression).to_pandas(types_mapper=pd.ArrowDtype)
        assert result.loc[result.selected_for_pair, "lag_offset"].tolist() == [0]
        assert result.loc[result.lag_offset == -(2**63), "status"].tolist() == [
            "insufficient_pairs"
        ]
    finally:
        backend.disconnect()
