"""Closed correlation invocation and retained descriptive Association authority."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Literal

from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetFamilyRowSemantics,
    DatasetRowContract,
    DatasetRowSetContract,
    _descriptor_payload,
)
from marivo.analysis.datasets.handles import CanonicalValue, _LogicalNodePayload
from marivo.semantic._quantile import ApproximationClass, decode_approximation

CorrelationMethod = Literal["pearson", "spearman", "kendall"]
ASSOCIATION_SHAPES = ("entity", "dimension", "time-lag", "dimension-time-lag")
MAX_CANDIDATES = 4096
STATUSES = ("valid", "insufficient_pairs", "constant_a", "constant_b", "constant_both")
SELECTION_RULE_ID = "association.max_abs_coefficient_min_abs_lag_min_signed_lag@v1"
SELECTION_TERMS = (
    ("coefficient", "absolute", "descending"),
    ("lag_offset", "absolute", "ascending"),
    ("lag_offset", "signed", "ascending"),
)


def selection_key(coefficient: float, lag: int) -> tuple[float | int, ...]:
    values: dict[str, float | int] = {"coefficient": coefficient, "lag_offset": lag}
    return tuple(
        (-1 if direction == "descending" else 1)
        * (abs(values[name]) if transform == "absolute" else values[name])
        for name, transform, direction in SELECTION_TERMS
    )


def selection_description() -> str:
    return ", ".join(
        ("max " if direction == "descending" else "min ")
        + (f"abs({name})" if transform == "absolute" else name)
        for name, transform, direction in SELECTION_TERMS
    )


def pair_count(metric_count: int) -> int:
    return metric_count * (metric_count - 1) // 2


def candidate_count(metric_count: int, lag_count: int, series_count: int = 1) -> int:
    return pair_count(metric_count) * lag_count * series_count


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class AssociationSemantics(DatasetFamilyRowSemantics, _token=_CORE_TOKEN):
    method: CorrelationMethod
    input_shape: str
    metric_keys: tuple[str, ...]
    metric_units: tuple[str | None, ...]
    approximations: tuple[str, ...]
    lag_offsets: tuple[int, ...]
    fold_authority: str
    kind: Literal["association/metric@v1"] = field(default="association/metric@v1", init=False)


@dataclass(frozen=True, slots=True)
class PairApproximationBinding:
    metric_key_a: str
    metric_key_b: str
    approximation_a: ApproximationClass
    approximation_b: ApproximationClass


def pair_approximation_bindings(s: AssociationSemantics) -> tuple[PairApproximationBinding, ...]:
    return tuple(
        PairApproximationBinding(
            s.metric_keys[a],
            s.metric_keys[b],
            decode_approximation(s.approximations[a]),
            decode_approximation(s.approximations[b]),
        )
        for a, b in combinations(range(len(s.metric_keys)), 2)
    )


@dataclass(frozen=True, slots=True, repr=False)
class CorrelateSpecV1:
    input_row: DatasetRowContract
    input_rows: DatasetRowSetContract
    output_row: DatasetRowContract
    output_rows: DatasetRowSetContract

    @property
    def semantics(self) -> AssociationSemantics:
        from marivo.analysis.operators.errors import correlation_error

        value = self.output_row.family_semantics
        if not isinstance(value, AssociationSemantics):
            raise correlation_error("closed Association semantics", "invalid output family")
        return value

    @property
    def metric_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.input_row.schema.columns if f.role_id == "metric")

    @property
    def dimensions(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.output_row.schema.columns if f.role_id == "dimension")

    @property
    def time_name(self) -> str | None:
        return next(
            (f.name for f in self.input_row.schema.columns if f.role_id == "time_dimension"), None
        )

    def identity_payload(self) -> CanonicalValue:
        return (
            "correlate@v1",
            _descriptor_payload(self.input_row),
            _descriptor_payload(self.input_rows),
            _descriptor_payload(self.output_row),
            _descriptor_payload(self.output_rows),
        )


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class CorrelatePayload(_LogicalNodePayload, _token=_CORE_TOKEN):
    spec: CorrelateSpecV1

    @property
    def identity_payload(self) -> CanonicalValue:
        return self.spec.identity_payload()


@dataclass(frozen=True, slots=True)
class AssociationSearchSummary:
    """Original complete search facts retained across downstream selections."""

    series_count: int
    candidate_count: int
    complete_pair_range: tuple[int, int]
    null_pair_range: tuple[int, int]


COUNT_NAMES = (
    "input_observation_count",
    "matched_observation_count",
    "null_pair_count",
    "complete_pair_count",
)
PAIR_NAMES = ("metric_key_a", "metric_key_b", "lag_offset", *COUNT_NAMES, "value_a", "value_b")


METRIC_ORDER = "association.metric_request_order@v1"
LAG_ORDER = "association.lag_request_order@v1"


def association_orders(
    row: DatasetRowContract, rows: DatasetRowSetContract
) -> dict[str, tuple[str | int, ...]]:
    """Resolve authored order terms only from their bound Association semantics."""
    from marivo.analysis.datasets.descriptors import _OrderedOrdering

    meaning = row.family_semantics
    if not isinstance(meaning, AssociationSemantics) or not isinstance(
        rows.ordering, _OrderedOrdering
    ):
        return {}
    names = {f.field_id: f.name for f in row.schema.columns}
    return {
        names[t.field_id]: meaning.metric_keys
        if t.value_order_contract_id == METRIC_ORDER
        else meaning.lag_offsets
        for t in rows.ordering.terms
        if t.value_order_contract_id in (METRIC_ORDER, LAG_ORDER)
    }
