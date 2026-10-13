"""Project handoff keeps semantic, source, and ontology authority separate."""

from __future__ import annotations

import textwrap
from pathlib import Path

import duckdb

import marivo.datasource as md
import marivo.ontology as mo
import marivo.semantic as ms


def _extend_authoring_project(root: Path) -> None:
    database = duckdb.connect(str(root / "warehouse.duckdb"))
    try:
        database.execute(
            "CREATE TABLE events (event_id INTEGER, query_id INTEGER, "
            "sequence_number INTEGER, occurred_at TIMESTAMP)"
        )
        database.execute(
            "INSERT INTO events VALUES "
            "(11, 1, 1, '2041-07-17 09:00:00'), "
            "(12, 1, 2, '2041-07-17 09:00:00')"
        )
        database.execute("CREATE TABLE calendar (calendar_date DATE, month VARCHAR)")
        database.execute(
            "INSERT INTO calendar VALUES ('2041-07-17', '2041-07'), ('2041-07-18', '2041-07')"
        )
    finally:
        database.close()

    source = root / "models" / "semantic" / "sales" / "models.py"
    source.write_text(
        source.read_text()
        + textwrap.dedent(
            "\nquery_id = ms.dimension_column(name='query_id', entity=orders, column='query_id')\nevents = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'),\n                   source=md.table('events'), primary_key=['event_id'])\nevent_id = ms.dimension_column(name='event_id', entity=events, column='event_id')\nevent_query_id = ms.dimension_column(name='query_id', entity=events, column='query_id')\nsequence = ms.dimension_column(name='sequence_number', entity=events,\n                               column='sequence_number')\noccurred_at = ms.time_dimension_column(name='occurred_at', entity=events,\n    column='occurred_at', granularity='second', parse=ms.timestamp(timezone='UTC'))\nevent_to_order = ms.relationship(name='event_to_order', from_entity=events,\n    to_entity=orders, keys=[ms.join_on(event_query_id, query_id)])\n\n\n\n\ncalendar = ms.entity(name='calendar', datasource=ms.ref.datasource('warehouse'),\n    source=md.table('calendar'))\ncalendar_date = ms.time_dimension_column(name='calendar_date', entity=calendar,\n    column='calendar_date', granularity='day')\nmonth = ms.dimension_column(name='month', entity=calendar, column='month')\nfiscal = ms.period_calendar(name='fiscal', date=calendar_date,\n    boundary_timezone='UTC',\n    coverage=(__import__('datetime').date(2041, 7, 17),\n              __import__('datetime').date(2041, 7, 19)),\n    levels={'month': month})\n"
        ),
        encoding="utf-8",
    )
    (root / "models" / "ontology.py").write_text(
        "import marivo.ontology as mo\n"
        "import marivo.semantic as ms\n"
        "mo.related_to(name='sales.order_context', "
        "left=ms.ref.entity('sales.orders'), right=ms.ref.metric('sales.revenue'), "
        "ai_context=ms.ai_context(business_definition='Order revenue describes orders.'))\n",
        encoding="utf-8",
    )


def test_project_handoff_separates_static_and_physical_evidence(
    authoring_evidence_project: Path,
) -> None:
    _extend_authoring_project(authoring_evidence_project)
    catalog = ms.load(workspace_dir=authoring_evidence_project)
    revenue = ms.ref.metric("sales.revenue")
    calendar = ms.ref.period_calendar("sales.fiscal")
    assert tuple(catalog.require(ref).ref for ref in (revenue, calendar)) == (
        revenue,
        calendar,
    )

    baseline = catalog.readiness(refs=[revenue])
    assert baseline.analysis_ready_inputs == (revenue,)
    calendar_report = catalog.readiness(refs=[calendar])
    assert calendar_report.analysis_ready_inputs == ()
    assert any(
        issue.kind == "period_calendar_artifact_missing" for issue in calendar_report.blockers
    )

    ontology = mo.load(semantic=catalog)
    assert ontology.edge_count == 1
    assert ontology.semantic_catalog_fingerprint == catalog.definition_fingerprint
    assert "entity:sales.orders -> metric:sales.revenue" in ontology.render()
    assert catalog.readiness(refs=[revenue]).analysis_ready_inputs == (revenue,)

    scope = md.unpruned(max_rows=10, timeout_seconds=30)
    preview = catalog.preview(revenue, scope=scope)
    assert preview.rows == ({"value": 751.5},)
    assert preview.coverage.scopes == (("sales.orders", scope),)
    assert preview.coverage.scope_exactness == "sample_only"

    health = catalog.source_health(
        [ms.ref.entity("sales.orders")],
        checks=[
            ms.source_check.allowed_values(
                ms.ref.dimension("sales.orders.region"), values=("moon-base",)
            )
        ],
        scope=scope,
    )
    assert health.status == "failed"
    value_check = next(check for check in health.checks if check.kind == "allowed_values")
    assert value_check.status == "failed"
    assert value_check.user_data_queried is True
    assert value_check.scopes == ((ms.ref.entity("sales.orders"), scope),)
    assert catalog.readiness(refs=[revenue]).to_dict() | {"checked_at": None} == (
        baseline.to_dict() | {"checked_at": None}
    )
