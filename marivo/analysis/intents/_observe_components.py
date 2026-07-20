"""Component-aware composition helpers for derived observe metrics.

Internal to ``marivo.analysis.intents`` — extracted from ``observe``.
"""

from __future__ import annotations

from typing import Any

from marivo.analysis.errors import MetricShapeUnsupportedError
from marivo.analysis.frames.component import (
    resolve_role_column_name,
    resolve_role_columns,
)

_COMPONENT_AWARE_COMPOSITIONS = {"ratio", "linear"}

#: Composition kinds that divide, mapped to the role holding the denominator.
_DIVISION_DENOMINATOR_ROLES = {"ratio": "denominator"}


def _divide_null_on_zero(numerator: Any, denominator: Any) -> Any:
    """Divide with ``zero_division="null"`` semantics.

    A present zero denominator yields null, never +/-inf; null operands stay
    null. Accepts pandas Series (the merged observe path) and ibis columns.
    """
    pandas = __import__("pandas")
    if isinstance(denominator, pandas.Series):
        return numerator / denominator.mask(denominator == 0)
    return numerator / denominator.nullif(0)


def _zero_denominator_row_count(metric_ir: Any, frame: Any) -> int | None:
    """Count merged rows whose present division denominator is zero.

    Returns None for compositions that do not divide (e.g. linear). ``frame``
    is the merged pandas component frame; absent (null) denominators are not
    counted — they are missing components, not zero denominators.
    """
    kind = getattr(getattr(metric_ir, "composition", None), "kind", None)
    role = _DIVISION_DENOMINATOR_ROLES.get(kind) if isinstance(kind, str) else None
    if role is None:
        return None
    denominator = frame[_role_to_column_name(metric_ir, role)]
    return int((denominator == 0).sum())


def _is_component_aware_composition(metric_ir: Any) -> bool:
    composition = getattr(metric_ir, "composition", None)
    kind = getattr(composition, "kind", None)
    return isinstance(kind, str) and kind in _COMPONENT_AWARE_COMPOSITIONS


def _composition_payload(metric_ir: Any) -> dict[str, Any] | None:
    if not _is_component_aware_composition(metric_ir):
        return None
    return {
        "kind": metric_ir.composition.kind,
        "components": dict(metric_ir.composition.components),
    }


def _role_to_column_name(metric_ir: Any, role: str) -> str:
    return resolve_role_column_name(metric_ir.composition.components, role)


def _component_parent_columns(metric_ir: Any) -> list[str]:
    return resolve_role_columns(metric_ir.composition.components)


def _component_frame_df(
    *,
    raw_df: Any,
    metric_ir: Any,
    axes_columns: list[str],
    metric_value_column: str,
) -> Any:
    role_columns = _component_parent_columns(metric_ir)
    for role, col in zip(metric_ir.composition.components, role_columns, strict=True):
        _require_component_role_column(metric_ir, role, col, raw_df)
    selected = [*axes_columns, *role_columns, metric_value_column]
    return raw_df[selected][[*axes_columns, *role_columns, metric_value_column]]


def _add_fold_metadata_to_component_df(
    df: Any,
    metric_ir: Any,
    component_plans: list[Any],
    merge_keys: list[str],
    metric_name: str,
) -> Any:
    """Transform wide-format component df to long format with fold metadata columns.

    Each component role becomes a separate row with component_metric_id, time_fold,
    and status_time_dimension columns.
    """
    pandas = __import__("pandas")
    role_columns = _component_parent_columns(metric_ir)
    # Build a mapping from role column name to component plan metadata
    role_to_meta: dict[str, dict[str, Any]] = {}
    for cp in component_plans:
        role = cp.role
        col_name = resolve_role_column_name(metric_ir.composition.components, role)
        role_to_meta[col_name] = {
            "component_metric_id": cp.component_metric_ir.semantic_id.rsplit(".", 1)[-1],
            "time_fold": (
                cp.component_metric_ir.time_fold.label()
                if getattr(cp.component_metric_ir, "time_fold", None)
                else None
            ),
            "status_time_dimension": getattr(cp.component_metric_ir, "status_time_dimension", None),
        }
    # Melt the wide-format df into long format
    long_frames: list[Any] = []
    for col_name in role_columns:
        meta = role_to_meta.get(
            col_name,
            {
                "component_metric_id": col_name,
                "time_fold": None,
                "status_time_dimension": None,
            },
        )
        subset = df[[*merge_keys, col_name, metric_name]].copy()
        subset = subset.rename(columns={col_name: "value"})
        subset["component_metric_id"] = meta["component_metric_id"]
        subset["time_fold"] = meta["time_fold"]
        subset["status_time_dimension"] = meta["status_time_dimension"]
        long_frames.append(subset)
    result = pandas.concat(long_frames, ignore_index=True)
    if merge_keys:
        result = result.sort_values([*merge_keys, "component_metric_id"]).reset_index(drop=True)
    return result


def _evaluate_composition_on_frame(metric_ir: Any, frame: Any) -> Any:
    kind = metric_ir.composition.kind
    if kind == "ratio":
        num_col = _role_to_column_name(metric_ir, "numerator")
        den_col = _role_to_column_name(metric_ir, "denominator")
        return _divide_null_on_zero(frame[num_col], frame[den_col])
    if kind == "linear":
        terms = metric_ir.composition.components
        acc = None
        for i, role in enumerate(sorted(terms, key=lambda r: int(r.removeprefix("term")))):
            col = frame[_role_to_column_name(metric_ir, role)]
            # Determine sign from linear_terms: sign is the first element of each tuple
            sign_str = metric_ir.linear_terms[i][0] if metric_ir.linear_terms else "+"
            signed = col if sign_str == "+" else -col
            acc = signed if acc is None else acc + signed
        return acc
    raise MetricShapeUnsupportedError(
        message=f"unsupported derived metric composition kind {kind!r}",
        context={
            "kind": "DerivedMetricCompositionUnsupported",
            "metric": metric_ir.semantic_id,
            "composition_kind": kind,
        },
    )


def _require_component_role_column(
    metric_ir: Any,
    role: str,
    column_name: str,
    frame: Any,
) -> Any:
    component_id = metric_ir.composition.components.get(role)
    if component_id is None:
        raise MetricShapeUnsupportedError(
            message=f"derived metric {metric_ir.semantic_id!r} is missing component role {role!r}",
            context={
                "kind": "DerivedMetricComponentMissing",
                "metric": metric_ir.semantic_id,
                "role": role,
            },
        )
    if column_name not in frame:
        raise MetricShapeUnsupportedError(
            message=(
                f"derived metric {metric_ir.semantic_id!r} component role column "
                f"{column_name!r} (role {role!r}) is missing"
            ),
            context={
                "kind": "DerivedMetricComponentColumnMissing",
                "metric": metric_ir.semantic_id,
                "role": role,
                "component_metric": component_id,
                "column": column_name,
            },
        )
    return frame[column_name]
