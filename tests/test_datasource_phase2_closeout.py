"""Destructive closeout gates for the Phase 2 datasource live surface."""

from __future__ import annotations

import importlib.util
import inspect

import marivo.datasource as md
from marivo.datasource import constraints, errors, inspection


def test_phase2_removes_legacy_datasource_guidance_contracts() -> None:
    """Datasource guidance has no compatibility bridge into Phase 1 or skills."""
    assert importlib.util.find_spec("marivo.datasource.help") is None
    assert not hasattr(md, "help")
    assert not hasattr(md, "help_text")
    source = inspect.getsource(inspection)
    assert "next_calls" not in source
    assert "next_safe_action" not in source
    assert "suggested_action" not in source

    error_source = inspect.getsource(errors)
    constraint_source = inspect.getsource(constraints)
    assert "details" not in error_source
    assert "DatasourceConfigError" not in error_source
    assert "marivo/skills/marivo-semantic" not in error_source
    assert "marivo/skills/marivo-semantic" not in constraint_source


def test_phase2_does_not_publish_private_contract_types() -> None:
    """Phase 2 keeps authoring and live handoff types private to datasource."""
    forbidden = {
        "AuthoringStateRef",
        "AuthoringEffects",
        "AuthoringContract",
        "AuthoringRepair",
        "AuthoringCapability",
    }

    assert forbidden.isdisjoint(md.__all__)


def test_milestone1_removes_semantic_discovery_projection_module() -> None:
    assert importlib.util.find_spec("marivo.datasource.evidence") is None
