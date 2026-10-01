"""Versioned private execution keys for legacy and staged graph routes."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from typing import TypeAlias

from pydantic import TypeAdapter

from marivo.analysis.compiler.graph_plan import GraphPlan, LocalMethodStage, SourceMethodStage
from marivo.analysis.core import model as core_model
from marivo.analysis.core.domain_captures import (
    EntryAxisCapture,
    EventCapture,
    OrderCapture,
    StateModelCapture,
)
from marivo.analysis.core.graph import FixedLeaf, MethodNode, Node, SourceLeaf
from marivo.analysis.core.predicates import DurationLiteral
from marivo.analysis.core.time_grid import (
    BoundTimeGrid,
    CumulativeBinding,
    EndpointWindow,
    GridVersionSelection,
    TimeCell,
)
from marivo.analysis.datasets.descriptors import _canonical_digest, _is_stable_identifier
from marivo.analysis.domains.completeness import (
    BoundedCompletenessDeclarationV1,
    SourceOriginCompletenessDeclarationV1,
)
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.methods import physical as method_physical
from marivo.analysis.methods.semantics import MethodKey
from marivo.refs import Ref, RefPayloadV1, SemanticKind
from marivo.semantic import metric_graph
from marivo.semantic.ir import TargetSnapshotSelection, TargetValiditySelection
from marivo.semantic.runtime_metric import (
    FrozenSliceMap,
    FrozenSlicePredicateV1,
    RuntimeAggregateExpr,
    RuntimeLinearExpr,
    RuntimeRatioExpr,
    RuntimeSliceExpr,
    RuntimeWeightedMeanExpr,
)

_DEFINITION = re.compile(r"ds_[0-9a-f]{64}\Z")
_GRAPH_PROTOCOL = "marivo.analysis.execution_key/v2"
_CanonicalValue: TypeAlias = None | bool | int | float | str | tuple["_CanonicalValue", ...]

_CAPTURE_WIRE: TypeAdapter[EntryAxisCapture | EventCapture | OrderCapture | StateModelCapture] = (
    TypeAdapter(EntryAxisCapture | EventCapture | OrderCapture | StateModelCapture)
)

_WIRE_TAGS: dict[type[object], str] = {
    BoundedCompletenessDeclarationV1: "bounded_completeness",
    SourceOriginCompletenessDeclarationV1: "origin_completeness",
    DurationLiteral: "duration_literal",
    core_model.EntryAxesPart: "entry_axes_part",
    core_model.FunnelPart: "funnel_part",
    core_model.FunnelComparisonPart: "funnel_comparison_part",
    core_model.FunnelAllocationPart: "funnel_allocation_part",
    core_model.FindingPolicyPart: "finding_policy_part",
    core_model.JourneyPart: "journey_part",
    core_model.OccurrencePart: "occurrence_part",
    core_model.AttributionPart: "attribution_part",
    BoundTimeGrid: "time_grid",
    CumulativeBinding: "cumulative_binding",
    EndpointWindow: "endpoint_window",
    GridVersionSelection: "grid_version_selection",
    TimeCell: "time_cell",
    core_model.Binding: "binding",
    core_model.Coordinate: "coordinate",
    core_model.Correspondence: "correspondence",
    core_model.DomainSignature: "domain",
    core_model.ObservedQuantity: "observed_quantity",
    core_model.DerivedQuantity: "derived_quantity",
    core_model.RowStatisticQuantity: "row_statistic_quantity",
    core_model.RolledQuantity: "rolled_quantity",
    core_model.DisplayPart: "display_part",
    core_model.ReferenceStatePart: "reference_state_part",
    core_model.CohortDecisionPart: "cohort_decision_part",
    core_model.SubjectPart: "subject_part",
    core_model.PairCountsPart: "pair_counts_part",
    core_model.EndpointPart: "endpoint_part",
    core_model.CorrespondencePart: "correspondence_part",
    core_model.OriginalStatePart: "original_state_part",
    core_model.CoordinateStatePart: "coordinate_state_part",
    core_model.RowStatePart: "row_state_part",
    core_model.CoveragePart: "coverage_part",
    core_model.FixedReferencePart: "fixed_reference_part",
    core_model.StatisticalWeightPart: "statistical_weight_part",
    core_model.FactInput: "fact_input",
    core_model.Fact: "fact",
    core_model.Evidence: "evidence",
    core_model.Obligation: "obligation",
    core_model.Signature: "signature",
    TargetSnapshotSelection: "snapshot_selection",
    TargetValiditySelection: "validity_selection",
    RefPayloadV1: "semantic_ref_payload",
    MethodKey: "method",
    method_physical.ScalarType: "scalar_type",
    method_physical.DecimalType: "decimal_type",
    method_physical.DurationType: "duration_type",
    method_physical.NoTime: "no_time",
    method_physical.TimeShape: "time_shape",
    method_physical.SourceShape: "source_shape",
    method_physical.FixedShape: "fixed_shape",
    method_physical.QualificationKey: "qualification",
    RuntimeAggregateExpr: "runtime_aggregate",
    RuntimeSliceExpr: "runtime_slice",
    RuntimeRatioExpr: "runtime_ratio",
    RuntimeWeightedMeanExpr: "runtime_weighted_mean",
    RuntimeLinearExpr: "runtime_linear",
    metric_graph.MetricExpressionGraphV1: "metric_expression_graph",
    metric_graph.MetricGraphNodeRecordV1: "metric_graph_node_record",
    metric_graph.CanonicalSliceEntryV1: "canonical_slice_entry",
    metric_graph.AggregateNodeV1: "aggregate_node",
    metric_graph.SliceNodeV1: "slice_node",
    metric_graph.RatioNodeV1: "ratio_node",
    metric_graph.LinearNodeV1: "linear_node",
    metric_graph.LinearTermV1: "linear_term",
    metric_graph.CumulativeNodeV1: "cumulative_node",
    metric_graph.WeightedMeanAggregateNodeV1: "weighted_mean_node",
    metric_graph.ExpressionOccurrenceV1: "expression_occurrence",
    metric_graph.CatalogBodyLeafV1: "catalog_body_leaf",
    metric_graph.CatalogMetricIdentity: "catalog_metric_identity",
    metric_graph.RuntimeExpressionIdentity: "runtime_expression_identity",
    FrozenSliceMap: "frozen_slice_map",
    FrozenSlicePredicateV1: "frozen_slice_predicate",
}


def _key_error(expected: str, received: str) -> IntegrityError:
    return IntegrityError(
        expected=expected,
        received=received,
        repair="Rebuild the exact admitted graph and verified input bindings.",
        stage="graph_execution_key",
    )


def _wire(value: object) -> _CanonicalValue:
    """Encode only closed contract values, using stable tags instead of class names."""
    if value is None:
        return None
    if type(value) is datetime:
        if value.utcoffset() is None:
            raise _key_error("an aware frozen time boundary", "naive datetime")
        return ("instant", value.isoformat())
    if isinstance(value, Decimal):
        return ("decimal", str(value))
    if type(value) is Ref:
        return ("semantic_ref", value.kind.value, value.path)
    if type(value) is SemanticKind:
        return value.value
    if isinstance(value, (bool, int, float, str)):
        return value
    if type(value) is tuple:
        return tuple(_wire(item) for item in value)
    if isinstance(value, (EntryAxisCapture, EventCapture, OrderCapture, StateModelCapture)):
        return ("r7_capture", type(value).__name__, _CAPTURE_WIRE.dump_json(value).decode())
    tag = _WIRE_TAGS.get(type(value))
    if tag is None or not is_dataclass(value):
        raise _key_error("a closed canonical graph contract value", type(value).__name__)
    return (tag, tuple((item.name, _wire(getattr(value, item.name))) for item in fields(value)))


def _ordered_plan(admitted: GraphPlan) -> _CanonicalValue:
    nodes = {
        stage.node.identity: stage.node
        for stage in admitted.stages
        if isinstance(stage, (SourceMethodStage, LocalMethodStage))
    }
    ordered: list[_CanonicalValue] = []
    for requirement in admitted.physical_requirements:
        qualified = requirement.implementation.qualification
        node = nodes.get(requirement.node_id)
        if not isinstance(qualified, method_physical.Qualified) or node is None:
            raise _key_error("a selected qualified method for every reachable stage", "mismatch")
        ordered.append(
            (
                requirement.key.method.name,
                requirement.key.method.version,
                requirement.key.route,
                _wire(requirement.key),
                qualified.implementation_id,
                requirement.implementation.contract_version,
                _wire(node.signature),
            )
        )
    return tuple(ordered)


def _fixed_input_occurrences(root: Node) -> tuple[FixedLeaf, ...]:
    """Retain ordered fixed operand slots while expanding shared methods once."""
    pending = [root]
    expanded: set[str] = set()
    leaves: list[FixedLeaf] = []
    while pending:
        node = pending.pop()
        if isinstance(node, FixedLeaf):
            leaves.append(node)
        elif isinstance(node, MethodNode) and node.identity not in expanded:
            expanded.add(node.identity)
            pending.extend(edge.node for edge in reversed(node.inputs))
    return tuple(leaves)


@dataclass(frozen=True, slots=True)
class SourceKeyBinding:
    """Selected source metadata; this value alone never certifies a source read."""

    leaf: SourceLeaf
    physical_shape: method_physical.SourceShape
    semantic_dependency_digest: str
    selected_binding_fingerprint: str


@dataclass(frozen=True, slots=True)
class FixedPartKey:
    role: str
    contract_id: str
    contract_version: int
    receipt_digest: str


@dataclass(frozen=True, slots=True)
class FixedKeyInput:
    """Exact fixed metadata; R4.4 must verify receipts before using a hit."""

    leaf: FixedLeaf
    session_ref: str
    producing_run_ref: str
    primary_receipt_digest: str
    ordered_parts: tuple[FixedPartKey, ...]
    input_binding: str
    method_state_contract_id: str
    method_state_version: int
    snapshot_digest: str


def graph_source_execution_key(
    admitted: GraphPlan, bindings: tuple[SourceKeyBinding, ...], run_ref: str
) -> str:
    """Hash one newly allocated source evaluation without consulting old outputs."""
    if (
        admitted.classification.kind != "source"
        or not _is_stable_identifier(run_ref)
        or type(bindings) is not tuple
        or len(bindings) != len(admitted.classification.sources)
        or any(
            binding.leaf is not leaf
            for binding, leaf in zip(bindings, admitted.classification.sources, strict=True)
        )
        or any(
            type(binding.physical_shape) is not method_physical.SourceShape
            or binding.physical_shape != binding.leaf.definition.shape
            or not binding.semantic_dependency_digest
            or not binding.selected_binding_fingerprint
            for binding in bindings
        )
    ):
        raise _key_error("one admitted source plan and exact ordered source bindings", "mismatch")
    ordered_bindings: _CanonicalValue = tuple(
        (
            binding.leaf.definition.fingerprint,
            _wire(binding.leaf.definition.datasource),
            _wire(binding.physical_shape),
            binding.semantic_dependency_digest,
            binding.selected_binding_fingerprint,
        )
        for binding in bindings
    )
    return _canonical_digest(
        (
            _GRAPH_PROTOCOL,
            "source",
            admitted.root.fingerprint,
            _ordered_plan(admitted),
            ordered_bindings,
            run_ref,
        )
    )


def graph_fixed_execution_key(admitted: GraphPlan, inputs: tuple[FixedKeyInput, ...]) -> str:
    """Hash ordered fixed metadata, without claiming receipt or cache authority."""
    occurrences = _fixed_input_occurrences(admitted.root)
    if (
        admitted.classification.kind != "artifact"
        or type(inputs) is not tuple
        or len(inputs) != len(occurrences)
        or any(value.leaf is not leaf for value, leaf in zip(inputs, occurrences, strict=True))
        or any(
            value.session_ref != value.leaf.signature.domain.binding.session_id
            or type(value.ordered_parts) is not tuple
            or type(value.method_state_version) is not int
            or value.method_state_version < 1
            or not all(
                (
                    value.session_ref,
                    value.producing_run_ref,
                    value.primary_receipt_digest,
                    value.input_binding,
                    value.method_state_contract_id,
                    value.snapshot_digest,
                )
            )
            or any(
                type(part.contract_version) is not int
                or part.contract_version < 1
                or not all((part.role, part.contract_id, part.receipt_digest))
                for part in value.ordered_parts
            )
            for value in inputs
        )
    ):
        raise _key_error("one admitted fixed plan and exact ordered Artifact inputs", "mismatch")
    ordered_inputs: _CanonicalValue = tuple(
        (
            value.session_ref,
            value.leaf.artifact.ref,
            value.producing_run_ref,
            value.primary_receipt_digest,
            tuple(
                (part.role, part.contract_id, part.contract_version, part.receipt_digest)
                for part in value.ordered_parts
            ),
            value.input_binding,
            value.method_state_contract_id,
            value.method_state_version,
            value.snapshot_digest,
        )
        for value in inputs
    )
    return _canonical_digest(
        (
            _GRAPH_PROTOCOL,
            "fixed",
            admitted.root.fingerprint,
            _ordered_plan(admitted),
            ordered_inputs,
        )
    )


def execution_key(definition_fingerprint: str) -> str:
    """Bind a definition to common materialization protocol v1, within one Session."""
    return _canonical_digest(("marivo.dataset_execution_key/v1", definition_fingerprint, 1))
