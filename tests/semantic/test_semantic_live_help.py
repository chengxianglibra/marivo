"""Semantic live-help target resolution and runtime behavior."""

from __future__ import annotations

from typing import Literal

import pytest

import marivo
import marivo.analysis as mv
import marivo.semantic as ms
from marivo._authoring.model import AuthoringRepair
from marivo._help.model import MarivoHelpTargetError
from marivo._symbolic import native_symbol_ref
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import ref as ref_factory
from marivo.semantic.errors import SemanticHelpTargetError, SemanticLoadError, SemanticRuntimeError


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

    assert None not in required
    for canonical_id in required:
        qualified = f"semantic.{canonical_id}"
        assert qualified in distances
        assert distances[qualified] <= 4


@pytest.mark.parametrize(
    ("target", "callable_value"),
    (
        ("nulls.reject", ms.nulls.reject),
        ("empty.zero", ms.empty.zero),
    ),
)
def test_retired_value_policy_variants_are_not_help_targets(
    target: str, callable_value: object
) -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE
    from marivo.semantic.errors import SemanticHelpTargetError

    for query in (target, callable_value):
        with pytest.raises(SemanticHelpTargetError):
            resolve_live_target(query, SEMANTIC_LIVE_SURFACE)


@pytest.mark.parametrize("target", ("zero_denominator.error", "metric.divide", "row.divide"))
def test_removed_numeric_policies_and_divide_are_not_help_targets(target: str) -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    with pytest.raises(SemanticHelpTargetError):
        resolve_live_target(target, SEMANTIC_LIVE_SURFACE)
    assert not hasattr(ms, "zero_denominator")


def test_semantic_live_surface_rejects_cross_surface_target() -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    with pytest.raises(SemanticHelpTargetError):
        resolve_live_target(mv.Session, SEMANTIC_LIVE_SURFACE)


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
    (
        (
            "_raise_period_lookup",
            ms.ref.period_calendar("sales.fiscal"),
            "grain",
            "analysis",
            "calendar.grain",
        ),
        (
            "_raise_period_lookup",
            ms.ref.period_calendar("sales.fiscal"),
            "period",
            "analysis",
            "calendar.period",
        ),
        (
            "_raise_period_lookup",
            ms.ref.period_calendar("sales.fiscal"),
            "period_on",
            "analysis",
            "calendar.period_on",
        ),
        (
            "_raise_period_lookup",
            ms.ref.period_calendar("sales.fiscal"),
            "periods",
            "analysis",
            "calendar.periods",
        ),
        (
            "_raise_period_lookup",
            ms.ref.period_calendar("sales.fiscal"),
            "snapshot",
            "semantic",
            "preview",
        ),
        (
            "_raise_temporal_set_lookup",
            ms.ref.temporal_set("sales.campaigns"),
            "occurrence",
            "analysis",
            "temporal_set.occurrence",
        ),
        (
            "_raise_temporal_set_lookup",
            ms.ref.temporal_set("sales.campaigns"),
            "occurrences",
            "analysis",
            "temporal_set.occurrences",
        ),
        (
            "_raise_temporal_set_lookup",
            ms.ref.temporal_set("sales.campaigns"),
            "snapshot",
            "semantic",
            "preview",
        ),
    ),
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


def test_temporal_catalog_lookup_rejects_an_unregistered_operation() -> None:
    from marivo.semantic.catalog import _raise_period_lookup

    with pytest.raises(RuntimeError, match="unsupported period-calendar lookup operation"):
        _raise_period_lookup(
            ref_factory.period_calendar("sales.fiscal"),
            "synthetic",
            "probe",
            details={},
        )


def test_help_rejects_private_object() -> None:
    assert callable(marivo.help)
    with pytest.raises(MarivoHelpTargetError):
        marivo.help(object())


def test_help_rejects_private_callable_owner_string() -> None:
    assert callable(marivo.help)
    with pytest.raises(MarivoHelpTargetError):
        marivo.help("_authoring_declarations.metric")


def test_loaded_entry_help_is_reference_briefing_without_runtime_effects(
    authoring_evidence_project: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert callable(marivo.help)
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    catalog = ms.load()
    entry = catalog.domains.get("sales")
    revenue = catalog.metrics.get("sales.revenue")

    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("reference help must not load or query")

    monkeypatch.setattr("marivo.semantic.reader.SemanticProject.load", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)

    with pytest.raises(SemanticHelpTargetError):
        resolve_live_target(entry, SEMANTIC_LIVE_SURFACE)
    # Definition facts remain available through the explicit Catalog operation.
    value = catalog.describe(ms.ref.metric("sales.revenue"))
    assert value is not None
    assert marivo.help(type(value)) is None


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
    without_repair = SemanticLoadError(
        kind="synthetic_unregistered_error",
        message="semantic project is invalid",
    )

    for error in (with_repair, without_repair):
        with pytest.raises(SemanticHelpTargetError):
            resolve_live_target(error, SEMANTIC_LIVE_SURFACE)
    assert resolve_live_target(SemanticLoadError, SEMANTIC_LIVE_SURFACE).kind == "error_contract"
    assert with_repair.repair is not None


def test_expression_error_help_has_one_owner_and_preserves_repairs(
    capsys: pytest.CaptureFixture[str],
) -> None:
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
    public_help(SemanticExpressionError)
    class_help = capsys.readouterr().out
    public_help("semantic.semantic_expression_error")
    assert capsys.readouterr().out == class_help
    with pytest.raises(MarivoHelpTargetError):
        public_help(error)
    assert error.expected and error.received and error.repair.action


def test_live_help_performs_no_runtime_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    assert callable(marivo.help)

    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("help must not perform runtime effects")

    monkeypatch.setattr("marivo.semantic.reader.SemanticProject.load", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)

    assert marivo.help() is None
    for target in (
        "semantic",
        "semantic.model",
        "semantic.model.identity",
        "semantic.model.fields",
        "semantic.model.relationships",
        "semantic.expressions",
        "semantic.time",
        "semantic.time.parsing",
        "semantic.time.calendars",
        "semantic.catalog",
        "semantic.validation",
        "semantic.load",
        ms.load,
        ms.SemanticCatalog,
    ):
        assert marivo.help(target) is None


def test_expression_constraint_retains_event_ast_rules() -> None:
    from marivo.semantic.constraints import CONSTRAINTS, ConstraintId

    constraint = CONSTRAINTS[ConstraintId.AST_SINGLE_RETURN]
    spec = constraint.ast_spec
    assert spec is not None
    assert "Assign" not in spec.forbidden_statements
    assert "AugAssign" in spec.forbidden_statements


def test_field_null_predicate_has_one_resolvable_reachable_help_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert callable(marivo.help)
    from collections import deque

    from marivo.semantic._capabilities.registry import REGISTRY

    marivo.help("semantic.dimension.isnull")
    expected = capsys.readouterr().out
    assert "region.isnull()" in expected
    descriptor = REGISTRY.by_canonical_id("dimension.isnull")
    assert descriptor.callable_path is None
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
    for target, method in (("ref.entity", ms.ref.entity), ("ref.metric", ms.ref.metric)):
        marivo.help("semantic." + target)
        expected = capsys.readouterr().out
        marivo.help(method)
        assert capsys.readouterr().out == expected
        assert len(expected.splitlines()) <= 72 and len(expected) <= 7000
        assert target in REGISTRY.canonical_ids()
    assert not hasattr(ms, "finalize")
    for target in ("finalize", "finalized_declarations", "finalized_declarations.resolve"):
        with pytest.raises(MarivoHelpTargetError):
            marivo.help("semantic." + target)


def test_expression_display_has_one_owner_for_all_receiver_forms(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo.semantic import expressions
    from marivo.semantic._capabilities.registry import REGISTRY

    assert callable(marivo.help)
    marivo.help("semantic.entity.show")
    expected = capsys.readouterr().out
    assert "orders.show()" in expected and "None" in expected
    for receiver in (expressions.Entity, expressions.Relationship):
        marivo.help(receiver.show)
        assert capsys.readouterr().out == expected
    owners = [
        row.canonical_id
        for row in REGISTRY.descriptors
        if row.callable_path == "marivo.semantic.expressions._ExpressionDisplay.show"
    ]
    assert owners == ["entity.show"]
    marivo.help("semantic.semantic_catalog.describe")
    assert "catalog.describe(" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("target", "facts"),
    (
        (
            "semantic.metric",
            (
                "Readiness checks physical dependencies, not Metrics",
                "catalog.describe(ref).show()",
                "AnalysisFrame.compute()/persist()",
            ),
        ),
        (
            "semantic.entity.show",
            (
                "Entity or Relationship metadata without source reads",
                "Deferred inputs have no show method",
                "AnalysisFrame.show()",
                "Pending Frame display does not evaluate or prepare schema",
            ),
        ),
        (
            "semantic.entity.contract",
            (
                "definition metadata without source reads",
                "Deferred inputs carry no contract method",
                "AnalysisFrame.show()",
                "without evaluating pending inputs",
            ),
        ),
        (
            "semantic.metric.where",
            (
                "Filter source contributions before reduction",
                "AnalysisFrame.where on current rows",
            ),
        ),
        (
            "semantic.readiness",
            (
                "without connecting or querying",
                "Unrelated load warnings are excluded",
                "computing or persisting a source AnalysisFrame",
                "physical types and execution support",
            ),
        ),
        (
            "semantic.semantic_catalog.describe",
            (
                "Describe one compiled Entity, field, Metric or Relationship without source reads",
                "definition facts live here",
                "captured analysis facts live on AnalysisFrame.show()",
                "Pending Frame display does not evaluate or prepare schema",
            ),
        ),
        (
            "semantic.expression_contract",
            (
                "Bounded source-free definition facts",
                "AnalysisFrame.compute()/persist()",
            ),
        ),
    ),
)
def test_semantic_analysis_guidance_keeps_frame_ownership_and_source_boundaries(
    target: str,
    facts: tuple[str, ...],
) -> None:
    from marivo._help.render import render_help_text

    page = render_help_text(target)[0]
    assert "Query" not in page
    for fact in facts:
        assert fact in page


def test_semantic_frame_continuations_resolve_to_exact_reflected_receivers(
    authoring_evidence_project: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo._help.render import render_help_text
    from marivo.semantic.expressions import Entity, ExpressionContract, Relationship

    catalog = ms.load()
    orders = catalog.entities.get("sales.orders")
    contract = catalog.describe(ms.ref.metric("sales.revenue"))
    for target, receivers in (
        ("semantic.entity.show", (Entity.show, Relationship.show, orders.show)),
        ("semantic.entity.contract", (Entity.contract, Relationship.contract, orders.contract)),
        ("semantic.readiness", (ms.SemanticCatalog.readiness, catalog.readiness)),
        ("semantic.semantic_catalog.describe", (ms.SemanticCatalog.describe, catalog.describe)),
        ("semantic.expression_contract", (ExpressionContract, contract.show)),
    ):
        expected = render_help_text(target)[0]
        for receiver in receivers:
            marivo.help(receiver)
            assert capsys.readouterr().out == expected + "\n"

    for source, method_name in (
        ("semantic.entity.show", "show"),
        ("semantic.entity.contract", "show"),
        ("semantic.semantic_catalog.describe", "show"),
        ("semantic.metric.where", "where"),
        ("semantic.readiness", "compute"),
        ("semantic.readiness", "persist"),
    ):
        target = f"analysis.analysis_frame.{method_name}"
        assert f'marivo.help("{target}")' in render_help_text(source)[0]
        assert (
            render_help_text(target)[0]
            == render_help_text(getattr(mv.AnalysisFrame, method_name))[0]
        )


def test_closed_definition_display_routes_remain_bounded_and_reachable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from collections import deque

    from marivo.semantic._capabilities.registry import REGISTRY
    from marivo.semantic.expressions import Entity

    assert callable(marivo.help)
    marivo.help("semantic.entity.show")
    expected = capsys.readouterr().out
    for operation in (Entity.show,):
        marivo.help(operation)
        assert capsys.readouterr().out == expected
    assert "orders.show()" in expected
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
    assert distances["entity.show"] <= 3


def test_advertised_semantic_native_methods_have_exact_public_help_routes() -> None:
    from marivo._help.route import NativeHelpRoute, route_help_target
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

    from marivo._help.route import NativeHelpRoute, route_help_target
    from marivo.datasource._capabilities.registry import TYPE_CONTRACTS as DATASOURCE_TYPES
    from marivo.ontology._capabilities.registry import TYPE_CONTRACTS as ONTOLOGY_TYPES
    from marivo.semantic._capabilities.registry import TYPE_CONTRACTS as SEMANTIC_TYPES

    for type_obj, contract in (
        *SEMANTIC_TYPES.items(),
        *DATASOURCE_TYPES.items(),
        *ONTOLOGY_TYPES.items(),
    ):
        marivo.help(type_obj)
        page = capsys.readouterr().out
        method_targets = dict(re.findall(r'  (\w+): marivo.help\("([a-z_.]+)"\)', page))
        assert set(contract.public_methods) <= method_targets.keys(), contract.name
        assert set(contract.type_page_methods) <= set(contract.public_methods)
        for name in contract.public_methods:
            route = route_help_target(method_targets[name])
            assert isinstance(route, NativeHelpRoute), (contract.name, name)
            marivo.help(method_targets[name])
            assert f"Target: {method_targets[name]}\n" in capsys.readouterr().out

    marivo.help("semantic.semantic_catalog")
    page = capsys.readouterr().out
    assert 'preview: marivo.help("semantic.preview")' in page
    assert 'show: marivo.help("analysis.runtime.values.show")' in page
    marivo.help("semantic.relationship")
    assert 'contract: marivo.help("semantic.entity.contract")' in capsys.readouterr().out
    marivo.help("semantic.period_calendar_entry")
    assert (
        'period: marivo.help("analysis.catalog.period_calendars.period")' in capsys.readouterr().out
    )


def test_unregistered_method_requires_explicit_type_page_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    from marivo.preview import PreviewResult
    from marivo.semantic._capabilities import registry

    contracts = dict(registry.TYPE_CONTRACTS)
    contracts[PreviewResult] = replace(contracts[PreviewResult], type_page_methods=())
    monkeypatch.setattr(registry, "TYPE_CONTRACTS", contracts)
    with pytest.raises(MarivoHelpTargetError):
        marivo.help(PreviewResult)


@pytest.mark.parametrize(
    ("target", "syntax"),
    (
        ("semantic.metric.where", "revenue.where"),
        ("semantic.row.sum", "amount.sum"),
        ("semantic.field.via", "region.via"),
        ("semantic.metric_samples.first", "samples.first"),
    ),
)
def test_native_deferred_grammar_keeps_expression_kind_and_syntax(
    target: str,
    syntax: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marivo.help(target)
    page = capsys.readouterr().out
    assert f"Target: {target}\nKind: expression\n" in page
    assert f"Syntax: {syntax}(...)" in page
    assert "  Example:\n" in page
    assert "Signature:" not in page


@pytest.mark.parametrize("target", ("semantic.row.sum", "semantic.metric_samples.sum"))
def test_sum_help_discloses_fixed_empty_and_native_numeric_rules(
    target: str, capsys: pytest.CaptureFixture[str]
) -> None:
    marivo.help(target)
    page = capsys.readouterr().out
    assert "empty input returns zero" in page
    assert "nonempty all-NA input returns null" in page
    assert "Native NaN/Infinity are retained" in page
    assert "parameters: empty" not in page


@pytest.mark.parametrize("target", ("semantic.row.mean", "semantic.metric_samples.mean"))
def test_mean_help_discloses_fixed_null_rules_and_sampling(
    target: str, capsys: pytest.CaptureFixture[str]
) -> None:
    marivo.help(target)
    page = capsys.readouterr().out
    assert "Mean skips NULLs and returns NA without valid inputs" in page
    assert "asserts no NULLs on the demanded input" in page
    assert "empty input passes" in page
    assert "Successful values use the same computation" in page
    assert "Native time-sample AVG has no local fallback" in page
    if target == "semantic.metric_samples.mean":
        assert "without scheduled coverage or implicit fill" in page
        assert "independently of formula shape or execution route" in page


def test_entity_help_preserves_the_supported_table_decorator(
    capsys: pytest.CaptureFixture[str],
) -> None:
    marivo.help(ms.entity)
    text = capsys.readouterr().out
    assert "omitting name returns" in text
    assert "omit name for the restricted Table decorator" in text
    assert "not a decorator" not in text
    assert "remove the decorated function body" not in text


@pytest.mark.parametrize("method", ("first", "last"))
def test_ordered_sample_help_discloses_inference_and_null_default(
    method: str, capsys: pytest.CaptureFixture[str]
) -> None:
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
    marivo.help(f"semantic.{target}")
    text = capsys.readouterr().out
    assert "when omitted, exactly one internal TimeField must be inferable" in text
    assert "include_null defaults to False" in text
    assert "Time keys must uniquely order the samples" in text


def test_per_help_covers_observed_and_complete_sample_domains(
    capsys: pytest.CaptureFixture[str],
) -> None:
    marivo.help("semantic.metric.per")
    text = capsys.readouterr().out
    for family in ("Dimension", "TimeField", "Grouping", "tuple[", "Population"):
        assert family in text
    assert "Field samples use observed keys" in text
    assert "Population binds every compatible tuple, including empty samples" in text
    assert "native field Grouping" in text
    assert "Place correlated mv.membership(...) rules directly in samples" in text
    assert "explicit non-first fields locate interleaved axes" in text
    assert "(P, rule, ...)" in text
    assert "parameters: memberships" not in text


def test_semantic_concept_constructor_and_expression_role_share_one_navigation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A Metric concept leads to both its definition and complete expression grammar."""
    from marivo._help.render import render_help_text

    concept = render_help_text("semantic.metric")[0]
    assert "Kind: object" in concept
    for target in (
        "semantic.metric.define",
        "semantic.metric.where",
        "semantic.metric.per",
        "semantic.metric.window",
    ):
        assert target in concept
        assert render_help_text(target)[0]
    marivo.help(ms.metric)
    assert capsys.readouterr().out == render_help_text("semantic.metric.define")[0] + "\n"
    for retired in ("semantic.objects.metric", "semantic.Metric.where", "ms.metric"):
        with pytest.raises(MarivoHelpTargetError):
            render_help_text(retired)


def test_semantic_concept_outputs_follow_each_actual_construction_mode() -> None:
    from marivo._help.render import render_help_text

    metric = render_help_text("semantic.metric")[0]
    assert "lookup symbol: ms.ref.metric(path)" in metric
    assert 'marivo.help("semantic.metric.define"); returns Deferred' in metric
    assert "output: Ref[metric]" not in metric
    measure = render_help_text("semantic.measure")[0]
    assert 'marivo.help("semantic.measure.from_column"); returns MeasureSymbol' in measure
    assert 'marivo.help("semantic.measure.define"); returns Deferred' in measure


def test_removed_join_key_tuple_method_is_not_a_help_target() -> None:
    with pytest.raises(MarivoHelpTargetError):
        marivo.help("semantic.join_key.to_tuple")


def test_join_key_help_discloses_its_ordered_fields(capsys: pytest.CaptureFixture[str]) -> None:
    marivo.help(ms.JoinKey)
    text = capsys.readouterr().out
    assert "Public fields: from_key, to_key" in text
    assert "to_tuple" not in text


def test_scalar_field_role_links_governed_acquisition_and_existing_grammar() -> None:
    from marivo._help.render import render_help_text

    page = render_help_text("semantic.field")[0]
    assert "Kind: topic" in page
    assert "Dimension, TimeField or Measure" in page
    for target in (
        "semantic.dimension",
        "semantic.time_dimension",
        "semantic.measure",
        "semantic.field.via",
        "semantic.field.at",
        "semantic.dimension.isnull",
    ):
        assert target in page
        assert render_help_text(target)[0]
    assert "semantic.field" in render_help_text("semantic.expressions")[0]


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
    for target in (
        "semantic.model.identity",
        "semantic.model.fields",
        "semantic.model.relationships",
        "semantic.metric",
        "semantic.metric.define",
        "semantic.ai_context",
    ):
        assert f'marivo.help("{target}")' in model
        assert render_help_text(target)[0]
    validation = render_help_text("semantic.validation")[0]
    for target in ("load", "readiness", "preview", "preview_many", "source_health", "source_check"):
        assert f'marivo.help("semantic.{target}")' in validation
    assert "load success != readiness != preview success != source health" in validation
    assert "Ordinary previews do not persist certification or change readiness" in validation
    assert "That the current source satisfies it" in validation
    assert (
        'marivo.help("analysis.inputs.time.calendars")'
        in render_help_text("semantic.time.calendars")[0]
    )


def test_expression_navigation_serves_closed_question_scoped_composition() -> None:
    from marivo._help.render import render_help_text

    page = render_help_text("semantic.expressions")[0]
    assert "closed Catalog fields and Metrics" in page
    assert "does not require authoring or publishing a Metric" in page
    assert "revenue / order_count" in page
    assert "bare lookup symbols are not analysis inputs" in page
    assert "a compound Metric is not automatically filterable" in page
    assert "Decimal" not in page
    for role in ("field", "predicate", "row", "metric", "metric_samples"):
        assert f'marivo.help("semantic.{role}")' in page
    for operation in ("where", "per", "window"):
        assert f'marivo.help("semantic.metric.{operation}")' in page
    metric = render_help_text("semantic.metric")[0]
    assert metric.index("Existing closed values") < metric.index(
        "Reusable definition construction modes"
    )
    assert "without publishing a definition" in metric
    assert "Decimal" in render_help_text("semantic.row")[0]


def test_retired_semantic_topics_have_no_alias() -> None:
    for target in (
        "authoring",
        "objects",
        "builders",
        "checks",
        "builders.entity_history",
        "builders.field_metric_support",
        "builders.governed_temporal",
        "builders.relationship",
        "builders.temporal_parsing",
        "context",
        "model.metrics",
        "validation.health",
        "validation.preview",
    ):
        with pytest.raises(MarivoHelpTargetError):
            marivo.help(f"semantic.{target}")


def test_ontology_authoring_remains_the_exact_load_callable() -> None:
    import marivo.ontology as mo
    from marivo._help.render import render_help_text

    exact = render_help_text("ontology.authoring")[0]
    assert "Kind: callable" in exact
    assert render_help_text(mo.load)[0] == exact
    for target in ("authoring", "influences", "related_to"):
        assert f'marivo.help("ontology.{target}")' in render_help_text("ontology")[0]
