"""Shared retained private-state fixtures for local and live object acceptance."""

from pathlib import Path
from typing import Literal

from marivo.analysis import time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.observation.contracts import MetricInput
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.semantic._quantile import QuantileMethod, quantile_metric
from tests.lazy_distinct_fixtures import (
    CHANNEL,
    DISTINCT_BUYERS,
    make_distinct_registry,
    seed_distinct_database,
)
from tests.lazy_distribution_fixtures import (
    METRIC,
    make_distribution_registry,
    seed_distribution_database,
)


def operands(
    project: Path,
    kind: Literal["distinct", "distribution"],
    method: QuantileMethod,
) -> tuple[DatasetRuntime, LogicalMetricDataset, LogicalMetricDataset, Path]:
    database = project / "warehouse.duckdb"
    if kind == "distinct":
        seed_distinct_database(database)
        registry, sidecar = make_distinct_registry(database)
        metric: MetricInput = DISTINCT_BUYERS
    else:
        seed_distribution_database(database)
        registry, sidecar = make_distribution_registry(database)
        metric = quantile_metric(METRIC, method=method)
    runtime = DatasetRuntime.create(project, "private-parquet")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    return (
        runtime,
        sources.observe(metric, time_scope=time_scope(start="2026-02-01", end="2026-02-05"))
        .with_dimensions(CHANNEL)
        .aggregate(),
        sources.observe(metric, time_scope=time_scope(start="2026-01-01", end="2026-01-05"))
        .with_dimensions(CHANNEL)
        .aggregate(),
        database,
    )
