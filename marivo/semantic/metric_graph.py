"""Closed, dependency-neutral contracts for metric expression graphs.

This module defines in-process metric-expression graph and identity facts shared
by semantic readiness and analysis. It has no executable expressions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from typing_extensions import TypeAliasType

from marivo._temporal import Grain as TemporalGrain
from marivo.refs import RefPayloadV1, SemanticKind
from marivo.semantic._expression_binding import ExpressionBindingV1
from marivo.semantic.ir import AggKind, AggregateFoldInput

MAX_EXPRESSION_DEPTH = 10
MAX_EXPRESSION_OCCURRENCES = 256

CanonicalScalar: TypeAlias = str | int | float | bool | None
CanonicalValue = TypeAliasType(  # type: ignore[misc]
    "CanonicalValue",
    CanonicalScalar | RefPayloadV1 | tuple["CanonicalValue", ...],  # type: ignore[misc]
)
CanonicalField: TypeAlias = tuple[str, CanonicalValue]  # type: ignore[misc]


@dataclass(frozen=True, slots=True)
class CanonicalSliceEntryV1:
    """One canonical slice predicate keyed by an exact semantic field ref."""

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


CanonicalSlice: TypeAlias = tuple[CanonicalSliceEntryV1, ...]
CumulativeAnchorV1: TypeAlias = (
    Literal["all_history"]
    | tuple[Literal["grain_to_date"], str | TemporalGrain]
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
    """Canonical dependency closure that binds a graph to consumed definitions."""

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
    zero_division: Literal["null", "undefined", "error"]
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


MetricGraphNodeV1: TypeAlias = (
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


MetricIdentity: TypeAlias = CatalogMetricIdentity | RuntimeExpressionIdentity


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
    "CumulativeNodeV1",
    "ExpressionOccurrenceV1",
    "ExpressionPresentationV1",
    "LinearNodeV1",
    "LinearTermV1",
    "MetricExpressionGraphV1",
    "MetricGraphNodeRecordV1",
    "MetricGraphNodeV1",
    "MetricIdentity",
    "PresentationLabelV1",
    "RatioNodeV1",
    "RuntimeExpressionIdentity",
    "SemanticDependencyDigestV1",
    "SemanticDependencyEntryV1",
    "SliceCanonicalizationV1",
    "SliceNodeV1",
    "WeightedMeanAggregateNodeV1",
    "node_child_ids",
]
