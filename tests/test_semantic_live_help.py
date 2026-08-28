"""Semantic live-help target and render contracts."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

import marivo
import marivo.analysis as mv
import marivo.semantic as ms
from marivo._authoring.model import AuthoringRepair
from marivo._help.model import MarivoHelpTargetError
from marivo.introspection.live.model import SURFACE_LIMITS, LiveHelpTarget
from marivo.refs import SemanticKind
from marivo.semantic.errors import SemanticLoadError, SemanticRuntimeError
from tests.shared_fixtures import rendered_help

_HELP_CALL_RE = re.compile(
    r'marivo\.help\("(analysis|datasource|semantic|ontology)\.'
    r'([A-Za-z_][A-Za-z0-9_.]*)"\)'
)


def _text(target: object | None = None) -> str:
    return rendered_help(target, owner="semantic")


def _rendered_routes(text: str) -> tuple[LiveHelpTarget, ...]:
    return tuple(
        dict.fromkeys(
            LiveHelpTarget(surface=surface, canonical_id=canonical_id)
            for surface, canonical_id in _HELP_CALL_RE.findall(text)
        )
    )


def _example_ref_path(kind: SemanticKind) -> str:
    if kind in {SemanticKind.DOMAIN, SemanticKind.DATASOURCE}:
        return "example"
    if kind in {
        SemanticKind.DIMENSION,
        SemanticKind.TIME_DIMENSION,
        SemanticKind.MEASURE,
    }:
        return f"example.subject.{kind.value}"
    return f"example.{kind.value}"


def test_root_help_reveals_current_environment() -> None:
    text = _text()
    assert f"Marivo: {marivo.__version__}" in text
    assert f"Python: {sys.executable}" in text
    assert f"Package: {Path(marivo.__file__).resolve()}" in text


def test_root_help_within_line_budget() -> None:
    text = _text()
    assert text.count("\n") + 1 <= SURFACE_LIMITS.root_help_max_lines
    assert len(text) <= SURFACE_LIMITS.root_help_max_codepoints


def test_root_help_is_a_compact_family_index() -> None:
    text = _text()
    for target in (
        "semantic.authoring",
        "semantic.load",
        "semantic.objects",
        "semantic.builders",
        "semantic.checks",
        "semantic.SemanticCatalog",
        "semantic.CatalogEntry",
    ):
        assert f'marivo.help("{target}")' in text
    assert "semantic.where" not in text
    assert "semantic.work_schedule" not in text


def test_root_help_uses_registry_owned_sections_without_expanding_leaves() -> None:
    text = _text()
    for section in ("Start", "Discover authoring contracts", "Current catalog"):
        assert section in text
    assert "Signature:" not in text
    assert "Example:" not in text


def test_help_text_none_returns_root() -> None:
    text = _text()
    assert "marivo.semantic" in text
    assert "Discover authoring contracts:" in text


def test_empty_string_is_not_a_hidden_root_alias() -> None:
    with pytest.raises(MarivoHelpTargetError):
        _text("")


def test_help_resolves_authoring_topic() -> None:
    text = _text("authoring")
    assert "authoring" in text
    for target in (
        "semantic.objects",
        "semantic.builders",
        "semantic.checks",
        "semantic.load",
        "semantic.SemanticCatalog",
        "ontology.authoring",
    ):
        assert f'marivo.help("{target}")' in text
    assert "Signature:" not in text


def test_object_index_routes_to_every_closed_semantic_kind() -> None:
    text = _text("objects")
    assert "Relationship overview:" in text
    for kind in set(SemanticKind) - {SemanticKind.DATASOURCE}:
        assert f'marivo.help("semantic.objects.{kind.value}")' in text
    assert "Signature:" not in text
    assert "Example:" not in text


def test_every_object_page_renders_registered_decisions_once_without_leaf_expansion() -> None:
    from marivo.semantic._capabilities.registry import REGISTRY

    for contract in REGISTRY.object_contracts:
        text = _text(contract.canonical_id)
        assert f"output: Ref[{contract.semantic_kind.value}]" in text
        assert f"catalog.{contract.catalog_collection}" in text
        assert "Construction modes:" in text
        assert "Signature:" not in text
        assert "Example:" not in text
        for decision in contract.decisions:
            assert text.count(decision.question) == 1
            assert decision.determine_from in text
            if decision.does_not_establish is not None:
                assert decision.does_not_establish in text
            if decision.encoding_status == "unsupported":
                assert decision.unsupported_reason in text


def test_metric_page_fits_shared_navigation_budget_with_all_structural_routes() -> None:
    from marivo.semantic._capabilities.registry import REGISTRY

    text = _text("objects.metric")
    routes = _rendered_routes(text)
    budget = REGISTRY.render_budget("navigation")

    assert len(routes) == len(REGISTRY.routes("objects.metric")) == 21
    assert len(routes) <= budget.max_outgoing_routes == 24
    assert "default: Count Entity rows." in text
    assert "escape_hatch: Use one restricted Ibis expression body." in text


def test_builder_and_check_navigation_route_by_need_without_signatures() -> None:
    builders = _text("builders")
    family = _text("builders.field_metric_support")
    checks = _text("checks")

    for target in (
        "semantic.ref",
        "semantic.ai_context",
        "semantic.builders.entity_history",
        "semantic.builders.temporal_parsing",
        "semantic.builders.field_metric_support",
        "semantic.builders.relationship_event",
        "semantic.builders.state_model",
        "semantic.builders.governed_temporal",
    ):
        assert f'marivo.help("{target}")' in builders
    for target in ("where", "semi_additive", "bind", "from_sql", "grain_to_date", "trailing"):
        assert f'marivo.help("semantic.{target}")' in family
    assert "Proves:" in checks
    assert "Does not prove:" in checks
    assert "load success != readiness != preview success != source health" in checks
    assert "Signature:" not in builders + family + checks


def test_every_rendered_descriptor_route_matches_registry_topology_and_resolves() -> None:
    from marivo._help.route import NativeHelpRoute, route_help_target
    from marivo.semantic._capabilities.registry import REGISTRY

    for descriptor in REGISTRY.help_descriptors:
        rendered = _rendered_routes(_text(descriptor.canonical_id))
        registered = REGISTRY.routes(descriptor.canonical_id)
        assert len(rendered) == len(registered)
        assert set(rendered) == set(registered)
        for target in rendered:
            qualified = f"{target.surface}.{target.canonical_id}"
            assert isinstance(route_help_target(qualified), NativeHelpRoute)


def test_rendered_root_routes_match_registry_sections_and_resolve() -> None:
    from marivo._help.route import NativeHelpRoute, route_help_target
    from marivo.semantic._capabilities.registry import REGISTRY

    registered = tuple(target for section in REGISTRY.root_sections for target in section.members)
    rendered = _rendered_routes(_text())

    assert rendered == registered
    for target in rendered:
        qualified = f"{target.surface}.{target.canonical_id}"
        assert isinstance(route_help_target(qualified), NativeHelpRoute)


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


def test_render_root_help_is_bounded_and_has_fingerprint() -> None:
    from marivo.semantic._capabilities.registry import REGISTRY
    from marivo.semantic._capabilities.render import render_root_help

    text = render_root_help()
    budget = REGISTRY.render_budget("root")
    assert "marivo.semantic" in text
    assert len(text.splitlines()) <= budget.max_lines
    assert len(text) <= budget.max_codepoints
    assert len(_rendered_routes(text)) <= budget.max_outgoing_routes
    assert text.count("  Example:") <= budget.max_examples_or_snippets == 0


def test_every_static_semantic_page_obeys_its_four_dimensional_budget() -> None:
    from marivo._authoring.model import AuthoringCapability
    from marivo.semantic._capabilities.registry import REGISTRY

    for descriptor in REGISTRY.help_descriptors:
        text = _text(descriptor.canonical_id)
        budget = REGISTRY.render_budget(REGISTRY.render_class(descriptor.canonical_id))
        assert len(text.splitlines()) <= budget.max_lines
        assert len(text) <= budget.max_codepoints
        assert len(_rendered_routes(text)) <= budget.max_outgoing_routes
        expected_examples = int(
            isinstance(descriptor, AuthoringCapability) and descriptor.minimal_example is not None
        )
        assert text.count("  Example:") == expected_examples
        assert expected_examples <= budget.max_examples_or_snippets


def test_semantic_budget_overflow_fails_for_every_dimension() -> None:
    from marivo.semantic._capabilities.registry import REGISTRY
    from marivo.semantic._capabilities.render import enforce_semantic_help_budget

    budget = REGISTRY.render_budget("navigation")
    with pytest.raises(RuntimeError, match="render budget exceeded"):
        enforce_semantic_help_budget(
            "\n".join("line" for _ in range(budget.max_lines + 1)),
            render_class="navigation",
            examples_or_snippets=0,
        )
    with pytest.raises(RuntimeError, match="render budget exceeded"):
        enforce_semantic_help_budget(
            "x" * (budget.max_codepoints + 1),
            render_class="navigation",
            examples_or_snippets=0,
        )
    routes = "\n".join(
        f'marivo.help("semantic.synthetic_{index}")'
        for index in range(budget.max_outgoing_routes + 1)
    )
    with pytest.raises(RuntimeError, match="outgoing-route budget"):
        enforce_semantic_help_budget(
            routes,
            render_class="navigation",
            examples_or_snippets=0,
        )
    with pytest.raises(RuntimeError, match="example/snippet budget"):
        enforce_semantic_help_budget(
            "bounded",
            render_class="navigation",
            examples_or_snippets=1,
        )


def test_semantic_live_surface_resolves_registered_callable() -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    resolved = resolve_live_target("authoring", SEMANTIC_LIVE_SURFACE)
    assert resolved.surface == "semantic"


def test_exact_ref_factory_leaf_reflects_bound_public_signature() -> None:
    text = _text("ref.metric")

    assert "Entrypoint: ms.ref.metric" in text
    assert "Signature: (path: 'str') -> 'Ref[MetricKind]'" in text
    assert "self" not in text
    assert "Output family: Ref[metric]" in text


def test_exact_source_check_leaf_reflects_closed_variant() -> None:
    text = _text("source_check.freshness")

    assert "Entrypoint: ms.source_check.freshness" in text
    assert "max_age: 'timedelta'" in text
    assert "Output family: FreshnessSourceCheck" in text
    assert "catalog.source_health" not in text


def test_relationship_routes_required_keys_to_join_key_builder() -> None:
    text = _text("relationship")

    assert "dependency: JoinKey (parameters: keys)" in text
    assert 'See also: marivo.help("semantic.join_on")' in text


def test_factory_pages_route_to_every_exact_leaf_without_expanding_signatures() -> None:
    ref_text = _text("ref")
    source_check_text = _text("source_check")

    for kind in SemanticKind:
        assert f'marivo.help("semantic.ref.{kind.value}")' in ref_text
    for method in (
        "not_null",
        "allowed_values",
        "unique",
        "freshness",
        "relationship_matches",
        "relationship_cardinality",
    ):
        assert f'marivo.help("semantic.source_check.{method}")' in source_check_text
    assert "Signature:" not in ref_text
    assert "Signature:" not in source_check_text


@pytest.mark.parametrize(
    ("parent_id", "retained_target", "excluded_target"),
    (
        ("ref", "ref.metric", "ref.dimension"),
        ("source_check", "source_check.freshness", "source_check.not_null"),
    ),
)
def test_factory_page_routes_follow_descriptor_membership(
    parent_id: str,
    retained_target: str,
    excluded_target: str,
) -> None:
    from marivo.semantic._capabilities.registry import REGISTRY
    from marivo.semantic._capabilities.render import _render_descriptor

    parent = REGISTRY.by_canonical_id(parent_id)
    narrowed = parent.model_copy(
        update={"see_also": (LiveHelpTarget(surface="semantic", canonical_id=retained_target),)}
    )
    text = _render_descriptor(narrowed)

    assert f'marivo.help("semantic.{retained_target}")' in text
    assert f'marivo.help("semantic.{excluded_target}")' not in text


def test_semantic_live_surface_rejects_cross_surface_target() -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    with pytest.raises(Exception):
        resolve_live_target(mv.Session, SEMANTIC_LIVE_SURFACE)


# ---------------------------------------------------------------------------
# Help target matrix — string, callable, type, error type, cross-surface
# rejections, unknown string, private object, no-runtime-effects.
# ---------------------------------------------------------------------------


def test_help_resolves_string_target() -> None:
    text = _text("load")
    assert "load" in text
    assert "catalog = ms.load()" in text
    assert "catalog.show()" in text


def test_help_resolves_callable_target() -> None:
    text = _text(ms.load)
    assert "load" in text


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
    assert _text(target).startswith("preview\n")


def test_where_is_registered_help_target_and_count_teaches_filter() -> None:
    """ms.where is a public primitive and must be a registered help target; count
    and aggregate must teach filter=ms.where(...). See MR !29 review (help).
    """
    where_text = _text("where")
    assert "where" in where_text
    assert "ms.where" in where_text
    assert "tuple/list values mean membership" in where_text
    assert "ms.where(type=(2, 4), query_kind='Select')" in where_text
    assert "filter_condition_valid" in where_text

    count_text = _text("count")
    assert "filter" in count_text.lower()
    assert "ms.where" in count_text

    aggregate_text = _text("aggregate")
    assert "filter" in aggregate_text.lower()
    assert "ms.where" in aggregate_text
    assert "TimeFold optional" in aggregate_text
    assert "time_fold_valid" in aggregate_text
    assert "time_fold_requires_semi_additive" in aggregate_text


def test_help_resolves_type_target() -> None:
    text = _text(ms.SemanticCatalog)
    assert "SemanticCatalog" in text


def test_catalog_collection_help_teaches_displayed_typed_key_lookup() -> None:
    text = _text(ms.CatalogCollection)

    assert "displayed same-kind typed key" in text
    assert "catalog.metrics.get('metric:sales.revenue')" in text
    assert "marivo.help(entry)" in text
    assert "entry or entry.ref" in text
    assert "call .show() for bounded readable state" in text


def test_root_and_ref_help_teach_entry_runtime_and_ref_identity_handoffs() -> None:
    root = _text()
    focused = _text(ms.Ref)

    assert "CatalogEntry" in root
    assert 'marivo.help("semantic.CatalogEntry")' in root
    assert 'marivo.help("semantic.ref")' not in root
    assert "entry = catalog.metrics.get('sales.revenue')" in focused
    assert "metric_ref = entry.ref" in focused
    assert "catalog.require(ref) resolves the exact ref" in focused
    assert "marivo.help(ref) reports identity only" in focused
    assert "ms.bind(field_ref, entity_alias)" in focused
    assert "semantic.bind" not in root

    entry_help = _text(ms.CatalogEntry)
    assert "marivo.help(entry) combines current details" in entry_help

    factory = _text(ms.ref)
    assert factory.startswith("ref\n")
    assert "ms.ref.<kind>(path)" in factory
    assert _text("ref") == factory

    bind = _text(ms.bind)
    assert "ms.bind(amount, orders)" in bind


@pytest.mark.parametrize("target", ["preview", "preview_many", "source_health", "readiness"])
def test_runtime_help_uses_public_semantic_input_name(target: str) -> None:
    text = _text(target)
    assert "_SemanticInput" not in text
    assert "SemanticInput" in text


def test_help_resolves_error_type_target() -> None:
    text = _text(SemanticLoadError)
    assert "SemanticLoadError" in text


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
    assert f'Next help: marivo.help("{surface}.{canonical_id}")' in _text(error)


def test_temporal_catalog_lookup_rejects_an_unregistered_operation() -> None:
    from marivo.semantic.catalog import _raise_period_lookup

    with pytest.raises(RuntimeError, match="unsupported period-calendar lookup operation"):
        _raise_period_lookup(
            ms.ref.period_calendar("sales.fiscal"),
            "synthetic",
            "probe",
            details={},
        )


def test_preview_result_type_string_and_instance_share_one_static_contract() -> None:
    from marivo.preview import PreviewResult

    instance = object.__new__(PreviewResult)
    by_type = _text(PreviewResult)
    by_string = _text("PreviewResult")
    by_instance = _text(instance)

    assert by_type == by_string == by_instance
    assert "Producers: preview" in by_type
    assert "Public fields: kind, ref, columns, types, rows" in by_type
    assert "Public consumption: show, render" in by_type


def test_help_rejects_unknown_string() -> None:
    with pytest.raises(MarivoHelpTargetError) as exc_info:
        _text("nonexistent_target")
    assert exc_info.value.outcome == "unknown"


@pytest.mark.parametrize(
    "target",
    ("lifecycle", "ontology", "verify", "decisions", "objects.metric.population_value"),
)
def test_progressive_semantic_help_adds_no_alias_or_decision_target(target: str) -> None:
    with pytest.raises(MarivoHelpTargetError):
        _text(target)


def test_help_rejects_private_object() -> None:
    with pytest.raises(MarivoHelpTargetError):
        _text(object())


def test_help_rejects_private_callable_owner_string() -> None:
    with pytest.raises(MarivoHelpTargetError):
        _text("_authoring_declarations.metric")


def test_ref_help_resolves_to_object_near_reference_briefing() -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    ref = ms.ref.metric("sales.revenue")
    resolved = resolve_live_target(ref, SEMANTIC_LIVE_SURFACE)

    assert resolved.kind == "reference_briefing"
    assert resolved.reference_id == "sales.revenue"
    text = _text(ref)
    assert "metric: sales.revenue" in text
    assert "entry = catalog.require(ref)" in text
    assert "entry.details().show()" not in text
    assert "catalog.readiness" not in text
    assert "observe" not in text
    assert "preview" not in text


def test_every_ref_kind_remains_identity_only_and_bounded() -> None:
    from marivo.semantic._capabilities.registry import REGISTRY

    budget = REGISTRY.render_budget("current_briefing")
    for kind in SemanticKind:
        path = _example_ref_path(kind)
        ref = getattr(ms.ref, kind.value)(path)
        text = _text(ref)
        assert f"{kind.value}: {path}" in text
        assert "entry = catalog.require(ref)" in text
        assert 'marivo.help("semantic.Ref")' in text
        assert "catalog.readiness" not in text
        assert "Analysis handoff" not in text
        assert "Scoped preview" not in text
        assert len(text.splitlines()) <= budget.max_lines
        assert len(text) <= budget.max_codepoints
        assert len(_rendered_routes(text)) <= budget.max_outgoing_routes


def test_every_catalog_entry_kind_uses_object_contract_runtime_routes_without_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marivo.semantic.catalog as catalog_module
    from marivo.semantic._capabilities.catalog_members import CATALOG_MEMBER_CONTRACTS
    from marivo.semantic._capabilities.registry import REGISTRY

    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("entry help must not load or query")

    monkeypatch.setattr("marivo.semantic.reader.SemanticProject.load", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)
    budget = REGISTRY.render_budget("current_briefing")

    for member in CATALOG_MEMBER_CONTRACTS:
        entry_type = getattr(catalog_module, member.entry_type_name)
        ref = getattr(ms.ref, member.kind.value)(_example_ref_path(member.kind))
        entry = entry_type(ref=ref, _details=object(), _catalog=object())
        text = _text(entry)
        assert f"Object: {member.entry_type_name}" in text
        assert "entry.show()" in text
        assert "entry.details().show()" in text
        assert "catalog.readiness(refs=[entry]).show()" in text

        try:
            object_contract = REGISTRY.object_contract(member.kind)
        except KeyError:
            expected_runtime_targets: set[str] = set()
        else:
            expected_runtime_targets = {
                target.canonical_id
                for target in object_contract.check_targets
                if target.canonical_id in {"preview", "source_health"}
            }
        for target in ("preview", "source_health"):
            invocation = f'marivo.help("semantic.{target}")'
            assert (invocation in text) is (target in expected_runtime_targets)

        assert len(text.splitlines()) <= budget.max_lines
        assert len(text) <= budget.max_codepoints
        assert len(_rendered_routes(text)) <= budget.max_outgoing_routes


def test_loaded_entry_help_is_reference_briefing_without_runtime_effects(
    authoring_evidence_project: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.render import render_help_target
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
    text = _text(entry)
    native_text = render_help_target(resolved, original_target=entry)
    for fact in (
        "metric: sales.revenue",
        "Object: MetricEntry",
        "entry.details().show()",
        'marivo.help("semantic.preview")',
        "Readiness is not inferred here",
    ):
        assert fact in native_text
        assert fact in text
    assert "Analysis handoff" not in native_text
    assert "Object: MetricEntry" in text
    assert "metric: sales.revenue" in text
    assert "Object-near inspection:" in text
    assert 'marivo.help("semantic.preview")' in text
    assert 'marivo.help("semantic.source_health")' not in text
    assert "Analysis handoff (kind-level" in text
    assert "session.observe(...) -> MetricFrame" in text
    assert 'marivo.help("analysis.observe")' in text
    assert "result.contract().show()" in text
    assert "Readiness is not inferred here" in text
    from marivo.semantic._capabilities.registry import REGISTRY

    budget = REGISTRY.render_budget("current_briefing")
    assert len(text.splitlines()) <= budget.max_lines
    assert len(text) <= budget.max_codepoints
    assert len(_rendered_routes(text)) <= budget.max_outgoing_routes


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
    text = _text(with_repair)
    assert "Kind: retry" in text
    assert "Expected: one loaded domain" in text
    assert "Received: no domains" in text
    assert "Location: semantic project" in text
    assert 'Next help: marivo.help("analysis.observe")' in text
    assert 'marivo.help("analysis.observe")' in text
    assert "Candidates: observe" in text
    from marivo.semantic._capabilities.registry import REGISTRY

    budget = REGISTRY.render_budget("current_briefing")
    assert len(text.splitlines()) <= budget.max_lines
    assert len(text) <= budget.max_codepoints
    assert len(_rendered_routes(text)) <= budget.max_outgoing_routes


def test_live_help_performs_no_runtime_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("help must not perform runtime effects")

    monkeypatch.setattr("marivo.semantic.reader.SemanticProject.load", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)

    assert _text()
    for target in ("load", ms.load, ms.SemanticCatalog):
        assert _text(target)
