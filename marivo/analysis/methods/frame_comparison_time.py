"""Bind baseline time rows to the exact captured current coordinate domain."""

from __future__ import annotations

from dataclasses import replace
from typing import TypeAlias

import pandas as pd

from marivo._definition.correspondence import CalendarCorrespondence
from marivo._kernel.temporal_grid import CivilShiftAbsent
from marivo._kernel.validation import is_instance
from marivo.analysis.core.attachments import OutputAttachment
from marivo.analysis.core.frame_operations import NamedFrameComparison
from marivo.analysis.core.frozen_calendar import (
    FrozenCalendarDirectory,
    FrozenComparisonAbsent,
    FrozenComparisonEntry,
    FrozenComparisonMatch,
    FrozenDirectMatch,
    FrozenIntervalDirectory,
    build_partial_calendar_map,
    build_partial_interval_map,
    calendar_directory_rows,
    calendar_match_intents,
    frozen_key_rows,
    frozen_key_table,
    interval_directory_rows,
    read_calendar_directory,
    read_direct_mapping,
    read_interval_directory,
)
from marivo.analysis.model.coordinate_schema import (
    calendar_directory_schema,
    comparison_mapping_schema,
    interval_directory_schema,
)
from marivo.analysis.model.data_schema import (
    BuiltinCalendarData,
    CalendarComparisonBinding,
    CalendarDataAxis,
    DataSchema,
    IntervalComparisonBinding,
    IntervalDataAxis,
    require_compatible_calendar_axes,
)
from marivo.analysis.model.data_types import (
    DataType,
)
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.frozen_keys import FrozenKey

Key: TypeAlias = tuple[tuple[DataType, object], ...]


def comparison_failure(
    expected: str,
    received: str,
    *,
    repair: str = "Use unique complete keys and compatible complete axes.",
    received_limit: int = 320,
) -> None:
    raise MaterializationError(
        expected=expected,
        received=received,
        repair=repair,
        stage="frame_combine",
        received_limit=received_limit,
    )


def _calendar_relation(
    current: FrozenCalendarDirectory,
    baseline: FrozenCalendarDirectory,
    tables: dict[str, OutputAttachment],
    rule: CalendarCorrespondence | None,
) -> tuple[FrozenComparisonEntry, ...]:
    """Consume saved direct facts or the accepted matcher without reconstructing dates."""
    assert baseline.axis.frozen is not None
    direct = baseline.axis.frozen.direct
    saved: tuple[FrozenComparisonEntry, ...] | None = None
    if direct is not None:
        attachment = (
            tables[direct.axis.frozen.directory_name] if direct.axis.frozen is not None else None
        )
        assert attachment is not None
        origin = read_calendar_directory(direct.axis, attachment.schema, attachment.rows)
        require_compatible_calendar_axes(current.axis, origin.axis)
        table = tables[direct.mapping_name]
        entries = read_direct_mapping(baseline.axis, table.schema, table.rows, origin, baseline)
        saved = tuple(
            FrozenComparisonMatch(entry.origin_key, entry.target_key)
            if isinstance(entry, FrozenDirectMatch)
            else FrozenComparisonAbsent(entry.origin_key, entry.reason)
            for entry in entries
        )
        direct_rule = CalendarCorrespondence(direct.kind, direct.by)
        expected = _matched_calendar_relation(origin, baseline, direct_rule)
        if saved != expected:
            comparison_failure(
                "direct map consistent with its frozen target intentions and boundaries",
                repr(saved),
            )
        if current.buckets != origin.buckets:
            if rule is not None:
                return _matched_calendar_relation(current, baseline, rule)
            comparison_failure(
                "the complete frozen immediate current origin domain for inferred match",
                "baseline origin differs from current calendar",
                repair="Pass an explicit match from the current to baseline frozen domain, or supply a baseline shifted directly from this current domain.",
            )
        if (
            rule is not None
            and rule.kind != "ordinal"
            and tuple(intent.result for intent in calendar_match_intents(current, rule))
            != tuple(intent.result for intent in calendar_match_intents(current, direct_rule))
        ):
            comparison_failure(
                "explicit match consistent with the frozen direct target intentions", repr(rule)
            )
    if rule is not None:
        result = _matched_calendar_relation(current, baseline, rule)
        if saved is not None and result != saved:
            comparison_failure(
                "explicit match consistent with the consumed direct relation", repr(rule)
            )
        return result
    if saved is not None:
        return saved
    identities = {bucket.key: bucket.key for bucket in baseline.buckets}
    return tuple(
        FrozenComparisonMatch(bucket.key, target)
        if (target := identities.get(bucket.key)) is not None
        else FrozenComparisonAbsent(bucket.key, "unmatched")
        for bucket in current.buckets
    )


def _matched_calendar_relation(
    current: FrozenCalendarDirectory,
    baseline: FrozenCalendarDirectory,
    rule: CalendarCorrespondence,
) -> tuple[FrozenComparisonEntry, ...]:
    pairs = build_partial_calendar_map(current, baseline, rule)
    reasons = (
        {
            intent.key: intent.result.reason
            for intent in calendar_match_intents(current, rule)
            if isinstance(intent.result, CivilShiftAbsent)
        }
        if rule.kind != "ordinal"
        else {}
    )
    return tuple(
        FrozenComparisonMatch(pair.left_key, pair.right_key)
        if pair.right_key is not None
        else FrozenComparisonAbsent(pair.left_key, reasons.get(pair.left_key, "unmatched"))
        for pair in pairs
        if pair.left_key is not None
    )


def _time_domain_summary(directory: FrozenCalendarDirectory | FrozenIntervalDirectory) -> str:
    if not directory.buckets:
        return "empty"
    if isinstance(directory, FrozenCalendarDirectory):
        return ",".join(bucket.nominal_label for bucket in directory.buckets[:4]) + (
            ",..." if len(directory.buckets) > 4 else ""
        )
    else:
        start = min(bucket.start for bucket in directory.buckets)
        end = max(bucket.end for bucket in directory.buckets)
    return f"[{start.isoformat().replace('+00:00', 'Z')},{end.isoformat().replace('+00:00', 'Z')})"


def _unmapped_time_details(
    alias: str,
    current: FrozenCalendarDirectory | FrozenIntervalDirectory,
    baseline: FrozenCalendarDirectory | FrozenIntervalDirectory,
    rule: CalendarCorrespondence | None,
    key: FrozenKey,
) -> str:
    effective = (
        f"{rule.kind}={rule.by}"
        if rule is not None and rule.kind != "ordinal"
        else "ordinal"
        if rule is not None
        else "equality"
    )
    if isinstance(baseline, FrozenCalendarDirectory):
        frozen = baseline.axis.frozen
        assert frozen is not None
        if rule is None and frozen.direct is not None:
            effective = f"frozen_direct({frozen.direct.kind}={frozen.direct.by})"
        definition = frozen.definition
        grain = (
            f"{definition.unit} step={definition.step}"
            if is_instance(definition, BuiltinCalendarData)
            else "calendar"
        )
        timezone = baseline.axis.timezone
        calendar_bucket = next((bucket for bucket in baseline.buckets if bucket.key == key), None)
        bounds: str | tuple[str, str] | None = (
            calendar_bucket.nominal_label if calendar_bucket else None
        )
    else:
        grain = "interval"
        interval_bucket = next((bucket for bucket in baseline.buckets if bucket.key == key), None)
        timezone = interval_bucket.timezone if interval_bucket else "unknown"
        bounds = (
            (
                interval_bucket.start.isoformat().replace("+00:00", "Z"),
                interval_bucket.end.isoformat().replace("+00:00", "Z"),
            )
            if interval_bucket
            else None
        )
    return (
        f"alias={alias!r}; rule={effective}; grain={grain}; tz={timezone!r}; "
        f"current={_time_domain_summary(current)}; baseline={_time_domain_summary(baseline)}; "
        f"key={tuple(value for _, value in key)!r}; bounds={bounds!r}"
    )


def map_baseline_time(
    frame: pd.DataFrame,
    rows: pd.DataFrame,
    current: DataSchema,
    schema: DataSchema,
    item: NamedFrameComparison,
    binding: CalendarComparisonBinding | IntervalComparisonBinding,
    partners: dict[int, int],
    current_attachments: tuple[OutputAttachment, ...],
    group: tuple[OutputAttachment, ...],
    output_attachments: list[OutputAttachment],
) -> None:
    position = next(
        i for i, axis in enumerate(current.axes) if axis.token == binding.current_axis_token
    )
    left, right = current.axes[position], schema.axes[partners[position]]
    assert isinstance(left, (CalendarDataAxis, IntervalDataAxis)) and isinstance(
        right, (CalendarDataAxis, IntervalDataAxis)
    )
    assert left.frozen is not None and right.frozen is not None
    left_tables, right_tables = (
        {a.name: a for a in tables} for tables in (current_attachments, group)
    )
    a, b = (
        left_tables[left.frozen.directory_name],
        right_tables[right.frozen.directory_name],
    )
    if isinstance(left, CalendarDataAxis):
        assert isinstance(right, CalendarDataAxis) and isinstance(
            binding.baseline, CalendarDataAxis
        )
        ldir = read_calendar_directory(left, a.schema, a.rows)
        rdir = read_calendar_directory(right, b.schema, b.rows)
        left_directory: FrozenCalendarDirectory | FrozenIntervalDirectory = ldir
        right_directory: FrozenCalendarDirectory | FrozenIntervalDirectory = rdir
        relation = _calendar_relation(ldir, rdir, right_tables, item.match)
        copied = replace(rdir, axis=binding.baseline)
        endpoint_rows = calendar_directory_rows(copied)
        endpoint_schema = calendar_directory_schema(binding.baseline)
    else:
        assert isinstance(right, IntervalDataAxis) and isinstance(
            binding.baseline, IntervalDataAxis
        )
        li, ri = (
            read_interval_directory(left, a.schema, a.rows),
            read_interval_directory(right, b.schema, b.rows),
        )
        left_directory, right_directory = li, ri
        if item.match is not None:
            matches = build_partial_interval_map(li, ri, item.match)
            relation = tuple(
                FrozenComparisonMatch(pair.left_key, pair.right_key)
                for pair in matches
                if pair.left_key is not None and pair.right_key is not None
            )
        else:
            interval_keys = {
                (bucket.start, bucket.end, bucket.timezone): bucket.key for bucket in ri.buckets
            }
            relation = tuple(
                FrozenComparisonMatch(bucket.key, target)
                if (target := interval_keys.get((bucket.start, bucket.end, bucket.timezone)))
                is not None
                else FrozenComparisonAbsent(bucket.key, "unmatched")
                for bucket in li.buckets
            )
        copied_interval = replace(ri, axis=binding.baseline)
        endpoint_rows = interval_directory_rows(copied_interval)
        endpoint_schema = interval_directory_schema(binding.baseline)
    assert binding.baseline.frozen is not None
    output_attachments.append(
        OutputAttachment(binding.baseline.frozen.directory_name, endpoint_schema, endpoint_rows)
    )
    map_schema = comparison_mapping_schema(left, binding)
    mapping_rows = frozen_key_table(left.columns, tuple(entry.current_key for entry in relation))
    targets = frozen_key_table(
        map_schema.values[:-1],
        tuple(
            entry.baseline_key if isinstance(entry, FrozenComparisonMatch) else None
            for entry in relation
        ),
    )
    for column in map_schema.values[:-1]:
        mapping_rows[column.slot_id] = targets[column.slot_id]
    mapping_rows["reason"] = pd.Series(
        [entry.reason if isinstance(entry, FrozenComparisonAbsent) else None for entry in relation],
        dtype="string[pyarrow]",
    )
    output_attachments.append(OutputAttachment(binding.mapping_name, map_schema, mapping_rows))
    reverse = {
        entry.baseline_key: entry.current_key
        for entry in relation
        if isinstance(entry, FrozenComparisonMatch)
    }
    actual = frozen_key_rows(rows, right.columns)
    unmapped = next((key for key in actual if key not in reverse), None)
    if unmapped is not None:
        comparison_failure(
            "every actual baseline time row mapping into the declared current domain",
            _unmapped_time_details(
                item.alias, left_directory, right_directory, item.match, unmapped
            ),
            repair="Check the offset's grain unit and both time domains. Adjust the rule or inputs; filter the baseline only if intended, or use align for a general endpoint time union.",
            received_limit=2048,
        )
    encoded = frozen_key_table(right.columns, tuple(reverse[key] for key in actual))
    for column in right.columns:
        frame[column.slot_id] = encoded[column.slot_id]
