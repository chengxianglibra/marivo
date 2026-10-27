"""Statistics use complete terminal exports and explicit pandas calculations."""

from itertools import combinations
from typing import Literal

import pandas as pd
import pytest

from marivo.analysis.errors import AnalysisError
from tests.analysis.statistics.pandas_workflows import (
    ForecastModel,
    assert_clean,
    daily,
    forecast,
    monthly,
    plain,
)
from tests.shared_fixtures import DslCaseFactory, run_ids, terminal_runs


@pytest.mark.parametrize("method", ("pearson", "spearman", "kendall"))
def test_exported_quantities_support_pandas_association_and_reopening(
    analysis_dsl_case_factory: DslCaseFactory, method: Literal["pearson", "spearman", "kendall"]
) -> None:
    case = analysis_dsl_case_factory("j4")
    artifact = monthly(case, "2026-08-01", "2026-09-01").persist()
    frame = plain(artifact)
    before = run_ids(case.session)
    coefficients = frame[["value", "count"]].astype(float).corr(method=method)
    coefficient = coefficients.loc["value", "count"]
    assert isinstance(coefficient, float)
    assert -1 <= coefficient <= 1
    restored = plain(case.session.persisted_frame(artifact.artifact_id))
    pd.testing.assert_frame_equal(restored, frame)
    assert (
        restored[["value", "count"]].astype(float).corr(method=method).loc["value", "count"]
        == coefficient
    )
    assert run_ids(case.session) == before


@pytest.mark.parametrize("model", ("naive", "drift", "seasonal_naive"))
def test_exported_history_supports_three_pandas_forecast_models(
    analysis_dsl_case_factory: DslCaseFactory, model: ForecastModel
) -> None:
    case = analysis_dsl_case_factory("j2")
    artifact = daily(case).persist()
    history = plain(artifact)
    before = run_ids(case.session)
    prediction = forecast(history.value, model)
    assert len(prediction) == 2
    assert (prediction.lower <= prediction.prediction).all()
    assert (prediction.upper >= prediction.prediction).all()
    pd.testing.assert_frame_equal(
        forecast(plain(case.session.persisted_frame(artifact.artifact_id)).value, model), prediction
    )
    for level in (0.0, 1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            forecast(history.value, model, level=level)
    assert run_ids(case.session) == before
    assert not hasattr(artifact, "forecast")


def test_exported_quantities_support_all_requested_pandas_pairs(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j4")
    frame = plain(monthly(case, "2026-08-01", "2026-09-01").compute())
    values = pd.DataFrame({f"times_{i}": frame["count"].astype(float) * i for i in range(1, 17)})
    before = run_ids(case.session)
    pairs = [
        (left, right, values[left].corr(values[right])) for left, right in combinations(values, 2)
    ]
    assert len(pairs) == 120
    assert [coefficient for _, _, coefficient in pairs] == pytest.approx([1.0] * 120)
    assert run_ids(case.session) == before


@pytest.mark.parametrize("lag", range(-2, 3))
def test_pandas_lag_shift_preserves_nominal_grid_distance(
    analysis_dsl_case_factory: DslCaseFactory, lag: int
) -> None:
    case = analysis_dsl_case_factory("j2")
    history = plain(daily(case).compute())
    before = run_ids(case.session)
    # Pandas shift retains the nominal grid: filtering never compresses lag distance.
    pairs = pd.DataFrame({"left": history.value, "right": history.value.shift(lag)})
    assert len(pairs.dropna()) == 3 - abs(lag)
    assert int(pairs.right.isna().sum()) == abs(lag)
    if lag == 0:
        assert pairs.astype(float).corr().loc["left", "right"] == pytest.approx(1.0)
    elif abs(lag) == 1:
        assert pd.isna(pairs.astype(float).corr().loc["left", "right"])
    prediction = forecast(history.value, horizon=1)
    assert len(prediction) == 1
    assert run_ids(case.session) == before


@pytest.mark.parametrize("point", ("insert_evidence", "insert_findings", "before_commit"))
def test_statistical_failure_is_atomic(
    analysis_dsl_case_factory: DslCaseFactory, point: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j4")
    query = monthly(case, "2026-08-01", "2026-09-01")
    previous = query.persist()
    saved = previous.to_pandas()

    def inject(actual: str) -> None:
        if actual == point:
            raise RuntimeError("injected terminal publication failure")

    from marivo.analysis.materialization import terminal_publication

    monkeypatch.setattr(terminal_publication, "_event", inject)
    with pytest.raises(AnalysisError):
        query.persist()
    pd.testing.assert_frame_equal(
        case.session.persisted_frame(previous.artifact_id).to_pandas(), saved
    )
    assert_clean(case.session, artifacts=1)
    assert len(terminal_runs(case.session, status="failed")) == 1


def test_pandas_forecast_can_feed_an_explicit_second_model_without_run(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import SourceSession
    from tests.analysis.statistics.deviation_oracle import forbidden

    case = analysis_dsl_case_factory("j2")
    artifact = daily(case).persist()
    before = run_ids(case.session)
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    first = forecast(plain(artifact).value, "drift", horizon=3)
    second = forecast(first.prediction, "drift", horizon=1)
    assert second.prediction.iloc[0] == pytest.approx(
        first.prediction.iloc[-1] + (first.prediction.iloc[-1] - first.prediction.iloc[0]) / 2
    )
    assert run_ids(case.session) == before
    assert_clean(case.session, artifacts=1)
