"""Cross-surface tests for the agent-facing help() contract."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.analysis.constraints import CONSTRAINTS as ANALYSIS_CONSTRAINTS
from marivo.datasource.constraints import CONSTRAINTS as DATASOURCE_CONSTRAINTS
from marivo.semantic.constraints import CONSTRAINTS as SEMANTIC_CONSTRAINTS

REPO_ROOT = Path(__file__).resolve().parents[1]

ANALYSIS_HELP_ONLY_ENTRIES = {
    "observe",
    "compare",
    "decompose",
    "discover",
    "transform",
    "correlate",
    "forecast",
    "assess_quality",
    "hypothesis_test",
    "alignment",
    "calendar",
    "select",
}

SURFACES = [
    pytest.param("marivo.datasource", md, DATASOURCE_CONSTRAINTS, set(), id="datasource"),
    pytest.param(
        "marivo.semantic",
        ms,
        SEMANTIC_CONSTRAINTS,
        {"constraints", "decomposition"},
        id="semantic",
    ),
    pytest.param(
        "marivo.analysis",
        mv,
        ANALYSIS_CONSTRAINTS,
        ANALYSIS_HELP_ONLY_ENTRIES,
        id="analysis",
    ),
]

REQUIRED_BY_KIND = {
    "surface": {"schema_version", "surface", "kind", "symbol", "summary", "entries"},
    "callable": {
        "schema_version",
        "surface",
        "kind",
        "symbol",
        "summary",
        "signature",
        "constraints",
        "examples",
        "see_also",
    },
    "class": {
        "schema_version",
        "surface",
        "kind",
        "symbol",
        "summary",
        "signature",
        "constraints",
        "examples",
        "see_also",
    },
    "frame": {
        "schema_version",
        "surface",
        "kind",
        "symbol",
        "summary",
        "constraints",
        "examples",
        "see_also",
        "methods",
    },
    "module": {
        "schema_version",
        "surface",
        "kind",
        "symbol",
        "summary",
        "signature",
        "constraints",
        "examples",
        "see_also",
    },
    "topic": {
        "schema_version",
        "surface",
        "kind",
        "symbol",
        "summary",
        "constraints",
        "examples",
        "see_also",
        "content",
    },
    "unknown": {"schema_version", "surface", "kind", "symbol", "summary", "did_you_mean"},
}


def _json_size(data: dict[str, object]) -> int:
    return len(json.dumps(data, sort_keys=True, separators=(",", ":")))


def _json_help(module: Any, symbol: str | None = None) -> dict[str, Any]:
    data = module.help(symbol, format="json")
    assert isinstance(data, dict)
    return cast("dict[str, Any]", data)


def _help_text(module: Any, name: str) -> str:
    if hasattr(module, "help_text"):
        if module.__name__ in {"marivo.analysis", "marivo.datasource"}:
            assert "help_text" in module.__all__
        return cast("str", module.help_text(name))
    if module.__name__ != "marivo.semantic":
        raise AssertionError(f"{module.__name__} must expose public help_text")
    help_module = getattr(module.help, "__module__", "")
    if help_module:
        imported = __import__(help_module, fromlist=["help_text"])
        if isinstance(imported, ModuleType) and hasattr(imported, "help_text"):
            return cast("str", imported.help_text(name))
    raise AssertionError(f"{module.__name__} has no non-printing text help renderer")


def _looks_like_repo_path(value: str) -> bool:
    return value.endswith(".py") or value.endswith(".md")


def _normalize_repo_ref(value: str) -> str:
    path = value.split("#", 1)[0]
    base, sep, suffix = path.rpartition(":")
    if sep and suffix.isdigit():
        return base
    return path


@pytest.mark.parametrize(("surface_name", "module", "catalog", "extra_names"), SURFACES)
def test_top_level_listing_matches_public_surface(
    surface_name: str,
    module: Any,
    catalog: dict[Any, Any],
    extra_names: set[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = _json_help(module)

    assert capsys.readouterr().out != ""
    assert data["schema_version"] == "1"
    assert data["surface"] == surface_name
    assert data["kind"] == "surface"
    entry_names = {entry["name"] for entry in data["entries"]}
    assert entry_names == set(module.__all__) | extra_names
    assert _json_size(data) < 12_000


@pytest.mark.parametrize(("surface_name", "module", "catalog", "extra_names"), SURFACES)
def test_every_listed_name_resolves_to_descriptor(
    surface_name: str,
    module: Any,
    catalog: dict[Any, Any],
    extra_names: set[str],
) -> None:
    names = set(module.__all__) | extra_names
    for name in sorted(names):
        data = _json_help(module, name)
        assert data["schema_version"] == "1"
        assert data["surface"] == surface_name
        assert data["kind"] != "unknown", name
        assert REQUIRED_BY_KIND[data["kind"]] <= set(data), name
        assert _json_size(data) < 12_000, name


@pytest.mark.parametrize(("surface_name", "module", "catalog", "extra_names"), SURFACES)
def test_text_renders_for_every_listed_name(
    surface_name: str,
    module: Any,
    catalog: dict[Any, Any],
    extra_names: set[str],
) -> None:
    for name in sorted(set(module.__all__) | extra_names):
        result = _help_text(module, name)
        assert isinstance(result, str)
        assert result.strip()


@pytest.mark.parametrize(("surface_name", "module", "catalog", "extra_names"), SURFACES)
def test_constraint_paths_exist(
    surface_name: str,
    module: Any,
    catalog: dict[Any, Any],
    extra_names: set[str],
) -> None:
    for constraint in catalog.values():
        docs_ref = constraint.docs_ref
        if docs_ref is not None:
            assert (REPO_ROOT / _normalize_repo_ref(docs_ref)).exists(), (
                f"{surface_name} {constraint.id} docs_ref"
            )
        example = constraint.example
        if isinstance(example, str) and _looks_like_repo_path(example):
            assert (REPO_ROOT / _normalize_repo_ref(example)).exists(), (
                f"{surface_name} {constraint.id} example"
            )


@pytest.mark.parametrize(("surface_name", "module", "catalog", "extra_names"), SURFACES)
def test_l1_constraints_and_methods_are_summaries_only(
    surface_name: str,
    module: Any,
    catalog: dict[Any, Any],
    extra_names: set[str],
) -> None:
    for name in sorted(set(module.__all__) | extra_names):
        data = _json_help(module, name)
        for constraint in data.get("constraints", []):
            assert set(constraint) <= {"id", "title", "hint", "example"}, name
            assert "why" not in constraint
            assert "ast_spec" not in constraint
        for method in data.get("methods", []):
            assert set(method) == {"name", "summary"}, name
            assert "(" not in method["name"]


@pytest.mark.parametrize(("surface_name", "module", "catalog", "extra_names"), SURFACES)
def test_l2_constraint_drilldowns_resolve(
    surface_name: str,
    module: Any,
    catalog: dict[Any, Any],
    extra_names: set[str],
) -> None:
    for constraint in catalog.values():
        data = _json_help(module, constraint.id)
        assert data["kind"] == "topic"
        assert data["symbol"] == constraint.id
        assert data["content"]["id"] == constraint.id
        assert "why" in data["content"]


@pytest.mark.parametrize(("surface_name", "module", "catalog", "extra_names"), SURFACES)
def test_l2_method_drilldowns_resolve(
    surface_name: str,
    module: Any,
    catalog: dict[Any, Any],
    extra_names: set[str],
) -> None:
    checked = 0
    for name in sorted(set(module.__all__) | extra_names):
        class_data = _json_help(module, name)
        for method in class_data.get("methods", []):
            method_name = method["name"]
            data = _json_help(module, f"{name}.{method_name}")
            checked += 1
            assert data["kind"] == "callable", f"{surface_name} {name}.{method_name}"
            assert data["symbol"] == f"{name}.{method_name}"
            assert "signature" in data
    if surface_name != "marivo.datasource":
        assert checked > 0


def test_unknown_symbol_returns_descriptor_with_suggestion() -> None:
    data = _json_help(mv, "MetricFram")

    assert data["kind"] == "unknown"
    assert data["did_you_mean"][0] == "MetricFrame"


def test_no_inherited_or_module_docstring_leaks() -> None:
    assert _json_help(mv, "AlignmentPolicy").get("doc", "") != inspect.getdoc(object)

    original_doc = mv.load_frame.__doc__
    mv.load_frame.__doc__ = None
    try:
        data = _json_help(mv, "load_frame")
        assert data.get("doc", "") == ""
        assert "Marivo Python-native analysis runtime" not in data.get("doc", "")
    finally:
        mv.load_frame.__doc__ = original_doc


def test_semantic_project_descriptor_lists_agent_workflow_methods() -> None:
    result = _json_help(ms, "SemanticProject")

    assert result["kind"] == "class"
    methods = {entry["name"] for entry in cast("list[dict[str, Any]]", result["methods"])}
    assert {
        "load",
        "list_metrics",
        "bind_datasource_access",
        "inspect_source_context",
        "inspect_column_context",
        "inspect_authored_object",
        "readiness",
        "richness",
    } <= methods


def test_semantic_metric_descriptor_uses_l1_constraint_summaries() -> None:
    result = _json_help(ms, "metric")

    assert result["kind"] == "callable"
    assert result["symbol"] == "metric"
    assert "metric(" in cast("str", result["signature"])
    assert result["doc"]
    constraints = cast("list[dict[str, Any]]", result["constraints"])
    assert constraints
    for constraint in constraints:
        assert set(constraint) <= {"id", "title", "hint", "example"}


def test_datasource_spec_descriptor_lists_secret_env_constraint() -> None:
    result = _json_help(md, "DatasourceSpec")

    assert result["kind"] == "class"
    assert result["symbol"] == "DatasourceSpec"
    constraints = cast("list[dict[str, Any]]", result["constraints"])
    assert {constraint["id"] for constraint in constraints} >= {"datasource_secret_env_ref"}


def test_datasource_help_does_not_resolve_private_symbols() -> None:
    result = _json_help(md, "_build_ai_context")

    assert result["kind"] == "unknown"
    assert result["symbol"] == "_build_ai_context"


def test_datasource_constraint_defaults_use_error_details() -> None:
    from marivo.datasource.constraints import default_constraint_for_error

    backend_type = default_constraint_for_error(
        "DatasourceFieldInvalid",
        {"field": "backend_type", "reason": "backend_type is required"},
    )
    loader_context = default_constraint_for_error(
        "DatasourceFieldInvalid",
        {"field": "<context>", "reason": "outside loader"},
    )
    load_error = default_constraint_for_error("DatasourceLoad", {"path": "broken.py"})

    assert backend_type is not None
    assert backend_type.id == "datasource_backend_type_required"
    assert loader_context is not None
    assert loader_context.id == "datasource_loader_context"
    assert load_error is not None
    assert load_error.id == "datasource_file_loadable"


def test_datasource_text_help_prints_and_can_be_suppressed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = md.help("DatasourceSpec")

    captured = capsys.readouterr()
    assert result is None
    assert "marivo.datasource: DatasourceSpec" in captured.out

    suppressed = md.help("DatasourceSpec", print=False)
    captured = capsys.readouterr()
    assert isinstance(suppressed, str)
    assert "marivo.datasource: DatasourceSpec" in suppressed
    assert captured.out == ""


def test_datasource_help_json_print_false_suppresses_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = md.help("DatasourceSpec", format="json", print=False)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert isinstance(result, dict)


def test_datasource_help_invalid_format_raises_shared_error() -> None:
    with pytest.raises(ValueError, match="format must be 'text' or 'json'"):
        md.help(format="yaml")  # type: ignore[arg-type]


def test_shared_catalog_hint_lookup_supports_semantic() -> None:
    from marivo.semantic.constraints import default_hint_for_error_kind as semantic_hint

    assert semantic_hint("invalid_decomposition")


def test_analysis_error_can_receive_catalog_default_hint() -> None:
    from marivo.analysis.errors import FrameReadError

    err = FrameReadError(message="bad preview", details={"limit": 101})

    assert err.hint is not None
    assert "preview" in err.hint.lower()


def test_datasource_error_can_receive_catalog_default_hint() -> None:
    from marivo.datasource.errors import DatasourceSecretInPlaintextError

    err = DatasourceSecretInPlaintextError(
        message="secret",
        details={"datasource": "warehouse", "field": "password"},
    )

    assert err.hint is not None
    assert "*_env" in err.hint
