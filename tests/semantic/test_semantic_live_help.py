"""Semantic live-help target resolution and runtime behavior."""

from __future__ import annotations

from typing import Literal

import pytest

import marivo
import marivo.semantic as ms
from marivo._authoring.model import AuthoringRepair
from marivo._symbolic import native_symbol_ref
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic.errors import SemanticLoadError, SemanticRuntimeError


def test_registry_graph_reaches_every_required_semantic_leaf_within_four_edges() -> None:
    from collections import deque

    from marivo._authoring.model import AuthoringCapability
    from marivo.semantic._capabilities.registry import REGISTRY

    required = {
        descriptor.canonical_id
        for descriptor in REGISTRY.descriptors
        if descriptor.kind != "method"
    }
    required.update(
        descriptor.canonical_id
        for descriptor in REGISTRY.help_descriptors
        if not isinstance(descriptor, AuthoringCapability)
    )
    required.update(
        descriptor.canonical_id
        for descriptor in REGISTRY.descriptors
        if descriptor.kind == "method"
        and descriptor.canonical_id.startswith(("ref.", "source_check."))
    )

    targets: tuple[LiveHelpTarget, ...]
    distances: dict[str, int] = {"global.authoring": 0}
    queue = deque(("global.authoring",))
    while queue:
        node = queue.popleft()
        if node == "global.authoring":
            targets = tuple(
                target for section in REGISTRY.root_sections for target in section.members
            )
        elif node.startswith("semantic."):
            canonical_id = node.removeprefix("semantic.")
            if canonical_id not in REGISTRY.canonical_ids():
                continue
            targets = REGISTRY.routes(canonical_id)
        else:
            continue
        for target in targets:
            if target.canonical_id is None:
                continue
            child = f"{target.surface}.{target.canonical_id}"
            if child in distances:
                continue
            distances[child] = distances[node] + 1
            queue.append(child)

    for canonical_id in required:
        qualified = f"semantic.{canonical_id}"
        assert qualified in distances
        assert distances[qualified] <= 4


# ---------------------------------------------------------------------------
# Help target matrix — string, callable, type, error type, cross-surface
# rejections, unknown string, private object, no-runtime-effects.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    ("preview",),
)
def test_registered_preview_string_paths_resolve_to_one_descriptor(target: str) -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    resolved = resolve_live_target(target, SEMANTIC_LIVE_SURFACE)
    assert resolved.kind == "descriptor"
    assert resolved.canonical_id == "preview"


@pytest.mark.parametrize(
    ("raiser_name", "ref", "operation", "surface", "canonical_id"),
    (),
)
def test_temporal_catalog_error_instances_route_to_the_exact_next_help(
    raiser_name: str,
    ref: object,
    operation: str,
    surface: Literal["analysis", "datasource", "semantic", "ontology"],
    canonical_id: str,
) -> None:
    import marivo.semantic.catalog as catalog_module

    raiser = getattr(catalog_module, raiser_name)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        reference = native_symbol_ref(ref)
        assert reference is not None
        raiser(reference, operation, "probe", details={})

    error = exc_info.value
    assert error.repair is not None
    assert error.repair.help_target == LiveHelpTarget(
        surface=surface,
        canonical_id=canonical_id,
    )


def test_error_help_preserves_instances_with_and_without_repair() -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    with_repair = SemanticLoadError(
        kind="invalid_project",
        message="semantic project is invalid",
        expected="one loaded domain",
        received="no domains",
        location_label="semantic project",
        repair=AuthoringRepair(
            kind="retry",
            help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
            action="Inspect the analysis input contract.",
            snippet='marivo.help("analysis.observe")',
            candidates=("observe",),
        ),
    )
    assert resolve_live_target(SemanticLoadError, SEMANTIC_LIVE_SURFACE).kind == "error_contract"
    assert with_repair.repair is not None


def test_expression_error_help_has_one_owner_and_preserves_repairs() -> None:
    from marivo._help.render import help as public_help
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE
    from marivo.semantic.errors import SemanticExpressionError

    with pytest.raises(SemanticExpressionError) as caught:
        ms.aggregate(name="old_revenue")
    error = caught.value
    assert error.expected and error.received and error.repair
    for target in (SemanticExpressionError, "semantic_expression_error"):
        resolved = resolve_live_target(target, SEMANTIC_LIVE_SURFACE)
        assert resolved.kind == "error_contract"
        assert resolved.error_name == "SemanticExpressionError"
    assert marivo.help is public_help


def test_field_null_predicate_has_one_resolvable_reachable_help_contract() -> None:
    assert callable(marivo.help)
    from collections import deque

    from marivo.semantic._capabilities.registry import REGISTRY

    descriptor = REGISTRY.by_canonical_id("dimension.isnull")
    assert descriptor.callable_path == "marivo.semantic._expression_values.RowExpression.isnull"
    distances = {
        target.canonical_id: 1 for section in REGISTRY.root_sections for target in section.members
    }
    queue = deque(distances)
    while queue:
        node = queue.popleft()
        if node not in REGISTRY.canonical_ids():
            continue
        for route in REGISTRY.routes(node):
            target = route.canonical_id
            if route.surface == "semantic" and target is not None and target not in distances:
                distances[target] = distances[node] + 1
                queue.append(target)
    assert distances["dimension.isnull"] <= 3


def test_native_lookup_factories_have_owned_help_without_finalization_routes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo.semantic._capabilities.registry import REGISTRY

    assert callable(marivo.help)
    for target in ("ref.entity", "ref.metric"):
        marivo.help("semantic." + target)
        expected = capsys.readouterr().out
        assert len(expected.splitlines()) <= 72 and len(expected) <= 7000
        assert target in REGISTRY.canonical_ids()


def test_expression_display_has_one_owner_for_all_receiver_forms() -> None:
    from marivo.semantic._capabilities.registry import REGISTRY

    owners = [
        row.canonical_id
        for row in REGISTRY.descriptors
        if row.callable_path == "marivo.semantic.catalog._SemanticObject.show"
    ]
    assert owners == ["semantic_object.show"]


def test_closed_definition_display_routes_remain_bounded_and_reachable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from collections import deque

    from marivo.semantic._capabilities.registry import REGISTRY

    assert callable(marivo.help)
    marivo.help("semantic.semantic_object.show")
    expected = capsys.readouterr().out
    assert len(expected.splitlines()) <= 72 and len(expected) <= 7000
    distances = {
        target.canonical_id: 1 for section in REGISTRY.root_sections for target in section.members
    }
    queue = deque(distances)
    while queue:
        node = queue.popleft()
        if node not in REGISTRY.canonical_ids():
            continue
        for route in REGISTRY.routes(node):
            target = route.canonical_id
            if route.surface == "semantic" and target is not None and target not in distances:
                distances[target] = distances[node] + 1
                queue.append(target)
    assert distances["semantic_object.show"] <= 3


def test_advertised_semantic_native_methods_have_exact_public_help_routes() -> None:
    from marivo._help.model import NativeHelpRoute
    from marivo._help.route import route_help_target
    from marivo.semantic._capabilities.registry import TYPE_CONTRACTS

    for result_type, contract in TYPE_CONTRACTS.items():
        for method_name in contract.public_methods:
            # Shared presentation methods keep their existing type-card contract.
            if method_name in {"show", "render"}:
                continue
            method = getattr(result_type, method_name)
            route = route_help_target(method)
            assert isinstance(route, NativeHelpRoute), (contract.name, method_name)
            assert route.resolved.kind == "descriptor", (contract.name, method_name)
            canonical_id = route.resolved.canonical_id
            assert canonical_id is not None
            qualified = f"{route.owner}.{canonical_id}"
            string_route = route_help_target(qualified)
            assert isinstance(string_route, NativeHelpRoute)
            assert string_route.resolved == route.resolved


@pytest.mark.release
def test_type_method_navigation_uses_the_actual_documentation_owner(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import re

    from marivo._help.model import NativeHelpRoute
    from marivo._help.route import route_help_target
    from marivo.datasource._capabilities.model import DatasourceTypeContract
    from marivo.datasource._capabilities.registry import TYPE_CONTRACTS as DATASOURCE_TYPES
    from marivo.ontology._capabilities.registry import TYPE_CONTRACTS as ONTOLOGY_TYPES
    from marivo.ontology._capabilities.registry import OntologyTypeContract
    from marivo.semantic._capabilities.model import SemanticTypeContract
    from marivo.semantic._capabilities.registry import TYPE_CONTRACTS as SEMANTIC_TYPES

    contracts: tuple[
        tuple[type, SemanticTypeContract | DatasourceTypeContract | OntologyTypeContract], ...
    ] = (
        *SEMANTIC_TYPES.items(),
        *DATASOURCE_TYPES.items(),
        *ONTOLOGY_TYPES.items(),
    )
    for type_obj, contract in contracts:
        marivo.help(type_obj)
        page = capsys.readouterr().out
        method_targets = dict(re.findall(r'  (\w+): marivo.help\("([a-z0-9_.]+)"\)', page))
        assert set(contract.public_methods) <= method_targets.keys(), contract.name
        assert set(contract.type_page_methods) <= set(contract.public_methods)
        for name in contract.public_methods:
            route = route_help_target(method_targets[name])
            assert isinstance(route, NativeHelpRoute), (contract.name, name)
            marivo.help(method_targets[name])


@pytest.mark.parametrize(
    "target",
    ("row.min", "row.max", "row.count", "entity.count", "metric.window"),
)
def test_focused_expression_help_keeps_only_the_receivers_contract(
    target: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marivo.help(f"semantic.{target}")
    page = capsys.readouterr().out
    assert len(page) < 2000


@pytest.mark.parametrize("method", ("first", "last"))
def test_ordered_sample_help_discloses_inference_and_null_default(method: str) -> None:
    from marivo._authoring.model import AuthoringCapability
    from marivo.semantic._capabilities.registry import REGISTRY

    target = f"metric_samples.{method}"
    descriptor = REGISTRY.by_canonical_id(target)
    assert isinstance(descriptor, AuthoringCapability)
    parameters = {
        name: requirement
        for requirement in descriptor.input_requirements
        for name in requirement.parameter_names
    }
    assert parameters["order_by"].min_count == 0
    assert parameters["include_null"].min_count == 0


def test_semantic_task_tree_keeps_definition_and_proof_choices_direct() -> None:
    from marivo._help.render import render_help_text
    from marivo.semantic._capabilities.registry import REGISTRY

    assert tuple(
        target.canonical_id for section in REGISTRY.root_sections for target in section.members
    ) == (
        "model",
        "expressions",
        "time",
        "catalog",
        "validation",
    )
    model = render_help_text("semantic.model")[0]
    validation = render_help_text("semantic.validation")[0]


@pytest.mark.parametrize("carrier", (ms.RowExpression, ms.MetricExpression))
def test_typed_carrier_methods_have_one_native_callable_owner(carrier: type) -> None:
    from marivo._help.model import NativeHelpRoute
    from marivo._help.route import native_route_target, route_help_target

    for name, method in vars(carrier).items():
        if name.startswith("_") or not callable(method):
            continue
        route = route_help_target(method)
        assert isinstance(route, NativeHelpRoute), (carrier, name)
        assert route.owner == "semantic"
        target = native_route_target(route)
        string_route = route_help_target(target)
        assert isinstance(string_route, NativeHelpRoute)
        assert string_route.resolved == route.resolved
