"""Semantic live capability registry invariants."""

from __future__ import annotations

import inspect
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from types import MappingProxyType
from typing import get_args

import pytest

from marivo._authoring.model import AuthoringCapability
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import SemanticKind
from marivo.semantic._capabilities.catalog_members import CATALOG_COLLECTION_PROPERTIES
from marivo.semantic._capabilities.model import (
    SEMANTIC_HELP_RENDER_BUDGETS,
    AuthoringSourceContract,
    ConstructionMode,
    SemanticBuilderTopic,
    SemanticCapabilityRegistry,
    SemanticCheckRoute,
    SemanticCheckTopic,
    SemanticHelpDescriptor,
    SemanticHelpRenderBudget,
    SemanticHelpRenderClass,
    SemanticNavigationRoute,
    SemanticNavigationTopic,
    SemanticObjectContract,
    SemanticObjectDecision,
    SemanticObjectIndexEntry,
    SemanticObjectRelationship,
    SemanticRepairContract,
    SemanticRootSection,
    SemanticTypeContract,
)
from marivo.semantic._capabilities.registry import (
    ERROR_TYPES,
    REGISTRY,
    TYPE_CONTRACTS,
    _finalize_registry,
    _validate_registry,
)
from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE


def _semantic_target(canonical_id: str) -> LiveHelpTarget:
    return LiveHelpTarget(surface="semantic", canonical_id=canonical_id)


def _finalize_fixture(
    descriptors: tuple[AuthoringCapability, ...] | None = None,
    *,
    help_descriptors: tuple[SemanticHelpDescriptor, ...] | None = None,
    object_contracts: tuple[SemanticObjectContract, ...] | None = None,
    root_sections: tuple[SemanticRootSection, ...] | None = None,
    repair_contracts: Mapping[str, SemanticRepairContract] | None = None,
    render_budgets: Mapping[SemanticHelpRenderClass, SemanticHelpRenderBudget] | None = None,
) -> SemanticCapabilityRegistry:
    active_descriptors = REGISTRY.descriptors if descriptors is None else descriptors
    active_objects = REGISTRY.object_contracts if object_contracts is None else object_contracts
    if help_descriptors is None:
        navigation_rows: list[SemanticHelpDescriptor] = []
        for descriptor in REGISTRY.help_descriptors:
            if isinstance(descriptor, (AuthoringCapability, SemanticObjectContract)):
                continue
            if (
                isinstance(descriptor, SemanticNavigationTopic)
                and descriptor.canonical_id == "objects"
            ):
                descriptor = replace(
                    descriptor,
                    members=tuple(
                        SemanticObjectIndexEntry(contract) for contract in active_objects
                    ),
                )
            navigation_rows.append(descriptor)
        navigation = tuple(navigation_rows)
        help_descriptors = (*active_descriptors, *navigation, *active_objects)
    return _finalize_registry(
        active_descriptors,
        root_sections=(REGISTRY.root_sections if root_sections is None else root_sections),
        source_contracts=REGISTRY._source_contracts,
        repair_contracts=(
            REGISTRY._repair_contracts if repair_contracts is None else repair_contracts
        ),
        help_descriptors=help_descriptors,
        object_contracts=active_objects,
        render_budgets=(SEMANTIC_HELP_RENDER_BUDGETS if render_budgets is None else render_budgets),
    )


def test_semantic_surface_uses_the_native_registry_without_copying() -> None:
    assert SEMANTIC_LIVE_SURFACE.registry is REGISTRY
    for canonical_id in REGISTRY.canonical_ids():
        native = REGISTRY.by_canonical_id(canonical_id)
        assert SEMANTIC_LIVE_SURFACE.registry.by_canonical_id(canonical_id) is native


def test_registry_surface_is_semantic() -> None:
    assert isinstance(REGISTRY, SemanticCapabilityRegistry)
    assert REGISTRY.surface == "semantic"


def test_semantic_help_descriptor_union_is_closed() -> None:
    assert set(get_args(SemanticHelpDescriptor)) == {
        AuthoringCapability,
        SemanticNavigationTopic,
        SemanticBuilderTopic,
        SemanticCheckTopic,
        SemanticObjectContract,
    }


def test_semantic_help_render_classes_and_budgets_are_closed() -> None:
    assert set(get_args(SemanticHelpRenderClass)) == {
        "root",
        "decision_hub",
        "navigation",
        "exact_contract",
        "current_briefing",
    }
    expected = {
        "root": (32, 3_000, 10, 0),
        "decision_hub": (40, 4_000, 8, 0),
        "navigation": (64, 6_000, 24, 0),
        "exact_contract": (72, 7_000, 8, 1),
        "current_briefing": (64, 6_000, 6, 1),
    }
    assert {
        render_class: (
            budget.max_lines,
            budget.max_codepoints,
            budget.max_outgoing_routes,
            budget.max_examples_or_snippets,
        )
        for render_class, budget in REGISTRY.render_budgets.items()
    } == expected
    assert REGISTRY.render_budgets is not SEMANTIC_HELP_RENDER_BUDGETS
    with pytest.raises(TypeError):
        REGISTRY.render_budgets["root"] = SemanticHelpRenderBudget(1, 1, 1, 0)  # type: ignore[index]


def test_native_descriptor_models_are_frozen_and_non_invokable() -> None:
    target = _semantic_target("load")
    route = SemanticCheckRoute(
        question="Does the project load?",
        targets=(target,),
        proves="Static project assembly succeeds.",
        does_not_prove="The datasource is healthy.",
    )
    decision = SemanticObjectDecision(
        decision_id="identity",
        question="What does this object identify?",
        determine_from="Current business authority.",
        basis="business_authority",
        encoding_status="supported",
        next_targets=(target,),
    )
    relationship = SemanticObjectRelationship(
        relation="consumed_by",
        target=target,
        explanation="The loader consumes the declaration.",
    )
    descriptors: tuple[SemanticHelpDescriptor, ...] = (
        SemanticNavigationTopic(
            "objects",
            "Browse object kinds.",
            (SemanticNavigationRoute("load", target),),
        ),
        SemanticBuilderTopic("builders.values", "Values", "Build values.", (target,)),
        SemanticCheckTopic("checks", "Choose a check.", (route,)),
        SemanticObjectContract(
            canonical_id="objects.domain",
            summary="A governed business namespace.",
            semantic_kind=SemanticKind.DOMAIN,
            ref_target=target,
            catalog_collection="domains",
            placement_kind="domain_entrypoint",
            decisions=(decision,),
            construction_modes=(ConstructionMode("Declare a domain.", "default", target),),
            relationships=(relationship,),
            supporting_targets=(),
            check_targets=(target,),
        ),
    )

    for descriptor in descriptors:
        assert descriptor.canonical_id
        assert descriptor.summary
        assert descriptor.public_entrypoint is None
        assert descriptor.callable_path is None
        with pytest.raises(FrozenInstanceError):
            descriptor.summary = "changed"  # type: ignore[misc]


def test_slice3_activates_native_navigation_and_object_descriptors() -> None:
    assert REGISTRY.help_descriptors != REGISTRY.descriptors
    assert {
        "authoring",
        "objects",
        "builders",
        "checks",
        *(contract.canonical_id for contract in REGISTRY.object_contracts),
    } <= set(REGISTRY.canonical_ids())
    assert len(REGISTRY.object_contracts) == 12
    assert all(contract in REGISTRY.help_descriptors for contract in REGISTRY.object_contracts)
    assert len(REGISTRY.object_contracts) == 12


REQUIRED_DECISION_IDS = MappingProxyType(
    {
        SemanticKind.DOMAIN: frozenset(
            {
                "business_boundary",
                "accountable_owner",
                "default_domain_behavior",
                "definition_guardrails",
            }
        ),
        SemanticKind.ENTITY: frozenset(
            {
                "recordset_meaning",
                "authoritative_source",
                "row_grain",
                "identity_key",
                "history_as_of_model",
                "domain_ownership",
            }
        ),
        SemanticKind.DIMENSION: frozenset(
            {"owning_entity", "dimension_meaning", "code_null_semantics", "construction_mode"}
        ),
        SemanticKind.TIME_DIMENSION: frozenset(
            {
                "owning_entity",
                "business_time_role",
                "granularity",
                "physical_time_encoding",
                "default_axis",
                "sampled_cadence",
            }
        ),
        SemanticKind.MEASURE: frozenset(
            {
                "numeric_fact_grain",
                "unit",
                "dimensional_additivity",
                "temporal_additivity",
                "semi_additive_axis_fold",
                "construction_mode",
            }
        ),
        SemanticKind.METRIC: frozenset(
            {
                "population_value",
                "construction_mode",
                "aggregation_filter",
                "denominator_failure",
                "root_fanout",
                "unit_additivity",
                "temporal_behavior",
                "provenance",
                "guardrails",
            }
        ),
        SemanticKind.RELATIONSHIP: frozenset(
            {
                "directed_meaning",
                "endpoint_grains",
                "join_key_equivalence",
                "multiplicity_fanout",
                "evidence_checks",
            }
        ),
        SemanticKind.EVENT: frozenset(
            {
                "occurrence_predicate",
                "occurrence_identity",
                "occurrence_time",
                "participant_roles",
                "directed_paths",
                "participant_cardinality",
            }
        ),
        SemanticKind.STATE_MODEL: frozenset(
            {
                "subject_lifecycle",
                "state_vocabulary",
                "initial_terminal_meaning",
                "inception_transitions",
                "deterministic_transitions",
                "excluded_replay_policies",
            }
        ),
        SemanticKind.PERIOD_CALENDAR: frozenset(
            {
                "calendar_convention",
                "civil_date_authority",
                "boundary_timezone",
                "finite_coverage",
                "level_key_meaning",
                "containment_expectations",
                "correspondence_conventions",
            }
        ),
        SemanticKind.TEMPORAL_SET: frozenset(
            {
                "occurrence_set_meaning",
                "occurrence_identity",
                "half_open_bounds",
                "temporal_encoding",
                "boundary_timezone",
                "finite_coverage",
                "category",
                "overlap_gap_semantics",
            }
        ),
        SemanticKind.WORK_SCHEDULE: frozenset(
            {
                "working_status_authority",
                "date_boolean_meaning",
                "boundary_timezone",
                "finite_coverage",
                "rule_precedence",
            }
        ),
    }
)


def test_object_contracts_cover_closed_kinds_and_pinned_decisions() -> None:
    assert set(REQUIRED_DECISION_IDS) == set(SemanticKind) - {SemanticKind.DATASOURCE}
    assert {contract.semantic_kind for contract in REGISTRY.object_contracts} == set(
        REQUIRED_DECISION_IDS
    )
    for contract in REGISTRY.object_contracts:
        assert {decision.decision_id for decision in contract.decisions} == REQUIRED_DECISION_IDS[
            contract.semantic_kind
        ]


def test_object_construction_modes_return_the_owning_ref_kind() -> None:
    for contract in REGISTRY.object_contracts:
        expected = f"Ref[{contract.semantic_kind.value}]"
        assert (
            REGISTRY.by_canonical_id(contract.ref_target.canonical_id or "").output_family
            == expected
        )
        for mode in contract.construction_modes:
            descriptor = REGISTRY.by_canonical_id(mode.target.canonical_id or "")
            assert descriptor.output_family == expected
        assert not {mode.target for mode in contract.construction_modes} & set(
            contract.supporting_targets
        )


def test_state_model_replay_policy_is_the_only_unencoded_decision() -> None:
    unsupported = {
        (contract.semantic_kind, decision.decision_id)
        for contract in REGISTRY.object_contracts
        for decision in contract.decisions
        if decision.encoding_status == "unsupported"
    }
    assert unsupported == {(SemanticKind.STATE_MODEL, "excluded_replay_policies")}


def test_registry_rejects_missing_required_object_kind_eagerly() -> None:
    with pytest.raises(ValueError, match="must cover every non-datasource kind"):
        _finalize_fixture(object_contracts=REGISTRY.object_contracts[:-1])


def test_registry_rejects_object_construction_output_drift_eagerly() -> None:
    metric = REGISTRY.object_contract(SemanticKind.METRIC)
    invalid_mode = replace(
        metric.construction_modes[0],
        target=_semantic_target("dimension_column"),
    )
    invalid = replace(metric, construction_modes=(invalid_mode, *metric.construction_modes[1:]))
    contracts = tuple(
        invalid if contract is metric else contract for contract in REGISTRY.object_contracts
    )
    with pytest.raises(ValueError, match="construction output-kind drift"):
        _finalize_fixture(object_contracts=contracts)


def test_registry_rejects_missing_object_decision_target_eagerly() -> None:
    domain = REGISTRY.object_contract(SemanticKind.DOMAIN)
    decision = replace(domain.decisions[0], next_targets=(_semantic_target("preview"),))
    invalid = replace(domain, decisions=(decision, *domain.decisions[1:]))
    contracts = tuple(
        invalid if contract is domain else contract for contract in REGISTRY.object_contracts
    )
    with pytest.raises(ValueError, match="decision target escapes its owner"):
        _finalize_fixture(object_contracts=contracts)


def test_registry_rejects_object_catalog_mapping_drift_eagerly() -> None:
    metric = REGISTRY.object_contract(SemanticKind.METRIC)
    invalid = replace(metric, catalog_collection="dimensions")
    contracts = tuple(
        invalid if contract is metric else contract for contract in REGISTRY.object_contracts
    )
    with pytest.raises(ValueError, match="object catalog collection drift"):
        _finalize_fixture(object_contracts=contracts)


def test_registry_rejects_object_ref_kind_mapping_drift_eagerly() -> None:
    metric = REGISTRY.object_contract(SemanticKind.METRIC)
    invalid = replace(metric, ref_target=_semantic_target("ref.dimension"))
    contracts = tuple(
        invalid if contract is metric else contract for contract in REGISTRY.object_contracts
    )
    with pytest.raises(ValueError, match="object ref target drift"):
        _finalize_fixture(object_contracts=contracts)


def test_registry_rejects_unknown_root_route_eagerly() -> None:
    start = REGISTRY.root_sections[0]
    invalid = replace(
        start,
        members=(*start.members, _semantic_target("synthetic.missing")),
    )
    with pytest.raises(ValueError, match="unknown semantic Help route"):
        _finalize_fixture(root_sections=(invalid, *REGISTRY.root_sections[1:]))


def test_registry_rejects_multiple_discovery_owners_eagerly() -> None:
    builders = REGISTRY.by_canonical_id("builders")
    assert isinstance(builders, SemanticNavigationTopic)
    invalid = replace(
        builders,
        members=(*builders.members, SemanticNavigationRoute("load", _semantic_target("load"))),
    )
    help_descriptors = tuple(
        invalid if descriptor is builders else descriptor
        for descriptor in REGISTRY.help_descriptors
    )
    with pytest.raises(ValueError, match="multiple semantic discovery owners: load"):
        _finalize_fixture(help_descriptors=help_descriptors)


def test_navigation_labels_remain_bound_to_targets_when_teaching_order_changes() -> None:
    from marivo.semantic._capabilities.render import _render_navigation_topic

    authoring = REGISTRY.by_canonical_id("authoring")
    assert isinstance(authoring, SemanticNavigationTopic)
    reordered = replace(
        authoring,
        members=(authoring.members[1], authoring.members[0], *authoring.members[2:]),
    )
    help_descriptors = tuple(
        reordered if descriptor is authoring else descriptor
        for descriptor in REGISTRY.help_descriptors
    )

    _finalize_fixture(help_descriptors=help_descriptors)
    text = _render_navigation_topic(reordered, render_class="decision_hub")

    assert 'supporting parameter or handle: marivo.help("semantic.builders")' in text
    assert 'object meaning and construction: marivo.help("semantic.objects")' in text


def test_registry_rejects_public_constructor_without_discovery_owner() -> None:
    dimension = REGISTRY.object_contract(SemanticKind.DIMENSION)
    trimmed = replace(
        dimension,
        construction_modes=tuple(
            mode for mode in dimension.construction_modes if mode.target.canonical_id != "dimension"
        ),
        decisions=tuple(
            replace(
                decision,
                next_targets=tuple(
                    target for target in decision.next_targets if target.canonical_id != "dimension"
                ),
            )
            for decision in dimension.decisions
        ),
    )
    contracts = tuple(
        trimmed if contract is dimension else contract for contract in REGISTRY.object_contracts
    )

    with pytest.raises(ValueError, match="semantic discovery owner missing: dimension"):
        _finalize_fixture(object_contracts=contracts)


def test_registry_rejects_dead_cross_surface_object_relationship_eagerly() -> None:
    entity = REGISTRY.object_contract(SemanticKind.ENTITY)
    relationships = tuple(
        replace(
            relationship,
            target=LiveHelpTarget(surface="datasource", canonical_id="synthetic.missing"),
        )
        if relationship.target.surface == "datasource"
        else relationship
        for relationship in entity.relationships
    )
    assert relationships != entity.relationships
    invalid = replace(entity, relationships=relationships)
    contracts = tuple(
        invalid if contract is entity else contract for contract in REGISTRY.object_contracts
    )
    with pytest.raises(ValueError, match="unknown datasource object relationship: entity"):
        _finalize_fixture(object_contracts=contracts)


def test_registry_rejects_missing_ref_factory_leaf_eagerly() -> None:
    descriptors = tuple(
        descriptor
        for descriptor in REGISTRY.descriptors
        if descriptor.canonical_id != "ref.datasource"
    )
    with pytest.raises(ValueError, match="semantic ref factory target is not exact"):
        _finalize_fixture(descriptors=descriptors)


def test_signature_parameter_drift_is_detected_adversarially() -> None:
    from marivo.introspection.live.reflect import import_registered_callable
    from marivo.semantic._capabilities.validation import _validate_parameter_metadata

    descriptor = REGISTRY.by_canonical_id("entity")
    first = descriptor.input_requirements[0].model_copy(
        update={"parameter_names": ("removed_parameter",)}
    )
    invalid = descriptor.model_copy(
        update={"input_requirements": (first, *descriptor.input_requirements[1:])}
    )
    with pytest.raises(AssertionError, match="missing live parameter"):
        _validate_parameter_metadata(invalid, import_registered_callable(descriptor.callable_path))


def test_output_family_drift_is_detected_adversarially() -> None:
    from marivo.introspection.live.reflect import import_registered_callable
    from marivo.semantic._capabilities.validation import _validate_output_metadata

    descriptor = REGISTRY.by_canonical_id("dimension_column")
    invalid = descriptor.model_copy(update={"output_family": "Ref[metric]"})
    with pytest.raises(AssertionError, match="output family drift"):
        _validate_output_metadata(invalid, import_registered_callable(descriptor.callable_path))


def test_every_active_descriptor_has_one_registry_owned_render_class() -> None:
    assert set(REGISTRY._render_classes) == set(REGISTRY.canonical_ids())
    assert REGISTRY.render_class("authoring") == "decision_hub"
    navigation_ids = {
        descriptor.canonical_id
        for descriptor in REGISTRY.help_descriptors
        if isinstance(
            descriptor,
            (
                SemanticNavigationTopic,
                SemanticBuilderTopic,
                SemanticCheckTopic,
                SemanticObjectContract,
            ),
        )
        and descriptor.canonical_id != "authoring"
    } | {"ref", "source_check"}
    assert all(
        REGISTRY.render_class(canonical_id) == "navigation" for canonical_id in navigation_ids
    )
    assert all(
        REGISTRY.render_class(canonical_id) == "exact_contract"
        for canonical_id in set(REGISTRY.canonical_ids()) - navigation_ids - {"authoring"}
    )


def test_registry_canonical_ids_are_unique() -> None:
    ids = REGISTRY.canonical_ids()
    assert len(ids) == len(set(ids))


def test_registry_rejects_duplicate_help_canonical_ids_eagerly() -> None:
    duplicate = REGISTRY.descriptors[0].model_copy()
    with pytest.raises(ValueError, match="duplicate semantic help canonical id"):
        _finalize_fixture((*REGISTRY.descriptors, duplicate))


def test_registry_rejects_duplicate_callable_paths_eagerly() -> None:
    first = next(
        descriptor for descriptor in REGISTRY.descriptors if descriptor.callable_path is not None
    )
    duplicate = first.model_copy(update={"canonical_id": "synthetic.duplicate_callable"})
    with pytest.raises(ValueError, match="duplicate semantic callable path"):
        _finalize_fixture((*REGISTRY.descriptors, duplicate))


@pytest.mark.parametrize("extra", (False, True))
def test_registry_requires_exact_render_budget_coverage(extra: bool) -> None:
    budgets: dict[object, SemanticHelpRenderBudget] = dict(REGISTRY.render_budgets)
    if extra:
        budgets["unknown"] = SemanticHelpRenderBudget(1, 1, 1, 0)
    else:
        budgets.pop("navigation")
    with pytest.raises(ValueError, match="budgets must cover every render class"):
        _finalize_fixture(render_budgets=budgets)  # type: ignore[arg-type]


def test_registry_rejects_non_positive_render_budget() -> None:
    budgets = dict(REGISTRY.render_budgets)
    budgets["navigation"] = replace(budgets["navigation"], max_lines=0)
    with pytest.raises(ValueError, match="invalid semantic Help render budget"):
        _finalize_fixture(render_budgets=budgets)


def test_registry_rejects_missing_render_assignment_before_rendering() -> None:
    render_classes = dict(REGISTRY._render_classes)
    render_classes.pop("authoring")
    invalid = replace(REGISTRY, _render_classes=MappingProxyType(render_classes))
    with pytest.raises(ValueError, match="assignments must cover every descriptor"):
        _validate_registry(invalid)


@pytest.mark.parametrize("field_name", ("public_entrypoint", "callable_path"))
def test_registry_rejects_invokable_navigation_before_rendering(field_name: str) -> None:
    topic = SemanticNavigationTopic(
        canonical_id="synthetic.navigation",
        summary="Synthetic navigation.",
        members=(SemanticNavigationRoute("load", _semantic_target("load")),),
    )
    object.__setattr__(topic, field_name, "marivo.semantic.load")
    with pytest.raises(ValueError, match="navigation descriptor must not be invokable"):
        _finalize_fixture(help_descriptors=(*REGISTRY.help_descriptors, topic))


def test_registry_root_sections_are_registered_and_ordered() -> None:
    assert tuple(section.section_id for section in REGISTRY.root_sections) == (
        "start",
        "discover_authoring",
        "current_catalog",
    )
    assert tuple(section.label for section in REGISTRY.root_sections) == (
        "Start",
        "Discover authoring contracts",
        "Current catalog",
    )


def test_type_contract_type_is_dataclass() -> None:
    import dataclasses

    assert dataclasses.is_dataclass(SemanticTypeContract)


def test_validate_semantic_live_surface_passes() -> None:
    from marivo.semantic._capabilities.validation import validate_semantic_live_surface

    validate_semantic_live_surface()


def test_ontology_cold_import_does_not_reenter_semantic_registry() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import marivo.ontology"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_every_registry_repair_routes_to_a_public_exact_target() -> None:
    from marivo._help.model import NativeHelpRoute
    from marivo._help.route import route_help_target
    from marivo.semantic.errors import ErrorKind

    expected = {
        "outside_loader_context": "semantic.authoring",
        "missing_domain": "semantic.objects.domain",
        "invalid_filter": "semantic.where",
        "filter_value_runtime_incompatible": "semantic.where",
        "invalid_project": "semantic.authoring",
        "domain_file_missing": "semantic.objects.domain",
        "domain_file_mismatch": "semantic.domain",
        "organization_error": "semantic.authoring",
    }
    actual = {
        error_kind: (f"{contract.help_target.surface}.{contract.help_target.canonical_id}")
        for error_kind, contract in REGISTRY._repair_contracts.items()
    }

    assert actual == expected
    assert set(REGISTRY._repair_contracts) <= {kind.value for kind in ErrorKind}
    for target in actual.values():
        assert isinstance(route_help_target(target), NativeHelpRoute)


def test_registry_rejects_unknown_or_over_broad_repair_targets() -> None:
    missing_domain = REGISTRY._repair_contracts["missing_domain"]

    for target, message in (
        (_semantic_target("synthetic.missing"), "unknown semantic Help route"),
        (_semantic_target("objects"), "semantic repair target is over-broad"),
    ):
        repair_contract = replace(missing_domain, help_target=target)
        repairs = dict(REGISTRY._repair_contracts)
        repairs[repair_contract.error_kind] = repair_contract
        with pytest.raises(ValueError, match=message):
            _finalize_fixture(repair_contracts=repairs)


def test_registry_covers_all_public_callables() -> None:
    import marivo.semantic as ms

    for name in ms.__all__:
        exported = getattr(ms, name)
        if inspect.isroutine(exported) and not isinstance(exported, type):
            assert REGISTRY.by_callable(exported), (
                f"{name} is not registered in the semantic registry"
            )


def test_ref_factory_namespace_and_every_exact_method_share_registry_contracts() -> None:
    import marivo.semantic as ms
    from marivo.introspection.live.resolve import resolve_live_target

    resolved = resolve_live_target(ms.ref, SEMANTIC_LIVE_SURFACE)
    assert resolved.descriptor is REGISTRY.by_canonical_id("ref")
    assert resolve_live_target("ref", SEMANTIC_LIVE_SURFACE).descriptor is resolved.descriptor
    expected = {f"ref.{kind.value}" for kind in SemanticKind}
    public_methods = {
        name
        for name, value in vars(type(ms.ref)).items()
        if not name.startswith("_") and callable(value)
    }
    assert public_methods == {kind.value for kind in SemanticKind}
    parent = REGISTRY.by_canonical_id("ref")
    assert {target.canonical_id for target in parent.see_also} == expected
    for kind in SemanticKind:
        method = getattr(ms.ref, kind.value)
        descriptor = REGISTRY.by_canonical_id(f"ref.{kind.value}")
        assert REGISTRY.by_callable(method) is descriptor
        assert resolve_live_target(method, SEMANTIC_LIVE_SURFACE).descriptor is descriptor
        assert (
            resolve_live_target(f"ref.{kind.value}", SEMANTIC_LIVE_SURFACE).descriptor is descriptor
        )
        assert descriptor.output_family == f"Ref[{kind.value}]"


def test_source_check_namespace_and_every_exact_method_share_registry_contracts() -> None:
    import marivo.semantic as ms
    from marivo.introspection.live.resolve import resolve_live_target

    expected_outputs = {
        "not_null": "NotNullSourceCheck",
        "allowed_values": "AllowedValuesSourceCheck",
        "unique": "UniqueSourceCheck",
        "freshness": "FreshnessSourceCheck",
        "relationship_matches": "RelationshipMatchesSourceCheck",
        "relationship_cardinality": "RelationshipCardinalitySourceCheck",
    }
    public_methods = {
        name
        for name, value in vars(type(ms.source_check)).items()
        if not name.startswith("_") and callable(value)
    }
    assert public_methods == set(expected_outputs)
    resolved = resolve_live_target(ms.source_check, SEMANTIC_LIVE_SURFACE)
    assert resolved.descriptor is REGISTRY.by_canonical_id("source_check")
    assert (
        resolve_live_target("source_check", SEMANTIC_LIVE_SURFACE).descriptor is resolved.descriptor
    )
    for method_name, output_family in expected_outputs.items():
        method = getattr(ms.source_check, method_name)
        descriptor = REGISTRY.by_canonical_id(f"source_check.{method_name}")
        assert REGISTRY.by_callable(method) is descriptor
        assert resolve_live_target(method, SEMANTIC_LIVE_SURFACE).descriptor is descriptor
        assert (
            resolve_live_target(f"source_check.{method_name}", SEMANTIC_LIVE_SURFACE).descriptor
            is descriptor
        )
        assert descriptor.output_family == output_family


def test_every_bound_input_fact_names_live_parameters() -> None:
    from marivo.introspection.live.reflect import import_registered_callable

    for descriptor in REGISTRY.descriptors:
        if descriptor.callable_path is None:
            continue
        signature = inspect.signature(import_registered_callable(descriptor.callable_path))
        parameters = tuple(signature.parameters.values())
        if parameters and parameters[0].name in {"self", "cls"}:
            signature = signature.replace(parameters=parameters[1:])
        for requirement in descriptor.input_requirements:
            assert set(requirement.parameter_names) <= set(signature.parameters)


def test_registry_covers_all_public_types() -> None:
    import marivo.semantic as ms

    for name in ms.__all__:
        exported = getattr(ms, name)
        if isinstance(exported, type):
            assert exported in TYPE_CONTRACTS, f"{name} ({exported}) is not in TYPE_CONTRACTS"


def test_type_and_error_help_matrices_are_closed_and_equivalent() -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.preview import PreviewResult

    for type_obj, contract in TYPE_CONTRACTS.items():
        if contract.name == "ref":
            continue
        by_type = resolve_live_target(type_obj, SEMANTIC_LIVE_SURFACE)
        by_name = resolve_live_target(contract.name, SEMANTIC_LIVE_SURFACE)
        assert by_type.kind == by_name.kind == "type_contract"
        assert by_type.type_name == by_name.type_name == contract.name

    assert PreviewResult in TYPE_CONTRACTS
    assert TYPE_CONTRACTS[PreviewResult].producers == (_semantic_target("preview"),)

    for error_name, error_type in ERROR_TYPES.items():
        by_type = resolve_live_target(error_type, SEMANTIC_LIVE_SURFACE)
        by_name = resolve_live_target(error_name, SEMANTIC_LIVE_SURFACE)
        repair_free_instance = error_type.__new__(error_type)
        by_instance = resolve_live_target(repair_free_instance, SEMANTIC_LIVE_SURFACE)
        assert by_type.kind == by_name.kind == by_instance.kind == "error_contract"
        assert by_type.error_name == by_name.error_name == by_instance.error_name == error_name


def test_terminal_result_help_matrix_accepts_type_string_and_instance() -> None:
    import marivo
    import marivo.semantic as ms
    from marivo._help.model import NativeHelpRoute
    from marivo._help.route import route_help_target
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.preview import PreviewResult
    from marivo.semantic.catalog import CalendarPeriodPage, TemporalOccurrencePage
    from marivo.semantic.dtos import PreviewBatchResult
    from marivo.semantic.parity import ParityResult
    from marivo.semantic.readiness import ReadinessReport
    from marivo.semantic.richness import RichnessReport
    from marivo.semantic.source_health import SourceHealthCheckResult, SourceHealthReport

    result_types = (
        PreviewResult,
        PreviewBatchResult,
        ReadinessReport,
        SourceHealthReport,
        SourceHealthCheckResult,
        RichnessReport,
        ParityResult,
        CalendarPeriodPage,
        TemporalOccurrencePage,
    )
    assert TYPE_CONTRACTS[CalendarPeriodPage].producers == (
        LiveHelpTarget(surface="analysis", canonical_id="calendar.periods"),
    )
    assert TYPE_CONTRACTS[TemporalOccurrencePage].producers == (
        LiveHelpTarget(surface="analysis", canonical_id="temporal_set.occurrences"),
    )
    for result_type in (CalendarPeriodPage, TemporalOccurrencePage):
        for producer in TYPE_CONTRACTS[result_type].producers:
            route = route_help_target(f"{producer.surface}.{producer.canonical_id}")
            assert isinstance(route, NativeHelpRoute)
            assert route.owner == producer.surface
    assert not hasattr(marivo, "PreviewResult")
    assert not hasattr(ms, "PreviewResult")
    for result_type in result_types:
        contract = TYPE_CONTRACTS[result_type]
        declared_members = {
            name for base in result_type.__mro__ for name in getattr(base, "__annotations__", {})
        }
        declared_members.update(
            name for name, value in vars(result_type).items() if isinstance(value, property)
        )
        assert set(contract.public_properties) <= declared_members
        assert all(hasattr(result_type, method) for method in contract.public_methods)
        instance = object.__new__(result_type)
        resolutions = (
            resolve_live_target(result_type, SEMANTIC_LIVE_SURFACE),
            resolve_live_target(contract.name, SEMANTIC_LIVE_SURFACE),
            resolve_live_target(instance, SEMANTIC_LIVE_SURFACE),
        )
        assert all(resolution.kind == "type_contract" for resolution in resolutions)
        assert {resolution.type_name for resolution in resolutions} == {contract.name}


def test_registry_includes_authoring_topic() -> None:
    assert "authoring" in REGISTRY.canonical_ids()


def test_source_contracts_cover_every_source_authored_ref_constructor() -> None:
    expected = {
        descriptor.canonical_id
        for descriptor in REGISTRY._descriptors
        if descriptor.output_family is not None
        and descriptor.output_family.startswith("Ref[")
        and descriptor.effects is not None
        and "semantic_source" in descriptor.effects.mutations
    }

    assert set(REGISTRY._source_contracts) == expected
    assert all(
        isinstance(contract, AuthoringSourceContract)
        for contract in REGISTRY._source_contracts.values()
    )
    assert all(
        not hasattr(contract, "prerequisite_targets")
        for contract in REGISTRY._source_contracts.values()
    )


def test_catalog_type_contract_uses_the_closed_member_contract() -> None:
    from marivo.semantic.catalog import CatalogCollection, SemanticCatalog

    catalog_contract = TYPE_CONTRACTS[SemanticCatalog]
    collection_contract = TYPE_CONTRACTS[CatalogCollection]

    assert catalog_contract.public_properties == CATALOG_COLLECTION_PROPERTIES
    assert catalog_contract.public_methods == (
        "items",
        "require",
        "preview",
        "preview_many",
        "source_health",
        "readiness",
        "render",
        "show",
    )
    assert collection_contract.public_properties == ("items", "refs")
    assert collection_contract.public_methods == ("get", "show", "render")
    assert not hasattr(collection_contract, "state_bearing")


def test_preview_capability_is_one_entry_or_exact_ref() -> None:
    import marivo.semantic as ms

    preview = REGISTRY.by_canonical_id("preview")
    subject = next(
        requirement for requirement in preview.input_requirements if requirement.role == "subject"
    )

    assert preview.output_family == "PreviewResult"
    assert subject.min_count == 1
    assert subject.max_count == 1
    assert subject.family == "CatalogEntry | Ref"
    assert preview.minimal_example is not None
    assert "catalog.preview(revenue" in preview.minimal_example
    assert preview.effects is not None
    assert preview.effects.mutations == ()
    assert "may_publish_certified_artifact" in preview.effects.flags
    preview_many = REGISTRY.by_canonical_id("preview_many")
    assert preview_many.output_family == "PreviewBatchResult"
    assert preview_many.effects is not None
    assert "may_publish_certified_artifact" not in preview_many.effects.flags
    assert ms.PreviewBatchResult in TYPE_CONTRACTS
