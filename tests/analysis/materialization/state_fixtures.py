"""Shared builders for state fixtures tests."""

from __future__ import annotations

import marivo.analysis as mv
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
