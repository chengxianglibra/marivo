"""Small governed time histories for Forecast contract and Runtime tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from marivo._temporal import builtin_grain, time_scope
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.analysis.operators.forecast_contracts import (
    DEFAULT_MODEL,
    ForecastModel,
    ForecastPayload,
    ForecastSpecV1,
    periods,
)
from marivo.analysis.session._lazy_sources import LazySources
from marivo.refs import ref
from tests.lazy_local_fixtures import setup_local
from tests.lazy_observation_fixtures import make_sources


def history(source: LazySources, *, panel: bool = False) -> LogicalMetricDataset:
    value = source.observe(
        ref.metric("sales.revenue"), time_scope=time_scope(start="2026-02-01", end="2026-02-20")
    )
    if panel:
        value = value.with_dimensions(ref.dimension("sales.orders.channel"))
    return value.with_time_axis(
        ref.time_dimension("sales.orders.order_time"), grain=builtin_grain("day")
    ).aggregate()


def forecast_spec(
    model: ForecastModel = DEFAULT_MODEL, *, panel: bool = False, horizon: int = 4
) -> ForecastSpecV1:
    result = history(make_sources(), panel=panel).forecast(horizon=periods(horizon), model=model)
    assert isinstance(result._root, LogicalRootHandle) and isinstance(
        result._root.payload, ForecastPayload
    )
    return result._root.payload.spec


def history_frame(values: Sequence[float | None], *, channel: str | None = None) -> pd.DataFrame:
    data: dict[str, object] = {
        "order_time": [date(2026, 2, 1) + timedelta(days=i) for i in range(len(values))],
        "revenue": values,
    }
    if channel is not None:
        data["channel"] = [channel] * len(values)
    return pd.DataFrame(data)


def setup_forecast(
    project: Path, values: tuple[float, ...] = (1.0, 2.0, 4.0, 4.0)
) -> tuple[DatasetRuntime, LazySources, Path]:
    runtime, source, database = setup_local(project)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("DELETE FROM orders")
        connection.executemany(
            "INSERT INTO orders (id, customer_id, amount, weight, day, channel) VALUES (?, 1, ?, 1, ?, 'web')",
            [
                (i + 1, value, date(2026, 2, 1) + timedelta(days=i))
                for i, value in enumerate(values)
            ],
        )
    return runtime, source, database
