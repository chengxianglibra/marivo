"""Independent validation of the semantic Help graph and evidence routes."""

from collections import deque
from typing import Protocol, get_args

from marivo._authoring.model import AuthoringCapability
from marivo._kernel.refs import SemanticKind
from marivo.datasource._help_specs.manifest import REGISTRY as DATASOURCE_REGISTRY
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic._help_specs.builders import _symbol_family, _target
from marivo.semantic._help_specs.model import (
    SemanticBuilderTopic,
    SemanticCheckTopic,
    SemanticHelpDescriptor,
    SemanticHelpRenderClass,
    SemanticManifest,
    SemanticNavigationTopic,
    SemanticObjectContract,
    SemanticObjectIndexEntry,
    SemanticRootGroup,
)
from marivo.semantic._help_specs.object_specs import _SUPPORT_DISCOVERY_OWNERS

_SEMANTIC_NAVIGATION_DESCRIPTOR_TYPES = (
    SemanticNavigationTopic,
    SemanticBuilderTopic,
    SemanticCheckTopic,
    SemanticObjectContract,
)


def _target_key(target: LiveHelpTarget) -> tuple[str, str | None]:
    return target.surface, target.canonical_id


def _discovery_members(descriptor: SemanticHelpDescriptor) -> tuple[LiveHelpTarget, ...]:
    """Return ownership edges, excluding object and hub cross-links."""

    if isinstance(descriptor, SemanticNavigationTopic):
        return tuple(member.target for member in descriptor.members if member.owns_discovery)
    if isinstance(descriptor, SemanticBuilderTopic):
        return descriptor.members
    if isinstance(descriptor, SemanticCheckTopic):
        return tuple(target for route in descriptor.routes for target in route.targets)
    if isinstance(descriptor, SemanticObjectContract):
        return (
            *(mode.target for mode in descriptor.construction_modes),
            *(
                _target(target)
                for target in _SUPPORT_DISCOVERY_OWNERS.get(descriptor.semantic_kind, ())
            ),
        )
    if isinstance(descriptor, AuthoringCapability) and descriptor.canonical_id in {  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
        "ref",
        "source_check",
    }:
        return descriptor.see_also
    return ()


class _TargetValidator(Protocol):
    def __call__(self, target: LiveHelpTarget, *, owner: str) -> None: ...


def _validate_render_budgets(
    registry: SemanticManifest, help_descriptors: tuple[SemanticHelpDescriptor, ...]
) -> None:
    expected_render_classes = set(get_args(SemanticHelpRenderClass))
    if set(registry.render_budgets) != expected_render_classes:
        raise ValueError("semantic Help budgets must cover every render class")
    for render_class, budget in registry.render_budgets.items():
        if (
            budget.max_lines <= 0
            or budget.max_codepoints <= 0
            or budget.max_outgoing_routes <= 0
            or budget.max_examples_or_snippets < 0
        ):
            raise ValueError(f"invalid semantic Help render budget: {render_class}")

    for descriptor in help_descriptors:
        if isinstance(descriptor, _SEMANTIC_NAVIGATION_DESCRIPTOR_TYPES) and (
            descriptor.public_entrypoint is not None or descriptor.callable_path is not None  # type: ignore[redundant-expr]  # Preserve the runtime check.
        ):
            raise ValueError(
                f"semantic navigation descriptor must not be invokable: {descriptor.canonical_id}"
            )
        if isinstance(descriptor, SemanticNavigationTopic):
            if not descriptor.member_heading.strip() or not descriptor.members:
                raise ValueError(
                    f"semantic navigation descriptor is incomplete: {descriptor.canonical_id}"
                )
            for member in descriptor.members:
                if not member.label.strip() or (
                    member.summary is not None and not member.summary.strip()
                ):
                    raise ValueError(
                        f"semantic navigation member is incomplete: {descriptor.canonical_id}"
                    )


def _validate_object_relationships(
    contract: SemanticObjectContract,
    canonical_ids: tuple[str, ...],
    object_contracts: tuple[SemanticObjectContract, ...],
) -> None:
    kind = contract.semantic_kind
    for relationship in contract.relationships:
        target_id = relationship.target.canonical_id
        if not relationship.explanation.strip() or target_id is None:
            raise ValueError(f"semantic object relationship is incomplete: {kind.value}")
        registered_relationship_ids = {
            *canonical_ids,
            *(object_contract.canonical_id for object_contract in object_contracts),
        }
        if relationship.target.surface == "semantic":
            if target_id not in registered_relationship_ids:
                raise ValueError(f"unknown semantic object relationship: {kind.value}")
        elif relationship.target.surface == "datasource":
            try:
                DATASOURCE_REGISTRY.by_canonical_id(target_id)
            except KeyError as exc:
                raise ValueError(f"unknown datasource object relationship: {kind.value}") from exc
        else:
            raise ValueError(f"unsupported object relationship surface: {kind.value}")


def _validate_object_decisions(
    contract: SemanticObjectContract, allowed_next_ids: set[str]
) -> None:
    kind = contract.semantic_kind
    decision_ids = tuple(decision.decision_id for decision in contract.decisions)
    if not decision_ids or any(not decision_id.strip() for decision_id in decision_ids):
        raise ValueError(f"semantic object decisions must be named: {kind.value}")
    if len(decision_ids) != len(set(decision_ids)):
        raise ValueError(f"duplicate semantic object decision id: {kind.value}")
    for decision in contract.decisions:
        if not decision.question.strip() or not decision.determine_from.strip():
            raise ValueError(f"semantic object decision guidance is incomplete: {kind.value}")
        next_ids = tuple(target.canonical_id or "" for target in decision.next_targets)
        if any(
            target.surface != "semantic" or target_id not in allowed_next_ids
            for target, target_id in zip(decision.next_targets, next_ids, strict=True)
        ):
            raise ValueError(f"semantic object decision target escapes its owner: {kind.value}")
        if decision.encoding_status == "supported":
            if not next_ids or decision.unsupported_reason is not None:
                raise ValueError(f"invalid supported semantic object decision: {kind.value}")
        elif next_ids or not decision.unsupported_reason:
            raise ValueError(f"invalid unsupported semantic object decision: {kind.value}")


def _validate_object_contract(
    registry: SemanticManifest, contract: SemanticObjectContract, exact_ids: set[str]
) -> None:
    kind = contract.semantic_kind
    expected_ref_family = _symbol_family(kind)
    if contract.canonical_id != kind.value:
        raise ValueError(f"invalid semantic object-kind canonical id: {kind.value}")
    if contract.catalog_kind != kind.value:
        raise ValueError(f"semantic object catalog kind drift: {kind.value}")
    if contract.ref_target != _target(f"ref.{kind.value}"):
        raise ValueError(f"semantic object ref target drift: {kind.value}")
    ref_descriptor = registry.by_canonical_id(contract.ref_target.canonical_id or "")
    if not isinstance(ref_descriptor, AuthoringCapability):
        raise ValueError(f"semantic object ref target is not exact: {kind.value}")
    if ref_descriptor.output_family != expected_ref_family:
        raise ValueError(f"semantic object ref-kind output drift: {kind.value}")

    construction_ids = tuple(mode.target.canonical_id or "" for mode in contract.construction_modes)
    if not construction_ids:
        raise ValueError(f"semantic object kind has no construction mode: {kind.value}")
    if len(construction_ids) != len(set(construction_ids)):
        raise ValueError(f"duplicate semantic object construction mode: {kind.value}")
    for mode in contract.construction_modes:
        descriptor = registry.by_canonical_id(mode.target.canonical_id or "")
        if not isinstance(descriptor, AuthoringCapability):
            raise ValueError(f"semantic construction target is not exact: {kind.value}")
        if descriptor.output_family not in contract.construction_families:
            raise ValueError(f"semantic construction output-kind drift: {kind.value}")

    supporting_ids = tuple(target.canonical_id or "" for target in contract.supporting_targets)
    check_ids = tuple(target.canonical_id or "" for target in contract.check_targets)
    if len(supporting_ids) != len(set(supporting_ids)):
        raise ValueError(f"duplicate semantic supporting target: {kind.value}")
    if len(check_ids) != len(set(check_ids)):
        raise ValueError(f"duplicate semantic check target: {kind.value}")
    for target in (*contract.supporting_targets, *contract.check_targets):
        if target.surface != "semantic" or target.canonical_id not in exact_ids:
            raise ValueError(f"unknown semantic object edge: {kind.value}")

    _validate_object_decisions(contract, {*construction_ids, *supporting_ids, *check_ids})


def _validate_object_contracts(
    registry: SemanticManifest,
    help_descriptors: tuple[SemanticHelpDescriptor, ...],
    exact_ids: set[str],
    canonical_ids: tuple[str, ...],
) -> None:
    object_contracts = registry.object_contracts
    object_index_entries = tuple(
        member
        for descriptor in help_descriptors
        if isinstance(descriptor, SemanticNavigationTopic)
        for member in descriptor.members
        if isinstance(member, SemanticObjectIndexEntry)
    )
    if len(object_index_entries) != len(object_contracts) or {
        id(entry.contract) for entry in object_index_entries
    } != {id(contract) for contract in object_contracts}:
        raise ValueError("semantic object index must embed the registered object contracts")
    expected_object_kinds = set(SemanticKind) - {SemanticKind.DATASOURCE}
    object_kinds = tuple(contract.semantic_kind for contract in object_contracts)
    if len(object_kinds) != len(set(object_kinds)):
        raise ValueError("duplicate semantic object-kind contract")
    if set(object_kinds) != expected_object_kinds:
        raise ValueError("semantic object-kind contracts must cover every non-datasource kind")

    for contract in object_contracts:
        _validate_object_contract(registry, contract, exact_ids)
        _validate_object_relationships(contract, canonical_ids, object_contracts)


def _validate_discovery(
    registry: SemanticManifest,
    help_descriptors: tuple[SemanticHelpDescriptor, ...],
    exact_descriptors: tuple[AuthoringCapability, ...],
    ref_method_ids: tuple[str, ...],
    source_check_method_ids: tuple[str, ...],
    validate_target: _TargetValidator,
) -> None:
    discovery_owners: dict[tuple[str, str | None], str] = {}
    for descriptor in help_descriptors:
        routes = registry.routes(descriptor.canonical_id)
        budget = registry.render_budget(registry.render_class(descriptor.canonical_id))
        if len(routes) > budget.max_outgoing_routes:
            raise ValueError(
                f"semantic Help descriptor exceeds route budget: {descriptor.canonical_id}"
            )
        for target in routes:
            validate_target(target, owner=descriptor.canonical_id)

        discovery_members = _discovery_members(descriptor)
        if len(discovery_members) != len(set(discovery_members)):
            raise ValueError(f"duplicate semantic discovery member: {descriptor.canonical_id}")
        for discovery_target in discovery_members:
            key = _target_key(discovery_target)
            previous_owner = discovery_owners.setdefault(key, descriptor.canonical_id)
            if previous_owner != descriptor.canonical_id:
                raise ValueError(f"multiple semantic discovery owners: {discovery_target.display}")

    required_discovery_ids = (
        {descriptor.canonical_id for descriptor in exact_descriptors if descriptor.kind != "method"}
        | set(ref_method_ids)
        | set(source_check_method_ids)
    )
    owned_semantic_ids = {
        target_id
        for (surface, target_id) in discovery_owners
        if surface == "semantic" and target_id is not None
    }
    missing_owners = sorted(required_discovery_ids - owned_semantic_ids)
    if missing_owners:
        raise ValueError("semantic discovery owner missing: " + ", ".join(missing_owners))

    required_graph_ids = required_discovery_ids | {
        descriptor.canonical_id
        for descriptor in help_descriptors
        if not isinstance(descriptor, AuthoringCapability)
    }
    distances = {
        target.canonical_id: 1
        for section in registry.root_sections
        for target in section.members
        if target.canonical_id is not None
    }
    pending = deque(distances)
    while pending:
        current = pending.popleft()
        for target in registry.routes(current):
            target_id = target.canonical_id
            if (
                target.surface != "semantic"
                or target_id is None
                or target_id not in registry._by_id
                or target_id in distances
            ):
                continue
            distances[target_id] = distances[current] + 1
            pending.append(target_id)

    unreachable = sorted(required_graph_ids - set(distances))
    if unreachable:
        raise ValueError("semantic Help graph is unreachable: " + ", ".join(unreachable))
    too_deep = sorted(
        canonical_id for canonical_id in required_graph_ids if distances[canonical_id] > 3
    )
    if too_deep:
        raise ValueError("semantic Help graph exceeds four surface reads: " + ", ".join(too_deep))


def _validate_factory_methods(
    registry: SemanticManifest, validate_target: _TargetValidator
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    ref_method_ids = tuple(f"ref.{kind.value}" for kind in SemanticKind)
    ref_parent = registry.by_canonical_id("ref")
    if not isinstance(ref_parent, AuthoringCapability):
        raise ValueError("semantic ref factory contract must be exact")
    if tuple(target.canonical_id for target in ref_parent.see_also) != ref_method_ids or any(
        target.surface != "semantic" for target in ref_parent.see_also
    ):
        raise ValueError("semantic ref factory membership drift")
    for target_id in ref_method_ids:
        try:
            descriptor = registry.by_canonical_id(target_id)
        except KeyError as exc:
            raise ValueError("semantic ref factory target is not exact") from exc
        if (
            not isinstance(descriptor, AuthoringCapability)
            or descriptor.kind != "method"
            or descriptor.callable_path is None
        ):
            raise ValueError("semantic ref factory target is not exact")

    source_check_method_ids = (
        "source_check.not_null",
        "source_check.allowed_values",
        "source_check.unique",
        "source_check.freshness",
        "source_check.relationship_matches",
        "source_check.relationship_cardinality",
    )
    source_check_parent = registry.by_canonical_id("source_check")
    if not isinstance(source_check_parent, AuthoringCapability):
        raise ValueError("semantic source-check factory contract must be exact")
    if tuple(target.canonical_id for target in source_check_parent.see_also) != (
        source_check_method_ids
    ) or any(target.surface != "semantic" for target in source_check_parent.see_also):
        raise ValueError("semantic source-check factory membership drift")
    for target_id in source_check_method_ids:
        try:
            descriptor = registry.by_canonical_id(target_id)
        except KeyError as exc:
            raise ValueError("semantic source-check factory target is not exact") from exc
        if (
            not isinstance(descriptor, AuthoringCapability)
            or descriptor.kind != "method"
            or descriptor.callable_path is None
        ):
            raise ValueError("semantic source-check factory target is not exact")
    return ref_method_ids, source_check_method_ids


def _validate_root_routes(registry: SemanticManifest, validate_target: _TargetValidator) -> None:
    for section in registry.root_sections:
        if not section.label.strip() or not section.members:
            raise ValueError(f"semantic root section is incomplete: {section.section_id}")
        for target in section.members:
            validate_target(target, owner=f"root.{section.section_id}")
        if len(section.route_summaries) != len(section.members) or any(
            not summary.strip() for summary in section.route_summaries
        ):
            raise ValueError(f"semantic root decisions are incomplete: {section.section_id}")

    for error_kind, repair_contract in registry._repair_contracts.items():
        if error_kind != repair_contract.error_kind or not error_kind.strip():
            raise ValueError("semantic repair contract key drift")
        target = repair_contract.help_target
        target_id = target.canonical_id
        if target_id is None:
            raise ValueError(f"semantic repair target lacks a canonical id: {error_kind}")
        if target.surface != "semantic":
            continue
        validate_target(target, owner=f"repair.{error_kind}")
        descriptor = registry.by_canonical_id(target_id)
        if not isinstance(descriptor, (AuthoringCapability, SemanticObjectContract)):
            raise ValueError(f"semantic repair target is over-broad: {error_kind}")


def _validate_descriptor_maps(
    registry: SemanticManifest,
    help_descriptors: tuple[SemanticHelpDescriptor, ...],
    canonical_ids: tuple[str, ...],
) -> tuple[AuthoringCapability, ...]:
    if any(not canonical_id.strip() for canonical_id in canonical_ids):
        raise ValueError("semantic help canonical ids must not be empty")
    if any(not descriptor.summary.strip() for descriptor in help_descriptors):
        raise ValueError("semantic help descriptor summaries must not be empty")

    exact_descriptors = tuple(
        descriptor for descriptor in help_descriptors if isinstance(descriptor, AuthoringCapability)
    )
    if exact_descriptors != registry.descriptors:
        raise ValueError("semantic exact descriptor projection is inconsistent")
    if tuple(registry._by_id) != canonical_ids:
        raise ValueError("semantic help id index is inconsistent")
    for descriptor in help_descriptors:
        if registry._by_id.get(descriptor.canonical_id) is not descriptor:
            raise ValueError("semantic help id index does not preserve descriptor identity")

    callable_descriptors = tuple(
        descriptor for descriptor in exact_descriptors if descriptor.callable_path is not None
    )
    callable_paths = tuple(descriptor.callable_path for descriptor in callable_descriptors)
    if len(callable_paths) != len(set(callable_paths)):
        raise ValueError("duplicate semantic callable path")
    if set(registry._by_callable_path) != set(callable_paths):
        raise ValueError("semantic callable index is inconsistent")
    for descriptor in callable_descriptors:
        callable_path = descriptor.callable_path
        if callable_path is None or registry._by_callable_path.get(callable_path) is not descriptor:
            raise ValueError("semantic callable index does not preserve descriptor identity")
    return exact_descriptors


def validate_registry(registry: SemanticManifest) -> None:
    """Reject invalid closed registry state before the live surface is built."""

    help_descriptors = registry.help_descriptors
    canonical_ids = tuple(descriptor.canonical_id for descriptor in help_descriptors)
    exact_descriptors = _validate_descriptor_maps(registry, help_descriptors, canonical_ids)
    _validate_render_budgets(registry, help_descriptors)

    exact_ids = {descriptor.canonical_id for descriptor in exact_descriptors}
    expected_root_sections: tuple[SemanticRootGroup, ...] = ("tasks",)
    if tuple(section.section_id for section in registry.root_sections) != expected_root_sections:
        raise ValueError("semantic root sections must use the reviewed compact topology")
    root_routes = tuple(target for section in registry.root_sections for target in section.members)
    if len(root_routes) != len(set(root_routes)):
        raise ValueError("duplicate semantic root route")
    if len(root_routes) > registry.render_budget("root").max_outgoing_routes:
        raise ValueError("semantic root exceeds its route budget")

    known_semantic_type_targets = {
        "semantic_catalog",
        "semantic_object",
    }

    def validate_target(target: LiveHelpTarget, *, owner: str) -> None:
        target_id = target.canonical_id
        if target_id is None:
            raise ValueError(f"semantic Help route lacks a canonical id: {owner}")
        if target.surface == "semantic":
            if target_id not in set(canonical_ids) | known_semantic_type_targets:
                raise ValueError(f"unknown semantic Help route: {owner} -> {target_id}")
            return
        try:
            if target.surface == "datasource":
                DATASOURCE_REGISTRY.by_canonical_id(target_id)
            elif target.surface in {"analysis", "ontology"}:
                # Unified route tests validate cross-surface existence. Importing the
                # analysis or ontology registry here would create a cycle during
                # an analysis-first or ontology-first cold start.
                return
            else:
                raise ValueError(f"unsupported semantic Help route surface: {target.surface}")
        except KeyError as exc:
            raise ValueError(f"unknown external semantic Help route: {owner}") from exc

    _validate_root_routes(registry, validate_target)
    _validate_object_contracts(registry, help_descriptors, exact_ids, canonical_ids)

    ref_method_ids, source_check_method_ids = _validate_factory_methods(registry, validate_target)
    _validate_discovery(
        registry,
        help_descriptors,
        exact_descriptors,
        ref_method_ids,
        source_check_method_ids,
        validate_target,
    )
