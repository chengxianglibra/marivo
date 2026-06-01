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

    @ms.dataset(
        datasource="warehouse",
        primary_key=["order_id"],
        description="Orders table",
        ai_context={"business_definition": "One row per paid order."},
    )
    def orders(backend):
        return backend.table("orders")

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
            raw_previews=("sales.orders",),
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
            python_native_metrics=(),
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


_UNVERIFIED_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic as ms

    @ms.dataset(datasource="warehouse", description="Orders")
    def orders(backend):
        return backend.table("orders")

    @ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), description="Total amount")
    def total_amount(table):
        return table.amount.sum()
""")


_DRIFTED_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic as ms

    @ms.dataset(datasource="warehouse", description="Orders")
    def orders(backend):
        return backend.table("orders")

    @ms.metric(
        datasets=[orders],
        additivity="additive",
        decomposition=ms.sum(),
        source_sql="SELECT 999.0 AS total_amount",
        source_dialect="duckdb",
        description="Total amount",
    )
    def total_amount(table):
        return table.amount.sum()
""")


_PYTHON_NATIVE_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic as ms

    @ms.dataset(datasource="warehouse", description="Orders")
    def orders(backend):
        return backend.table("orders")

    @ms.metric(
        datasets=[orders],
        additivity="additive",
        decomposition=ms.sum(),
        declared_status="python_native",
        description="Total amount",
    )
    def total_amount(table):
        return table.amount.sum()
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
        raw_previews=("sales.orders",),
        confirmed_relationships=(),
        primary_keys_sampled=("sales.orders",),
        backend_factory=backend_factory,
    )

    assert report.status == "ready"
    assert report.blockers == ()
    assert report.warnings == ()
    assert "sales.orders" in report.analysis_ready_refs
    assert "sales.amount" in report.analysis_ready_refs
    assert "sales.created_at" in report.analysis_ready_refs
    assert "sales.total_amount" in report.analysis_ready_refs
    assert report.preview_summary.required_previews == (
        "sales.orders",
        "sales.amount",
        "sales.created_at",
        "sales.total_amount",
    )
    assert set(report.preview_summary.completed_previews) == {
        "sales.orders",
        "sales.amount",
        "sales.created_at",
        "sales.total_amount",
    }
    assert report.parity_summary.verified_metrics == ("sales.total_amount",)


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


def test_readiness_warns_for_python_native_metric(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = _project(semantic_project_factory, _PYTHON_NATIVE_MODEL_PY)

    report = project.readiness(
        strict_provenance=True,
        require_preview=False,
        backend_factory=backend_factory,
    )

    assert report.status == "ready_with_warnings"
    assert report.parity_summary.python_native_metrics == ("sales.total_amount",)
    assert "unverified_metric" not in _issue_kinds(report.blockers)
    assert any("python_native" in issue.message for issue in report.warnings)


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
        raw_previews=("sales.orders",),
        primary_keys_sampled=("sales.orders",),
        backend_factory=backend_factory,
    )

    assert payload["status"] == "ready"
    assert payload["readiness"]["status"] == "ready"
    assert payload["readiness"]["parity_summary"]["verified_metrics"] == ["sales.total_amount"]


_COMMENTLESS_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic as ms

    @ms.dataset(datasource="warehouse", primary_key=["order_id"])
    def orders(backend):
        return backend.table("orders")

    @ms.field(dataset=orders)
    def amount(table):
        return table.amount

    @ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), declared_status="python_native")
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

    assert report.status == "ready_with_warnings"
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


def test_unresolved_clarification_is_a_valid_issue_kind():
    from typing import get_args

    from marivo.semantic.readiness import ReadinessIssueKind

    assert "unresolved_clarification" in get_args(ReadinessIssueKind)


def test_evidence_ledger_blockers_flags_metric_without_decision(semantic_project_factory):
    from marivo.semantic.readiness import _evidence_ledger_blockers

    project = semantic_project_factory(
        {
            "sales/_model.py": "import marivo.semantic as ms\nms.model(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "@ms.dataset(name='orders', datasource='warehouse')\n"
                "def orders(backend):\n    return backend.table('orders')\n"
                "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='revenue')\n"
                "def revenue(orders):\n    return orders.amount.sum()\n"
            ),
        }
    )

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
                "@ms.dataset(name='orders', datasource='warehouse')\n"
                "def orders(backend):\n    return backend.table('orders')\n"
                "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='revenue')\n"
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
                "@ms.dataset(name='orders', datasource='warehouse')\n"
                "def orders(backend):\n    return backend.table('orders')\n"
                "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='revenue')\n"
                "def revenue(orders):\n    return orders.amount.sum()\n"
            ),
        }
    )

    # Default (flag off): no unresolved_clarification blockers.
    default_report = project.readiness(require_preview=False)
    assert all(b.kind != "unresolved_clarification" for b in default_report.blockers)

    # Flag on: the unaudited metric is fail-closed.
    strict_report = project.readiness(require_preview=False, require_evidence_ledger=True)
    kinds = {b.kind for b in strict_report.blockers}
    assert "unresolved_clarification" in kinds
    assert strict_report.status == "blocked"


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
            "sales.orders",
            "--primary-key-sampled",
            "sales.orders",
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "ready"
    assert payload["readiness"]["status"] == "ready"
