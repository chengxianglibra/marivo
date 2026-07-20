"""Closed role-preserving semantic records for analysis persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from marivo.refs import RefPayloadV1, SemanticKind
from marivo.semantic.metric_graph import (
    CatalogMetricIdentity,
    DeltaComparisonIdentityV1,
    MetricIdentity,
    RuntimeExpressionIdentity,
)
from marivo.semantic.metric_graph_canonical import canonical_value

if TYPE_CHECKING:
    from marivo.analysis.frames.base import BaseFrame

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class AxisBindingV1:
    """Persist one physical result column and its exact semantic axis role."""

    ref: RefPayloadV1
    column: str
    role: Literal["dimension", "time_dimension"]
    grain: str | None = None

    def __post_init__(self) -> None:
        if type(self.ref) is not RefPayloadV1:
            raise TypeError("axis binding ref must be an exact RefPayloadV1")
        expected = {
            "dimension": SemanticKind.DIMENSION,
            "time_dimension": SemanticKind.TIME_DIMENSION,
        }[self.role]
        if self.ref.kind is not expected:
            raise ValueError(
                f"axis binding role {self.role!r} requires {expected.value} ref; "
                f"received {self.ref.kind.value}"
            )
        if type(self.column) is not str or not self.column:
            raise ValueError("axis binding column must be a non-empty string")
        if self.grain is not None and (type(self.grain) is not str or not self.grain):
            raise ValueError("axis binding grain must be a non-empty string when provided")


@dataclass(frozen=True, slots=True)
class SlicePredicateV1:
    """Persist one exact semantic slice role without string-keyed identity."""

    dimension_ref: RefPayloadV1
    value: JsonValue

    def __post_init__(self) -> None:
        if type(self.dimension_ref) is not RefPayloadV1:
            raise TypeError("slice predicate dimension_ref must be an exact RefPayloadV1")
        if self.dimension_ref.kind not in {
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
        }:
            raise ValueError("slice predicate requires a dimension or time_dimension ref")


@dataclass(frozen=True, slots=True)
class ComponentBindingV1:
    """Bind one named composition role to an exact metric identity and column."""

    role: str
    column: str
    metric_identity: MetricIdentity | None = None
    expression_node_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.role) is not str or not self.role:
            raise ValueError("component binding role must be a non-empty string")
        if type(self.column) is not str or not self.column:
            raise ValueError("component binding column must be a non-empty string")
        if (self.metric_identity is None) == (self.expression_node_id is None):
            raise ValueError(
                "component binding requires exactly one metric_identity or expression_node_id"
            )
        if self.expression_node_id is not None and (
            type(self.expression_node_id) is not str or not self.expression_node_id
        ):
            raise ValueError("component binding expression_node_id must be non-empty")


def job_semantics_from_frames(*frames: BaseFrame) -> dict[str, Any]:
    """Derive one role-preserving analysis-job semantic envelope."""

    if not frames:
        raise ValueError("job semantic envelope requires at least one frame")
    fingerprints = {
        value
        for frame in frames
        if (value := getattr(frame.meta, "catalog_definition_fingerprint", None)) is not None
    }
    if len(fingerprints) > 1:
        raise ValueError("job frames disagree on catalog definition fingerprint")

    identities: list[MetricIdentity] = []
    comparisons: list[DeltaComparisonIdentityV1] = []
    digests: list[object] = []
    axis_bindings: list[AxisBindingV1] = []
    predicates: list[SlicePredicateV1] = []
    for frame in frames:
        meta = frame.meta
        comparison = getattr(meta, "comparison_identity", None)
        if isinstance(comparison, DeltaComparisonIdentityV1) and comparison not in comparisons:
            comparisons.append(comparison)
        frame_identities = tuple(getattr(meta, "metric_identities", ()))
        if not frame_identities:
            identity = getattr(meta, "metric_identity", None)
            if isinstance(identity, (CatalogMetricIdentity, RuntimeExpressionIdentity)):
                frame_identities = (identity,)
        for identity in frame_identities:
            if identity not in identities:
                identities.append(identity)
        digest = getattr(meta, "semantic_dependency_digest", None)
        if digest is not None and digest not in digests:
            digests.append(digest)
        for digest in tuple(getattr(meta, "source_dependency_digests", ())):
            if digest not in digests:
                digests.append(digest)
        for binding in tuple(getattr(meta, "axis_bindings", ())):
            if binding not in axis_bindings:
                axis_bindings.append(binding)
        for predicate in tuple(getattr(meta, "slice_predicates", ())):
            if predicate not in predicates:
                predicates.append(predicate)

    def subject(identity: MetricIdentity) -> dict[str, Any]:
        if isinstance(identity, CatalogMetricIdentity):
            return {"kind": "catalog_metric", "metric_ref": identity.metric_ref.to_dict()}
        return {
            "kind": "runtime_expression",
            "expression_schema": identity.expression_schema,
            "expression_fingerprint": identity.expression_fingerprint,
        }

    payload: dict[str, Any] = {
        "catalog_definition_fingerprint": next(iter(fingerprints), None),
        "dimension_refs": [
            binding.ref.to_dict() for binding in axis_bindings if binding.role == "dimension"
        ],
        "slice_predicates": canonical_value(tuple(predicates)),
        "time_dimension_ref": next(
            (
                binding.ref.to_dict()
                for binding in axis_bindings
                if binding.role == "time_dimension"
            ),
            None,
        ),
    }
    if len(digests) == 1:
        payload["semantic_dependency_digest"] = canonical_value(digests[0])
    elif digests:
        payload["semantic_dependency_digests"] = canonical_value(tuple(digests))
    else:
        payload["semantic_dependency_digest"] = None
    if len(comparisons) == 1:
        payload["subject"] = {
            "kind": "delta_metric",
            "comparison": canonical_value(comparisons[0]),
        }
    elif len(identities) == 1:
        payload["subject"] = subject(identities[0])
    else:
        payload["subjects"] = [subject(identity) for identity in identities]
    return payload


__all__ = [
    "AxisBindingV1",
    "ComponentBindingV1",
    "JsonScalar",
    "JsonValue",
    "SlicePredicateV1",
    "job_semantics_from_frames",
]
