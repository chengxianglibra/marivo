"""Certified temporal comparison alignment for Slice 3."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import pandas as pd

from marivo._temporal import (
    AlignmentEvidenceV1,
    BuiltinPeriodBindingV1,
    ComparisonTemporalContractV1,
    FrameTemporalContractV1,
    GregorianIsoResolver,
    PeriodBindingV1,
    PeriodCalendarSnapshotV1,
    PeriodProgressCoordinate,
    SemanticPeriodBindingV1,
    TemporalResolver,
    TemporalSnapshotStore,
    ref_factory_period_calendar,
)
from marivo.analysis.delta_math import compute_delta_columns
from marivo.analysis.errors import AlignmentFailedError, AlignmentPolicyNotApplicableError
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.policies import AlignmentPolicy

_TIME_KINDS = {"time_series", "panel"}
_MISSING = object()


@dataclass(frozen=True, slots=True)
class TemporalAlignmentResult:
    """Output of one admitted temporal pairing."""

    frame: pd.DataFrame
    target_binding: PeriodBindingV1 | None
    evidence: dict[str, object]
    cumulative_info: dict[str, int]


def _axis_columns(frame: MetricFrame) -> tuple[list[str], str | None]:
    dimensions: list[str] = []
    time_column: str | None = None
    for axis in frame.meta.axes.values():
        if not isinstance(axis, dict):
            continue
        column = axis.get("column")
        if not isinstance(column, str) or not column:
            continue
        if axis.get("role") == "dimension":
            dimensions.append(column)
        elif axis.get("role") == "time":
            time_column = column
    return sorted(set(dimensions)), time_column


def _value_column(frame: MetricFrame, data: pd.DataFrame, *, excluded: Iterable[str]) -> str:
    excluded_set = set(excluded) | {"period_key", "period_start", "period_end"}
    if "value" in data.columns and "value" not in excluded_set:
        return "value"
    measure = frame.meta.measure.get("name")
    if isinstance(measure, str) and measure in data.columns and measure not in excluded_set:
        return measure
    candidates = [str(column) for column in data.columns if str(column) not in excluded_set]
    if len(candidates) != 1:
        raise AlignmentFailedError(
            message="temporal alignment requires exactly one value column",
            expected="one metric value column beside declared dimensions and time axis",
            received=f"candidates={candidates!r}",
            context={"kind": "TemporalAlignmentValueColumnAmbiguous"},
        )
    return candidates[0]


def _contract(frame: MetricFrame) -> FrameTemporalContractV1:
    contract = getattr(frame.meta, "temporal_contract", None)
    if not isinstance(contract, FrameTemporalContractV1):
        raise AlignmentFailedError(
            message="temporal alignment requires a persisted frame temporal contract",
            expected="FrameTemporalContractV1 on both comparison inputs",
            received=f"frame={frame.ref}",
            context={"kind": "TemporalContractMissing", "frame_ref": frame.ref},
        )
    return contract


def _binding_key(binding: PeriodBindingV1 | None) -> tuple[object, ...] | None:
    if binding is None:
        return None
    return tuple(sorted(binding.model_dump(mode="json").items()))


def _authority_key(binding: PeriodBindingV1) -> tuple[object, ...]:
    """Return authority identity without the selected level name."""
    payload = binding.model_dump(mode="json")
    payload.pop("level_name", None)
    return tuple(sorted(payload.items()))


def _require_same_binding(
    current: PeriodBindingV1 | None,
    baseline: PeriodBindingV1 | None,
    *,
    purpose: str,
) -> PeriodBindingV1:
    if current is None or baseline is None:
        raise AlignmentPolicyNotApplicableError(
            message=f"{purpose} requires both frames to carry a period authority",
            expected="matching PeriodBindingV1 values",
            received=f"current={current!r}, baseline={baseline!r}",
            context={"kind": "TemporalAuthorityMissing", "purpose": purpose},
        )
    if _binding_key(current) != _binding_key(baseline):
        raise AlignmentPolicyNotApplicableError(
            message=f"{purpose} requires the same exact period authority",
            expected="matching calendar/algorithm, snapshot, level, and boundary timezone",
            received=f"current={current!r}, baseline={baseline!r}",
            context={"kind": "TemporalAuthorityMismatch", "purpose": purpose},
        )
    return current


def _resolver(
    binding: PeriodBindingV1,
    *,
    session: Any,
) -> tuple[GregorianIsoResolver | TemporalResolver, PeriodCalendarSnapshotV1 | None]:
    if isinstance(binding, BuiltinPeriodBindingV1):
        return GregorianIsoResolver(binding.boundary_timezone), None
    store = TemporalSnapshotStore(session.project_root)
    try:
        snapshot = store.load_exact(
            ref_factory_period_calendar(binding.calendar_ref),
            snapshot_digest=binding.snapshot_digest,
        )
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise AlignmentPolicyNotApplicableError(
            message="temporal alignment authority snapshot is unavailable",
            expected="the exact certified snapshot named by both frame contracts",
            received=(
                f"calendar={binding.calendar_ref!r}, snapshot_digest={binding.snapshot_digest!r}"
            ),
            context={
                "kind": "TemporalSnapshotUnavailable",
                "calendar_ref": binding.calendar_ref,
                "snapshot_digest": binding.snapshot_digest,
            },
        ) from exc
    return TemporalResolver(snapshot), snapshot


def _local_datetime(value: object, *, timezone: str) -> datetime:
    if value is None:
        raise AlignmentFailedError(
            message="temporal alignment encountered a null time value",
            context={"kind": "TemporalAlignmentTimeValueInvalid", "value": None},
        )
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            raise AlignmentFailedError(
                message="temporal alignment encountered a null time value",
                context={"kind": "TemporalAlignmentTimeValueInvalid", "value": None},
            )
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=None)
        return value.astimezone(ZoneInfo(timezone)).replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, str):
        try:
            parsed = pd.Timestamp(value)
        except (TypeError, ValueError) as exc:
            raise AlignmentFailedError(
                message="temporal alignment encountered an invalid time value",
                received=repr(value),
                context={"kind": "TemporalAlignmentTimeValueInvalid", "value": value},
            ) from exc
        return _local_datetime(parsed, timezone=timezone)
    raise AlignmentFailedError(
        message="temporal alignment encountered an unsupported time value",
        received=repr(value),
        context={"kind": "TemporalAlignmentTimeValueInvalid"},
    )


def _local_date(value: object, *, timezone: str) -> date:
    return _local_datetime(value, timezone=timezone).date()


def _local_civil_dates(series: pd.Series, *, report_tz: str) -> pd.Series:
    """Convert an endpoint series to report-timezone civil dates.

    All-history cumulative pairing needs the same local-date key as the
    temporal policies, but it must remain independent of the removed
    file-backed holiday-calendar implementation.  Keep parsing failures typed
    so malformed persisted endpoints cannot leak pandas exceptions.
    """
    timezone = ZoneInfo(report_tz)
    if isinstance(series.dtype, pd.DatetimeTZDtype):
        parsed = pd.to_datetime(series, errors="coerce")
        if parsed.isna().any():
            raise AlignmentFailedError(
                message=f"failed to parse date values with session timezone {report_tz!r}",
                context={
                    "kind": "TemporalAlignmentDateParseFailed",
                    "session_timezone": report_tz,
                },
            )
        return parsed.dt.tz_convert(timezone).dt.date
    if pd.api.types.is_datetime64_any_dtype(series):
        parsed = pd.to_datetime(series, errors="coerce")
        if parsed.isna().any():
            raise AlignmentFailedError(
                message=f"failed to parse date values with session timezone {report_tz!r}",
                context={
                    "kind": "TemporalAlignmentDateParseFailed",
                    "session_timezone": report_tz,
                },
            )
        return parsed.dt.date

    def coerce(value: object) -> date:
        missing = False
        if value is None:
            missing = True
        elif not isinstance(value, (date, datetime, pd.Timestamp)):
            try:
                missing = bool(pd.isna(cast("Any", value)))
            except (TypeError, ValueError):
                missing = False
        if missing:
            raise AlignmentFailedError(
                message=f"failed to parse date value {value!r}",
                context={"kind": "TemporalAlignmentDateParseFailed", "value": value},
            )
        if isinstance(value, pd.Timestamp):
            timestamp = value
        elif isinstance(value, datetime):
            timestamp = pd.Timestamp(value)
        elif isinstance(value, date):
            return value
        elif isinstance(value, str):
            stripped = value.strip()
            try:
                parsed_date = date.fromisoformat(stripped)
            except ValueError:
                parsed_date = None
            if parsed_date is not None and parsed_date.isoformat() == stripped:
                return parsed_date
            try:
                timestamp = pd.to_datetime(stripped, utc=True, errors="raise")
            except (TypeError, ValueError) as exc:
                raise AlignmentFailedError(
                    message=f"failed to parse date value {value!r}",
                    context={"kind": "TemporalAlignmentDateParseFailed", "value": value},
                ) from exc
            timestamp = pd.Timestamp(timestamp)
        else:
            raise AlignmentFailedError(
                message=f"unsupported date value {value!r}",
                context={"kind": "TemporalAlignmentDateParseFailed", "value": str(value)},
            )
        if pd.isna(timestamp):
            raise AlignmentFailedError(
                message=f"failed to parse date value {value!r}",
                context={"kind": "TemporalAlignmentDateParseFailed", "value": value},
            )
        if timestamp.tzinfo is None:
            return timestamp.date()
        return timestamp.tz_convert(timezone).date()

    return series.map(coerce)


def _binding_for_grain(
    grain: Any,
    *,
    frame_contract: FrameTemporalContractV1,
    session: Any,
) -> tuple[
    PeriodBindingV1, GregorianIsoResolver | TemporalResolver, PeriodCalendarSnapshotV1 | None
]:
    if getattr(grain, "kind", None) == "semantic":
        calendar = getattr(grain, "calendar", None)
        level = getattr(grain, "level", None)
        if calendar is None or not isinstance(level, str):
            raise AlignmentFailedError(
                message="semantic alignment grain is incomplete",
                context={"kind": "TemporalAlignmentGrainInvalid"},
            )
        observation = frame_contract.observation_period
        binding: PeriodBindingV1
        if (
            isinstance(observation, SemanticPeriodBindingV1)
            and observation.calendar_ref == calendar.path
        ):
            binding = SemanticPeriodBindingV1(
                calendar_ref=calendar.path,
                snapshot_digest=observation.snapshot_digest,
                level_name=level,
            )
        else:
            scope = frame_contract.time_scope
            if (
                scope is None
                or scope.kind != "calendar_period"
                or scope.calendar_ref != calendar.path
                or not scope.snapshot_digest
            ):
                raise AlignmentPolicyNotApplicableError(
                    message="semantic alignment grain requires a certified calendar scope",
                    expected=f"calendar scope for {calendar.path!r} with snapshot digest",
                    received=f"observation={observation!r}, scope={scope!r}",
                    context={"kind": "AlignmentGrainAuthorityMismatch"},
                )
            binding = SemanticPeriodBindingV1(
                calendar_ref=calendar.path,
                snapshot_digest=scope.snapshot_digest,
                level_name=level,
            )
        resolver, snapshot = _resolver(binding, session=session)
        return binding, resolver, snapshot
    unit = getattr(grain, "unit", None)
    count = getattr(grain, "count", None)
    if not isinstance(unit, str) or count != 1:
        raise AlignmentPolicyNotApplicableError(
            message="built-in alignment grain must be one certified period level",
            expected="a built-in grain with count=1",
            received=repr(grain),
            context={"kind": "AlignmentGrainInvalid"},
        )
    expected_level = unit
    timezone = frame_contract.display_timezone
    binding = BuiltinPeriodBindingV1(level_name=expected_level, boundary_timezone=timezone)
    resolver, snapshot = _resolver(binding, session=session)
    return binding, resolver, snapshot


def _target_period(
    frame: MetricFrame,
    *,
    binding: PeriodBindingV1,
    resolver: GregorianIsoResolver | TemporalResolver,
    target_level: str,
    timezone: str,
) -> Any:
    contract = _contract(frame)
    scope = contract.time_scope
    if scope is None:
        raise AlignmentPolicyNotApplicableError(
            message="temporal alignment requires a persisted frame scope",
            expected="one non-empty TimeScopeContractV1",
            received=f"frame={frame.ref}",
            context={"kind": "TemporalScopeMissing"},
        )
    start_date = _local_date(scope.start, timezone=timezone)
    end_local = _local_datetime(scope.end, timezone=timezone)
    end_date = end_local.date()
    end_probe_date = (end_local - timedelta(microseconds=1)).date()
    try:
        first = resolver.period_on(target_level, start_date)
        last = resolver.period_on(target_level, end_probe_date)
    except (KeyError, ValueError) as exc:
        raise AlignmentPolicyNotApplicableError(
            message="temporal alignment scope is outside certified target coverage",
            expected="both scope bounds inside one certified target period",
            received=f"start={start_date}, end={end_date}",
            context={"kind": "TemporalTargetCoverageMissing"},
        ) from exc
    if first.key != last.key:
        raise AlignmentPolicyNotApplicableError(
            message="temporal alignment requires exactly one containing target period",
            expected="scope start and exclusive end resolve to one target period",
            received=f"start_period={first.key!r}, end_period={last.key!r}",
            context={
                "kind": "TemporalTargetPeriodCountMismatch",
                "target_level": target_level,
                "frame_ref": frame.ref,
            },
        )
    return first


def _target_for_day_of_week(
    frame: MetricFrame,
    *,
    within: Any,
    session: Any,
) -> tuple[PeriodBindingV1, GregorianIsoResolver | TemporalResolver, Any, str]:
    contract = _contract(frame)
    binding, resolver, _snapshot = _binding_for_grain(
        within,
        frame_contract=contract,
        session=session,
    )
    if isinstance(binding, BuiltinPeriodBindingV1):
        timezone = binding.boundary_timezone
    else:
        assert _snapshot is not None
        timezone = _snapshot.boundary_timezone
    target_level = binding.level_name
    target = _target_period(
        frame,
        binding=binding,
        resolver=resolver,
        target_level=target_level,
        timezone=timezone,
    )
    return binding, resolver, target, timezone


def _target_for_progress(
    frame: MetricFrame,
    *,
    session: Any,
) -> tuple[PeriodBindingV1, GregorianIsoResolver | TemporalResolver, Any, str]:
    contract = _contract(frame)
    binding = contract.cumulative_reset_period
    if binding is None and contract.time_scope is not None:
        scope = contract.time_scope
        if scope.kind == "calendar_period" and scope.calendar_ref and scope.snapshot_digest:
            binding = SemanticPeriodBindingV1(
                calendar_ref=scope.calendar_ref,
                snapshot_digest=scope.snapshot_digest,
                level_name=cast("str", scope.level),
            )
    if binding is None:
        raise AlignmentPolicyNotApplicableError(
            message="period_progress requires a cumulative reset or exact period scope",
            expected="one target PeriodBindingV1 per frame",
            received=f"frame={frame.ref}",
            context={"kind": "TemporalTargetBindingMissing"},
        )
    resolver, snapshot = _resolver(binding, session=session)
    if isinstance(binding, BuiltinPeriodBindingV1):
        timezone = binding.boundary_timezone
    else:
        assert snapshot is not None
        timezone = snapshot.boundary_timezone
    target = _target_period(
        frame,
        binding=binding,
        resolver=resolver,
        target_level=binding.level_name,
        timezone=timezone,
    )
    return binding, resolver, target, timezone


def _require_progress_source_authority(
    source: PeriodBindingV1,
    target: PeriodBindingV1,
    *,
    target_resolver: GregorianIsoResolver | TemporalResolver,
    source_level: str,
) -> None:
    """Reject coarser source buckets that a target authority cannot certify."""
    if (
        source_level == "day"
        and isinstance(source, BuiltinPeriodBindingV1)
        and isinstance(target, SemanticPeriodBindingV1)
    ):
        assert isinstance(target_resolver, TemporalResolver)
        if source.boundary_timezone != target_resolver.snapshot.boundary_timezone:
            raise AlignmentPolicyNotApplicableError(
                message="period_progress day source and semantic target use different boundary timezones",
                expected="matching civil-day boundary timezone",
                received=(
                    f"source={source.boundary_timezone!r}, "
                    f"target={target_resolver.snapshot.boundary_timezone!r}"
                ),
                context={"kind": "TemporalAuthorityTimezoneMismatch"},
            )
        return
    if type(source) is not type(target) or _authority_key(source) != _authority_key(target):
        raise AlignmentPolicyNotApplicableError(
            message="period_progress requires source and target period authority to match",
            expected="the same built-in authority or the same semantic calendar snapshot",
            received=f"source={source!r}, target={target!r}",
            context={
                "kind": "TemporalAuthorityMismatch",
                "source_level": source_level,
                "source": source.model_dump(mode="json"),
                "target": target.model_dump(mode="json"),
            },
        )


def _dimension_key(row: Mapping[Any, Any], dimensions: list[str]) -> tuple[object, ...]:
    return tuple(row.get(column) for column in dimensions)


def _row_key_text(key: object) -> str:
    return json.dumps(key, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _prepare_frame(frame: MetricFrame) -> tuple[pd.DataFrame, list[str], str | None, str]:
    data = frame._dataframe_copy()
    dimensions, time_column = _axis_columns(frame)
    value_column = _value_column(frame, data, excluded=(*dimensions, time_column or ""))
    return data, dimensions, time_column, value_column


def _row_coordinate(
    row: Mapping[Any, Any],
    *,
    frame: MetricFrame,
    dimensions: list[str],
    coordinate: object,
    current: bool,
    time_column: str | None,
    value_column: str,
) -> tuple[tuple[object, ...], dict[str, object]]:
    dimension_key = _dimension_key(row, dimensions)
    key = (*dimension_key, coordinate)
    raw_time = row.get(time_column) if time_column is not None else None
    payload: dict[str, object] = {
        **{column: row.get(column) for column in dimensions},
        "_coord": coordinate,
        "_row_time": raw_time,
        "_value": row.get(value_column),
    }
    if current:
        payload["_current_key"] = key
    else:
        payload["_baseline_key"] = key
    return key, payload


def _pair_maps(
    current_rows: dict[tuple[object, ...], dict[str, object]],
    baseline_rows: dict[tuple[object, ...], dict[str, object]],
    *,
    policy: AlignmentPolicy,
    current_key_text: Any,
    baseline_key_text: Any,
) -> TemporalAlignmentResult:
    current_keys = set(current_rows)
    baseline_keys = set(baseline_rows)
    paired_keys = current_keys & baseline_keys
    current_only = current_keys - paired_keys
    baseline_only = baseline_keys - paired_keys
    unmatched = len(current_only) + len(baseline_only)
    unmatched_mode = getattr(policy, "unmatched", "fail")
    if unmatched and unmatched_mode == "fail":
        raise AlignmentFailedError(
            message=f"{policy.kind} alignment has unmatched coordinates",
            expected="every admitted coordinate to exist on both sides",
            received=(f"current_only={len(current_only)}, baseline_only={len(baseline_only)}"),
            context={
                "kind": "TemporalAlignmentUnmatched",
                "alignment_kind": policy.kind,
                "current_only": tuple(sorted(map(_row_key_text, current_only)))[:20],
                "baseline_only": tuple(sorted(map(_row_key_text, baseline_only)))[:20],
            },
        )
    rows: list[dict[str, object]] = []
    for key in sorted(paired_keys, key=_row_key_text):
        current = current_rows[key]
        baseline = baseline_rows[key]
        rows.append(
            {
                **{column: current[column] for column in current_key_text},
                "align_key": _row_key_text(key),
                "align_quality": "exact",
                "presence_status": "matched",
                "bucket_start_a": current["_row_time"],
                "bucket_start_b": baseline["_row_time"],
                "current": current["_value"],
                "baseline": baseline["_value"],
            }
        )
    output = pd.DataFrame(rows)
    if output.empty:
        output = pd.DataFrame(
            columns=[
                *current_key_text,
                "align_key",
                "align_quality",
                "presence_status",
                "bucket_start_a",
                "bucket_start_b",
                "current",
                "baseline",
            ]
        )
    output = compute_delta_columns(output)
    dropped = unmatched if unmatched_mode == "drop" else 0
    evidence: dict[str, object] = {
        "candidate_current_points": len(current_keys),
        "candidate_baseline_points": len(baseline_keys),
        "paired_points": len(paired_keys),
        "current_only_points": len(current_only),
        "baseline_only_points": len(baseline_only),
        "unmatched_points": unmatched,
        "dropped_points": dropped,
        "dropped_reason": "unmatched_coordinate" if dropped else None,
        "execution_path": "local",
        "backend_optimized": False,
    }
    return TemporalAlignmentResult(
        frame=output.reset_index(drop=True),
        target_binding=None,
        evidence=evidence,
        cumulative_info={
            "dropped_rows_a": len(current_only),
            "dropped_rows_b": len(baseline_only),
            "fallback_rows": 0,
        },
    )


def _day_of_week_alignment(
    current: MetricFrame,
    baseline: MetricFrame,
    *,
    policy: AlignmentPolicy,
    session: Any,
) -> TemporalAlignmentResult:
    if current.meta.semantic_kind != baseline.meta.semantic_kind:
        raise AlignmentPolicyNotApplicableError(
            message="day_of_week alignment requires matching frame shapes",
            context={"kind": "TemporalShapeMismatch"},
        )
    if (
        current.meta.semantic_kind not in _TIME_KINDS
        or baseline.meta.semantic_kind not in _TIME_KINDS
    ):
        raise AlignmentPolicyNotApplicableError(
            message="day_of_week alignment requires time-series or panel frames",
            context={"kind": "AlignmentPolicyNotApplicable", "alignment_kind": policy.kind},
        )
    dimensions, current_time = _axis_columns(current)
    baseline_dimensions, baseline_time = _axis_columns(baseline)
    if current_time is None or baseline_time is None or dimensions != baseline_dimensions:
        raise AlignmentPolicyNotApplicableError(
            message="day_of_week alignment requires matching declared time and dimension axes",
            context={"kind": "TemporalAxisMismatch"},
        )
    source_current = _contract(current).observation_period
    source_baseline = _contract(baseline).observation_period
    if (
        not isinstance(source_current, (BuiltinPeriodBindingV1, SemanticPeriodBindingV1))
        or source_current.level_name != "day"
    ):
        raise AlignmentPolicyNotApplicableError(
            message="day_of_week alignment requires an effective day grain",
            expected="a built-in or semantic PeriodBindingV1(level_name='day')",
            received=repr(source_current),
            context={"kind": "TemporalSourceGrainUnsupported"},
        )
    if (
        not isinstance(source_baseline, (BuiltinPeriodBindingV1, SemanticPeriodBindingV1))
        or source_baseline.level_name != "day"
    ):
        raise AlignmentPolicyNotApplicableError(
            message="day_of_week alignment requires an effective day grain on both frames",
            received=repr(source_baseline),
            context={"kind": "TemporalSourceGrainUnsupported"},
        )
    _require_same_binding(source_current, source_baseline, purpose="day_of_week source")
    policy_any = cast("Any", policy)
    current_binding, current_resolver, current_target, current_tz = _target_for_day_of_week(
        current,
        within=policy_any.within,
        session=session,
    )
    baseline_binding, baseline_resolver, baseline_target, baseline_tz = _target_for_day_of_week(
        baseline,
        within=policy_any.within,
        session=session,
    )
    _require_same_binding(current_binding, baseline_binding, purpose="day_of_week")
    if current_target.level_name != baseline_target.level_name:
        raise AlignmentPolicyNotApplicableError(
            message="day_of_week alignment requires one target level on both sides",
            context={"kind": "TemporalTargetLevelMismatch"},
        )
    _require_progress_source_authority(
        source_current,
        current_binding,
        target_resolver=current_resolver,
        source_level="day",
    )
    current_data, _current_dimensions, _current_time, current_value = _prepare_frame(current)
    baseline_data, _baseline_dimensions, _baseline_time, baseline_value = _prepare_frame(baseline)
    current_rows: dict[tuple[object, ...], dict[str, object]] = {}
    baseline_rows: dict[tuple[object, ...], dict[str, object]] = {}
    for row in current_data.to_dict("records"):
        local_day = _local_date(row[current_time], timezone=current_tz)
        target = current_resolver.period_on(current_target.level_name, local_day)
        if target.key != current_target.key:
            raise AlignmentPolicyNotApplicableError(
                message="day_of_week frame contains more than one target period",
                context={"kind": "TemporalTargetPeriodCountMismatch", "frame_ref": current.ref},
            )
        coordinate = (local_day.isoweekday(), (local_day - target.start_date).days // 7)
        key, payload = _row_coordinate(
            row,
            frame=current,
            dimensions=dimensions,
            coordinate=coordinate,
            current=True,
            time_column=current_time,
            value_column=current_value,
        )
        if key in current_rows:
            raise AlignmentFailedError(
                message="day_of_week alignment found duplicate weekday occurrence coordinates",
                context={"kind": "TemporalAlignmentDuplicateCoordinate", "coordinate": coordinate},
            )
        current_rows[key] = payload
    for row in baseline_data.to_dict("records"):
        local_day = _local_date(row[baseline_time], timezone=baseline_tz)
        target = baseline_resolver.period_on(baseline_target.level_name, local_day)
        if target.key != baseline_target.key:
            raise AlignmentPolicyNotApplicableError(
                message="day_of_week frame contains more than one target period",
                context={"kind": "TemporalTargetPeriodCountMismatch", "frame_ref": baseline.ref},
            )
        coordinate = (local_day.isoweekday(), (local_day - target.start_date).days // 7)
        key, payload = _row_coordinate(
            row,
            frame=baseline,
            dimensions=dimensions,
            coordinate=coordinate,
            current=False,
            time_column=baseline_time,
            value_column=baseline_value,
        )
        if key in baseline_rows:
            raise AlignmentFailedError(
                message="day_of_week alignment found duplicate weekday occurrence coordinates",
                context={"kind": "TemporalAlignmentDuplicateCoordinate", "coordinate": coordinate},
            )
        baseline_rows[key] = payload
    result = _pair_maps(
        current_rows,
        baseline_rows,
        policy=policy,
        current_key_text=dimensions,
        baseline_key_text=dimensions,
    )
    return TemporalAlignmentResult(
        frame=result.frame,
        target_binding=current_binding,
        evidence=result.evidence,
        cumulative_info=result.cumulative_info,
    )


def _period_progress_for_row(
    row: Mapping[Any, Any],
    *,
    resolver: GregorianIsoResolver | TemporalResolver,
    source_level: str,
    target: Any,
    timezone: str,
    time_column: str,
    cumulative: bool,
) -> tuple[object, object]:
    if cumulative:
        value = row.get("evaluation_end")
        if value is None:
            raise AlignmentFailedError(
                message="period_progress cumulative scalar requires evaluation_end",
                context={"kind": "CumulativeEvaluationEndMissing"},
            )
        local = _local_datetime(value, timezone=timezone)
        coordinate = PeriodProgressCoordinate(
            day_ordinal=(local.date() - target.start_date).days,
            microseconds_of_day=(
                (local.hour * 3600 + local.minute * 60 + local.second) * 1_000_000
                + local.microsecond
            ),
        )
        return (coordinate.day_ordinal, coordinate.microseconds_of_day), local.date().isoformat()
    local = _local_datetime(row[time_column], timezone=timezone)
    if source_level == "day":
        coordinate = PeriodProgressCoordinate(day_ordinal=(local.date() - target.start_date).days)
        return (coordinate.day_ordinal, coordinate.microseconds_of_day), local.date()
    key_value = row.get("period_key", _MISSING)
    if key_value is _MISSING:
        try:
            key_value = resolver.period_on(source_level, local.date()).key
        except (KeyError, ValueError, TypeError) as exc:
            raise AlignmentPolicyNotApplicableError(
                message="period_progress could not resolve a source period",
                expected="every source time value to resolve in the certified authority",
                received=f"level={source_level!r}, local_day={local.date().isoformat()}",
                context={
                    "kind": "TemporalSourcePeriodMissing",
                    "source_level": source_level,
                    "local_day": local.date().isoformat(),
                },
            ) from exc
    try:
        source_period = resolver.period(source_level, cast("Any", key_value))
        ordinal = resolver.ordinal_within(source_level, source_period.key, target.level_name)
    except (KeyError, ValueError, TypeError) as exc:
        raise AlignmentPolicyNotApplicableError(
            message="period_progress source period is not contained by the target period",
            expected="a certified source-to-target containment edge",
            received=(
                f"source={source_level}:{key_value!r}, target={target.level_name}:{target.key!r}"
            ),
            context={
                "kind": "TemporalContainmentMissing",
                "source_level": source_level,
                "source_key": key_value,
                "target_level": target.level_name,
                "target_key": target.key,
            },
        ) from exc
    return (ordinal, 0), source_period.key


def _period_progress_alignment(
    current: MetricFrame,
    baseline: MetricFrame,
    *,
    policy: AlignmentPolicy,
    session: Any,
) -> TemporalAlignmentResult:
    if current.meta.semantic_kind not in {
        "scalar",
        "time_series",
        "panel",
    } or baseline.meta.semantic_kind not in {
        "scalar",
        "time_series",
        "panel",
    }:
        raise AlignmentPolicyNotApplicableError(
            message="period_progress alignment is not admitted for these frame shapes",
            context={"kind": "AlignmentPolicyNotApplicable", "alignment_kind": policy.kind},
        )
    if current.meta.semantic_kind == "scalar" or baseline.meta.semantic_kind == "scalar":
        if current.meta.semantic_kind != "scalar" or baseline.meta.semantic_kind != "scalar":
            raise AlignmentPolicyNotApplicableError(
                message="period_progress scalar admission requires scalar frames on both sides",
                context={"kind": "TemporalShapeMismatch"},
            )
        if current.meta.cumulative is None or baseline.meta.cumulative is None:
            raise AlignmentPolicyNotApplicableError(
                message="period_progress scalar admission requires cumulative frames",
                context={"kind": "TemporalCumulativeRequired"},
            )
    current_binding, current_resolver, current_target, current_tz = _target_for_progress(
        current, session=session
    )
    baseline_binding, baseline_resolver, baseline_target, baseline_tz = _target_for_progress(
        baseline, session=session
    )
    _require_same_binding(current_binding, baseline_binding, purpose="period_progress")
    cumulative = current.meta.semantic_kind == "scalar"
    dimensions, current_time = _axis_columns(current)
    baseline_dimensions, baseline_time = _axis_columns(baseline)
    if dimensions != baseline_dimensions:
        raise AlignmentPolicyNotApplicableError(
            message="period_progress panel frames require identical declared dimensions",
            context={"kind": "TemporalAxisMismatch"},
        )
    if not cumulative and (current_time is None or baseline_time is None):
        raise AlignmentPolicyNotApplicableError(
            message="period_progress time-series admission requires declared time axes",
            context={"kind": "TemporalAxisMissing"},
        )
    current_source = _contract(current).observation_period
    baseline_source = _contract(baseline).observation_period
    if not cumulative:
        if not isinstance(
            current_source, (BuiltinPeriodBindingV1, SemanticPeriodBindingV1)
        ) or not isinstance(baseline_source, (BuiltinPeriodBindingV1, SemanticPeriodBindingV1)):
            raise AlignmentPolicyNotApplicableError(
                message="period_progress requires observed source grains",
                context={"kind": "TemporalSourceGrainMissing"},
            )
        if _binding_key(current_source) != _binding_key(baseline_source):
            raise AlignmentPolicyNotApplicableError(
                message="period_progress requires the same effective source grain",
                context={"kind": "TemporalSourceGrainMismatch"},
            )
        source_level = current_source.level_name
        if source_level in {"second", "minute", "hour"}:
            raise AlignmentPolicyNotApplicableError(
                message="period_progress rejects sub-day source grains",
                context={"kind": "TemporalSourceGrainUnsupported", "source_level": source_level},
            )
        _require_progress_source_authority(
            current_source,
            current_binding,
            target_resolver=current_resolver,
            source_level=source_level,
        )
    else:
        source_level = "scalar"
    current_data, _current_dimensions, _current_time, current_value = _prepare_frame(current)
    baseline_data, _baseline_dimensions, _baseline_time, baseline_value = _prepare_frame(baseline)
    current_rows: dict[tuple[object, ...], dict[str, object]] = {}
    baseline_rows: dict[tuple[object, ...], dict[str, object]] = {}
    for row in current_data.to_dict("records"):
        coordinate, source_key = _period_progress_for_row(
            row,
            resolver=current_resolver,
            source_level=source_level,
            target=current_target,
            timezone=current_tz,
            time_column=current_time or "",
            cumulative=cumulative,
        )
        key, payload = _row_coordinate(
            row,
            frame=current,
            dimensions=dimensions,
            coordinate=coordinate,
            current=True,
            time_column=current_time,
            value_column=current_value,
        )
        payload["_source_key"] = source_key
        if key in current_rows:
            raise AlignmentFailedError(
                message="period_progress found duplicate progress coordinates",
                context={"kind": "TemporalAlignmentDuplicateCoordinate"},
            )
        current_rows[key] = payload
    for row in baseline_data.to_dict("records"):
        coordinate, source_key = _period_progress_for_row(
            row,
            resolver=baseline_resolver,
            source_level=source_level,
            target=baseline_target,
            timezone=baseline_tz,
            time_column=baseline_time or "",
            cumulative=cumulative,
        )
        key, payload = _row_coordinate(
            row,
            frame=baseline,
            dimensions=dimensions,
            coordinate=coordinate,
            current=False,
            time_column=baseline_time,
            value_column=baseline_value,
        )
        payload["_source_key"] = source_key
        if key in baseline_rows:
            raise AlignmentFailedError(
                message="period_progress found duplicate progress coordinates",
                context={"kind": "TemporalAlignmentDuplicateCoordinate"},
            )
        baseline_rows[key] = payload
    result = _pair_maps(
        current_rows,
        baseline_rows,
        policy=policy,
        current_key_text=dimensions,
        baseline_key_text=dimensions,
    )
    return TemporalAlignmentResult(
        frame=result.frame,
        target_binding=current_binding,
        evidence=result.evidence,
        cumulative_info=result.cumulative_info,
    )


def _period_correspondence_alignment(
    current: MetricFrame,
    baseline: MetricFrame,
    *,
    policy: AlignmentPolicy,
    session: Any,
) -> TemporalAlignmentResult:
    if current.meta.semantic_kind != baseline.meta.semantic_kind:
        raise AlignmentPolicyNotApplicableError(
            message="period_correspondence alignment requires matching frame shapes",
            context={"kind": "TemporalShapeMismatch"},
        )
    if (
        current.meta.semantic_kind not in _TIME_KINDS
        or baseline.meta.semantic_kind not in _TIME_KINDS
    ):
        raise AlignmentPolicyNotApplicableError(
            message="period_correspondence alignment requires time-series or panel frames",
            context={"kind": "AlignmentPolicyNotApplicable", "alignment_kind": policy.kind},
        )
    current_contract = _contract(current)
    baseline_contract = _contract(baseline)
    current_binding = current_contract.observation_period
    baseline_binding = baseline_contract.observation_period
    binding = _require_same_binding(
        current_binding,
        baseline_binding,
        purpose="period_correspondence",
    )
    if not isinstance(binding, SemanticPeriodBindingV1):
        raise AlignmentPolicyNotApplicableError(
            message="period_correspondence requires a semantic observation grain",
            context={"kind": "TemporalSemanticAuthorityRequired"},
        )
    resolver, snapshot = _resolver(binding, session=session)
    assert snapshot is not None
    policy_any = cast("Any", policy)
    correspondence_name = cast("str", policy_any.correspondence)
    records = tuple(item for item in snapshot.correspondences if item.name == correspondence_name)
    if not records:
        raise AlignmentPolicyNotApplicableError(
            message=f"correspondence {correspondence_name!r} is not certified",
            context={
                "kind": "TemporalCorrespondenceMissing",
                "correspondence": correspondence_name,
            },
        )
    level = records[0].level_name
    if binding.level_name != level:
        raise AlignmentPolicyNotApplicableError(
            message="period_correspondence requires the frame grain to equal the correspondence level",
            expected=level,
            received=binding.level_name,
            context={"kind": "TemporalCorrespondenceLevelMismatch"},
        )
    dimensions, current_time = _axis_columns(current)
    baseline_dimensions, baseline_time = _axis_columns(baseline)
    if dimensions != baseline_dimensions or current_time is None or baseline_time is None:
        raise AlignmentPolicyNotApplicableError(
            message="period_correspondence requires matching time and dimension axes",
            context={"kind": "TemporalAxisMismatch"},
        )
    current_data, _current_dimensions, _current_time, current_value = _prepare_frame(current)
    baseline_data, _baseline_dimensions, _baseline_time, baseline_value = _prepare_frame(baseline)
    for label, data in (("current", current_data), ("baseline", baseline_data)):
        if "is_complete" not in data.columns or not bool(data["is_complete"].all()):
            raise AlignmentPolicyNotApplicableError(
                message=f"period_correspondence requires complete {label} periods",
                context={"kind": "TemporalPartialPeriod", "frame": label},
            )

    def period_key(row: Mapping[Any, Any], time_column: str) -> object:
        raw = row.get("period_key", _MISSING)
        if raw is not _MISSING:
            return raw
        local_day = _local_date(row[time_column], timezone=snapshot.boundary_timezone)
        try:
            return resolver.period_on(level, local_day).key
        except (KeyError, ValueError, TypeError) as exc:
            raise AlignmentPolicyNotApplicableError(
                message="period_correspondence could not resolve a candidate period",
                expected="every time value to resolve in the certified correspondence level",
                received=f"level={level!r}, local_day={local_day.isoformat()}",
                context={
                    "kind": "TemporalCorrespondencePeriodMissing",
                    "level": level,
                    "local_day": local_day.isoformat(),
                },
            ) from exc

    baseline_rows: dict[tuple[object, ...], dict[str, object]] = {}
    for row in baseline_data.to_dict("records"):
        key_value = period_key(row, baseline_time)
        key = (*_dimension_key(row, dimensions), key_value)
        if key in baseline_rows:
            raise AlignmentFailedError(
                message="period_correspondence found duplicate period coordinates",
                context={"kind": "TemporalAlignmentDuplicateCoordinate"},
            )
        _key, payload = _row_coordinate(
            row,
            frame=baseline,
            dimensions=dimensions,
            coordinate=key_value,
            current=False,
            time_column=baseline_time,
            value_column=baseline_value,
        )
        baseline_rows[key] = payload

    current_rows: dict[tuple[object, ...], dict[str, object]] = {}
    missing_mapping = 0
    for row in current_data.to_dict("records"):
        key_value = period_key(row, current_time)
        try:
            mapped = resolver.correspondence(
                correspondence_name,
                level,
                cast("Any", key_value),
            )
        except KeyError as exc:
            raise AlignmentPolicyNotApplicableError(
                message="period_correspondence encountered an uncertified period key",
                expected="every candidate period key to exist in the named correspondence",
                received=f"correspondence={correspondence_name!r}, key={key_value!r}",
                context={
                    "kind": "TemporalCorrespondencePeriodMissing",
                    "correspondence": correspondence_name,
                    "level": level,
                    "period_key": key_value,
                },
            ) from exc
        if mapped is None:
            missing_mapping += 1
            continue
        key = (*_dimension_key(row, dimensions), mapped)
        if key in current_rows:
            raise AlignmentFailedError(
                message="period_correspondence found duplicate current period coordinates",
                context={"kind": "TemporalAlignmentDuplicateCoordinate", "coordinate": key},
            )
        _key, payload = _row_coordinate(
            row,
            frame=current,
            dimensions=dimensions,
            coordinate=key_value,
            current=True,
            time_column=current_time,
            value_column=current_value,
        )
        payload["_mapped_baseline_key"] = mapped
        current_rows[key] = payload
    result = _pair_maps(
        current_rows,
        baseline_rows,
        policy=policy,
        current_key_text=dimensions,
        baseline_key_text=dimensions,
    )
    if missing_mapping and policy_any.unmatched == "fail":
        raise AlignmentFailedError(
            message="period_correspondence has current periods without a certified baseline",
            context={"kind": "TemporalCorrespondenceUnmatched", "count": missing_mapping},
        )
    evidence = dict(result.evidence)
    evidence["candidate_current_points"] = len(current_data)
    evidence["candidate_baseline_points"] = len(baseline_data)
    evidence["current_only_points"] = cast("int", evidence["current_only_points"]) + missing_mapping
    evidence["unmatched_points"] = cast("int", evidence["unmatched_points"]) + missing_mapping
    if missing_mapping and policy_any.unmatched == "drop":
        evidence["dropped_points"] = cast("int", evidence["dropped_points"]) + missing_mapping
        evidence["dropped_reason"] = "missing_correspondence"
    cumulative_info = dict(result.cumulative_info)
    cumulative_info["dropped_rows_a"] = (
        int(cumulative_info.get("dropped_rows_a", 0)) + missing_mapping
    )
    return TemporalAlignmentResult(
        frame=result.frame,
        target_binding=binding,
        evidence=evidence,
        cumulative_info=cumulative_info,
    )


def align_temporal_policy(
    current: MetricFrame,
    baseline: MetricFrame,
    *,
    policy: AlignmentPolicy,
    session: Any,
) -> TemporalAlignmentResult:
    """Run Slice 3 preflight and pairing for one closed policy variant."""
    if policy.kind == "day_of_week":
        return _day_of_week_alignment(current, baseline, policy=policy, session=session)
    if policy.kind == "period_progress":
        return _period_progress_alignment(current, baseline, policy=policy, session=session)
    if policy.kind == "period_correspondence":
        return _period_correspondence_alignment(current, baseline, policy=policy, session=session)
    raise AlignmentPolicyNotApplicableError(
        message=f"temporal alignment does not handle policy {policy.kind!r}",
        context={"kind": "AlignmentPolicyNotApplicable", "alignment_kind": policy.kind},
    )


def comparison_temporal_contract(
    current: MetricFrame,
    baseline: MetricFrame,
    *,
    policy: AlignmentPolicy,
    result: TemporalAlignmentResult,
    report_timezone: str,
) -> ComparisonTemporalContractV1:
    """Build the closed comparison authority contract before artifact commit."""
    current_contract = getattr(current.meta, "temporal_contract", None)
    baseline_contract = getattr(baseline.meta, "temporal_contract", None)
    if not isinstance(current_contract, FrameTemporalContractV1):
        current_contract = FrameTemporalContractV1(display_timezone=report_timezone)
    if not isinstance(baseline_contract, FrameTemporalContractV1):
        baseline_contract = FrameTemporalContractV1(display_timezone=report_timezone)
    return ComparisonTemporalContractV1(
        current=current_contract,
        baseline=baseline_contract,
        alignment_policy=cast("Any", policy.model_dump(mode="json")),
        resolved_target_period=result.target_binding,
        alignment_evidence=AlignmentEvidenceV1.model_validate(result.evidence),
    )
