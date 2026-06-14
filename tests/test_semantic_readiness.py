"""Tests for semantic readiness reports."""

from __future__ import annotations

import json
import textwrap

from marivo.semantic.readiness import (
    ReadinessInputSummary,
    ReadinessIssue,
    ReadinessReport,
)

_DOMAIN_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    ms.domain(name="sales", default=True)
""")

_READY_DOMAIN_PY = textwrap.dedent("""\
    import marivo.semantic as ms

    orders = ms.entity(
        name="orders",
        datasource="warehouse",
        source=ms.table("orders"),
        primary_key=["order_id"],
        description="Orders table",
        ai_context={"business_definition": "One row per paid order."},
    )

    @ms.dimension(
        entity=orders,
        description="Order amount",
        ai_context={"business_definition": "Gross order amount in USD."},
    )
    def amount(table):
        return table.amount

    @ms.time_dimension(
        entity=orders,
        data_type="timestamp",
        granularity="day",
        description="Order creation time",
        ai_context={"business_definition": "Timestamp when the order was created."},
    )
    def created_at(table):
        return table.created_at

    @ms.metric(
        entities=[orders],
        additivity="additive",
        decomposition=ms.sum(),
        verification_mode="python_native",
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
                kind="fragile_string_ref",
                severity="warning",
                refs=("sales.orders",),
                message="string ref used",
                suggested_action="Use stable object refs.",
            ),
        ),
        input_summary=ReadinessInputSummary(
            datasources=("warehouse",),
            refs=("sales.total_amount",),
            tables=("sales.orders",),
            decision_records=("sales.total_amount:metric_decomposition",),
        ),
        checked_at="2026-05-29T00:00:00Z",
    )

    payload = report.to_dict()

    assert payload["status"] == "ready_with_warnings"
    assert payload["warnings"][0]["kind"] == "fragile_string_ref"
    assert payload["input_summary"]["tables"] == ["sales.orders"]
    assert json.loads(json.dumps(payload))["analysis_ready_refs"] == ["sales.total_amount"]
    assert "preview_summary" not in payload
    assert "parity_summary" not in payload
    assert "richness_summary" not in payload


def test_readiness_report_target_fields_are_json_safe() -> None:
    report = ReadinessReport(
        status="ready_with_warnings",
        analysis_ready_refs=("sales.total_amount",),
        blockers=(),
        warnings=(
            ReadinessIssue(
                kind="fragile_string_ref",
                severity="warning",
                refs=("sales.orders",),
                message="string ref used",
                suggested_action="Use stable object refs.",
            ),
        ),
        input_summary=ReadinessInputSummary(
            datasources=("warehouse",),
            refs=("sales.total_amount",),
            tables=("sales.orders",),
            decision_records=("sales.total_amount:metric_decomposition",),
        ),
        checked_at="2026-05-29T00:00:00Z",
    )

    payload = report.to_dict()

    assert payload["input_summary"]["refs"] == ["sales.total_amount"]
    assert json.loads(json.dumps(payload))["analysis_ready_refs"] == ["sales.total_amount"]


def test_project_readiness_accepts_refs_argument(
    semantic_project_factory,
) -> None:
    project = _project(semantic_project_factory, _READY_DOMAIN_PY)

    report = project.readiness(refs=("sales.orders",))

    assert report.input_summary.refs == ("sales.orders",)


def test_readiness_blocks_unknown_requested_ref(semantic_project_factory) -> None:
    project = _project(semantic_project_factory, _READY_DOMAIN_PY)

    report = project.readiness(refs=("sales.missing_metric",))

    assert report.status == "blocked"
    assert report.analysis_ready_refs == ()
    assert "unknown_ref" in _issue_kinds(report.blockers)
    assert report.blockers[0].refs == ("sales.missing_metric",)


def test_readiness_maps_time_dimension_pushdown_advisory(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": textwrap.dedent("""\
                import marivo.semantic as ms

                ms.domain(name="sales")

                orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

                @ms.time_dimension(entity=orders, data_type="date", granularity="day")
                def order_date(table):
                    return table.dt.cast("date")
            """)
        }
    )

    report = project.readiness()

    assert any(issue.kind == "time_dimension_pushdown_advisory" for issue in report.warnings)


def test_readiness_warns_for_missing_business_definition(
    semantic_project_factory,
):
    project = _project(semantic_project_factory, _COMMENTLESS_DOMAIN_PY)

    report = project.readiness()
    assert report.status == "blocked"
    assert "missing_business_definition" in _issue_kinds(report.blockers)


def test_readiness_strict_enrichment_warns_when_only_guardrails_missing(
    semantic_project_factory,
):
    # _READY_DOMAIN_PY has business_definition on every object but no guardrails.
    project = _project(semantic_project_factory, _READY_DOMAIN_PY)

    report = project.readiness()

    assert "missing_business_definition" not in _issue_kinds(report.blockers)
    assert "missing_guardrails" in _issue_kinds(report.warnings)


def test_readiness_reports_authoring_abandoned_candidates(
    semantic_project_factory,
) -> None:
    from datetime import UTC, datetime

    from marivo.semantic.ledger import DecisionRecord, LedgerStore, RejectedCandidate

    project = semantic_project_factory(
        {"sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n"}
    )
    ledger = LedgerStore(project.state_root)
    decided_at = datetime.now(UTC).isoformat()
    ledger.record_decision(
        "sales.missing_metric",
        DecisionRecord(
            decision_kind="authoring_abandoned",
            chosen="abandoned",
            agreement_confidence="high",
            qualifying_sources=("structural",),
            materiality="low",
            blast_radius=0,
            evidence_fingerprint="",
            question_id=None,
            decided_at=decided_at,
        ),
    )
    ledger.write_rejected_candidate(
        RejectedCandidate(
            decision_kind="authoring_abandoned",
            candidate="sales.missing_metric",
            reason="No source evidence was available.",
            evidence_fingerprint="",
            rejected_at=decided_at,
        )
    )

    report = project.readiness(refs=("sales",))

    assert [candidate.candidate for candidate in report.abandoned] == ["sales.missing_metric"]


_COMMENTLESS_DOMAIN_PY = textwrap.dedent("""\
    import marivo.semantic as ms

    orders = ms.entity(name="orders", datasource="warehouse", primary_key=["order_id"], source=ms.table("orders"))

    @ms.dimension(entity=orders)
    def amount(table):
        return table.amount

    @ms.metric(
        entities=[orders],
        additivity='additive',
        decomposition=ms.sum(),
        verification_mode="python_native",
    )
    def total_amount(table):
        return table.amount.sum()
""")


def _project(semantic_project_factory, model_py: str):
    return semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": model_py,
        }
    )


def _issue_kinds(issues):
    return {issue.kind for issue in issues}


def test_readiness_requires_raw_sql_blocker(semantic_project_factory) -> None:
    domain_py = textwrap.dedent("""\
        import marivo.semantic as ms

        orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"),
                           description="Orders", ai_context={"business_definition": "One row per order."})

        @ms.metric(
            entities=[orders],
            additivity="additive",
            decomposition=ms.sum(),
            verification_mode="sql_parity",
            source_sql="SELECT SUM(amount) AS total_amount FROM orders",
            source_dialect="duckdb",
            description="Total amount",
            ai_context={"business_definition": "Sum of amount."},
        )
        def total_amount(table):
            return table.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": domain_py,
        }
    )

    report = project.readiness(refs=("sales.total_amount",))
    assert "requires_raw_sql" in _issue_kinds(report.blockers)


def test_readiness_cross_datasource_unfederated(semantic_project_factory) -> None:
    domain_py = textwrap.dedent("""\
        import marivo.semantic as ms

        orders = ms.entity(name="orders", datasource="warehouse_a", source=ms.table("orders"),
                           description="Orders A", ai_context={"business_definition": "Orders A."})
        items = ms.entity(name="items", datasource="warehouse_b", source=ms.table("items"),
                          description="Items B", ai_context={"business_definition": "Items B."})

        @ms.metric(
            entities=[orders, items],
            root_entity=orders,
            additivity="additive",
            decomposition=ms.sum(),
            verification_mode="python_native",
            description="Cross metric",
            ai_context={"business_definition": "Cross-datasource metric."},
        )
        def cross_metric(table):
            return table.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": domain_py,
        }
    )

    report = project.readiness(refs=("sales.cross_metric",))
    assert "cross_datasource_unfederated" in _issue_kinds(report.blockers)


def test_readiness_no_backend_access_required(semantic_project_factory) -> None:
    """Readiness is a pure in-memory check — no datasource connection needed."""
    project = _project(semantic_project_factory, _READY_DOMAIN_PY)

    # No _patch_connection_service, no backend setup — readiness should still work.
    report = project.readiness()

    assert report.status in {"ready", "ready_with_warnings", "blocked"}


# -- evidence ledger blockers ------------------------------------------------


def test_evidence_ledger_blockers_flags_metric_without_decision(semantic_project_factory):
    from marivo.semantic.readiness import _evidence_ledger_blockers

    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
                "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native')\n"
                "def revenue(orders):\n    return orders.amount.sum()\n"
            ),
        }
    )

    # Auto-record creates a decision during load(). Remove it to test
    # the underlying readiness check for "no decision" state.
    from marivo.semantic.ledger import LedgerStore

    LedgerStore(project.state_root)._object_path("sales.revenue").unlink(missing_ok=True)

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
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
                "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native')\n"
                "def revenue(orders):\n    return orders.amount.sum()\n"
            ),
        }
    )
    # Write DecisionRecord directly to LedgerStore (same pattern as auto_record)
    store = lg.LedgerStore(project.state_root)
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


def test_readiness_require_evidence_ledger_flags_missing_decision(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'),\n"
                "    ai_context={'business_definition': 'One row per order.'})\n"
                "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native',\n"
                "    ai_context={'business_definition': 'Sum of amount.'})\n"
                "def revenue(orders):\n    return orders.amount.sum()\n"
            ),
        }
    )

    # After load, no decisions exist in the ledger, so readiness
    # flags the missing metric_decomposition decision.
    bare_report = project.readiness()
    kinds = {b.kind for b in bare_report.blockers}
    assert "unresolved_clarification" in kinds

    # Record a decision manually. Readiness is a pure check — it
    # sees the new ledger entry and clears the blocker.
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
    store = lg.LedgerStore(project.state_root)
    store.write_object(
        lg.ObjectEvidence(
            semantic_id="sales.revenue",
            authored_at="2026-06-01T00:00:00+00:00",
            decisions=(user_decision,),
            rejected_candidates=(),
        )
    )

    resolved_report = project.readiness()
    assert all(b.kind != "unresolved_clarification" for b in resolved_report.blockers)


def test_readiness_evidence_ledger_persists_answer_across_reload(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
                "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native')\n"
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
    store = lg.LedgerStore(project.state_root)
    store.write_object(
        lg.ObjectEvidence(
            semantic_id="sales.revenue",
            authored_at="2026-06-01T00:00:00+00:00",
            decisions=(user_decision,),
            rejected_candidates=(),
        )
    )

    from marivo.semantic.reader import SemanticProject

    reloaded = SemanticProject(root=project.root)
    reloaded.load()

    report = reloaded.readiness()
    refs = {
        ref
        for issue in report.blockers
        if issue.kind == "unresolved_clarification"
        for ref in issue.refs
    }
    assert "sales.revenue" not in refs


# -- enrichment predicates ---------------------------------------------------


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
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/objects.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'),\n"
                "    ai_context={'business_definition': 'One row per order.',\n"
                "               'guardrails': ['Exclude test orders.']})\n"
                "@ms.dimension(entity=orders, name='amount',\n"
                "    ai_context={'business_definition': 'Gross amount.',\n"
                "               'guardrails': ['USD only.']})\n"
                "def amount(table):\n    return table.amount\n"
                "@ms.dimension(entity=orders, name='region')\n"
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


# -- issue kind validation ---------------------------------------------------


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


# -- check CLI ---------------------------------------------------------------


def test_semantic_check_run_check_returns_json_ready_report(
    semantic_project_factory,
) -> None:
    project = _project(semantic_project_factory, _READY_DOMAIN_PY)
    workspace_dir = project.workspace_dir

    from marivo.semantic.check import run_check

    payload = run_check(
        workspace_dir=workspace_dir,
        readiness=True,
        format="json",
    )

    # Structural readiness: python_native metric with all definitions
    assert payload["readiness"]["status"] in {"ready", "ready_with_warnings", "blocked"}
    assert "status" in payload["readiness"]


def test_semantic_check_main_prints_json(
    semantic_project_factory,
    capsys,
) -> None:
    project = _project(semantic_project_factory, _READY_DOMAIN_PY)

    import marivo.semantic.check as semantic_check

    exit_code = semantic_check.main(
        [
            "--workspace-dir",
            str(project.workspace_dir),
            "--format",
            "json",
            "--readiness",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert "readiness" in payload
    assert payload["readiness"]["status"] in {"ready", "ready_with_warnings", "blocked"}
