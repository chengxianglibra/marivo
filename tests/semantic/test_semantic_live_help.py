"""Semantic live-help target resolution and runtime behavior."""

from __future__ import annotations

from typing import Literal

import pytest

import marivo
import marivo.analysis as mv
import marivo.semantic as ms
from marivo._authoring.model import AuthoringRepair
from marivo._help.model import MarivoHelpTargetError
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
            targets = (LiveHelpTarget(surface="semantic", canonical_id="authoring"),)
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
        ("zero_denominator.error", ms.zero_denominator.error),
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
    assert (
        resolve_live_target("Metric.divide", SEMANTIC_LIVE_SURFACE).canonical_id == "Metric.divide"
    )


def test_semantic_live_surface_rejects_cross_surface_target() -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    with pytest.raises(Exception):
        resolve_live_target(mv.Session, SEMANTIC_LIVE_SURFACE)


# ---------------------------------------------------------------------------
# Help target matrix — string, callable, type, error type, cross-surface
# rejections, unknown string, private object, no-runtime-effects.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    ("preview", "catalog.preview", "SemanticCatalog.preview", "ms.SemanticCatalog.preview"),
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
        (
            "_raise_work_schedule_lookup",
            ms.ref.work_schedule("sales.schedule"),
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
        raiser(ref, operation, "probe", details={})

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
            ms.ref.period_calendar("sales.fiscal"),
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
    entry = catalog.require(ms.ref.domain("sales"))
    revenue = catalog.require(ms.ref.metric("sales.revenue"))

    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("reference help must not load or query")

    monkeypatch.setattr("marivo.semantic.reader.SemanticProject.load", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)

    resolved = resolve_live_target(entry, SEMANTIC_LIVE_SURFACE)
    assert resolved.kind == "reference_briefing"
    assert resolved.reference_id == "sales"
    assert marivo.help(entry) is None
    # Catalog values share their type's contract with finalized values.
    value_help = resolve_live_target(revenue, SEMANTIC_LIVE_SURFACE)
    assert value_help.kind == "type_contract"
    assert value_help.type_name == "Metric"
    assert marivo.help(revenue) is None


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

    briefing = resolve_live_target(with_repair, SEMANTIC_LIVE_SURFACE)
    contract = resolve_live_target(without_repair, SEMANTIC_LIVE_SURFACE)
    error_class = resolve_live_target(SemanticLoadError, SEMANTIC_LIVE_SURFACE)

    assert briefing.kind == "error_briefing"
    assert contract.kind == "error_briefing"
    assert contract.original is without_repair
    assert error_class.kind == "error_contract"
    assert with_repair.repair is not None
    assert with_repair.repair.help_target == LiveHelpTarget(
        surface="analysis",
        canonical_id="observe",
    )


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
    for target in (SemanticExpressionError, "SemanticExpressionError"):
        resolved = resolve_live_target(target, SEMANTIC_LIVE_SURFACE)
        assert resolved.kind == "error_contract"
        assert resolved.error_name == "SemanticExpressionError"
    assert marivo.help is public_help
    public_help(SemanticExpressionError)
    class_help = capsys.readouterr().out
    public_help("semantic.SemanticExpressionError")
    assert capsys.readouterr().out == class_help
    public_help(error)
    instance_help = capsys.readouterr().out
    assert error.expected in instance_help
    assert error.received in instance_help
    assert error.repair.action in instance_help
    assert "SemanticExpressionError" in instance_help


def test_live_help_performs_no_runtime_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    assert callable(marivo.help)

    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("help must not perform runtime effects")

    monkeypatch.setattr("marivo.semantic.reader.SemanticProject.load", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)

    assert marivo.help() is None
    for target in ("semantic.load", ms.load, ms.SemanticCatalog):
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

    from marivo._definition.types import ExactRef
    from marivo.semantic._capabilities.registry import REGISTRY
    from marivo.semantic.expressions import Dimension, TimeField

    root = ExactRef(ms.ref.entity("sales.orders"), "entity-version")
    region = Dimension(
        ExactRef(ms.ref.dimension("sales.orders.region"), "region-version"), root, None
    )
    paid_at = TimeField(
        ExactRef(ms.ref.time_dimension("sales.orders.paid_at"), "time-version"), root, None
    )
    marivo.help("semantic.Dimension.is_null")
    expected = capsys.readouterr().out
    assert "Predicate" in expected and "region.is_null()" in expected
    for method in (region.is_null, paid_at.is_null):
        marivo.help(method)
        assert capsys.readouterr().out == expected
    owners = [
        row.canonical_id
        for row in REGISTRY.descriptors
        if row.callable_path == "marivo.semantic.expressions._FieldComparisons.is_null"
    ]
    assert owners == ["Dimension.is_null"]
    distances = {"authoring": 0}
    queue = deque(("authoring",))
    while queue:
        node = queue.popleft()
        if node not in REGISTRY.canonical_ids():
            continue
        for route in REGISTRY.routes(node):
            target = route.canonical_id
            if route.surface == "semantic" and target is not None and target not in distances:
                distances[target] = distances[node] + 1
                queue.append(target)
    assert distances["Dimension.is_null"] <= 3


def test_capture_finalization_has_owned_help_and_navigation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo.semantic._capabilities.registry import REGISTRY
    from marivo.semantic.expressions import FinalizedDeclarations

    assert callable(marivo.help)
    for target, method in (
        ("finalize", ms.finalize),
        ("FinalizedDeclarations.resolve", FinalizedDeclarations.resolve),
    ):
        marivo.help("semantic." + target)
        expected = capsys.readouterr().out
        marivo.help(method)
        assert capsys.readouterr().out == expected
        assert len(expected.splitlines()) <= 72 and len(expected) <= 7000
        assert target in {route.canonical_id for route in REGISTRY.routes("expressions")}
    marivo.help("semantic.FinalizedDeclarations")
    assert "resolve" in capsys.readouterr().out


def test_expression_display_has_one_owner_for_all_receiver_forms(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo._definition.types import ExactRef, SumAll
    from marivo.semantic import expressions
    from marivo.semantic._capabilities.registry import REGISTRY

    assert callable(marivo.help)
    root = ExactRef(ms.ref.entity("sales.orders"), "entity-version")
    region = expressions.Dimension(
        ExactRef(ms.ref.dimension("sales.orders.region"), "region-version"), root, None
    )
    marivo.help("semantic.Measure.show")
    expected = capsys.readouterr().out
    assert "amount.show()" in expected and "None" in expected
    for receiver in (
        expressions.Entity,
        expressions.Dimension,
        expressions.TimeField,
        expressions.Measure,
        expressions.NumericRowExpr,
        expressions.DeclaredMeasure,
        expressions.Metric,
        expressions.DeclaredMetric,
        expressions.MetricSamples,
        expressions.Predicate,
        expressions.Relationship,
    ):
        for target in (receiver.show, "semantic." + receiver.__name__ + ".show"):
            marivo.help(target)
            assert capsys.readouterr().out == expected
    amount = expressions.Measure(
        ExactRef(ms.ref.measure("sales.orders.amount"), "amount-definition"),
        root,
        "CNY",
        SumAll(),
        None,
    )
    for operation in (region.show, amount.show):
        marivo.help(operation)
        assert capsys.readouterr().out == expected
    owners = [
        row.canonical_id
        for row in REGISTRY.descriptors
        if row.callable_path == "marivo.semantic.expressions._ExpressionDisplay.show"
    ]
    assert owners == ["Measure.show"]
    assert len(expected.splitlines()) <= 72 and len(expected) <= 7000


def test_frozen_declaration_display_resolves_and_display_routes_are_reachable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from collections import deque

    from marivo.semantic._capabilities.registry import REGISTRY
    from marivo.semantic.expressions import FinalizedDeclarations
    from marivo.semantic.loader import LoaderContext, loader_context

    assert callable(marivo.help)
    with loader_context(LoaderContext()):
        finalized = ms.finalize()
    marivo.help("semantic.FinalizedDeclarations.show")
    expected = capsys.readouterr().out
    for operation in (FinalizedDeclarations.show, finalized.show):
        marivo.help(operation)
        assert capsys.readouterr().out == expected
    assert "finalized.show()" in expected
    assert len(expected.splitlines()) <= 72 and len(expected) <= 7000
    distances = {"authoring": 0}
    queue = deque(("authoring",))
    while queue:
        node = queue.popleft()
        if node not in REGISTRY.canonical_ids():
            continue
        for route in REGISTRY.routes(node):
            target = route.canonical_id
            if route.surface == "semantic" and target is not None and target not in distances:
                distances[target] = distances[node] + 1
                queue.append(target)
    for target in ("Measure.show", "FinalizedDeclarations.show"):
        assert distances[target] <= 3
