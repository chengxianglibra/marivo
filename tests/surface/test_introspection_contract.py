"""Cross-surface constraint ownership and structured error contracts."""

from __future__ import annotations

import pytest

from marivo._help.model import MarivoHelpTargetError
from marivo.semantic.constraints import CONSTRAINTS as SEMANTIC_CONSTRAINTS
from tests.shared_fixtures import rendered_help
from tests.support.paths import PROJECT_ROOT

REPO_ROOT = PROJECT_ROOT


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


def test_datasource_help_does_not_resolve_private_symbols() -> None:
    with pytest.raises(MarivoHelpTargetError):
        rendered_help("_build_ai_context", owner="datasource")


def test_datasource_constraint_defaults_use_error_kind_only() -> None:
    from marivo.datasource.constraints import default_constraint_for_error_kind

    constraint = default_constraint_for_error_kind("DatasourceLoad")

    assert constraint is not None
    assert constraint.id == "datasource_file_loadable"


def test_shared_catalog_hint_lookup_supports_semantic() -> None:
    from marivo.semantic.constraints import default_hint_for_error_kind as semantic_hint

    assert semantic_hint("invalid_composition")


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
