"""S3 integration checks for domain-independent J4 execution."""

from __future__ import annotations

from dataclasses import replace

import ibis
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.dsl_j4_source import run_j4_source, run_j4_source_numeric
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.observation.dsl_j1 import J1Context
from tests.shared_fixtures import DSL_NAMES, DslCaseFactory


def test_j4_renamed_entity_and_fields_keep_both_numeric_routes(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    names = replace(
        DSL_NAMES,
        domain="telemetry",
        customer="device",
        order="reading",
        order_line="sample",
        customer_id="device_key",
        order_id="reading_key",
        line_id="sample_key",
        region="zone",
        channel="sensor_type",
        status="quality_flag",
        ordered_at="observed_at",
        amount="signal_value",
        line_amount="sample_value",
        buyer="reading_device",
        line_order="sample_reading",
        revenue="signal_sum",
        order_count="reading_count",
        line_revenue="sample_sum",
        aov="sample_per_reading",
    )
    case = analysis_dsl_case_factory("j4", names=names, revenue_unit="kWh")
    store = SessionStore(case.root)
    store.create_session("j4-renamed", session_ref="session")
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "session", store.store_id)
    members = context.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    during = mv.time_scope(start="2026-08-01", end="2026-09-01")
    via = ms.ref.relationship(f"{names.domain}.{names.buyer}")
    revenue = members.observe(
        ms.ref.metric(f"{names.domain}.{names.revenue}"), during=during, via=via
    )
    count = members.observe(
        ms.ref.metric(f"{names.domain}.{names.order_count}"), during=during, via=via
    )
    association = revenue.correlate(count, method="spearman")

    backend = ibis.duckdb.connect(str(case.database_path))
    try:
        tables = {
            f"{names.domain}.{names.customer}": backend.table(names.customer),
            f"{names.domain}.{names.order}": backend.table(names.order),
        }
        python = run_j4_source(association, backend, tables)
        source_numeric = run_j4_source_numeric(association, backend, tables)
    finally:
        backend.disconnect()

    for result in (python, source_numeric):
        assert result.coefficient == pytest.approx(-0.4)
        assert result.status == "valid"
        assert result.complete_pair_count == 4
        assert result.metric_key_a == f"metric:{names.domain}.{names.revenue}"
        assert result.metric_key_b == f"metric:{names.domain}.{names.order_count}"
