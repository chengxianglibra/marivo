"""Coverage and fold metadata helpers used by graph-native observe."""

from __future__ import annotations

from typing import Any

from marivo.analysis.intents._observe_base import _resolve_fold_time_field
from marivo.analysis.intents.sampled_fold import sample_interval_token


def _merge_component_coverages(
    component_coverages: list[Any],
    merge_keys: list[str],
) -> Any:
    """Merge recursive child coverage using the least-covered child."""

    pandas = __import__("pandas")
    if not component_coverages:
        return None
    window_columns = ["expected_span", "covered_span"]
    sample_columns = ["actual_samples", "expected_samples"]
    window_coverages = [
        frame for frame in component_coverages if set(window_columns).issubset(frame.columns)
    ]
    sample_coverages = [
        frame for frame in component_coverages if set(sample_columns).issubset(frame.columns)
    ]
    selected = sample_coverages or window_coverages
    if not selected:
        return None
    fields = (
        [*sample_columns, "coverage_ratio", "coverage_status"]
        if sample_coverages
        else [*window_columns, "coverage_ratio", "coverage_status"]
    )
    normalized = [frame[[*merge_keys, *fields]] for frame in selected]
    combined = pandas.concat(normalized, ignore_index=True)
    aggregations = {
        fields[0]: "min" if sample_coverages else "max",
        fields[1]: "max" if sample_coverages else "min",
        "coverage_ratio": "min",
    }
    if merge_keys:
        merged = combined.groupby(merge_keys, dropna=False, as_index=False).agg(aggregations)
    else:
        merged = pandas.DataFrame(
            {
                column: [getattr(combined[column], operation)()]
                for column, operation in aggregations.items()
            }
        )
    merged["coverage_status"] = (
        merged["coverage_ratio"].eq(1.0).map({True: "complete", False: "partial"})
    )
    return merged


def _build_fold_meta(metric_ir: Any, catalog: Any) -> dict[str, Any]:
    """Build fold metadata for one physical aggregate leaf."""

    sample_interval_token_value: str | None = None
    if metric_ir.status_time_dimension is not None:
        time_field = _resolve_fold_time_field(catalog, metric_ir.status_time_dimension)
        sample_interval = getattr(time_field, "sample_interval", None)
        if sample_interval is not None:
            sample_interval_token_value = sample_interval_token(sample_interval)
    return {
        "time_fold": metric_ir.time_fold.label(),
        "fold_kind": getattr(metric_ir.time_fold, "kind", None),
        "status_time_dimension": metric_ir.status_time_dimension,
        "sample_interval": sample_interval_token_value,
    }


__all__ = ["_build_fold_meta", "_merge_component_coverages"]
