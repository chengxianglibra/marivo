"""Tests for the closed capability kernel models and surface limits.

These tests pin the private ``_capabilities`` package: the closed descriptor
union, root-group teaching order, artifact-family vocabulary, frozen
dataclass behavior, the single ``SURFACE_LIMITS`` value, absence of
kernel types from the public ``marivo.analysis.__all__``, and the
complete immutable capability registry with type algebra.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import get_args

import pytest

from marivo.analysis._capabilities import (
    ARTIFACT_FAMILIES,
    ROOT_GROUP_ORDER,
    BoundaryCapability,
    CapabilityBase,
    CapabilityDescriptor,
    ConstructorCapability,
    OperatorCapability,
    ReadCapability,
    RecoveryCapability,
    SameAsInputFamily,
)
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.introspection.live.model import SURFACE_LIMITS, SurfaceLimits

# ---------------------------------------------------------------------------
# Root groups
# ---------------------------------------------------------------------------

EXPECTED_ROOT_GROUPS = (
    "session_state",
    "semantic_inputs",
    "policies_builders",
    "artifact_production",
    "typed_analysis",
    "family_operations",
    "artifact_inspection",
    "recovery",
    "boundaries",
)


def test_root_group_order_has_nine_groups() -> None:
    assert len(ROOT_GROUP_ORDER) == 9


def test_root_group_order_matches_expected_teaching_order() -> None:
    assert ROOT_GROUP_ORDER == EXPECTED_ROOT_GROUPS


def test_root_group_order_has_no_duplicates() -> None:
    assert len(set(ROOT_GROUP_ORDER)) == len(ROOT_GROUP_ORDER)


# ---------------------------------------------------------------------------
# Artifact families
# ---------------------------------------------------------------------------

EXPECTED_ARTIFACT_FAMILIES = (
    "MetricFrame",
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


def test_artifact_families_has_ten_members() -> None:
    assert len(ARTIFACT_FAMILIES) == 10


def test_artifact_families_matches_expected_vocabulary() -> None:
    assert ARTIFACT_FAMILIES == EXPECTED_ARTIFACT_FAMILIES


def test_artifact_families_has_no_duplicates() -> None:
    assert len(set(ARTIFACT_FAMILIES)) == len(ARTIFACT_FAMILIES)


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
    instance = cls(
        id="test.capability",
        public_entrypoint="test.capability()",
        help_target="test.capability",
        summary="test summary",
        root_group="typed_analysis",
        root_visibility="direct",
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
    "root_group": "session_state",
    "root_visibility": "direct",
}

_FROZEN_INSTANCES: list[object] = [
    CapabilityBase(**_BASE_INIT_KWARGS),  # type: ignore[call-arg]
    OperatorCapability(**_BASE_INIT_KWARGS),  # type: ignore[call-arg]
    ConstructorCapability(**_BASE_INIT_KWARGS),  # type: ignore[call-arg]
    ReadCapability(**_BASE_INIT_KWARGS),  # type: ignore[call-arg]
    RecoveryCapability(**_BASE_INIT_KWARGS),  # type: ignore[call-arg]
    BoundaryCapability(**_BASE_INIT_KWARGS),  # type: ignore[call-arg]
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
    assert "root_group" in field_names
    assert "root_visibility" in field_names
    assert "constraint_ids" in field_names
    assert "callable_path" in field_names


def test_capability_base_defaults() -> None:
    base = CapabilityBase(
        id="test.base",
        public_entrypoint="test.base()",
        help_target="test.base",
        summary="base summary",
        root_group="session_state",
        root_visibility="direct",
    )
    assert base.constraint_ids == ()
    assert base.callable_path is None


def test_operator_capability_defaults() -> None:
    cap = OperatorCapability(
        id="op.test",
        public_entrypoint="session.test()",
        help_target="test",
        summary="test op",
        root_group="typed_analysis",
        root_visibility="direct",
    )
    assert cap.receiver == ""
    assert cap.accepted_inputs == {}
    assert cap.output_family == "MetricFrame"


def test_read_capability_defaults() -> None:
    cap = ReadCapability(
        id="read.test",
        public_entrypoint="artifact.show()",
        help_target="test",
        summary="test read",
        root_group="artifact_inspection",
        root_visibility="direct",
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
        root_group="recovery",
        root_visibility="direct",
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
        root_group="boundaries",
        root_visibility="direct",
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
        root_group="policies_builders",
        root_visibility="direct",
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
    ids = [d.id for d in REGISTRY.descriptors]
    assert len(ids) == len(set(ids)), f"duplicate ids: {ids}"


def test_registry_has_no_duplicate_help_targets() -> None:
    targets = [d.help_target for d in REGISTRY.descriptors]
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
        root_group="recovery",
        root_visibility="direct",
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
        root_group="recovery",
        root_visibility="direct",
        callable_path="some.module.fn",
        receiver_family="TestType",
        result_kind="immutable_metadata",
        read_bound="bounded",
    )
    with pytest.raises(ValueError, match="duplicate callable_path"):
        _finalize_registry((desc_a, desc_b))


def test_registry_by_id_returns_same_object() -> None:
    for descriptor in REGISTRY.descriptors:
        assert REGISTRY.by_id(descriptor.id) is descriptor


def test_registry_by_help_target_returns_same_object() -> None:
    for descriptor in REGISTRY.descriptors:
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

    descriptor = REGISTRY.by_callable(mv.TimeScope)
    assert descriptor.id == "TimeScope"

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
    "dow_aligned",
    "holiday_aligned",
    "holiday_and_dow_aligned",
    "TimeScope",
    "AbsoluteWindow",
    "SamplingPolicy",
}


EXPECTED_BOUNDARY_IDS = {
    "boundary.to_pandas",
    "boundary.semantic_handoff",
}


def test_all_expected_operator_ids_registered() -> None:
    ids = set(REGISTRY.capability_ids)
    missing = EXPECTED_OPERATOR_IDS - ids
    assert not missing, f"missing operator ids: {missing}"


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
        "session.list",
        "session.delete",
        "session.jobs",
        "session.recent_jobs",
        "session.job",
        "session.frame_summaries",
        "session.get_frame",
        "session.knowledge",
    ):
        assert expected in ids, f"missing recovery/read id: {expected}"


def test_session_evidence_methods_registered() -> None:
    ids = set(REGISTRY.capability_ids)
    for expected in (
        "session.evidence.findings",
        "session.evidence.propositions",
        "session.evidence.assessments",
        "session.evidence.proposition",
        "session.evidence.latest_assessment",
        "session.evidence.trace",
    ):
        assert expected in ids, f"missing evidence id: {expected}"


def test_base_frame_reads_registered() -> None:
    ids = set(REGISTRY.capability_ids)
    assert "BaseFrame.show" in ids
    assert "BaseFrame.contract" in ids
    assert "BaseFrame.to_pandas" in ids or "boundary.to_pandas" in ids


def test_help_and_help_text_registered() -> None:
    ids = set(REGISTRY.capability_ids)
    assert "help" in ids
    assert "help_text" in ids


# ---------------------------------------------------------------------------
# Registry: grouping descriptors
# ---------------------------------------------------------------------------


def test_grouping_descriptors_exist() -> None:
    for topic in ("discover", "transform", "recovery", "boundary"):
        desc = REGISTRY.by_help_target(topic)
        assert desc is not None, f"missing grouping descriptor for {topic}"


def test_grouping_descriptors_are_not_invokable() -> None:
    """Grouping descriptors must not have a callable_path."""
    for topic in ("discover", "transform", "recovery", "boundary"):
        desc = REGISTRY.by_help_target(topic)
        assert desc.callable_path is None, f"{topic} grouping must not be invokable"


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
    "DimensionSemantic",
    "TimeDimensionSemantic",
    "SemanticProject",
    "AlignmentPolicy",
    "SamplingPolicy",
    "TimeScopeInput",
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
    #    AttributionFrame.contract and CandidateSet.contract inherit directly
    #    and are caught by the registered_paths check.
    "MetricFrame.contract": "override of BaseFrame.contract, registered via canonical path",
    "DeltaFrame.contract": "override of BaseFrame.contract, registered via canonical path",
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
