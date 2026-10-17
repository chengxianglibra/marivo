"""Pandas forecasting recipe boundary checks."""

import pytest


@pytest.mark.parametrize("bad", (0, 1001, True))
def test_pandas_recipe_horizon_boundaries(bad: int) -> None:
    import pandas as pd

    from tests.analysis.statistics.pandas_workflows import forecast as pandas_forecast

    with pytest.raises(ValueError):
        pandas_forecast(pd.Series([1, 2, 7]), horizon=bad)


def test_pandas_recipe_horizon_and_season_endpoints() -> None:
    import pandas as pd

    from tests.analysis.statistics.pandas_workflows import forecast as pandas_forecast

    values = pd.Series([1, 2, 7])
    assert len(pandas_forecast(values, horizon=1)) == 1
    assert len(pandas_forecast(values, horizon=1000)) == 1000
    assert pandas_forecast(values, "seasonal_naive", season=2).prediction.tolist() == [2, 7]
