"""Snapshot-independent semantic readiness contracts."""

from __future__ import annotations

import json
import textwrap
from dataclasses import fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import get_args

import ibis

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo._authoring.model import AuthoringRepair, LiveHelpTarget
from marivo._compat import UTC
from marivo.semantic.readiness import (
    ReadinessInputSummary,
    ReadinessIssue,
    ReadinessIssueKind,
    ReadinessReport,
)

_DOMAIN = "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n"


def _ready_project(semantic_project_factory, *, datasource_path: Path | None = None):
    files = {
        "sales/_domain.py": _DOMAIN,
        "sales/models.py": textwrap.dedent(
            """\
                import marivo.datasource as md
                import marivo.semantic as ms
                orders = ms.entity(
                    name="orders",
                    datasource=ms.ref.datasource("warehouse"),
                    source=md.table("orders"),
                    ai_context=ms.ai_context(
                        business_definition="One row per order.",
                        guardrails=["Exclude tests."],
                    ),
                )
                region = ms.dimension_column(
                    name="region", entity=orders, column="region",
                    ai_context=ms.ai_context(
                        business_definition="Order region.",
                        guardrails=["Use normalized values."],
                    ),
                )
                @ms.measure(entity=orders, additivity="additive", unit="USD")
                def amount(orders):
                    return orders.amount
                revenue = ms.aggregate(
                    name="revenue", measure=amount, agg="sum",
                    ai_context=ms.ai_context(
                        business_definition="Order revenue.",
                        guardrails=["Exclude tests."],
                    ),
                )
                double_revenue = ms.linear(name="double_revenue", add=[revenue, revenue])
                """
        ),
    }
    if datasource_path is not None:
        files["datasources/warehouse.py"] = (
            "import marivo.datasource as md\n"
            f"md.duckdb(name='warehouse', path={str(datasource_path)!r})\n"
        )
    return semantic_project_factory(files)


def _without_checked_at(report: ReadinessReport) -> dict[str, object]:
    payload = report.to_dict()
    payload.pop("checked_at")
    return payload


def test_report_has_one_ready_input_projection() -> None:
    assert tuple(field.name for field in fields(ReadinessReport)) == (
        "status",
        "analysis_ready_inputs",
        "blockers",
        "warnings",
        "input_summary",
        "checked_at",
        "catalog_definition_fingerprint",
    )


def test_report_json_and_render_expose_only_analysis_ready_inputs() -> None:
    report = ReadinessReport(
        status="ready_with_warnings",
        analysis_ready_inputs=(ms.ref.metric("sales.revenue"),),
        blockers=(),
        warnings=(
            ReadinessIssue(
                kind="sql_parity_unverified",
                severity="warning",
                refs=("sales.revenue",),
                message="Parity remains advisory.",
                repair=AuthoringRepair(
                    kind="retry",
                    help_target=LiveHelpTarget(surface="semantic", canonical_id="parity_check"),
                    action="Run parity_check when parity matters.",
                ),
            ),
        ),
        input_summary=ReadinessInputSummary(
            datasources=("warehouse",),
            refs=("sales.revenue",),
            tables=("sales.orders",),
        ),
        checked_at="2026-08-21T00:00:00Z",
    )

    payload = json.loads(json.dumps(report.to_dict()))
    rendered = report.render()
    assert set(payload) == {
        "scope",
        "status",
        "analysis_ready_inputs",
        "blockers",
        "warnings",
        "input_summary",
        "checked_at",
        "catalog_definition_fingerprint",
    }
    assert payload["analysis_ready_inputs"] == [
        {"schema": "marivo.semantic_ref/v1", "kind": "metric", "path": "sales.revenue"}
    ]
    assert "analysis_ready:" in rendered
    assert "analysis_ready_refs" not in rendered
    assert "preview_required_refs" not in rendered


def test_runtime_expression_is_the_ready_input(semantic_project_factory) -> None:
    project = _ready_project(semantic_project_factory)
    catalog = ms.SemanticCatalog(project)
    expression = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.orders.amount"),
        agg="sum",
        label="Runtime revenue",
    )

    report = catalog.readiness(refs=[expression])

    assert report.status == "ready"
    assert report.analysis_ready_inputs == (expression,)
    assert report.to_dict()["analysis_ready_inputs"][0]["schema"] == "marivo.runtime_metric_expr/v1"


def test_direct_requests_only_are_ready_inputs(semantic_project_factory) -> None:
    project = _ready_project(semantic_project_factory)

    report = project.readiness(refs=("sales.double_revenue",))

    assert report.analysis_ready_inputs == (ms.ref.metric("sales.double_revenue"),)
    assert ms.ref.metric("sales.revenue") not in report.analysis_ready_inputs
    assert ms.ref.measure("sales.orders.amount") not in report.analysis_ready_inputs


def test_unknown_requested_ref_is_blocked(semantic_project_factory) -> None:
    report = _ready_project(semantic_project_factory).readiness(refs=("sales.missing",))
    assert report.status == "blocked"
    assert report.analysis_ready_inputs == ()
    assert {issue.kind for issue in report.blockers} == {"unknown_ref"}


def test_readiness_never_uses_discovery_or_preview_history(
    semantic_project_factory,
    monkeypatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "readiness.duckdb"
    backend = ibis.duckdb.connect(str(database_path))
    backend.raw_sql("CREATE TABLE orders (region TEXT, amount DOUBLE)")
    backend.raw_sql("INSERT INTO orders VALUES ('east', 10.0), ('west', 20.0)")
    backend.disconnect()
    project = _ready_project(semantic_project_factory, datasource_path=database_path)
    monkeypatch.chdir(tmp_path)
    history = tmp_path / ".marivo" / "authoring" / "checks"
    history.mkdir(parents=True)

    baseline = project.readiness(refs=("sales.revenue",))
    from marivo.datasource import authoring_store

    now = datetime(2026, 8, 21, tzinfo=UTC)
    monkeypatch.setattr(authoring_store, "_utc_now", lambda: now)
    inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))
    scope = md.unpruned(max_rows=100, timeout_seconds=30)
    fresh_snapshot = inspection.sample(
        scope=scope,
        columns=("region", "amount"),
        refresh=True,
    )
    history.joinpath("fresh.json").write_text('{"cache_status":"fresh"}', encoding="utf-8")
    fresh = project.readiness(refs=("sales.revenue",))
    monkeypatch.setattr(authoring_store, "_utc_now", lambda: now + timedelta(hours=25))
    stale_snapshot = inspection.sample(scope=scope, columns=("region", "amount"))
    history.joinpath("stale.json").write_text('{"cache_status":"stale"}', encoding="utf-8")
    stale = project.readiness(refs=("sales.revenue",))

    assert fresh_snapshot.cache_status == "fresh"
    assert stale_snapshot.id == fresh_snapshot.id
    assert stale_snapshot.cache_status == "stale"

    from marivo.datasource.authoring_store import AuthoringStore

    monkeypatch.setattr(
        AuthoringStore,
        "valid_snapshots",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("history queried")),
    )
    guarded = project.readiness(refs=("sales.revenue",))

    expected = _without_checked_at(baseline)
    assert _without_checked_at(fresh) == expected
    assert _without_checked_at(stale) == expected
    assert _without_checked_at(guarded) == expected
    kinds = {issue.kind for issue in (*guarded.blockers, *guarded.warnings)}
    assert "snapshot_missing" not in kinds
    assert "runtime_preview_missing" not in kinds


def test_business_context_is_advisory_richness_only(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN,
            "sales/models.py": textwrap.dedent(
                """\
                import marivo.datasource as md
                import marivo.semantic as ms
                orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
                region = ms.dimension_column(name="region", entity=orders, column="region")
                @ms.measure(entity=orders, additivity="additive", unit="USD")
                def amount(orders):
                    return orders.amount
                revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
                """
            ),
        }
    )

    readiness = project.readiness(refs=("sales.revenue",))
    richness = project.richness()

    assert readiness.status == "ready"
    assert not readiness.blockers
    assert not readiness.warnings
    gaps = {(gap.subkind, ref) for gap in richness.gaps for ref in gap.refs}
    assert ("missing_business_definition", "sales.orders") in gaps
    assert ("missing_guardrails", "sales.orders.region") in gaps
    assert ("missing_business_definition", "sales.orders.amount") in gaps
    assert ("missing_guardrails", "sales.revenue") in gaps


def test_cross_datasource_metric_remains_blocked(semantic_project_factory, tmp_path: Path) -> None:
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(tmp_path / 'warehouse.duckdb')!r})\n"
            ),
            "datasources/finance.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='finance', path={str(tmp_path / 'finance.duckdb')!r})\n"
            ),
            "sales/_domain.py": _DOMAIN,
            "sales/models.py": textwrap.dedent(
                """\
                import marivo.datasource as md
                import marivo.semantic as ms
                orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
                refunds = ms.entity(name="refunds", datasource=ms.ref.datasource("finance"), source=md.table("refunds"))
                @ms.metric(entities=[orders, refunds], root_entity=orders, additivity="additive")
                def net_revenue(orders, refunds):
                    return orders.amount.sum()
                """
            ),
        }
    )

    report = project.readiness(refs=("sales.net_revenue",))
    assert report.status == "blocked"
    assert report.analysis_ready_inputs == ()
    assert "cross_datasource_unfederated" in {issue.kind for issue in report.blockers}


def test_issue_vocabulary_has_artifacts_and_no_removed_history_or_richness_names() -> None:
    kinds = set(get_args(ReadinessIssueKind))
    assert {
        "period_calendar_artifact_missing",
        "period_calendar_artifact_stale",
        "period_calendar_artifact_invalid",
        "temporal_set_artifact_missing",
        "work_schedule_artifact_missing",
    } <= kinds
    assert {
        "snapshot_missing",
        "runtime_preview_missing",
        "missing_business_definition",
        "missing_guardrails",
    }.isdisjoint(kinds)
