"""Closed retained fold authority, independent of executable source definitions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from marivo._temporal import (
    Grain,
    PeriodCalendarSnapshotV1,
    TimeScope,
    _new_time_scope,
    _snapshot_from_json,
    _snapshot_json,
    builtin_grain,
    ref_factory_period_calendar,
    semantic_grain,
)
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetFieldId,
    DatasetRowContract,
    _canonical_digest,
    _descriptor_payload,
)
from marivo.analysis.datasets.handles import CanonicalValue, _LogicalNodePayload
from marivo.refs import SemanticKind
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CumulativeNodeV1,
    LinearNodeV1,
    RatioNodeV1,
    SliceNodeV1,
    TargetMetricContract,
    WeightedMeanAggregateNodeV1,
)

if TYPE_CHECKING:
    from marivo.analysis.observation.contracts import MetricDefinition

Merge = Literal["sum", "min", "max", "first", "last", "blocked"]
BUILTIN_GRAIN_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}
BUILTIN_GRAIN_MONTHS = {"month": 1, "quarter": 3, "year": 12}


class FoldNodeV1(BaseModel):
    """A closed value-composition node with no source expression or catalog leaf."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)
    node_id: str
    kind: Literal["component", "ratio", "linear", "identity"]
    children: tuple[str, ...] = ()
    coefficients: tuple[float, ...] = ()
    zero_division: Literal["null", "error"] = "null"


class FoldComponentV1(BaseModel):
    """Exact named component state and independently admitted axis reductions."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)
    node_id: str
    kind: Literal["sum", "count", "min", "max", "mean", "weighted_mean", "opaque"]
    state_columns: tuple[tuple[str, str], ...]
    spatial_merge: Merge
    time_merge: Merge
    empty_rule: Literal["zero", "null"]
    null_rule: Literal["ignore_null_inputs", "non_null_pairs"]
    cumulative: bool


class MetricFoldAuthorityV1(BaseModel):
    """The immutable merge/finalize closure for exactly one visible Metric."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)
    metric_ref: str
    field_id: str
    root_id: str
    nodes: tuple[FoldNodeV1, ...]
    components: tuple[FoldComponentV1, ...]
    axis_partitions: tuple[tuple[str, str], ...]

    @property
    def cumulative(self) -> bool:
        return any(component.cumulative for component in self.components)


class FoldAuthorityV1(BaseModel):
    """Versioned row meaning, with exact scope and optional certified calendar."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)
    schema_version: Literal[1]
    metrics: tuple[MetricFoldAuthorityV1, ...]
    grain: tuple[str, str, str] | None = None
    scope: tuple[str, str] | None = None
    calendar_json: str | None = None
    coverage: Literal["current_rows"] = "current_rows"

    def to_json(self) -> str:
        return self.model_dump_json()

    def time_grain(self) -> Grain | None:
        if self.grain is None:
            return None
        kind, first, second = self.grain
        if kind == "builtin":
            return builtin_grain(first, count=int(second))
        if kind == "semantic":
            return semantic_grain(calendar=ref_factory_period_calendar(first), level=second)
        raise ValueError("invalid retained grain kind")

    def time_scope(self) -> TimeScope | None:
        return (
            None if self.scope is None else _new_time_scope(start=self.scope[0], end=self.scope[1])
        )

    def temporal_snapshot(self) -> PeriodCalendarSnapshotV1 | None:
        if self.calendar_json is None:
            return None
        value: object = json.loads(self.calendar_json)
        if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
            raise ValueError("invalid retained calendar")
        return _snapshot_from_json(value)


def grain_authority(grain: Grain | None) -> tuple[str, str, str] | None:
    if grain is None:
        return None
    if grain.kind == "builtin":
        return ("builtin", str(grain.unit), str(grain.count))
    if grain.calendar is None or grain.level is None:
        raise ValueError("incomplete semantic grain")
    return ("semantic", grain.calendar.path, grain.level)


def decode_fold_authority(payload: str) -> FoldAuthorityV1:
    """Decode only the current exact schema; no source-era fallback is accepted."""
    if len(payload.encode("utf-8")) > 1_048_576:
        raise ValueError("retained fold authority byte bound exceeded")
    result = FoldAuthorityV1.model_validate_json(payload)
    result.time_grain()
    result.time_scope()
    result.temporal_snapshot()
    for metric in result.metrics:
        _validate_metric_authority(metric)
    if result.to_json() != payload:
        raise ValueError("noncanonical retained fold authority")
    return result


def _validate_metric_authority(metric: MetricFoldAuthorityV1) -> None:
    if metric.field_id != f"metric.{_canonical_digest(metric.metric_ref)[:32]}@v1":
        raise ValueError("mismatched retained Metric identity")
    if len(dict(metric.axis_partitions)) != len(metric.axis_partitions) or any(
        partition not in ("functional", "disjoint", "overlapping")
        for _, partition in metric.axis_partitions
    ):
        raise ValueError("invalid retained contribution partition")
    nodes = {node.node_id: node for node in metric.nodes}
    components = {component.node_id: component for component in metric.components}
    if len(nodes) != len(metric.nodes) or len(components) != len(metric.components):
        raise ValueError("duplicate retained fold node")
    if metric.root_id not in nodes:
        raise ValueError("missing retained fold root")
    states = {
        "row_count",
        "count",
        "sum",
        "non_null_count",
        "min",
        "max",
        "value",
        "weighted_numerator",
        "weight_sum",
        "non_null_pair_count",
    }
    for component in metric.components:
        prefix = f"__mv_{_canonical_digest((metric.metric_ref, component.node_id))[:20]}_"
        if len({state for state, _ in component.state_columns}) != len(component.state_columns):
            raise ValueError("duplicate retained state binding")
        if any(
            state not in states or name != prefix + state for state, name in component.state_columns
        ):
            raise ValueError("invalid retained component state binding")
        if not component.state_columns and (
            component.spatial_merge != "blocked" or component.time_merge != "blocked"
        ):
            raise ValueError("foldable component requires named retained state")
    visited: set[str] = set()
    active: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in active or node_id not in nodes:
            raise ValueError("invalid retained fold graph")
        if node_id in visited:
            return
        active.add(node_id)
        node = nodes[node_id]
        expected = 0 if node.kind == "component" else 2 if node.kind == "ratio" else 1
        if node.kind != "linear" and len(node.children) != expected:
            raise ValueError("invalid retained fold node arity")
        if node.kind == "linear" and (
            not node.children or len(node.children) != len(node.coefficients)
        ):
            raise ValueError("invalid retained linear composition")
        if node.kind == "component" and node.node_id not in components:
            raise ValueError("missing retained fold component")
        for child in node.children:
            visit(child)
        active.remove(node_id)
        visited.add(node_id)

    visit(metric.root_id)
    if visited != set(nodes) or set(components) != {
        key for key, node in nodes.items() if node.kind == "component"
    }:
        raise ValueError("non-closed retained fold dependency closure")


def fold_part_role(authority: MetricFoldAuthorityV1) -> str:
    return f"metric_components.{_canonical_digest(authority.metric_ref)[:20]}"


def coverage_columns(authority: MetricFoldAuthorityV1) -> tuple[str, ...]:
    if not authority.cumulative:
        return ()
    prefix = f"__mv_{_canonical_digest(authority.metric_ref)[:20]}"
    return tuple(
        f"{prefix}_{suffix}"
        for suffix in (
            "evaluation_end",
            "coverage_start",
            "coverage_end",
            "coverage_seconds",
            "coverage_complete",
        )
    )


def fold_state_names(authority: MetricFoldAuthorityV1) -> tuple[str, ...]:
    return (
        *tuple(
            dict.fromkeys(name for part in authority.components for _, name in part.state_columns)
        ),
        *coverage_columns(authority),
    )


def _metric_authority(
    metric: TargetMetricContract, definition: MetricDefinition
) -> MetricFoldAuthorityV1:
    components: list[FoldComponentV1] = []
    nodes: list[FoldNodeV1] = []
    by_id = {item.node_id: item.node for item in metric.graph.nodes}
    for component in {item.node_id: item for item in metric.components}.values():
        node = by_id[component.node_id]
        kind: Literal["sum", "count", "min", "max", "mean", "weighted_mean", "opaque"] = "opaque"
        if isinstance(node, WeightedMeanAggregateNodeV1):
            kind = "weighted_mean"
        elif isinstance(node, AggregateNodeV1) and node.agg in (
            "sum",
            "count",
            "min",
            "max",
            "mean",
        ):
            if node.agg == "sum":
                kind = "sum"
            elif node.agg == "count":
                kind = "count"
            elif node.agg == "min":
                kind = "min"
            elif node.agg == "max":
                kind = "max"
            else:
                kind = "mean"
        spatial: Merge = "min" if kind == "min" else "max" if kind == "max" else "sum"
        temporal: Merge = spatial
        cumulative = bool(metric.cumulative)
        if kind == "opaque" or component.requires_source_recompute:
            spatial = "blocked"
            temporal = "blocked"
        # Matching extrema commute exactly without endpoint alignment. Other
        # status folds need their separately retained evaluation-state protocol.
        if component.time_fold == kind and kind in ("min", "max"):
            spatial = kind
            temporal = kind
            kind = "opaque"
        if (
            isinstance(node, AggregateNodeV1)
            and node.agg == "count_distinct"
            and node.target_ref.kind is SemanticKind.ENTITY
            and node.target_ref.path == definition.entity.ref.path
            and component.computation_root.path == definition.entity.ref.path
        ):
            spatial = "sum"
        if component.time_fold in ("min", "max"):
            temporal = "min" if component.time_fold == "min" else "max"
            kind = "opaque"
        if cumulative:
            temporal = "last"
        digest = _canonical_digest((metric.ref.path, component.node_id))[:20]
        components.append(
            FoldComponentV1(
                node_id=component.node_id,
                kind=kind,
                state_columns=tuple(
                    (state, f"__mv_{digest}_{state}") for state in component.required_state
                )
                if metric.required_state or spatial != "blocked" or temporal != "blocked"
                else (),
                spatial_merge=spatial,
                time_merge=temporal,
                empty_rule=component.empty_rule,
                null_rule=component.null_rule,
                cumulative=cumulative,
            )
        )
    for node_id, node in by_id.items():
        if isinstance(node, (AggregateNodeV1, WeightedMeanAggregateNodeV1)):
            nodes.append(FoldNodeV1(node_id=node_id, kind="component"))
        elif isinstance(node, RatioNodeV1):
            nodes.append(
                FoldNodeV1(
                    node_id=node_id,
                    kind="ratio",
                    children=(node.numerator_id, node.denominator_id),
                    zero_division=node.zero_division,
                )
            )
        elif isinstance(node, LinearNodeV1):
            nodes.append(
                FoldNodeV1(
                    node_id=node_id,
                    kind="linear",
                    children=tuple(term.child_id for term in node.terms),
                    coefficients=tuple(float(term.coefficient) for term in node.terms),
                )
            )
        elif isinstance(node, (SliceNodeV1, CumulativeNodeV1)):
            nodes.append(FoldNodeV1(node_id=node_id, kind="identity", children=(node.child_id,)))
        else:
            # Opaque source leaf cannot accidentally become a foldable value.
            components.append(
                FoldComponentV1(
                    node_id=node_id,
                    kind="opaque",
                    state_columns=(),
                    spatial_merge="blocked",
                    time_merge="blocked",
                    empty_rule="null",
                    null_rule="ignore_null_inputs",
                    cumulative=False,
                )
            )
            nodes.append(FoldNodeV1(node_id=node_id, kind="component"))
    contract = next(
        item for item in definition.aggregation_contracts if item.metric_ref == metric.ref.path
    )
    return MetricFoldAuthorityV1(
        metric_ref=metric.ref.path,
        field_id=f"metric.{_canonical_digest(metric.ref.path)[:32]}@v1",
        root_id=metric.graph.roots[0],
        nodes=tuple(nodes),
        components=tuple(components),
        axis_partitions=tuple(sorted(contract.contribution_partition_by_reduced_axis)),
    )


def make_fold_authority(definition: MetricDefinition) -> str:
    return FoldAuthorityV1(
        schema_version=1,
        metrics=tuple(_metric_authority(metric, definition) for metric in definition.metrics),
        grain=grain_authority(definition.grain),
        scope=None
        if definition.time_scope is None
        else (definition.time_scope.start.isoformat(), definition.time_scope.end.isoformat()),
        calendar_json=None
        if definition.temporal_snapshot is None
        else json.dumps(
            _snapshot_json(definition.temporal_snapshot), sort_keys=True, separators=(",", ":")
        ),
    ).to_json()


@dataclass(frozen=True, slots=True)
class FoldSpecV1:
    axis: Literal["entity", "dimension", "time"]
    dropped_field_ids: tuple[DatasetFieldId, ...]
    grain: Grain | None
    drop_time: bool
    input_row: DatasetRowContract
    output_row: DatasetRowContract

    def identity_payload(self) -> CanonicalValue:
        return (
            self.axis,
            tuple(item.value for item in self.dropped_field_ids),
            grain_authority(self.grain),
            self.drop_time,
            _canonical_digest(_descriptor_payload(self.input_row)),
            _canonical_digest(_descriptor_payload(self.output_row)),
        )


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class RetainedFoldPayload(_LogicalNodePayload, _token=_CORE_TOKEN):
    spec: FoldSpecV1

    @property
    def identity_payload(self) -> CanonicalValue:
        return self.spec.identity_payload()


def fold_state_columns(authority: MetricFoldAuthorityV1) -> tuple[tuple[str, str, bool], ...]:
    """Return independent expected name, closed type class, and nullability facts."""
    result: dict[str, tuple[str, str, bool]] = {}
    for component in authority.components:
        for state, name in component.state_columns:
            counter = state.endswith("count")
            result[name] = (name, "integer" if counter else "numeric", not counter)
    for name in coverage_columns(authority):
        kind = (
            "boolean"
            if name.endswith("_complete")
            else "floating"
            if name.endswith("_seconds")
            else "timestamp"
        )
        result[name] = (name, kind, kind == "timestamp")
    return tuple(result.values())
