"""Cross-surface tests for the agent-facing help() contract.

All three surfaces (analysis, datasource, semantic) now use
capability-registry-based live renderers. The old JSON ``Surface``
infrastructure has been removed. Live help invariants for the semantic
surface live in ``tests/test_semantic_help_contract.py``; analysis help
invariants live in ``tests/test_analysis_help.py``.

This file retains catalog-level, constraint-path, and datasource/analysis
regression tests that do not depend on the removed ``_surface`` function.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.analysis.constraints import CONSTRAINTS as ANALYSIS_CONSTRAINTS
from marivo.semantic.constraints import CONSTRAINTS as SEMANTIC_CONSTRAINTS

REPO_ROOT = Path(__file__).resolve().parents[1]


def _looks_like_repo_path(value: str) -> bool:
    return value.endswith(".py") or value.endswith(".md")


def _normalize_repo_ref(value: str) -> str:
    path = value.split("#", 1)[0]
    base, sep, suffix = path.rpartition(":")
    if sep and suffix.isdigit():
        return base
    return path


def test_constraint_paths_exist() -> None:
    for constraint in SEMANTIC_CONSTRAINTS.values():
        docs_ref = constraint.docs_ref
        if docs_ref is not None:
            assert (REPO_ROOT / _normalize_repo_ref(docs_ref)).exists(), (
                f"semantic {constraint.id} docs_ref"
            )
        example = constraint.example
        if isinstance(example, str) and _looks_like_repo_path(example):
            assert (REPO_ROOT / _normalize_repo_ref(example)).exists(), (
                f"semantic {constraint.id} example"
            )


def test_analysis_constraint_help_targets_are_canonical() -> None:
    """Every analysis constraint's non-null help_target must resolve as a known
    canonical id or topic in the analysis help surface."""

    from marivo.analysis._capabilities.registry import REGISTRY

    known_targets: set[str] = set()
    for constraint in ANALYSIS_CONSTRAINTS.values():
        if constraint.help_target is not None:
            known_targets.add(constraint.help_target)

    # The known canonical targets that constraints may point to.
    canonical_targets = {
        "observe",
        "compare",
        "attribute",
        "discover",
        "correlate",
        "hypothesis_test",
        "forecast",
        "assess_quality",
        "events.match",
        "transform",
        "session",
        "datasources",
        "help",
        "artifacts",
        "recovery",
        "boundary.to_pandas",
        "alignment",
        "calendar",
        "runtime_metric",
    }

    for constraint in ANALYSIS_CONSTRAINTS.values():
        if constraint.help_target is not None:
            assert constraint.help_target in canonical_targets, (
                f"constraint {constraint.id} has non-canonical help_target "
                f"{constraint.help_target!r}"
            )

    # Every canonical target must resolve in the registry.
    for target in canonical_targets:
        if target in {"datasources", "alignment", "calendar"}:
            # These are legacy targets not in the new registry.
            continue
        try:
            REGISTRY.by_help_target(target)
        except KeyError:
            # Also try by id.
            try:
                REGISTRY.by_id(target)
            except KeyError:
                pytest.fail(f"canonical target {target!r} not in registry")


def test_no_inherited_or_module_docstring_leaks() -> None:
    text = md.help_text("trino")
    assert "Signature:" in text
    assert "__init__" not in text


def test_semantic_catalog_help_lists_workflow_methods() -> None:
    text = ms.help_text(ms.SemanticCatalog)
    assert "SemanticCatalog" in text
    assert "require" in text
    assert "readiness" in text
    assert "verify" in text


def test_semantic_load_help_mentions_entrypoint() -> None:
    text = ms.help_text("load")
    assert "ms.load" in text
    assert "Signature:" in text
    assert "SemanticCatalog" in text


def test_semantic_metric_help_contains_constructor_and_constraints() -> None:
    text = ms.help_text("metric")
    assert "ms.metric" in text
    assert "Signature:" in text
    assert "Constraints:" in text
    assert "metric_entities_required" in text
    assert "metric_additivity_required" in text


def test_datasource_trino_descriptor_lists_secret_env_constraint() -> None:
    assert "datasource_secret_env_ref" in md.help_text("trino")


def test_datasource_help_does_not_resolve_private_symbols() -> None:
    from marivo.datasource.errors import DatasourceHelpTargetError

    with pytest.raises(DatasourceHelpTargetError):
        md.help_text("_build_ai_context")


def test_datasource_constraint_defaults_use_error_kind_only() -> None:
    from marivo.datasource.constraints import default_constraint_for_error_kind

    constraint = default_constraint_for_error_kind("DatasourceLoad")

    assert constraint is not None
    assert constraint.id == "datasource_file_loadable"


def test_datasource_text_help_prints_and_help_text_returns_string(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = md.help("trino")

    captured = capsys.readouterr()
    assert result is None
    assert captured.out.startswith("trino\n")

    text = md.help_text("trino")
    captured = capsys.readouterr()
    assert text.startswith("trino\n")
    assert captured.out == ""


def test_datasource_help_rejects_format_and_print_kwargs() -> None:
    with pytest.raises(TypeError):
        md.help("trino", format="json")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        md.help("trino", print=False)  # type: ignore[call-arg]


def test_datasource_help_has_no_format_or_print_parameter() -> None:
    sig = inspect.signature(md.help)
    assert "format" not in sig.parameters
    assert "print" not in sig.parameters


def test_shared_catalog_hint_lookup_supports_semantic() -> None:
    from marivo.semantic.constraints import default_hint_for_error_kind as semantic_hint

    assert semantic_hint("invalid_composition")


def test_analysis_error_can_receive_catalog_default_hint() -> None:
    from marivo.analysis.errors import FrameReadError

    err = FrameReadError(message="bad read")

    assert err.hint is not None
    assert "show()" in err.hint.lower()


def test_datasource_error_requires_typed_repair() -> None:
    from marivo.datasource.errors import DatasourceSecretInPlaintextError, repair

    err = DatasourceSecretInPlaintextError(
        message="secret",
        expected="an environment-variable reference",
        received="password",
        location="models/datasources/",
        repair=repair(kind="environment", canonical_id="trino", action="Use password_env."),
    )

    assert err.repair is not None
    assert not hasattr(err, "hint")


def test_analysis_constraints_do_not_reference_deleted_skill_attachments() -> None:
    """No analysis constraint's example or docs_ref may point to the deleted
    marivo-analysis references tree."""
    deleted_prefix = "marivo/skills/marivo-analysis" + "/references"
    for constraint in ANALYSIS_CONSTRAINTS.values():
        if constraint.example is not None:
            assert deleted_prefix not in constraint.example, (
                f"constraint {constraint.id} example references deleted path"
            )
        if constraint.docs_ref is not None:
            assert deleted_prefix not in constraint.docs_ref, (
                f"constraint {constraint.id} docs_ref references deleted path"
            )
