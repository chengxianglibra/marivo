"""Immutable capability registry for ``marivo.analysis``.

This module owns the complete immutable capability table, callable/type
indexes, reverse edges, grouping-topic expansion, public type/member
allowlists, and generated type-algebra rows.

All names are private to ``marivo.analysis``.  Nothing is added to
``marivo/analysis/__init__.py``.
"""

from __future__ import annotations

import ast
import builtins
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, get_args

from pydantic import BaseModel

from marivo.analysis._capabilities.model import (
    ARTIFACT_FAMILIES,
    ArtifactAdmissionRule,
    ArtifactOutputContract,
    BoundaryCapability,
    CapabilityDescriptor,
    ConstructorCapability,
    HelpExample,
    InputFamily,
    OperatorCapability,
    ReadCapability,
    RecoveryCapability,
    RootGroup,
    SameAsInputFamily,
)
from marivo.introspection.live.model import LiveHelpTarget
from marivo.introspection.live.reflect import callable_identity
from marivo.refs import SemanticKind
from marivo.semantic._capabilities.catalog_members import CATALOG_MEMBER_CONTRACTS

# ---------------------------------------------------------------------------
# Public type/member contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublicObjectContract:
    """Stable intrinsic members for one public analysis object."""

    properties: tuple[str, ...] = ()
    intrinsic_methods: tuple[str, ...] = ("render", "show")


@dataclass(frozen=True)
class SemanticHandoffContract:
    """Analysis continuation owned by one semantic catalog kind."""

    semantic_kind: SemanticKind
    collection_property: str
    input_family: InputFamily | None = None
    handoff_targets: tuple[LiveHelpTarget, ...] = ()
    preparation_targets: tuple[LiveHelpTarget, ...] = ()


def _catalog_property(kind: SemanticKind) -> str:
    return next(member.property_name for member in CATALOG_MEMBER_CONTRACTS if member.kind is kind)


SEMANTIC_HANDOFF_CONTRACTS: Mapping[SemanticKind, SemanticHandoffContract] = MappingProxyType(
    {
        SemanticKind.METRIC: SemanticHandoffContract(
            SemanticKind.METRIC,
            _catalog_property(SemanticKind.METRIC),
            "MetricSemantic",
            handoff_targets=(LiveHelpTarget(surface="analysis", canonical_id="observe"),),
        ),
        SemanticKind.DIMENSION: SemanticHandoffContract(
            SemanticKind.DIMENSION,
            _catalog_property(SemanticKind.DIMENSION),
            "DimensionSemantic",
            handoff_targets=(LiveHelpTarget(surface="analysis", canonical_id="observe"),),
        ),
        SemanticKind.TIME_DIMENSION: SemanticHandoffContract(
            SemanticKind.TIME_DIMENSION,
            _catalog_property(SemanticKind.TIME_DIMENSION),
            "TimeDimensionSemantic",
            handoff_targets=(LiveHelpTarget(surface="analysis", canonical_id="observe"),),
        ),
        SemanticKind.EVENT: SemanticHandoffContract(
            SemanticKind.EVENT,
            _catalog_property(SemanticKind.EVENT),
            handoff_targets=(LiveHelpTarget(surface="analysis", canonical_id="events.match"),),
            preparation_targets=(
                LiveHelpTarget(surface="semantic", canonical_id="participant_role"),
                LiveHelpTarget(surface="analysis", canonical_id="step"),
                LiveHelpTarget(surface="analysis", canonical_id="sequence"),
            ),
        ),
        SemanticKind.STATE_MODEL: SemanticHandoffContract(
            SemanticKind.STATE_MODEL,
            _catalog_property(SemanticKind.STATE_MODEL),
            "StateModelSemantic",
            handoff_targets=(LiveHelpTarget(surface="analysis", canonical_id="lifecycle.replay"),),
        ),
        SemanticKind.PERIOD_CALENDAR: SemanticHandoffContract(
            SemanticKind.PERIOD_CALENDAR,
            _catalog_property(SemanticKind.PERIOD_CALENDAR),
            handoff_targets=(
                LiveHelpTarget(surface="analysis", canonical_id="period_progress"),
                LiveHelpTarget(surface="analysis", canonical_id="period_correspondence"),
            ),
        ),
        SemanticKind.TEMPORAL_SET: SemanticHandoffContract(
            SemanticKind.TEMPORAL_SET,
            _catalog_property(SemanticKind.TEMPORAL_SET),
            handoff_targets=(
                LiveHelpTarget(surface="analysis", canonical_id="occurrence_progress"),
            ),
        ),
        SemanticKind.WORK_SCHEDULE: SemanticHandoffContract(
            SemanticKind.WORK_SCHEDULE,
            _catalog_property(SemanticKind.WORK_SCHEDULE),
            handoff_targets=(
                LiveHelpTarget(surface="analysis", canonical_id="working_day_progress"),
            ),
        ),
    }
)

PUBLIC_FRAME_METHODS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "BaseFrame": ("show", "contract", "to_pandas"),
        "MetricFrame": (
            "metric",
            "components",
            "coverage",
            "as_scalar",
            "as_time_series",
            "as_segmented",
            "as_panel",
        ),
        "DeltaFrame": (
            "components",
            "predicted_attribution_shape",
            "as_scalar",
            "as_time_series",
            "as_segmented",
            "as_panel",
        ),
        "AttributionFrame": (
            "as_sum",
            "as_ratio_mix",
            "as_weighted_mix",
            "at_resolution",
        ),
        "EventFrame": (),
        "LifecycleFrame": (),
        "SubjectSet": (),
        "CandidateSet": (
            "select",
            "as_point_anomaly",
            "as_period_shift",
            "as_driver_axis",
            "as_slice",
            "as_window",
            "as_cross_sectional_outlier",
            "as_semantic_hypothesis",
        ),
    }
)

PUBLIC_FRAME_PROPERTIES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "BaseFrame": (
            "ref",
            "kind",
            "lineage",
            "quality_summary",
            "evidence_status",
            "evidence_digest",
            "state",
            "shape",
            "columns",
        ),
        "MetricFrame": (
            "semantic_shape",
            "metrics",
            "arity",
            "value_columns",
            "time_dimension_columns",
            "transform",
        ),
        "DeltaFrame": ("semantic_shape", "transform"),
        "AttributionFrame": ("attribution_shape", "attribution_mode"),
        "LifecycleFrame": ("semantic_shape",),
        "QualityReport": (
            "overall_status",
            "blocking_issue_count",
            "warning_count",
        ),
    }
)

PUBLIC_TYPE_VARIANTS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "ArtifactIssue": (
            "DataQualityIssue",
            "ComparabilityIssue",
            "EvidenceAvailabilityIssue",
            "CandidateResolutionIssue",
        ),
        "CandidateSelection": (
            "PointAnomalySelection",
            "PeriodShiftSelection",
            "DriverAxisSelection",
            "SliceSelection",
            "WindowSelection",
            "CrossSectionalOutlierSelection",
            "OntologyMetricCandidate",
        ),
        "EventFrame": (
            "journey",
            "funnel",
            "time_to_event",
        ),
        "LifecycleFrame": (
            "history",
            "distribution",
            "transitions",
            "dwell",
            "violations",
        ),
        "QualityReport": (
            "metric",
            "delta",
            "event_journey",
            "event_funnel",
            "event_time_to_event",
            "lifecycle_history",
            "lifecycle_distribution",
            "lifecycle_transitions",
            "lifecycle_dwell",
            "lifecycle_violations",
            "funnel_delta",
            "attribution",
            "funnel_attribution",
        ),
    }
)

PUBLIC_OBJECT_CONTRACTS: Mapping[str, PublicObjectContract] = MappingProxyType(
    {
        "Session": PublicObjectContract(
            properties=(
                "id",
                "name",
                "question",
                "catalog",
                "created_at",
                "updated_at",
                "report_tz_name",
                "is_read_only",
                "evidence",
                "discover",
                "events",
                "lifecycle",
            ),
            intrinsic_methods=("source_bindings",),
        ),
        "SessionEvents": PublicObjectContract(),
        "SessionLifecycle": PublicObjectContract(),
        "FrameSummaryEntry": PublicObjectContract(properties=("ref",)),
    }
)


# ---------------------------------------------------------------------------
# Type algebra row
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TypeAlgebraRow:
    """A single rendered type-algebra edge.

    Parameters
    ----------
    help_target:
        Canonical help target for the capability.
    source_families:
        Frozen set of input families feeding this edge.
    output_contract:
        Typed artifact output or an external terminal type string.
    is_terminal:
        ``True`` for the single aggregate ``boundary.to_pandas`` row.
    """

    help_target: str
    source_families: frozenset[str]
    output_contract: ArtifactOutputContract | str
    is_terminal: bool = False

    @property
    def output_family(self) -> str:
        """Return the family-only view retained by algebra callers."""

        if isinstance(self.output_contract, ArtifactOutputContract):
            family = self.output_contract.family
            if isinstance(family, SameAsInputFamily):
                return f"same as {family.parameter}"
            return family
        return self.output_contract

    def render(self) -> str:
        """Render the row as a single-line type-algebra edge string."""
        sources_text = (
            "all registered artifact families"
            if self.is_terminal
            else ", ".join(sorted(self.source_families))
        )
        suffix = " (terminal)" if self.is_terminal else ""
        output = (
            self.output_contract.render()
            if isinstance(self.output_contract, ArtifactOutputContract)
            else self.output_contract
        )
        return f"{sources_text} -> {self.help_target} -> {output}{suffix}"


# ---------------------------------------------------------------------------
# Grouping descriptors (queryable but not invokable)
# ---------------------------------------------------------------------------


def _make_grouping_descriptor(
    topic: str,
    summary: str,
    root_group: RootGroup,
    *,
    root_summary: str | None = None,
) -> ConstructorCapability:
    """Create a non-invokable grouping descriptor for a collapsed topic."""
    return ConstructorCapability(
        id=topic,
        public_entrypoint=f'marivo.help("analysis.{topic}")',
        help_target=topic,
        summary=summary,
        root_group=root_group,
        root_visibility="grouped",
        root_summary=root_summary,
        callable_path=None,
        output_type="",
    )


# ---------------------------------------------------------------------------
# Registry builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityRegistry:
    """Immutable registry of all analysis capabilities.

    Provides lookup by id, help_target, and callable identity, plus
    reverse-edge indexes and generated type-algebra rows.
    """

    _descriptors: tuple[CapabilityDescriptor, ...]
    _by_id: Mapping[str, CapabilityDescriptor] = field(default_factory=dict)
    _by_help_target: Mapping[str, CapabilityDescriptor] = field(default_factory=dict)
    _by_callable: Mapping[str, CapabilityDescriptor] = field(default_factory=dict)
    _constructor_consumers: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    _algebra_rows: tuple[TypeAlgebraRow, ...] = field(default_factory=tuple)

    @property
    def surface(self) -> Literal["analysis"]:
        """Return the owning help surface for the neutral registry protocol."""
        return "analysis"

    # -- Properties --------------------------------------------------------

    @property
    def descriptors(self) -> tuple[CapabilityDescriptor, ...]:
        return self._descriptors

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(d.id for d in self._descriptors)

    @property
    def help_targets(self) -> tuple[str, ...]:
        return tuple(d.help_target for d in self._descriptors)

    @property
    def constructor_consumers(self) -> Mapping[str, tuple[str, ...]]:
        return self._constructor_consumers

    def public_member_names(self, receiver_family: str) -> tuple[str, ...]:
        """Return registered public methods owned by one receiver type."""

        names: list[str] = []
        for descriptor in self._descriptors:
            receiver: str | None = None
            if isinstance(descriptor, OperatorCapability):
                receiver = descriptor.receiver
            elif isinstance(descriptor, ReadCapability):
                receiver = descriptor.receiver_family
            elif (
                isinstance(descriptor, RecoveryCapability)
                and descriptor.callable_path is not None
                and f".{receiver_family}." in descriptor.callable_path
            ):
                receiver = receiver_family
            if receiver != receiver_family:
                continue
            callable_name = descriptor.public_entrypoint.partition("(")[0]
            member_name = callable_name.rsplit(".", 1)[-1]
            if member_name not in names:
                names.append(member_name)
        return tuple(names)

    def public_member_calls(self, receiver_family: str) -> tuple[str, ...]:
        """Return receiver-relative registered call skeletons."""

        calls: list[str] = []
        member_names = set(self.public_member_names(receiver_family))
        for descriptor in self._descriptors:
            callable_text = descriptor.public_entrypoint
            callable_path, separator, call_suffix = callable_text.partition("(")
            callable_name = callable_path.rsplit(".", 1)[-1]
            if callable_name not in member_names:
                continue
            if isinstance(descriptor, OperatorCapability):
                receiver = descriptor.receiver
            elif isinstance(descriptor, ReadCapability):
                receiver = descriptor.receiver_family
            elif (
                isinstance(descriptor, RecoveryCapability)
                and descriptor.callable_path is not None
                and f".{receiver_family}." in descriptor.callable_path
            ):
                receiver = receiver_family
            else:
                receiver = None
            if receiver != receiver_family:
                continue
            call = f".{callable_name}"
            if separator:
                call += f"({call_suffix}"
            if call not in calls:
                calls.append(call)
        return tuple(calls)

    def public_object_members(
        self,
        type_name: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return registry-backed properties and methods for one public object."""

        contract = PUBLIC_OBJECT_CONTRACTS.get(type_name)
        if contract is None:
            return (), ()
        methods = tuple(
            dict.fromkeys((*contract.intrinsic_methods, *self.public_member_names(type_name)))
        )
        return contract.properties, methods

    def producer_targets(self, input_family: InputFamily) -> tuple[LiveHelpTarget, ...]:
        """Return registered producers and acquisition paths for one input family."""

        targets: list[LiveHelpTarget] = []
        for descriptor in self._descriptors:
            if isinstance(descriptor, OperatorCapability):
                family = descriptor.output_contract.family
                if not isinstance(family, SameAsInputFamily) and family == input_family:
                    targets.append(
                        LiveHelpTarget(
                            surface="analysis",
                            canonical_id=descriptor.help_target,
                        )
                    )
            elif (
                isinstance(descriptor, ConstructorCapability)
                and descriptor.produced_input_family == input_family
            ) or (
                isinstance(descriptor, ReadCapability)
                and descriptor.produced_input_family == input_family
            ):
                targets.append(
                    LiveHelpTarget(
                        surface="analysis",
                        canonical_id=descriptor.help_target,
                    )
                )
        for handoff in SEMANTIC_HANDOFF_CONTRACTS.values():
            if handoff.input_family == input_family:
                targets.append(
                    LiveHelpTarget(
                        surface="analysis",
                        canonical_id=f"catalog.{handoff.collection_property}",
                    )
                )
        if input_family == "EventPattern":
            targets = [
                target
                for target in targets
                if not (target.surface == "analysis" and target.canonical_id == "sequence")
            ]
            targets.extend(SEMANTIC_HANDOFF_CONTRACTS[SemanticKind.EVENT].preparation_targets)
        return tuple(dict.fromkeys(targets))

    def semantic_handoff(self, semantic_kind: str) -> SemanticHandoffContract | None:
        """Return the typed analysis handoff for one semantic kind value."""

        try:
            kind = SemanticKind(semantic_kind)
        except ValueError:
            return None
        return SEMANTIC_HANDOFF_CONTRACTS.get(kind)

    def semantic_handoffs_for_input_family(
        self,
        input_family: InputFamily,
    ) -> tuple[SemanticHandoffContract, ...]:
        """Return semantic acquisition contracts satisfying one input family."""

        return tuple(
            handoff
            for handoff in SEMANTIC_HANDOFF_CONTRACTS.values()
            if handoff.input_family == input_family
        )

    # -- Lookup -----------------------------------------------------------

    def by_id(self, capability_id: str) -> CapabilityDescriptor:
        """Return the descriptor with the given canonical id."""
        return self._by_id[capability_id]

    def canonical_ids(self) -> tuple[str, ...]:
        """Return canonical help targets in native registry order."""
        return self.help_targets

    def by_canonical_id(self, canonical_id: str) -> CapabilityDescriptor:
        """Resolve canonical help grammar, then the native capability id."""
        try:
            return self.by_help_target(canonical_id)
        except KeyError:
            return self.by_id(canonical_id)

    def by_help_target(self, help_target: str) -> CapabilityDescriptor:
        """Return the descriptor with the given help target."""
        return self._by_help_target[help_target]

    def by_callable(self, callable_obj: object) -> CapabilityDescriptor:
        """Return the descriptor registered for the given callable identity.

        Resolves bound methods through ``__func__`` so that
        ``Session.observe`` and ``session.observe`` resolve to the same
        descriptor.  The canonical identity is the ``callable_path`` string,
        not the function object's ``id()``.
        """
        key = self._callable_key(callable_obj)
        return self._by_callable[key]

    @staticmethod
    def _callable_key(callable_obj: object) -> str:
        """Return the canonical callable_path for a callable or type.

        Resolves bound methods through ``__func__`` and uses
        :func:`_module_path_for` to produce the dotted import path that
        matches the ``callable_path`` stored on descriptors.
        """
        return callable_identity(callable_obj)

    # -- Type algebra -----------------------------------------------------

    def type_algebra_rows(self) -> tuple[TypeAlgebraRow, ...]:
        """Return the generated type-algebra rows in deterministic order."""
        return self._algebra_rows

    def compatible_consumers(
        self,
        output: ArtifactOutputContract,
    ) -> tuple[str, ...]:
        """Return consumers compatible with all statically known output facts."""

        family = output.family
        if isinstance(family, SameAsInputFamily):
            return ()
        consumers: set[str] = set()
        for desc in self._descriptors:
            if not isinstance(desc, (OperatorCapability, BoundaryCapability)):
                continue
            for parameter, accepted in desc.accepted_inputs.items():
                if family not in accepted:
                    continue
                if isinstance(desc, OperatorCapability):
                    admission = desc.artifact_admission.get(parameter)
                    if admission is not None:
                        shapes = admission.semantic_shapes.get(family)
                        if (
                            shapes
                            and output.semantic_shapes
                            and shapes.isdisjoint(output.semantic_shapes)
                        ):
                            continue
                        matching = admission.matching_kinds.get(family)
                        if (
                            matching
                            and output.matching_kinds
                            and matching.isdisjoint(output.matching_kinds)
                        ):
                            continue
                consumers.add(desc.id)
        return tuple(sorted(consumers))


# ---------------------------------------------------------------------------
# Registry construction
# ---------------------------------------------------------------------------

_MF: frozenset[InputFamily] = frozenset({"MetricFrame"})
_EF: frozenset[InputFamily] = frozenset({"EventFrame"})
_LF: frozenset[InputFamily] = frozenset({"LifecycleFrame"})
_SS: frozenset[InputFamily] = frozenset({"SubjectSet"})
_DF: frozenset[InputFamily] = frozenset({"DeltaFrame"})
_MF_OR_DF: frozenset[InputFamily] = frozenset({"MetricFrame", "DeltaFrame"})
_CS: frozenset[InputFamily] = frozenset({"CandidateSet"})
_AF: frozenset[InputFamily] = frozenset({"AttributionFrame"})
_FIELD_SEMANTIC: frozenset[InputFamily] = frozenset({"DimensionSemantic", "TimeDimensionSemantic"})


def _output(
    family: Any,
    *,
    shapes: tuple[str, ...] = (),
    matching: tuple[str, ...] = (),
    nullable: bool = False,
) -> ArtifactOutputContract:
    """Build one immutable artifact output contract."""

    return ArtifactOutputContract(
        family=family,
        semantic_shapes=frozenset(shapes),
        matching_kinds=frozenset(matching),
        nullable=nullable,
    )


def _build_registry() -> CapabilityRegistry:
    """Build the complete immutable capability registry."""

    # Late imports to avoid circular dependencies at module load time.
    from marivo.analysis import grain, time_scope
    from marivo.analysis.event import (
        declared_complete_through,
        every_start,
        first_per_subject,
        sequence,
        step,
    )
    from marivo.analysis.frames.attribution import AttributionFrame
    from marivo.analysis.frames.candidate import CandidateSet
    from marivo.analysis.frames.delta import DeltaFrame
    from marivo.analysis.frames.metric import MetricFrame
    from marivo.analysis.funnel import funnel_loss_rate
    from marivo.analysis.lifecycle import from_inception, in_state
    from marivo.analysis.policies import (
        SamplingPolicy,
        day_of_week,
        occurrence_progress,
        period_correspondence,
        period_progress,
        window_bucket,
        working_day_progress,
    )
    from marivo.analysis.runtime_metric import aggregate, ratio, slice, weighted_mean
    from marivo.analysis.subject import dropped_before
    from marivo.analysis.windows.spec import AbsoluteWindow

    all_artifact_families: frozenset[InputFamily] = frozenset(ARTIFACT_FAMILIES)

    descriptors: list[CapabilityDescriptor] = []

    # -- Session operators ------------------------------------------------

    descriptors.append(
        OperatorCapability(
            id="observe",
            public_entrypoint="session.observe(...)",
            help_target="observe",
            summary=(
                "Materialize exact current-catalog entries/refs or closed runtime "
                "metric expressions through one bounded graph into a typed MetricFrame; "
                "grain accepts builtin mv.grain(...) values or certified semantic "
                "ms.calendar_grain(...) values."
            ),
            root_group="artifact_production",
            root_visibility="direct",
            root_summary="Materialize governed metric inputs into a typed MetricFrame.",
            constraint_ids=(
                "metric_expression_resolvable",
                "metric_readiness_verified",
                "window_absolute_parseable",
            ),
            callable_path="marivo.analysis.session.core.Session.observe",
            receiver="Session",
            accepted_inputs={
                "metrics": frozenset(
                    {"MetricSemantic", "RuntimeMetricExpression", "OntologyMetricCandidate"}
                ),
                "time_scope": frozenset({"TimeScopeInput"}),
                "dimensions": _FIELD_SEMANTIC,
                "slice_by": _FIELD_SEMANTIC,
                "time_dimension": frozenset({"TimeDimensionSemantic"}),
                "cohort": _SS,
            },
            artifact_admission={
                "cohort": ArtifactAdmissionRule(
                    coverage_statuses={"SubjectSet": frozenset({"ready"})},
                ),
            },
            output_contract=_output("MetricFrame"),
            additional_examples=(
                HelpExample(
                    label="Direct Ref segmented time series",
                    code=(
                        "import marivo.analysis as mv\n"
                        "frame = session.observe(\n"
                        '    ms.ref.metric("sales.revenue"),\n'
                        '    time_scope=mv.time_scope(start="2026-07-01", end="2026-07-04"),\n'
                        '    grain=mv.grain("day"),\n'
                        '    dimensions=[ms.ref.dimension("sales.orders.region")],\n'
                        ")"
                    ),
                ),
                HelpExample(
                    label="Metric scoped by a typed SubjectSet",
                    code=(
                        "scoped_metric = session.observe(\n"
                        '    ms.ref.metric("commerce.event_count"),\n'
                        "    cohort=subjects,\n"
                        ")"
                    ),
                    requires=("subjects",),
                ),
            ),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="events.match",
            public_entrypoint="session.events.match(...)",
            help_target="events.match",
            summary=(
                "Match a typed EventPattern into dense subject journeys with "
                "explicit follow-up completeness."
            ),
            root_group="artifact_production",
            root_visibility="direct",
            constraint_ids=(
                "event_pattern_valid",
                "event_window_valid",
                "event_completeness_valid",
            ),
            callable_path="marivo.analysis.session.core.SessionEvents.match",
            receiver="SessionEvents",
            accepted_inputs={
                "pattern": frozenset({"EventPattern"}),
                "cohort_window": frozenset({"TimeScopeInput"}),
                "matching": frozenset({"EventMatchingPolicy"}),
                "completeness": frozenset({"CompletenessDeclaration"}),
                "cohort": _SS,
            },
            artifact_admission={
                "cohort": ArtifactAdmissionRule(
                    coverage_statuses={"SubjectSet": frozenset({"ready"})},
                ),
            },
            output_contract=_output("EventFrame", shapes=("journey",)),
            additional_examples=(
                HelpExample(
                    label="Repeated attempts with exclusive completion assignment",
                    code=(
                        "exclusive_attempts = session.events.match(\n"
                        "    pattern=mv.sequence(\n"
                        '        mv.step(participant=cart_user, key="cart"),\n'
                        '        mv.step(participant=payment_buyer, key="payment"),\n'
                        "    ),\n"
                        "    cohort_window=mv.time_scope(\n"
                        '        start="2026-07-01T00:00:00Z",\n'
                        '        end="2026-07-08T00:00:00Z",\n'
                        "    ),\n"
                        '    completion_through="2026-07-15T00:00:00Z",\n'
                        '    matching=mv.every_start(completion_assignment="exclusive"),\n'
                        ")"
                    ),
                    requires=("cart_user", "payment_buyer"),
                ),
                HelpExample(
                    label="Repeated attempts with shared completion assignment",
                    code=(
                        "shared_attempts = session.events.match(\n"
                        "    pattern=mv.sequence(\n"
                        '        mv.step(participant=cart_user, key="cart"),\n'
                        '        mv.step(participant=payment_buyer, key="payment"),\n'
                        "    ),\n"
                        "    cohort_window=mv.time_scope(\n"
                        '        start="2026-07-01T00:00:00Z",\n'
                        '        end="2026-07-08T00:00:00Z",\n'
                        "    ),\n"
                        '    completion_through="2026-07-15T00:00:00Z",\n'
                        '    matching=mv.every_start(completion_assignment="shared"),\n'
                        ")"
                    ),
                    requires=("cart_user", "payment_buyer"),
                ),
                HelpExample(
                    label="Journey matching scoped by a typed SubjectSet",
                    code=(
                        "scoped_journeys = session.events.match(\n"
                        "    pattern=pattern,\n"
                        "    cohort=subjects,\n"
                        "    cohort_window=mv.time_scope(\n"
                        '        start="2026-07-01T00:00:00Z",\n'
                        '        end="2026-07-02T00:00:00Z",\n'
                        "    ),\n"
                        '    completion_through="2026-07-02T00:00:00Z",\n'
                        "    matching=mv.first_per_subject(),\n"
                        "    completeness=completeness,\n"
                        ")"
                    ),
                    requires=("completeness", "pattern", "subjects"),
                ),
            ),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="events.funnel",
            public_entrypoint="session.events.funnel(...)",
            help_target="events.funnel",
            summary=(
                "Reduce first-per-subject journeys into censoring-aware funnel "
                "counts with optional governed subject axes."
            ),
            root_group="typed_analysis",
            root_visibility="direct",
            constraint_ids=("event_reducer_source_valid", "event_subject_axis_valid"),
            callable_path="marivo.analysis.session.core.SessionEvents.funnel",
            receiver="SessionEvents",
            accepted_inputs={
                "journeys": _EF,
                "axes": frozenset({"DimensionSemantic"}),
            },
            artifact_admission={
                "journeys": ArtifactAdmissionRule(
                    semantic_shapes={"EventFrame": frozenset({"journey"})},
                    matching_kinds={"EventFrame": frozenset({"first_per_subject"})},
                ),
            },
            output_contract=_output(
                "EventFrame",
                shapes=("funnel",),
                matching=("first_per_subject",),
            ),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="lifecycle.replay",
            public_entrypoint="session.lifecycle.replay(...)",
            help_target="lifecycle.replay",
            summary=(
                "Replay one exact StateModel from its explicit inception seed into "
                "canonical clipped state history."
            ),
            root_group="artifact_production",
            root_visibility="direct",
            constraint_ids=(
                "lifecycle_model_valid",
                "lifecycle_window_valid",
                "lifecycle_seed_valid",
                "event_completeness_valid",
            ),
            callable_path="marivo.analysis.session.core.SessionLifecycle.replay",
            receiver="SessionLifecycle",
            accepted_inputs={
                "model": frozenset({"StateModelSemantic"}),
                "window": frozenset({"TimeScopeInput"}),
                "seed": frozenset({"LifecycleSeed"}),
                "completeness": frozenset({"CompletenessDeclaration"}),
                "cohort": _SS,
            },
            artifact_admission={
                "cohort": ArtifactAdmissionRule(
                    coverage_statuses={"SubjectSet": frozenset({"ready"})},
                ),
            },
            output_contract=_output("LifecycleFrame", shapes=("history",)),
            additional_examples=(
                HelpExample(
                    label="Replay from the first modeled inception",
                    code=(
                        "history = session.lifecycle.replay(\n"
                        '    ms.ref.state_model("commerce.order_lifecycle"),\n'
                        "    window=mv.time_scope(\n"
                        '        start="2026-07-01T00:00:00Z",\n'
                        '        end="2026-08-01T00:00:00Z",\n'
                        "    ),\n"
                        "    seed=mv.from_inception(),\n"
                        ")"
                    ),
                ),
                HelpExample(
                    label="Replay scoped by a ready SubjectSet",
                    code=(
                        "scoped_history = session.lifecycle.replay(\n"
                        '    ms.ref.state_model("commerce.order_lifecycle"),\n'
                        "    window=mv.time_scope(\n"
                        '        start="2026-08-01T00:00:00Z",\n'
                        '        end="2026-09-01T00:00:00Z",\n'
                        "    ),\n"
                        "    seed=mv.from_inception(),\n"
                        "    cohort=subjects,\n"
                        ")"
                    ),
                    requires=("subjects",),
                ),
            ),
        )
    )

    lifecycle_reducer_specs = (
        (
            "lifecycle.distribution",
            "Reduce replay history into dense point-in-time state distributions.",
            "marivo.analysis.session.core.SessionLifecycle.distribution",
        ),
        (
            "lifecycle.transitions",
            "Count dense modeled state pairs from committed replay history.",
            "marivo.analysis.session.core.SessionLifecycle.transitions",
        ),
        (
            "lifecycle.dwell",
            "Summarize completed and censored state intervals from committed history.",
            "marivo.analysis.session.core.SessionLifecycle.dwell",
        ),
        (
            "lifecycle.violations",
            "Expose the fixed-contract illegal modeled-Event trace from replay.",
            "marivo.analysis.session.core.SessionLifecycle.violations",
        ),
    )
    for capability_id, summary, callable_path in lifecycle_reducer_specs:
        descriptors.append(
            OperatorCapability(
                id=capability_id,
                public_entrypoint=f"session.{capability_id}(...)",
                help_target=capability_id,
                summary=summary,
                root_group="typed_analysis",
                root_visibility="direct",
                constraint_ids=("lifecycle_reducer_source_valid",),
                callable_path=callable_path,
                receiver="SessionLifecycle",
                accepted_inputs={"history": _LF},
                artifact_admission={
                    "history": ArtifactAdmissionRule(
                        semantic_shapes={"LifecycleFrame": frozenset({"history"})},
                    ),
                },
                output_contract=_output(
                    "LifecycleFrame",
                    shapes=(capability_id.rsplit(".", 1)[-1],),
                ),
                additional_examples=(
                    (
                        HelpExample(
                            label="Distribution at explicit instants with governed axes",
                            code=(
                                "distribution = session.lifecycle.distribution(\n"
                                "    history,\n"
                                '    at=("2026-07-08T00:00:00Z",),\n'
                                '    axes=[ms.ref.dimension("commerce.orders.region")],\n'
                                ")"
                            ),
                            requires=("history",),
                        ),
                    )
                    if capability_id == "lifecycle.distribution"
                    else ()
                ),
            )
        )

    descriptors.append(
        OperatorCapability(
            id="events.time_to_event",
            public_entrypoint="session.events.time_to_event(...)",
            help_target="events.time_to_event",
            summary=(
                "Project exact persisted journey assignments into time-to-event rows "
                "without querying or rematching Events, with optional governed "
                "subject axes to group elapsed durations."
            ),
            root_group="typed_analysis",
            root_visibility="direct",
            root_summary="Project persisted journeys into elapsed-time rows.",
            constraint_ids=(
                "event_reducer_source_valid",
                "event_step_pair_valid",
                "event_subject_axis_valid",
            ),
            callable_path="marivo.analysis.session.core.SessionEvents.time_to_event",
            receiver="SessionEvents",
            accepted_inputs={
                "journeys": _EF,
                "axes": frozenset({"DimensionSemantic"}),
            },
            artifact_admission={
                "journeys": ArtifactAdmissionRule(
                    semantic_shapes={"EventFrame": frozenset({"journey"})},
                ),
            },
            output_contract=_output("EventFrame", shapes=("time_to_event",)),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="select_subjects",
            public_entrypoint="session.select_subjects(...)",
            help_target="select_subjects",
            summary=(
                "Materialize a closed typed SubjectSet from resolved Event loss "
                "or replayed Lifecycle state."
            ),
            root_group="typed_analysis",
            root_visibility="direct",
            constraint_ids=("event_reducer_source_valid", "subject_selection_valid"),
            callable_path="marivo.analysis.session.core.Session.select_subjects",
            receiver="Session",
            accepted_inputs={
                "artifact": _EF | _LF,
                "selection": frozenset({"SubjectSelection"}),
            },
            artifact_admission={
                "artifact": ArtifactAdmissionRule(
                    semantic_shapes={
                        "EventFrame": frozenset({"journey"}),
                        "LifecycleFrame": frozenset({"history"}),
                    },
                    matching_kinds={"EventFrame": frozenset({"first_per_subject"})},
                ),
            },
            output_contract=_output("SubjectSet"),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="compare",
            public_entrypoint="session.compare(...)",
            help_target="compare",
            summary=(
                "Compute a typed Metric delta, including canonical cumulative-period "
                "pairing, or exactly align two compatible EventFrame[funnel] artifacts. "
                "MetricFrame inputs must each carry a single metric (arity=1); a "
                'multi-metric frame is projected with frame.metric("<metric_id>") '
                "before comparing."
            ),
            root_group="typed_analysis",
            root_visibility="direct",
            root_summary="Compare compatible metric or funnel artifacts into a DeltaFrame.",
            constraint_ids=(
                "frame_kind_compatible",
                "single_metric_input",
                "alignment_policy_shape",
                "cumulative_compare_compatible",
                "funnel_comparison_compatible",
            ),
            callable_path="marivo.analysis.session.core.Session.compare",
            receiver="Session",
            accepted_inputs={
                "current": _MF | _EF,
                "baseline": _MF | _EF,
                "alignment": frozenset({"AlignmentPolicy"}),
            },
            artifact_admission={
                "current": ArtifactAdmissionRule(
                    semantic_shapes={"EventFrame": frozenset({"funnel"})},
                    matching_kinds={"EventFrame": frozenset({"first_per_subject"})},
                ),
                "baseline": ArtifactAdmissionRule(
                    semantic_shapes={"EventFrame": frozenset({"funnel"})},
                    matching_kinds={"EventFrame": frozenset({"first_per_subject"})},
                ),
            },
            output_contract=_output(
                "DeltaFrame",
                shapes=("scalar", "time_series", "segmented", "panel", "funnel"),
            ),
            additional_examples=(
                HelpExample(
                    label="Compare compatible all-history cumulative levels",
                    code=(
                        "current = session.observe(\n"
                        "    cumulative_metric,\n"
                        '    time_scope=mv.time_scope(start="2026-07-01", end="2026-07-08"),\n'
                        '    grain=mv.grain("day"),\n'
                        ")\n"
                        "baseline = session.observe(\n"
                        "    cumulative_metric,\n"
                        '    time_scope=mv.time_scope(start="2026-06-01", end="2026-06-08"),\n'
                        '    grain=mv.grain("day"),\n'
                        ")\n"
                        "delta = session.compare(current, baseline)\n"
                        "delta.show()\n"
                        "delta.contract().show()\n"
                        "endpoints = delta.to_pandas()[[\n"
                        '    "current_evaluation_end",\n'
                        '    "baseline_evaluation_end",\n'
                        "]]"
                    ),
                    requires=("cumulative_metric",),
                ),
                HelpExample(
                    label="Compare month-to-date by day-of-week position",
                    code=(
                        "alignment = mv.day_of_week(\n"
                        '    within=mv.grain("month"),\n'
                        ")\n"
                        "delta = session.compare(current_mtd, baseline_mtd, alignment=alignment)\n"
                        "print(delta.meta.cumulative_alignment.pairs)"
                    ),
                    requires=("baseline_mtd", "current_mtd"),
                ),
                HelpExample(
                    label="Inspect a structured cumulative incompatibility",
                    code=(
                        "from marivo.analysis.errors import AnalysisError\n"
                        "try:\n"
                        "    session.compare(current, incompatible_baseline)\n"
                        "except AnalysisError as exc:\n"
                        "    print(exc.expected)\n"
                        "    print(exc.received)\n"
                        "    print(exc.repair.action if exc.repair else None)"
                    ),
                    requires=("current", "incompatible_baseline"),
                ),
                HelpExample(
                    label="Compare two exact funnel scopes",
                    code="delta = session.compare(current_funnel, baseline_funnel)",
                    requires=("baseline_funnel", "current_funnel"),
                ),
            ),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="attribute",
            public_entrypoint="session.attribute(...)",
            help_target="attribute",
            summary=(
                "Attribute a DeltaFrame's movement over explicit axes with "
                "reconciled contributions and explicit share denominators. The installed "
                "automatic methods are additive/component allocation, exact distinct "
                "membership, exact value-frequency quantiles, Trino mergeable qdigest, and "
                "typed cumulative endpoint/base-flow routes; non-mergeable reservoir quantiles "
                "and unsupported cumulative route combinations remain blocked by the delta "
                "contract."
            ),
            root_group="typed_analysis",
            root_visibility="direct",
            root_summary=(
                "Attribute a DeltaFrame over explicit axes with reconciled contributions."
            ),
            constraint_ids=(
                "frame_kind_compatible",
                "attribution_additivity_compatible",
                "attribution_reconciliation",
                "cumulative_attribution_route_compatible",
                "funnel_attribution_target_valid",
                "funnel_attribution_reconciliation",
            ),
            callable_path="marivo.analysis.session.core.Session.attribute",
            receiver="Session",
            accepted_inputs={
                "frame": _DF,
                "axes": _FIELD_SEMANTIC,
                "target": frozenset({"FunnelLossRate"}),
            },
            artifact_admission={
                "frame": ArtifactAdmissionRule(
                    semantic_shapes={
                        "DeltaFrame": frozenset(
                            {"scalar", "time_series", "segmented", "panel", "funnel"}
                        )
                    },
                ),
            },
            output_contract=_output("AttributionFrame"),
            additional_examples=(
                HelpExample(
                    label="Canonical scalar-delta attribution",
                    code=(
                        "current = session.observe(metric, time_scope=current_window)\n"
                        "baseline = session.observe(metric, time_scope=baseline_window)\n"
                        "delta = session.compare(current, baseline)\n"
                        "drivers = session.attribute(delta, axes=[region])"
                    ),
                    requires=(
                        "baseline_window",
                        "current_window",
                        "metric",
                        "region",
                    ),
                ),
                HelpExample(
                    label="Attribute one funnel loss rate",
                    code=(
                        "drivers = session.attribute(\n"
                        "    delta,\n"
                        "    axes=[channel],\n"
                        "    target=mv.funnel_loss_rate(step=payment_step),\n"
                        ")"
                    ),
                    requires=("channel", "delta", "payment_step"),
                ),
            ),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="correlate",
            public_entrypoint="session.correlate(...)",
            help_target="correlate",
            summary="Measure the association between two MetricFrames.",
            root_group="typed_analysis",
            root_visibility="direct",
            constraint_ids=(
                "frame_kind_compatible",
                "alignment_policy_shape",
                "correlate_lag_semantics",
                "single_metric_input",
            ),
            callable_path="marivo.analysis.session.core.Session.correlate",
            receiver="Session",
            accepted_inputs={
                "a": _MF,
                "b": _MF,
                "alignment": frozenset({"AlignmentPolicy"}),
            },
            output_contract=_output("AssociationResult"),
            additional_examples=(
                HelpExample(
                    label="Common-key cross-sectional frames from exact Refs",
                    code=(
                        'region = ms.ref.dimension("sales.orders.region")\n'
                        "a = session.observe(\n"
                        '    ms.ref.metric("sales.revenue"), dimensions=[region]\n'
                        ")\n"
                        "b = session.observe(\n"
                        '    ms.ref.metric("sales.order_count"), dimensions=[region]\n'
                        ")\n"
                        "result = session.correlate(a, b)"
                    ),
                ),
            ),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="hypothesis_test",
            public_entrypoint="session.hypothesis_test(...)",
            help_target="hypothesis_test",
            summary="Run a paired hypothesis test over two MetricFrames.",
            root_group="typed_analysis",
            root_visibility="direct",
            constraint_ids=(
                "frame_kind_compatible",
                "alignment_policy_shape",
                "single_metric_input",
            ),
            callable_path="marivo.analysis.session.core.Session.hypothesis_test",
            receiver="Session",
            accepted_inputs={
                "a": _MF,
                "b": _MF,
                "alignment": frozenset({"AlignmentPolicy"}),
                "sampling": frozenset({"SamplingPolicy"}),
            },
            output_contract=_output("HypothesisTestResult"),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="forecast",
            public_entrypoint="session.forecast(...)",
            help_target="forecast",
            summary=(
                "Project a time_series or panel MetricFrame forward; certified semantic "
                "periods use their exact ordinal binding and future coverage."
            ),
            root_group="typed_analysis",
            root_visibility="direct",
            root_summary="Forecast a time-series or panel MetricFrame.",
            constraint_ids=("forecast_input_shape", "single_metric_input"),
            callable_path="marivo.analysis.session.core.Session.forecast",
            receiver="Session",
            accepted_inputs={
                "history": _MF,
            },
            output_contract=_output("ForecastFrame"),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="assess_quality",
            public_entrypoint="session.assess_quality(...)",
            help_target="assess_quality",
            summary=(
                "Run fixed quality checks over supported MetricFrame, EventFrame, "
                "LifecycleFrame, DeltaFrame, and AttributionFrame shapes."
            ),
            root_group="typed_analysis",
            root_visibility="direct",
            root_summary="Run fixed quality checks over supported analysis artifacts.",
            constraint_ids=("quality_target_shape", "single_metric_input"),
            callable_path="marivo.analysis.session.core.Session.assess_quality",
            receiver="Session",
            accepted_inputs={
                "frame": _MF | _EF | _LF | _DF | _AF,
            },
            artifact_admission={
                "frame": ArtifactAdmissionRule(
                    semantic_shapes={
                        "MetricFrame": frozenset({"scalar", "time_series", "segmented", "panel"}),
                        "EventFrame": frozenset({"journey", "funnel", "time_to_event"}),
                        "LifecycleFrame": frozenset(
                            {"history", "distribution", "transitions", "dwell", "violations"}
                        ),
                        "DeltaFrame": frozenset(
                            {"scalar", "time_series", "segmented", "panel", "funnel"}
                        ),
                        "AttributionFrame": frozenset(
                            {"scalar", "time_series", "segmented", "panel", "funnel_loss_rate"}
                        ),
                    },
                ),
            },
            output_contract=_output("QualityReport"),
        )
    )

    # -- Discover operators -----------------------------------------------

    _discover_specs: tuple[
        tuple[str, str, frozenset[InputFamily], Mapping[str, frozenset[InputFamily]]], ...
    ] = (
        ("discover.point_anomalies", "Find time-series points with unusual values.", _MF, {}),
        ("discover.period_shifts", "Find period-shift candidates from a DeltaFrame.", _DF, {}),
        (
            "discover.driver_axes",
            "Find dimensions that explain a delta.",
            _DF,
            {"search_space": _FIELD_SEMANTIC},
        ),
        (
            "discover.interesting_slices",
            "Find dimension slices with notable values.",
            _MF_OR_DF,
            {"search_space": _FIELD_SEMANTIC},
        ),
        ("discover.interesting_windows", "Find time windows with notable behavior.", _MF_OR_DF, {}),
        (
            "discover.cross_sectional_outliers",
            "Find segments that are outliers compared to their peers.",
            _MF,
            {"peer_scope": _FIELD_SEMANTIC},
        ),
    )

    for obj_id, summary, source_families, extra_inputs in _discover_specs:
        objective = obj_id.split(".", 1)[1]
        # Objectives that accept a MetricFrame source gate on single-metric
        # arity via require_single_metric; declare the precondition in help.
        discover_constraints: tuple[str, ...] = (
            "discover_minimum_evidence",
            "frame_kind_compatible",
        )
        if "MetricFrame" in source_families:
            discover_constraints = (*discover_constraints, "single_metric_input")
        descriptors.append(
            OperatorCapability(
                id=obj_id,
                public_entrypoint=f"session.discover.{objective}(...)",
                help_target=obj_id,
                summary=summary,
                root_group="typed_analysis",
                root_visibility="grouped",
                constraint_ids=discover_constraints,
                callable_path=f"marivo.analysis.session.core.SessionDiscoverNamespace.{objective}",
                receiver="SessionDiscoverNamespace",
                accepted_inputs={
                    "source": source_families,
                    **extra_inputs,
                },
                output_contract=_output("CandidateSet"),
            )
        )

    descriptors.append(
        OperatorCapability(
            id="discover.semantic_hypotheses",
            public_entrypoint="session.discover.semantic_hypotheses(source, limit=50)",
            help_target="discover.semantic_hypotheses",
            summary=(
                "Resolve bounded unscored Metric hypotheses through one explicit "
                "ontology edge while preserving the exact source scope."
            ),
            root_group="typed_analysis",
            root_visibility="grouped",
            constraint_ids=("frame_kind_compatible",),
            callable_path=(
                "marivo.analysis.session.core.SessionDiscoverNamespace.semantic_hypotheses"
            ),
            receiver="SessionDiscoverNamespace",
            accepted_inputs={"source": _MF_OR_DF},
            artifact_admission={
                "source": ArtifactAdmissionRule(
                    semantic_shapes={
                        "MetricFrame": frozenset({"scalar", "time_series", "segmented", "panel"}),
                        "DeltaFrame": frozenset({"scalar", "time_series", "segmented", "panel"}),
                    }
                )
            },
            output_contract=_output("CandidateSet", shapes=("semantic_hypothesis",)),
        )
    )

    # -- Transform operators ----------------------------------------------

    shared_transform_ops: tuple[
        tuple[str, str, frozenset[InputFamily], Mapping[str, frozenset[InputFamily]]], ...
    ] = (
        ("filter", "Filter rows using a boolean predicate.", _MF_OR_DF, {}),
        (
            "slice",
            "Filter rows by catalog-backed axis values.",
            _MF_OR_DF,
            {"slice_by": _FIELD_SEMANTIC},
        ),
        (
            "rollup",
            "Aggregate by dropping axes or re-bucketing time; certified semantic "
            "Grain roll-ups require containment and complete source periods.",
            _MF_OR_DF,
            {"drop_axes": _FIELD_SEMANTIC},
        ),
        ("topk", "Keep the largest rows ordered by a column.", _MF_OR_DF, {}),
        ("bottomk", "Keep the smallest rows ordered by a column.", _MF_OR_DF, {}),
        ("rank", "Add a rank column ordered by a value column.", _MF_OR_DF, {}),
        (
            "window",
            "Restrict to a half-open time window.",
            _MF_OR_DF,
            {"window": frozenset({"TimeScopeInput"})},
        ),
    )

    for op_name, summary, families, extra_inputs in shared_transform_ops:
        cap_id = f"transform.{op_name}"
        descriptors.append(
            OperatorCapability(
                id=cap_id,
                public_entrypoint=f"frame.transform.{op_name}(...)",
                help_target=cap_id,
                summary=summary,
                root_group="family_operations",
                root_visibility="grouped",
                constraint_ids=(
                    "transform_arguments",
                    "transform_frame_shape",
                    "transform_operator_supported",
                    "single_metric_input",
                ),
                callable_path=f"marivo.analysis.frames.transforms._FrameTransforms.{op_name}",
                receiver="MetricFrameTransforms|DeltaFrameTransforms",
                accepted_inputs={
                    "receiver": families,
                    **extra_inputs,
                },
                output_contract=_output(SameAsInputFamily(parameter="receiver")),
            )
        )

    # normalize is MetricFrame-only
    descriptors.append(
        OperatorCapability(
            id="transform.normalize",
            public_entrypoint="frame.transform.normalize(...)",
            help_target="transform.normalize",
            summary="Normalize MetricFrame values.",
            root_group="family_operations",
            root_visibility="grouped",
            constraint_ids=(
                "transform_arguments",
                "transform_frame_shape",
                "transform_operator_supported",
                "single_metric_input",
            ),
            callable_path="marivo.analysis.frames.transforms.MetricFrameTransforms.normalize",
            receiver="MetricFrameTransforms",
            accepted_inputs={
                "receiver": _MF,
            },
            output_contract=_output("MetricFrame"),
        )
    )

    # -- Frame methods (operators / reads) --------------------------------

    descriptors.append(
        OperatorCapability(
            id="MetricFrame.metric",
            public_entrypoint="frame.metric(...)",
            help_target="MetricFrame.metric",
            summary="Project one metric out of a multi-metric frame.",
            root_group="family_operations",
            root_visibility="grouped",
            constraint_ids=("frame_kind_compatible",),
            callable_path="marivo.analysis.frames.metric.MetricFrame.metric",
            receiver="MetricFrame",
            accepted_inputs={"receiver": _MF},
            output_contract=_output("MetricFrame"),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="MetricFrame.components",
            public_entrypoint="frame.components()",
            help_target="MetricFrame.components",
            summary="Load the recursive component graph persisted for a MetricFrame.",
            root_group="family_operations",
            root_visibility="grouped",
            constraint_ids=("component_frame_available",),
            callable_path="marivo.analysis.frames.metric.MetricFrame.components",
            receiver="MetricFrame",
            accepted_inputs={"receiver": _MF},
            output_contract=_output("ComponentFrame"),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="MetricFrame.coverage",
            public_entrypoint="frame.coverage()",
            help_target="MetricFrame.coverage",
            summary="Load the linked CoverageFrame for this metric frame.",
            root_group="family_operations",
            root_visibility="grouped",
            constraint_ids=(),
            callable_path="marivo.analysis.frames.metric.MetricFrame.coverage",
            receiver="MetricFrame",
            accepted_inputs={"receiver": _MF},
            output_contract=_output("CoverageFrame", nullable=True),
            additional_examples=(
                HelpExample(
                    label="Guard nullable coverage",
                    code="coverage = frame.coverage()",
                    requires=("frame",),
                ),
            ),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="DeltaFrame.components",
            public_entrypoint="frame.components()",
            help_target="DeltaFrame.components",
            summary="Load the linked ComponentFrame for component-aware deltas.",
            root_group="family_operations",
            root_visibility="grouped",
            constraint_ids=("component_frame_available",),
            callable_path="marivo.analysis.frames.delta.DeltaFrame.components",
            receiver="DeltaFrame",
            accepted_inputs={"receiver": _DF},
            output_contract=_output("ComponentFrame"),
        )
    )

    descriptors.append(
        ReadCapability(
            id="CandidateSet.select",
            public_entrypoint="cands.select(...)",
            help_target="CandidateSet.select",
            summary="Return one closed shape-specific selection by its stable item_id.",
            root_group="family_operations",
            root_visibility="grouped",
            constraint_ids=("frame_kind_compatible",),
            callable_path="marivo.analysis.frames.candidate.CandidateSet.select",
            receiver_family="CandidateSet",
            result_kind="defensive_copy",
            read_bound="bounded",
            produced_input_family="OntologyMetricCandidate",
        )
    )
    descriptors.append(
        ReadCapability(
            id="AttributionFrame.at_resolution",
            public_entrypoint="frame.at_resolution(axes=[...])",
            help_target="AttributionFrame.at_resolution",
            summary="Select one exact ordered semantic-ref resolution without a query.",
            root_group="family_operations",
            root_visibility="grouped",
            constraint_ids=("frame_kind_compatible",),
            callable_path=("marivo.analysis.frames.attribution.AttributionFrame.at_resolution"),
            receiver_family="AttributionFrame",
            result_kind="defensive_copy",
            read_bound="bounded",
            additional_examples=(
                HelpExample(
                    label="Select one exact ordered semantic-ref prefix",
                    code="regional = frame.at_resolution(axes=[region])",
                    requires=("frame", "region"),
                ),
            ),
        )
    )

    # -- Shape-narrowing methods (reads) ----------------------------------

    for class_name, cls_obj, methods in (
        ("MetricFrame", MetricFrame, ("as_scalar", "as_time_series", "as_segmented", "as_panel")),
        ("DeltaFrame", DeltaFrame, ("as_scalar", "as_time_series", "as_segmented", "as_panel")),
        ("AttributionFrame", AttributionFrame, ("as_sum", "as_ratio_mix", "as_weighted_mix")),
        (
            "CandidateSet",
            CandidateSet,
            (
                "as_point_anomaly",
                "as_period_shift",
                "as_driver_axis",
                "as_slice",
                "as_window",
                "as_cross_sectional_outlier",
                "as_semantic_hypothesis",
            ),
        ),
    ):
        family = class_name
        for method_name in methods:
            descriptors.append(
                ReadCapability(
                    id=f"{class_name}.{method_name}",
                    public_entrypoint=f"frame.{method_name}()",
                    help_target=f"{class_name}.{method_name}",
                    summary=f"Narrow {class_name} to its declared shape.",
                    root_group="family_operations",
                    root_visibility="grouped",
                    constraint_ids=("frame_kind_compatible",),
                    callable_path=_module_path_for(getattr(cls_obj, method_name)),
                    receiver_family=family,
                    result_kind="immutable_metadata",
                    read_bound="bounded",
                )
            )

    # Also register DeltaFrame.predicted_attribution_shape as a read
    descriptors.append(
        ReadCapability(
            id="DeltaFrame.predicted_attribution_shape",
            public_entrypoint="delta.predicted_attribution_shape()",
            help_target="DeltaFrame.predicted_attribution_shape",
            summary="Predict the AttributionFrame shape decompose will produce.",
            root_group="family_operations",
            root_visibility="grouped",
            constraint_ids=(),
            callable_path="marivo.analysis.frames.delta.DeltaFrame.predicted_attribution_shape",
            receiver_family="DeltaFrame",
            result_kind="immutable_metadata",
            read_bound="bounded",
        )
    )

    # -- BaseFrame reads --------------------------------------------------

    descriptors.append(
        ReadCapability(
            id="BaseFrame.show",
            public_entrypoint="frame.show()",
            help_target="BaseFrame.show",
            summary="Bounded inspection of the artifact.",
            root_group="artifact_inspection",
            root_visibility="grouped",
            constraint_ids=("frame_read_bounds",),
            callable_path="marivo.analysis.frames.base.BaseFrame.show",
            receiver_family="BaseFrame",
            result_kind="terminal_text",
            read_bound="bounded",
        )
    )

    for method_name, summary in (
        ("render", "Return bounded session state as text without writing stdout."),
        ("show", "Print bounded session state for inspection."),
    ):
        descriptors.append(
            ReadCapability(
                id=f"Session.{method_name}",
                public_entrypoint=f"session.{method_name}()",
                help_target=f"Session.{method_name}",
                summary=summary,
                root_group="artifact_inspection",
                root_visibility="grouped",
                constraint_ids=(),
                callable_path=f"marivo.analysis.session.core.Session.{method_name}",
                receiver_family="Session",
                result_kind="terminal_text",
                read_bound="bounded",
            )
        )

    descriptors.append(
        ReadCapability(
            id="events.watermark",
            public_entrypoint="session.events.watermark(...)",
            help_target="events.watermark",
            summary="Return the authoritative observed completeness watermark for one Event, or None.",
            root_group="artifact_inspection",
            root_visibility="grouped",
            constraint_ids=(),
            callable_path="marivo.analysis.session.core.SessionEvents.watermark",
            receiver_family="SessionEvents",
            result_kind="immutable_metadata",
            read_bound="bounded",
        )
    )

    descriptors.append(
        ReadCapability(
            id="events.occurrence_bounds",
            public_entrypoint="session.events.occurrence_bounds(...)",
            help_target="events.occurrence_bounds",
            summary=(
                "Return EventOccurrenceBounds for one exact Event or "
                "StateModel's observed occurrences."
            ),
            root_group="artifact_inspection",
            root_visibility="grouped",
            constraint_ids=(),
            callable_path=("marivo.analysis.session.core.SessionEvents.occurrence_bounds"),
            receiver_family="SessionEvents",
            result_kind="immutable_metadata",
            read_bound="bounded",
        )
    )

    descriptors.append(
        ReadCapability(
            id="BaseFrame.contract",
            public_entrypoint="frame.contract()",
            help_target="BaseFrame.contract",
            summary="Return the mechanical consumption contract for the artifact.",
            root_group="artifact_inspection",
            root_visibility="grouped",
            constraint_ids=(),
            callable_path="marivo.analysis.frames.base.BaseFrame.contract",
            receiver_family="BaseFrame",
            result_kind="immutable_metadata",
            read_bound="bounded",
        )
    )

    # -- Boundaries -------------------------------------------------------

    descriptors.append(
        BoundaryCapability(
            id="boundary.to_pandas",
            public_entrypoint="frame.to_pandas()",
            help_target="boundary.to_pandas",
            summary="Terminal exit: return a defensive pandas DataFrame copy.",
            root_group="boundaries",
            root_visibility="direct",
            constraint_ids=("frame_immutable",),
            callable_path="marivo.analysis.frames.base.BaseFrame.to_pandas",
            direction="terminal_exit",
            accepted_inputs={
                "receiver": all_artifact_families,
            },
            output_family="pandas.DataFrame",
            preserves=(),
            does_not_preserve=("lineage", "meta", "session_ownership", "evidence"),
        )
    )

    # -- Constructors -----------------------------------------------------

    constructor_specs: tuple[
        tuple[str, str, str, str, object, str, InputFamily | None],
        ...,
    ] = (
        (
            "grain",
            "mv.grain(...)",
            "grain",
            "Construct one builtin aggregation Grain.",
            grain,
            "Grain",
            None,
        ),
        (
            "funnel_loss_rate",
            "mv.funnel_loss_rate(...)",
            "funnel_loss_rate",
            "Target one exact non-initial funnel PatternStep loss rate.",
            funnel_loss_rate,
            "FunnelLossRate",
            "FunnelLossRate",
        ),
        (
            "step",
            "mv.step(...)",
            "step",
            "Construct one typed Event Journey step from a participant role.",
            step,
            "PatternStep",
            None,
        ),
        (
            "sequence",
            "mv.sequence(...)",
            "sequence",
            "Construct an ordered EventPattern from typed steps.",
            sequence,
            "EventPattern",
            "EventPattern",
        ),
        (
            "first_per_subject",
            "mv.first_per_subject()",
            "first_per_subject",
            "Choose the earliest first-step occurrence per subject.",
            first_per_subject,
            "FirstPerSubject",
            "EventMatchingPolicy",
        ),
        (
            "every_start",
            "mv.every_start(...)",
            "every_start",
            "Create one attempt per first-step occurrence.",
            every_start,
            "EveryStart",
            "EventMatchingPolicy",
        ),
        (
            "declared_complete_through",
            "mv.declared_complete_through(...)",
            "declared_complete_through",
            "Declare exact Event inputs complete through a follow-up bound.",
            declared_complete_through,
            "CompletenessDeclaration",
            "CompletenessDeclaration",
        ),
        (
            "dropped_before",
            "mv.dropped_before(...)",
            "dropped_before",
            "Select resolved subjects lost before one exact EventPattern step.",
            dropped_before,
            "DroppedBefore",
            "SubjectSelection",
        ),
        (
            "from_inception",
            "mv.from_inception()",
            "from_inception",
            "Construct the required first-inception Lifecycle replay seed.",
            from_inception,
            "FromInception",
            "LifecycleSeed",
        ),
        (
            "in_state",
            "mv.in_state(...)",
            "in_state",
            "Select subjects in one exact modeled state at an explicit instant.",
            in_state,
            "InState",
            "SubjectSelection",
        ),
        (
            "window_bucket",
            "mv.window_bucket()",
            "window_bucket",
            "Construct a window-bucket alignment policy.",
            window_bucket,
            "AlignmentPolicy",
            "AlignmentPolicy",
        ),
        (
            "day_of_week",
            "mv.day_of_week(...)",
            "day_of_week",
            "Construct a day-of-week containing-period alignment policy.",
            day_of_week,
            "AlignmentPolicy",
            "AlignmentPolicy",
        ),
        (
            "period_progress",
            "mv.period_progress(...)",
            "period_progress",
            "Construct same-progress alignment inside one certified target period.",
            period_progress,
            "AlignmentPolicy",
            "AlignmentPolicy",
        ),
        (
            "period_correspondence",
            "mv.period_correspondence(...)",
            "period_correspondence",
            "Construct named certified period correspondence alignment.",
            period_correspondence,
            "AlignmentPolicy",
            "AlignmentPolicy",
        ),
        (
            "occurrence_progress",
            "mv.occurrence_progress(...)",
            "occurrence_progress",
            "Construct same-local-day progress alignment inside two exact temporal occurrences.",
            occurrence_progress,
            "AlignmentPolicy",
            "AlignmentPolicy",
        ),
        (
            "working_day_progress",
            "mv.working_day_progress(...)",
            "working_day_progress",
            "Construct same-working-day ordinal alignment under one certified work schedule.",
            working_day_progress,
            "AlignmentPolicy",
            "AlignmentPolicy",
        ),
        (
            "time_scope",
            "mv.time_scope(...)",
            "time_scope",
            "Half-open time interval [start, end) for observe time_scope; "
            'start is inclusive and end is exclusive (for example, end="2026-08-01" '
            "includes all of July and excludes August 1).",
            time_scope,
            "TimeScope",
            "TimeScopeInput",
        ),
        (
            "AbsoluteWindow",
            "mv.time_scope(...)",
            "AbsoluteWindow",
            "Half-open time interval [start, end) with optional grain.",
            AbsoluteWindow,
            "AbsoluteWindow",
            "TimeScopeInput",
        ),
        (
            "SamplingPolicy",
            "mv.SamplingPolicy(...)",
            "SamplingPolicy",
            "Sampling policy for hypothesis_test.",
            SamplingPolicy,
            "SamplingPolicy",
            "SamplingPolicy",
        ),
    )

    for (
        cap_id,
        entrypoint,
        target,
        summary,
        callable_obj,
        output_type,
        produced_input_family,
    ) in constructor_specs:
        descriptors.append(
            ConstructorCapability(
                id=cap_id,
                public_entrypoint=entrypoint,
                help_target=target,
                summary=summary,
                root_group="policies_builders",
                root_visibility=(
                    "grouped"
                    if cap_id
                    in {
                        "dropped_before",
                        "from_inception",
                        "in_state",
                        "day_of_week",
                        "period_progress",
                        "period_correspondence",
                        "occurrence_progress",
                        "working_day_progress",
                        "AbsoluteWindow",
                        "SamplingPolicy",
                    }
                    else "direct"
                ),
                root_summary=(
                    "Construct a half-open [start, end) analysis window."
                    if cap_id == "time_scope"
                    else None
                ),
                constraint_ids=(
                    ("window_absolute_parseable",)
                    if cap_id in {"time_scope", "AbsoluteWindow"}
                    else ("alignment_policy_shape",)
                    if cap_id == "working_day_progress"
                    else ()
                ),
                callable_path=_module_path_for(callable_obj),
                output_type=output_type,
                produced_input_family=produced_input_family,
            )
        )

    descriptors.append(
        ConstructorCapability(
            id="Session.source_bindings",
            public_entrypoint="session.source_bindings({...})",
            help_target="Session.source_bindings",
            summary=(
                "Bind declared non-secret JSON request parameters to one Session runtime "
                "for a nested analysis execution scope."
            ),
            root_group="policies_builders",
            root_visibility="grouped",
            constraint_ids=("source_bindings_exact",),
            callable_path="marivo.analysis.session.core.Session.source_bindings",
            output_type="AbstractContextManager[None]",
            produced_input_family=None,
        )
    )

    runtime_metric_specs: tuple[tuple[str, str, object, str], ...] = (
        (
            "runtime_metric.aggregate",
            "mv.runtime_metric.aggregate(...) ",
            aggregate,
            "RuntimeAggregateExpr",
        ),
        (
            "runtime_metric.slice",
            "mv.runtime_metric.slice(...) ",
            slice,
            "RuntimeSliceExpr",
        ),
        (
            "runtime_metric.weighted_mean",
            "mv.runtime_metric.weighted_mean(...) ",
            weighted_mean,
            "RuntimeWeightedMeanExpr",
        ),
        (
            "runtime_metric.ratio",
            "mv.runtime_metric.ratio(...) ",
            ratio,
            "RuntimeRatioExpr",
        ),
    )
    for cap_id, entrypoint, callable_obj, output_type in runtime_metric_specs:
        descriptors.append(
            ConstructorCapability(
                id=cap_id,
                public_entrypoint=entrypoint.rstrip(),
                help_target=cap_id,
                summary="Build one frozen node in the closed runtime metric expression algebra.",
                root_group="policies_builders",
                root_visibility="grouped",
                constraint_ids=(
                    "runtime_metric_closed_algebra",
                    *(
                        ("runtime_weighted_mean_valid",)
                        if cap_id == "runtime_metric.weighted_mean"
                        else ()
                    ),
                ),
                callable_path=_module_path_for(callable_obj),
                output_type=output_type,
                produced_input_family="RuntimeMetricExpression",
            )
        )

    descriptors.append(
        ConstructorCapability(
            id="AttributionMode",
            public_entrypoint=('mode="joint" | mode="hierarchy" | mode="multiresolution"'),
            help_target="AttributionMode",
            summary=(
                "Multi-axis row layout: joint emits one additive row per complete axis "
                "combination; hierarchy emits rollup-safe prefix rows; multiresolution "
                "independently recomputes each ordered prefix for non-additive methods. "
                "Metric session.attribute calls default to joint for multiple axes. "
                "Funnel attribution and decompose still require an explicit multi-axis mode. "
                "Omit mode for one axis, where a supplied value has no effect. Mode is "
                "distinct from attribution method. DeltaFrame.contract().attribute_admission "
                "lists the exact legal pair and multiple-axes default."
            ),
            root_group="policies_builders",
            root_visibility="grouped",
            constraint_ids=(),
            callable_path=None,
            output_type='Literal["joint", "hierarchy", "multiresolution"]',
        )
    )

    # -- Recovery / reads: session lifecycle ------------------------------

    recovery_specs: tuple[tuple[str, str, str, str, str, str, str], ...] = (
        (
            "session.get_or_create",
            "mv.session.get_or_create(...)",
            "session.get_or_create",
            "Attach to an existing session or create a new one.",
            "recovery",
            "Session",
            "session_name",
        ),
        (
            "session.current",
            "mv.session.current()",
            "session.current",
            "Return the current session or None.",
            "recovery",
            "Session",
            "none",
        ),
        (
            "session.recent",
            "mv.session.recent()",
            "session.recent",
            "Return a bounded page of recently updated project sessions.",
            "recovery",
            "SessionSummaryPage",
            "none",
        ),
        (
            "session.inspect",
            "mv.session.inspect(name)",
            "session.inspect",
            "Read a bounded historical session metadata snapshot without resuming it.",
            "recovery",
            "SessionInspection",
            "session_name",
        ),
        (
            "session.delete",
            "mv.session.delete(name)",
            "session.delete",
            "Permanently delete a session and all its on-disk data.",
            "recovery",
            "None",
            "session_name",
        ),
    )

    for cap_id, entrypoint, target, summary, group, restored, identity in recovery_specs:
        descriptors.append(
            RecoveryCapability(
                id=cap_id,
                public_entrypoint=entrypoint,
                help_target=target,
                summary=summary,
                root_group=group,  # type: ignore[arg-type]  # group is a str from a tuple; validated at runtime
                root_visibility="direct" if cap_id == "session.get_or_create" else "grouped",
                constraint_ids=(),
                callable_path=f"marivo.analysis.session.{cap_id.split('.', 1)[1]}",
                identity_input=identity,
                restored_family=restored,
                query_behavior="none",
            )
        )

    # -- Session job/frame reads ------------------------------------------

    session_read_specs: tuple[tuple[str, str, str, str, str], ...] = (
        (
            "session.jobs",
            "session.jobs()",
            "session.jobs",
            "Return lightweight summaries for every recorded job.",
            "JobSummary",
        ),
        (
            "session.recent_jobs",
            "session.recent_jobs(limit=5)",
            "session.recent_jobs",
            "Return the most recent job summaries.",
            "JobSummary",
        ),
        (
            "session.job",
            "session.job(job_id)",
            "session.job",
            "Return the full record for a single job.",
            "dict",
        ),
        (
            "session.frame_summaries",
            "session.frame_summaries()",
            "session.frame_summaries",
            "Return rich metadata for each persisted frame.",
            "FrameSummaryPage",
        ),
        (
            "session.get_frame",
            "session.get_frame(ref)",
            "session.get_frame",
            "Load a persisted frame by ref or artifact_id.",
            "BaseFrame",
        ),
    )

    for cap_id, entrypoint, target, summary, restored in session_read_specs:
        method_name = cap_id.split(".", 1)[1]
        descriptors.append(
            RecoveryCapability(
                id=cap_id,
                public_entrypoint=entrypoint,
                help_target=target,
                summary=summary,
                root_group="recovery",
                root_visibility="grouped",
                constraint_ids=(),
                callable_path=f"marivo.analysis.session.core.Session.{method_name}",
                identity_input="session_id_or_frame_ref",
                restored_family=restored,
                query_behavior="none",
            )
        )

    # -- Evidence namespace reads -----------------------------------------

    evidence_specs: tuple[tuple[str, str, str, str], ...] = (
        (
            "session.evidence.digests",
            "session.evidence.digests(...) ",
            "session.evidence.digests",
            "Return a bounded newest-first page of persisted artifact digests.",
        ),
        (
            "session.evidence.findings",
            "session.evidence.findings(...)",
            "session.evidence.findings",
            "Return Surface 3 findings for this session.",
        ),
        (
            "session.evidence.finding",
            "session.evidence.finding(id)",
            "session.evidence.finding",
            "Return one canonical typed finding by identity.",
        ),
        (
            "session.evidence.digest",
            "session.evidence.digest(artifact_ref)",
            "session.evidence.digest",
            "Return one persisted artifact digest by identity.",
        ),
        (
            "session.evidence.trace",
            "session.evidence.trace(id)",
            "session.evidence.trace",
            "Trace one finding to its source fields and retained digest items.",
        ),
    )

    for cap_id, entrypoint, target, summary in evidence_specs:
        method_name = cap_id.split(".")[-1]
        descriptors.append(
            ReadCapability(
                id=cap_id,
                public_entrypoint=entrypoint,
                help_target=target,
                summary=summary,
                root_group="recovery",
                root_visibility="grouped",
                constraint_ids=(),
                callable_path=f"marivo.analysis.session.core.EvidenceNamespace.{method_name}",
                receiver_family="EvidenceNamespace",
                result_kind="immutable_metadata",
                read_bound="bounded",
            )
        )

    # -- Semantic catalog reads -------------------------------------------

    catalog_specs: tuple[tuple[str, str, str, str], ...] = (
        *(
            (
                f"catalog.{member.property_name}",
                f"session.catalog.{member.property_name}",
                f"catalog.{member.property_name}",
                f"Browse catalog {member.property_name}.",
            )
            for member in CATALOG_MEMBER_CONTRACTS
        ),
        (
            "catalog.require",
            "session.catalog.require(ref)",
            "catalog.require",
            "Require one exact ref in the compiled catalog.",
        ),
        (
            "catalog.readiness",
            "session.catalog.readiness(refs=...)",
            "catalog.readiness",
            "Check semantic readiness for refs.",
        ),
    )

    for cap_id, entrypoint, target, summary in catalog_specs:
        descriptors.append(
            ReadCapability(
                id=cap_id,
                public_entrypoint=entrypoint,
                help_target=target,
                summary=summary,
                root_group="semantic_inputs",
                root_visibility="grouped",
                constraint_ids=(),
                callable_path=f"marivo.semantic.catalog.SemanticCatalog.{cap_id.split('.', 1)[1]}",
                receiver_family="SemanticCatalog",
                result_kind="immutable_metadata",
                read_bound="bounded",
            )
        )

    descriptors.append(
        ReadCapability(
            id="catalog.period_calendars.period",
            public_entrypoint="calendar.period(level, key)",
            help_target="calendar.period",
            summary="Return one exact certified TimeScope for a named calendar period.",
            root_group="semantic_inputs",
            root_visibility="grouped",
            constraint_ids=(),
            callable_path="marivo.semantic.catalog.PeriodCalendarEntry.period",
            receiver_family="PeriodCalendarEntry",
            result_kind="immutable_metadata",
            read_bound="bounded",
            produced_input_family="TimeScopeInput",
        )
    )

    # -- Grouping descriptors (non-invokable) -----------------------------

    descriptors.append(
        _make_grouping_descriptor(
            "session",
            "Analysis session lifecycle and persistence helpers.",
            "recovery",
        )
    )

    descriptors.append(
        _make_grouping_descriptor(
            "catalog",
            "Browse the typed semantic catalog and all registered object collections.",
            "semantic_inputs",
        )
    )

    descriptors.append(
        _make_grouping_descriptor(
            "runtime_metric",
            "Closed recursive runtime metric expression constructors.",
            "policies_builders",
        )
    )

    descriptors.append(
        _make_grouping_descriptor(
            "alignment",
            "Closed temporal alignment policy family and its operator admission matrix.",
            "policies_builders",
        )
    )

    descriptors.append(
        _make_grouping_descriptor(
            "discover",
            "Objective helpers for deterministic candidate discovery.",
            "typed_analysis",
        )
    )

    descriptors.append(
        _make_grouping_descriptor(
            "transform",
            "Family-preserving reshape of a MetricFrame or DeltaFrame.",
            "family_operations",
        )
    )

    descriptors.append(
        _make_grouping_descriptor(
            "events",
            "Match and reduce typed Event journeys.",
            "typed_analysis",
        )
    )

    descriptors.append(
        _make_grouping_descriptor(
            "lifecycle",
            "Replay and reduce typed StateModel history.",
            "typed_analysis",
        )
    )

    descriptors.append(
        _make_grouping_descriptor(
            "recovery",
            "Cross-script frame and job recovery helpers.",
            "recovery",
        )
    )

    descriptors.append(
        _make_grouping_descriptor(
            "boundary",
            "Typed-flow boundary crossings.",
            "boundaries",
        )
    )

    descriptors.append(
        _make_grouping_descriptor(
            "artifacts",
            (
                "Read artifacts progressively: inspect bounded state, check mechanical "
                "compatibility, then cross a terminal boundary only for intentionally "
                "custom work."
            ),
            "artifact_inspection",
            root_summary="Inspect bounded state, valid continuations, and terminal exits.",
        )
    )

    # -- Finalize: build indexes ------------------------------------------

    return _finalize_registry(tuple(descriptors))


def _module_path_for(obj: object) -> str:
    """Return a best-effort dotted path for a callable or type.

    ``property`` objects lack ``__module__`` and ``__qualname__`` but their
    ``fget`` getter functions carry both, so we unwrap properties before
    computing the path.
    """
    if isinstance(obj, property):
        obj = obj.fget
    module: str | None = getattr(obj, "__module__", None)
    qualname: str | None = getattr(obj, "__qualname__", None) or getattr(obj, "__name__", None)
    if module and qualname:
        return f"{module}.{qualname}"
    if module:
        return str(module)
    return str(qualname) if qualname else ""


def _finalize_registry(
    descriptors: tuple[CapabilityDescriptor, ...],
) -> CapabilityRegistry:
    """Build indexes, validate uniqueness, and generate type algebra rows."""

    _validate_public_type_variants()

    # Validate no duplicate ids
    by_id: dict[str, CapabilityDescriptor] = {}
    for desc in descriptors:
        if desc.id in by_id:
            raise ValueError(f"duplicate capability id: {desc.id}")
        _validate_additional_examples(desc)
        by_id[desc.id] = desc

    # Validate no duplicate help_targets
    by_help_target: dict[str, CapabilityDescriptor] = {}
    for desc in descriptors:
        if desc.help_target in by_help_target:
            raise ValueError(f"duplicate help_target: {desc.help_target}")
        by_help_target[desc.help_target] = desc

    # Build callable identity index keyed by callable_path (canonical string).
    # Reject duplicates: two descriptors with the same callable_path is an
    # error, not a silently-ignored collision.
    by_callable: dict[str, CapabilityDescriptor] = {}
    for desc in descriptors:
        if desc.callable_path is None:
            continue
        if desc.callable_path in by_callable:
            raise ValueError(
                f"duplicate callable_path: {desc.callable_path!r} "
                f"(shared by {by_callable[desc.callable_path].id!r} and {desc.id!r})"
            )
        by_callable[desc.callable_path] = desc

    # Build constructor consumer reverse index from all capabilities that
    # declare accepted_inputs (operators AND boundaries).
    constructor_consumers: dict[str, list[str]] = {}
    for desc in descriptors:
        if isinstance(desc, (OperatorCapability, BoundaryCapability)):
            for param_families in desc.accepted_inputs.values():
                for family in param_families:
                    constructor_consumers.setdefault(family, []).append(desc.id)

    constructor_consumers_frozen: dict[str, tuple[str, ...]] = {
        family: tuple(sorted(set(consumers))) for family, consumers in constructor_consumers.items()
    }

    # Generate type algebra rows
    algebra_rows = _generate_algebra_rows(descriptors, by_id)

    registry = CapabilityRegistry(
        _descriptors=descriptors,
        _by_id=MappingProxyType(by_id),
        _by_help_target=MappingProxyType(by_help_target),
        _by_callable=MappingProxyType(by_callable),
        _constructor_consumers=MappingProxyType(constructor_consumers_frozen),
        _algebra_rows=algebra_rows,
    )
    _validate_input_producers(registry)
    return registry


def _validate_input_producers(registry: CapabilityRegistry) -> None:
    """Require every closed operator input family to have a teaching path."""

    missing: list[str] = []
    for descriptor in registry.descriptors:
        if not isinstance(descriptor, (OperatorCapability, BoundaryCapability)):
            continue
        for parameter, families in descriptor.accepted_inputs.items():
            for family in families:
                if not registry.producer_targets(family):
                    missing.append(f"{descriptor.id}.{parameter}:{family}")
    if missing:
        raise ValueError(
            "analysis input families lack registered producers: " + ", ".join(sorted(missing))
        )


def _validate_public_type_variants() -> None:
    """Keep help variants equal to the closed persisted shape contracts."""
    from marivo.analysis.frames.event import (
        EventFrameMeta,
        EventFunnelFrameMeta,
        EventTimeToEventFrameMeta,
    )
    from marivo.analysis.frames.lifecycle import (
        LifecycleDistributionFrameMeta,
        LifecycleDwellFrameMeta,
        LifecycleHistoryFrameMeta,
        LifecycleTransitionsFrameMeta,
        LifecycleViolationsFrameMeta,
    )
    from marivo.analysis.frames.quality import QualityReportMeta

    def one_literal(model: type[BaseModel], field_name: str) -> str:
        values = get_args(model.model_fields[field_name].annotation)
        if len(values) != 1 or not isinstance(values[0], str):
            raise ValueError(f"{model.__name__}.{field_name} must be one string Literal")
        return values[0]

    expected = {
        "EventFrame": tuple(
            one_literal(model, "semantic_kind")
            for model in (
                EventFrameMeta,
                EventFunnelFrameMeta,
                EventTimeToEventFrameMeta,
            )
        ),
        "LifecycleFrame": tuple(
            one_literal(model, "semantic_kind")
            for model in (
                LifecycleHistoryFrameMeta,
                LifecycleDistributionFrameMeta,
                LifecycleTransitionsFrameMeta,
                LifecycleDwellFrameMeta,
                LifecycleViolationsFrameMeta,
            )
        ),
        "QualityReport": get_args(QualityReportMeta.model_fields["report_shape"].annotation),
    }
    for type_name, expected_variants in expected.items():
        actual = PUBLIC_TYPE_VARIANTS.get(type_name)
        if actual != expected_variants:
            raise ValueError(
                f"{type_name} help variants must match persisted shapes: "
                f"expected={expected_variants!r}, received={actual!r}"
            )


def _validate_additional_examples(descriptor: CapabilityDescriptor) -> None:
    """Validate bounded examples and their ownership before indexing."""
    if not descriptor.additional_examples:
        return
    owned_call = descriptor.public_entrypoint.split("(", 1)[0].strip()
    for example in descriptor.additional_examples:
        if not example.label.strip():
            raise ValueError(f"{descriptor.id}: additional example label must not be empty")
        code = example.code.strip()
        if not code:
            raise ValueError(f"{descriptor.id}: additional example code must not be empty")
        if "..." in code or re.search(r"<[^>]+>", code):
            raise ValueError(f"{descriptor.id}: additional example contains a placeholder")
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise ValueError(f"{descriptor.id}: additional example is not parseable") from exc
        matching_calls = tuple(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _call_name(node.func) == owned_call
        )
        if owned_call and len(matching_calls) != 1:
            raise ValueError(
                f"{descriptor.id}: additional example must call {owned_call!r} exactly once"
            )
        discarded_contract = next(
            (
                statement
                for statement in tree.body
                if isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Attribute)
                and statement.value.func.attr == "contract"
            ),
            None,
        )
        if discarded_contract is not None:
            raise ValueError(
                f"{descriptor.id}: additional example discards .contract() output; "
                "call .contract().show() or assign the contract to a name"
            )
        assigned_names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        }
        assigned_names.update(
            alias.asname or alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        )
        assigned_names.update(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler) and isinstance(node.name, str)
        )
        loaded_names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        implicit_names = {"ms", "mv", "session", *dir(builtins)}
        external_names = loaded_names - assigned_names - implicit_names
        if external_names != set(example.requires):
            raise ValueError(
                f"{descriptor.id}: additional example requirements must match "
                f"external names: expected={sorted(external_names)!r}, "
                f"received={sorted(example.requires)!r}"
            )


def _call_name(node: ast.expr) -> str | None:
    """Return one static dotted call name without evaluating the expression."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _generate_algebra_rows(
    descriptors: tuple[CapabilityDescriptor, ...],
    by_id: Mapping[str, CapabilityDescriptor],
) -> tuple[TypeAlgebraRow, ...]:
    """Generate the type algebra rows from the descriptor table.

    - Invokable operators produce one row each with their accepted input
      families.
    - discover.* and transform.* member edges collapse to the canonical
      ``discover`` / ``transform`` grouping topic in the root algebra.
    - Governed-entry boundary capabilities
      produce a row showing their accepted input families and output family.
    - The single terminal ``boundary.to_pandas`` aggregate row appears once.
    """

    rows: list[TypeAlgebraRow] = []

    # Collapse discover and transform members into their grouping topics.
    discover_source_families: set[str] = set()
    transform_source_families: set[str] = set()

    for desc in descriptors:
        if not isinstance(desc, OperatorCapability):
            continue
        if desc.id.startswith("discover."):
            for families in desc.accepted_inputs.values():
                discover_source_families.update(families)
            continue
        if desc.id.startswith("transform."):
            for families in desc.accepted_inputs.values():
                transform_source_families.update(families)
            continue

        # Non-collapsed operator: produce an individual row.
        source_families: set[str] = set()
        for families in desc.accepted_inputs.values():
            source_families.update(families)

        rows.append(
            TypeAlgebraRow(
                help_target=desc.help_target,
                source_families=frozenset(source_families),
                output_contract=desc.output_contract,
                is_terminal=False,
            )
        )

    # Collapsed grouping rows
    if discover_source_families:
        rows.append(
            TypeAlgebraRow(
                help_target="discover",
                source_families=frozenset(discover_source_families),
                output_contract=_output("CandidateSet"),
                is_terminal=False,
            )
        )

    if transform_source_families:
        rows.append(
            TypeAlgebraRow(
                help_target="transform",
                source_families=frozenset(transform_source_families),
                output_contract="MetricFrame|DeltaFrame",
                is_terminal=False,
            )
        )

    # Governed-entry boundary rows.
    # These produce an artifact family from governed inputs and appear as
    # producer edges alongside the operator that produces the same family.
    for desc in descriptors:
        if not isinstance(desc, BoundaryCapability):
            continue
        if desc.direction != "governed_entry":
            continue
        source_families_gov: set[str] = set()
        for families in desc.accepted_inputs.values():
            source_families_gov.update(families)
        rows.append(
            TypeAlgebraRow(
                help_target=desc.help_target,
                source_families=frozenset(source_families_gov),
                output_contract=desc.output_family,
                is_terminal=False,
            )
        )

    # Terminal boundary row (exactly once)
    to_pandas_desc = by_id.get("boundary.to_pandas")
    if to_pandas_desc is not None and isinstance(to_pandas_desc, BoundaryCapability):
        receiver_families = to_pandas_desc.accepted_inputs.get("receiver", frozenset())
        rows.append(
            TypeAlgebraRow(
                help_target="boundary.to_pandas",
                source_families=frozenset(receiver_families),
                output_contract="pandas.DataFrame",
                is_terminal=True,
            )
        )

    return tuple(rows)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

REGISTRY: CapabilityRegistry = _build_registry()
