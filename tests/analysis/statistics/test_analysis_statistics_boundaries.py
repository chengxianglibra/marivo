"""Calendar/rank kernels and pandas statistics retain their owning boundaries."""

from datetime import datetime, timedelta
from typing import Literal

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._temporal_grid import bind_grid
from marivo.analysis.dsl.temporal import exact_scope
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
        exact_scope(finish - 2 * step, finish, "America/New_York"),
        mv.grain(unit),
        report_timezone="America/New_York",
    )
    expected_start = history.cells[-1].end
    future = bind_grid(
        exact_scope(expected_start, expected_start + count * step, "America/New_York"),
        mv.grain(unit),
        report_timezone="America/New_York",
    )
    assert [(c.start, c.end, c.partial) for c in future.cells] == [
        (expected_start + i * step, expected_start + (i + 1) * step, False) for i in range(count)
    ]


@pytest.mark.parametrize("family", ("association", "forecast"))
def test_exported_ratio_na_limits_pandas_statistical_input(
    analysis_dsl_case_factory: DslCaseFactory,
    family: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    query = daily(case)
    ratio = query.select({"value": query["value"] / query["value"]}).compute()
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
        monthly(case, "2026-08-01", "2026-09-01").persist()
        if family == "association"
        else daily(case).persist()
    )
    if fixed:
        case.database_path.rename(case.database_path.with_suffix(".offline"))
    frame = plain(case.session.persisted_frame(artifact.artifact.id))
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
