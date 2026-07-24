"""Pure pandas quality checks for analysis frames."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
from typing import Any, Literal

import pandas as pd

from marivo.analysis.frames._meta_defaults import GRAIN_FREQ, normalize_coverage_buckets
from marivo.analysis.frames.event import EventFrame, EventInputCoverage
from marivo.analysis.frames.metric import MetricFrame

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
    axis = frame.meta.axes.get("time", {})
    if isinstance(axis, dict):
        return str(axis.get("field") or axis.get("column") or "time"), str(axis.get("grain", "day"))
    return "time", "day"


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
    dims = frame.meta.axes.get("dimensions")
    if isinstance(dims, list):
        return [
            str(dim.get("column") or dim.get("field"))
            for dim in dims
            if isinstance(dim, dict) and (dim.get("column") or dim.get("field"))
        ]
    columns: list[str] = []
    for axis in frame.meta.axes.values():
        if not isinstance(axis, dict) or axis.get("role") != "dimension":
            continue
        column = axis.get("column") or axis.get("field")
        if isinstance(column, str):
            columns.append(column)
    return columns


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
