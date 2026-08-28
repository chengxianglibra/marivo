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
import re
from pathlib import Path

import pytest

import marivo
import marivo.semantic as ms
from marivo._help.model import MarivoHelpTargetError
from marivo.analysis.constraints import CONSTRAINTS as ANALYSIS_CONSTRAINTS
from marivo.semantic.constraints import CONSTRAINTS as SEMANTIC_CONSTRAINTS
from tests.shared_fixtures import rendered_help

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
        "forecast",
        "MetricFrame.metric",
        "events.match",
        "events.funnel",
        "events.time_to_event",
        "lifecycle.replay",
        "select_subjects",
        "transform",
        "catalog.readiness",
        "artifacts.reading",
        "artifacts.quality_projection",
        "runtime.artifacts",
        "runtime.sessions",
        "boundary.to_pandas",
        "alignment",
        "runtime_metric",
        "Session.source_bindings",
    }

    assert known_targets == canonical_targets

    # Every canonical target must resolve in the registry.
    for target in canonical_targets:
        try:
            REGISTRY.by_help_target(target)
        except KeyError:
            # Also try by id.
            try:
                REGISTRY.by_id(target)
            except KeyError:
                pytest.fail(f"canonical target {target!r} not in registry")


def test_no_inherited_or_module_docstring_leaks() -> None:
    text = rendered_help("trino", owner="datasource")
    assert "Signature:" in text
    assert "__init__" not in text


def test_semantic_catalog_help_lists_workflow_methods() -> None:
    text = rendered_help(ms.SemanticCatalog, owner="semantic")
    assert "SemanticCatalog" in text
    assert "require" in text
    assert "readiness" in text
    assert "verify" not in text


def test_semantic_load_help_mentions_entrypoint() -> None:
    text = rendered_help("load", owner="semantic")
    assert "ms.load" in text
    assert "Signature:" in text
    assert "SemanticCatalog" in text


def test_semantic_metric_help_contains_constructor_and_constraints() -> None:
    text = rendered_help("metric", owner="semantic")
    assert "ms.metric" in text
    assert "Signature:" in text
    assert "Constraints:" in text
    assert "metric_entities_required" in text
    assert "metric_additivity_required" in text


def test_datasource_trino_descriptor_lists_secret_env_constraint() -> None:
    assert "datasource_secret_env_ref" in rendered_help("trino", owner="datasource")


def test_datasource_help_does_not_resolve_private_symbols() -> None:
    with pytest.raises(MarivoHelpTargetError):
        rendered_help("_build_ai_context", owner="datasource")


def test_datasource_constraint_defaults_use_error_kind_only() -> None:
    from marivo.datasource.constraints import default_constraint_for_error_kind

    constraint = default_constraint_for_error_kind("DatasourceLoad")

    assert constraint is not None
    assert constraint.id == "datasource_file_loadable"


def test_public_help_prints_and_private_renderer_returns_string(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = marivo.help("datasource.trino")

    captured = capsys.readouterr()
    assert result is None
    assert captured.out.startswith("trino\n")

    text = rendered_help("trino", owner="datasource")
    captured = capsys.readouterr()
    assert text.startswith("trino\n")
    assert captured.out == ""


def test_public_help_rejects_format_and_print_kwargs() -> None:
    with pytest.raises(TypeError):
        marivo.help("datasource.trino", format="json")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        marivo.help("datasource.trino", print=False)  # type: ignore[call-arg]


def test_public_help_has_no_format_or_print_parameter() -> None:
    sig = inspect.signature(marivo.help)
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


def test_frame_meta_invalid_error_receives_catalog_default_hint() -> None:
    """FrameMetaInvalidError.hint must come from CONSTRAINTS (issue #66).

    The catalog lookup matches ``AnalysisError.kind`` (``"FrameMetaInvalid"``)
    against ``constraint.error_kind``. Without a matching constraint the hint
    falls back to None and ``str(e)`` omits the ``Hint:`` line.
    """
    from marivo.analysis.constraints import (
        CONSTRAINTS,
        ConstraintId,
        default_hint_for_error_kind,
    )
    from marivo.analysis.errors import FrameMetaInvalidError

    err = FrameMetaInvalidError(message="bad frame metadata")

    assert err.hint is not None
    assert "Hint:" in str(err)
    assert default_hint_for_error_kind("FrameMetaInvalid") is not None
    assert err.hint == default_hint_for_error_kind("FrameMetaInvalid")

    # Pin the hint's observable content: it must point at the Location line and
    # the on-disk meta.json, and must NOT regress to the previously-flawed
    # "frame.meta" phrasing (issue #66 review P3-1/P3-2).
    assert "Location" in err.hint
    assert "meta.json" in err.hint
    assert "frame.meta" not in err.hint

    # Pin the conditional scope of the meta.json step: it must be gated on the
    # Location naming a frame ref (7/26 construction sites carry no ref/artifact),
    # while the recovery action stays unconditional for all sites. Split on
    # sentence boundaries and assert each property on its own sentence -- this
    # pins the scope, not the exact wording or capitalization (fourth-round
    # review P3-1).
    sentences = re.split(r"(?<=\.)\s+", err.hint)
    recovery = next(s for s in sentences if "re-run" in s)
    assert (
        re.search(r"when\s+the\s+location\s+names\s+a\s+frame\s+ref", recovery, re.IGNORECASE)
        is None
    )
    meta_step = next(s for s in sentences if "meta.json" in s)
    assert (
        re.search(r"when\s+the\s+location\s+names\s+a\s+frame\s+ref", meta_step, re.IGNORECASE)
        is not None
    )

    # Pin applies_to as a closed set (not issubset) so drift or over-tightening
    # on the frame families covered by this constraint fails loudly.
    constraint = CONSTRAINTS[ConstraintId.FRAME_META_INVALID]
    assert constraint.applies_to == (
        "BaseFrame",
        "MetricFrame",
        "DeltaFrame",
        "CandidateSet",
        "EventFrame",
        "LifecycleFrame",
        "AttributionFrame",
    )


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
