"""Exact keyed comparison-side component retention and validation."""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal

import pandas as pd
import pyarrow as pa

from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.observation.fold_contracts import (
    coverage_columns,
    fold_part_role,
    fold_state_names,
)
from marivo.analysis.operators.attribution_contracts import (
    delta_part_authorities,
    delta_presence_name,
    delta_state_name,
)
from marivo.analysis.operators.contracts import CompareSpecV1, DeltaSemantics
from marivo.analysis.operators.errors import attribution_error
from marivo.analysis.operators.rollup import _value
from marivo.analysis.operators.row import PartFrame, aligned_part_positions
from marivo.analysis.operators.row_values import _missing, frame_keys, row_key_names


def execute_compare_parts(
    current: pd.DataFrame,
    baseline: pd.DataFrame,
    spec: CompareSpecV1,
    current_parts: tuple[PartFrame, ...],
    baseline_parts: tuple[PartFrame, ...],
    output: pd.DataFrame | None = None,
) -> tuple[PartFrame, ...]:
    """Align each complete input component role onto the committed Delta key."""
    if output is None:
        from marivo.analysis.operators.compare import execute_compare

        output = execute_compare(current, baseline, spec)
    result: list[PartFrame] = []
    keys = row_key_names(spec.output_row)
    output_records = output.to_dict(orient="records")
    for (role, authority), side, primary, parts, row in zip(
        delta_part_authorities(spec.output_row),
        ("current", "baseline"),
        (current, baseline),
        (current_parts, baseline_parts),
        (spec.current_row, spec.baseline_row),
        strict=True,
    ):
        source_role = fold_part_role(authority)
        part = next((part for part in parts if part.role == source_role), None)
        if part is None:
            raise attribution_error(
                "complete named comparison side components", "missing side component role"
            )
        source_keys = row_key_names(row)
        positions = aligned_part_positions(part, source_keys, frame_keys(primary, source_keys))
        state_names = fold_state_names(authority)
        if not set(state_names) <= set(part.frame.columns):
            raise attribution_error("complete side component columns", "missing component state")
        time_name = next(
            (field.name for field in row.schema.columns if field.role_id == "time_dimension"), None
        )
        values: dict[str, list[object]] = {
            name: []
            for name in (
                *keys,
                delta_presence_name(side),
                *(delta_state_name(side, name) for name in state_names),
            )
        }
        for record in output_records:
            original_key = tuple(
                record[f"{side}_time"] if name == time_name else record[name]
                for name in source_keys
            )
            position = positions.get(original_key)
            present = position is not None
            for key in keys:
                values[key].append(record[key])
            values[delta_presence_name(side)].append(present)
            for name in state_names:
                empty: object = None
                values[delta_state_name(side, name)].append(
                    part.frame[name].iloc[position] if position is not None else empty
                )
        key_fields: list[pa.Field] = []
        output_fields = {field.name: field for field in spec.output_row.schema.columns}
        for name in keys:
            dtype = output[name].dtype
            if not isinstance(dtype, pd.ArrowDtype):
                raise attribution_error("exact Arrow Delta key type", "unresolved Delta key")
            key_fields.append(
                pa.field(name, dtype.pyarrow_dtype, nullable=output_fields[name].nullable)
            )
        schema = pa.schema(
            [
                *key_fields,
                *(
                    pa.field(
                        delta_state_name(side, name), part.schema.field(name).type, nullable=True
                    )
                    for name in state_names
                ),
                pa.field(delta_presence_name(side), pa.bool_(), nullable=False),
            ]
        )
        frame = pd.DataFrame(
            {
                field.name: pd.Series(values[field.name], dtype=pd.ArrowDtype(field.type))
                for field in schema
            }
        )
        result.append(PartFrame(role, "delta.sufficient_components", 1, schema, keys, frame))
    return tuple(result)


def validate_delta_parts(
    frame: pd.DataFrame, parts: tuple[PartFrame, ...], row: DatasetRowContract
) -> None:
    """Require complete keyed state and reconcile every present side to its visible value."""
    semantics = row.family_semantics
    if not isinstance(semantics, DeltaSemantics):
        raise attribution_error("Delta retained semantics", "invalid input semantics")
    keys = row_key_names(row)
    expected = frame_keys(frame, keys)
    if len(set(expected)) != len(expected) or len({part.role for part in parts}) != len(parts):
        raise attribution_error(
            "unique primary keys and retained roles", "duplicate retained state"
        )
    for role, authority in delta_part_authorities(row):
        side = role.rsplit(".", 1)[1]
        part = next((part for part in parts if part.role == role), None)
        if part is None:
            raise attribution_error(
                "both complete comparison side state roles", "missing side state"
            )
        positions = aligned_part_positions(part, keys, expected)
        names = fold_state_names(authority)
        if set(part.frame.columns) != {
            *keys,
            delta_presence_name(side),
            *(delta_state_name(side, name) for name in names),
        }:
            raise attribution_error("exact named comparison side state", "invalid side columns")
        for index, key in enumerate(expected):
            position = positions[key]
            present = part.frame[delta_presence_name(side)].iloc[position]
            expected_present = frame["coordinate_presence"].iloc[index] != (
                "baseline_only" if side == "current" else "current_only"
            )
            if type(present) is not bool or present != expected_present:
                raise attribution_error(
                    "side presence matching comparison rows", "contradictory side presence"
                )
            state = {
                name: part.frame[delta_state_name(side, name)].iloc[position] for name in names
            }
            if not present:
                if any(not _missing(value) for value in state.values()):
                    raise attribution_error("null absent-side state", "nonempty absent side")
                continue
            for component in authority.components:
                supports = {
                    kind: state[name]
                    for kind, name in component.state_columns
                    if kind.endswith("count")
                }
                if any(type(value) is not int or value < 0 for value in supports.values()):
                    raise attribution_error(
                        "nonnegative exact support counts", "invalid component support"
                    )
                maximum = supports.get("row_count")
                if isinstance(maximum, int) and any(
                    isinstance(value, int) and value > maximum for value in supports.values()
                ):
                    raise attribution_error(
                        "support within row count", "component support exceeds row count"
                    )
                for _kind, name in component.state_columns:
                    value = state[name]
                    if _missing(value):
                        continue
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float, Decimal))
                        or not math.isfinite(value)
                    ):
                        raise attribution_error(
                            "finite numeric component state", "invalid component value"
                        )
            if authority.cumulative:
                endpoint_name, start_name, end_name, seconds_name, complete_name = coverage_columns(
                    authority
                )
                endpoints = tuple(state[name] for name in (endpoint_name, start_name, end_name))
                seconds, complete = state[seconds_name], state[complete_name]
                absent = tuple(_missing(value) for value in endpoints)
                support_values = tuple(
                    state[name]
                    for component in authority.components
                    for kind, name in component.state_columns
                    if kind.endswith("count")
                )
                if any(absent):
                    if not (
                        all(absent)
                        and seconds == 0
                        and complete is False
                        and all(value == 0 for value in support_values)
                    ):
                        raise attribution_error(
                            "empty endpoint state with zero support",
                            "invalid empty semi-additive coverage",
                        )
                elif not all(isinstance(value, (date, datetime)) for value in endpoints):
                    raise attribution_error(
                        "typed retained evaluation-end values", "invalid temporal state"
                    )
                else:
                    timestamps = [
                        pd.Timestamp(value)
                        for value in endpoints
                        if isinstance(value, (date, datetime))
                    ]
                    endpoint, start, end = timestamps
                    if (
                        endpoint != end
                        or start > end
                        or isinstance(seconds, bool)
                        or not isinstance(seconds, (int, float))
                        or not math.isfinite(seconds)
                        or not 0 <= seconds <= (end - start).total_seconds()
                        or type(complete) is not bool
                    ):
                        raise attribution_error(
                            "consistent finite evaluation-end coverage",
                            "invalid semi-additive coverage",
                        )
            if not names:
                continue
            actual = frame[f"{side}_value"].iloc[index]
            computed = _value(authority, state)
            if _missing(actual) and _missing(computed):
                continue
            if _missing(actual) or _missing(computed) or actual != computed:
                raise attribution_error(
                    "visible side value matching retained components", "side endpoint mismatch"
                )
