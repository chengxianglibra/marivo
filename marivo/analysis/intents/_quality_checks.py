"""Pure pandas quality checks for analysis frames."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
from numbers import Real
from pathlib import Path
from typing import Any, Literal, cast

import pandas as pd

from marivo.analysis.frames._attribution_columns import (
    ATTRIBUTION_AXIS_COLUMN,
    ATTRIBUTION_DRIVER_COLUMN,
    ATTRIBUTION_LEVEL_COLUMN,
    ATTRIBUTION_PATH_COLUMN,
)
from marivo.analysis.frames._content_hash import (
    compute_file_content_hash,
    compute_frame_content_hash,
)
from marivo.analysis.frames._meta_defaults import GRAIN_FREQ, normalize_coverage_buckets
from marivo.analysis.frames.attribution import (
    FUNNEL_ATTRIBUTION_COLUMNS,
    AttributionFrame,
    FunnelAttributionFrameMeta,
)
from marivo.analysis.frames.delta import (
    FUNNEL_DELTA_COLUMNS,
    DeltaFrame,
    FunnelDeltaFrameMeta,
)
from marivo.analysis.frames.event import (
    EventFrame,
    EventFunnelFrameMeta,
    EventInputCoverage,
    EventTimeToEventFrameMeta,
)
from marivo.analysis.frames.lifecycle import (
    LIFECYCLE_DISTRIBUTION_VALUE_COLUMNS,
    LIFECYCLE_DWELL_COLUMNS,
    LIFECYCLE_HISTORY_COLUMNS,
    LIFECYCLE_TRANSITIONS_COLUMNS,
    LIFECYCLE_VIOLATIONS_COLUMNS,
    LifecycleDistributionFrameMeta,
    LifecycleDwellFrameMeta,
    LifecycleFrame,
    LifecycleHistoryFrameMeta,
    LifecycleTransitionsFrameMeta,
    LifecycleViolationsFrameMeta,
)
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents._event_funnel import (
    FUNNEL_ADDITIVE_COLUMNS,
    funnel_reconciliation_hash,
)
from marivo.analysis.intents._event_occurrences import stable_digest
from marivo.analysis.intents._lifecycle_dwell import reduce_lifecycle_dwell
from marivo.analysis.intents._lifecycle_transitions import (
    reduce_lifecycle_transitions,
)
from marivo.analysis.intents._lifecycle_violations import (
    reduce_lifecycle_violations,
)
from marivo.analysis.intents._metric_axes import (
    metric_dimension_columns,
    metric_time_axis,
)

_FREQ = GRAIN_FREQ


def run_metric_checks(frame: MetricFrame, *, tz: str | None = None) -> list[dict[str, str]]:
    df = frame._dataframe_copy()
    rows = [_row_count_check(df, semantic_kind=frame.meta.semantic_kind)]
    rows.extend(_null_ratio_checks(df, frame))
    if frame.meta.semantic_kind in {"time_series", "panel"}:
        rows.append(_time_coverage_check(df, frame, tz=tz))
    if frame.meta.semantic_kind in {"segmented", "panel"}:
        rows.append(_duplicate_keys_check(df, frame))
    return rows


def run_delta_checks(frame: DeltaFrame) -> list[dict[str, str]]:
    """Return deterministic row-contract and cumulative-pairing checks."""

    if isinstance(frame.meta, FunnelDeltaFrameMeta):
        raise ValueError("metric delta quality does not accept DeltaFrame[funnel]")
    df = frame._dataframe_copy()
    rows = [_row_count_check(df, semantic_kind=frame.meta.semantic_kind)]
    required_columns = {
        "current",
        "baseline",
        "delta",
        "pct_change",
        "pct_change_status",
    }
    missing_columns = sorted(required_columns - set(df.columns))
    invalid_count = len(missing_columns) + int(len(df) != frame.meta.row_count)
    severity = "blocking" if invalid_count else "ok"
    rows.append(
        _result(
            "delta_row_contract",
            "delta_row_contract",
            severity,
            severity,
            (
                "Delta rows match the persisted result contract"
                if not invalid_count
                else f"Delta rows have {invalid_count} contract violation(s)"
            ),
            {
                "invalid_count": invalid_count,
                "missing_columns": missing_columns,
                "row_count": len(df),
                "metadata_row_count": frame.meta.row_count,
            },
        )
    )
    alignment = frame.meta.cumulative_alignment
    if alignment is not None:
        pairs = alignment.pairs
        caveat_count = (
            pairs.matched_null_rows
            + pairs.current_unpaired_rows
            + pairs.baseline_unpaired_rows
            + pairs.fallback_rows
        )
        severity = "warning" if caveat_count else "ok"
        rows.append(
            _result(
                "cumulative_pairing",
                "cumulative_pairing",
                severity,
                severity,
                (
                    "Cumulative pairing contains no null, unpaired, or fallback caveats"
                    if not caveat_count
                    else "Cumulative pairing retains explicit alignment caveats"
                ),
                {
                    "caveat_count": caveat_count,
                    "matched_rows": pairs.matched_rows,
                    "matched_null_rows": pairs.matched_null_rows,
                    "current_unpaired_rows": pairs.current_unpaired_rows,
                    "baseline_unpaired_rows": pairs.baseline_unpaired_rows,
                    "fallback_rows": pairs.fallback_rows,
                    "unpaired_action": pairs.unpaired_action,
                },
            )
        )
    return rows


def run_event_journey_checks(frame: EventFrame) -> list[dict[str, str]]:
    """Return deterministic quality predicates for a journey-shaped EventFrame."""
    df = frame._dataframe_copy()
    rows = [
        _event_row_contract_check(df, frame),
        _event_identity_check(df),
        _event_participant_check(df, frame),
        _event_ordering_check(df, frame),
    ]
    rows.extend(_event_coverage_checks(frame))
    rows.append(_event_declaration_check(frame))
    rows.append(_event_censoring_check(df))
    return rows


def run_event_funnel_checks(frame: EventFrame) -> list[dict[str, str]]:
    """Return deterministic quality predicates for a funnel-shaped EventFrame."""
    df = frame._dataframe_copy()
    rows = [
        _event_funnel_row_contract_check(df, frame),
        _event_funnel_math_check(df, frame),
        _event_funnel_axis_check(df, frame),
        _event_funnel_reconciliation_check(frame),
        _event_funnel_censoring_check(df),
    ]
    rows.extend(_event_coverage_checks(frame))
    rows.append(_event_declaration_check(frame))
    return rows


def run_event_time_to_event_checks(frame: EventFrame) -> list[dict[str, str]]:
    """Return deterministic checks for a time-to-event EventFrame."""
    df = frame._dataframe_copy()
    rows = [
        _event_time_to_event_contract_check(df, frame),
        _event_time_to_event_identity_check(df),
        _event_time_to_event_duration_check(df),
    ]
    rows.extend(_event_coverage_checks(frame))
    rows.append(_event_declaration_check(frame))
    rows.append(_event_censoring_check(df))
    return rows


def run_event_checks(frame: EventFrame) -> list[dict[str, str]]:
    """Dispatch Event quality by the frame's closed semantic shape."""
    if frame.meta.semantic_kind == "journey":
        return run_event_journey_checks(frame)
    if frame.meta.semantic_kind == "funnel":
        return run_event_funnel_checks(frame)
    if frame.meta.semantic_kind == "time_to_event":
        return run_event_time_to_event_checks(frame)
    raise ValueError(f"unsupported EventFrame shape {frame.meta.semantic_kind!r}")


def run_funnel_delta_checks(frame: DeltaFrame) -> list[dict[str, str]]:
    """Return deterministic checks for exact funnel comparison artifacts."""
    if not isinstance(frame.meta, FunnelDeltaFrameMeta):
        raise ValueError("funnel delta quality requires DeltaFrame[funnel]")
    df = frame._dataframe_copy()
    axes = tuple(axis.output_column for axis in frame.meta.axes)
    expected = (
        *axes,
        *FUNNEL_DELTA_COLUMNS,
    )
    missing = tuple(column for column in expected if column not in df.columns)
    extra = tuple(column for column in df.columns if column not in expected)
    order_mismatch = tuple(df.columns) != expected
    row_invalid = len(missing) + len(extra) + int(order_mismatch) + int(df.empty)
    additive = [
        column
        for column in expected
        if (column.startswith("current_") or column.startswith("baseline_"))
        and column.endswith("_count")
    ]
    present_additive = [column for column in additive if column in df.columns]
    numeric_invalid = 0
    for column in present_additive:
        values = pd.to_numeric(df[column], errors="coerce")
        numeric_invalid += int((values.isna() | values.lt(0) | values.mod(1).ne(0)).sum())
    component_invalid = len(additive) - len(present_additive) + numeric_invalid

    retained_steps = tuple(step.key for step in frame.meta.pattern.steps)
    alignment_invalid = int(tuple(frame.meta.aligned_step_keys) != retained_steps)
    duplicate_count = 0
    unknown_step_count = 0
    incomplete_tuple_count = 0
    observed_zero_filled_tuple_count: int | None = None
    alignment_keys = [*axes, "step_key"]
    if all(column in df.columns for column in alignment_keys):
        duplicate_count = int(df.duplicated(subset=alignment_keys, keep=False).sum())
        unknown_step_count = int((~df["step_key"].astype(str).isin(retained_steps)).sum())
        if axes:
            groups = tuple(
                group
                for _, group in df.groupby(
                    list(axes),
                    dropna=False,
                    sort=False,
                )
            )
        else:
            groups = (df,)
        incomplete_tuple_count = sum(
            set(group["step_key"].astype(str)) != set(retained_steps) for group in groups
        )
        if {
            "current_cohort_count",
            "baseline_cohort_count",
        }.issubset(df.columns):
            observed_zero_filled_tuple_count = sum(
                bool(
                    pd.to_numeric(group["current_cohort_count"], errors="coerce")
                    .fillna(0)
                    .eq(0)
                    .all()
                    or pd.to_numeric(group["baseline_cohort_count"], errors="coerce")
                    .fillna(0)
                    .eq(0)
                    .all()
                )
                for group in groups
            )
            alignment_invalid += int(
                observed_zero_filled_tuple_count != frame.meta.zero_filled_tuple_count
            )
    else:
        alignment_invalid += len([column for column in alignment_keys if column not in df.columns])
    alignment_invalid += duplicate_count + unknown_step_count + incomplete_tuple_count

    rate_invalid = 0
    first_step = retained_steps[0]
    for side in ("current", "baseline"):
        lost_column = f"{side}_lost_count"
        denominator_column = f"{side}_resolved_entry_count"
        rate_column = f"{side}_loss_rate_from_previous"
        if not {lost_column, denominator_column, rate_column}.issubset(df.columns):
            continue
        lost = pd.to_numeric(df[lost_column], errors="coerce")
        denominator = pd.to_numeric(df[denominator_column], errors="coerce")
        actual = pd.to_numeric(df[rate_column], errors="coerce")
        expected_rate = lost.div(denominator.where(denominator.ne(0)))
        if "step_key" in df.columns:
            expected_rate = expected_rate.mask(df["step_key"].astype(str).eq(first_step))
        rate_invalid += int(
            (
                ~(
                    (actual.isna() & expected_rate.isna())
                    | (actual.sub(expected_rate).abs() <= 1e-12)
                )
            ).sum()
        )
    component_invalid += rate_invalid
    coverage_invalid = int(
        frame.meta.current_coverage_basis == "unknown"
        or frame.meta.baseline_coverage_basis == "unknown"
    )
    declarations = (*frame.meta.current_completeness, *frame.meta.baseline_completeness)
    declaration_event_refs = sorted({ref.path for item in declarations for ref in item.inputs})
    rows = [
        _result(
            "funnel_delta_alignment",
            "funnel_delta_alignment",
            "blocking" if alignment_invalid else "ok",
            "blocking" if alignment_invalid else "ok",
            "funnel delta alignment is valid" if not alignment_invalid else "invalid alignment",
            {
                "invalid_count": alignment_invalid,
                "duplicate_count": duplicate_count,
                "unknown_step_count": unknown_step_count,
                "incomplete_tuple_count": incomplete_tuple_count,
                "expected_zero_filled_tuple_count": frame.meta.zero_filled_tuple_count,
                "observed_zero_filled_tuple_count": observed_zero_filled_tuple_count,
            },
        ),
        _result(
            "funnel_delta_components",
            "funnel_delta_components",
            "blocking" if component_invalid else "ok",
            "blocking" if component_invalid else "ok",
            "funnel delta components are valid" if not component_invalid else "invalid components",
            {
                "invalid_count": component_invalid,
                "missing_columns": [column for column in additive if column not in df.columns],
                "invalid_rate_count": rate_invalid,
            },
        ),
        _result(
            "funnel_delta_coverage",
            "funnel_delta_coverage",
            "blocking" if coverage_invalid else "ok",
            "blocking" if coverage_invalid else "ok",
            "funnel delta coverage is disclosed",
            {
                "invalid_count": coverage_invalid,
                "current_basis": frame.meta.current_coverage_basis,
                "baseline_basis": frame.meta.baseline_coverage_basis,
            },
        ),
        _result(
            "funnel_delta_row_contract",
            "funnel_delta_row_contract",
            "blocking" if row_invalid else "ok",
            "blocking" if row_invalid else "ok",
            "funnel delta row contract is valid" if not row_invalid else "invalid row contract",
            {
                "invalid_count": row_invalid,
                "missing_columns": missing,
                "extra_columns": extra,
                "column_order_mismatch": order_mismatch,
            },
        ),
    ]
    rows.append(
        _result(
            "declared_completeness_used",
            "declared_completeness_used",
            "warning" if declarations else "ok",
            "warning" if declarations else "ok",
            (
                f"{len(declarations)} funnel comparison completeness declaration(s) retained"
                if declarations
                else "no caller completeness declaration was used"
            ),
            {
                "declared_input_count": len(declaration_event_refs),
                "event_refs": declaration_event_refs,
            },
        )
    )
    return rows


def run_funnel_attribution_checks(frame: AttributionFrame) -> list[dict[str, str]]:
    """Return deterministic checks for ratio-mix funnel attribution."""
    if not isinstance(frame.meta, FunnelAttributionFrameMeta):
        raise ValueError("funnel attribution quality requires AttributionFrame[funnel_loss_rate]")
    df = frame._dataframe_copy()
    missing = [column for column in FUNNEL_ATTRIBUTION_COLUMNS if column not in df.columns]
    components_invalid = len(missing)
    kinds = set(df["contribution_kind"].astype(str)) if "contribution_kind" in df.columns else set()
    components_invalid += int(kinds != {"loss", "denominator_mix"})
    contributions = (
        pd.to_numeric(df["contribution"], errors="coerce")
        if "contribution" in df.columns
        else pd.Series(dtype="float64")
    )
    components_invalid += int(contributions.isna().sum())
    if frame.meta.mode == "hierarchy":
        layout_columns = {
            ATTRIBUTION_LEVEL_COLUMN,
            ATTRIBUTION_AXIS_COLUMN,
            ATTRIBUTION_DRIVER_COLUMN,
            ATTRIBUTION_PATH_COLUMN,
        }
        components_invalid += len(layout_columns - set(df.columns))
        if ATTRIBUTION_LEVEL_COLUMN in df.columns and not df.empty:
            levels = pd.to_numeric(df[ATTRIBUTION_LEVEL_COLUMN], errors="coerce")
            components_invalid += int(levels.isna().sum())
            deepest = df.loc[levels == levels.max()].copy()
        else:
            deepest = df.iloc[0:0].copy()
        coordinate_columns = [
            ATTRIBUTION_LEVEL_COLUMN,
            ATTRIBUTION_PATH_COLUMN,
            "contribution_kind",
        ]
    else:
        axis_columns = [axis.output_column for axis in frame.meta.axes]
        components_invalid += len([column for column in axis_columns if column not in df.columns])
        deepest = df
        coordinate_columns = [*axis_columns, "contribution_kind"]
    present_coordinates = [column for column in coordinate_columns if column in df.columns]
    if len(present_coordinates) == len(coordinate_columns):
        components_invalid += int(df.duplicated(subset=present_coordinates, keep=False).sum())
        group_columns = coordinate_columns[:-1]
        components_invalid += sum(
            set(group["contribution_kind"].astype(str)) != {"loss", "denominator_mix"}
            for _, group in df.groupby(
                group_columns,
                dropna=False,
                sort=False,
            )
        )

    deepest_values = (
        pd.to_numeric(deepest["contribution"], errors="coerce")
        if "contribution" in deepest.columns
        else pd.Series(dtype="float64")
    )
    contribution_sum = float(deepest_values.sum()) if not deepest_values.isna().any() else None
    positive_pool = (
        float(deepest_values.loc[deepest_values > 0].sum())
        if contribution_sum is not None
        else None
    )
    negative_pool = (
        float(deepest_values.loc[deepest_values < 0].sum())
        if contribution_sum is not None
        else None
    )
    receipt = frame.meta.reconciliation
    pools_invalid = int(receipt.positive_pool < 0 or receipt.negative_pool > 0)
    if positive_pool is None or negative_pool is None:
        pools_invalid += 1
    else:
        pools_invalid += int(abs(positive_pool - receipt.positive_pool) > receipt.tolerance)
        pools_invalid += int(abs(negative_pool - receipt.negative_pool) > receipt.tolerance)
    derived_residual = (
        receipt.target_loss_rate_delta - contribution_sum
        if receipt.target_loss_rate_delta is not None and contribution_sum is not None
        else None
    )
    residual_invalid = int(
        derived_residual is None
        or abs(derived_residual) > receipt.tolerance
        or abs((derived_residual or 0.0) - receipt.residual) > receipt.tolerance
    )
    reconciliation_invalid = int(receipt.status != "reconciled")
    if contribution_sum is None or receipt.contribution_sum is None:
        reconciliation_invalid += 1
    else:
        reconciliation_invalid += int(
            abs(contribution_sum - receipt.contribution_sum) > receipt.tolerance
        )
    reconciliation_invalid += residual_invalid
    return [
        _result(
            "funnel_attribution_components",
            "funnel_attribution_components",
            "blocking" if components_invalid else "ok",
            "blocking" if components_invalid else "ok",
            "funnel attribution components are valid",
            {"invalid_count": components_invalid, "missing_columns": missing},
        ),
        _result(
            "funnel_attribution_pools",
            "funnel_attribution_pools",
            "blocking" if pools_invalid else "ok",
            "blocking" if pools_invalid else "ok",
            "funnel attribution pools are valid",
            {
                "invalid_count": pools_invalid,
                "row_positive_pool": positive_pool,
                "row_negative_pool": negative_pool,
                "receipt_positive_pool": receipt.positive_pool,
                "receipt_negative_pool": receipt.negative_pool,
            },
        ),
        _result(
            "funnel_attribution_residual",
            "funnel_attribution_residual",
            "blocking" if residual_invalid else "ok",
            "blocking" if residual_invalid else "ok",
            "funnel attribution residual is bounded",
            {
                "invalid_count": residual_invalid,
                "row_residual": derived_residual,
                "receipt_residual": receipt.residual,
            },
        ),
        _result(
            "funnel_attribution_reconciliation",
            "funnel_attribution_reconciliation",
            "blocking" if reconciliation_invalid else "ok",
            "blocking" if reconciliation_invalid else "ok",
            "funnel attribution reconciles exactly",
            {
                "invalid_count": reconciliation_invalid,
                "row_contribution_sum": contribution_sum,
                "receipt_contribution_sum": receipt.contribution_sum,
                "target_loss_rate_delta": receipt.target_loss_rate_delta,
            },
        ),
    ]


def run_lifecycle_history_checks(frame: LifecycleFrame) -> list[dict[str, str]]:
    """Return deterministic row-, trace-, coverage-, and model-quality checks."""
    if not isinstance(frame.meta, LifecycleHistoryFrameMeta):
        raise ValueError("Lifecycle history quality requires LifecycleFrame[history]")
    df = frame._dataframe_copy()
    return [
        _lifecycle_history_row_contract_check(df, frame),
        _lifecycle_history_state_check(df, frame),
        _lifecycle_history_interval_check(df, frame),
        _lifecycle_history_count_check(df, frame),
        _lifecycle_trace_check(frame),
        *_lifecycle_coverage_checks(frame),
        _lifecycle_declaration_check(frame),
        _lifecycle_censoring_check(df, frame),
    ]


def run_lifecycle_distribution_checks(frame: LifecycleFrame) -> list[dict[str, str]]:
    """Return deterministic checks for Lifecycle state distribution."""
    if not isinstance(frame.meta, LifecycleDistributionFrameMeta):
        raise ValueError("Lifecycle distribution quality requires LifecycleFrame[distribution]")
    df = frame._dataframe_copy()
    return [
        _lifecycle_distribution_row_contract_check(df, frame),
        _lifecycle_distribution_math_check(df, frame),
        _lifecycle_distribution_reconciliation_check(df, frame),
        _lifecycle_source_history_check(frame),
    ]


def run_lifecycle_transitions_checks(frame: LifecycleFrame) -> list[dict[str, str]]:
    """Return deterministic checks for Lifecycle transition reduction."""
    if not isinstance(frame.meta, LifecycleTransitionsFrameMeta):
        raise ValueError("Lifecycle transitions quality requires LifecycleFrame[transitions]")
    df = frame._dataframe_copy()
    return [
        _lifecycle_transitions_row_contract_check(df, frame),
        _lifecycle_transitions_math_check(df, frame),
        _lifecycle_source_history_check(frame),
    ]


def run_lifecycle_dwell_checks(frame: LifecycleFrame) -> list[dict[str, str]]:
    """Return deterministic checks for Lifecycle dwell reduction."""
    if not isinstance(frame.meta, LifecycleDwellFrameMeta):
        raise ValueError("Lifecycle dwell quality requires LifecycleFrame[dwell]")
    df = frame._dataframe_copy()
    return [
        _lifecycle_dwell_row_contract_check(df, frame),
        _lifecycle_dwell_math_check(df, frame),
        _lifecycle_source_history_check(frame),
    ]


def run_lifecycle_violations_checks(frame: LifecycleFrame) -> list[dict[str, str]]:
    """Return deterministic checks for exposed Lifecycle replay violations."""
    if not isinstance(frame.meta, LifecycleViolationsFrameMeta):
        raise ValueError("Lifecycle violations quality requires LifecycleFrame[violations]")
    df = frame._dataframe_copy()
    return [
        _lifecycle_violations_row_contract_check(df, frame),
        _lifecycle_violations_math_check(df, frame),
        _lifecycle_source_history_check(frame),
    ]


def run_lifecycle_checks(frame: LifecycleFrame) -> list[dict[str, str]]:
    """Dispatch Lifecycle quality by its closed Phase 3 artifact shape."""
    if frame.meta.semantic_kind == "history":
        return run_lifecycle_history_checks(frame)
    if frame.meta.semantic_kind == "distribution":
        return run_lifecycle_distribution_checks(frame)
    if frame.meta.semantic_kind == "transitions":
        return run_lifecycle_transitions_checks(frame)
    if frame.meta.semantic_kind == "dwell":
        return run_lifecycle_dwell_checks(frame)
    if frame.meta.semantic_kind == "violations":
        return run_lifecycle_violations_checks(frame)
    raise ValueError(f"unsupported LifecycleFrame shape {frame.meta.semantic_kind!r}")


def _lifecycle_expected_columns(frame: LifecycleFrame) -> tuple[str, ...]:
    meta = frame.meta
    if isinstance(meta, LifecycleHistoryFrameMeta):
        return LIFECYCLE_HISTORY_COLUMNS
    if isinstance(meta, LifecycleDistributionFrameMeta):
        return (
            *(axis.output_column for axis in meta.axes),
            *LIFECYCLE_DISTRIBUTION_VALUE_COLUMNS,
        )
    if isinstance(meta, LifecycleTransitionsFrameMeta):
        return LIFECYCLE_TRANSITIONS_COLUMNS
    if isinstance(meta, LifecycleDwellFrameMeta):
        return LIFECYCLE_DWELL_COLUMNS
    return LIFECYCLE_VIOLATIONS_COLUMNS


def _lifecycle_row_contract_result(
    *,
    check_id: str,
    df: pd.DataFrame,
    expected_columns: tuple[str, ...],
    invalid_rows: int = 0,
) -> dict[str, str]:
    missing_columns = tuple(column for column in expected_columns if column not in df.columns)
    extra_columns = tuple(column for column in df.columns if column not in expected_columns)
    order_mismatch = tuple(df.columns) != expected_columns
    invalid_count = len(missing_columns) + len(extra_columns) + int(order_mismatch) + invalid_rows
    severity = "blocking" if invalid_count else "ok"
    return _result(
        check_id,
        check_id,
        severity,
        severity,
        (
            f"{check_id.replace('_', ' ')} is valid"
            if not invalid_count
            else f"{check_id.replace('_', ' ')} has {invalid_count} violation(s)"
        ),
        {
            "invalid_count": invalid_count,
            "missing_columns": missing_columns,
            "extra_columns": extra_columns,
            "column_order_mismatch": order_mismatch,
            "invalid_row_count": invalid_rows,
            "expected_columns": expected_columns,
        },
    )


def _identity_is_valid(value: object, *, components: int) -> bool:
    return (
        isinstance(value, tuple)
        and len(value) == components
        and all(component is not None for component in value)
    )


def _safe_missing(value: object) -> bool:
    try:
        result = pd.isna(cast("Any", value))
    except (TypeError, ValueError):
        return False
    if isinstance(result, bool):
        return result
    item = getattr(result, "item", None)
    return bool(item()) if callable(item) else False


def _is_utc_value(value: object) -> bool:
    try:
        timestamp = pd.Timestamp(cast("Any", value))
    except (TypeError, ValueError):
        return False
    offset = timestamp.utcoffset()
    return timestamp.tzinfo is not None and offset is not None and offset.total_seconds() == 0


def _lifecycle_history_row_contract_check(
    df: pd.DataFrame,
    frame: LifecycleFrame,
) -> dict[str, str]:
    components = len(frame.meta.subject_identity)
    invalid_rows = 0
    if set(LIFECYCLE_HISTORY_COLUMNS).issubset(df.columns):
        states = {item.state.name for item in frame.meta.states}
        for row in df.to_dict("records"):
            completed = row["interval_status"] == "completed"
            exit_complete = (
                isinstance(row["exited_by_event_ref"], str)
                and bool(row["exited_by_event_ref"])
                and isinstance(row["exited_by_event_identity"], tuple)
            )
            valid = (
                _identity_is_valid(row["subject_identity"], components=components)
                and row["model_state"] in states
                and _is_utc_value(row["valid_from"])
                and _is_utc_value(row["valid_to"])
                and pd.Timestamp(row["valid_from"]) < pd.Timestamp(row["valid_to"])
                and isinstance(row["entered_by_event_ref"], str)
                and bool(row["entered_by_event_ref"])
                and isinstance(row["entered_by_event_identity"], tuple)
                and row["interval_status"] in {"completed", "right_censored", "coverage_censored"}
                and completed == exit_complete
            )
            invalid_rows += int(not valid)
    return _lifecycle_row_contract_result(
        check_id="lifecycle_history_row_contract",
        df=df,
        expected_columns=LIFECYCLE_HISTORY_COLUMNS,
        invalid_rows=invalid_rows,
    )


def _lifecycle_history_state_check(
    df: pd.DataFrame,
    frame: LifecycleFrame,
) -> dict[str, str]:
    meta = cast("LifecycleHistoryFrameMeta", frame.meta)
    initial_states = tuple(item.state.name for item in meta.states if item.initial)
    inception_targets = tuple(
        trigger.to_state for trigger in meta.triggers if trigger.kind == "inception"
    )
    invalid_count = int(
        meta.seed.kind != "from_inception"
        or len(initial_states) != 1
        or not inception_targets
        or any(target != initial_states[0] for target in inception_targets)
    )
    if set(LIFECYCLE_HISTORY_COLUMNS).issubset(df.columns):
        trigger_targets = {
            (f"{trigger.event_ref.kind.value}:{trigger.event_ref.path}", trigger.to_state)
            for trigger in meta.triggers
        }
        transition_triggers = {
            (
                trigger.from_state,
                trigger.to_state,
                f"{trigger.event_ref.kind.value}:{trigger.event_ref.path}",
            )
            for trigger in meta.triggers
            if trigger.kind == "transition"
        }
        for _subject, subject_rows in df.groupby("subject_identity", sort=False):
            records = subject_rows.sort_values("valid_from", kind="stable").to_dict("records")
            for index, row in enumerate(records):
                invalid_count += int(
                    (
                        str(row["entered_by_event_ref"]),
                        str(row["model_state"]),
                    )
                    not in trigger_targets
                )
                if row["interval_status"] != "completed":
                    continue
                if index + 1 >= len(records):
                    invalid_count += 1
                    continue
                following = records[index + 1]
                exit_matches_entry = (
                    row["exited_by_event_ref"] == following["entered_by_event_ref"]
                    and row["exited_by_event_identity"] == following["entered_by_event_identity"]
                )
                trigger_matches = (
                    str(row["model_state"]),
                    str(following["model_state"]),
                    str(row["exited_by_event_ref"]),
                ) in transition_triggers
                invalid_count += int(not exit_matches_entry or not trigger_matches)
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "lifecycle_history_state",
        "lifecycle_history_state",
        severity,
        severity,
        (
            "Lifecycle history rows use declared states and trigger bindings"
            if not invalid_count
            else f"Lifecycle history has {invalid_count} state/trigger violation(s)"
        ),
        {"invalid_count": invalid_count},
    )


def _lifecycle_history_interval_check(
    df: pd.DataFrame,
    frame: LifecycleFrame,
) -> dict[str, str]:
    meta = cast("LifecycleHistoryFrameMeta", frame.meta)
    window_start = pd.Timestamp(meta.window.start)
    window_end = pd.Timestamp(meta.window.end)
    invalid_count = 0
    if set(LIFECYCLE_HISTORY_COLUMNS).issubset(df.columns):
        ordering: list[tuple[str, pd.Timestamp]] = []
        expected_final = (
            "coverage_censored" if meta.coverage_basis == "unknown" else "right_censored"
        )
        for subject, subject_rows in df.groupby("subject_identity", sort=False):
            records = subject_rows.to_dict("records")
            previous: dict[Any, Any] | None = None
            for row in records:
                start = pd.Timestamp(row["valid_from"])
                end = pd.Timestamp(row["valid_to"])
                ordering.append(
                    (
                        json.dumps(subject, sort_keys=True, default=str),
                        start,
                    )
                )
                invalid_count += int(
                    start < window_start
                    or end > window_end
                    or start >= end
                    or (
                        previous is not None
                        and (
                            previous["interval_status"] != "completed"
                            or pd.Timestamp(cast("Any", previous["valid_to"])) != start
                        )
                    )
                )
                previous = row
            if previous is not None:
                invalid_count += int(previous["interval_status"] != expected_final)
        invalid_count += int(ordering != sorted(ordering))
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "lifecycle_history_intervals",
        "lifecycle_history_intervals",
        severity,
        severity,
        (
            "Lifecycle history intervals are ordered, adjacent, and inside the window"
            if not invalid_count
            else f"Lifecycle history has {invalid_count} interval violation(s)"
        ),
        {"invalid_count": invalid_count},
    )


def _lifecycle_history_count_check(
    df: pd.DataFrame,
    frame: LifecycleFrame,
) -> dict[str, str]:
    meta = cast("LifecycleHistoryFrameMeta", frame.meta)
    seeded = int(df["subject_identity"].nunique()) if "subject_identity" in df.columns else 0
    invalid_count = sum(
        (
            int(len(df) != meta.interval_count),
            int(len(df) != meta.row_count),
            int(seeded != meta.seeded_subject_count),
            int(meta.seeded_subject_count > meta.population_count),
            int(
                meta.coverage_censored_subject_count
                > meta.population_count - meta.seeded_subject_count
            ),
        )
    )
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "lifecycle_history_counts",
        "lifecycle_history_counts",
        severity,
        severity,
        (
            "Lifecycle history row and subject counts reconcile"
            if not invalid_count
            else f"Lifecycle history has {invalid_count} count mismatch(es)"
        ),
        {
            "invalid_count": invalid_count,
            "row_count": len(df),
            "metadata_interval_count": meta.interval_count,
            "seeded_subject_count": seeded,
            "metadata_seeded_subject_count": meta.seeded_subject_count,
            "population_count": meta.population_count,
            "coverage_censored_subject_count": meta.coverage_censored_subject_count,
        },
    )


def _lifecycle_frame_dir(
    frame: LifecycleFrame,
    *,
    artifact_ref: str,
) -> Path:
    return (
        Path(frame.meta.project_root)
        / ".marivo"
        / "analysis"
        / "sessions"
        / frame.meta.session_id
        / "frames"
        / artifact_ref
    )


def _load_lifecycle_source_history(
    frame: LifecycleFrame,
) -> tuple[pd.DataFrame, LifecycleHistoryFrameMeta] | None:
    meta = frame.meta
    if not isinstance(
        meta,
        (
            LifecycleDistributionFrameMeta,
            LifecycleTransitionsFrameMeta,
            LifecycleDwellFrameMeta,
            LifecycleViolationsFrameMeta,
        ),
    ):
        return None
    frame_dir = _lifecycle_frame_dir(frame, artifact_ref=meta.source_history_ref)
    data_path = frame_dir / "data.parquet"
    meta_path = frame_dir / "meta.json"
    if not data_path.is_file() or not meta_path.is_file():
        return None
    try:
        source_meta = LifecycleHistoryFrameMeta.model_validate_json(
            meta_path.read_text(encoding="utf-8")
        )
        source_df = pd.read_parquet(
            data_path,
            engine="pyarrow",
            to_pandas_kwargs={},
        )
        trace_path = frame_dir / source_meta.violation_trace.filename
        if (
            source_meta.violation_trace.content_hash is None
            or not trace_path.is_file()
            or compute_file_content_hash(trace_path) != source_meta.violation_trace.content_hash
            or len(
                pd.read_parquet(
                    trace_path,
                    engine="pyarrow",
                    to_pandas_kwargs={},
                )
            )
            != source_meta.violation_trace.row_count
        ):
            return None
    except (OSError, TypeError, ValueError):
        return None
    for column in (
        "subject_identity",
        "entered_by_event_identity",
        "exited_by_event_identity",
    ):
        if column in source_df:
            source_df[column] = source_df[column].map(
                lambda value: (
                    tuple(value)
                    if isinstance(value, list)
                    else tuple(value.tolist())
                    if callable(getattr(value, "tolist", None)) and isinstance(value.tolist(), list)
                    else value
                )
            )
    if (
        source_meta.ref != meta.source_history_ref
        or source_meta.content_hash is None
        or source_meta.content_hash != meta.source_history_fingerprint
        or compute_frame_content_hash(meta=source_meta, data_path=data_path)
        != source_meta.content_hash
        or source_meta.state_model_ref != meta.state_model_ref
        or source_meta.state_model_fingerprint != meta.state_model_fingerprint
    ):
        return None
    return source_df, source_meta


def _lifecycle_source_history_check(frame: LifecycleFrame) -> dict[str, str]:
    loaded = _load_lifecycle_source_history(frame)
    valid = loaded is not None
    return _result(
        "lifecycle_source_history",
        "lifecycle_source_history",
        "ok" if valid else "blocking",
        "ok" if valid else "blocking",
        (
            "Lifecycle reducer source history and content fingerprint are current"
            if valid
            else "Lifecycle reducer source history is missing, corrupt, or stale"
        ),
        {"invalid_count": int(not valid)},
    )


def _lifecycle_trace_check(frame: LifecycleFrame) -> dict[str, str]:
    meta = cast("LifecycleHistoryFrameMeta", frame.meta)
    trace = frame._auxiliary_frames.get(meta.violation_trace.filename)
    invalid_count = 0
    if trace is None:
        invalid_count += 1
    else:
        invalid_count += int(tuple(trace.columns) != LIFECYCLE_VIOLATIONS_COLUMNS)
        invalid_count += int(len(trace) != meta.violation_trace.row_count)
        if set(LIFECYCLE_VIOLATIONS_COLUMNS).issubset(trace.columns):
            states = {item.state.name for item in meta.states}
            trigger_refs = {
                f"{item.event_ref.kind.value}:{item.event_ref.path}" for item in meta.triggers
            }
            components = len(meta.subject_identity)
            invalid_count += sum(
                int(
                    not _identity_is_valid(
                        row["subject_identity"],
                        components=components,
                    )
                    or row["model_state_at_event"] not in states
                    or row["trigger_event_ref"] not in trigger_refs
                    or not isinstance(row["trigger_event_identity"], tuple)
                    or not _is_utc_value(row["occurred_at"])
                    or row["violation_kind"]
                    not in {"illegal_transition", "transition_from_terminal"}
                )
                for row in trace.to_dict("records")
            )
    if meta.violation_trace.content_hash is not None:
        artifact_ref = meta.artifact_id or meta.ref
        trace_path = (
            _lifecycle_frame_dir(frame, artifact_ref=artifact_ref) / meta.violation_trace.filename
        )
        invalid_count += int(
            not trace_path.is_file()
            or compute_file_content_hash(trace_path) != meta.violation_trace.content_hash
        )
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "lifecycle_trace",
        "lifecycle_trace",
        severity,
        severity,
        (
            "Lifecycle replay trace matches its closed manifest"
            if not invalid_count
            else f"Lifecycle replay trace has {invalid_count} integrity violation(s)"
        ),
        {
            "invalid_count": invalid_count,
            "trace_row_count": 0 if trace is None else len(trace),
            "metadata_trace_row_count": meta.violation_trace.row_count,
        },
    )


def _coverage_entry_issues(
    coverage: EventInputCoverage,
    *,
    required_through: pd.Timestamp,
) -> tuple[str, ...]:
    issues: list[str] = []
    if coverage.basis == "observed_watermark":
        if coverage.receipt is None:
            issues.append("missing_watermark_receipt")
        else:
            try:
                complete_through = pd.Timestamp(coverage.receipt.complete_through)
            except (TypeError, ValueError):
                issues.append("invalid_complete_through")
            else:
                if complete_through.tzinfo is None or complete_through < required_through:
                    issues.append("watermark_before_window_end")
    elif coverage.basis == "declared_complete":
        if not coverage.declaration_fingerprint or not coverage.declaration_rationale:
            issues.append("missing_declaration_evidence")
    elif coverage.basis != "unknown":
        issues.append("invalid_basis")
    return tuple(issues)


def _lifecycle_coverage_checks(frame: LifecycleFrame) -> list[dict[str, str]]:
    meta = cast("LifecycleHistoryFrameMeta", frame.meta)
    required_through = pd.Timestamp(meta.window.end)
    expected_refs = tuple(dict.fromkeys(item.event_ref.path for item in meta.triggers))
    coverage_by_ref: dict[str, list[EventInputCoverage]] = {}
    for item in meta.input_coverage:
        coverage_by_ref.setdefault(item.event_ref.path, []).append(item)
    rows: list[dict[str, str]] = []
    for event_ref in expected_refs:
        matches = coverage_by_ref.get(event_ref, [])
        coverage = matches[0] if len(matches) == 1 else None
        issues = (
            _coverage_entry_issues(
                coverage,
                required_through=required_through,
            )
            if coverage is not None
            else ("missing_or_duplicate_coverage",)
        )
        unknown = coverage is not None and coverage.basis == "unknown"
        severity = "blocking" if issues else "warning" if unknown else "ok"
        rows.append(
            _result(
                f"lifecycle_coverage:{event_ref}",
                "lifecycle_coverage",
                severity,
                severity,
                (
                    f"Lifecycle coverage is unknown for {event_ref}"
                    if unknown and not issues
                    else (
                        f"Lifecycle coverage for {event_ref} is invalid"
                        if issues
                        else f"Lifecycle coverage for {event_ref} is supported"
                    )
                ),
                {
                    "event_ref": event_ref,
                    "basis": coverage.basis if coverage is not None else None,
                    "invalid_count": len(issues),
                    "unknown_count": int(unknown),
                    "evidence_issues": issues,
                },
            )
        )
    expected_basis = _expected_coverage_basis(meta.input_coverage)
    aggregate_valid = meta.coverage_basis == expected_basis and set(coverage_by_ref) == set(
        expected_refs
    )
    rows.append(
        _result(
            "lifecycle_coverage:aggregate",
            "lifecycle_coverage",
            "ok" if aggregate_valid else "blocking",
            "ok" if aggregate_valid else "blocking",
            (
                "Lifecycle aggregate coverage matches its per-Event evidence"
                if aggregate_valid
                else "Lifecycle aggregate coverage does not match its per-Event evidence"
            ),
            {
                "basis": meta.coverage_basis,
                "expected_basis": expected_basis,
                "invalid_count": int(not aggregate_valid),
                "unknown_count": int(meta.coverage_basis == "unknown"),
            },
        )
    )
    return rows


def _lifecycle_declaration_check(frame: LifecycleFrame) -> dict[str, str]:
    meta = cast("LifecycleHistoryFrameMeta", frame.meta)
    declared = [item for item in meta.input_coverage if item.basis == "declared_complete"]
    count = len(declared)
    severity = "warning" if count else "ok"
    return _result(
        "declared_completeness_used",
        "declared_completeness_used",
        severity,
        severity,
        (
            f"{count} Lifecycle Event input(s) rely on caller-declared completeness"
            if count
            else "no caller completeness declaration was used"
        ),
        {
            "declared_input_count": count,
            "event_refs": [item.event_ref.path for item in declared],
        },
    )


def _lifecycle_censoring_check(
    df: pd.DataFrame,
    frame: LifecycleFrame,
) -> dict[str, str]:
    meta = cast("LifecycleHistoryFrameMeta", frame.meta)
    interval_count = (
        int((df["interval_status"] == "coverage_censored").sum()) if "interval_status" in df else 0
    )
    subject_count = meta.coverage_censored_subject_count
    present = interval_count > 0 or subject_count > 0
    return _result(
        "lifecycle_censoring",
        "lifecycle_censoring",
        "warning" if present else "ok",
        "warning" if present else "ok",
        (
            "Lifecycle replay contains coverage-censored state"
            if present
            else "Lifecycle replay has no coverage-censored state"
        ),
        {
            "coverage_censored_interval_count": interval_count,
            "coverage_censored_subject_count": subject_count,
            "invalid_count": 0,
        },
    )


def _lifecycle_distribution_row_contract_check(
    df: pd.DataFrame,
    frame: LifecycleFrame,
) -> dict[str, str]:
    meta = cast("LifecycleDistributionFrameMeta", frame.meta)
    expected_columns = _lifecycle_expected_columns(frame)
    invalid_rows = 0
    if set(expected_columns).issubset(df.columns):
        keys = [*(axis.output_column for axis in meta.axes), "as_of", "model_state"]
        invalid_rows += int(df.duplicated(subset=keys, keep=False).sum())
        state_order = tuple(item.state.name for item in meta.states)
        axis_columns = [axis.output_column for axis in meta.axes]
        group_columns = [*axis_columns, "as_of"]
        groups = (
            df.groupby(group_columns, dropna=False, sort=False) if group_columns else (((), df),)
        )
        for _key, group in groups:
            invalid_rows += int(tuple(group["model_state"]) != state_order)
        invalid_rows += int(not set(df["as_of"]).issubset(set(meta.at)))
    return _lifecycle_row_contract_result(
        check_id="lifecycle_distribution_row_contract",
        df=df,
        expected_columns=expected_columns,
        invalid_rows=invalid_rows,
    )


def _nonnegative_integer(value: object) -> bool:
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and float(value).is_integer()
        and float(value) >= 0
    )


def _lifecycle_distribution_math_check(
    df: pd.DataFrame,
    frame: LifecycleFrame,
) -> dict[str, str]:
    meta = cast("LifecycleDistributionFrameMeta", frame.meta)
    invalid_count = 0
    required = {*_lifecycle_expected_columns(frame)}
    if required.issubset(df.columns):
        axis_columns = [axis.output_column for axis in meta.axes]
        for _key, group in df.groupby(
            [*axis_columns, "as_of"],
            dropna=False,
            sort=False,
        ):
            valid_counts = all(_nonnegative_integer(value) for value in group["subject_count"])
            invalid_count += int(not valid_counts)
            if not valid_counts:
                continue
            denominator = int(pd.to_numeric(group["subject_count"]).sum())
            for count, share in zip(
                group["subject_count"],
                group["share"],
                strict=True,
            ):
                if denominator == 0:
                    invalid_count += int(not _safe_missing(share))
                else:
                    invalid_count += int(
                        _safe_missing(share)
                        or abs(float(share) - float(count) / denominator) > 1e-12
                    )
        for instant in meta.at:
            instant_rows = df.loc[df["as_of"] == instant]
            observed = int(pd.to_numeric(instant_rows["subject_count"]).sum())
            invalid_count += int(observed != meta.known_subject_counts[instant])
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "lifecycle_distribution_math",
        "lifecycle_distribution_math",
        severity,
        severity,
        (
            "Lifecycle distribution counts and shares reconcile"
            if not invalid_count
            else f"Lifecycle distribution has {invalid_count} math violation(s)"
        ),
        {"invalid_count": invalid_count},
    )


def _source_state_counts(
    source: pd.DataFrame,
    *,
    instants: tuple[str, ...],
    states: tuple[str, ...],
) -> dict[tuple[str, str], int]:
    return {
        (instant, state): int(
            (
                (pd.to_datetime(source["valid_from"], utc=True) <= pd.Timestamp(instant))
                & (pd.to_datetime(source["valid_to"], utc=True) > pd.Timestamp(instant))
                & (source["model_state"] == state)
            ).sum()
        )
        for instant in instants
        for state in states
    }


def _lifecycle_distribution_reconciliation_check(
    df: pd.DataFrame,
    frame: LifecycleFrame,
) -> dict[str, str]:
    meta = cast("LifecycleDistributionFrameMeta", frame.meta)
    loaded = _load_lifecycle_source_history(frame)
    invalid_count = 0
    hash_matches = False
    if loaded is None:
        invalid_count = 1
    else:
        source, source_meta = loaded
        states = tuple(item.state.name for item in meta.states)
        expected = _source_state_counts(
            source,
            instants=meta.at,
            states=states,
        )
        axis_columns = [axis.output_column for axis in meta.axes]
        reconciliation: list[dict[str, object]] = []
        for instant in meta.at:
            for state in states:
                current = df.loc[(df["as_of"] == instant) & (df["model_state"] == state)]
                grouped_count = int(
                    pd.to_numeric(current["subject_count"], errors="coerce").fillna(0).sum()
                )
                source_count = expected[(instant, state)]
                invalid_count += int(grouped_count != source_count)
                reconciliation.append(
                    {
                        "as_of": instant,
                        "model_state": state,
                        "ungrouped_count": source_count,
                        "grouped_count": grouped_count,
                    }
                )
            known = sum(expected[(instant, state)] for state in states)
            invalid_count += int(meta.known_subject_counts[instant] != known)
            invalid_count += int(
                meta.coverage_censored_subject_counts[instant]
                != source_meta.population_count - known
            )
        hash_matches = stable_digest(reconciliation) == meta.grouped_reconciliation_hash
        invalid_count += int(not hash_matches)
        del axis_columns
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "lifecycle_distribution_reconciliation",
        "lifecycle_distribution_reconciliation",
        severity,
        severity,
        (
            "Lifecycle grouped distribution reconciles to committed history"
            if not invalid_count
            else f"Lifecycle distribution has {invalid_count} reconciliation violation(s)"
        ),
        {
            "invalid_count": invalid_count,
            "receipt_matches_current_rows": hash_matches,
        },
    )


def _lifecycle_transitions_row_contract_check(
    df: pd.DataFrame,
    frame: LifecycleFrame,
) -> dict[str, str]:
    meta = cast("LifecycleTransitionsFrameMeta", frame.meta)
    invalid_rows = 0
    if set(LIFECYCLE_TRANSITIONS_COLUMNS).issubset(df.columns):
        actual_pairs = tuple(
            zip(
                df["from_model_state"],
                df["to_model_state"],
                strict=True,
            )
        )
        expected_pairs = tuple((pair.from_state, pair.to_state) for pair in meta.modeled_pairs)
        invalid_rows += int(actual_pairs != expected_pairs)
        invalid_rows += int(set(df["transition_status"]) != ({"modeled"} if len(df) else set()))
    return _lifecycle_row_contract_result(
        check_id="lifecycle_transitions_row_contract",
        df=df,
        expected_columns=LIFECYCLE_TRANSITIONS_COLUMNS,
        invalid_rows=invalid_rows,
    )


def _dataframes_match(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    columns: tuple[str, ...],
) -> bool:
    if tuple(left.columns) != columns or tuple(right.columns) != columns:
        return False
    try:
        pd.testing.assert_frame_equal(
            left.reset_index(drop=True),
            right.reset_index(drop=True),
            check_dtype=False,
            check_like=False,
        )
    except AssertionError:
        return False
    return True


def _lifecycle_transitions_math_check(
    df: pd.DataFrame,
    frame: LifecycleFrame,
) -> dict[str, str]:
    meta = cast("LifecycleTransitionsFrameMeta", frame.meta)
    loaded = _load_lifecycle_source_history(frame)
    invalid_count = 0
    if loaded is None:
        invalid_count = 1
    else:
        source, source_meta = loaded
        expected = reduce_lifecycle_transitions(
            source,
            triggers=source_meta.triggers,
        )
        invalid_count += int(
            not _dataframes_match(
                df,
                expected.rows,
                columns=LIFECYCLE_TRANSITIONS_COLUMNS,
            )
        )
        invalid_count += int(meta.modeled_transition_count != expected.modeled_transition_count)
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "lifecycle_transitions_math",
        "lifecycle_transitions_math",
        severity,
        severity,
        (
            "Lifecycle transitions exactly recompute from committed history"
            if not invalid_count
            else f"Lifecycle transitions have {invalid_count} source mismatch(es)"
        ),
        {"invalid_count": invalid_count},
    )


def _lifecycle_dwell_row_contract_check(
    df: pd.DataFrame,
    frame: LifecycleFrame,
) -> dict[str, str]:
    expected_states = tuple(item.state.name for item in frame.meta.states)
    invalid_rows = (
        int(tuple(df["model_state"]) != expected_states) if "model_state" in df.columns else 0
    )
    return _lifecycle_row_contract_result(
        check_id="lifecycle_dwell_row_contract",
        df=df,
        expected_columns=LIFECYCLE_DWELL_COLUMNS,
        invalid_rows=invalid_rows,
    )


def _lifecycle_dwell_math_check(
    df: pd.DataFrame,
    frame: LifecycleFrame,
) -> dict[str, str]:
    meta = cast("LifecycleDwellFrameMeta", frame.meta)
    loaded = _load_lifecycle_source_history(frame)
    invalid_count = 0
    if loaded is None:
        invalid_count = 1
    else:
        source, _source_meta = loaded
        expected = reduce_lifecycle_dwell(
            source,
            state_order=tuple(item.state.name for item in meta.states),
        )
        invalid_count += int(
            not _dataframes_match(
                df,
                expected.rows,
                columns=LIFECYCLE_DWELL_COLUMNS,
            )
        )
        invalid_count += int(meta.source_interval_count != expected.source_interval_count)
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "lifecycle_dwell_math",
        "lifecycle_dwell_math",
        severity,
        severity,
        (
            "Lifecycle dwell exactly recomputes from committed history"
            if not invalid_count
            else f"Lifecycle dwell has {invalid_count} source mismatch(es)"
        ),
        {"invalid_count": invalid_count},
    )


def _lifecycle_violations_row_contract_check(
    df: pd.DataFrame,
    frame: LifecycleFrame,
) -> dict[str, str]:
    components = len(frame.meta.subject_identity)
    invalid_rows = 0
    if set(LIFECYCLE_VIOLATIONS_COLUMNS).issubset(df.columns):
        states = {item.state.name for item in frame.meta.states}
        for row in df.to_dict("records"):
            invalid_rows += int(
                not _identity_is_valid(
                    row["subject_identity"],
                    components=components,
                )
                or row["model_state_at_event"] not in states
                or not isinstance(row["trigger_event_ref"], str)
                or not isinstance(row["trigger_event_identity"], tuple)
                or not _is_utc_value(row["occurred_at"])
                or row["violation_kind"] not in {"illegal_transition", "transition_from_terminal"}
            )
    return _lifecycle_row_contract_result(
        check_id="lifecycle_violations_row_contract",
        df=df,
        expected_columns=LIFECYCLE_VIOLATIONS_COLUMNS,
        invalid_rows=invalid_rows,
    )


def _lifecycle_violations_math_check(
    df: pd.DataFrame,
    frame: LifecycleFrame,
) -> dict[str, str]:
    meta = cast("LifecycleViolationsFrameMeta", frame.meta)
    loaded = _load_lifecycle_source_history(frame)
    invalid_count = 0
    if loaded is None:
        invalid_count = 1
    else:
        _source, source_meta = loaded
        source_dir = _lifecycle_frame_dir(
            frame,
            artifact_ref=meta.source_history_ref,
        )
        trace_path = source_dir / source_meta.violation_trace.filename
        if (
            not trace_path.is_file()
            or source_meta.violation_trace.content_hash is None
            or compute_file_content_hash(trace_path) != source_meta.violation_trace.content_hash
            or source_meta.violation_trace.content_hash != meta.source_trace_content_hash
        ):
            invalid_count += 1
        else:
            trace = pd.read_parquet(
                trace_path,
                engine="pyarrow",
                to_pandas_kwargs={},
            )
            for column in ("subject_identity", "trigger_event_identity"):
                trace[column] = trace[column].map(
                    lambda value: (
                        tuple(value)
                        if isinstance(value, list)
                        else tuple(value.tolist())
                        if callable(getattr(value, "tolist", None))
                        and isinstance(value.tolist(), list)
                        else value
                    )
                )
            expected = reduce_lifecycle_violations(trace)
            invalid_count += int(
                not _dataframes_match(
                    df,
                    expected.rows,
                    columns=LIFECYCLE_VIOLATIONS_COLUMNS,
                )
            )
            invalid_count += int(meta.violation_count != expected.violation_count)
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "lifecycle_violations_math",
        "lifecycle_violations_math",
        severity,
        severity,
        (
            "Lifecycle violations exactly reproduce the committed replay trace"
            if not invalid_count
            else f"Lifecycle violations have {invalid_count} source mismatch(es)"
        ),
        {"invalid_count": invalid_count},
    )


def _result(
    check_id: str,
    check_kind: str,
    status: str,
    severity: str,
    message: str,
    details: dict[str, Any],
) -> dict[str, str]:
    return {
        "check_id": check_id,
        "check_kind": check_kind,
        "status": status,
        "severity": severity,
        "message": message,
        "details_json": json.dumps(details, sort_keys=True, default=str),
    }


def _row_count_check(
    df: pd.DataFrame,
    *,
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"],
) -> dict[str, str]:
    count = len(df)
    if count == 0:
        severity = "blocking"
    elif semantic_kind == "scalar":
        severity = "ok"
    else:
        severity = "warning" if count < 5 else "ok"
    return _result(
        "row_count",
        "row_count",
        severity,
        severity,
        f"row count is {count}",
        {"row_count": count, "threshold_warning": 5, "threshold_blocking": 0},
    )


def _measure_columns(frame: MetricFrame) -> list[str]:
    measure = frame.meta.measure
    if isinstance(measure.get("field"), str):
        return [str(measure["field"])]
    if isinstance(measure.get("fields"), list):
        return [str(column) for column in measure["fields"]]
    return []


def _null_ratio_checks(df: pd.DataFrame, frame: MetricFrame) -> list[dict[str, str]]:
    rows = []
    denominator = len(df)
    for column in _measure_columns(frame):
        null_count = int(df[column].isna().sum()) if column in df else denominator
        ratio = 0.0 if denominator == 0 else null_count / denominator
        severity = "blocking" if ratio > 0.5 else "warning" if ratio > 0.1 else "ok"
        rows.append(
            _result(
                f"null_ratio:{column}",
                "null_ratio",
                severity,
                severity,
                f"null ratio for {column} is {ratio:.3f}",
                {
                    "column": column,
                    "null_count": null_count,
                    "null_ratio": ratio,
                    "threshold_warning": 0.1,
                    "threshold_blocking": 0.5,
                },
            )
        )
    return rows


def _time_axis(frame: MetricFrame) -> tuple[str, str]:
    return metric_time_axis(frame)


def _time_coverage_check(
    df: pd.DataFrame, frame: MetricFrame, *, tz: str | None = None
) -> dict[str, str]:
    time_col, grain = _time_axis(frame)
    window = frame.meta.window or {}
    start = window.get("start")
    end = window.get("end")
    if start is None or end is None or grain not in _FREQ:
        return _result(
            "time_coverage",
            "time_coverage",
            "warning",
            "warning",
            "time coverage cannot be computed from frame metadata",
            {
                "expected_buckets": 0,
                "observed_buckets": int(df[time_col].nunique()) if time_col in df else 0,
                "coverage_ratio": 0.0,
                "missing_examples": [],
            },
        )
    expected = pd.date_range(
        pd.Timestamp(start), pd.Timestamp(end), freq=_FREQ[grain], inclusive="left"
    )
    observed_ts = (
        pd.to_datetime(df[time_col]).dropna()
        if time_col in df and len(df)
        else pd.Series(dtype="datetime64[ns]")
    )
    if tz and len(observed_ts) > 0 and observed_ts.dt.tz is not None:
        observed_ts = observed_ts.dt.tz_convert(tz).dt.tz_localize(None)
    observed = normalize_coverage_buckets(observed_ts, grain=grain).unique()
    observed_set = {pd.Timestamp(value) for value in observed}
    expected_buckets = normalize_coverage_buckets(pd.Series(expected), grain=grain)
    missing = [value for value in expected_buckets if pd.Timestamp(value) not in observed_set]
    ratio = 1.0 if len(expected) == 0 else (len(expected) - len(missing)) / len(expected)
    severity = "blocking" if ratio < 0.8 else "warning" if ratio < 0.95 else "ok"
    return _result(
        "time_coverage",
        "time_coverage",
        severity,
        severity,
        f"time coverage ratio is {ratio:.3f}",
        {
            "expected_buckets": len(expected),
            "observed_buckets": len(observed_set),
            "coverage_ratio": ratio,
            "missing_examples": [value.isoformat() for value in missing[:5]],
        },
    )


def _segment_dimensions(frame: MetricFrame) -> list[str]:
    return metric_dimension_columns(frame)


def _duplicate_keys_check(df: pd.DataFrame, frame: MetricFrame) -> dict[str, str]:
    keys = _segment_dimensions(frame)
    if frame.meta.semantic_kind == "panel":
        time_col, _ = _time_axis(frame)
        keys.append(time_col)
    duplicates = df.duplicated(subset=keys, keep=False) if keys else pd.Series([False] * len(df))
    duplicate_count = int(duplicates.sum())
    severity = "blocking" if duplicate_count else "ok"
    examples = df.loc[duplicates, keys].head(5).to_dict("records") if duplicate_count else []
    return _result(
        "duplicate_keys",
        "duplicate_keys",
        severity,
        severity,
        f"duplicate key row count is {duplicate_count}",
        {"duplicate_count": duplicate_count, "examples": examples},
    )


_EVENT_JOURNEY_COLUMNS = (
    "journey_id",
    "completion_status",
    "subject_identity",
    "step_key",
    "event_identity",
    "occurred_at",
    "elapsed_from_start",
    "elapsed_from_previous",
)

_EVENT_FUNNEL_COUNT_COLUMNS = (
    "cohort_count",
    "resolved_cohort_count",
    "entry_count",
    "resolved_entry_count",
    "reached_count",
    "lost_count",
    "coverage_censored_count",
)
_EVENT_FUNNEL_RATE_COLUMNS = (
    "conversion_from_first",
    "conversion_from_previous",
    "loss_rate_from_previous",
)
_EVENT_TIME_TO_EVENT_COLUMNS = (
    "journey_id",
    "subject_identity",
    "start_event_identity",
    "start_time",
    "end_event_identity",
    "end_time",
    "duration",
    "completion_status",
)


def _event_funnel_row_contract_check(
    df: pd.DataFrame,
    frame: EventFrame,
) -> dict[str, str]:
    meta = frame.meta
    if not isinstance(meta, EventFunnelFrameMeta):
        raise ValueError("funnel quality requires EventFrame[funnel]")
    expected_axes = tuple(axis.output_column for axis in meta.axes)
    expected_columns = (
        *expected_axes,
        "step_key",
        *_EVENT_FUNNEL_COUNT_COLUMNS[:-1],
        *_EVENT_FUNNEL_RATE_COLUMNS,
        "coverage_censored_count",
    )
    missing_columns = tuple(column for column in expected_columns if column not in df.columns)
    extra_columns = tuple(column for column in df.columns if column not in expected_columns)
    column_order_mismatch = tuple(df.columns) != expected_columns
    step_order = tuple(step.key for step in frame.meta.pattern.steps)
    invalid_groups = 0
    duplicate_rows = 0
    if not missing_columns:
        group_columns = list(expected_axes)
        duplicate_rows = int(df.duplicated(subset=[*group_columns, "step_key"], keep=False).sum())
        groups = (
            df.groupby(group_columns, dropna=False, sort=False) if group_columns else (((), df),)
        )
        for _, group in groups:
            if tuple(group["step_key"].astype(str)) != step_order:
                invalid_groups += 1
    invalid_count = (
        len(missing_columns)
        + len(extra_columns)
        + int(column_order_mismatch)
        + duplicate_rows
        + invalid_groups
    )
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "event_funnel_row_contract",
        "event_funnel_row_contract",
        severity,
        severity,
        (
            "funnel row contract is valid"
            if not invalid_count
            else f"funnel row contract has {invalid_count} violation(s)"
        ),
        {
            "invalid_count": invalid_count,
            "missing_columns": missing_columns,
            "extra_columns": extra_columns,
            "column_order_mismatch": column_order_mismatch,
            "duplicate_rows": duplicate_rows,
            "invalid_step_order_groups": invalid_groups,
            "expected_columns": expected_columns,
        },
    )


def _rate_matches(value: Any, expected: float | None) -> bool:
    if expected is None:
        return bool(pd.isna(value))
    if bool(pd.isna(value)):
        return False
    return abs(float(value) - expected) <= 1e-12


def _event_funnel_math_check(df: pd.DataFrame, frame: EventFrame) -> dict[str, str]:
    required = {
        "step_key",
        *_EVENT_FUNNEL_COUNT_COLUMNS,
        *_EVENT_FUNNEL_RATE_COLUMNS,
    }
    violations = 0
    non_integer_or_negative = 0
    if required.issubset(df.columns):
        step_order = tuple(step.key for step in frame.meta.pattern.steps)
        meta = frame.meta
        if not isinstance(meta, EventFunnelFrameMeta):
            raise ValueError("funnel quality requires EventFrame[funnel]")
        group_columns = [axis.output_column for axis in meta.axes]
        groups = (
            df.groupby(group_columns, dropna=False, sort=False) if group_columns else (((), df),)
        )
        for _, group in groups:
            previous_reached: int | None = None
            for row in group.to_dict("records"):
                row_invalid = False
                for column in _EVENT_FUNNEL_COUNT_COLUMNS:
                    value = row[column]
                    valid = (
                        isinstance(value, Real)
                        and not isinstance(value, bool)
                        and float(value).is_integer()
                        and float(value) >= 0
                    )
                    non_integer_or_negative += int(not valid)
                    row_invalid = row_invalid or not valid
                if row_invalid:
                    continue
                first = str(row["step_key"]) == step_order[0]
                cohort = int(row["cohort_count"])
                resolved_cohort = int(row["resolved_cohort_count"])
                entry = int(row["entry_count"])
                resolved_entry = int(row["resolved_entry_count"])
                reached = int(row["reached_count"])
                lost = int(row["lost_count"])
                censored = int(row["coverage_censored_count"])
                if first:
                    violations += int(
                        not (
                            cohort == resolved_cohort == entry == resolved_entry == reached
                            and lost == 0
                            and censored == 0
                        )
                    )
                    violations += int(not pd.isna(row["conversion_from_previous"]))
                    violations += int(not pd.isna(row["loss_rate_from_previous"]))
                else:
                    violations += int(previous_reached is None or entry != previous_reached)
                    violations += int(resolved_entry != entry - censored)
                    violations += int(lost != resolved_entry - reached)
                violations += int(resolved_cohort > cohort)
                violations += int(
                    not _rate_matches(
                        row["conversion_from_first"],
                        reached / resolved_cohort if resolved_cohort else None,
                    )
                )
                violations += int(
                    not _rate_matches(
                        row["conversion_from_previous"],
                        (reached / resolved_entry if not first and resolved_entry else None),
                    )
                )
                violations += int(
                    not _rate_matches(
                        row["loss_rate_from_previous"],
                        lost / resolved_entry if not first and resolved_entry else None,
                    )
                )
                previous_reached = reached
    else:
        violations = len(required - set(df.columns))
    invalid_count = violations + non_integer_or_negative
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "event_funnel_math",
        "event_funnel_math",
        severity,
        severity,
        (
            "funnel count and rate equations reconcile"
            if not invalid_count
            else f"funnel math has {invalid_count} violation(s)"
        ),
        {
            "invalid_count": invalid_count,
            "equation_violations": violations,
            "non_integer_or_negative_counts": non_integer_or_negative,
        },
    )


def _event_funnel_axis_check(df: pd.DataFrame, frame: EventFrame) -> dict[str, str]:
    meta = frame.meta
    if not isinstance(meta, EventFunnelFrameMeta):
        raise ValueError("funnel quality requires EventFrame[funnel]")
    axes = tuple(meta.axes)
    missing_columns = tuple(axis.output_column for axis in axes if axis.output_column not in df)
    invalid_anchor_count = sum(axis.anchor != "cohort_entry" for axis in axes)
    invalid_null_contract_count = sum(axis.null_group != "explicit" for axis in axes)
    invalid_count = len(missing_columns) + invalid_anchor_count + invalid_null_contract_count
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "event_funnel_axes",
        "event_funnel_axes",
        severity,
        severity,
        (
            "funnel axes retain governed cohort-entry contracts"
            if not invalid_count
            else f"funnel axes have {invalid_count} violation(s)"
        ),
        {
            "invalid_count": invalid_count,
            "missing_columns": missing_columns,
            "invalid_anchor_count": invalid_anchor_count,
            "invalid_null_contract_count": invalid_null_contract_count,
            "axis_count": len(axes),
        },
    )


def _event_funnel_reconciliation_check(frame: EventFrame) -> dict[str, str]:
    meta = frame.meta
    if not isinstance(meta, EventFunnelFrameMeta):
        raise ValueError("funnel quality requires EventFrame[funnel]")
    receipt = meta.grouped_reconciliation
    step_keys = tuple(step.key for step in meta.pattern.steps)
    current_grouped_hash: str | None = None
    try:
        grouped_totals = (
            frame._dataframe_copy()
            .groupby("step_key", dropna=False, sort=False)[list(FUNNEL_ADDITIVE_COLUMNS)]
            .sum()
            .reindex(step_keys, fill_value=0)
            .reset_index()
        )
        current_grouped_hash = funnel_reconciliation_hash(
            grouped_totals,
            step_keys=step_keys,
        )
    except (KeyError, TypeError, ValueError):
        pass
    valid = bool(
        receipt.status == "pass"
        and current_grouped_hash
        and receipt.ungrouped_hash == receipt.grouped_hash == current_grouped_hash
    )
    return _result(
        "event_funnel_reconciliation",
        "event_funnel_reconciliation",
        "ok" if valid else "blocking",
        "ok" if valid else "blocking",
        (
            "grouped funnel reconciles to the ungrouped source"
            if valid
            else "grouped funnel reconciliation receipt is invalid"
        ),
        {
            "invalid_count": 0 if valid else 1,
            "status": receipt.status,
            "additive_columns": receipt.additive_columns,
            "current_grouped_hash_matches": (
                current_grouped_hash == receipt.grouped_hash
                if current_grouped_hash is not None
                else False
            ),
        },
    )


def _event_funnel_censoring_check(df: pd.DataFrame) -> dict[str, str]:
    count = int(df["coverage_censored_count"].sum()) if "coverage_censored_count" in df else 0
    severity = "warning" if count else "ok"
    return _result(
        "event_censoring",
        "event_censoring",
        severity,
        severity,
        f"funnel coverage-censored population is {count}",
        {"coverage_censored_count": count},
    )


def _event_time_to_event_contract_check(
    df: pd.DataFrame,
    frame: EventFrame,
) -> dict[str, str]:
    meta = frame.meta
    if not isinstance(meta, EventTimeToEventFrameMeta):
        raise ValueError("time-to-event quality requires EventFrame[time_to_event]")
    missing = tuple(column for column in _EVENT_TIME_TO_EVENT_COLUMNS if column not in df)
    extra = tuple(column for column in df.columns if column not in _EVENT_TIME_TO_EVENT_COLUMNS)
    column_order_mismatch = tuple(df.columns) != _EVENT_TIME_TO_EVENT_COLUMNS
    invalid_statuses = 0
    null_consistency = 0
    duplicate_journeys = 0
    if not missing:
        statuses = set(df["completion_status"].dropna().astype(str))
        invalid_statuses = len(statuses - {"complete", "incomplete", "coverage_censored"})
        duplicate_journeys = int(df["journey_id"].duplicated(keep=False).sum())
        completed = df["completion_status"].astype(str) == "complete"
        end_present = df["end_time"].notna()
        null_consistency += int((completed != end_present).sum())
        null_consistency += int(
            (completed != df["end_event_identity"].map(_identity_is_present)).sum()
        )
        null_consistency += int((completed != df["duration"].notna()).sum())
    invalid_count = (
        len(missing)
        + len(extra)
        + int(column_order_mismatch)
        + invalid_statuses
        + null_consistency
        + duplicate_journeys
    )
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "event_time_to_event_row_contract",
        "event_time_to_event_row_contract",
        severity,
        severity,
        (
            "time-to-event row contract is valid"
            if not invalid_count
            else f"time-to-event row contract has {invalid_count} violation(s)"
        ),
        {
            "invalid_count": invalid_count,
            "missing_columns": missing,
            "extra_columns": extra,
            "column_order_mismatch": column_order_mismatch,
            "invalid_statuses": invalid_statuses,
            "null_consistency_violations": null_consistency,
            "duplicate_journeys": duplicate_journeys,
            "start_step": meta.start_step.key,
            "end_step": meta.end_step.key,
        },
    )


def _event_time_to_event_identity_check(df: pd.DataFrame) -> dict[str, str]:
    required = {
        "journey_id",
        "subject_identity",
        "start_event_identity",
        "start_time",
        "end_event_identity",
        "end_time",
    }
    invalid_count = len(required - set(df.columns))
    if required.issubset(df.columns):
        invalid_count += int(df["journey_id"].isna().sum())
        invalid_count += int((~df["subject_identity"].map(_identity_is_present)).sum())
        invalid_count += int((~df["start_event_identity"].map(_identity_is_present)).sum())
        invalid_count += int(df["start_time"].isna().sum())
        end_present = df["end_time"].notna()
        invalid_count += int(
            (end_present != df["end_event_identity"].map(_identity_is_present)).sum()
        )
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "event_time_to_event_identity",
        "event_time_to_event_identity",
        severity,
        severity,
        (
            "time-to-event identities are valid"
            if not invalid_count
            else f"time-to-event has {invalid_count} identity violation(s)"
        ),
        {"invalid_count": invalid_count},
    )


def _event_time_to_event_duration_check(df: pd.DataFrame) -> dict[str, str]:
    required = {"start_time", "end_time", "duration"}
    invalid_count = len(required - set(df.columns))
    negative_count = 0
    mismatch_count = 0
    if required.issubset(df.columns):
        start = pd.to_datetime(df["start_time"], errors="coerce", utc=True)
        end = pd.to_datetime(df["end_time"], errors="coerce", utc=True)
        duration = pd.to_timedelta(df["duration"], errors="coerce")
        complete = end.notna()
        negative_count = int((duration.loc[complete] < pd.Timedelta(0)).sum())
        mismatch_count = int(
            (duration.loc[complete] != (end.loc[complete] - start.loc[complete])).sum()
        )
        invalid_count += negative_count + mismatch_count
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "event_time_to_event_duration",
        "event_time_to_event_duration",
        severity,
        severity,
        (
            "time-to-event durations are exact and non-negative"
            if not invalid_count
            else f"time-to-event has {invalid_count} duration violation(s)"
        ),
        {
            "invalid_count": invalid_count,
            "negative_count": negative_count,
            "mismatch_count": mismatch_count,
        },
    )


def _event_row_contract_check(df: pd.DataFrame, frame: EventFrame) -> dict[str, str]:
    missing_columns = [column for column in _EVENT_JOURNEY_COLUMNS if column not in df.columns]
    expected_steps = tuple(step.key for step in frame.meta.pattern.steps)
    duplicate_rows = 0
    missing_step_rows = 0
    inconsistent_statuses = 0
    invalid_statuses = 0
    dense_suffix_violations = 0
    elapsed_violations = 0
    unknown_steps: list[str] = []
    if not missing_columns:
        duplicate_rows = int(df.duplicated(subset=["journey_id", "step_key"], keep=False).sum())
        observed_steps = set(df["step_key"].dropna().astype(str))
        unknown_steps = sorted(observed_steps - set(expected_steps))
        for _, journey in df.groupby("journey_id", dropna=False, sort=False):
            keys = tuple(journey["step_key"].astype(str))
            missing_step_rows += len(set(expected_steps) - set(keys))
            inconsistent_statuses += int(journey["completion_status"].nunique(dropna=False) != 1)
            statuses = set(journey["completion_status"].dropna().astype(str))
            invalid_statuses += len(statuses - {"complete", "incomplete", "coverage_censored"})
            ordered = journey.assign(
                __step_order=journey["step_key"].map(
                    {key: index for index, key in enumerate(expected_steps)}
                )
            ).sort_values("__step_order", kind="stable")
            present = ordered["occurred_at"].notna()
            if len(present):
                dense_suffix_violations += int(not bool(present.iloc[0]))
            if len(statuses) == 1:
                status = next(iter(statuses))
                dense_suffix_violations += int(status == "complete" and not bool(present.all()))
                dense_suffix_violations += int(
                    status in {"incomplete", "coverage_censored"} and bool(present.all())
                )
            missing_seen = (~present).cummax().shift(fill_value=False)
            if bool((present & missing_seen).any()):
                dense_suffix_violations += 1
            timestamps = pd.to_datetime(ordered["occurred_at"], errors="coerce", utc=True)
            elapsed_start = pd.to_timedelta(ordered["elapsed_from_start"], errors="coerce")
            elapsed_previous = pd.to_timedelta(ordered["elapsed_from_previous"], errors="coerce")
            if bool(present.any()):
                anchor = timestamps.loc[present].iloc[0]
                previous = anchor
                for row_position, is_present in enumerate(present):
                    if not bool(is_present):
                        elapsed_violations += int(not pd.isna(elapsed_start.iloc[row_position]))
                        elapsed_violations += int(not pd.isna(elapsed_previous.iloc[row_position]))
                        continue
                    current = timestamps.iloc[row_position]
                    elapsed_violations += int(elapsed_start.iloc[row_position] != current - anchor)
                    elapsed_violations += int(
                        elapsed_previous.iloc[row_position] != current - previous
                    )
                    previous = current
    invalid_count = (
        len(missing_columns)
        + duplicate_rows
        + missing_step_rows
        + inconsistent_statuses
        + invalid_statuses
        + dense_suffix_violations
        + elapsed_violations
        + len(unknown_steps)
    )
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "event_row_contract",
        "event_row_contract",
        severity,
        severity,
        (
            "journey row contract is valid"
            if not invalid_count
            else f"journey row contract has {invalid_count} violation(s)"
        ),
        {
            "invalid_count": invalid_count,
            "missing_columns": missing_columns,
            "duplicate_rows": duplicate_rows,
            "missing_step_rows": missing_step_rows,
            "inconsistent_statuses": inconsistent_statuses,
            "invalid_statuses": invalid_statuses,
            "dense_suffix_violations": dense_suffix_violations,
            "elapsed_violations": elapsed_violations,
            "unknown_steps": unknown_steps[:5],
            "expected_columns": list(_EVENT_JOURNEY_COLUMNS),
        },
    )


def _identity_is_present(value: object) -> bool:
    if not isinstance(value, tuple) or not value:
        return False
    return not any(pd.isna(component) for component in value)


def _event_identity_check(df: pd.DataFrame) -> dict[str, str]:
    required = {"journey_id", "subject_identity", "event_identity", "occurred_at"}
    if not required.issubset(df.columns):
        invalid_count = len(required - set(df.columns))
        null_journey_count = int("journey_id" not in df.columns)
        invalid_subject_count = int("subject_identity" not in df.columns)
        invalid_event_count = int(
            "event_identity" not in df.columns or "occurred_at" not in df.columns
        )
    else:
        null_journey_count = int(df["journey_id"].isna().sum())
        invalid_subject_count = int((~df["subject_identity"].map(_identity_is_present)).sum())
        occurrence_present = df["occurred_at"].notna()
        invalid_event_count = int(
            (occurrence_present & ~df["event_identity"].map(_identity_is_present)).sum()
        )
        invalid_event_count += int(
            (~occurrence_present & df["event_identity"].map(_identity_is_present)).sum()
        )
        invalid_count = null_journey_count + invalid_subject_count + invalid_event_count
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "event_identity",
        "event_identity",
        severity,
        severity,
        (
            "journey and occurrence identities are valid"
            if not invalid_count
            else f"{invalid_count} identity violation(s) detected"
        ),
        {
            "invalid_count": invalid_count,
            "null_journey_count": null_journey_count,
            "invalid_subject_count": invalid_subject_count,
            "invalid_event_count": invalid_event_count,
        },
    )


def _event_participant_check(df: pd.DataFrame, frame: EventFrame) -> dict[str, str]:
    expected_keys = {step.key for step in frame.meta.pattern.steps}
    endpoint_keys = set(frame.meta.role_endpoints)
    missing_endpoint_keys = sorted(expected_keys - endpoint_keys)
    mismatched_endpoint_keys = sorted(
        key
        for key, endpoint in frame.meta.role_endpoints.items()
        if key in expected_keys and endpoint != frame.meta.subject_entity_ref
    )
    subject_mismatch_count = 0
    if {"journey_id", "subject_identity"}.issubset(df.columns):
        for _, journey in df.groupby("journey_id", dropna=False, sort=False):
            subject_mismatch_count += int(journey["subject_identity"].nunique(dropna=False) != 1)
    invalid_count = (
        len(missing_endpoint_keys)
        + len(mismatched_endpoint_keys)
        + subject_mismatch_count
        + int(not frame.meta.subject_identity)
    )
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "event_participant",
        "event_participant",
        severity,
        severity,
        (
            "participant role bindings are consistent"
            if not invalid_count
            else f"{invalid_count} participant binding violation(s) detected"
        ),
        {
            "invalid_count": invalid_count,
            "missing_endpoint_keys": missing_endpoint_keys,
            "mismatched_endpoint_keys": mismatched_endpoint_keys,
            "subject_mismatch_count": subject_mismatch_count,
        },
    )


def _event_ordering_check(df: pd.DataFrame, frame: EventFrame) -> dict[str, str]:
    step_order = {step.key: index for index, step in enumerate(frame.meta.pattern.steps)}
    step_event = {step.key: step.event.path for step in frame.meta.pattern.steps}
    out_of_order_count = 0
    ambiguous_equal_time_count = 0
    reused_occurrence_count = 0
    if {"journey_id", "step_key", "event_identity", "occurred_at"}.issubset(df.columns):
        for _, journey in df.groupby("journey_id", dropna=False, sort=False):
            ordered = journey.assign(__step_order=journey["step_key"].map(step_order)).sort_values(
                "__step_order", kind="stable"
            )
            present = ordered.loc[ordered["occurred_at"].notna()]
            times = pd.to_datetime(present["occurred_at"], errors="coerce", utc=True)
            out_of_order_count += int((times.diff().dropna() < pd.Timedelta(0)).sum())
            present_event_refs = present["step_key"].map(step_event)
            ambiguous_equal_time_count += int(
                (times.eq(times.shift()) & present_event_refs.ne(present_event_refs.shift())).sum()
            )
            identity_keys = pd.Series(
                [
                    (step_event.get(str(row.step_key)), repr(row.event_identity))
                    for row in present.itertuples(index=False)
                ],
                index=present.index,
            )
            reused_occurrence_count += int(identity_keys.duplicated(keep=False).sum())
    invalid_count = out_of_order_count + ambiguous_equal_time_count + reused_occurrence_count
    severity = "blocking" if invalid_count else "ok"
    return _result(
        "event_ordering",
        "event_ordering",
        severity,
        severity,
        (
            "journey occurrence ordering is deterministic"
            if not invalid_count
            else f"{invalid_count} ordering violation(s) detected"
        ),
        {
            "invalid_count": invalid_count,
            "out_of_order_count": out_of_order_count,
            "ambiguous_equal_time_count": ambiguous_equal_time_count,
            "reused_occurrence_count": reused_occurrence_count,
        },
    )


def _quality_coverage_bound(value: object) -> pd.Timestamp | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC")


def _expected_coverage_basis(
    entries: tuple[EventInputCoverage, ...],
) -> str:
    bases = {entry.basis for entry in entries}
    if "unknown" in bases:
        return "unknown"
    if bases == {"observed_watermark"}:
        return "observed_watermark"
    if bases == {"declared_complete"}:
        return "declared_complete"
    return "mixed"


def _event_coverage_entry_issues(
    *,
    frame: EventFrame,
    coverage: EventInputCoverage,
) -> list[str]:
    issues: list[str] = []
    receipt = coverage.receipt
    receipt_bound = (
        _quality_coverage_bound(receipt.complete_through) if receipt is not None else None
    )
    required = _quality_coverage_bound(frame.meta.completion_through)
    if receipt is not None:
        if receipt_bound is None:
            issues.append("receipt_complete_through_invalid")
        if _quality_coverage_bound(receipt.observed_at) is None:
            issues.append("receipt_observed_at_invalid")
        if coverage.observed_complete_through != receipt.complete_through:
            issues.append("observed_complete_through_mismatch")
    elif coverage.observed_complete_through is not None:
        issues.append("observed_complete_through_without_receipt")

    if coverage.basis == "observed_watermark":
        if receipt is None:
            issues.append("observed_watermark_receipt_missing")
        elif required is None or receipt_bound is None or receipt_bound < required:
            issues.append("observed_watermark_bound_insufficient")
        if coverage.declaration_fingerprint is not None:
            issues.append("observed_watermark_has_declaration")
    elif coverage.basis == "declared_complete":
        matching_declarations = [
            declaration
            for declaration in frame.meta.completeness
            if coverage.event_ref.path in {event_ref.path for event_ref in declaration.inputs}
            and declaration.fingerprint == coverage.declaration_fingerprint
        ]
        if not matching_declarations:
            issues.append("declared_complete_declaration_missing")
        elif required is None or any(
            (bound := _quality_coverage_bound(declaration.through)) is None or bound < required
            for declaration in matching_declarations
        ):
            issues.append("declared_complete_bound_insufficient")
        elif coverage.declaration_rationale not in {
            declaration.rationale for declaration in matching_declarations
        }:
            issues.append("declared_complete_rationale_mismatch")
        if not coverage.declaration_rationale:
            issues.append("declared_complete_rationale_missing")
    elif coverage.declaration_fingerprint is not None or coverage.declaration_rationale is not None:
        issues.append("unknown_has_declaration")
    return issues


def _event_coverage_checks(frame: EventFrame) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    expected_refs = tuple(dict.fromkeys(step.event.path for step in frame.meta.pattern.steps))
    coverage_by_ref: dict[str, list[EventInputCoverage]] = {}
    for coverage_entry in frame.meta.input_coverage:
        coverage_by_ref.setdefault(coverage_entry.event_ref.path, []).append(coverage_entry)
    for event_ref in expected_refs:
        entries = coverage_by_ref.get(event_ref, [])
        coverage: EventInputCoverage | None = entries[0] if entries else None
        entry_count = len(entries)
        unknown = coverage is None or coverage.basis == "unknown"
        basis = coverage.basis if coverage is not None else "missing"
        evidence_issues = (
            _event_coverage_entry_issues(frame=frame, coverage=coverage)
            if coverage is not None
            else []
        )
        severity = (
            "blocking" if entry_count != 1 or evidence_issues else "warning" if unknown else "ok"
        )
        rows.append(
            _result(
                f"event_coverage:{event_ref}",
                "event_coverage",
                severity,
                severity,
                (
                    f"coverage metadata has {entry_count} entries for {event_ref}"
                    if entry_count != 1
                    else f"coverage is unknown for {event_ref}"
                    if unknown
                    else (f"coverage for {event_ref} is supported by {basis}")
                ),
                {
                    "event_ref": event_ref,
                    "basis": basis,
                    "coverage_entry_count": entry_count,
                    "unknown_count": int(unknown or bool(evidence_issues)),
                    "evidence_issues": evidence_issues,
                    "required_through": frame.meta.completion_through,
                    "observed_complete_through": (
                        coverage.observed_complete_through if coverage is not None else None
                    ),
                },
            )
        )
    expected_aggregate = _expected_coverage_basis(frame.meta.input_coverage)
    aggregate_valid = frame.meta.coverage_basis == expected_aggregate
    rows.append(
        _result(
            "event_coverage:aggregate",
            "event_coverage",
            "ok" if aggregate_valid else "blocking",
            "ok" if aggregate_valid else "blocking",
            (
                "aggregate coverage basis matches the per-Event evidence"
                if aggregate_valid
                else (
                    f"aggregate coverage basis is {frame.meta.coverage_basis!r}; "
                    f"expected {expected_aggregate!r}"
                )
            ),
            {
                "event_ref": None,
                "basis": frame.meta.coverage_basis,
                "expected_basis": expected_aggregate,
                "coverage_entry_count": len(frame.meta.input_coverage),
                "unknown_count": int(not aggregate_valid),
                "required_through": frame.meta.completion_through,
                "observed_complete_through": None,
            },
        )
    )
    for event_ref in sorted(set(coverage_by_ref) - set(expected_refs)):
        rows.append(
            _result(
                f"event_coverage:unexpected:{event_ref}",
                "event_coverage",
                "blocking",
                "blocking",
                f"coverage metadata references Event outside the pattern: {event_ref}",
                {
                    "event_ref": event_ref,
                    "basis": "unexpected",
                    "coverage_entry_count": len(coverage_by_ref[event_ref]),
                    "unknown_count": 1,
                    "required_through": frame.meta.completion_through,
                    "observed_complete_through": None,
                },
            )
        )
    return rows


def _event_declaration_check(frame: EventFrame) -> dict[str, str]:
    declared = [item for item in frame.meta.input_coverage if item.basis == "declared_complete"]
    count = len(declared)
    severity = "warning" if count else "ok"
    return _result(
        "declared_completeness_used",
        "declared_completeness_used",
        severity,
        severity,
        (
            f"{count} Event input(s) rely on caller-declared completeness"
            if count
            else "no caller completeness declaration was used"
        ),
        {
            "declared_input_count": count,
            "event_refs": [item.event_ref.path for item in declared],
        },
    )


def _event_censoring_check(df: pd.DataFrame) -> dict[str, str]:
    if {"journey_id", "completion_status"}.issubset(df.columns):
        censored_count = int(
            df.loc[df["completion_status"] == "coverage_censored", "journey_id"].nunique()
        )
    else:
        censored_count = 0
    severity = "warning" if censored_count else "ok"
    return _result(
        "event_censoring",
        "event_censoring",
        severity,
        severity,
        (
            f"{censored_count} journey(s) are coverage-censored"
            if censored_count
            else "no journey is coverage-censored"
        ),
        {"coverage_censored_count": censored_count},
    )
