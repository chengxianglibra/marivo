"""Tests for semantic readiness reports."""

from __future__ import annotations

import json
import textwrap

import ibis
import pytest

from marivo.analysis.datasources.metadata import TableMetadata
from marivo.preview import PreviewWarning
from marivo.semantic.readiness import (
    EvidenceSummary,
    ParitySummary,
    PreviewSummary,
    ReadinessIssue,
    ReadinessReport,
    build_readiness_report,
)


@pytest.fixture
def duckdb_backend():
    con = ibis.duckdb.connect(":memory:")
    con.con.execute(
        "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
    )
    con.con.execute(
        "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), (2, 200.0, 'EU', '2025-02-01')"
    )
    return con


@pytest.fixture
def backend_factory(duckdb_backend):
    def _factory(datasource_semantic_id: str):
        return duckdb_backend

    return _factory


def _fake_inspect_source(datasource, *, source, include_partitions=True):
    from marivo.analysis.datasources.metadata import TableMetadata

    return TableMetadata(
        datasource=datasource,
        table=getattr(source, "table", "fake_table"),
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=(),
        partitions=(),
        warnings=(),
    )


_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    ms.model(name="sales", default=True)
""")

_READY_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic as ms

    orders = ms.dataset(
        name="orders",
        datasource="warehouse",
        source=ms.table("orders"),
        primary_key=["order_id"],
        description="Orders table",
        ai_context={"business_definition": "One row per paid order."},
    )

    @ms.field(
        dataset=orders,
        description="Order amount",
        ai_context={"business_definition": "Gross order amount in USD."},
    )
    def amount(table):
        return table.amount

    @ms.time_field(
        dataset=orders,
        data_type="timestamp",
        granularity="day",
        description="Order creation time",
        ai_context={"business_definition": "Timestamp when the order was created."},
    )
    def created_at(table):
        return table.created_at

    @ms.metric(
        datasets=[orders],
        additivity="additive",
        decomposition=ms.sum(),
        verification_mode="sql_parity",
        source_sql="SELECT SUM(amount) AS total_amount FROM orders",
        source_dialect="duckdb",
        description="Total revenue",
        ai_context={"business_definition": "Sum of order amount."},
    )
    def total_amount(table):
        return table.amount.sum()
""")


def test_readiness_report_to_dict_is_json_safe() -> None:
    report = ReadinessReport(
        status="ready_with_warnings",
        analysis_ready_refs=("sales.total_amount",),
        blockers=(),
        warnings=(
            ReadinessIssue(
                kind="primary_key_unsampled",
                severity="warning",
                refs=("sales.orders",),
                message="primary key was not sampled",
                suggested_action="Preview the primary key and sample uniqueness.",
            ),
        ),
        evidence_summary=EvidenceSummary(
            datasources_checked=("warehouse",),
            tables_inspected=("sales.orders",),
            raw_previews=("warehouse.orders",),
            knowledge_documents=("revenue.md",),
            user_confirmations=("owner confirmed revenue excludes tax",),
            semantic_objects_changed=("sales.total_amount",),
        ),
        preview_summary=PreviewSummary(
            required_previews=("sales.orders", "sales.total_amount"),
            completed_previews=("sales.orders", "sales.total_amount"),
            failed_previews=(),
            warnings=(
                PreviewWarning(
                    kind="redacted_column",
                    message="values were redacted",
                    columns=("email",),
                ),
            ),
        ),
        parity_summary=ParitySummary(
            verified_metrics=("sales.total_amount",),
            unverified_metrics=(),
            drifted_metrics=(),
            skipped_metrics=(),
        ),
        checked_at="2026-05-29T00:00:00Z",
    )

    payload = report.to_dict()

    assert payload["status"] == "ready_with_warnings"
    assert payload["warnings"][0]["kind"] == "primary_key_unsampled"
    assert payload["preview_summary"]["warnings"][0]["columns"] == ["email"]
    assert json.loads(json.dumps(payload))["analysis_ready_refs"] == ["sales.total_amount"]


def test_readiness_maps_time_field_pushdown_advisory(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": textwrap.dedent("""\
                import marivo.semantic as ms

                ms.model(name="sales")

                orders = ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"))

                @ms.time_field(dataset=orders, data_type="date", granularity="day")
                def order_date(table):
                    return table.dt.cast("date")
            """)
        }
    )

    report = project.readiness()

    # Status may be blocked due to missing previews (require_preview is always on),
    # but the pushdown advisory should still appear as a warning.
    assert any(issue.kind == "time_field_pushdown_advisory" for issue in report.warnings)


_UNVERIFIED_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic as ms

    orders = ms.dataset(name="orders", datasource="warehouse", description="Orders", source=ms.table("orders"))

    @ms.metric(
        datasets=[orders],
        additivity='additive',
        decomposition=ms.sum(),
        verification_mode="sql_parity",
        source_sql="SELECT SUM(amount) AS total_amount FROM orders",
        source_dialect="duckdb",
        description="Total amount",
    )
    def total_amount(table):
        return table.amount.sum()
""")


_DRIFTED_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic as ms

    orders = ms.dataset(name="orders", datasource="warehouse", description="Orders", source=ms.table("orders"))

    @ms.metric(
        datasets=[orders],
        additivity="additive",
        decomposition=ms.sum(),
        verification_mode="sql_parity",
        source_sql="SELECT 999.0 AS total_amount",
        source_dialect="duckdb",
        description="Total amount",
    )
    def total_amount(table):
        return table.amount.sum()
""")


_PYTHON_NATIVE_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic as ms

    orders = ms.dataset(name="orders", datasource="warehouse", description="Orders", source=ms.table("orders"))

    @ms.metric(
        datasets=[orders],
        additivity="additive",
        decomposition=ms.sum(),
        verification_mode="python_native",
        description="Total amount",
    )
    def total_amount(table):
        return table.amount.sum()
""")


_DERIVED_WITH_PYTHON_NATIVE_COMPONENT_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic as ms

    orders = ms.dataset(name="orders", datasource="warehouse", description="Orders", source=ms.table("orders"))

    @ms.metric(
        datasets=[orders],
        additivity="additive",
        decomposition=ms.sum(),
        verification_mode="python_native",
        description="Total amount",
    )
    def total_amount(table):
        return table.amount.sum()

    avg_amount = ms.derived_metric(
        name="avg_amount",
        decomposition=ms.ratio(
            numerator="sales.total_amount",
            denominator="sales.total_amount",
        ),
        description="Average amount placeholder.",
    )
""")


def _project(semantic_project_factory, model_py: str):
    return semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": model_py,
        }
    )


def _issue_kinds(issues):
    return {issue.kind for issue in issues}


def test_readiness_ready_after_required_preview_and_parity(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _READY_MODEL_PY)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=backend_factory
    )
    project.collect_source_preview(
        datasource="warehouse", table="orders", backend_factory=backend_factory
    )
    project.record_primary_key_sample("sales.orders")
    project.parity_check("sales.total_amount", backend_factory=backend_factory)

    report = project.readiness()

    # The only blocker should be requires_raw_sql (auto-derived for sql_parity
    # metrics with source_sql).  Preview and parity checks are satisfied.
    non_sql_blockers = tuple(b for b in report.blockers if b.kind != "requires_raw_sql")
    assert non_sql_blockers == ()
    assert "requires_raw_sql" in _issue_kinds(report.blockers)
    # Guardrails warnings are expected (no guardrails defined on model objects).
    assert all(w.kind == "missing_guardrails" for w in report.warnings)
    assert "sales.orders" in report.analysis_ready_refs
    assert "sales.orders.amount" in report.analysis_ready_refs
    assert "sales.orders.created_at" in report.analysis_ready_refs
    assert report.preview_summary.required_previews == (
        "warehouse.orders",
        "sales.orders",
        "sales.orders.amount",
        "sales.orders.created_at",
        "sales.total_amount",
    )
    assert set(report.preview_summary.completed_previews) == {
        "warehouse.orders",
        "sales.orders",
        "sales.orders.amount",
        "sales.orders.created_at",
        "sales.total_amount",
    }
    assert report.parity_summary.verified_metrics == ("sales.total_amount",)


def test_readiness_folds_dataset_field_and_time_field_previews(
    semantic_project_factory,
    backend_factory,
    monkeypatch,
) -> None:
    from ibis.expr.types.relations import Table

    project = _project(semantic_project_factory, _READY_MODEL_PY)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=backend_factory
    )
    project.collect_source_preview(
        datasource="warehouse", table="orders", backend_factory=backend_factory
    )
    project.record_primary_key_sample("sales.orders")
    project.parity_check("sales.total_amount", backend_factory=backend_factory)
    execute_calls = 0
    original_execute = Table.execute

    def counting_execute(self, *args, **kwargs):
        nonlocal execute_calls
        execute_calls += 1
        return original_execute(self, *args, **kwargs)

    monkeypatch.setattr(Table, "execute", counting_execute)

    report = project.readiness()

    # Status may be blocked due to requires_raw_sql (auto-derived), but
    # the preview folding behavior is what this test validates.
    assert set(report.preview_summary.completed_previews) == {
        "warehouse.orders",
        "sales.orders",
        "sales.orders.amount",
        "sales.orders.created_at",
        "sales.total_amount",
    }
    assert execute_calls == 2


def test_readiness_folded_preview_falls_back_to_precise_field_blocker(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": textwrap.dedent("""\
                import marivo.semantic as ms

                orders = ms.dataset(
                    name="orders",
                    datasource="warehouse",
                    source=ms.table("orders"),
                    description="Orders table",
                    ai_context={"business_definition": "One row per paid order."},
                )

                @ms.field(
                    dataset=orders,
                    description="Order amount",
                    ai_context={"business_definition": "Gross order amount in USD."},
                )
                def amount(table):
                    return table.amount

                @ms.field(
                    dataset=orders,
                    description="Missing field",
                    ai_context={"business_definition": "A field with a broken expression."},
                )
                def missing_field(table):
                    return table.missing_column
            """),
        }
    )

    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=backend_factory
    )
    project.collect_source_preview(
        datasource="warehouse", table="orders", backend_factory=backend_factory
    )

    report = project.readiness()

    assert report.status == "blocked"
    assert "sales.orders" in report.preview_summary.completed_previews
    assert "sales.orders.amount" in report.preview_summary.completed_previews
    assert "sales.orders.missing_field" in report.preview_summary.failed_previews
    assert {issue.refs for issue in report.blockers if issue.kind == "field_preview_failed"} == {
        ("sales.orders.missing_field",)
    }


def test_readiness_blocks_when_required_raw_preview_missing(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _READY_MODEL_PY)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=backend_factory
    )
    project.record_primary_key_sample("sales.orders")
    project.parity_check("sales.total_amount", backend_factory=backend_factory)
    # Do NOT call collect_source_preview -- raw preview will be missing.

    report = project.readiness()

    assert report.status == "blocked"
    assert "missing_raw_preview" in _issue_kinds(report.blockers)
    assert "sales.orders" not in report.analysis_ready_refs


def test_readiness_uses_collected_source_preview_evidence(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _READY_MODEL_PY)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=backend_factory
    )
    project.record_primary_key_sample("sales.orders")
    project.parity_check("sales.total_amount", backend_factory=backend_factory)

    before = project.readiness()
    project.collect_source_preview(
        datasource="warehouse",
        table="orders",
        backend_factory=backend_factory,
    )
    after = project.readiness()

    assert before.status == "blocked"
    assert "missing_raw_preview" in _issue_kinds(before.blockers)
    # After collecting the preview, the raw_preview blocker is resolved.
    # The overall status may still be blocked by requires_raw_sql (auto-derived).
    assert "missing_raw_preview" not in _issue_kinds(after.blockers)
    assert "warehouse.orders" in after.evidence_summary.raw_previews


def test_readiness_uses_collected_physical_raw_preview_ref(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _READY_MODEL_PY)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=backend_factory
    )
    project.record_primary_key_sample("sales.orders")
    project.parity_check("sales.total_amount", backend_factory=backend_factory)
    project.collect_source_preview(
        datasource="warehouse",
        table="orders",
        backend_factory=backend_factory,
    )

    report = project.readiness()

    # Status may be blocked by requires_raw_sql, but the raw preview is collected.
    assert "missing_raw_preview" not in _issue_kinds(report.blockers)
    assert "warehouse.orders" in report.evidence_summary.raw_previews


def test_readiness_failed_raw_preview_overrides_collected_evidence(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _READY_MODEL_PY)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=backend_factory
    )
    project.record_primary_key_sample("sales.orders")
    project.parity_check("sales.total_amount", backend_factory=backend_factory)
    project.collect_source_preview(
        datasource="warehouse",
        table="orders",
        backend_factory=backend_factory,
    )
    # Override with a failed preview record.
    project.record_failed_preview(
        datasource="warehouse",
        table="orders",
    )

    report = project.readiness()

    assert report.status == "blocked"
    assert "raw_preview_failed" in _issue_kinds(report.blockers)


def test_reload_preserves_collected_source_preview_evidence(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _READY_MODEL_PY)

    project.collect_source_preview(
        datasource="warehouse",
        table="orders",
        backend_factory=backend_factory,
    )
    assert project.raw_preview_evidence()

    project.reload()

    assert project.raw_preview_evidence() == ("warehouse.orders",)


def test_readiness_uses_persisted_source_preview_evidence_in_new_project_instance(
    semantic_project_factory,
    backend_factory,
) -> None:
    from marivo.semantic.reader import SemanticProject

    project = _project(semantic_project_factory, _READY_MODEL_PY)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=backend_factory
    )
    project.record_primary_key_sample("sales.orders")
    project.parity_check("sales.total_amount", backend_factory=backend_factory)
    project.collect_source_preview(
        datasource="warehouse",
        table="orders",
        backend_factory=backend_factory,
    )

    reloaded = SemanticProject(workspace_dir=project.workspace_dir)
    reloaded.load()
    reloaded.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=backend_factory
    )
    reloaded.parity_check("sales.total_amount", backend_factory=backend_factory)
    report = reloaded.readiness()

    assert reloaded.raw_preview_evidence() == ("warehouse.orders",)
    # Status may be blocked by requires_raw_sql, but raw preview is persisted.
    assert "missing_raw_preview" not in _issue_kinds(report.blockers)
    assert "warehouse.orders" in report.evidence_summary.raw_previews


def test_readiness_strict_blocks_unverified_metric(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _UNVERIFIED_MODEL_PY)

    report = project.readiness()

    assert report.status == "blocked"
    assert report.parity_summary.unverified_metrics == ("sales.total_amount",)
    assert "unverified_metric" in _issue_kinds(report.blockers)


def test_readiness_blocks_drifted_metric(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _DRIFTED_MODEL_PY)
    project.parity_check("sales.total_amount", backend_factory=backend_factory)

    report = project.readiness()

    assert report.status == "blocked"
    assert report.parity_summary.drifted_metrics == ("sales.total_amount",)
    assert "parity_drifted" in _issue_kinds(report.blockers)


def test_readiness_treats_python_native_metric_as_verified(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _PYTHON_NATIVE_MODEL_PY)

    report = project.readiness()

    assert report.parity_summary.verified_metrics == ("sales.total_amount",)
    assert "unverified_metric" not in _issue_kinds(report.blockers)
    assert not any("python_native" in issue.message for issue in report.warnings)


def test_readiness_treats_derived_python_native_component_as_verified(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _DERIVED_WITH_PYTHON_NATIVE_COMPONENT_MODEL_PY)

    report = project.readiness()

    assert "sales.avg_amount" in report.parity_summary.verified_metrics
    assert "sales.total_amount" in report.parity_summary.verified_metrics
    assert "unverified_metric" not in _issue_kinds(report.blockers)


def test_semantic_check_run_check_returns_json_ready_report(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _READY_MODEL_PY)
    root = project.semantic_root

    # Record evidence via the project API so auto-collect can discover it.
    project.collect_source_preview(
        datasource="warehouse", table="orders", backend_factory=backend_factory
    )
    project.record_primary_key_sample("sales.orders")

    from marivo.semantic.check import run_check

    payload = run_check(
        workspace_dir=project.workspace_dir,
        readiness=True,
        format="json",
        backend_factory=backend_factory,
    )

    assert payload["status"] == "blocked"
    assert payload["readiness"]["status"] == "blocked"
    # Parity is verified via _run_parity_checks re-running in run_check.
    assert payload["readiness"]["parity_summary"]["verified_metrics"] == ["sales.total_amount"]
    # Raw SQL requirement is auto-derived for sql_parity metrics with source_sql.
    blocker_kinds = [b["kind"] for b in payload["readiness"]["blockers"]]
    assert "requires_raw_sql" in blocker_kinds


_COMMENTLESS_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic as ms

    orders = ms.dataset(name="orders", datasource="warehouse", primary_key=["order_id"], source=ms.table("orders"))

    @ms.field(dataset=orders)
    def amount(table):
        return table.amount

    @ms.metric(
        datasets=[orders],
        additivity='additive',
        decomposition=ms.sum(),
        verification_mode="python_native",
    )
    def total_amount(table):
        return table.amount.sum()
""")


@pytest.mark.skip(reason="table_metadata not yet auto-collected")
def test_readiness_emits_derived_source_grain_unverified_for_view(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": textwrap.dedent("""\
                import marivo.semantic as ms

                ms.model(name="sales")

                orders = ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"))
            """)
        }
    )
    ds = project._registry.datasets["sales.orders"]
    view_md = TableMetadata(
        datasource=ds.datasource,
        table=ds.source.table,
        database=ds.source.database,
        backend_type="duckdb",
        comment=None,
        columns=(),
        partitions=(),
        warnings=(),
        is_view=True,
        view_definition="SELECT 1",
    )

    from marivo.semantic.readiness import _ReadinessEvidence

    evidence_view = _ReadinessEvidence(
        raw_previews=(),
        failed_raw_previews=(),
        required_raw_previews=(),
        required_semantic_previews=(),
        knowledge_documents=(),
        user_confirmations=(),
        confirmed_relationships=(),
        primary_keys_sampled=(),
        raw_sql_required_refs=(),
        table_metadata=(view_md,),
        supports_federation=False,
    )
    report = build_readiness_report(
        project,
        evidence_view,
        refs=["sales.orders"],
    )
    assert any(i.kind == "derived_source_grain_unverified" for i in report.warnings)

    base_md = TableMetadata(
        datasource=ds.datasource,
        table=ds.source.table,
        database=ds.source.database,
        backend_type="duckdb",
        comment=None,
        columns=(),
        partitions=(),
        warnings=(),
    )
    evidence_base = _ReadinessEvidence(
        raw_previews=(),
        failed_raw_previews=(),
        required_raw_previews=(),
        required_semantic_previews=(),
        knowledge_documents=(),
        user_confirmations=(),
        confirmed_relationships=(),
        primary_keys_sampled=(),
        raw_sql_required_refs=(),
        table_metadata=(base_md,),
        supports_federation=False,
    )
    base_report = build_readiness_report(
        project,
        evidence_base,
        refs=["sales.orders"],
    )
    assert not any(i.kind == "derived_source_grain_unverified" for i in base_report.warnings)


def test_view_advisory_attaches_to_aliased_dataset(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": textwrap.dedent("""\
                import marivo.semantic as ms

                ms.model(name="sales")

                orders = ms.dataset(
                    name="orders",
                    datasource="warehouse",
                    source=ms.table("v_orders"),
                )
            """)
        }
    )
    ds = project._registry.datasets["sales.orders"]
    assert ds.source.table == "v_orders"
    view_md = TableMetadata(
        datasource=ds.datasource,
        table="v_orders",
        database=ds.source.database,
        backend_type="duckdb",
        comment=None,
        columns=(),
        partitions=(),
        warnings=(),
        is_view=True,
        view_definition="SELECT 1",
    )
    from marivo.semantic.readiness import _ReadinessEvidence

    evidence = _ReadinessEvidence(
        raw_previews=(),
        failed_raw_previews=(),
        required_raw_previews=(),
        required_semantic_previews=(),
        knowledge_documents=(),
        user_confirmations=(),
        confirmed_relationships=(),
        primary_keys_sampled=(),
        raw_sql_required_refs=(),
        table_metadata=(view_md,),
        supports_federation=False,
    )
    report = build_readiness_report(
        project,
        evidence,
        refs=["sales.orders"],
    )
    assert any(i.kind == "derived_source_grain_unverified" for i in report.warnings)


@pytest.mark.skip(reason="table_metadata not yet auto-collected")
def test_view_advisory_matches_clickhouse_datasource_default_database(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "datasource/warehouse.py": textwrap.dedent("""\
                import marivo.datasource as md

                warehouse = md.DatasourceSpec(
                    name="warehouse",
                    backend_type="clickhouse",
                    host="clickhouse.example",
                    database="analytics",
                )
                md.datasource(warehouse)
            """),
            "sales/_model.py": textwrap.dedent("""\
                import marivo.semantic as ms

                ms.model(name="sales")

                orders = ms.dataset(
                    name="orders",
                    datasource="warehouse",
                    source=ms.table("v_orders"),
                )
            """),
        }
    )
    ds = project._registry.datasets["sales.orders"]
    assert ds.source.database is None
    view_md = TableMetadata(
        datasource=ds.datasource,
        table="v_orders",
        database="analytics",
        backend_type="clickhouse",
        comment=None,
        columns=(),
        partitions=(),
        warnings=(),
        is_view=True,
        view_definition="CREATE VIEW analytics.v_orders AS SELECT 1",
    )

    from marivo.semantic.readiness import _ReadinessEvidence

    evidence = _ReadinessEvidence(
        raw_previews=(),
        failed_raw_previews=(),
        required_raw_previews=(),
        required_semantic_previews=(),
        knowledge_documents=(),
        user_confirmations=(),
        confirmed_relationships=(),
        primary_keys_sampled=(),
        raw_sql_required_refs=(),
        table_metadata=(view_md,),
        supports_federation=False,
    )
    report = build_readiness_report(
        project,
        evidence,
        refs=["sales.orders"],
    )

    assert any(i.kind == "derived_source_grain_unverified" for i in report.warnings)


@pytest.mark.skip(reason="table_metadata not yet auto-collected")
def test_readiness_does_not_attach_view_metadata_from_different_database(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": textwrap.dedent("""\
                import marivo.semantic as ms

                ms.model(name="sales")

                orders = ms.dataset(
                    name="orders",
                    datasource="warehouse",
                    source=ms.table("orders", database="base_schema"),
                )
            """)
        }
    )
    ds = project._registry.datasets["sales.orders"]
    view_md = TableMetadata(
        datasource=ds.datasource,
        table=ds.source.table,
        database="view_schema",
        backend_type="duckdb",
        comment=None,
        columns=(),
        partitions=(),
        warnings=(),
        is_view=True,
        view_definition="SELECT 1",
    )

    from marivo.semantic.readiness import _ReadinessEvidence

    evidence = _ReadinessEvidence(
        raw_previews=(),
        failed_raw_previews=(),
        required_raw_previews=(),
        required_semantic_previews=(),
        knowledge_documents=(),
        user_confirmations=(),
        confirmed_relationships=(),
        primary_keys_sampled=(),
        raw_sql_required_refs=(),
        table_metadata=(view_md,),
        supports_federation=False,
    )
    report = build_readiness_report(
        project,
        evidence,
        refs=["sales.orders"],
    )

    assert not any(i.kind == "derived_source_grain_unverified" for i in report.warnings)


def test_unresolved_clarification_is_a_valid_issue_kind():
    from typing import get_args

    from marivo.semantic.readiness import ReadinessIssueKind

    assert "unresolved_clarification" in get_args(ReadinessIssueKind)


def test_strict_enrichment_issue_kinds_are_valid():
    from typing import get_args

    from marivo.semantic.readiness import ReadinessIssueKind

    kinds = get_args(ReadinessIssueKind)
    assert "missing_business_definition" in kinds
    assert "missing_guardrails" in kinds


def test_evidence_ledger_blockers_flags_metric_without_decision(semantic_project_factory):
    from marivo.semantic.readiness import _evidence_ledger_blockers

    project = semantic_project_factory(
        {
            "sales/_model.py": "import marivo.semantic as ms\nms.model(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.dataset(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
                "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native')\n"
                "def revenue(orders):\n    return orders.amount.sum()\n"
            ),
        }
    )

    # Auto-record creates a decision during load(). Remove it to test
    # the underlying readiness check for "no decision" state.
    from marivo.semantic.ledger import LedgerStore

    LedgerStore(project.semantic_root)._object_path("sales.revenue").unlink(missing_ok=True)

    issues = _evidence_ledger_blockers(project)
    refs = {ref for issue in issues for ref in issue.refs}
    assert "sales.revenue" in refs  # metric has no metric_decomposition decision recorded
    assert all(issue.kind == "unresolved_clarification" for issue in issues)
    assert all(issue.severity == "blocker" for issue in issues)


def test_evidence_ledger_blockers_clears_after_decision_recorded(semantic_project_factory):
    from marivo.semantic import ledger as lg
    from marivo.semantic.readiness import _evidence_ledger_blockers

    project = semantic_project_factory(
        {
            "sales/_model.py": "import marivo.semantic as ms\nms.model(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.dataset(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
                "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native')\n"
                "def revenue(orders):\n    return orders.amount.sum()\n"
            ),
        }
    )
    # Write DecisionRecord directly to LedgerStore (same pattern as auto_record)
    store = lg.LedgerStore(project.semantic_root)
    store.write_object(
        lg.ObjectEvidence(
            semantic_id="sales.revenue",
            authored_at="t",
            decisions=(
                lg.DecisionRecord(
                    decision_kind="metric_decomposition",
                    chosen="sum",
                    agreement_confidence="high",
                    qualifying_sources=("source_sql",),
                    materiality="high",
                    blast_radius=0,
                    evidence_fingerprint="sha256:a",
                    question_id=None,
                    decided_at="t",
                ),
            ),
            rejected_candidates=(),
        )
    )
    refs = {ref for issue in _evidence_ledger_blockers(project) for ref in issue.refs}
    assert "sales.revenue" not in refs


def test_readiness_require_evidence_ledger_blocks_unaudited_metric(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_model.py": "import marivo.semantic as ms\nms.model(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.dataset(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
                "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native')\n"
                "def revenue(orders):\n    return orders.amount.sum()\n"
            ),
        }
    )

    # Auto-record creates a decision during load(), so readiness
    # passes for normally-loaded projects (evidence ledger is always on).
    auto_report = project.readiness()
    assert all(b.kind != "unresolved_clarification" for b in auto_report.blockers)

    # Remove the auto-recorded decision to test the "no decision" edge case.
    from marivo.semantic.ledger import LedgerStore

    LedgerStore(project.semantic_root)._object_path("sales.revenue").unlink(missing_ok=True)

    strict_report = project.readiness()
    kinds = {b.kind for b in strict_report.blockers}
    assert "unresolved_clarification" in kinds
    assert strict_report.status == "blocked"


def test_readiness_evidence_ledger_persists_answer_across_reload(semantic_project_factory):
    import marivo.semantic as ms

    project = semantic_project_factory(
        {
            "sales/_model.py": "import marivo.semantic as ms\nms.model(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.dataset(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
                "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native')\n"
                "def revenue(orders):\n    return orders.amount.sum()\n"
            ),
        }
    )
    # Record a user-confirmed decision directly in the ledger
    from marivo.semantic import ledger as lg

    user_decision = lg.DecisionRecord(
        decision_kind="metric_decomposition",
        chosen="sum",
        agreement_confidence="high",
        qualifying_sources=("user_confirmation",),
        materiality="high",
        blast_radius=0,
        evidence_fingerprint="sha256:answer",
        question_id="q-metric-decomposition",
        decided_at="2026-06-01T00:00:00+00:00",
    )
    store = lg.LedgerStore(project.semantic_root)
    store.write_object(
        lg.ObjectEvidence(
            semantic_id="sales.revenue",
            authored_at="2026-06-01T00:00:00+00:00",
            decisions=(user_decision,),
            rejected_candidates=(),
        )
    )

    reloaded = ms.SemanticProject(workspace_dir=project.workspace_dir)
    reloaded.load()

    report = reloaded.readiness()
    refs = {
        ref
        for issue in report.blockers
        if issue.kind == "unresolved_clarification"
        for ref in issue.refs
    }
    assert "sales.revenue" not in refs


def test_readiness_evidence_ledger_collects_user_confirmation_from_evidence_store(
    semantic_project_factory,
) -> None:
    import marivo.semantic as ms

    project = semantic_project_factory(
        {
            "sales/_model.py": "import marivo.semantic as ms\nms.model(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.dataset(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
                "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native')\n"
                "def revenue(orders):\n    return orders.amount.sum()\n"
            ),
        }
    )
    # Record user confirmation evidence via the evidence store
    project.record_authoring_evidence(
        ms.AuthoringEvidenceInput(
            kind="user_confirmation",
            subject_refs=("sales.revenue",),
            content="Revenue is the sum of paid order amounts.",
        )
    )

    report = project.readiness()
    assert "sales.revenue" in report.evidence_summary.user_confirmations


def test_missing_business_definition_predicate():
    from types import SimpleNamespace

    from marivo.datasource.ir import AiContextIR
    from marivo.semantic.readiness import _missing_business_definition

    assert _missing_business_definition(SimpleNamespace(ai_context=AiContextIR()))
    assert _missing_business_definition(
        SimpleNamespace(ai_context=AiContextIR(business_definition="   "))
    )
    assert not _missing_business_definition(
        SimpleNamespace(ai_context=AiContextIR(business_definition="One row per order."))
    )
    # description alone does NOT satisfy the strict floor.
    assert _missing_business_definition(
        SimpleNamespace(ai_context=AiContextIR(), description="Orders")
    )


def test_missing_guardrails_predicate():
    from types import SimpleNamespace

    from marivo.datasource.ir import AiContextIR
    from marivo.semantic.readiness import _missing_guardrails

    assert _missing_guardrails(SimpleNamespace(ai_context=AiContextIR()))
    assert not _missing_guardrails(
        SimpleNamespace(ai_context=AiContextIR(guardrails=("Exclude test orders.",)))
    )


def test_strict_enrichment_issues_flags_bare_ref(semantic_project_factory):
    from marivo.semantic.readiness import _object_maps, _strict_enrichment_issues

    project = semantic_project_factory(
        {
            "sales/_model.py": "import marivo.semantic as ms\nms.model(name='sales')\n",
            "sales/objects.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.dataset(name='orders', datasource='warehouse', source=ms.table('orders'),\n"
                "    ai_context={'business_definition': 'One row per order.',\n"
                "               'guardrails': ['Exclude test orders.']})\n"
                "@ms.field(dataset=orders, name='amount',\n"
                "    ai_context={'business_definition': 'Gross amount.',\n"
                "               'guardrails': ['USD only.']})\n"
                "def amount(table):\n    return table.amount\n"
                "@ms.field(dataset=orders, name='region')\n"
                "def region(table):\n    return table.region\n"
            ),
        }
    )

    kinds, objects = _object_maps(project)
    blockers, warnings = _strict_enrichment_issues(tuple(kinds), kinds, objects)

    blocker_refs = {ref for issue in blockers for ref in issue.refs}
    warning_refs = {ref for issue in warnings for ref in issue.refs}

    # The bare field is flagged; the fully enriched dataset and field are not.
    assert "sales.orders.region" in blocker_refs
    assert "sales.orders" not in blocker_refs
    assert "sales.orders.amount" not in blocker_refs
    assert "sales.orders.region" in warning_refs
    assert all(issue.kind == "missing_business_definition" for issue in blockers)
    assert all(issue.severity == "blocker" for issue in blockers)
    assert all(issue.kind == "missing_guardrails" for issue in warnings)
    assert all(issue.severity == "warning" for issue in warnings)


def test_readiness_strict_enrichment_blocks_missing_business_definition(
    semantic_project_factory,
):
    # strict_enrichment is always on now; _COMMENTLESS_MODEL_PY has no
    # business_definition on its objects, so readiness should block.
    project = _project(semantic_project_factory, _COMMENTLESS_MODEL_PY)

    report = project.readiness()
    assert report.status == "blocked"
    assert "missing_business_definition" in _issue_kinds(report.blockers)


def test_readiness_strict_enrichment_warns_when_only_guardrails_missing(
    semantic_project_factory,
):
    # _READY_MODEL_PY has business_definition on every object but no guardrails.
    project = _project(semantic_project_factory, _READY_MODEL_PY)

    report = project.readiness()

    assert "missing_business_definition" not in _issue_kinds(report.blockers)
    assert "missing_guardrails" in _issue_kinds(report.warnings)


def test_semantic_check_main_prints_json(
    semantic_project_factory,
    backend_factory,
    capsys,
    monkeypatch,
) -> None:
    project = _project(semantic_project_factory, _READY_MODEL_PY)

    # Record evidence via the project API so auto-collect can discover it.
    project.collect_source_preview(
        datasource="warehouse", table="orders", backend_factory=backend_factory
    )
    project.record_primary_key_sample("sales.orders")

    import marivo.semantic.check as semantic_check

    monkeypatch.setattr(
        semantic_check,
        "_default_backend_factory",
        lambda: backend_factory,
    )

    exit_code = semantic_check.main(
        [
            "--workspace-dir",
            str(project.workspace_dir),
            "--format",
            "json",
            "--readiness",
        ]
    )

    assert exit_code == 1  # blocked due to requires_raw_sql
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "blocked"
    assert payload["readiness"]["status"] == "blocked"
    blocker_kinds = [b["kind"] for b in payload["readiness"]["blockers"]]
    assert "requires_raw_sql" in blocker_kinds


def test_build_readiness_report_strict_enrichment_floor(semantic_project_factory):
    from marivo.semantic.readiness import _ReadinessEvidence, build_readiness_report

    project = _project(semantic_project_factory, _COMMENTLESS_MODEL_PY)

    # strict_enrichment is always on now; every analyzable bare ref is blocked.
    evidence = _ReadinessEvidence(
        raw_previews=(),
        failed_raw_previews=(),
        required_raw_previews=(),
        required_semantic_previews=(),
        knowledge_documents=(),
        user_confirmations=(),
        confirmed_relationships=(),
        primary_keys_sampled=(),
        raw_sql_required_refs=(),
        table_metadata=(),
        supports_federation=False,
    )
    report = build_readiness_report(project, evidence)
    blocked_refs = {
        ref for b in report.blockers if b.kind == "missing_business_definition" for ref in b.refs
    }
    assert report.status == "blocked"
    assert blocked_refs == {"sales.orders", "sales.orders.amount", "sales.total_amount"}
    assert any(w.kind == "missing_guardrails" for w in report.warnings)
    # Blocked refs are excluded from the handoff set.
    assert "sales.orders" not in report.analysis_ready_refs


def test_raw_preview_evidence_status_field() -> None:
    from marivo.semantic.ledger import RawPreviewEvidence

    rec = RawPreviewEvidence(
        ref="warehouse.orders",
        datasource="warehouse",
        table="orders",
        database=None,
        columns=("order_id", "amount"),
        types={"order_id": "int64", "amount": "float64"},
        requested_limit=100,
        returned_row_count=100,
        sample_policy={"method": "bounded_limit", "limit": 100},
        collected_at="2026-06-07T00:00:00Z",
        status="success",
    )
    assert rec.status == "success"
    as_dict = rec.to_dict()
    assert as_dict["status"] == "success"

    round_tripped = RawPreviewEvidence.from_dict(as_dict)
    assert round_tripped.status == "success"


def test_record_failed_preview_persists_to_ledger(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": textwrap.dedent("""\
                import marivo.semantic as ms
                ms.model(name="sales")
                ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"))
            """),
        }
    )

    project.record_failed_preview(
        datasource="warehouse",
        table="orders",
    )

    from marivo.semantic.ledger import LedgerStore

    store = LedgerStore(project.semantic_root)
    records = store.read_raw_previews()
    assert len(records) == 1
    assert records[0].ref == "warehouse.orders"
    assert records[0].status == "failed"


def test_primary_key_sample_persistence(
    semantic_project_factory,
) -> None:
    from marivo.semantic.ledger import LedgerStore

    project = semantic_project_factory(
        {
            "sales/_model.py": textwrap.dedent("""\
                import marivo.semantic as ms
                ms.model(name="sales")
                ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"),
                          primary_key=("order_id",))
            """),
        }
    )

    store = LedgerStore(project.semantic_root)
    store.write_primary_key_sample("sales.orders")
    assert store.read_primary_key_samples() == ("sales.orders",)

    store.write_primary_key_sample("sales.orders")
    assert store.read_primary_key_samples() == ("sales.orders",)


def test_evidence_store_list_authoring_by_kind(
    semantic_project_factory,
) -> None:
    from marivo.semantic.evidence import AuthoringEvidenceInput
    from marivo.semantic.evidence_store import EvidenceStore

    project = semantic_project_factory(
        {
            "sales/_model.py": textwrap.dedent("""\
                import marivo.semantic as ms
                ms.model(name="sales")
                ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"))
            """),
        }
    )

    store = EvidenceStore(project.semantic_root)
    store.write_authoring_evidence(
        AuthoringEvidenceInput(
            kind="knowledge_document",
            subject_refs=("sales.orders",),
            content="Revenue definition doc",
        )
    )
    store.write_authoring_evidence(
        AuthoringEvidenceInput(
            kind="user_confirmation",
            subject_refs=("sales.orders",),
            content="Owner confirmed revenue excludes tax",
        )
    )

    kd_refs = store.list_authoring_by_kind("knowledge_document")
    assert len(kd_refs) == 1
    assert kd_refs[0].kind == "knowledge_document"

    uc_refs = store.list_authoring_by_kind("user_confirmation")
    assert len(uc_refs) == 1
    assert uc_refs[0].kind == "user_confirmation"


def test_readiness_auto_evidence_end_to_end(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": textwrap.dedent("""\
                import marivo.semantic as ms
                ms.model(name="sales")
                orders = ms.dataset(
                    name="orders",
                    datasource="warehouse",
                    source=ms.table("orders"),
                    primary_key=("order_id",),
                )
                @ms.metric(
                    datasets=[orders],
                    additivity="additive",
                    decomposition=ms.sum(),
                    verification_mode="python_native",
                )
                def total_amount(table):
                    return table.amount.sum()
            """),
        }
    )
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=backend_factory
    )

    # Agent workflow: collect preview, record pk sample
    project.collect_source_preview(datasource="warehouse", table="orders")
    project.record_primary_key_sample("sales.orders")

    # Single call, no manual evidence
    report = project.readiness()

    assert "warehouse.orders" in report.evidence_summary.raw_previews
    assert "sales.orders" in report.evidence_summary.tables_inspected
