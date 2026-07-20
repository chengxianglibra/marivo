"""Registered bottom-up evaluators for metric-expression graph nodes.

Physical leaf execution returns one typed-key frame per graph node.  This
module owns composition semantics after that boundary so catalog and runtime
expressions cannot drift by using different pandas/SQL implementations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class RoleQualityV1:
    """Presence facts for one child role after typed-key union alignment."""

    absent_rows: int = 0
    present_null_rows: int = 0
    present_zero_rows: int = 0


@dataclass(frozen=True)
class MetricEvaluationQualityV1:
    """Stable quality facts emitted by one registered evaluator."""

    roles: dict[str, RoleQualityV1] = field(default_factory=dict)
    affected_result_rows: int = 0
    zero_division_rows: int = 0


@dataclass(frozen=True)
class MetricEvaluationV1:
    """A node value frame plus evaluator-owned quality facts."""

    frame: Any
    key_columns: tuple[str, ...]
    quality: MetricEvaluationQualityV1


class MetricEvaluationError(ValueError):
    """Raised when child key schemas or registered policies are invalid."""


def _require_value_frame(frame: Any, *, role: str) -> None:
    columns = tuple(getattr(frame, "columns", ()))
    if "value" not in columns:
        raise MetricEvaluationError(f"metric child role {role!r} has no 'value' column")


def _presence_column(role: str) -> str:
    return f"__marivo_present_{role}"


def _value_column(role: str) -> str:
    return f"__marivo_value_{role}"


def _normalize_child(frame: Any, *, role: str, key_columns: tuple[str, ...]) -> Any:
    _require_value_frame(frame, role=role)
    actual_keys = tuple(column for column in frame.columns if column != "value")
    if actual_keys != key_columns:
        raise MetricEvaluationError(
            f"metric child role {role!r} has key schema {actual_keys!r}; expected {key_columns!r}"
        )
    normalized = frame.copy()
    normalized[_presence_column(role)] = True
    return normalized.rename(columns={"value": _value_column(role)})


def _stable_sort(frame: Any, key_columns: tuple[str, ...]) -> Any:
    if not key_columns or frame.empty:
        return frame.reset_index(drop=True)
    try:
        return frame.sort_values(list(key_columns), kind="mergesort").reset_index(drop=True)
    except TypeError:
        # Exact typed schemas normally sort directly.  Keep deterministic
        # behavior for object-backed extension values without coercing the
        # persisted key columns themselves.
        ordered = frame.copy()
        helper_columns: list[str] = []
        for index, column in enumerate(key_columns):
            helper = f"__marivo_sort_{index}"
            helper_columns.append(helper)
            ordered[helper] = ordered[column].map(
                lambda value: (type(value).__qualname__, repr(value))
            )
        return (
            ordered.sort_values(helper_columns, kind="mergesort")
            .drop(columns=helper_columns)
            .reset_index(drop=True)
        )


def align_metric_children_v1(
    children: tuple[tuple[str, Any], ...],
) -> tuple[Any, tuple[str, ...], dict[str, RoleQualityV1]]:
    """Outer-align child frames while preserving absent versus present-null.

    The first child defines the exact typed-key schema.  All remaining
    children must use the same ordered key columns.  Scalar children have an
    empty key tuple and are joined by their single row position.
    """

    if not children:
        raise MetricEvaluationError("metric evaluation requires at least one child")
    first_role, first_frame = children[0]
    _require_value_frame(first_frame, role=first_role)
    key_columns = tuple(column for column in first_frame.columns if column != "value")
    normalized = [
        _normalize_child(frame, role=role, key_columns=key_columns) for role, frame in children
    ]
    pandas = __import__("pandas")
    if key_columns:
        merged = normalized[0]
        for child in normalized[1:]:
            merged = pandas.merge(
                merged,
                child,
                on=list(key_columns),
                how="outer",
                sort=False,
                validate="one_to_one",
            )
    else:
        for (role, frame), child in zip(children, normalized, strict=True):
            if len(frame) != 1:
                raise MetricEvaluationError(
                    f"scalar metric child role {role!r} must contain exactly one row"
                )
            child.index = [0]
        merged = pandas.concat(normalized, axis=1)

    quality: dict[str, RoleQualityV1] = {}
    for role, _frame in children:
        present = merged[_presence_column(role)].fillna(False).astype(bool)
        value = merged[_value_column(role)]
        quality[role] = RoleQualityV1(
            absent_rows=int((~present).sum()),
            present_null_rows=int((present & value.isna()).sum()),
            present_zero_rows=int((present & value.eq(0).fillna(False)).sum()),
        )
    return _stable_sort(merged, key_columns), key_columns, quality


@dataclass(frozen=True)
class AggregateEvaluationV1:
    """Registered leaf-result adapter shared by catalog/runtime aggregates."""

    version: Literal["aggregate-evaluation/v1"] = "aggregate-evaluation/v1"

    def evaluate(self, frame: Any) -> MetricEvaluationV1:
        _require_value_frame(frame, role="aggregate")
        key_columns = tuple(column for column in frame.columns if column != "value")
        return MetricEvaluationV1(
            frame=_stable_sort(frame.copy(), key_columns),
            key_columns=key_columns,
            quality=MetricEvaluationQualityV1(),
        )


@dataclass(frozen=True)
class RatioEvaluationV1:
    """Registered E0 ratio evaluator used at every nesting depth."""

    version: Literal["ratio-evaluation/v1"] = "ratio-evaluation/v1"

    def evaluate(
        self,
        numerator: Any,
        denominator: Any,
        *,
        zero_division: Literal["null", "error"],
    ) -> MetricEvaluationV1:
        merged, key_columns, roles = align_metric_children_v1(
            (("numerator", numerator), ("denominator", denominator))
        )
        numerator_present = merged[_presence_column("numerator")].fillna(False).astype(bool)
        denominator_present = merged[_presence_column("denominator")].fillna(False).astype(bool)
        numerator_value = merged[_value_column("numerator")]
        denominator_value = merged[_value_column("denominator")]
        zero_denominator = denominator_present & denominator_value.eq(0).fillna(False)
        if zero_division == "error" and bool(zero_denominator.any()):
            raise ZeroDivisionError(
                f"ratio denominator is zero for {int(zero_denominator.sum())} aligned row(s)"
            )
        result = numerator_value / denominator_value.mask(zero_denominator)
        missing_child = ~numerator_present | ~denominator_present
        result = result.mask(missing_child)
        output = merged[list(key_columns)].copy() if key_columns else merged.iloc[:, 0:0].copy()
        output["value"] = result
        affected = missing_child | numerator_value.isna() | denominator_value.isna()
        if zero_division == "null":
            affected = affected | zero_denominator
        return MetricEvaluationV1(
            frame=_stable_sort(output, key_columns),
            key_columns=key_columns,
            quality=MetricEvaluationQualityV1(
                roles=roles,
                affected_result_rows=int(affected.sum()),
                zero_division_rows=int(zero_denominator.sum()),
            ),
        )


def evaluate_linear_v1(terms: tuple[tuple[str, float, Any], ...]) -> MetricEvaluationV1:
    """Evaluate a linear node over the typed-key union of its ordered terms."""

    if not terms:
        raise MetricEvaluationError("linear evaluation requires at least one term")
    aligned, key_columns, roles = align_metric_children_v1(
        tuple((role, frame) for role, _coefficient, frame in terms)
    )
    value = None
    all_present = None
    any_null = None
    for role, coefficient, _frame in terms:
        present = aligned[_presence_column(role)].fillna(False).astype(bool)
        child = aligned[_value_column(role)]
        signed = child * coefficient
        value = signed if value is None else value + signed
        all_present = present if all_present is None else all_present & present
        child_null = child.isna()
        any_null = child_null if any_null is None else any_null | child_null
    assert value is not None and all_present is not None and any_null is not None
    affected = ~all_present | any_null
    value = value.mask(affected)
    output = aligned[list(key_columns)].copy() if key_columns else aligned.iloc[:, 0:0].copy()
    output["value"] = value
    return MetricEvaluationV1(
        frame=_stable_sort(output, key_columns),
        key_columns=key_columns,
        quality=MetricEvaluationQualityV1(
            roles=roles,
            affected_result_rows=int(affected.sum()),
        ),
    )


__all__ = [
    "AggregateEvaluationV1",
    "MetricEvaluationError",
    "MetricEvaluationQualityV1",
    "MetricEvaluationV1",
    "RatioEvaluationV1",
    "RoleQualityV1",
    "align_metric_children_v1",
    "evaluate_linear_v1",
]
