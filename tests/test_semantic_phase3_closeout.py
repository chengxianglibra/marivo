"""Destructive closeout gates for the Phase 3 semantic live surface."""

from __future__ import annotations

import inspect

import marivo.semantic as ms
from marivo.semantic import constraints, dtos, errors, preview_checks, readiness
from marivo.semantic import help as help_mod


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
    preview_source = inspect.getsource(preview_checks)
    assert "suggested_action" not in readiness_source
    assert "suggested_action" not in preview_source


def test_phase3_removes_old_help_surface_function() -> None:
    assert not hasattr(help_mod, "_surface")
    assert not hasattr(help_mod, "_authoring_contracts")


def test_phase3_does_not_publish_private_contract_types() -> None:
    forbidden = {
        "AuthoringStateRef",
        "AuthoringEffects",
        "AuthoringContract",
        "AuthoringRepair",
        "LiveCapability",
    }
    assert forbidden.isdisjoint(ms.__all__)


def test_phase3_semantic_error_has_repair_field() -> None:
    err = errors.SemanticError(kind="not_found", message="missing")
    assert hasattr(err, "repair")
    assert err.repair is None


def test_phase3_catalog_object_and_results_have_contract() -> None:
    from marivo.semantic.catalog import CatalogObject, SemanticCatalog
    from marivo.semantic.dtos import VerifyResult
    from marivo.semantic.readiness import ReadinessReport

    assert callable(getattr(CatalogObject, "contract", None))
    assert callable(getattr(SemanticCatalog, "contract", None))
    assert callable(getattr(VerifyResult, "contract", None))
    assert callable(getattr(ReadinessReport, "contract", None))


def test_phase4_readiness_report_carries_masked_analysis_handoff() -> None:
    import marivo.semantic as ms
    from marivo.semantic.readiness import ReadinessReport

    # The field exists and defaults to None.
    fields = {f.name for f in __import__("dataclasses").fields(ReadinessReport)}
    assert "analysis_handoff" in fields
    # The handoff type is module-internal, not a public export.
    assert not hasattr(ms, "SemanticToAnalysisHandoff")
    assert "SemanticToAnalysisHandoff" not in ms.__all__
