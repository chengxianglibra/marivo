"""Exact bounded pandas folds over current rows and named retained state."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pyarrow as pa

from marivo._temporal import Grain, PeriodCalendarSnapshotV1
from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.observation.contracts import (
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
)
from marivo.analysis.observation.fold_contracts import (
    BUILTIN_GRAIN_MONTHS,
    BUILTIN_GRAIN_SECONDS,
    FoldComponentV1,
    MetricFoldAuthorityV1,
    coverage_columns,
    fold_part_role,
    fold_state_names,
)
from marivo.analysis.operators.row import (
    PartFrame,
    RowCall,
    _missing,
    aligned_part_positions,
    frame_keys,
    ordered,
    row_key_names,
)


def _timestamp(value: object) -> pd.Timestamp:
    if not isinstance(value, (date, datetime)):
        raise compilation_error("an exact retained temporal coordinate", "invalid fold coordinate")
    return pd.Timestamp(value)


def bucket_bounds(
    value: object,
    grain: Grain,
    snapshot: PeriodCalendarSnapshotV1 | None,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Use the admitted DuckDB calendar anchoring without consulting a backend."""
    stamp = _timestamp(value)
    if grain.kind == "semantic":
        if snapshot is None or snapshot.calendar_ref != grain.calendar:
            raise compilation_error(
                "the exact certified calendar snapshot", "missing fold calendar"
            )
        if grain.level == "day":
            start = stamp.normalize()
            return start, start + timedelta(days=1)
        for period in snapshot.periods:
            start, end = pd.Timestamp(period.start_date), pd.Timestamp(period.end_date)
            if period.level_name == grain.level and start <= stamp < end:
                return start, end
        raise compilation_error("a containing certified period", "uncovered fold coordinate")
    unit, count = grain.unit, grain.count
    if unit is None or count is None:
        raise compilation_error("one exact builtin grain", "missing fold width")
    if unit in BUILTIN_GRAIN_MONTHS:
        width = count * BUILTIN_GRAIN_MONTHS[unit]
        month = (stamp.year - 2000) * 12 + stamp.month - 1
        aligned = (month // width) * width
        year, offset = divmod(aligned, 12)
        start = pd.Timestamp(year=2000 + year, month=offset + 1, day=1, tz=stamp.tz)
        return start, start + pd.DateOffset(months=width)
    if unit not in BUILTIN_GRAIN_SECONDS:
        raise compilation_error("an admitted builtin grain", "unsupported fold grain")
    width_seconds = BUILTIN_GRAIN_SECONDS[unit] * count
    # DuckDB time_bucket anchors sub-month intervals at Monday 2000-01-03.
    origin = pd.Timestamp("2000-01-03", tz=stamp.tz)
    index = int((stamp - origin).total_seconds() // width_seconds)
    start = origin + timedelta(seconds=index * width_seconds)
    return start, start + timedelta(seconds=width_seconds)


def _merge(values: list[object], merge: str, state: str) -> object:
    present = [value for value in values if not _missing(value)]
    if not present:
        return (
            0 if state in ("count", "row_count", "non_null_count", "non_null_pair_count") else None
        )
    if not all(isinstance(value, (int, float, Decimal)) for value in present):
        raise compilation_error("numeric exact component state", "invalid fold state value")
    if any(isinstance(value, Decimal) for value in present):
        decimals = [Decimal(str(value)) for value in present]
        if merge in ("min", "max") and state in ("value", "min", "max"):
            return min(decimals) if merge == "min" else max(decimals)
        return sum(decimals)
    numbers = [value for value in present if isinstance(value, (int, float))]
    if merge in ("min", "max") and state in ("value", "min", "max"):
        return min(numbers) if merge == "min" else max(numbers)
    return sum(numbers)


def _number(value: object) -> float | int | Decimal | None:
    if _missing(value):
        return None
    if not isinstance(value, (int, float, Decimal)):
        raise compilation_error("an exact numeric component", "invalid component value")
    return value


def _component_value(
    component: FoldComponentV1, state: dict[str, object]
) -> float | int | Decimal | None:
    names = dict(component.state_columns)
    if component.kind == "count":
        value = _number(state[names["count"]])
        return 0 if value is None else value
    if component.kind in ("mean", "weighted_mean"):
        numerator, denominator = (
            ("sum", "non_null_count")
            if component.kind == "mean"
            else ("weighted_numerator", "weight_sum")
        )
        top, bottom = _number(state[names[numerator]]), _number(state[names[denominator]])
        return None if top is None or bottom is None or bottom == 0 else float(top) / float(bottom)
    name = (
        "sum"
        if component.kind == "sum"
        else component.kind
        if component.kind in ("min", "max")
        else "value"
    )
    value = _number(state[names[name]])
    return 0 if value is None and component.empty_rule == "zero" else value


def _value(
    authority: MetricFoldAuthorityV1, state: dict[str, object]
) -> float | int | Decimal | None:
    nodes = {node.node_id: node for node in authority.nodes}
    components = {component.node_id: component for component in authority.components}

    def evaluate(node_id: str) -> float | int | Decimal | None:
        node = nodes[node_id]
        if node.kind == "component":
            return _component_value(components[node_id], state)
        if node.kind == "identity":
            return evaluate(node.children[0])
        if node.kind == "ratio":
            top, bottom = evaluate(node.children[0]), evaluate(node.children[1])
            if bottom == 0 and node.zero_division == "error":
                raise compilation_error(
                    "a nonzero retained denominator", "zero denominator in exact fold"
                )
            return (
                None if top is None or bottom is None or bottom == 0 else float(top) / float(bottom)
            )
        values = [evaluate(child) for child in node.children]
        if any(value is None for value in values):
            return None
        return sum(
            float(value) * coefficient
            for value, coefficient in zip(values, node.coefficients, strict=True)
            if value is not None
        )

    return evaluate(authority.root_id)


def validate_parts(
    frame: pd.DataFrame, parts: tuple[PartFrame, ...], row: DatasetRowContract
) -> None:
    """Reconcile every consumed role with its visible value before any transformation."""
    semantics = row.family_semantics
    if not isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        raise compilation_error("Metric retained state authority", "invalid retained input")
    authorities = {fold_part_role(authority): authority for authority in semantics.metric_folds}
    fields = {field.field_id.value: field.name for field in row.schema.columns}
    keys = row_key_names(row)
    expected_keys = frame_keys(frame, keys)
    if len({part.role for part in parts}) != len(parts):
        raise compilation_error("one consumed role per Metric", "duplicate retained role")
    for part in parts:
        if part.role == "population_sampling_state":
            continue
        authority = authorities.get(part.role)
        if authority is None:
            raise compilation_error("a consumed Metric component role", "unknown retained role")
        positions = aligned_part_positions(part, keys, expected_keys)
        visible = frame[fields[authority.field_id]]
        dtype = visible.dtype
        if not isinstance(dtype, pd.ArrowDtype):
            raise compilation_error("one exact retained Metric type", "unknown visible type")
        state_rows = [
            dict(zip(part.frame.columns, values, strict=True))
            for values in part.frame.itertuples(index=False, name=None)
        ]
        for index, key in enumerate(expected_keys):
            state = state_rows[positions[key]]
            for component in authority.components:
                support = {
                    kind: state[name]
                    for kind, name in component.state_columns
                    if kind.endswith("count")
                }
                if any(
                    not isinstance(count, int) or isinstance(count, bool) or count < 0
                    for count in support.values()
                ):
                    raise compilation_error(
                        "nonnegative exact component support counts", "invalid retained support"
                    )
                maximum = support.get("row_count")
                if isinstance(maximum, int) and any(
                    isinstance(count, int) and count > maximum for count in support.values()
                ):
                    raise compilation_error(
                        "component support within current row count",
                        "retained support exceeds row count",
                    )
            if authority.cumulative:
                endpoint_name, start_name, end_name, seconds_name, complete_name = coverage_columns(
                    authority
                )
                endpoints = tuple(state[name] for name in (endpoint_name, start_name, end_name))
                seconds, complete = state[seconds_name], state[complete_name]
                absent = tuple(_missing(value) for value in endpoints)
                if any(absent):
                    supports = tuple(
                        state[name]
                        for component in authority.components
                        for kind, name in component.state_columns
                        if kind.endswith("count")
                    )
                    if not (
                        all(absent)
                        and seconds == 0
                        and complete is False
                        and all(value == 0 for value in supports)
                    ):
                        raise compilation_error(
                            "empty endpoint state with zero support and incomplete coverage",
                            "invalid empty retained coverage",
                        )
                else:
                    endpoint, start, end = (_timestamp(value) for value in endpoints)
                    if (
                        endpoint != end
                        or start > end
                        or not isinstance(seconds, (int, float))
                        or not math.isfinite(seconds)
                        or seconds < 0
                        or seconds > (end - start).total_seconds()
                        or not isinstance(complete, bool)
                    ):
                        raise compilation_error(
                            "valid current endpoint and coverage interval",
                            "invalid retained coverage",
                        )
            computed = _value(authority, state)
            expected: object = pa.scalar(computed, type=dtype.pyarrow_dtype).as_py()
            actual: object = visible.iloc[index]
            if _missing(expected) and _missing(actual):
                continue
            if (
                isinstance(expected, float)
                and isinstance(actual, float)
                and math.isnan(expected)
                and math.isnan(actual)
            ):
                continue
            if _missing(expected) or _missing(actual) or expected != actual:
                raise compilation_error(
                    "primary values reconciled with exact retained component state",
                    "retained value differs from primary",
                )


def _coverage(
    authority: MetricFoldAuthorityV1,
    data: pd.DataFrame,
    indices: list[int],
    call: RowCall,
    coordinates: dict[str, object],
) -> dict[str, object]:
    endpoint, start_name, end_name, seconds_name, complete_name = coverage_columns(authority)
    selected = data.iloc[indices]
    spec = call.fold
    if spec is None:
        raise compilation_error("a retained fold invocation", "missing fold specification")
    semantics = call.input_row.family_semantics
    if not isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        raise compilation_error("Metric temporal authority", "invalid temporal fold input")
    names = (endpoint, start_name, end_name, seconds_name, complete_name)
    if not indices:
        return dict(zip(names, (None, None, None, 0.0, False), strict=True))
    rows = list(selected.loc[:, list(names)].itertuples(index=False, name=None))
    if any(_missing(value) for row in rows for value in row):
        if all(
            all(_missing(value) for value in row[:3]) and row[3] == 0 and row[4] is False
            for row in rows
        ):
            return dict(zip(names, (None, None, None, 0.0, False), strict=True))
        raise compilation_error(
            "complete retained endpoint and coverage state", "missing temporal state"
        )
    if spec.axis != "time":
        if any(row != rows[0] for row in rows[1:]):
            raise compilation_error(
                "aligned retained endpoint and coverage across dimensions",
                "unaligned cumulative fold state",
            )
        if any(row[3] != (_timestamp(row[2]) - _timestamp(row[1])).total_seconds() for row in rows):
            raise compilation_error(
                "one aligned contiguous observed interval across dimensions",
                "noncontiguous cumulative coverage",
            )
        return dict(zip(names, rows[0], strict=True))
    starts = [_timestamp(row[1]) for row in rows]
    ends = [_timestamp(row[2]) for row in rows]
    start, end = min(starts), max(ends)
    seconds = sum(float(row[3]) for row in rows)
    complete = all(row[4] is True for row in rows)
    time_name = next(
        (
            field.name
            for field in call.output_row.schema.columns
            if field.role_id == "time_dimension"
        ),
        None,
    )
    if spec.grain is not None and time_name is not None:
        target_start, target_end = bucket_bounds(
            coordinates[time_name], spec.grain, semantics.fold_temporal_snapshot
        )
    elif semantics.fold_time_scope is not None:
        target_start = pd.Timestamp(semantics.fold_time_scope.start)
        target_end = pd.Timestamp(semantics.fold_time_scope.end)
    else:
        target_start, target_end = start, end
    complete = (
        complete
        and start == target_start
        and end == target_end
        and seconds == (target_end - target_start).total_seconds()
    )
    return dict(
        zip(
            names,
            (max(_timestamp(row[0]) for row in rows), start, end, seconds, complete),
            strict=True,
        )
    )


def _part_schema(part: PartFrame, output_row: DatasetRowContract) -> pa.Schema:
    keys = row_key_names(output_row)
    columns = [part.schema.field(name) for name in keys]
    return pa.schema(
        [*columns, *(column for column in part.schema if column.name not in part.keys)]
    )


def execute_fold(
    frame: pd.DataFrame, parts: tuple[PartFrame, ...], call: RowCall
) -> tuple[pd.DataFrame, tuple[PartFrame, ...]]:
    """Reduce one normalized axis and retain precisely the state of its output rows."""
    spec = call.fold
    semantics = call.input_row.family_semantics
    if spec is None or not isinstance(
        semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)
    ):
        raise compilation_error("a typed retained Metric fold", "missing fold authority")
    keys = row_key_names(call.output_row)
    old_keys = row_key_names(call.input_row)
    primary_keys = frame_keys(frame, old_keys)
    coordinate_frame = frame.loc[:, list(keys)].copy(deep=True)
    if spec.axis == "time" and spec.grain is not None:
        time_name = next(
            field.name
            for field in call.output_row.schema.columns
            if field.role_id == "time_dimension"
        )
        time_values: list[object] = []
        for value in coordinate_frame[time_name].tolist():
            start, _ = bucket_bounds(value, spec.grain, semantics.fold_temporal_snapshot)
            time_values.append(
                start.date()
                if isinstance(value, date) and not isinstance(value, datetime)
                else start
            )
        coordinate_frame[time_name] = pd.Series(time_values, dtype=frame[time_name].dtype)
    groups: dict[tuple[object, ...], list[int]] = {} if keys else {(): []}
    for index, key in enumerate(frame_keys(coordinate_frame, keys)):
        groups.setdefault(key, []).append(index)
    by_role = {part.role: part for part in parts}
    aligned: dict[str, pd.DataFrame] = {}
    for authority in semantics.metric_folds:
        role = fold_part_role(authority)
        part = by_role.get(role)
        if part is None or not set(fold_state_names(authority)) <= set(part.schema.names):
            raise compilation_error(
                "all exact named fold state roles", "missing required retained fold state"
            )
        positions = aligned_part_positions(part, old_keys, primary_keys)
        aligned[role] = part.frame.iloc[[positions[key] for key in primary_keys]].reset_index(
            drop=True
        )
    output_values: dict[str, list[object]] = {
        field.name: [] for field in call.output_row.schema.columns
    }
    part_values: dict[str, list[dict[str, object]]] = {role: [] for role in aligned}
    fields = {field.field_id.value: field.name for field in call.output_row.schema.columns}
    for group, indices in groups.items():
        coordinates = dict(zip(keys, group, strict=True))
        for coordinate_name, value in coordinates.items():
            output_values[coordinate_name].append(value)
        for authority in semantics.metric_folds:
            role = fold_part_role(authority)
            data = aligned[role]
            state: dict[str, object] = dict(coordinates)
            for component in authority.components:
                merge = component.time_merge if spec.axis == "time" else component.spatial_merge
                if merge == "blocked":
                    raise compilation_error(
                        "an admitted exact component merge", "blocked retained fold"
                    )
                chosen: int | None = None
                if merge in ("first", "last") and indices:
                    endpoint = coverage_columns(authority)[0]
                    chosen = (min if merge == "first" else max)(
                        indices, key=lambda index: _timestamp(data[endpoint].iloc[index])
                    )
                for kind, name in component.state_columns:
                    state[name] = (
                        data[name].iloc[chosen]
                        if chosen is not None
                        else _merge(data.iloc[indices][name].tolist(), merge, kind)
                    )
            if authority.cumulative:
                state.update(_coverage(authority, data, indices, call, coordinates))
            output_values[fields[authority.field_id]].append(_value(authority, state))
            part_values[role].append(state)
    output = pd.DataFrame(
        {name: pd.Series(values, dtype=frame[name].dtype) for name, values in output_values.items()}
    )
    output = ordered(output, call.output_row, call.output_rows)
    output_keys = frame_keys(output, keys)
    original_positions = {group: position for position, group in enumerate(groups)}
    output_parts: list[PartFrame] = []
    for role, retained_values in part_values.items():
        part = by_role[role]
        schema = _part_schema(part, call.output_row)
        part_frame = pd.DataFrame(
            {
                column.name: pd.Series(
                    [value[column.name] for value in retained_values],
                    dtype=part.frame[column.name].dtype,
                )
                for column in schema
            }
        )
        part_frame = part_frame.iloc[[original_positions[key] for key in output_keys]].reset_index(
            drop=True
        )
        output_parts.append(
            PartFrame(role, part.contract_id, part.contract_version, schema, keys, part_frame)
        )
    output_parts.extend(part for part in parts if part.role == "population_sampling_state")
    return output, tuple(output_parts)
