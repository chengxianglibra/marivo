"""Destructive closeout gates for the Phase 3 semantic live surface."""

from __future__ import annotations

import importlib.util
import inspect

import marivo.semantic as ms
from marivo.semantic import constraints, dtos, errors, preview_scope, readiness


def test_phase3_removes_authoring_question() -> None:
    assert not hasattr(ms, "AuthoringQuestion")
    assert "AuthoringQuestion" not in ms.__all__
    assert not hasattr(dtos, "AuthoringQuestion")


def test_phase3_removes_question_bearing_assessment_path() -> None:
    source = inspect.getsource(dtos)
    assert "questions" not in source
    assert "AuthoringQuestion" not in source


def test_phase3_removes_top_level_verify_and_readiness_wrappers() -> None:
    # verify_object had no submodule, so hasattr is sufficient.
    assert not hasattr(ms, "verify_object")
    # readiness the *function* is gone; the readiness.py submodule still
    # exists and Python registers it on the package, so verify it is a
    # module, not a callable wrapper.
    assert not callable(getattr(ms, "readiness", None))
    assert "verify_object" not in ms.__all__
    assert "readiness" not in ms.__all__


def test_phase3_removes_skill_paths_from_constraints() -> None:
    source = inspect.getsource(constraints)
    assert "marivo/skills/marivo-semantic" not in source
    assert "_SEMANTIC_WORKFLOW_REF" not in source
    assert "_SEMANTIC_AUTHOR_EXAMPLE" not in source
    assert "_EXAMPLE_BASE" not in source


def test_phase3_removes_suggested_action_from_readiness_and_preview() -> None:
    readiness_source = inspect.getsource(readiness)
    preview_source = inspect.getsource(preview_scope)
    assert "suggested_action" not in readiness_source
    assert "suggested_action" not in preview_source


def test_phase3_removes_old_help_surface_function() -> None:
    assert importlib.util.find_spec("marivo.semantic.help") is None
    assert not hasattr(ms, "help")
    assert not hasattr(ms, "help_text")


def test_phase3_does_not_publish_private_contract_types() -> None:
    forbidden = {
        "AuthoringStateRef",
        "AuthoringEffects",
        "AuthoringContract",
        "AuthoringRepair",
        "AuthoringCapability",
    }
    assert forbidden.isdisjoint(ms.__all__)


def test_phase3_semantic_error_has_repair_field() -> None:
    err = errors.SemanticError(kind="not_found", message="missing")
    assert hasattr(err, "repair")
    assert err.repair is None


def test_milestone1_removes_verify_and_lifecycle_contracts() -> None:
    from marivo.semantic.catalog import CatalogEntry, SemanticCatalog
    from marivo.semantic.readiness import ReadinessReport

    assert not hasattr(SemanticCatalog, "verify")
    assert not hasattr(CatalogEntry, "contract")
    assert not hasattr(SemanticCatalog, "contract")
    assert not hasattr(ReadinessReport, "contract")
    assert not hasattr(dtos, "VerifyResult")
