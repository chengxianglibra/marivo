"""Closed, dependency-neutral contracts for metric expression graphs.

This module owns persisted computation identities and graph payloads shared by
semantic readiness and analysis.  It intentionally has no analysis imports and
contains no executable expressions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo.refs import RefPayloadV1, SemanticKind
from marivo.semantic._expression_binding import ExpressionBindingV1
from marivo.semantic.ir import AggKind, AggregateFoldInput

MAX_EXPRESSION_DEPTH = 10
MAX_EXPRESSION_OCCURRENCES = 256

type CanonicalScalar = str | int | float | bool | None
type CanonicalValue = CanonicalScalar | RefPayloadV1 | tuple[CanonicalValue, ...]
type CanonicalField = tuple[str, CanonicalValue]


@dataclass(frozen=True, slots=True)
class CanonicalSliceEntryV1:
    """One persisted slice value keyed by an exact semantic field ref."""

    dimension_ref: RefPayloadV1
    value: CanonicalValue

    def __post_init__(self) -> None:
        if type(self.dimension_ref) is not RefPayloadV1:
            raise TypeError("slice dimension_ref must be an exact RefPayloadV1")
        if self.dimension_ref.kind not in {
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
        }:
            raise ValueError("slice dimension_ref must identify a dimension or time_dimension")


type CanonicalSlice = tuple[CanonicalSliceEntryV1, ...]
type CumulativeAnchorV1 = (
    Literal["all_history"]
    | tuple[Literal["grain_to_date"], str]
    | tuple[Literal["trailing"], int, str]
)


@dataclass(frozen=True, slots=True)
class SemanticDependencyEntryV1:
    """Value-relevant projection of one resolved semantic dependency."""

    ref: RefPayloadV1
    body_digest: str | None
    fields: tuple[CanonicalField, ...] = ()
    bindings: tuple[ExpressionBindingV1, ...] = ()

    def __post_init__(self) -> None:
        if type(self.ref) is not RefPayloadV1:
            raise TypeError("semantic dependency entry ref must be an exact RefPayloadV1")
        if self.body_digest is not None and (
            type(self.body_digest) is not str or not self.body_digest
        ):
            raise ValueError("semantic dependency body_digest must be non-empty when provided")
        if type(self.fields) is not tuple:
            raise TypeError("semantic dependency fields must be a tuple")
        if type(self.bindings) is not tuple or any(
            type(binding) is not ExpressionBindingV1 for binding in self.bindings
        ):
            raise TypeError("semantic dependency bindings must contain ExpressionBindingV1 values")


@dataclass(frozen=True, slots=True)
class SemanticDependencyDigestV1:
    """Canonical dependency closure used to validate cache and replay state."""

    schema: Literal["marivo.semantic_dependency_digest/v1"]
    entries: tuple[SemanticDependencyEntryV1, ...]
    digest: str

    def __post_init__(self) -> None:
        if self.schema != "marivo.semantic_dependency_digest/v1":
            raise ValueError(
                "semantic dependency digest schema must be 'marivo.semantic_dependency_digest/v1'"
            )
        if type(self.entries) is not tuple or any(
            type(entry) is not SemanticDependencyEntryV1 for entry in self.entries
        ):
            raise TypeError(
                "semantic dependency digest entries must contain SemanticDependencyEntryV1 values"
            )
        if type(self.digest) is not str or not self.digest.startswith("sha256:"):
            raise ValueError("semantic dependency digest must use the sha256: prefix")


@dataclass(frozen=True, slots=True)
class CatalogBodyLeafV1:
    kind: Literal["catalog_body_leaf"]
    metric_ref: RefPayloadV1
    dependency_fingerprint: str
    unit_override: str | None = None


@dataclass(frozen=True, slots=True)
class AggregateNodeV1:
    kind: Literal["aggregate"]
    target_ref: RefPayloadV1
    dependency_fingerprint: str
    agg: AggKind
    fold: AggregateFoldInput
    filter: CanonicalSlice = ()
    unit_override: str | None = None


@dataclass(frozen=True, slots=True)
class WeightedMeanAggregateNodeV1:
    kind: Literal["weighted_mean"]
    value_ref: RefPayloadV1
    weight_ref: RefPayloadV1
    value_dependency_fingerprint: str
    weight_dependency_fingerprint: str
    filter: CanonicalSlice = ()
    unit_override: str | None = None


@dataclass(frozen=True, slots=True)
class SliceNodeV1:
    kind: Literal["slice"]
    child_id: str
    predicates: CanonicalSlice
    predicate_dependencies: tuple[tuple[RefPayloadV1, str], ...]

    def __post_init__(self) -> None:
        predicate_keys = tuple(item.dimension_ref for item in self.predicates)
        dependency_keys = tuple(key for key, _ in self.predicate_dependencies)
        if predicate_keys != tuple(sorted(set(predicate_keys), key=lambda item: item.path)):
            raise ValueError("SliceNodeV1 predicates must have unique sorted dimension refs")
        if dependency_keys != predicate_keys:
            raise ValueError(
                "SliceNodeV1 predicate dependencies must align with predicate dimension refs"
            )


@dataclass(frozen=True, slots=True)
class CumulativeNodeV1:
    kind: Literal["cumulative"]
    child_id: str
    time_dimension_ref: RefPayloadV1 | None
    anchor: CumulativeAnchorV1
    dependency_fingerprint: str
    unit_override: str | None = None


@dataclass(frozen=True)
class RatioNodeV1:
    kind: Literal["ratio"]
    numerator_id: str
    denominator_id: str
    zero_division: Literal["null", "error"]
    unit_override: str | None = None


@dataclass(frozen=True)
class LinearTermV1:
    child_id: str
    coefficient: float


@dataclass(frozen=True)
class LinearNodeV1:
    kind: Literal["linear"]
    terms: tuple[LinearTermV1, ...]
    unit_override: str | None = None


type MetricGraphNodeV1 = (
    CatalogBodyLeafV1
    | AggregateNodeV1
    | WeightedMeanAggregateNodeV1
    | SliceNodeV1
    | CumulativeNodeV1
    | RatioNodeV1
    | LinearNodeV1
)


@dataclass(frozen=True)
class MetricGraphNodeRecordV1:
    node_id: str
    node: MetricGraphNodeV1


@dataclass(frozen=True)
class ExpressionOccurrenceV1:
    """One submitted occurrence retained before DAG interning."""

    path: str
    node_id: str
    child_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetricExpressionGraphV1:
    """Canonical DAG plus the pre-CSE ordered occurrence forest."""

    schema: Literal["metric-expression/v1"]
    roots: tuple[str, ...]
    nodes: tuple[MetricGraphNodeRecordV1, ...]
    occurrences: tuple[ExpressionOccurrenceV1, ...]


@dataclass(frozen=True)
class PresentationLabelV1:
    occurrence_path: str
    label: str


@dataclass(frozen=True)
class ExpressionPresentationV1:
    schema: Literal["metric-presentation/v1"]
    labels: tuple[PresentationLabelV1, ...]


@dataclass(frozen=True)
class SliceCanonicalizationV1:
    """Canonical slice rewrite plus submitted-to-canonical occurrence paths."""

    graph: MetricExpressionGraphV1
    presentation: ExpressionPresentationV1
    occurrence_path_map: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class CatalogMetricIdentity:
    kind: Literal["catalog"]
    metric_ref: RefPayloadV1


@dataclass(frozen=True)
class RuntimeExpressionIdentity:
    kind: Literal["runtime_expression"]
    expression_schema: Literal["metric-expression/v1"]
    expression_fingerprint: str


type MetricIdentity = CatalogMetricIdentity | RuntimeExpressionIdentity


@dataclass(frozen=True)
class DatasourceCompatibilityDomainV1:
    """Exact resolved source identity required for cache/replay compatibility."""

    schema: Literal["datasource-compatibility/v1"]
    datasource_ref: RefPayloadV1
    backend_type: str
    profile_fingerprint: str

    def __post_init__(self) -> None:
        if self.datasource_ref.kind is not SemanticKind.DATASOURCE:
            raise ValueError("datasource compatibility domain requires a datasource ref")


@dataclass(frozen=True)
class MetricKeyFieldV1:
    name: str
    dtype: str
    nullable: bool


@dataclass(frozen=True)
class MetricKeySchemaV1:
    schema: Literal["metric-key-schema/v1"]
    fields: tuple[MetricKeyFieldV1, ...]
    fingerprint: str


@dataclass(frozen=True)
class ComparableValueSemanticsV1:
    schema: Literal["comparable-value-semantics/v1"]
    expression_fingerprint: str
    evaluator_contracts: tuple[str, ...]
    global_slice: CanonicalSlice
    key_schema_fingerprint: str
    unit: str | None
    fold: CanonicalValue
    source_domain_fingerprint: str
    definition_transform_fingerprint: str | None
    fingerprint: str


@dataclass(frozen=True)
class MetricArtifactIdentityV1:
    schema: Literal["metric-artifact/v1"]
    metric_identities: tuple[MetricIdentity, ...]
    scope_fingerprint: str
    source_domain_fingerprint: str
    dependency_fingerprint: str
    snapshot_fingerprint: str
    coverage_fingerprint: str
    presentation_fingerprint: str
    artifact_schema_version: str
    fingerprint: str


@dataclass(frozen=True)
class DeltaComparisonIdentityV1:
    schema: Literal["delta-comparison/v1"]
    current: MetricIdentity
    baseline: MetricIdentity
    current_artifact_id: str
    baseline_artifact_id: str
    comparable_semantics_fingerprint: str
    alignment_policy_fingerprint: str


@dataclass(frozen=True, slots=True)
class CatalogMetricSubjectV1:
    kind: Literal["catalog_metric"]
    session_id: str
    metric_ref: RefPayloadV1
    artifact_id: str
    scope_fingerprint: str


@dataclass(frozen=True)
class RuntimeExpressionSubjectV1:
    kind: Literal["runtime_expression"]
    session_id: str
    expression_fingerprint: str
    artifact_id: str
    scope_fingerprint: str


@dataclass(frozen=True)
class DeltaMetricSubjectV1:
    kind: Literal["delta_metric"]
    session_id: str
    comparison: DeltaComparisonIdentityV1


type TypedEvidenceSubject = (
    CatalogMetricSubjectV1 | RuntimeExpressionSubjectV1 | DeltaMetricSubjectV1
)


def node_child_ids(node: MetricGraphNodeV1) -> tuple[str, ...]:
    """Return ordered child ids for exhaustive graph traversal."""
    match node:
        case CatalogBodyLeafV1() | AggregateNodeV1() | WeightedMeanAggregateNodeV1():
            return ()
        case SliceNodeV1(child_id=child_id) | CumulativeNodeV1(child_id=child_id):
            return (child_id,)
        case RatioNodeV1(numerator_id=numerator, denominator_id=denominator):
            return (numerator, denominator)
        case LinearNodeV1(terms=terms):
            return tuple(term.child_id for term in terms)
        case _:
            raise TypeError(f"unsupported metric graph node: {type(node).__name__}")


__all__ = [
    "MAX_EXPRESSION_DEPTH",
    "MAX_EXPRESSION_OCCURRENCES",
    "AggregateNodeV1",
    "CanonicalSliceEntryV1",
    "CatalogBodyLeafV1",
    "CatalogMetricIdentity",
    "CatalogMetricSubjectV1",
    "ComparableValueSemanticsV1",
    "CumulativeNodeV1",
    "DatasourceCompatibilityDomainV1",
    "DeltaComparisonIdentityV1",
    "DeltaMetricSubjectV1",
    "ExpressionOccurrenceV1",
    "ExpressionPresentationV1",
    "LinearNodeV1",
    "LinearTermV1",
    "MetricArtifactIdentityV1",
    "MetricExpressionGraphV1",
    "MetricGraphNodeRecordV1",
    "MetricGraphNodeV1",
    "MetricIdentity",
    "MetricKeyFieldV1",
    "MetricKeySchemaV1",
    "PresentationLabelV1",
    "RatioNodeV1",
    "RuntimeExpressionIdentity",
    "RuntimeExpressionSubjectV1",
    "SemanticDependencyDigestV1",
    "SemanticDependencyEntryV1",
    "SliceCanonicalizationV1",
    "SliceNodeV1",
    "TypedEvidenceSubject",
    "WeightedMeanAggregateNodeV1",
    "node_child_ids",
]
