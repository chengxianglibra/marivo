"""Semantic readiness issues carry typed authoring repairs."""

from __future__ import annotations

from marivo._authoring.model import AuthoringRepair
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic.readiness import ReadinessIssue


def test_readiness_issue_has_typed_repair() -> None:
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
