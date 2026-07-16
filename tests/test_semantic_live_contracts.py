"""Semantic live-surface object-near contract tests."""

from __future__ import annotations

from marivo._authoring.model import AuthoringRepair
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic.readiness import ReadinessIssue


def test_readiness_issue_has_repair_field() -> None:
    issue = ReadinessIssue(
        kind="unknown_ref",
        severity="blocker",
        refs=("metric.foo",),
        message="not found",
        repair=AuthoringRepair(
            kind="inspect",
            help_target=LiveHelpTarget(surface="semantic", canonical_id="load"),
            action="Browse catalog.metrics before referencing a metric.",
        ),
    )
    assert issue.repair is not None
    assert issue.repair.kind == "inspect"


def test_readiness_issue_has_no_suggested_action() -> None:
    import inspect as _inspect

    source = _inspect.getsource(ReadinessIssue)
    assert "suggested_action" not in source


def test_preview_evidence_requirement_has_repair_not_suggested_action() -> None:
    import inspect as _inspect

    from marivo.semantic import preview_checks

    source = _inspect.getsource(preview_checks)
    assert "suggested_action" not in source
    assert "PreviewEvidenceRequirement" in source


def test_verify_result_contract_exposes_preview_continuation() -> None:
    from marivo.semantic.dtos import VerifyResult

    result = VerifyResult(
        status="passed",
        ref="metric.sales.revenue",
        kind="metric",
        validation_level="static",
        runtime_checked=False,
        issues=(),
        warnings=(),
    )
    contract = result.contract()
    assert contract.subject_refs == ("metric.sales.revenue",)
    assert any(t.kind == "preview" and t.available for t in contract.transitions)


def test_catalog_object_contract_exposes_verify_preview_readiness(
    authoring_evidence_project: object,
) -> None:
    import marivo.semantic as ms

    catalog = ms.load()
    obj = catalog.get("metric.sales.revenue")
    contract = obj.contract()
    kinds = {t.kind for t in contract.transitions}
    assert "verify" in kinds
    assert "preview" in kinds
    assert "readiness" in kinds


def test_semantic_catalog_contract_exposes_browse_load(
    authoring_evidence_project: object,
) -> None:
    import marivo.semantic as ms

    catalog = ms.load()
    contract = catalog.contract()
    kinds = {t.kind for t in contract.transitions}
    assert "load" in kinds
    # catalog-level contract should not expose per-object transitions
    assert "verify" not in kinds
    assert "preview" not in kinds


def test_readiness_report_keeps_ready_refs_without_analysis_transition() -> None:
    from marivo.semantic.readiness import ReadinessInputSummary, ReadinessReport

    report = ReadinessReport(
        status="ready",
        analysis_ready_refs=("metric.sales.revenue",),
        blockers=(),
        warnings=(),
        input_summary=ReadinessInputSummary(
            datasources=("warehouse",),
            refs=("metric.sales.revenue",),
            tables=("orders",),
        ),
        checked_at="2026-07-14T00:00:00Z",
    )
    contract = report.contract()
    assert report.analysis_ready_refs == ("metric.sales.revenue",)
    assert contract.transitions == ()


def test_readiness_report_contract_does_not_invent_analysis_transition_when_blocked() -> None:
    from marivo.semantic.readiness import (
        ReadinessInputSummary,
        ReadinessIssue,
        ReadinessReport,
    )

    issue = ReadinessIssue(
        kind="unknown_ref",
        severity="blocker",
        refs=("metric.foo",),
        message="not found",
        repair=AuthoringRepair(
            kind="inspect",
            help_target=LiveHelpTarget(surface="semantic", canonical_id="load"),
            action="Browse catalog first.",
        ),
    )
    report = ReadinessReport(
        status="blocked",
        analysis_ready_refs=(),
        blockers=(issue,),
        warnings=(),
        input_summary=ReadinessInputSummary(datasources=(), refs=(), tables=()),
        checked_at="2026-07-14T00:00:00Z",
    )
    contract = report.contract()
    assert contract.transitions == ()
