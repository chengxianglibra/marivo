"""Semantic live-help target resolution and runtime behavior."""

from __future__ import annotations

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
def test_all_authored_value_policy_variants_have_exact_help(
    target: str, callable_value: object
) -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    for query in (target, callable_value):
        resolved = resolve_live_target(query, SEMANTIC_LIVE_SURFACE)
        assert resolved.canonical_id == target


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
    surface: str,
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
    with pytest.raises(MarivoHelpTargetError):
        marivo.help(object())


def test_help_rejects_private_callable_owner_string() -> None:
    with pytest.raises(MarivoHelpTargetError):
        marivo.help("_authoring_declarations.metric")


def test_loaded_entry_help_is_reference_briefing_without_runtime_effects(
    authoring_evidence_project: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    catalog = ms.load()
    entry = catalog.require(ms.ref.metric("sales.revenue"))

    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("reference help must not load or query")

    monkeypatch.setattr("marivo.semantic.reader.SemanticProject.load", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)

    resolved = resolve_live_target(entry, SEMANTIC_LIVE_SURFACE)
    assert resolved.kind == "reference_briefing"
    assert resolved.reference_id == "sales.revenue"
    assert marivo.help(entry) is None


def test_error_help_kind_depends_on_concrete_repair_target() -> None:
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
    assert contract.kind == "error_contract"
    assert error_class.kind == "error_contract"
    assert contract == error_class
    assert with_repair.repair is not None
    assert with_repair.repair.help_target == LiveHelpTarget(
        surface="analysis",
        canonical_id="observe",
    )


def test_live_help_performs_no_runtime_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("help must not perform runtime effects")

    monkeypatch.setattr("marivo.semantic.reader.SemanticProject.load", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)

    assert marivo.help() is None
    for target in ("semantic.load", ms.load, ms.SemanticCatalog):
        assert marivo.help(target) is None
