"""Tests for semantic readiness reports."""

from __future__ import annotations

import json
import textwrap

import ibis
import pytest

from marivo.analysis.datasources.metadata import ColumnMetadata, TableMetadata
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

    report = project.readiness(require_preview=False, strict_provenance=False)

    assert report.status == "ready_with_warnings"
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
    project.parity_check("sales.total_amount", backend_factory=backend_factory)

    report = project.readiness(
        strict_provenance=True,
        require_preview=True,
        raw_previews=("warehouse.orders",),
        confirmed_relationships=(),
        primary_keys_sampled=("sales.orders",),
        backend_factory=backend_factory,
    )

    assert report.status == "ready"
    assert report.blockers == ()
    assert report.warnings == ()
    assert "sales.orders" in report.analysis_ready_refs
    assert "sales.orders.amount" in report.analysis_ready_refs
    assert "sales.orders.created_at" in report.analysis_ready_refs
    assert "sales.total_amount" in report.analysis_ready_refs
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
    project.parity_check("sales.total_amount", backend_factory=backend_factory)
    execute_calls = 0
    original_execute = Table.execute

    def counting_execute(self, *args, **kwargs):
        nonlocal execute_calls
        execute_calls += 1
        return original_execute(self, *args, **kwargs)

    monkeypatch.setattr(Table, "execute", counting_execute)

    report = project.readiness(
        strict_provenance=True,
        require_preview=True,
        raw_previews=("warehouse.orders",),
        confirmed_relationships=(),
        primary_keys_sampled=("sales.orders",),
        backend_factory=backend_factory,
    )

    assert report.status == "ready"
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

    report = project.readiness(
        strict_provenance=True,
        require_preview=True,
        raw_previews=("warehouse.orders",),
        backend_factory=backend_factory,
    )

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
    project.parity_check("sales.total_amount", backend_factory=backend_factory)

    report = project.readiness(
        strict_provenance=True,
        require_preview=True,
        raw_previews=(),
        primary_keys_sampled=("sales.orders",),
        backend_factory=backend_factory,
    )

    assert report.status == "blocked"
    assert "missing_raw_preview" in _issue_kinds(report.blockers)
    assert "sales.orders" not in report.analysis_ready_refs


def test_readiness_uses_collected_source_preview_evidence(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _READY_MODEL_PY)
    project.parity_check("sales.total_amount", backend_factory=backend_factory)

    before = project.readiness(
        strict_provenance=True,
        require_preview=True,
        raw_previews=(),
        primary_keys_sampled=("sales.orders",),
        backend_factory=backend_factory,
    )
    project.collect_source_preview(
        datasource="warehouse",
        table="orders",
        backend_factory=backend_factory,
    )
    after = project.readiness(
        strict_provenance=True,
        require_preview=True,
        raw_previews=(),
        primary_keys_sampled=("sales.orders",),
        backend_factory=backend_factory,
    )

    assert before.status == "blocked"
    assert "missing_raw_preview" in _issue_kinds(before.blockers)
    assert after.status == "ready"
    assert "missing_raw_preview" not in _issue_kinds(after.blockers)
    assert "warehouse.orders" in after.evidence_summary.raw_previews


def test_readiness_uses_collected_physical_raw_preview_ref(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _READY_MODEL_PY)
    project.parity_check("sales.total_amount", backend_factory=backend_factory)

    project.collect_source_preview(
        datasource="warehouse",
        table="orders",
        backend_factory=backend_factory,
    )
    report = project.readiness(
        strict_provenance=True,
        require_preview=True,
        required_raw_previews=("warehouse.orders",),
        raw_previews=(),
        primary_keys_sampled=("sales.orders",),
        backend_factory=backend_factory,
    )

    assert report.status == "ready"
    assert "warehouse.orders" in report.evidence_summary.raw_previews


def test_readiness_failed_raw_preview_overrides_collected_evidence(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _READY_MODEL_PY)
    project.parity_check("sales.total_amount", backend_factory=backend_factory)
    project.collect_source_preview(
        datasource="warehouse",
        table="orders",
        backend_factory=backend_factory,
    )

    report = project.readiness(
        strict_provenance=True,
        require_preview=True,
        raw_previews=(),
        failed_raw_previews=("warehouse.orders",),
        primary_keys_sampled=("sales.orders",),
        backend_factory=backend_factory,
    )

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
    project.parity_check("sales.total_amount", backend_factory=backend_factory)
    project.collect_source_preview(
        datasource="warehouse",
        table="orders",
        backend_factory=backend_factory,
    )

    reloaded = SemanticProject(root=project.root_path)
    reloaded.load()
    reloaded.parity_check("sales.total_amount", backend_factory=backend_factory)
    report = reloaded.readiness(
        strict_provenance=True,
        require_preview=True,
        raw_previews=(),
        primary_keys_sampled=("sales.orders",),
        backend_factory=backend_factory,
    )

    assert reloaded.raw_preview_evidence() == ("warehouse.orders",)
    assert report.status == "ready"
    assert "missing_raw_preview" not in _issue_kinds(report.blockers)
    assert "warehouse.orders" in report.evidence_summary.raw_previews


def test_readiness_strict_blocks_unverified_metric(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _UNVERIFIED_MODEL_PY)

    report = project.readiness(
        strict_provenance=True,
        require_preview=False,
        backend_factory=backend_factory,
    )

    assert report.status == "blocked"
    assert report.parity_summary.unverified_metrics == ("sales.total_amount",)
    assert "unverified_metric" in _issue_kinds(report.blockers)


def test_readiness_nonstrict_warns_for_unverified_metric(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _UNVERIFIED_MODEL_PY)

    report = project.readiness(
        strict_provenance=False,
        require_preview=False,
        backend_factory=backend_factory,
    )

    assert report.status == "ready_with_warnings"
    assert report.blockers == ()
    assert "unverified_metric" in _issue_kinds(report.warnings)


def test_readiness_blocks_drifted_metric(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _DRIFTED_MODEL_PY)
    project.parity_check("sales.total_amount", backend_factory=backend_factory)

    report = project.readiness(
        strict_provenance=False,
        require_preview=False,
        backend_factory=backend_factory,
    )

    assert report.status == "blocked"
    assert report.parity_summary.drifted_metrics == ("sales.total_amount",)
    assert "parity_drifted" in _issue_kinds(report.blockers)


def test_readiness_treats_python_native_metric_as_verified(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _PYTHON_NATIVE_MODEL_PY)

    report = project.readiness(
        strict_provenance=True,
        require_preview=False,
        backend_factory=backend_factory,
    )

    assert report.status == "ready"
    assert report.parity_summary.verified_metrics == ("sales.total_amount",)
    assert "unverified_metric" not in _issue_kinds(report.blockers)
    assert not any("python_native" in issue.message for issue in report.warnings)


def test_readiness_treats_derived_python_native_component_as_verified(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _DERIVED_WITH_PYTHON_NATIVE_COMPONENT_MODEL_PY)

    report = project.readiness(
        strict_provenance=True,
        require_preview=False,
        backend_factory=backend_factory,
    )

    assert report.status == "ready"
    assert "sales.avg_amount" in report.parity_summary.verified_metrics
    assert "sales.total_amount" in report.parity_summary.verified_metrics
    assert "unverified_metric" not in _issue_kinds(report.blockers)


def test_semantic_check_run_check_returns_json_ready_report(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _READY_MODEL_PY)
    root = project.root
    project.parity_check("sales.total_amount", backend_factory=backend_factory)

    from marivo.semantic.check import run_check

    payload = run_check(
        root=root,
        readiness=True,
        format="json",
        strict_provenance=True,
        require_preview=True,
        raw_previews=("warehouse.orders",),
        primary_keys_sampled=("sales.orders",),
        backend_factory=backend_factory,
    )

    assert payload["status"] == "ready"
    assert payload["readiness"]["status"] == "ready"
    assert payload["readiness"]["parity_summary"]["verified_metrics"] == ["sales.total_amount"]


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


def test_readiness_require_comments_accepts_table_metadata(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _COMMENTLESS_MODEL_PY)
    metadata = TableMetadata(
        datasource="warehouse",
        table="orders",
        database=None,
        backend_type="duckdb",
        comment="One row per order.",
        columns=(
            ColumnMetadata(
                name="order_id",
                type="int64",
                nullable=False,
                comment="Unique order id.",
                ordinal_position=1,
            ),
            ColumnMetadata(
                name="amount",
                type="float64",
                nullable=True,
                comment="Gross order amount in USD.",
                ordinal_position=2,
            ),
        ),
        partitions=(),
        warnings=(),
    )

    report = project.readiness(
        strict_provenance=True,
        require_preview=False,
        require_comments=True,
        backend_factory=backend_factory,
        table_metadata=(metadata,),
        primary_keys_sampled=("sales.orders",),
    )

    assert report.status == "ready"
    assert not any(issue.kind == "missing_comments" for issue in report.blockers)
    assert "sales.orders" in report.evidence_summary.tables_inspected


def test_readiness_require_comments_blocks_when_metadata_lacks_comments(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _COMMENTLESS_MODEL_PY)
    metadata = TableMetadata(
        datasource="warehouse",
        table="orders",
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=(
            ColumnMetadata(
                name="order_id",
                type="int64",
                nullable=False,
                comment=None,
                ordinal_position=1,
            ),
            ColumnMetadata(
                name="amount",
                type="float64",
                nullable=True,
                comment=None,
                ordinal_position=2,
            ),
        ),
        partitions=(),
        warnings=(),
    )

    report = project.readiness(
        strict_provenance=True,
        require_preview=False,
        require_comments=True,
        backend_factory=backend_factory,
        table_metadata=(metadata,),
        primary_keys_sampled=("sales.orders",),
    )

    assert report.status == "blocked"
    assert any(issue.kind == "missing_comments" for issue in report.blockers)


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
    ds = project.registry().datasets["sales.orders"]
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

    report = build_readiness_report(
        project,
        require_preview=False,
        strict_provenance=False,
        refs=["sales.orders"],
        table_metadata=[view_md],
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
    base_report = build_readiness_report(
        project,
        require_preview=False,
        strict_provenance=False,
        refs=["sales.orders"],
        table_metadata=[base_md],
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
    ds = project.registry().datasets["sales.orders"]
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
    report = build_readiness_report(
        project,
        require_preview=False,
        strict_provenance=False,
        refs=["sales.orders"],
        table_metadata=[view_md],
    )
    assert any(i.kind == "derived_source_grain_unverified" for i in report.warnings)


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
    ds = project.registry().datasets["sales.orders"]
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

    report = build_readiness_report(
        project,
        require_preview=False,
        strict_provenance=False,
        refs=["sales.orders"],
        table_metadata=[view_md],
    )

    assert any(i.kind == "derived_source_grain_unverified" for i in report.warnings)


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
    ds = project.registry().datasets["sales.orders"]
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

    report = build_readiness_report(
        project,
        require_preview=False,
        strict_provenance=False,
        refs=["sales.orders"],
        table_metadata=[view_md],
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

    LedgerStore(project.root_path)._object_path("sales.revenue").unlink(missing_ok=True)

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
    project.record_decision(
        "sales.revenue",
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

    # Default (flag off): no unresolved_clarification blockers.
    default_report = project.readiness(require_preview=False)
    assert all(b.kind != "unresolved_clarification" for b in default_report.blockers)

    # Auto-record creates a decision during load(), so require_evidence_ledger
    # passes for normally-loaded projects.
    auto_report = project.readiness(require_preview=False, require_evidence_ledger=True)
    assert all(b.kind != "unresolved_clarification" for b in auto_report.blockers)

    # Remove the auto-recorded decision to test the "no decision" edge case.
    from marivo.semantic.ledger import LedgerStore

    LedgerStore(project.root_path)._object_path("sales.revenue").unlink(missing_ok=True)

    strict_report = project.readiness(require_preview=False, require_evidence_ledger=True)
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
    question = ms.OpenQuestion(
        id="q-metric-decomposition",
        subject_refs=("sales.revenue",),
        decision_kind="metric_decomposition",
        gated_by=None,
        candidates=(),
        materiality="high",
        blast_radius=0,
        agreement_confidence="low",
        default_if_unanswered=None,
        severity="blocker",
        blocker_reason="high_materiality_low_confidence",
    )

    project.answer(question, "sum", evidence_fingerprint="sha256:answer")
    reloaded = ms.SemanticProject(root=project.root_path)
    reloaded.load()

    report = reloaded.readiness(require_preview=False, require_evidence_ledger=True)
    refs = {
        ref
        for issue in report.blockers
        if issue.kind == "unresolved_clarification"
        for ref in issue.refs
    }
    assert "sales.revenue" not in refs


def test_readiness_evidence_ledger_blocks_confirmation_only(
    semantic_project_factory,
) -> None:
    from datetime import UTC, datetime

    import marivo.semantic as ms
    from marivo.semantic import ledger as lg

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
    store = lg.LedgerStore(project.root_path)
    store.append_confirmation(
        lg.ConfirmationRecord(
            ts=datetime.now(UTC).isoformat(),
            question_id="q-metric-decomposition",
            decision_kind="metric_decomposition",
            subject_refs=("sales.revenue",),
            answer="sum",
            evidence_fingerprint="sha256:legacy",
        )
    )
    reloaded = ms.SemanticProject(root=project.root_path)
    reloaded.load()

    # Simulate the new-contract "confirmation-only" state: confirmations are
    # append-only user logs, but readiness requires object-level decisions.
    store._object_path("sales.revenue").unlink(missing_ok=True)

    report = reloaded.readiness(require_preview=False, require_evidence_ledger=True)
    refs = {
        ref
        for issue in report.blockers
        if issue.kind == "unresolved_clarification"
        for ref in issue.refs
    }
    assert "sales.revenue" in refs
    assert report.status == "blocked"
    assert store.read_object("sales.revenue") is None


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
    project = _project(semantic_project_factory, _COMMENTLESS_MODEL_PY)

    default = project.readiness(require_preview=False)
    assert "missing_business_definition" not in _issue_kinds(default.blockers)

    strict = project.readiness(require_preview=False, strict_enrichment=True)
    assert strict.status == "blocked"
    assert "missing_business_definition" in _issue_kinds(strict.blockers)


def test_readiness_strict_enrichment_warns_when_only_guardrails_missing(
    semantic_project_factory,
):
    # _READY_MODEL_PY has business_definition on every object but no guardrails.
    project = _project(semantic_project_factory, _READY_MODEL_PY)

    report = project.readiness(
        strict_provenance=False,  # isolate the floor from the unverified-metric blocker
        require_preview=False,
        strict_enrichment=True,
    )

    assert "missing_business_definition" not in _issue_kinds(report.blockers)
    assert "missing_guardrails" in _issue_kinds(report.warnings)


def test_semantic_check_main_prints_json(
    semantic_project_factory,
    backend_factory,
    capsys,
    monkeypatch,
) -> None:
    project = _project(semantic_project_factory, _READY_MODEL_PY)
    project.parity_check("sales.total_amount", backend_factory=backend_factory)

    import marivo.semantic.check as semantic_check

    monkeypatch.setattr(
        semantic_check,
        "_default_backend_factory",
        lambda: backend_factory,
    )

    exit_code = semantic_check.main(
        [
            "--root",
            project.root,
            "--format",
            "json",
            "--readiness",
            "--raw-preview",
            "warehouse.orders",
            "--primary-key-sampled",
            "sales.orders",
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "ready"
    assert payload["readiness"]["status"] == "ready"


def test_build_readiness_report_strict_enrichment_floor(semantic_project_factory):
    from marivo.semantic.readiness import build_readiness_report

    project = _project(semantic_project_factory, _COMMENTLESS_MODEL_PY)

    # Flag off (default): no business_definition blocker.
    off = build_readiness_report(project, require_preview=False)
    assert all(b.kind != "missing_business_definition" for b in off.blockers)

    # Flag on: every analyzable bare ref is blocked.
    on = build_readiness_report(project, require_preview=False, strict_enrichment=True)
    blocked_refs = {
        ref for b in on.blockers if b.kind == "missing_business_definition" for ref in b.refs
    }
    assert on.status == "blocked"
    assert blocked_refs == {"sales.orders", "sales.orders.amount", "sales.total_amount"}
    assert any(w.kind == "missing_guardrails" for w in on.warnings)
    # Blocked refs are excluded from the handoff set.
    assert "sales.orders" not in on.analysis_ready_refs
