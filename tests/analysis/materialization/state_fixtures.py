"""Shared builders for state fixtures tests."""

from __future__ import annotations

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.store import SessionStore
from tests.shared_fixtures import DslCase


def _values(case: DslCase) -> mv.LogicalNumericRelation:
    result = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    assert isinstance(result, mv.LogicalNumericRelation)
    return result


def _snapshot(store: SessionStore) -> tuple[tuple[tuple[object, ...], ...], ...]:
    with store._read() as connection:
        return tuple(
            tuple(tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1"))
            for table in (
                "sessions",
                "runtime_state",
                "analysis_action_runs",
                "analysis_action_run_terminals",
                "dataset_artifacts",
                "dataset_evidence",
                "findings",
                "action_resource_journal",
            )
        )
