"""Snapshot-independent semantic readiness contracts."""

from __future__ import annotations

import json
import textwrap
from collections.abc import Callable
from dataclasses import fields
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import ibis
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo._authoring.model import AuthoringRepair
from marivo._compat import UTC
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic.errors import StructuredWarning
from marivo.semantic.reader import SemanticProject
from marivo.semantic.readiness import (
    ReadinessInputSummary,
    ReadinessIssue,
    ReadinessReport,
)

_DOMAIN = "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n"


def _ready_project(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    *,
    datasource_path: Path | None = None,
) -> SemanticProject:
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
                amount_ref = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="USD")
                finalized = ms.finalize()
                amount = finalized.resolve(amount_ref)
                @ms.metric(
                    ai_context=ms.ai_context(
                        business_definition="Order revenue.",
                        guardrails=["Exclude tests."],
                    ),
                )
                def revenue():
                    return amount.sum()
                @ms.metric()
                def double_revenue():
                    return revenue + revenue
                """
        ),
    }
    if datasource_path is not None:
        files["datasources/warehouse.py"] = (
            "import marivo.datasource as md\n"
            f"md.duckdb(name='warehouse', path={str(datasource_path)!r})\n"
        )
    return semantic_project_factory(files)


def test_analysis_readiness_help_example_accepts_physical_selection(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    from marivo.analysis._capabilities.catalog_inputs import CATALOG_INPUTS

    catalog = ms.SemanticCatalog(_ready_project(semantic_project_factory))
    descriptor = next(item for item in CATALOG_INPUTS if item.id == "catalog.readiness")
    assert descriptor.example is not None
    namespace: dict[str, object] = {"session": SimpleNamespace(catalog=catalog)}
    exec(compile(descriptor.example, "<readiness-help-example>", "exec"), namespace)
    report = namespace["result"]
    assert isinstance(report, ReadinessReport)
    assert report.analysis_ready_inputs == (ms.ref.entity("sales.orders"),)


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
                kind="fragile_string_ref",
                severity="warning",
                refs=("sales.revenue",),
                message="Historical provenance remains unverified.",
                repair=AuthoringRepair(
                    kind="reauthor",
                    help_target=LiveHelpTarget(surface="semantic", canonical_id="metric"),
                    action="Review against an independent business source.",
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


def test_direct_requests_only_are_ready_inputs(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    project = _ready_project(semantic_project_factory)

    report = project.readiness(refs=("sales.orders.amount",))

    assert report.analysis_ready_inputs == (ms.ref.measure("sales.orders.amount"),)
    assert ms.ref.metric("sales.revenue") not in report.analysis_ready_inputs
    assert ms.ref.entity("sales.orders") not in report.analysis_ready_inputs


def test_scoped_readiness_uses_canonical_dependencies_and_local_warnings(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _ready_project(semantic_project_factory)
    monkeypatch.setattr(
        project,
        "_warnings",
        (
            StructuredWarning(
                kind="string_ref",
                message="Measure dependency warning.",
                refs=("sales.orders.amount",),
                location=None,
            ),
            StructuredWarning(
                kind="string_ref",
                message="Unrelated region warning.",
                refs=("sales.orders.region",),
                location=None,
            ),
        ),
    )

    metric = project.readiness(refs=(ms.ref.measure("sales.orders.amount"),))
    assert metric.analysis_ready_inputs == (ms.ref.measure("sales.orders.amount"),)
    assert "sales.orders.amount" in metric.input_summary.refs
    assert "sales.orders.region" not in metric.input_summary.refs
    assert tuple(issue.refs for issue in metric.warnings) == (("sales.orders.amount",),)

    region = project.readiness(refs=(ms.ref.dimension("sales.orders.region"),))
    assert tuple(issue.refs for issue in region.warnings) == (("sales.orders.region",),)


def test_unknown_requested_ref_is_blocked(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    report = _ready_project(semantic_project_factory).readiness(refs=("sales.missing",))
    assert report.status == "blocked"
    assert report.analysis_ready_inputs == ()
    assert {issue.kind for issue in report.blockers} == {"unknown_ref"}


def test_readiness_never_uses_discovery_or_preview_history(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    monkeypatch: pytest.MonkeyPatch,
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

    baseline = project.readiness(refs=("sales.orders.amount",))
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
    fresh = project.readiness(refs=("sales.orders.amount",))
    monkeypatch.setattr(authoring_store, "_utc_now", lambda: now + timedelta(hours=25))
    stale_snapshot = inspection.sample(scope=scope, columns=("region", "amount"))
    history.joinpath("stale.json").write_text('{"cache_status":"stale"}', encoding="utf-8")
    stale = project.readiness(refs=("sales.orders.amount",))

    assert fresh_snapshot.cache_status == "fresh"
    assert stale_snapshot.id == fresh_snapshot.id
    assert stale_snapshot.cache_status == "stale"

    from marivo.datasource.authoring_store import AuthoringStore

    monkeypatch.setattr(
        AuthoringStore,
        "valid_snapshots",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("history queried")),
    )
    guarded = project.readiness(refs=("sales.orders.amount",))

    expected = _without_checked_at(baseline)
    assert _without_checked_at(fresh) == expected
    assert _without_checked_at(stale) == expected
    assert _without_checked_at(guarded) == expected
    kinds = {issue.kind for issue in (*guarded.blockers, *guarded.warnings)}
    assert "snapshot_missing" not in kinds
    assert "runtime_preview_missing" not in kinds


def test_missing_business_context_does_not_block_readiness(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN,
            "sales/models.py": textwrap.dedent(
                """\
                import marivo.datasource as md
                import marivo.semantic as ms
                orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
                region = ms.dimension_column(name="region", entity=orders, column="region")
                amount_ref = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="USD")
                finalized = ms.finalize()
                amount = finalized.resolve(amount_ref)
                @ms.metric()
                def revenue():
                    return amount.sum()
                """
            ),
        }
    )

    readiness = project.readiness(refs=("sales.orders.amount",))
    assert readiness.status == "ready"
    assert not readiness.blockers
    assert not readiness.warnings


def test_cross_datasource_metric_certification_checks_physical_inputs(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject], tmp_path: Path
) -> None:
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
                amount_ref = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all())
                refund_ref = ms.measure_column(name="amount", entity=refunds, column="amount", additivity=ms.additive_all())
                finalized = ms.finalize()
                amount = finalized.resolve(amount_ref)
                refunded = finalized.resolve(refund_ref)
                @ms.metric()
                def net_revenue():
                    return amount.sum() - refunded.sum()
                """
            ),
        }
    )

    catalog = ms.SemanticCatalog(project)
    with pytest.raises(ms.errors.SemanticRuntimeError) as caught:
        catalog.readiness(refs=[ms.ref.metric("sales.net_revenue")])
    assert caught.value.kind == "invalid_expression_stage"
    assert caught.value.repair is not None
    assert "physical fields and Entities" in caught.value.repair.action
    assert caught.value.hint == (
        'Run marivo.help("semantic.readiness") to inspect physical input requirements.'
    )
    assert caught.value.repair.help_target == LiveHelpTarget(
        surface="semantic", canonical_id="readiness"
    )
    inputs = (ms.ref.measure("sales.orders.amount"), ms.ref.measure("sales.refunds.amount"))
    report = catalog.readiness(refs=inputs)
    assert report.status == "ready"
    assert report.analysis_ready_inputs == inputs
    assert set(report.input_summary.datasources) == {"warehouse", "finance"}


def test_retired_status_fold_requires_explicit_sample_reduction(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN,
            "sales/models.py": textwrap.dedent(
                """\
                import marivo.datasource as md
                import marivo.semantic as ms
                snapshots = ms.entity(
                    name="snapshots",
                    datasource=ms.ref.datasource("warehouse"),
                    source=md.table("snapshots"),
                )
                snapshot_date = ms.time_dimension_column(
                    name="snapshot_date",
                    entity=snapshots,
                    column="snapshot_date",
                    )
                value = ms.measure_column(
                    name="value",
                    entity=snapshots,
                    column="value",
                    additivity=ms.additive_all(except_=(snapshot_date,)),
                    status_time_dimension=snapshot_date,
                    status_time_fold=("percentile", 0.95),
                )
                """
            ),
        }
    )
    assert not project.is_ready()
    (error,) = project.errors()
    assert error.kind == "invalid_component_body"
    assert error.received is not None and "status_time_fold" in error.received
    assert error.repair is not None
    assert "per(status_time_field).reducer()" in error.repair.action


@pytest.mark.parametrize("parse", ("None", "ms.timestamp()", "ms.timestamp(timezone='UTC')"))
def test_readiness_defers_native_timestamp_kind_and_authority_without_probing(
    semantic_project_factory,
    monkeypatch: pytest.MonkeyPatch,
    parse: str,
) -> None:
    from marivo.datasource.timezone import probe_engine_timezone

    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN,
            "sales/models.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "events=ms.entity(name='events',datasource=ms.ref.datasource('warehouse'),source=md.table('events'))\n"
                f"point=ms.time_dimension_column(name='point',entity=events,column='point',parse={parse})\n"
            ),
        }
    )

    def reject_probe(*args: object, **kwargs: object) -> None:
        pytest.fail("static readiness probed physical time authority")

    monkeypatch.setattr(probe_engine_timezone.__module__ + ".probe_engine_timezone", reject_probe)
    report = project.readiness(refs=(ms.ref.time_dimension("sales.events.point"),))
    assert report.status == "ready"
    assert report.blockers == ()
    assert report.analysis_ready_inputs == (ms.ref.time_dimension("sales.events.point"),)
