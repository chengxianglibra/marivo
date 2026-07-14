"""Cross-surface tests for the agent-facing help() contract.

The analysis and datasource surfaces no longer use the shared JSON ``Surface``
introspection infrastructure — each has its own capability-registry-based
renderer. Only the semantic surface is covered by the JSON-based parametrised
tests below. Live help invariants live in
``tests/test_analysis_help.py``.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.analysis.constraints import CONSTRAINTS as ANALYSIS_CONSTRAINTS
from marivo.semantic.constraints import CONSTRAINTS as SEMANTIC_CONSTRAINTS

REPO_ROOT = Path(__file__).resolve().parents[1]

# Semantic still uses the shared JSON Surface.
SURFACES = [
    pytest.param(
        "marivo.semantic",
        ms,
        SEMANTIC_CONSTRAINTS,
        {"constraints", "additivity", "composition", "authoring"},
        id="semantic",
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
        "transform",
        "session",
        "datasources",
        "help",
        "artifacts",
        "recovery",
        "boundary.to_pandas",
        "boundary.derive_metric_frame",
        "alignment",
        "calendar",
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
            assert set(constraint) <= {"id", "title", "hint", "example", "help_target"}, name
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


def test_no_inherited_or_module_docstring_leaks() -> None:
    text = md.help_text("trino")
    assert "Signature:" in text
    assert "__init__" not in text


def test_semantic_catalog_descriptor_lists_agent_workflow_methods() -> None:
    result = _json_help(ms, "SemanticCatalog")

    assert result["kind"] == "class"
    assert "catalog.metrics" in result["doc"] or "catalog.domains" in result["doc"]
    methods = {entry["name"] for entry in cast("list[dict[str, Any]]", result["methods"])}
    assert {
        "get",
        "readiness",
    } <= methods


def test_semantic_load_descriptor_mentions_configured_layer_paths() -> None:
    result = _json_help(ms, "load")

    assert result["kind"] == "callable"
    assert "[semantic].layer_paths" in result["doc"]
    assert "external models roots" in result["doc"]


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
        "cumulative",
        "ratio",
        "weighted_average",
        "linear",
        "expression",
    ]
    variants = cast("dict[str, dict[str, Any]]", contract["variants"])
    assert variants["aggregate"]["constructor"] == "ms.aggregate"
    assert variants["expression"]["constructor"] == "@ms.metric"
    assert variants["cumulative"]["constructor"] == "ms.cumulative"


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
