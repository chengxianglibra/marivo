"""Destructive closeout gates for the Phase 2 datasource live surface."""

from __future__ import annotations

import inspect

import marivo.datasource as md
from marivo.datasource import constraints, errors, evidence, inspection


def test_phase2_removes_legacy_datasource_guidance_contracts() -> None:
    """Datasource guidance has no compatibility bridge into Phase 1 or skills."""
    import marivo.datasource.help as help_module

    assert not hasattr(help_module, "_surface")
    for module in (inspection, evidence):
        source = inspect.getsource(module)
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
        "LiveCapability",
    }

    assert forbidden.isdisjoint(md.__all__)
