"""Tests for the closed capability kernel and native help topology.

These tests pin the private ``_capabilities`` package: the closed descriptor
union, registry-owned discovery, artifact-family vocabulary, frozen
dataclass behavior, static Help budgets, absence of kernel types from the
public ``marivo.analysis.__all__``, and the immutable capability registry.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from types import MappingProxyType
from typing import get_args

import pytest

from marivo.analysis._capabilities import (
    ANALYSIS_HELP_RENDER_BUDGETS,
    ARTIFACT_FAMILIES,
    ROOT_GROUP_ORDER,
    AnalysisArtifactFamilyContract,
    AnalysisHelpDescriptor,
    AnalysisHelpRenderBudget,
    AnalysisHelpRenderClass,
    AnalysisMethodFamily,
    AnalysisNavigationTopic,
    ArtifactOutputContract,
    AuthorityPolicy,
    BoundaryCapability,
    CapabilityBase,
    CapabilityDescriptor,
    ConstructorCapability,
    EpistemicKind,
    OperatorCapability,
    ReadCapability,
    RecoveryCapability,
    RootGroup,
    SameAsInputFamily,
)
from marivo.analysis._capabilities.model import HelpExample
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.introspection.live.model import SURFACE_LIMITS, LiveHelpTarget, SurfaceLimits

# ---------------------------------------------------------------------------
# Root groups
# ---------------------------------------------------------------------------

EXPECTED_ROOT_GROUPS = (
    "semantic_inputs",
    "policies_builders",
    "artifact_production",
    "typed_analysis",
    "family_operations",
    "artifact_inspection",
    "recovery",
    "boundaries",
)


def test_root_group_order_has_eight_groups() -> None:
    assert len(ROOT_GROUP_ORDER) == 8


def test_root_group_order_matches_expected_teaching_order() -> None:
    assert ROOT_GROUP_ORDER == EXPECTED_ROOT_GROUPS


def test_policy_families_have_explicit_discovery_topics() -> None:
    discovery_ids = REGISTRY.discovery_ids()

    assert "alignment" in discovery_ids
    assert "SamplingPolicy" in discovery_ids
    assert "day_of_week" not in discovery_ids
    assert "sampling" not in discovery_ids


def test_root_group_order_has_no_duplicates() -> None:
    assert len(set(ROOT_GROUP_ORDER)) == len(ROOT_GROUP_ORDER)


def test_exact_capabilities_do_not_own_root_placement() -> None:
    field_names = {field.name for field in fields(CapabilityBase)}

    assert "root_group" not in field_names
    assert "root_visibility" not in field_names
    assert "root_summary" not in field_names
    assert tuple(group for group, _members in REGISTRY.discovery_groups()) == ROOT_GROUP_ORDER


def test_root_group_order_matches_render_labels() -> None:
    """The renderer's group labels and the teaching order must stay in sync.
    A label key that is not in ``ROOT_GROUP_ORDER`` (or vice versa) would either
    render a header with no members or leave a group untitled; both point at a
    group-removal edit that forgot one of the two sources."""
    from marivo.analysis._capabilities.render import _GROUP_LABELS

    assert set(ROOT_GROUP_ORDER) == set(_GROUP_LABELS)


def test_root_group_literal_matches_order() -> None:
    """The ``RootGroup`` literal must equal the teaching order exactly. A stale
    member left in the literal (the historical ``session_state`` drift) has no
    runtime consumer to trip over, so narrowing the literal was previously
    caught only by mypy — but widening it (keeping a removed group) passes
    every gate unless this contract pins the two sets equal."""
    assert set(get_args(RootGroup)) == set(ROOT_GROUP_ORDER)


def test_native_root_topology_has_six_hubs_and_one_exact_terminal_edge() -> None:
    from marivo.analysis.frames.base import BaseFrame

    assert tuple(topic.canonical_id for topic in REGISTRY.navigation_topics[:6]) == (
        "entry",
        "methods",
        "inputs",
        "artifacts",
        "evidence",
        "runtime",
    )
    assert tuple(target.canonical_id for target in REGISTRY.root_members) == (
        "entry",
        "methods",
        "inputs",
        "artifacts",
        "evidence",
        "runtime",
        "boundary.to_pandas",
    )
    assert all(topic.render_class == "decision_hub" for topic in REGISTRY.navigation_topics[:6])
    assert all(topic.public_entrypoint is None for topic in REGISTRY.navigation_topics)
    assert all(topic.callable_path is None for topic in REGISTRY.navigation_topics)
    assert REGISTRY.by_id("boundary.to_pandas") is REGISTRY.by_callable(BaseFrame.to_pandas)


def test_slice2_focused_topology_is_active_without_switching_the_root() -> None:
    assert "entry" not in REGISTRY.canonical_ids()
    assert "methods" in REGISTRY.canonical_ids()
    assert "inputs" in REGISTRY.canonical_ids()
    assert "evidence" not in REGISTRY.canonical_ids()
    assert "runtime" not in REGISTRY.canonical_ids()
    assert "artifacts" in REGISTRY.canonical_ids()


def test_runtime_registry_iteration_contains_exact_capabilities_only() -> None:
    assert not any(
        isinstance(
            descriptor,
            (
                AnalysisNavigationTopic,
                AnalysisMethodFamily,
                AnalysisArtifactFamilyContract,
            ),
        )
        for descriptor in REGISTRY.descriptors
    )
    assert isinstance(REGISTRY.by_canonical_id("artifacts"), AnalysisNavigationTopic)


# ---------------------------------------------------------------------------
# Artifact families
# ---------------------------------------------------------------------------

EXPECTED_ARTIFACT_FAMILIES = (
    "MetricFrame",
    "EventFrame",
    "LifecycleFrame",
    "SubjectSet",
    "DeltaFrame",
    "AttributionFrame",
    "ForecastFrame",
    "QualityReport",
    "CandidateSet",
    "AssociationResult",
    "ComponentFrame",
    "CoverageFrame",
    "HypothesisTestResult",
)


def test_artifact_families_has_thirteen_members() -> None:
    assert len(ARTIFACT_FAMILIES) == 13


def test_artifact_families_matches_expected_vocabulary() -> None:
    assert ARTIFACT_FAMILIES == EXPECTED_ARTIFACT_FAMILIES


def test_artifact_families_has_no_duplicates() -> None:
    assert len(set(ARTIFACT_FAMILIES)) == len(ARTIFACT_FAMILIES)


def test_every_artifact_family_has_one_canonical_contract() -> None:
    assert (
        tuple(contract.artifact_family for contract in REGISTRY.artifact_contracts)
        == ARTIFACT_FAMILIES
    )
    for contract in REGISTRY.artifact_contracts:
        assert contract.canonical_id == contract.type_name == contract.artifact_family
        assert REGISTRY.artifact_contract(contract.artifact_family) is contract
        assert REGISTRY.by_help_target(contract.canonical_id) is contract
        assert contract in REGISTRY.help_descriptors


def test_artifact_algebra_expands_same_as_nullable_and_terminal_edges() -> None:
    metric_producers = tuple(
        target.canonical_id for target in REGISTRY.artifact_producers("MetricFrame")
    )
    delta_producers = tuple(
        target.canonical_id for target in REGISTRY.artifact_producers("DeltaFrame")
    )
    family_preserving = tuple(
        f"transform.{name}"
        for name in ("filter", "slice", "rollup", "topk", "bottomk", "rank", "window")
    )

    assert metric_producers[0] == "observe"
    assert all(target in metric_producers for target in family_preserving)
    assert all(target in delta_producers for target in family_preserving)
    assert "transform.normalize" in metric_producers
    assert "transform.normalize" not in delta_producers
    assert tuple(
        target.canonical_id for target in REGISTRY.artifact_producers("CoverageFrame")
    ) == ("MetricFrame.coverage",)
    coverage = REGISTRY.by_help_target("MetricFrame.coverage")
    assert isinstance(coverage, OperatorCapability)
    assert coverage.output_contract.nullable is True
    for family in ARTIFACT_FAMILIES:
        assert "boundary.to_pandas" in tuple(
            target.canonical_id for target in REGISTRY.artifact_consumers(family)
        )


def test_artifact_edges_preserve_output_and_admission_contracts() -> None:
    coverage_producer = REGISTRY.artifact_producer_edges("CoverageFrame")[0]
    assert coverage_producer.target.canonical_id == "MetricFrame.coverage"
    assert coverage_producer.nullable is True

    metric_transforms = {
        edge.target.canonical_id: edge
        for edge in REGISTRY.artifact_producer_edges("MetricFrame")
        if edge.target.canonical_id and edge.target.canonical_id.startswith("transform.")
    }
    assert metric_transforms["transform.filter"].same_as_parameter == "receiver"

    event_consumers = REGISTRY.artifact_consumer_edges("EventFrame")
    compare_edges = tuple(edge for edge in event_consumers if edge.target.canonical_id == "compare")
    assert tuple(edge.parameter for edge in compare_edges) == ("current", "baseline")
    assert all(edge.semantic_shapes == frozenset({"funnel"}) for edge in compare_edges)
    assert all(edge.matching_kinds == frozenset({"first_per_subject"}) for edge in compare_edges)
    select_edge = next(
        edge for edge in event_consumers if edge.target.canonical_id == "select_subjects"
    )
    assert select_edge.semantic_shapes == frozenset({"journey"})
    assert select_edge.matching_kinds == frozenset({"first_per_subject"})


def test_slice2_cross_links_are_explicit_immutable_and_budgeted() -> None:
    assert tuple(target.canonical_id for target in REGISTRY.cross_links("artifacts.reading")) == (
        "session.evidence.digest",
        "session.evidence.findings",
        "session.evidence.trace",
        "boundary.to_pandas",
    )
    metric_routes = REGISTRY.cross_links("MetricFrame")
    assert tuple(target.canonical_id for target in metric_routes) == (
        "artifacts.reading",
        "observe",
        "transform",
        "methods.change",
        "methods.relationship_testing",
        "forecast",
        "assess_quality",
        "discover",
        "boundary.to_pandas",
        "session.get_frame",
    )
    assert len(metric_routes) == REGISTRY.render_budget("public_type").max_outgoing_routes
    for targets in REGISTRY.cross_link_index.values():
        for target in targets:
            if target.surface == "analysis" and target.canonical_id is not None:
                try:
                    REGISTRY.by_help_target(target.canonical_id)
                except KeyError:
                    import marivo.analysis as mv

                    assert hasattr(mv, target.canonical_id)
    with pytest.raises(TypeError):
        REGISTRY.cross_link_index["MetricFrame"] = ()  # type: ignore[index]


def test_slice2_discovery_owners_are_explicit_and_unique() -> None:
    expected = {
        "events.match": "events",
        "events.watermark": "entry.event_observations",
        "events.occurrence_bounds": "entry.event_observations",
        "calendar.period": "inputs.scope",
        "SamplingPolicy": "inputs",
        "MetricFrame": "artifacts.metric_change",
        "MetricFrame.coverage": "MetricFrame",
        "boundary.to_pandas": "analysis",
    }
    for target, owner in expected.items():
        resolved_owner = REGISTRY.discovery_owner(target)
        assert resolved_owner is not None
        assert resolved_owner.canonical_id == owner

    for descriptor in REGISTRY.descriptors:
        ordinary = descriptor.callable_path is not None or (
            isinstance(descriptor, ConstructorCapability) and bool(descriptor.output_type)
        )
        if ordinary:
            assert REGISTRY.discovery_owner(descriptor.help_target) is not None


def test_slice2_family_membership_is_exact_and_ordered() -> None:
    assert tuple(
        target.canonical_id for target in REGISTRY.discovery_members("methods.change")
    ) == ("compare", "attribute")
    assert tuple(target.canonical_id for target in REGISTRY.discovery_members("events")) == (
        "events.match",
        "events.funnel",
        "events.time_to_event",
    )
    assert tuple(target.canonical_id for target in REGISTRY.discovery_members("inputs.scope")) == (
        "grain",
        "time_scope",
        "AbsoluteWindow",
        "calendar.period",
        "Session.source_bindings",
    )
    assert tuple(
        target.canonical_id for target in REGISTRY.discovery_members("artifacts.reading")
    ) == ("BaseFrame.show", "BaseFrame.contract")


# ---------------------------------------------------------------------------
# Descriptor kinds
# ---------------------------------------------------------------------------


def test_capability_descriptor_union_has_five_variants() -> None:
    variants = get_args(CapabilityDescriptor)
    assert len(variants) == 5


def test_capability_descriptor_union_contains_all_kinds() -> None:
    variants = set(get_args(CapabilityDescriptor))
    assert variants == {
        OperatorCapability,
        ConstructorCapability,
        ReadCapability,
        RecoveryCapability,
        BoundaryCapability,
    }


def test_analysis_help_descriptor_adds_native_topology_without_widening_capabilities() -> None:
    assert set(get_args(AnalysisHelpDescriptor)) == {
        *get_args(CapabilityDescriptor),
        AnalysisNavigationTopic,
        AnalysisMethodFamily,
        AnalysisArtifactFamilyContract,
    }


def test_native_topology_vocabularies_are_closed() -> None:
    assert set(get_args(AnalysisHelpRenderClass)) == {
        "root",
        "decision_hub",
        "navigation",
        "exact_callable",
        "public_type",
        "current_briefing",
    }
    assert set(get_args(EpistemicKind)) == {
        "observed",
        "algebraic",
        "candidate",
        "association",
        "statistical_decision",
        "projection",
        "quality_evaluation",
        "selection",
    }


@pytest.mark.parametrize(
    "cls,expected_kind",
    [
        (OperatorCapability, "operator"),
        (ConstructorCapability, "constructor"),
        (ReadCapability, "read"),
        (RecoveryCapability, "recovery"),
        (BoundaryCapability, "boundary"),
    ],
)
def test_descriptor_kind_default(cls: type[CapabilityBase], expected_kind: str) -> None:
    """Each descriptor variant defaults to its kind literal."""
    variant_kwargs = {"authority_policy": "materialized_only"} if cls is OperatorCapability else {}
    instance = cls(
        id="test.capability",
        public_entrypoint="test.capability()",
        help_target="test.capability",
        summary="test summary",
        **variant_kwargs,  # type: ignore[arg-type]
    )
    assert instance.kind == expected_kind


# ---------------------------------------------------------------------------
# Frozen dataclass behavior
# ---------------------------------------------------------------------------

_BASE_INIT_KWARGS: dict[str, str] = {
    "id": "test.frozen",
    "public_entrypoint": "test.frozen()",
    "help_target": "test.frozen",
    "summary": "frozen check",
}

_FROZEN_INSTANCES: list[object] = [
    CapabilityBase(**_BASE_INIT_KWARGS),  # type: ignore[call-arg]
    OperatorCapability(
        **_BASE_INIT_KWARGS,  # type: ignore[arg-type]
        authority_policy="materialized_only",
    ),
    ConstructorCapability(**_BASE_INIT_KWARGS),  # type: ignore[call-arg]
    ReadCapability(**_BASE_INIT_KWARGS),  # type: ignore[call-arg]
    RecoveryCapability(**_BASE_INIT_KWARGS),  # type: ignore[call-arg]
    BoundaryCapability(**_BASE_INIT_KWARGS),  # type: ignore[call-arg]
    AnalysisNavigationTopic(
        canonical_id="test.navigation",
        summary="Test navigation.",
        render_class="navigation",
        members=(
            LiveHelpTarget(surface="analysis", canonical_id="one"),
            LiveHelpTarget(surface="analysis", canonical_id="two"),
        ),
    ),
    AnalysisMethodFamily(
        canonical_id="test.methods",
        summary="Test methods.",
        epistemic_kinds=("observed",),
        members=(
            LiveHelpTarget(surface="analysis", canonical_id="one"),
            LiveHelpTarget(surface="analysis", canonical_id="two"),
        ),
        input_routes=(),
        output_routes=(),
    ),
    AnalysisArtifactFamilyContract(
        canonical_id="MetricFrame",
        artifact_family="MetricFrame",
        summary="Test Artifact family.",
        epistemic_kinds=("observed",),
        semantic_shapes=("scalar",),
        type_name="MetricFrame",
        specialized_member_targets=(),
    ),
    AnalysisHelpRenderBudget(1, 1, 1, 0),
    SameAsInputFamily(parameter="receiver"),
    SurfaceLimits(),
]


@pytest.mark.parametrize("instance", _FROZEN_INSTANCES)
def test_all_kernel_dataclasses_are_frozen(instance: object) -> None:
    assert is_dataclass(instance)
    first_field = fields(instance)[0].name
    current = getattr(instance, first_field)
    with pytest.raises((AttributeError, TypeError)):
        setattr(instance, first_field, current)


def test_capability_base_required_fields() -> None:
    field_names = {f.name for f in fields(CapabilityBase)}
    assert "id" in field_names
    assert "public_entrypoint" in field_names
    assert "help_target" in field_names
    assert "summary" in field_names
    assert "constraint_ids" in field_names
    assert "callable_path" in field_names
    assert "additional_examples" in field_names


def test_capability_base_defaults() -> None:
    base = CapabilityBase(
        id="test.base",
        public_entrypoint="test.base()",
        help_target="test.base",
        summary="base summary",
    )
    assert base.constraint_ids == ()
    assert base.callable_path is None
    assert base.additional_examples == ()


@pytest.mark.parametrize(
    "example,error",
    (
        (HelpExample(label="", code="test.call()"), "label"),
        (HelpExample(label="Empty", code=""), "code"),
        (HelpExample(label="Placeholder", code="test.call(...)"), "placeholder"),
        (HelpExample(label="Syntax", code="test.call("), "parseable"),
        (HelpExample(label="Wrong owner", code="other.call()"), "must call"),
        (HelpExample(label="Owner prefix", code="test.callback()"), "must call"),
    ),
)
def test_registry_rejects_invalid_additional_examples(
    example: HelpExample,
    error: str,
) -> None:
    from marivo.analysis._capabilities.registry import _finalize_registry

    descriptor = ReadCapability(
        id="test.call",
        public_entrypoint="test.call()",
        help_target="test.call",
        summary="test",
        additional_examples=(example,),
    )
    with pytest.raises(ValueError, match=error):
        _finalize_registry((descriptor,))


def test_registry_additional_examples_are_owned_by_bounded_capabilities_only() -> None:
    owners = {
        descriptor.id: descriptor.additional_examples
        for descriptor in REGISTRY.descriptors
        if descriptor.additional_examples
    }
    assert tuple(owners) == (
        "observe",
        "events.match",
        "lifecycle.replay",
        "lifecycle.distribution",
        "compare",
        "attribute",
        "correlate",
        "MetricFrame.coverage",
        "AttributionFrame.at_resolution",
    )
    assert len(owners["observe"]) == 2
    assert len(owners["events.match"]) == 3
    assert len(owners["lifecycle.replay"]) == 2
    assert len(owners["lifecycle.distribution"]) == 1
    assert len(owners["AttributionFrame.at_resolution"]) == 1
    assert len(owners["correlate"]) == 1
    assert len(owners["MetricFrame.coverage"]) == 1


def test_operator_capability_defaults() -> None:
    cap = OperatorCapability(
        id="op.test",
        public_entrypoint="session.test()",
        help_target="test",
        summary="test op",
        authority_policy="materialized_only",
    )
    assert cap.receiver == ""
    assert cap.accepted_inputs == {}
    assert cap.output_family == "MetricFrame"


def test_artifact_output_contract_nullable_render_preserves_family() -> None:
    default = ArtifactOutputContract(family="CoverageFrame")
    nullable = ArtifactOutputContract(family="CoverageFrame", nullable=True)

    assert default.nullable is False
    assert default.render() == "CoverageFrame"
    assert nullable.family == "CoverageFrame"
    assert nullable.render() == "CoverageFrame | None"


def test_read_capability_defaults() -> None:
    cap = ReadCapability(
        id="read.test",
        public_entrypoint="artifact.show()",
        help_target="test",
        summary="test read",
    )
    assert cap.receiver_family == ""
    assert cap.result_kind == "immutable_metadata"
    assert cap.read_bound == "bounded"


def test_recovery_capability_defaults() -> None:
    cap = RecoveryCapability(
        id="recovery.test",
        public_entrypoint="session.get_frame()",
        help_target="test",
        summary="test recovery",
    )
    assert cap.identity_input == ""
    assert cap.restored_family == ""
    assert cap.query_behavior == "none"


def test_boundary_capability_defaults() -> None:
    cap = BoundaryCapability(
        id="boundary.test",
        public_entrypoint="frame.to_pandas()",
        help_target="boundary.test",
        summary="test boundary",
    )
    assert cap.direction == "terminal_exit"
    assert cap.accepted_inputs == {}
    assert cap.output_family == ""
    assert cap.preserves == ()
    assert cap.does_not_preserve == ()


def test_constructor_capability_defaults() -> None:
    cap = ConstructorCapability(
        id="ctor.test",
        public_entrypoint="window_bucket()",
        help_target="test",
        summary="test ctor",
    )
    assert cap.output_type == ""


# ---------------------------------------------------------------------------
# SameAsInputFamily
# ---------------------------------------------------------------------------


def test_same_as_input_family_is_frozen_dataclass() -> None:
    assert is_dataclass(SameAsInputFamily)
    val = SameAsInputFamily(parameter="receiver")
    assert val.parameter == "receiver"
    with pytest.raises((AttributeError, TypeError)):
        val.parameter = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Surface limits
# ---------------------------------------------------------------------------

EXPECTED_ANALYSIS_HELP_BUDGETS = {
    "root": (32, 3_000, 8, 0),
    "decision_hub": (44, 4_500, 10, 0),
    "navigation": (64, 6_500, 16, 0),
    "exact_callable": (104, 9_000, 10, 1),
    "public_type": (72, 7_000, 10, 0),
    "current_briefing": (72, 7_000, 6, 1),
}


def test_analysis_static_help_budgets_are_complete_and_exact() -> None:
    assert REGISTRY.render_budgets == ANALYSIS_HELP_RENDER_BUDGETS
    assert {
        render_class: (
            budget.max_lines,
            budget.max_codepoints,
            budget.max_outgoing_routes,
            budget.max_examples_or_snippets,
        )
        for render_class, budget in REGISTRY.render_budgets.items()
    } == EXPECTED_ANALYSIS_HELP_BUDGETS


def test_analysis_static_help_budgets_are_immutable() -> None:
    with pytest.raises(TypeError):
        REGISTRY.render_budgets["root"] = AnalysisHelpRenderBudget(1, 1, 1, 0)  # type: ignore[index]


def test_surface_limits_is_the_single_expected_value() -> None:
    assert isinstance(SURFACE_LIMITS, SurfaceLimits)
    values = tuple(getattr(SURFACE_LIMITS, field.name) for field in fields(SurfaceLimits))
    assert all(value > 0 for value in values)


def test_surface_limits_field_names() -> None:
    field_names = {f.name for f in fields(SurfaceLimits)}
    assert field_names == {
        "root_help_max_lines",
        "root_help_max_codepoints",
        "focused_help_max_lines",
        "focused_help_max_codepoints",
        "object_contract_max_subjects",
        "object_contract_render_max_lines",
        "object_contract_render_max_codepoints",
        "help_suggestion_limit",
    }


def test_surface_limits_is_frozen() -> None:
    with pytest.raises(Exception):
        SURFACE_LIMITS.root_help_max_lines = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Absence from public mv.__all__
# ---------------------------------------------------------------------------

_KERNEL_TYPE_NAMES = [
    "AuthorityPolicy",
    "CapabilityBase",
    "OperatorCapability",
    "ConstructorCapability",
    "ReadCapability",
    "RecoveryCapability",
    "BoundaryCapability",
    "SameAsInputFamily",
    "SurfaceLimits",
    "SURFACE_LIMITS",
    "CapabilityDescriptor",
    "AnalysisHelpDescriptor",
    "AnalysisNavigationTopic",
    "AnalysisMethodFamily",
    "AnalysisArtifactFamilyContract",
    "AnalysisHelpRenderBudget",
    "AnalysisHelpRenderClass",
    "EpistemicKind",
    "ANALYSIS_HELP_RENDER_BUDGETS",
    "ROOT_GROUP_ORDER",
    "ARTIFACT_FAMILIES",
]


def test_kernel_types_absent_from_mv_all() -> None:
    import marivo.analysis as mv

    for name in _KERNEL_TYPE_NAMES:
        assert name not in mv.__all__, f"{name} must not appear in mv.__all__"


# ---------------------------------------------------------------------------
# Registry: uniqueness and identity
# ---------------------------------------------------------------------------


def test_registry_has_no_duplicate_ids() -> None:
    ids = [d.id for d in REGISTRY.help_descriptors]
    assert len(ids) == len(set(ids)), f"duplicate ids: {ids}"


def test_registry_has_no_duplicate_help_targets() -> None:
    targets = [d.help_target for d in REGISTRY.help_descriptors]
    assert len(targets) == len(set(targets)), f"duplicate help_targets: {targets}"


def test_registry_has_no_duplicate_callable_paths() -> None:
    paths = [d.callable_path for d in REGISTRY.descriptors if d.callable_path is not None]
    assert len(paths) == len(set(paths)), f"duplicate callable_paths: {paths}"


def test_registry_rejects_duplicate_callable_paths() -> None:
    """The registry must raise ValueError when two descriptors share the
    same callable_path, not silently ignore the collision."""
    from marivo.analysis._capabilities.model import ReadCapability
    from marivo.analysis._capabilities.registry import _finalize_registry

    desc_a = ReadCapability(
        id="test.dup_a",
        public_entrypoint="test.dup_a()",
        help_target="test.dup_a",
        summary="first",
        callable_path="some.module.fn",
        receiver_family="TestType",
        result_kind="immutable_metadata",
        read_bound="bounded",
    )
    desc_b = ReadCapability(
        id="test.dup_b",
        public_entrypoint="test.dup_b()",
        help_target="test.dup_b",
        summary="second",
        callable_path="some.module.fn",
        receiver_family="TestType",
        result_kind="immutable_metadata",
        read_bound="bounded",
    )
    with pytest.raises(ValueError, match="duplicate callable_path"):
        _finalize_registry((desc_a, desc_b))


def _validate_topology_fixture(
    *,
    navigation_topics: tuple[AnalysisNavigationTopic, ...] | None = None,
    method_families: tuple[AnalysisMethodFamily, ...] | None = None,
    artifact_contracts: tuple[AnalysisArtifactFamilyContract, ...] | None = None,
    discovery_memberships: Mapping[str, tuple[LiveHelpTarget, ...]] | None = None,
    root_members: tuple[LiveHelpTarget, ...] | None = None,
    render_budgets: Mapping[
        AnalysisHelpRenderClass,
        AnalysisHelpRenderBudget,
    ] = ANALYSIS_HELP_RENDER_BUDGETS,
) -> None:
    from marivo.analysis._capabilities.registry import _validate_help_topology

    _validate_help_topology(
        descriptors=REGISTRY.descriptors,
        navigation_topics=(
            REGISTRY.navigation_topics if navigation_topics is None else navigation_topics
        ),
        method_families=(REGISTRY.method_families if method_families is None else method_families),
        artifact_contracts=(
            REGISTRY.artifact_contracts if artifact_contracts is None else artifact_contracts
        ),
        discovery_memberships=(
            REGISTRY.discovery_memberships
            if discovery_memberships is None
            else discovery_memberships
        ),
        root_members=REGISTRY.root_members if root_members is None else root_members,
        render_budgets=render_budgets,
    )


@pytest.mark.parametrize("member_count", (0, 1))
def test_registry_rejects_singleton_navigation(member_count: int) -> None:
    members = tuple(
        LiveHelpTarget(surface="analysis", canonical_id=str(index)) for index in range(member_count)
    )
    invalid = AnalysisNavigationTopic(
        canonical_id="invalid.navigation",
        summary="Invalid navigation.",
        render_class="navigation",
        members=members,
    )

    with pytest.raises(ValueError, match="at least two members"):
        _validate_topology_fixture(
            navigation_topics=(*REGISTRY.navigation_topics, invalid),
            root_members=(),
        )


def test_registry_rejects_duplicate_navigation_members() -> None:
    target = LiveHelpTarget(surface="analysis", canonical_id="same")
    invalid = AnalysisNavigationTopic(
        canonical_id="invalid.navigation",
        summary="Invalid navigation.",
        render_class="navigation",
        members=(target, target),
    )

    with pytest.raises(ValueError, match="duplicate members"):
        _validate_topology_fixture(
            navigation_topics=(*REGISTRY.navigation_topics, invalid),
            root_members=(),
        )


def test_registry_rejects_empty_method_family_epistemic_kind() -> None:
    invalid = AnalysisMethodFamily(
        canonical_id="invalid.methods",
        summary="Invalid methods.",
        epistemic_kinds=(),
        members=(
            LiveHelpTarget(surface="analysis", canonical_id="one"),
            LiveHelpTarget(surface="analysis", canonical_id="two"),
        ),
        input_routes=(),
        output_routes=(),
    )

    with pytest.raises(ValueError, match="requires an epistemic kind"):
        _validate_topology_fixture(method_families=(invalid,))


def test_registry_rejects_singleton_method_family() -> None:
    invalid = AnalysisMethodFamily(
        canonical_id="invalid.methods",
        summary="Invalid methods.",
        epistemic_kinds=("observed",),
        members=(LiveHelpTarget(surface="analysis", canonical_id="one"),),
        input_routes=(),
        output_routes=(),
    )

    with pytest.raises(ValueError, match="at least two members"):
        _validate_topology_fixture(method_families=(invalid,))


@pytest.mark.parametrize("field_name", ("public_entrypoint", "callable_path"))
def test_registry_rejects_invokable_navigation(field_name: str) -> None:
    invalid = AnalysisNavigationTopic(
        canonical_id="invalid.navigation",
        summary="Invalid navigation.",
        render_class="navigation",
        members=(
            LiveHelpTarget(surface="analysis", canonical_id="one"),
            LiveHelpTarget(surface="analysis", canonical_id="two"),
        ),
    )
    object.__setattr__(invalid, field_name, "invalid.callable")

    with pytest.raises(ValueError, match="must not be invokable"):
        _validate_topology_fixture(
            navigation_topics=(*REGISTRY.navigation_topics, invalid),
            root_members=(),
        )


def test_registry_rejects_duplicate_help_canonical_ids() -> None:
    duplicate = AnalysisNavigationTopic(
        canonical_id="entry",
        summary="Duplicate entry.",
        render_class="decision_hub",
        members=(
            LiveHelpTarget(surface="analysis", canonical_id="one"),
            LiveHelpTarget(surface="analysis", canonical_id="two"),
        ),
    )

    with pytest.raises(ValueError, match="duplicate analysis help canonical id"):
        _validate_topology_fixture(
            navigation_topics=(*REGISTRY.navigation_topics, duplicate),
        )


def test_registry_rejects_navigation_id_colliding_with_exact_canonical_id() -> None:
    exact = REGISTRY.by_id("catalog.period_calendars.period")
    assert exact.help_target == "calendar.period"
    duplicate = AnalysisNavigationTopic(
        canonical_id=exact.canonical_id,
        summary="Duplicate exact capability identity.",
        render_class="navigation",
        members=(
            LiveHelpTarget(surface="analysis", canonical_id="one"),
            LiveHelpTarget(surface="analysis", canonical_id="two"),
        ),
    )

    with pytest.raises(ValueError, match="duplicate analysis help canonical id"):
        _validate_topology_fixture(
            navigation_topics=(*REGISTRY.navigation_topics, duplicate),
            root_members=(),
        )


def test_registry_rejects_method_family_id_colliding_with_exact_canonical_id() -> None:
    exact = REGISTRY.by_id("catalog.period_calendars.period")
    assert exact.help_target == "calendar.period"
    duplicate = AnalysisMethodFamily(
        canonical_id=exact.canonical_id,
        summary="Duplicate exact capability identity.",
        epistemic_kinds=("observed",),
        members=(
            LiveHelpTarget(surface="analysis", canonical_id="one"),
            LiveHelpTarget(surface="analysis", canonical_id="two"),
        ),
        input_routes=(),
        output_routes=(),
    )

    with pytest.raises(ValueError, match="duplicate analysis help canonical id"):
        _validate_topology_fixture(method_families=(duplicate,))


def test_registry_rejects_missing_render_budget() -> None:
    budgets = dict(ANALYSIS_HELP_RENDER_BUDGETS)
    budgets.pop("current_briefing")

    with pytest.raises(ValueError, match="cover every render class"):
        _validate_topology_fixture(render_budgets=budgets)  # type: ignore[arg-type]


def test_registry_rejects_unknown_navigation_render_class() -> None:
    invalid = AnalysisNavigationTopic(
        canonical_id="invalid.navigation",
        summary="Invalid navigation.",
        render_class="unknown",  # type: ignore[arg-type]
        members=(
            LiveHelpTarget(surface="analysis", canonical_id="one"),
            LiveHelpTarget(surface="analysis", canonical_id="two"),
        ),
    )

    with pytest.raises(ValueError, match="unknown analysis navigation render class"):
        _validate_topology_fixture(
            navigation_topics=(*REGISTRY.navigation_topics, invalid),
            root_members=(),
        )


def test_registry_rejects_illegal_root_edge() -> None:
    invalid_root = (
        *REGISTRY.root_members[:-1],
        LiveHelpTarget(surface="analysis", canonical_id="compare"),
    )

    with pytest.raises(ValueError, match="root edges"):
        _validate_topology_fixture(root_members=invalid_root)


def test_registry_rejects_duplicate_discovery_ownership() -> None:
    memberships = dict(REGISTRY.discovery_memberships)
    memberships["inputs"] = (
        *memberships["inputs"],
        LiveHelpTarget(surface="analysis", canonical_id="observe"),
    )

    with pytest.raises(ValueError, match="duplicate analysis discovery owner"):
        _validate_topology_fixture(discovery_memberships=memberships)


def test_registry_rejects_missing_discovery_owner() -> None:
    memberships = dict(REGISTRY.discovery_memberships)
    memberships["methods"] = tuple(
        target for target in memberships["methods"] if target.canonical_id != "observe"
    )

    with pytest.raises(ValueError, match="lack a discovery owner: observe"):
        _validate_topology_fixture(discovery_memberships=memberships)


def test_registry_rejects_dead_discovery_edge() -> None:
    memberships = dict(REGISTRY.discovery_memberships)
    memberships["inputs.scope"] = (
        *memberships["inputs.scope"],
        LiveHelpTarget(surface="analysis", canonical_id="missing.scope"),
    )

    with pytest.raises(ValueError, match="dead analysis discovery edge"):
        _validate_topology_fixture(discovery_memberships=memberships)


def test_registry_rejects_dead_method_family_cross_link() -> None:
    change = REGISTRY.by_help_target("methods.change")
    assert isinstance(change, AnalysisMethodFamily)
    drifted = replace(
        change,
        input_routes=(LiveHelpTarget(surface="analysis", canonical_id="missing.input"),),
    )
    method_families = tuple(
        drifted if family is change else family for family in REGISTRY.method_families
    )

    with pytest.raises(ValueError, match="dead method-family cross-link"):
        _validate_topology_fixture(method_families=method_families)


def test_registry_rejects_dead_and_over_budget_static_cross_links() -> None:
    from marivo.analysis._capabilities.registry import _validate_cross_links

    with pytest.raises(ValueError, match="dead cross-link"):
        _validate_cross_links(
            help_descriptors=REGISTRY.help_descriptors,
            cross_links={
                "artifacts.reading": (
                    LiveHelpTarget(surface="analysis", canonical_id="missing.read"),
                )
            },
            render_budgets=REGISTRY.render_budgets,
        )

    with pytest.raises(ValueError, match="exposes 11 routes"):
        _validate_cross_links(
            help_descriptors=REGISTRY.help_descriptors,
            cross_links={
                "MetricFrame": (
                    *REGISTRY.cross_links("MetricFrame"),
                    LiveHelpTarget(surface="analysis", canonical_id="MetricFrame"),
                )
            },
            render_budgets=REGISTRY.render_budgets,
        )


def test_parameter_help_required_state_matches_live_defaults() -> None:
    expected = {
        ("observe", "grain"): False,
        ("events.time_to_event", "start_step"): True,
        ("events.time_to_event", "end_step"): True,
        ("transform.normalize", "mode"): True,
        ("transform.normalize", "baseline"): False,
    }
    for (target, parameter), required in expected.items():
        descriptor = REGISTRY.by_help_target(target)
        assert isinstance(descriptor, OperatorCapability)
        assert descriptor.parameter_help[parameter].required is required


def test_registry_rejects_parameter_name_and_default_drift() -> None:
    from marivo.analysis._capabilities.registry import _validate_parameter_help

    observe = REGISTRY.by_help_target("observe")
    assert isinstance(observe, OperatorCapability)
    grain = observe.parameter_help["grain"]

    missing = replace(observe, parameter_help={"missing": grain})
    with pytest.raises(ValueError, match="parameter is absent from live signature"):
        _validate_parameter_help((missing,))

    wrong_required = replace(
        observe,
        parameter_help={**observe.parameter_help, "grain": replace(grain, required=True)},
    )
    with pytest.raises(ValueError, match="required state disagrees with live default"):
        _validate_parameter_help((wrong_required,))


def test_registry_rejects_non_behavioral_derivation_witness() -> None:
    from marivo.analysis._capabilities.registry import _validate_parameter_help

    elapsed = REGISTRY.by_help_target("events.time_to_event")
    assert isinstance(elapsed, OperatorCapability)
    start_step = elapsed.parameter_help["start_step"]
    drifted = replace(
        elapsed,
        parameter_help={
            **elapsed.parameter_help,
            "start_step": replace(
                start_step,
                acquisition="select journeys.meta.nonexistent",
            ),
        },
    )

    with pytest.raises(ValueError, match="behavioral derivation witness failed"):
        _validate_parameter_help((drifted,))


def test_registry_rejects_artifact_output_shape_drift() -> None:
    from marivo.analysis._capabilities.registry import _validate_artifact_contract_alignment

    observe = REGISTRY.by_help_target("observe")
    assert isinstance(observe, OperatorCapability)
    drifted = replace(
        observe,
        output_contract=replace(
            observe.output_contract,
            semantic_shapes=frozenset({"unknown_shape"}),
        ),
    )
    descriptors = tuple(
        drifted if descriptor is observe else descriptor for descriptor in REGISTRY.descriptors
    )
    contracts = {contract.artifact_family: contract for contract in REGISTRY.artifact_contracts}

    with pytest.raises(ValueError, match="output shapes drift from MetricFrame"):
        _validate_artifact_contract_alignment(descriptors, contracts)


def test_registry_rejects_artifact_public_member_drift() -> None:
    from marivo.analysis._capabilities.registry import _validate_artifact_contract_alignment

    contracts = {contract.artifact_family: contract for contract in REGISTRY.artifact_contracts}
    metric = contracts["MetricFrame"]
    contracts["MetricFrame"] = replace(
        metric,
        specialized_member_targets=metric.specialized_member_targets[:-1],
    )

    with pytest.raises(ValueError, match="specialized public-member allowlist drift"):
        _validate_artifact_contract_alignment(REGISTRY.descriptors, contracts)


def test_registry_rejects_nonexistent_artifact_public_property(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marivo.analysis._capabilities.registry as registry_module
    from marivo.analysis._capabilities.registry import _validate_artifact_public_members

    properties = dict(registry_module.PUBLIC_FRAME_PROPERTIES)
    properties["MetricFrame"] = (*properties["MetricFrame"], "does_not_exist")
    monkeypatch.setattr(
        registry_module,
        "PUBLIC_FRAME_PROPERTIES",
        MappingProxyType(properties),
    )
    contracts = {contract.artifact_family: contract for contract in REGISTRY.artifact_contracts}

    with pytest.raises(ValueError, match=r"MetricFrame\.does_not_exist"):
        _validate_artifact_public_members(contracts)


def test_registry_by_id_returns_same_object() -> None:
    for descriptor in REGISTRY.descriptors:
        assert REGISTRY.by_id(descriptor.id) is descriptor


def test_registry_by_help_target_returns_same_object() -> None:
    for descriptor in REGISTRY.help_descriptors:
        resolved = REGISTRY.by_help_target(descriptor.help_target)
        assert resolved is descriptor


# ---------------------------------------------------------------------------
# Registry: callable identity index
# ---------------------------------------------------------------------------


def test_by_callable_resolves_session_observe() -> None:
    from marivo.analysis.session.core import Session

    descriptor = REGISTRY.by_callable(Session.observe)
    assert descriptor.id == "observe"


def test_by_callable_resolves_session_compare() -> None:
    from marivo.analysis.session.core import Session

    descriptor = REGISTRY.by_callable(Session.compare)
    assert descriptor.id == "compare"


def test_by_callable_resolves_constructors() -> None:
    import marivo.analysis as mv

    descriptor = REGISTRY.by_callable(mv.window_bucket)
    assert descriptor.id == "window_bucket"


def test_by_callable_resolves_types() -> None:
    import marivo.analysis as mv

    descriptor = REGISTRY.by_callable(mv.time_scope)
    assert descriptor.id == "time_scope"

    descriptor = REGISTRY.by_callable(mv.SamplingPolicy)
    assert descriptor.id == "SamplingPolicy"


def test_by_callable_resolves_semantic_catalog_properties() -> None:
    """Property objects on SemanticCatalog must resolve via their fget getter."""
    from marivo.semantic.catalog import SemanticCatalog

    descriptor = REGISTRY.by_callable(SemanticCatalog.domains)
    assert descriptor.id == "catalog.domains"

    descriptor = REGISTRY.by_callable(SemanticCatalog.metrics)
    assert descriptor.id == "catalog.metrics"

    descriptor = REGISTRY.by_callable(SemanticCatalog.dimensions)
    assert descriptor.id == "catalog.dimensions"


def test_by_callable_resolves_period_calendar_period_navigation() -> None:
    from marivo.semantic.catalog import PeriodCalendarEntry

    descriptor = REGISTRY.by_callable(PeriodCalendarEntry.period)
    assert descriptor.id == "catalog.period_calendars.period"
    assert descriptor.help_target == "calendar.period"
    assert descriptor.produced_input_family == "TimeScopeInput"


# ---------------------------------------------------------------------------
# Registry: capability ids coverage
# ---------------------------------------------------------------------------


EXPECTED_OPERATOR_IDS = {
    "observe",
    "compare",
    "attribute",
    "correlate",
    "hypothesis_test",
    "forecast",
    "assess_quality",
    "discover.point_anomalies",
    "discover.period_shifts",
    "discover.driver_axes",
    "discover.interesting_slices",
    "discover.interesting_windows",
    "discover.cross_sectional_outliers",
    "transform.filter",
    "transform.slice",
    "transform.rollup",
    "transform.topk",
    "transform.bottomk",
    "transform.rank",
    "transform.window",
    "transform.normalize",
    "MetricFrame.metric",
    "MetricFrame.components",
    "MetricFrame.coverage",
    "DeltaFrame.components",
}


EXPECTED_CONSTRUCTOR_IDS = {
    "window_bucket",
    "day_of_week",
    "period_progress",
    "period_correspondence",
    "time_scope",
    "AbsoluteWindow",
    "SamplingPolicy",
}


EXPECTED_BOUNDARY_IDS = {
    "boundary.to_pandas",
}


def test_all_expected_operator_ids_registered() -> None:
    ids = set(REGISTRY.capability_ids)
    missing = EXPECTED_OPERATOR_IDS - ids
    assert not missing, f"missing operator ids: {missing}"


SEMANTIC_CURRENT_OPERATOR_IDS = {
    "observe",
    "events.match",
    "events.funnel",
    "events.time_to_event",
    "lifecycle.replay",
    "lifecycle.distribution",
    "select_subjects",
    "attribute",
    "discover.semantic_hypotheses",
}


def test_operator_authority_policy_matrix_is_closed() -> None:
    operators = {
        descriptor.id: descriptor.authority_policy
        for descriptor in REGISTRY.descriptors
        if isinstance(descriptor, OperatorCapability)
    }

    assert set(get_args(AuthorityPolicy)) == {"semantic_current", "materialized_only"}
    assert {
        capability_id for capability_id, policy in operators.items() if policy == "semantic_current"
    } == SEMANTIC_CURRENT_OPERATOR_IDS
    assert all(policy in get_args(AuthorityPolicy) for policy in operators.values())
    assert set(operators) - SEMANTIC_CURRENT_OPERATOR_IDS == {
        capability_id
        for capability_id, policy in operators.items()
        if policy == "materialized_only"
    }


def test_operator_authority_policy_is_required_and_unknown_values_fail_closed() -> None:
    from marivo.analysis._capabilities.registry import _validate_authority_policies

    parameter = inspect.signature(OperatorCapability).parameters["authority_policy"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY

    invalid = OperatorCapability(
        id="test.invalid_authority",
        public_entrypoint="session.invalid_authority()",
        help_target="invalid_authority",
        summary="test",
        authority_policy="unknown",  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match=r"test\.invalid_authority:unknown"):
        _validate_authority_policies((invalid,))


def test_all_expected_constructor_ids_registered() -> None:
    ids = set(REGISTRY.capability_ids)
    missing = EXPECTED_CONSTRUCTOR_IDS - ids
    assert not missing, f"missing constructor ids: {missing}"


def test_all_expected_boundary_ids_registered() -> None:
    ids = set(REGISTRY.capability_ids)
    missing = EXPECTED_BOUNDARY_IDS - ids
    assert not missing, f"missing boundary ids: {missing}"


def test_boundary_to_pandas_accepted_inputs_cover_all_families() -> None:
    desc = REGISTRY.by_id("boundary.to_pandas")
    assert desc.kind == "boundary"
    receiver = desc.accepted_inputs.get("receiver", frozenset())
    assert frozenset(receiver) == frozenset(ARTIFACT_FAMILIES)


def test_constructor_consumers_includes_boundary_capabilities() -> None:
    """boundary.to_pandas must appear as a consumer in the reverse index
    because it declares accepted_inputs."""
    cc = REGISTRY.constructor_consumers

    # boundary.to_pandas accepts all artifact families as receiver.
    for family in ARTIFACT_FAMILIES:
        assert "boundary.to_pandas" in cc.get(family, ()), (
            f"boundary.to_pandas missing from consumers of {family}"
        )


def test_candidate_set_select_is_read_not_operator() -> None:
    """CandidateSet.select returns an immutable selected value (a scalar),
    not an artifact frame.  It must be modelled as a ReadCapability with
    ``result_kind="defensive_copy"``, not as an OperatorCapability with an
    ``output_family``."""
    desc = REGISTRY.by_id("CandidateSet.select")
    assert isinstance(desc, ReadCapability)
    assert desc.kind == "read"
    assert desc.result_kind == "defensive_copy"
    assert desc.read_bound == "bounded"
    assert desc.receiver_family == "CandidateSet"
    assert not hasattr(desc, "output_family")


# ---------------------------------------------------------------------------
# Registry: reads and recovery coverage
# ---------------------------------------------------------------------------


def test_session_recovery_methods_registered() -> None:
    ids = set(REGISTRY.capability_ids)
    for expected in (
        "session.get_or_create",
        "session.current",
        "session.resume",
        "session.recent",
        "session.inspect",
        "session.delete",
        "session.jobs",
        "session.recent_jobs",
        "session.job",
        "session.frame_summaries",
        "session.get_frame",
    ):
        assert expected in ids, f"missing recovery/read id: {expected}"


def test_session_evidence_methods_registered() -> None:
    ids = set(REGISTRY.capability_ids)
    for expected in (
        "session.evidence.findings",
        "session.evidence.digests",
        "session.evidence.digest",
        "session.evidence.finding",
        "session.evidence.trace",
    ):
        assert expected in ids, f"missing evidence id: {expected}"


def test_base_frame_reads_registered() -> None:
    ids = set(REGISTRY.capability_ids)
    assert "BaseFrame.show" in ids
    assert "BaseFrame.contract" in ids
    assert "BaseFrame.to_pandas" in ids or "boundary.to_pandas" in ids


def test_session_display_reads_registered() -> None:
    ids = set(REGISTRY.capability_ids)
    assert "Session.render" in ids
    assert "Session.show" in ids


def test_help_and_help_text_registered() -> None:
    ids = set(REGISTRY.capability_ids)
    assert "help" not in ids
    assert "help_text" not in ids


# ---------------------------------------------------------------------------
# Registry: grouping descriptors
# ---------------------------------------------------------------------------


def test_grouping_descriptors_exist() -> None:
    for topic in (
        "session",
        "catalog",
        "discover",
        "transform",
        "events",
        "lifecycle",
        "recovery",
        "boundary",
        "artifacts",
    ):
        desc = REGISTRY.by_help_target(topic)
        assert desc is not None, f"missing grouping descriptor for {topic}"


def test_grouping_descriptors_are_not_invokable() -> None:
    """Grouping descriptors must not have a callable_path."""
    for topic in (
        "session",
        "catalog",
        "discover",
        "transform",
        "events",
        "lifecycle",
        "recovery",
        "boundary",
        "artifacts",
    ):
        desc = REGISTRY.by_help_target(topic)
        assert desc.callable_path is None, f"{topic} grouping must not be invokable"
        if isinstance(desc, (AnalysisNavigationTopic, AnalysisMethodFamily)):
            assert desc.public_entrypoint is None
        else:
            assert desc.public_entrypoint == f'marivo.help("analysis.{topic}")'


def test_registry_rejects_type_variant_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import MappingProxyType

    import marivo.analysis._capabilities.registry as registry_module

    variants = dict(registry_module.PUBLIC_TYPE_VARIANTS)
    variants["QualityReport"] = variants["QualityReport"][:-1]
    monkeypatch.setattr(
        registry_module,
        "PUBLIC_TYPE_VARIANTS",
        MappingProxyType(variants),
    )

    with pytest.raises(ValueError, match="QualityReport help variants"):
        registry_module._validate_public_type_variants()


# ---------------------------------------------------------------------------
# Registry: constraint validation
# ---------------------------------------------------------------------------


def test_all_constraint_ids_are_valid() -> None:
    from marivo.analysis.constraints import CONSTRAINTS

    valid_ids = set(CONSTRAINTS.keys())
    for desc in REGISTRY.descriptors:
        for cid in desc.constraint_ids:
            cid_str = str(cid)
            assert cid_str in valid_ids, (
                f"descriptor {desc.id} references unknown constraint {cid_str}"
            )


# ---------------------------------------------------------------------------
# Registry: accepted input/output family validation
# ---------------------------------------------------------------------------


_VALID_INPUT_FAMILIES = set(ARTIFACT_FAMILIES) | {
    "MetricSemantic",
    "OntologyMetricCandidate",
    "RuntimeMetricExpression",
    "DimensionSemantic",
    "TimeDimensionSemantic",
    "SemanticProject",
    "AlignmentPolicy",
    "SamplingPolicy",
    "TimeScopeInput",
    "EventPattern",
    "EventMatchingPolicy",
    "CompletenessDeclaration",
    "SubjectSelection",
    "FunnelLossRate",
    "StateModelSemantic",
    "LifecycleSeed",
}

_VALID_OUTPUT_FAMILIES = set(ARTIFACT_FAMILIES) | {
    "pandas.DataFrame",
    "immutable selected value",
}


def test_operator_accepted_inputs_use_valid_families() -> None:
    for desc in REGISTRY.descriptors:
        if desc.kind != "operator":
            continue
        for param, families in desc.accepted_inputs.items():
            for family in families:
                assert family in _VALID_INPUT_FAMILIES, (
                    f"descriptor {desc.id} param {param} has invalid input family {family}"
                )


def test_operator_output_families_are_valid() -> None:
    for desc in REGISTRY.descriptors:
        if desc.kind != "operator":
            continue
        output = desc.output_family
        if isinstance(output, SameAsInputFamily):
            continue
        assert output in _VALID_OUTPUT_FAMILIES, (
            f"descriptor {desc.id} has invalid output family {output}"
        )


def test_operator_return_annotations_match_output_contracts() -> None:
    from marivo.introspection.live.reflect import (
        import_registered_callable,
        return_annotation_mismatch,
    )

    mismatches: list[str] = []
    for desc in REGISTRY.descriptors:
        if desc.kind != "operator" or desc.callable_path is None:
            continue
        mismatch = return_annotation_mismatch(
            import_registered_callable(desc.callable_path),
            expected_family=desc.output_contract.family,
            nullable=desc.output_contract.nullable,
        )
        if mismatch is not None:
            mismatches.append(f"{desc.id}: {mismatch}")
    assert not mismatches, "Output contracts disagree with return annotations:\n  " + "\n  ".join(
        mismatches
    )


# ---------------------------------------------------------------------------
# Registry: public member coverage (no silent reflection gaps)
# ---------------------------------------------------------------------------


def test_every_delegating_session_operator_is_registered() -> None:
    from marivo.analysis.session.core import Session

    intent_methods = [
        "observe",
        "compare",
        "attribute",
        "correlate",
        "forecast",
        "assess_quality",
        "hypothesis_test",
    ]
    for name in intent_methods:
        method = getattr(Session, name)
        desc = REGISTRY.by_callable(method)
        assert desc is not None, f"Session.{name} is not registered"


def test_every_discover_method_is_registered() -> None:
    from marivo.analysis.session.core import SessionDiscoverNamespace

    discover_methods = [
        "semantic_hypotheses",
        "point_anomalies",
        "period_shifts",
        "driver_axes",
        "interesting_slices",
        "interesting_windows",
        "cross_sectional_outliers",
    ]
    for name in discover_methods:
        method = getattr(SessionDiscoverNamespace, name)
        desc = REGISTRY.by_callable(method)
        assert desc is not None, f"SessionDiscoverNamespace.{name} is not registered"


def test_every_transform_method_is_registered() -> None:
    from marivo.analysis.frames.transforms import (
        DeltaFrameTransforms,
        MetricFrameTransforms,
    )

    shared_ops = ("filter", "slice", "rollup", "topk", "bottomk", "rank", "window")
    for op in shared_ops:
        desc = REGISTRY.by_callable(getattr(MetricFrameTransforms, op))
        assert desc is not None, f"MetricFrameTransforms.{op} not registered"
        desc = REGISTRY.by_callable(getattr(DeltaFrameTransforms, op))
        assert desc is not None, f"DeltaFrameTransforms.{op} not registered"

    desc = REGISTRY.by_callable(MetricFrameTransforms.normalize)
    assert desc is not None, "MetricFrameTransforms.normalize not registered"


def test_frame_methods_allowlist_matches_registered() -> None:
    """Every method in PUBLIC_FRAME_METHODS must have a registered descriptor
    or be explicitly excluded."""
    from marivo.analysis._capabilities.registry import PUBLIC_FRAME_METHODS

    # BaseFrame.to_pandas is registered as boundary.to_pandas (terminal exit).
    id_aliases: Mapping[str, str] = {
        "BaseFrame.to_pandas": "boundary.to_pandas",
    }

    for class_name, method_names in PUBLIC_FRAME_METHODS.items():
        for method_name in method_names:
            cap_id = f"{class_name}.{method_name}"
            expected_ids = {cap_id, id_aliases.get(cap_id, cap_id)}
            ids = set(REGISTRY.capability_ids)
            assert any(eid in ids for eid in expected_ids if eid), (
                f"{class_name}.{method_name} in allowlist but not registered"
            )


def test_frame_properties_allowlist_is_complete() -> None:
    from marivo.analysis._capabilities.registry import PUBLIC_FRAME_PROPERTIES

    # Every frame class in the allowlist must be a registered type
    for class_name in PUBLIC_FRAME_PROPERTIES:
        assert class_name in set(ARTIFACT_FAMILIES) or class_name == "BaseFrame", (
            f"unknown frame class in properties allowlist: {class_name}"
        )


# ---------------------------------------------------------------------------
# Registry: reflection-based coverage (no silent reflection gaps)
# ---------------------------------------------------------------------------

# Methods that are intentionally excluded from the capability registry.
# Each entry is documented with the reason for exclusion.
_REFLECTION_EXCLUDED: dict[str, str] = {
    # -- Session lifecycle ------------------------------------------------
    "Session.close": "lifecycle management, not an analysis capability",
    # -- Rendering and introspection utilities inherited from RenderableResult
    "BaseFrame.describe": "rendering utility from RenderableResult mixin",
    "BaseFrame.plot": "rendering utility from RenderableResult mixin",
    "BaseFrame.render": "rendering utility from RenderableResult mixin",
    "MetricFrame.describe": "rendering utility from RenderableResult mixin",
    "MetricFrame.plot": "rendering utility from RenderableResult mixin",
    "MetricFrame.render": "rendering utility from RenderableResult mixin",
    "DeltaFrame.describe": "rendering utility from RenderableResult mixin",
    "DeltaFrame.plot": "rendering utility from RenderableResult mixin",
    "DeltaFrame.render": "rendering utility from RenderableResult mixin",
    "AttributionFrame.describe": "rendering utility from RenderableResult mixin",
    "AttributionFrame.plot": "rendering utility from RenderableResult mixin",
    "AttributionFrame.render": "rendering utility from RenderableResult mixin",
    "CandidateSet.describe": "rendering utility from RenderableResult mixin",
    "CandidateSet.plot": "rendering utility from RenderableResult mixin",
    "CandidateSet.render": "rendering utility from RenderableResult mixin",
    # -- Per-class contract overrides: MetricFrame and DeltaFrame override
    #    BaseFrame.contract with their own gating logic, so their callable
    #    paths differ from the registered BaseFrame.contract canonical path.
    #    AttributionFrame also specializes contract for hierarchy resolution views.
    "MetricFrame.contract": "override of BaseFrame.contract, registered via canonical path",
    "DeltaFrame.contract": "override of BaseFrame.contract, registered via canonical path",
    "AttributionFrame.contract": "override of BaseFrame.contract, registered via canonical path",
    "CandidateSet.contract": "override of BaseFrame.contract, registered via canonical path",
    # -- Internal metadata accessor
    "MetricFrame.measures_meta": "internal metadata accessor, not a public capability",
}


def test_reflection_all_public_methods_registered_or_excluded() -> None:
    """Every public method discovered via reflection on Session, frame types,
    and transform namespaces must be either registered in the registry or
    named in the explicit exclusion set.

    This prevents silent gaps when new public methods are added without
    updating the capability registry.
    """
    import inspect

    from marivo.analysis._capabilities.registry import _module_path_for
    from marivo.analysis.frames.attribution import AttributionFrame
    from marivo.analysis.frames.base import BaseFrame
    from marivo.analysis.frames.candidate import CandidateSet
    from marivo.analysis.frames.delta import DeltaFrame
    from marivo.analysis.frames.metric import MetricFrame
    from marivo.analysis.frames.transforms import (
        DeltaFrameTransforms,
        MetricFrameTransforms,
    )
    from marivo.analysis.session.core import (
        Session,
        SessionDiscoverNamespace,
    )

    registered_paths = {
        d.callable_path for d in REGISTRY.descriptors if d.callable_path is not None
    }

    classes: list[tuple[type, str]] = [
        (Session, "Session"),
        (SessionDiscoverNamespace, "SessionDiscoverNamespace"),
        (BaseFrame, "BaseFrame"),
        (MetricFrame, "MetricFrame"),
        (DeltaFrame, "DeltaFrame"),
        (AttributionFrame, "AttributionFrame"),
        (CandidateSet, "CandidateSet"),
        (MetricFrameTransforms, "MetricFrameTransforms"),
        (DeltaFrameTransforms, "DeltaFrameTransforms"),
    ]

    unaccounted: list[str] = []

    for cls, cls_name in classes:
        for name, member in inspect.getmembers(cls):
            if name.startswith("_"):
                continue
            if not callable(member):
                continue
            # Skip properties and class attributes that are not functions
            if not (inspect.isfunction(member) or inspect.ismethod(member)):
                continue
            # Check by callable_path
            path = _module_path_for(member)
            qualified = f"{cls_name}.{name}"
            if path in registered_paths:
                continue
            if qualified in _REFLECTION_EXCLUDED:
                continue
            # Also check by descriptor id convention: ClassName.method_name
            cap_id = f"{cls_name}.{name}"
            if cap_id in set(REGISTRY.capability_ids):
                continue
            # Frame subclasses inherit the single BaseFrame.show capability.
            # Its registry path is deliberately owner-qualified so telemetry
            # does not wrap RenderableResult.show across other surfaces.
            if (
                issubclass(cls, BaseFrame)
                and name == "show"
                and "BaseFrame.show" in set(REGISTRY.capability_ids)
            ):
                continue
            # Check if registered via an alias (e.g. boundary.to_pandas)
            if cls_name == "BaseFrame" and name == "to_pandas":
                continue
            unaccounted.append(f"{qualified} (path={path}) not registered and not in exclusion set")

    assert not unaccounted, (
        "Public methods not accounted for in registry or exclusion set:\n  "
        + "\n  ".join(unaccounted)
    )


# ---------------------------------------------------------------------------
# Registry: immutability
# ---------------------------------------------------------------------------


def test_registry_is_immutable() -> None:
    """The REGISTRY singleton must not allow mutation of its internal state."""
    with pytest.raises((AttributeError, TypeError)):
        REGISTRY.descriptors = ()  # type: ignore[misc]


def test_registry_descriptors_is_a_tuple() -> None:
    assert isinstance(REGISTRY.descriptors, tuple)
    assert len(REGISTRY.descriptors) > 0


# ---------------------------------------------------------------------------
# Registry: accepted-input keys drift against installed callable signature
# ---------------------------------------------------------------------------


def _installed_signature_parameter_names(callable_path: str) -> set[str]:
    """Return the public parameter names of the callable at ``callable_path``.

    Resolves through the same ``import_registered_callable`` helper the live
    help renderer uses so the drift check mirrors what ``mv.help(...)`` sees.
    """
    from marivo.introspection.live.reflect import import_registered_callable

    callable_obj = import_registered_callable(callable_path)
    return {
        parameter.name
        for parameter in inspect.signature(callable_obj).parameters.values()
        if parameter.name != "self"
    }


def test_operator_accepted_input_keys_exist_in_installed_signature() -> None:
    """Every accepted-input parameter must exist on the installed callable.

    This pins the fix for issue #41: the live help (owned by the capability
    registry) advertised ``a``/``b``/``sampling`` for ``compare`` while the
    public signature is ``current``/``baseline``.  Advertising a keyword the
    signature does not accept forces agents into ``unexpected keyword
    argument`` errors instead of Marivo typed errors.

    The exemption is key-level, not descriptor-level: frame/transform
    operators legitimately declare a ``receiver`` accepted input that names
    the method owner (``self``) rather than a callable keyword, so only that
    single key is skipped.  Every other accepted-input key on every operator
    must still exist in the installed signature, so a future
    ``transform.rollup`` ``grain``/``granularity`` typo or a stale policy
    parameter is caught exactly like the issue #41 drift.
    """
    unaccounted: list[str] = []
    for desc in REGISTRY.descriptors:
        if desc.kind != "operator" or not desc.callable_path:
            continue
        try:
            signature_names = _installed_signature_parameter_names(desc.callable_path)
        except (ImportError, AttributeError, TypeError, ValueError):
            # Unresolvable or non-signature callables are out of scope; the
            # signature line in live help simply falls back to accepted_inputs.
            continue
        for parameter in desc.accepted_inputs:
            if parameter == "receiver":
                # The method owner is bound as ``self``; it is not a keyword
                # the signature accepts, so it is exempt by convention.
                continue
            if parameter not in signature_names:
                unaccounted.append(
                    f"{desc.id} accepted input {parameter!r} is not a parameter "
                    f"of {desc.callable_path} (signature: {sorted(signature_names)})"
                )
    assert not unaccounted, (
        "Accepted inputs advertise parameters the callable lacks:\n  " + "\n  ".join(unaccounted)
    )


def test_operator_parameter_help_keys_exist_in_installed_signature() -> None:
    unaccounted: list[str] = []
    for desc in REGISTRY.descriptors:
        if desc.kind != "operator" or not desc.callable_path:
            continue
        try:
            signature_names = _installed_signature_parameter_names(desc.callable_path)
        except (ImportError, AttributeError, TypeError, ValueError):
            continue
        for parameter in desc.parameter_help:
            if parameter not in signature_names:
                unaccounted.append(
                    f"{desc.id} parameter help {parameter!r} is not a parameter "
                    f"of {desc.callable_path} (signature: {sorted(signature_names)})"
                )
    assert not unaccounted, "Parameter help keys drifted from signatures:\n  " + "\n  ".join(
        unaccounted
    )


def test_opaque_operator_parameters_have_family_or_help_contracts() -> None:
    """Named closed aliases must never rely on reflection-only type names."""

    transparent_names = {
        "Callable",
        "DataFrame",
        "Literal",
        "None",
        "Sequence",
        "Series",
    }
    unaccounted: list[str] = []
    for desc in REGISTRY.descriptors:
        if desc.kind != "operator" or not desc.callable_path:
            continue
        from marivo.introspection.live.reflect import import_registered_callable

        callable_obj = import_registered_callable(desc.callable_path)
        for parameter in inspect.signature(callable_obj).parameters.values():
            if parameter.name == "self" or parameter.name in desc.accepted_inputs:
                continue
            annotation = (
                parameter.annotation
                if isinstance(parameter.annotation, str)
                else inspect.formatannotation(parameter.annotation)
            )
            named_types = set(re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", annotation))
            opaque_names = named_types - transparent_names
            if opaque_names and parameter.name not in desc.parameter_help:
                unaccounted.append(
                    f"{desc.id}.{parameter.name}: annotation={annotation!r}, "
                    f"opaque={sorted(opaque_names)!r}"
                )
    assert not unaccounted, "Opaque parameters lack Help construction routes:\n  " + "\n  ".join(
        unaccounted
    )


def test_operator_artifact_admission_keys_match_accepted_inputs() -> None:
    """``artifact_admission`` keys must line up with ``accepted_inputs`` keys.

    Both mappings are keyed by public parameter name; a drift here would make
    the family gate evaluate a rule for a parameter the operator does not
    accept (or leave an accepted parameter without admission rules).
    """
    mismatches: list[str] = []
    for desc in REGISTRY.descriptors:
        if desc.kind != "operator" or not desc.artifact_admission:
            continue
        accepted_keys = set(desc.accepted_inputs)
        admission_keys = set(desc.artifact_admission)
        if not admission_keys <= accepted_keys:
            mismatches.append(
                f"{desc.id} artifact_admission keys {sorted(admission_keys)} "
                f"not all in accepted_inputs keys {sorted(accepted_keys)}"
            )
    assert not mismatches, "\n  ".join(mismatches)
