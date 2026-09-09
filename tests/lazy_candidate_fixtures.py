"""Minimal governed Entity values and paired time series for Candidate Runtime checks."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import duckdb

from marivo._temporal import builtin_grain, time_scope
from marivo.analysis.datasets.base import Dataset
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.analysis.operators.candidate_contracts import CandidateObjective
from marivo.analysis.operators.candidate_dataset import LogicalCandidateDataset
from marivo.analysis.operators.delta import LogicalDeltaDataset, MaterializedDeltaDataset
from marivo.analysis.operators.discovery import DeltaDiscovery, MetricDiscovery
from marivo.analysis.session._lazy_sources import LazySources
from marivo.refs import ref
from tests.lazy_local_fixtures import setup_local

VALUES = (1.0, 1.0, 1.0, 2.0, 1.0, 1.0, 1.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0)


def setup_candidate(
    project: Path, values: tuple[float, ...] = VALUES
) -> tuple[DatasetRuntime, LazySources, Path]:
    runtime, source, database = setup_local(project)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("DELETE FROM orders")
        connection.executemany(
            "INSERT INTO orders (id, customer_id, amount, weight, day, channel) VALUES (?, 1, ?, 1, ?, 'web')",
            [
                (
                    month * 100 + i,
                    value if month == 2 else 1.0,
                    date(2026, month, 1) + timedelta(days=i),
                )
                for month in (1, 2)
                for i, value in enumerate(values)
            ],
        )
    return runtime, source, database


def time_series(
    source: LazySources, *, baseline: bool = False, panel: bool = False
) -> LogicalMetricDataset:
    month = "01" if baseline else "02"
    result = source.observe(
        ref.metric("sales.revenue"),
        time_scope=time_scope(start=f"2026-{month}-01", end=f"2026-{month}-20"),
    )
    if panel:
        result = result.with_dimensions(ref.dimension("sales.orders.channel"))
    return result.with_time_axis(
        ref.time_dimension("sales.orders.order_time"), grain=builtin_grain("day")
    ).aggregate()


def candidate_input(
    source: LazySources, objective: CandidateObjective, *, panel: bool = False
) -> LogicalMetricDataset | LogicalDeltaDataset:
    if objective == "entity_outliers":
        return source.observe(ref.metric("sales.mean_amount"))
    current = time_series(source, panel=panel)
    return (
        current.compare(time_series(source, baseline=True, panel=panel))
        if objective == "period_shifts"
        else current
    )


def discover(
    dataset: Dataset, objective: CandidateObjective, *, threshold: float = 1.0, limit: int = 50
) -> LogicalCandidateDataset:
    if objective == "period_shifts":
        assert isinstance(dataset, (LogicalDeltaDataset, MaterializedDeltaDataset))
        return DeltaDiscovery(dataset).period_shifts(threshold=threshold, limit=limit)
    namespace = MetricDiscovery(dataset)
    if objective == "entity_outliers":
        return namespace.entity_outliers(threshold=threshold, limit=limit)
    return (
        namespace.point_anomalies(threshold=threshold, limit=limit)
        if objective == "point_anomalies"
        else namespace.interesting_windows(threshold=threshold, limit=limit)
    )
