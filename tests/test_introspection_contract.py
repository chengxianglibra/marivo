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
    "agent_surface",
    "observe",
    "compare",
    "attribute",
    "discover",
    "transform",
    "correlate",
    "forecast",
    "assess_quality",
    "hypothesis_test",
    "derive_metric_frame",
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
        {"constraints", "additivity", "composition"},
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
    "type-alias": {
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
}


def _json_size(data: dict[str, object]) -> int:
    return len(json.dumps(data, sort_keys=True, separators=(",", ":")))


def _json_help(module: Any, symbol: str | None = None) -> dict[str, Any]:
    """Return JSON descriptor dict for a symbol using internal render."""
    import json as json_mod

    from marivo.introspection.surface import render as surface_render

    # Each help module exposes _surface() and uses Surface.render internally.
    help_mod_name = getattr(module.help, "__module__", "")
    if help_mod_name:
        help_mod = __import__(help_mod_name, fromlist=["_surface"])
        surface = help_mod._surface()
    else:
        raise AssertionError(f"Cannot locate help module for {module.__name__}")
    data = surface_render(surface, symbol, "json")
    assert isinstance(data, dict)
    # Print the JSON to stdout so capsys-based tests that check stdout still pass.
    print(json_mod.dumps(data, indent=2, sort_keys=True))
    return cast("dict[str, Any]", data)


def _hidden_names_for(module: Any) -> frozenset[str]:
    """Return names hidden from the top-level help index for a surface."""
    help_mod_name = getattr(module.help, "__module__", "")
    if help_mod_name:
        help_mod = __import__(help_mod_name, fromlist=["_surface"])
        surface = help_mod._surface()
        return surface.hidden_names
    return frozenset()


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
    folded_names = {name for fam in data.get("families", []) for name in fam["members"]}
    assert entry_names.isdisjoint(folded_names)
    assert entry_names | folded_names == (set(module.__all__) | extra_names) - _hidden_names_for(
        module
    )
    assert _json_size(data) < 12_000


@pytest.mark.parametrize(("surface_name", "module", "catalog", "extra_names"), SURFACES)
def test_every_listed_name_resolves_to_descriptor(
    surface_name: str,
    module: Any,
    catalog: dict[Any, Any],
    extra_names: set[str],
) -> None:
    names = (set(module.__all__) | extra_names) - _hidden_names_for(module)
    for name in sorted(names):
        data = _json_help(module, name)
        assert data["schema_version"] == "1"
        assert data["surface"] == surface_name
        assert data["kind"] != "unknown", name
        assert REQUIRED_BY_KIND[data["kind"]] <= set(data), name
        assert _json_size(data) < 14_000, name


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

    data = _json_help(mv, "load_frame")
    assert data["kind"] == "unknown"
    assert data.get("doc", "") == ""
    assert "Marivo Python-native analysis runtime" not in data.get("doc", "")


def test_semantic_catalog_descriptor_lists_agent_workflow_methods() -> None:
    result = _json_help(ms, "SemanticCatalog")

    assert result["kind"] == "class"
    methods = {entry["name"] for entry in cast("list[dict[str, Any]]", result["methods"])}
    assert {
        "list",
        "get",
        "readiness",
    } <= methods


def test_semantic_metric_descriptor_uses_l1_constraint_summaries() -> None:
    result = _json_help(ms, "metric")

    assert result["kind"] == "topic"
    assert result["symbol"] == "metric"
    assert result["doc"]
    content = cast("dict[str, Any]", result["content"])
    contract = cast("dict[str, Any]", content["authoring_contract"])
    assert contract["constructor"] == "metric family"
    assert contract["decision_order"] == [
        "count",
        "aggregate",
        "ratio",
        "weighted_average",
        "linear",
        "expression",
    ]
    variants = cast("dict[str, dict[str, Any]]", contract["variants"])
    assert variants["aggregate"]["constructor"] == "ms.aggregate"
    assert variants["expression"]["constructor"] == "@ms.metric"


def test_datasource_trino_descriptor_lists_secret_env_constraint() -> None:
    result = _json_help(md, "trino")

    assert result["kind"] == "callable"
    assert result["symbol"] == "trino"
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


def test_datasource_text_help_prints_and_help_text_returns_string(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = md.help("trino")

    captured = capsys.readouterr()
    assert result is None
    assert "marivo.datasource: trino" in captured.out

    text = md.help_text("trino")
    captured = capsys.readouterr()
    assert "marivo.datasource: trino" in text
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
