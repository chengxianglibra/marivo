"""Semantic result facts and lifecycle-removal contracts."""

from __future__ import annotations

import inspect

import marivo.semantic as ms
from marivo._authoring.model import AuthoringRepair
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic import preview_checks
from marivo.semantic.catalog import CatalogEntry, SemanticCatalog
from marivo.semantic.readiness import ReadinessIssue, ReadinessReport


def test_readiness_issue_has_typed_repair_without_suggested_action() -> None:
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
    assert "suggested_action" not in inspect.getsource(ReadinessIssue)


def test_preview_evidence_requirement_keeps_typed_repair_for_milestone2() -> None:
    source = inspect.getsource(preview_checks)
    assert "suggested_action" not in source
    assert "PreviewEvidenceRequirement" in source


def test_milestone1_removes_generic_lifecycle_contracts_and_verify() -> None:
    assert not hasattr(CatalogEntry, "contract")
    assert not hasattr(SemanticCatalog, "contract")
    assert not hasattr(SemanticCatalog, "verify")
    assert not hasattr(ReadinessReport, "contract")
    assert "VerifyResult" not in ms.__all__
