"""Minimal governed Entity values and independent followup contributions."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.analysis.session._lazy_sources import LazySources
from marivo.refs import ref
from tests.lazy_local_fixtures import setup_local


def setup_entity_candidate(
    project: Path, values: tuple[float | None, ...] = (1.0, 1.0, 1.0, 10.0)
) -> tuple[DatasetRuntime, LazySources, Path]:
    """Seed one order per identity and followup lines worth ten times the ID."""
    runtime, sources, database = setup_local(project)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute("DELETE FROM lines")
        if values:
            connection.executemany(
                "INSERT INTO orders (id, customer_id, amount, weight, day, channel) VALUES (?, ?, ?, 1, ?, 'web')",
                [
                    (index, index, value, date(2026, 2, 2))
                    for index, value in enumerate(values, start=1)
                ],
            )
            connection.executemany(
                "INSERT INTO lines (id, order_id, amount) VALUES (?, ?, ?)",
                [(index, index, 10.0 * index) for index in range(1, len(values) + 1)],
            )
    return runtime, sources, database


def entity_metric(sources: LazySources) -> LogicalMetricDataset:
    """Observe the governed mean so missing values retain their null meaning."""
    return sources.observe(ref.metric("sales.mean_amount"))
