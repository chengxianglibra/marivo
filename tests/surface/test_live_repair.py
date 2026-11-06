"""Typed authoring repair model contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marivo._authoring.model import AuthoringRepair
from marivo.introspection.live.model import LiveHelpTarget


def test_authoring_repair_minimal() -> None:
    repair = AuthoringRepair(
        kind="rescope",
        help_target=LiveHelpTarget(surface="semantic", canonical_id="preview"),
        action="Run preview with a complete explicit scope.",
    )
    assert repair.kind == "rescope"
    assert repair.snippet is None
    assert repair.candidates == ()
    assert repair.preserves_evidence is None


def test_authoring_repair_with_candidates_and_evidence_invalidation() -> None:
    repair = AuthoringRepair(
        kind="reacquire",
        help_target=LiveHelpTarget(surface="datasource", canonical_id="acquire"),
        action="Reacquire the snapshot.",
        candidates=("snapshot:orders",),
        preserves_evidence=False,
    )
    assert repair.preserves_evidence is False
    assert repair.candidates == ("snapshot:orders",)


def test_authoring_repair_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        AuthoringRepair(
            kind="guess",  # type: ignore[arg-type]  # Verify rejection of an unknown kind.
            help_target=LiveHelpTarget(surface="semantic"),
            action="just guess",
        )
