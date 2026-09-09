"""Independent normative innovation and multi-horizon prediction references."""

from statistics import NormalDist

import pytest

from marivo.analysis.operators.errors import ForecastError
from marivo.analysis.operators.forecast_contracts import ForecastModel, drift, naive, seasonal_naive
from marivo.analysis.operators.forecast_values import execute_forecast, prepare_history
from tests.lazy_forecast_fixtures import forecast_spec, history_frame


@pytest.mark.parametrize(
    "model,values,points,variances,df",
    [
        (naive(), [1.0, 2.0, 3.0], [3.0, 3.0, 3.0, 3.0], [1.0, 2.0, 3.0, 4.0], 2),
        (drift(), [1.0, 2.0, 4.0, 4.0], [5.0, 6.0, 7.0, 8.0], [4 / 3, 10 / 3, 6.0, 28 / 3], 2),
        (
            seasonal_naive(periods=2),
            [1.0, 4.0, 2.0, 6.0, 4.0],
            [6.0, 4.0, 6.0, 4.0],
            [3.0, 3.0, 6.0, 6.0],
            3,
        ),
    ],
)
def test_normative_equations(
    model: ForecastModel, values: list[float], points: list[float], variances: list[float], df: int
) -> None:
    spec = forecast_spec(model)
    frame, summary = execute_forecast(prepare_history(history_frame(values), spec), spec)
    assert frame.forecast_value.tolist() == pytest.approx(points)
    z = NormalDist().inv_cdf(0.975)
    assert frame.interval_upper.tolist() == pytest.approx(
        [p + z * v**0.5 for p, v in zip(points, variances, strict=True)]
    )
    assert frame.interval_lower.tolist() == pytest.approx(
        [p - z * v**0.5 for p, v in zip(points, variances, strict=True)]
    )
    assert summary.residual_df == df
    assert summary.zero_residual_series_count == 0


@pytest.mark.parametrize(
    "model,values",
    [(naive(), [2.0]), (drift(), [1.0, 2.0]), (seasonal_naive(periods=2), [1.0, 2.0])],
)
def test_insufficient_history_fails(model: ForecastModel, values: list[float]) -> None:
    spec = forecast_spec(model)
    with pytest.raises(ForecastError, match="at least"):
        prepare_history(history_frame(values), spec)


@pytest.mark.parametrize(
    "values", [[], [1.0, None, 3.0], [1.0, float("nan"), 3.0], [1.0, float("inf"), 3.0]]
)
def test_incomplete_values_fail(values: list[float | None]) -> None:
    spec = forecast_spec()
    with pytest.raises(ForecastError):
        prepare_history(history_frame(values), spec)


@pytest.mark.parametrize(
    "model,values",
    [
        (naive(), [2.0, 2.0, 2.0]),
        (drift(), [1.0, 2.0, 3.0]),
        (seasonal_naive(periods=2), [1.0, 2.0, 1.0, 2.0]),
    ],
)
def test_exact_zero_innovations_only(model: ForecastModel, values: list[float]) -> None:
    spec = forecast_spec(model)
    frame, proof = execute_forecast(prepare_history(history_frame(values), spec), spec)
    assert frame.forecast_value.equals(frame.interval_lower)
    assert frame.forecast_value.equals(frame.interval_upper)
    assert proof.variance_range == (0.0, 0.0) and proof.zero_residual_series_count == 1


def test_panel_rejects_mismatch_and_preserves_independent_variance() -> None:
    import pandas as pd

    spec = forecast_spec(panel=True)
    a, b = history_frame([1.0, 2.0, 3.0], channel="a"), history_frame([2.0, 4.0, 6.0], channel="b")
    frame = pd.concat([a, b], ignore_index=True)
    output, summary = execute_forecast(prepare_history(frame, spec), spec)
    assert summary.series_count == 2 and summary.variance_range == (1.0, 4.0)
    widths = output.interval_upper - output.forecast_value
    assert widths.iloc[4] == pytest.approx(widths.iloc[0] * 2)
    with pytest.raises(ForecastError, match="panel"):
        prepare_history(frame.drop(index=5), spec)
    with pytest.raises(ForecastError):
        prepare_history(pd.concat([a, a], ignore_index=True), spec)
    with pytest.raises(ForecastError):
        prepare_history(a.drop(index=1), spec)


@pytest.mark.parametrize("calendar", [False, True])
def test_exact_builtin_and_certified_future_coordinates(calendar: bool) -> None:
    import json
    from dataclasses import replace
    from datetime import date

    from marivo._temporal import _snapshot_json, certify_period_calendar
    from marivo.analysis.datasets.descriptors import _CORE_TOKEN
    from marivo.analysis.observation.fold_contracts import decode_fold_authority
    from marivo.refs import ref

    spec = forecast_spec(horizon=2)
    authority = decode_fold_authority(spec.semantics.fold_authority)
    if calendar:
        snapshot = certify_period_calendar(
            calendar_ref=ref.period_calendar("sales.fiscal"),
            boundary_timezone="UTC",
            coverage=(date(2026, 2, 1), date(2026, 2, 13)),
            rows=tuple(
                {"date": date(2026, 2, day), "period": (day - 1) // 2} for day in range(1, 13)
            ),
            levels={"reporting_period": "period"},
        )
        authority = authority.model_copy(
            update={
                "grain": ("semantic", "sales.fiscal", "reporting_period"),
                "calendar_json": json.dumps(_snapshot_json(snapshot)),
            }
        )
        times = [date(2026, 2, d) for d in (1, 3, 5)]
        expected = [date(2026, 2, d) for d in (7, 9)]
    else:
        authority = authority.model_copy(
            update={"grain": ("builtin", "month", "1"), "scope": ("2025-11-01", "2026-02-01")}
        )
        times = [date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1)]
        expected = [date(2026, 2, 1), date(2026, 3, 1)]
    meaning = replace(spec.semantics, _token=_CORE_TOKEN, fold_authority=authority.to_json())
    spec = replace(
        spec, output_row=replace(spec.output_row, _token=_CORE_TOKEN, family_semantics=meaning)
    )
    frame = history_frame([1.0, 2.0, 3.0])
    frame["order_time"] = times
    result, _ = execute_forecast(prepare_history(frame, spec), spec)
    assert result.order_time.tolist() == expected
    if calendar:
        too_far = replace(
            spec,
            output_row=replace(
                spec.output_row,
                _token=_CORE_TOKEN,
                family_semantics=replace(meaning, _token=_CORE_TOKEN, horizon=4),
            ),
        )
        with pytest.raises(ForecastError):
            prepare_history(frame, too_far)
    partial = authority.model_copy(update={"scope": (times[0].isoformat(), times[-1].isoformat())})
    invalid = replace(
        spec,
        output_row=replace(
            spec.output_row,
            _token=_CORE_TOKEN,
            family_semantics=replace(meaning, _token=_CORE_TOKEN, fold_authority=partial.to_json()),
        ),
    )
    with pytest.raises(ForecastError, match="partial"):
        prepare_history(frame, invalid)


@pytest.mark.parametrize("values", [[1e308, -1e308, 1e308], [1e-300, 2e-300, 3e-300]])
def test_overflow_and_underflow_cannot_fake_zero_uncertainty(values: list[float]) -> None:
    spec = forecast_spec()
    with pytest.raises(ForecastError):
        execute_forecast(prepare_history(history_frame(values), spec), spec)


def test_large_integer_innovations_never_collapse_to_zero_variance() -> None:
    from marivo.analysis.operators.forecast_values import predict_series

    meaning = forecast_spec().semantics
    with pytest.raises(ForecastError, match="precision"):
        predict_series((2**60, 2**60 + 1, 2**60 + 2), meaning)


def test_all_generated_model_bounds_are_finite_for_large_common_level() -> None:
    spec = forecast_spec()
    output, proof = execute_forecast(
        prepare_history(history_frame([1e8, 1e8 + 1, 1e8 + 2]), spec), spec
    )
    assert proof.variance_range == (1.0, 1.0)
    assert (output.interval_upper > output.forecast_value).all()
