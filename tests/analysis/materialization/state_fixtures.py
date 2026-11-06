"""Shared builders for state fixtures tests."""

from __future__ import annotations

import marivo.analysis as mv
from marivo.analysis.materialization.store import SessionStore
from tests.shared_fixtures import DslCase, catalog_value


def _values(case: DslCase) -> mv.AnalysisFrame:
    return case.session.observe(
        {"value": catalog_value(case.session.catalog, "sales.revenue", kind="metric")},
        by=mv.product(
            case.session.time_scope(start="2026-08-01", end="2026-09-01"),
            case.session.members(
                catalog_value(case.session.catalog, "sales.customer", kind="entity")
            ),
        ),
    )


def _snapshot(store: SessionStore) -> tuple[tuple[tuple[object, ...], ...], ...]:
    with store._read() as connection:
        return tuple(
            tuple(tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1"))
            for table in (
                "sessions",
                "analysis_action_runs",
                "analysis_action_run_terminals",
                "dataset_artifacts",
                "action_resource_journal",
            )
        )
