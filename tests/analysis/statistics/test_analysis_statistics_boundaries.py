"""Calendar/rank kernels and pandas statistics retain their owning boundaries."""

from datetime import datetime, timedelta
from fractions import Fraction
from typing import Literal

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._temporal import time_scope
from marivo.analysis.core.time_grid import bind_grid, continuation
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.statistical_execution import _verify_ranks
from marivo.analysis.methods.deviation_numeric import RationalFact
from tests.analysis.statistics.pandas_workflows import daily, forecast, monthly, plain
from tests.shared_fixtures import DslCaseFactory, run_ids


@pytest.mark.parametrize(
    "end,unit,count",
    (
        ("2026-11-01T00:00:00-04:00", "hour", 4),
        ("2026-03-08T00:00:00-05:00", "hour", 4),
        ("2026-03-08T01:59:00-05:00", "minute", 4),
        ("2026-11-01T01:30:00-05:00", "minute", 4),
    ),
)
def test_future_grid_uses_actual_dst_boundaries(
    end: str, unit: Literal["hour", "minute"], count: int
) -> None:
    finish = datetime.fromisoformat(end)
    step = timedelta(hours=1) if unit == "hour" else timedelta(minutes=1)
    history = bind_grid(
        time_scope(start=finish - 2 * step, end=finish),
        mv.grain(unit),
        report_timezone="America/New_York",
    )
    future = continuation(history, count)
    expected_start = history.cells[-1].end
    assert [(c.start, c.end, c.partial) for c in future.cells] == [
        (expected_start + i * step, expected_start + (i + 1) * step, False) for i in range(count)
    ]


def test_rank_witness_ties_and_corruption() -> None:
    original = tuple(Fraction(i // 2) for i in reversed(range(32)))
    ranks = tuple(RationalFact.capture(Fraction(4 * (i // 2) + 3, 2)) for i in reversed(range(32)))
    _verify_ranks(original, ranks)
    for corrupt in (ranks[:-1], (ranks[-1], *ranks[1:]), (RationalFact("3", "2"),) * 32):
        with pytest.raises(AnalysisError):
            _verify_ranks(original, corrupt)


@pytest.mark.parametrize("family", ("association", "forecast"))
def test_exported_ratio_na_limits_pandas_statistical_input(
    analysis_dsl_case_factory: DslCaseFactory,
    family: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    query = daily(case)
    ratio = query.select({"value": query["value"] / query["value"]}).execute()
    frame = plain(ratio)
    assert frame.value.iloc[0] == 1 and frame.value.iloc[1:].isna().all()
    before = run_ids(case.session)
    if family == "association":
        assert pd.isna(frame.value.astype(float).corr(frame.value.astype(float)))
    else:
        with pytest.raises(ValueError, match="too short"):
            forecast(frame.value.dropna())
    assert run_ids(case.session) == before


@pytest.mark.parametrize("family", ("association", "forecast"))
@pytest.mark.parametrize("fixed", (False, True))
def test_exported_statistics_use_pandas_rank_projection(
    analysis_dsl_case_factory: DslCaseFactory,
    family: str,
    fixed: bool,
) -> None:
    case = analysis_dsl_case_factory("j4_ties")
    artifact = (
        monthly(case, "2026-08-01", "2026-09-01").execute()
        if family == "association"
        else daily(case).execute()
    )
    if fixed:
        case.database_path.rename(case.database_path.with_suffix(".offline"))
    frame = plain(case.session.artifact(artifact.id))
    before = run_ids(case.session)
    if family == "association":
        coefficient = (
            frame[["value", "count"]].astype(float).corr(method="spearman").loc["value", "count"]
        )
        values = pd.DataFrame({"value": [coefficient]})
    else:
        values = forecast(frame.value).rename(columns={"prediction": "value"})
    values["rank"] = values.value.rank(method="dense", ascending=False)
    selected = values.sort_values("rank", kind="stable")
    assert not selected.empty and selected["rank"].min() == 1
    assert selected["rank"].is_monotonic_increasing
    pd.testing.assert_frame_equal(
        selected[["rank"]], values[["rank"]].sort_values("rank", kind="stable")
    )
    assert run_ids(case.session) == before
