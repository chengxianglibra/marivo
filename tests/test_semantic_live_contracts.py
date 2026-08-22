"""Semantic result facts and lifecycle-removal contracts."""

from __future__ import annotations

import importlib.util
import inspect

import marivo.semantic as ms
from marivo._authoring.model import AuthoringRepair
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic import preview_scope
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


def test_milestone2_removes_persisted_preview_check_contract() -> None:
    source = inspect.getsource(preview_scope)
    assert "suggested_action" not in source
    assert "PreviewEvidenceRequirement" not in source
    assert importlib.util.find_spec("marivo.semantic.preview_checks") is None


def test_milestone1_removes_generic_lifecycle_contracts_and_verify() -> None:
    assert not hasattr(CatalogEntry, "contract")
    assert not hasattr(SemanticCatalog, "contract")
    assert not hasattr(SemanticCatalog, "verify")
    assert not hasattr(ReadinessReport, "contract")
    assert "VerifyResult" not in ms.__all__
