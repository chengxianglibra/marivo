"""Shared cumulative frame metadata helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from marivo._fixed_duration import fixed_duration_seconds
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CatalogBodyLeafV1,
    ComparableValueSemanticsV1,
    CumulativeEquivalentComparisonSemanticsV1,
    CumulativeNodeV1,
    ExpressionOccurrenceV1,
    LinearNodeV1,
    LinearTermV1,
    MetricExpressionGraphV1,
    MetricGraphNodeRecordV1,
    MetricGraphNodeV1,
    RatioNodeV1,
    SliceNodeV1,
    WeightedMeanAggregateNodeV1,
)
from marivo.semantic.metric_graph_canonical import (
    fingerprint,
    node_fingerprint,
    validate_graph,
)

CUMULATIVE_CONTRACT_VERSION = 4

EVALUATION_END_COLUMN = "evaluation_end"
CURRENT_EVALUATION_END_COLUMN = "current_evaluation_end"
BASELINE_EVALUATION_END_COLUMN = "baseline_evaluation_end"

type AllHistoryLevelChangeSchema = Literal["all-history-level-change/v1"]
ALL_HISTORY_LEVEL_CHANGE_SCHEMA: AllHistoryLevelChangeSchema = "all-history-level-change/v1"


class AllHistoryLevelChangeV1(BaseModel):
    """Versioned meaning marker for an observed all-history level difference."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_: AllHistoryLevelChangeSchema = Field(
        default=ALL_HISTORY_LEVEL_CHANGE_SCHEMA,
        alias="schema",
        serialization_alias="schema",
    )

    @model_serializer(mode="plain")
    def _serialize_marker(self) -> dict[str, AllHistoryLevelChangeSchema]:
        return {"schema": self.schema_}


class AllHistoryPairAlignmentV1(BaseModel):
    """Validated alignment evidence for one all-history level comparison."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    anchor: Literal["all_history"] = "all_history"
    matched_rows: int = Field(ge=0)
    matched_null_rows: int = Field(ge=0)
    current_unpaired_rows: int = Field(ge=0)
    baseline_unpaired_rows: int = Field(ge=0)
    unpaired_action: Literal["dropped"] = "dropped"


class AuthoredTrailingAnchorV1(BaseModel):
    """Exact authored trailing anchor retained as comparison evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["trailing"]
    count: int = Field(gt=0)
    unit: Literal["second", "minute", "hour", "day", "week"]


class AuthoredGrainToDateAnchorV1(BaseModel):
    """Exact authored grain-to-date anchor retained as comparison evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["grain_to_date"]
    reset_grain: Literal["week", "month", "quarter", "year"]


type AuthoredComparablePeriodAnchorV1 = AuthoredTrailingAnchorV1 | AuthoredGrainToDateAnchorV1


class TrailingAnchorSemanticsV1(BaseModel):
    """Canonical fixed-duration trailing anchor."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["trailing"]
    span_seconds: int = Field(gt=0)


class GrainToDateAnchorSemanticsV1(BaseModel):
    """Canonical calendar-reset grain-to-date anchor."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["grain_to_date"]
    reset_grain: Literal["week", "month", "quarter", "year"]


type ComparablePeriodAnchorSemanticsV1 = TrailingAnchorSemanticsV1 | GrainToDateAnchorSemanticsV1


class CumulativePairSummaryV1(BaseModel):
    """Authoritative row accounting for one paired cumulative comparison."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    schema_: Literal["cumulative-pair-summary/v1"] = Field(
        ...,
        alias="schema",
        serialization_alias="schema",
    )
    matched_rows: int = Field(ge=0)
    matched_null_rows: int = Field(ge=0)
    current_unpaired_rows: int = Field(ge=0)
    baseline_unpaired_rows: int = Field(ge=0)
    fallback_rows: int = Field(ge=0)
    unpaired_action: Literal["dropped"]

    @model_validator(mode="after")
    def _validate_counts(self) -> CumulativePairSummaryV1:
        if self.matched_null_rows > self.matched_rows:
            raise ValueError("matched_null_rows cannot exceed matched_rows")
        if self.fallback_rows > self.matched_rows:
            raise ValueError("fallback_rows cannot exceed matched_rows")
        return self

    @model_serializer(mode="plain")
    def _serialize_summary(self) -> dict[str, object]:
        return {
            "schema": self.schema_,
            "matched_rows": self.matched_rows,
            "matched_null_rows": self.matched_null_rows,
            "current_unpaired_rows": self.current_unpaired_rows,
            "baseline_unpaired_rows": self.baseline_unpaired_rows,
            "fallback_rows": self.fallback_rows,
            "unpaired_action": self.unpaired_action,
        }


class CumulativeAlignmentV1(BaseModel):
    """Typed authored/canonical anchor evidence plus exact pair accounting."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    schema_: Literal["cumulative-alignment/v1"] = Field(
        ...,
        alias="schema",
        serialization_alias="schema",
    )
    current_authored_anchor: AuthoredComparablePeriodAnchorV1
    baseline_authored_anchor: AuthoredComparablePeriodAnchorV1
    canonical_anchor: ComparablePeriodAnchorSemanticsV1
    pairs: CumulativePairSummaryV1

    @model_validator(mode="after")
    def _validate_anchor_equivalence(self) -> CumulativeAlignmentV1:
        current = self.current_authored_anchor
        baseline = self.baseline_authored_anchor
        canonical = self.canonical_anchor
        if current.kind != baseline.kind or current.kind != canonical.kind:
            raise ValueError("cumulative alignment anchors must share one kind")
        if isinstance(current, AuthoredTrailingAnchorV1):
            if not isinstance(baseline, AuthoredTrailingAnchorV1) or not isinstance(
                canonical, TrailingAnchorSemanticsV1
            ):
                raise ValueError("trailing cumulative alignment requires trailing anchors")
            if trailing_span_seconds(current.count, current.unit) != canonical.span_seconds:
                raise ValueError("current trailing anchor does not match canonical span")
            if trailing_span_seconds(baseline.count, baseline.unit) != canonical.span_seconds:
                raise ValueError("baseline trailing anchor does not match canonical span")
        else:
            if not isinstance(baseline, AuthoredGrainToDateAnchorV1) or not isinstance(
                canonical, GrainToDateAnchorSemanticsV1
            ):
                raise ValueError(
                    "grain-to-date cumulative alignment requires grain-to-date anchors"
                )
            if (
                current.reset_grain != canonical.reset_grain
                or baseline.reset_grain != canonical.reset_grain
            ):
                raise ValueError("grain-to-date anchors do not match canonical reset grain")
        return self

    @model_serializer(mode="plain")
    def _serialize_alignment(self) -> dict[str, object]:
        return {
            "schema": self.schema_,
            "current_authored_anchor": self.current_authored_anchor.model_dump(mode="json"),
            "baseline_authored_anchor": self.baseline_authored_anchor.model_dump(mode="json"),
            "canonical_anchor": self.canonical_anchor.model_dump(mode="json"),
            "pairs": self.pairs.model_dump(mode="json"),
        }


type CumulativeAnchor = (
    Literal["all_history"]
    | tuple[Literal["grain_to_date"], str]
    | tuple[Literal["trailing"], int, str]
)
type CumulativeCompareBlocker = Literal[
    "non_cumulative_component",
    "mixed_component_anchors",
    "unresolved_component_anchor",
]

_DIRECT_REQUIRED_FIELDS = frozenset({"kind", "base", "over", "anchor", "components"})
_DERIVED_REQUIRED_FIELDS = frozenset({"kind", "anchor", "compare_blocker", "components"})
_COMPARE_BLOCKERS = frozenset(
    {
        "non_cumulative_component",
        "mixed_component_anchors",
        "unresolved_component_anchor",
    }
)


def normalize_cumulative_anchor(value: object) -> CumulativeAnchor | None:
    """Return a validated in-memory cumulative anchor from metadata."""
    if value == "all_history":
        return "all_history"
    if not isinstance(value, (tuple, list)):
        return None
    if len(value) == 2 and value[0] == "grain_to_date" and isinstance(value[1], str) and value[1]:
        return ("grain_to_date", value[1])
    if (
        len(value) == 3
        and value[0] == "trailing"
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
        and value[1] > 0
        and isinstance(value[2], str)
        and value[2]
    ):
        return ("trailing", value[1], value[2])
    return None


def trailing_span_seconds(
    count: int,
    unit: Literal["second", "minute", "hour", "day", "week"],
) -> int:
    """Return the exact fixed duration represented by one trailing anchor."""

    if type(count) is not int or count <= 0:
        raise ValueError("trailing anchor count must be a positive integer")
    return fixed_duration_seconds(count, unit)


def authored_comparable_period_anchor(
    anchor: CumulativeAnchor,
) -> AuthoredComparablePeriodAnchorV1:
    """Project one persisted comparable-period anchor into typed authored evidence."""

    if isinstance(anchor, tuple) and anchor[0] == "trailing":
        unit = anchor[2]
        if unit not in {"second", "minute", "hour", "day", "week"}:
            raise ValueError(f"unsupported trailing anchor unit: {unit!r}")
        return AuthoredTrailingAnchorV1(
            kind="trailing",
            count=anchor[1],
            unit=cast("Literal['second', 'minute', 'hour', 'day', 'week']", unit),
        )
    if isinstance(anchor, tuple) and anchor[0] == "grain_to_date":
        reset_grain = anchor[1]
        if reset_grain not in {"week", "month", "quarter", "year"}:
            raise ValueError(f"unsupported grain-to-date reset grain: {reset_grain!r}")
        return AuthoredGrainToDateAnchorV1(
            kind="grain_to_date",
            reset_grain=cast("Literal['week', 'month', 'quarter', 'year']", reset_grain),
        )
    raise ValueError(f"anchor is not a comparable-period cumulative anchor: {anchor!r}")


def canonical_comparable_period_anchor(
    anchor: CumulativeAnchor,
) -> ComparablePeriodAnchorSemanticsV1:
    """Return the closed canonical projection used by cumulative compare."""

    authored = authored_comparable_period_anchor(anchor)
    if isinstance(authored, AuthoredTrailingAnchorV1):
        return TrailingAnchorSemanticsV1(
            kind="trailing", span_seconds=trailing_span_seconds(authored.count, authored.unit)
        )
    return GrainToDateAnchorSemanticsV1(
        kind="grain_to_date",
        reset_grain=authored.reset_grain,
    )


def _rewrite_node_children(
    node: MetricGraphNodeV1,
    child_ids: tuple[str, ...],
) -> MetricGraphNodeV1:
    """Return one canonical comparison node with rewritten child ids."""

    match node:
        case CatalogBodyLeafV1() | AggregateNodeV1() | WeightedMeanAggregateNodeV1():
            if child_ids:
                raise ValueError("leaf metric graph node received rewritten children")
            return node
        case SliceNodeV1():
            return replace(node, child_id=child_ids[0])
        case CumulativeNodeV1():
            anchor = normalize_cumulative_anchor(node.anchor)
            if anchor is None:
                raise ValueError("cumulative expression node has an invalid anchor")
            rewritten_anchor = node.anchor
            if isinstance(anchor, tuple) and anchor[0] == "trailing":
                authored = authored_comparable_period_anchor(anchor)
                assert isinstance(authored, AuthoredTrailingAnchorV1)
                rewritten_anchor = (
                    "trailing",
                    trailing_span_seconds(authored.count, authored.unit),
                    "second",
                )
            return replace(node, child_id=child_ids[0], anchor=rewritten_anchor)
        case RatioNodeV1():
            return replace(node, numerator_id=child_ids[0], denominator_id=child_ids[1])
        case LinearNodeV1(terms=terms):
            return replace(
                node,
                terms=tuple(
                    LinearTermV1(child_id=child_id, coefficient=term.coefficient)
                    for term, child_id in zip(terms, child_ids, strict=True)
                ),
            )
    raise TypeError(f"unsupported metric graph node: {type(node).__name__}")


def canonical_cumulative_expression_graph(
    graph: MetricExpressionGraphV1,
) -> MetricExpressionGraphV1:
    """Rebuild a graph bottom-up with fixed trailing anchors in seconds."""

    validate_graph(graph)
    source_nodes = {record.node_id: record.node for record in graph.nodes}
    rewritten_ids: dict[str, str] = {}
    rewritten_nodes: dict[str, MetricGraphNodeV1] = {}
    active: set[str] = set()

    def visit(node_id: str) -> str:
        existing = rewritten_ids.get(node_id)
        if existing is not None:
            return existing
        if node_id in active:
            raise ValueError(f"metric expression graph contains a cycle at {node_id!r}")
        active.add(node_id)
        node = source_nodes[node_id]
        match node:
            case CatalogBodyLeafV1() | AggregateNodeV1() | WeightedMeanAggregateNodeV1():
                child_ids: tuple[str, ...] = ()
            case SliceNodeV1(child_id=child_id) | CumulativeNodeV1(child_id=child_id):
                child_ids = (visit(child_id),)
            case RatioNodeV1(numerator_id=numerator, denominator_id=denominator):
                child_ids = (visit(numerator), visit(denominator))
            case LinearNodeV1(terms=terms):
                child_ids = tuple(visit(term.child_id) for term in terms)
            case _:
                raise TypeError(f"unsupported metric graph node: {type(node).__name__}")
        rewritten = _rewrite_node_children(node, child_ids)
        rewritten_id = node_fingerprint(rewritten)
        rewritten_nodes.setdefault(rewritten_id, rewritten)
        rewritten_ids[node_id] = rewritten_id
        active.remove(node_id)
        return rewritten_id

    rewritten_roots = tuple(visit(root_id) for root_id in graph.roots)
    rewritten_graph = MetricExpressionGraphV1(
        schema="metric-expression/v1",
        roots=rewritten_roots,
        nodes=tuple(
            MetricGraphNodeRecordV1(node_id=node_id, node=rewritten_nodes[node_id])
            for node_id in sorted(rewritten_nodes)
        ),
        occurrences=tuple(
            ExpressionOccurrenceV1(
                path=occurrence.path,
                node_id=rewritten_ids[occurrence.node_id],
                child_paths=occurrence.child_paths,
            )
            for occurrence in graph.occurrences
        ),
    )
    validate_graph(rewritten_graph)
    return rewritten_graph


def canonical_cumulative_expression_fingerprint(
    graph: MetricExpressionGraphV1,
) -> str:
    """Fingerprint the versioned roots of a rebuilt cumulative graph."""

    rewritten = canonical_cumulative_expression_graph(graph)
    return fingerprint(
        {
            "schema": "canonical-cumulative-expression/v1",
            "roots": rewritten.roots,
        }
    )


def _comparable_non_expression_values(
    comparable: ComparableValueSemanticsV1,
) -> tuple[object, ...]:
    return (
        comparable.evaluator_contracts,
        comparable.global_slice,
        comparable.key_schema_fingerprint,
        comparable.unit,
        comparable.fold,
        comparable.source_domain_fingerprint,
        comparable.definition_transform_fingerprint,
    )


def _canonical_comparable_fingerprint(
    comparable: ComparableValueSemanticsV1,
    *,
    canonical_expression_fingerprint: str,
) -> str:
    return fingerprint(
        {
            "schema": "canonical-cumulative-comparable-semantics/v1",
            "expression_fingerprint": canonical_expression_fingerprint,
            "evaluator_contracts": comparable.evaluator_contracts,
            "global_slice": comparable.global_slice,
            "key_schema_fingerprint": comparable.key_schema_fingerprint,
            "unit": comparable.unit,
            "fold": comparable.fold,
            "source_domain_fingerprint": comparable.source_domain_fingerprint,
            "definition_transform_fingerprint": comparable.definition_transform_fingerprint,
        }
    )


def _require_graph_anchor(
    graph: MetricExpressionGraphV1,
    *,
    expected_anchor: CumulativeAnchor,
) -> None:
    node_by_id = {record.node_id: record.node for record in graph.nodes}
    reachable_ids: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in reachable_ids:
            return
        reachable_ids.add(node_id)
        node = node_by_id[node_id]
        match node:
            case CatalogBodyLeafV1() | AggregateNodeV1() | WeightedMeanAggregateNodeV1():
                child_ids: tuple[str, ...] = ()
            case SliceNodeV1(child_id=child_id) | CumulativeNodeV1(child_id=child_id):
                child_ids = (child_id,)
            case RatioNodeV1(numerator_id=numerator, denominator_id=denominator):
                child_ids = (numerator, denominator)
            case LinearNodeV1(terms=terms):
                child_ids = tuple(term.child_id for term in terms)
            case _:
                raise TypeError(f"unsupported metric graph node: {type(node).__name__}")
        for child_id in child_ids:
            visit(child_id)

    for root_id in graph.roots:
        visit(root_id)
    graph_anchors = {
        normalize_cumulative_anchor(record.node.anchor)
        for record in graph.nodes
        if record.node_id in reachable_ids and isinstance(record.node, CumulativeNodeV1)
    }
    if not graph_anchors or None in graph_anchors or graph_anchors != {expected_anchor}:
        raise ValueError(
            "persisted cumulative marker does not match expression graph anchors: "
            f"expected={expected_anchor!r}, graph={sorted(map(repr, graph_anchors))!r}"
        )


def cumulative_equivalent_comparison_semantics(
    *,
    current_graph: MetricExpressionGraphV1,
    baseline_graph: MetricExpressionGraphV1,
    current_comparable: ComparableValueSemanticsV1,
    baseline_comparable: ComparableValueSemanticsV1,
    current_anchor: CumulativeAnchor,
    baseline_anchor: CumulativeAnchor,
) -> CumulativeEquivalentComparisonSemanticsV1:
    """Validate and build comparison-only semantics for trailing/GTD inputs."""

    current_canonical_anchor = canonical_comparable_period_anchor(current_anchor)
    baseline_canonical_anchor = canonical_comparable_period_anchor(baseline_anchor)
    if current_canonical_anchor != baseline_canonical_anchor:
        raise ValueError(
            "cumulative anchors are not canonically equivalent: "
            f"current={current_canonical_anchor!r}, baseline={baseline_canonical_anchor!r}"
        )
    for label, graph, comparable, anchor in (
        ("current", current_graph, current_comparable, current_anchor),
        ("baseline", baseline_graph, baseline_comparable, baseline_anchor),
    ):
        validate_graph(graph)
        if len(graph.roots) != 1 or comparable.expression_fingerprint != graph.roots[0]:
            raise ValueError(
                f"{label} comparable expression fingerprint does not match its graph root"
            )
        _require_graph_anchor(graph, expected_anchor=anchor)
    if _comparable_non_expression_values(current_comparable) != _comparable_non_expression_values(
        baseline_comparable
    ):
        raise ValueError("cumulative compare requires all non-expression semantics to match")
    current_canonical_expression = canonical_cumulative_expression_fingerprint(current_graph)
    baseline_canonical_expression = canonical_cumulative_expression_fingerprint(baseline_graph)
    if current_canonical_expression != baseline_canonical_expression:
        raise ValueError("cumulative compare canonical expression fingerprints do not match")
    current_canonical_comparable = _canonical_comparable_fingerprint(
        current_comparable,
        canonical_expression_fingerprint=current_canonical_expression,
    )
    baseline_canonical_comparable = _canonical_comparable_fingerprint(
        baseline_comparable,
        canonical_expression_fingerprint=baseline_canonical_expression,
    )
    if current_canonical_comparable != baseline_canonical_comparable:
        raise ValueError("cumulative compare canonical comparable semantics do not match")
    return CumulativeEquivalentComparisonSemanticsV1(
        schema="cumulative-equivalent-comparison-semantics/v1",
        current_expression_fingerprint=current_comparable.expression_fingerprint,
        baseline_expression_fingerprint=baseline_comparable.expression_fingerprint,
        canonical_expression_fingerprint=current_canonical_expression,
        current_comparable_semantics_fingerprint=current_comparable.fingerprint,
        baseline_comparable_semantics_fingerprint=baseline_comparable.fingerprint,
        canonical_comparable_semantics_fingerprint=current_canonical_comparable,
    )


def _direct_cumulative_anchor(
    cumulative: Mapping[str, object],
) -> CumulativeAnchor | None:
    """Validate the current direct marker and return its anchor."""
    if not _DIRECT_REQUIRED_FIELDS.issubset(cumulative):
        return None
    if cumulative.get("kind") != "cumulative":
        return None
    base = cumulative.get("base")
    over = cumulative.get("over")
    if not isinstance(base, str) or not base or not isinstance(over, str) or not over:
        return None
    if cumulative.get("components") is not None:
        return None
    return normalize_cumulative_anchor(cumulative.get("anchor"))


def _derived_component_anchors(
    cumulative: Mapping[str, object],
) -> tuple[CumulativeAnchor | None, ...] | None:
    """Validate the required wrapper shape and return component anchors."""
    if not _DERIVED_REQUIRED_FIELDS.issubset(cumulative):
        return None
    components = cumulative.get("components")
    if not isinstance(components, Mapping) or not components:
        return None
    anchors: list[CumulativeAnchor | None] = []
    for role, payload in components.items():
        if not isinstance(role, str) or not role or not isinstance(payload, Mapping):
            return None
        anchors.append(_direct_cumulative_anchor(payload))
    return tuple(anchors)


def cumulative_compare_anchor(cumulative: Mapping[str, object] | None) -> CumulativeAnchor | None:
    """Return the compare anchor from the current cumulative metadata contract."""
    if cumulative is None:
        return None
    kind = cumulative.get("kind")
    if kind == "cumulative":
        return _direct_cumulative_anchor(cumulative)
    if kind != "derived_contains_cumulative":
        return None
    component_anchors = _derived_component_anchors(cumulative)
    if component_anchors is None or cumulative.get("compare_blocker") is not None:
        return None
    anchor = normalize_cumulative_anchor(cumulative.get("anchor"))
    if anchor is None or any(component_anchor != anchor for component_anchor in component_anchors):
        return None
    return anchor


def cumulative_alignment_evidence(
    *,
    current_anchor: CumulativeAnchor,
    baseline_anchor: CumulativeAnchor,
    pairs: CumulativePairSummaryV1,
) -> CumulativeAlignmentV1:
    """Build validated typed evidence for one comparable-period alignment."""

    current_authored = authored_comparable_period_anchor(current_anchor)
    baseline_authored = authored_comparable_period_anchor(baseline_anchor)
    canonical = canonical_comparable_period_anchor(current_anchor)
    if canonical != canonical_comparable_period_anchor(baseline_anchor):
        raise ValueError("cumulative alignment anchors are not canonically equivalent")
    return CumulativeAlignmentV1(
        schema="cumulative-alignment/v1",
        current_authored_anchor=current_authored,
        baseline_authored_anchor=baseline_authored,
        canonical_anchor=canonical,
        pairs=pairs,
    )


def cumulative_compare_blocker(
    cumulative: Mapping[str, object] | None,
) -> CumulativeCompareBlocker | None:
    """Return the persisted blocker for a derived cumulative wrapper."""
    if cumulative is None or cumulative.get("kind") != "derived_contains_cumulative":
        return None
    component_anchors = _derived_component_anchors(cumulative)
    if component_anchors is None:
        return "unresolved_component_anchor"
    blocker = cumulative.get("compare_blocker")
    if blocker in _COMPARE_BLOCKERS:
        if cumulative.get("anchor") is not None:
            return "unresolved_component_anchor"
        if blocker == "mixed_component_anchors":
            valid_anchors = [anchor for anchor in component_anchors if anchor is not None]
            if len(valid_anchors) < 2 or all(
                anchor == valid_anchors[0] for anchor in valid_anchors[1:]
            ):
                return "unresolved_component_anchor"
        if blocker == "unresolved_component_anchor" and all(
            anchor is not None for anchor in component_anchors
        ):
            return "unresolved_component_anchor"
        return cast("CumulativeCompareBlocker", blocker)
    if blocker is None and cumulative_compare_anchor(cumulative) is not None:
        return None
    return "unresolved_component_anchor"


def cumulative_has_evaluation_contract(cumulative: Mapping[str, object] | None) -> bool:
    """Return whether a marker describes a complete cumulative value contract."""

    return cumulative_compare_anchor(cumulative) is not None
