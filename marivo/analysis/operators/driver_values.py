"""Complete bounded additive concentration scoring, independent of net Delta shares."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping
from datetime import date, datetime
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import cmp_to_key

import numpy as np
import pandas as pd
import pyarrow as pa

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.operators.attribute_values import (
    _difference,
    _finite,
    _partition_values,
    _sum,
    exact_magnitude,
    partition_endpoints,
    prepare_partition_states,
    reconciles,
)
from marivo.analysis.operators.driver_contracts import (
    DriverCandidateDefinition,
    DriverCandidateEvaluationSummary,
    DriverCandidateSpecV1,
)
from marivo.analysis.operators.errors import driver_error as discovery_error
from marivo.analysis.operators.row import PartFrame, ordered
from marivo.analysis.operators.row_values import _missing, compare_value, frame_keys, row_key_names


def _scalar_encoding(value: object, kind: str) -> str:
    if isinstance(value, (float, np.floating)) and not math.isfinite(value):
        raise discovery_error("finite driver coordinates or governed null", "non-finite coordinate")
    if isinstance(value, Decimal) and not value.is_finite():
        raise discovery_error("finite driver coordinates or governed null", "non-finite coordinate")
    if _missing(value):
        return "N"
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        value = value.item()
    if kind.startswith(("int", "uint")) or kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise discovery_error("exact typed integer coordinate", "invalid coordinate")
        text = str(value)
    elif kind.startswith("float") or kind == "floating":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise discovery_error("finite floating coordinate", "invalid coordinate")
        number = float(value)
        if number == 0:
            text = "0x0p+0"
        else:
            fraction, power = math.frexp(number)
            mantissa = (fraction * 2).hex().split("p")[0].rstrip("0").rstrip(".")
            text = mantissa + "p" + f"{power - 1:+d}"
    elif kind.startswith("decimal"):
        if not isinstance(value, Decimal) or not value.is_finite():
            raise discovery_error("finite exact decimal coordinate", "invalid coordinate")
        text = "0" if value == 0 else format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
    elif kind == "date":
        if not isinstance(value, date) or isinstance(value, datetime):
            raise discovery_error("exact date coordinate", "invalid coordinate")
        text = value.isoformat()
    elif kind in ("timestamp", "datetime", "instant"):
        if not isinstance(value, datetime):
            raise discovery_error("exact timestamp coordinate", "invalid coordinate")
        stamp = pd.Timestamp(value)
        if stamp.nanosecond:
            raise discovery_error("microsecond timestamp coordinate", "submicrosecond coordinate")
        text = str(stamp.as_unit("us").value)
    elif kind in ("bool", "boolean"):
        if type(value) is not bool:
            raise discovery_error("boolean coordinate", "invalid coordinate")
        text = "true" if value else "false"
    elif kind == "string":
        if type(value) is not str:
            raise discovery_error("string coordinate", "invalid coordinate")
        text = value
    else:
        raise discovery_error("registered typed driver coordinate", "unsupported coordinate type")
    return "V" + text.encode("utf-8").hex().upper()


def driver_item_id(
    definition: DriverCandidateDefinition, row: d.DatasetRowContract, values: Mapping[str, object]
) -> str:
    """Match the source-native versioned tagged UTF-8 key encoding exactly."""
    fields = tuple(f for f in row.schema.columns if f.field_id in row.key_field_ids)
    encoded: list[str] = []
    for field in fields:
        value = values[field.name]
        if isinstance(field.identity, d._EntityFieldIdentity):
            raise discovery_error(
                "source-native Entity identity encoding",
                "local identity computation is not admitted",
            )
        encoded.append(_scalar_encoding(value, field.logical_type_id))
    prefix = (
        "candidate_driver_item@v1:"
        + d._canonical_digest(definition.identity_payload())
        + ":"
        + d._canonical_digest(tuple((f.field_id.value, f.logical_type_id) for f in fields))
        + ":"
    )
    return "sha256:" + hashlib.sha256((prefix + "|".join(encoded)).encode("utf-8")).hexdigest()


def _typed_output(
    records: list[dict[str, object]], spec: DriverCandidateSpecV1, source: pd.DataFrame
) -> pd.DataFrame:
    columns: dict[str, pd.Series] = {}
    for field in spec.output_row.schema.columns:
        values = [record[field.name] for record in records]
        if field.name == "reason_codes":
            arrow = pa.list_(pa.string())
        elif field.name in source and field.role_id in (
            "dimension",
            "comparison_coordinate",
            "comparison_time",
        ):
            dtype = source[field.name].dtype
            arrow = (
                dtype.pyarrow_dtype
                if isinstance(dtype, pd.ArrowDtype)
                else pa.array(source[field.name]).type
            )
            if pa.types.is_null(arrow):
                arrow = (
                    pa.date32()
                    if field.logical_type_id == "date"
                    else pa.type_for_alias(field.logical_type_id)
                )
        else:
            arrow = pa.type_for_alias(field.logical_type_id)
        columns[field.name] = pd.Series(values, dtype=pd.ArrowDtype(arrow))
    return pd.DataFrame(columns)


def _validate_input_values(
    frame: pd.DataFrame, row: d.DatasetRowContract, check: Callable[[], None] | None
) -> None:
    for field in row.schema.columns:
        if field.field_id not in row.coordinate_field_ids:
            continue
        for index, coordinate in enumerate(frame[field.name]):
            if check is not None and index % 1024 == 0:
                check()
            _scalar_encoding(coordinate, field.logical_type_id)
    for index in range(len(frame)):
        if check is not None and index % 1024 == 0:
            check()
        current = _finite(frame["current_value"].iloc[index])
        baseline = _finite(frame["baseline_value"].iloc[index])
        if (
            frame["calculation_status"].iloc[index] != "ok"
            or frame["coordinate_presence"].iloc[index]
            not in ("matched", "current_only", "baseline_only")
            or _finite(frame["delta"].iloc[index]) != _difference(current, baseline)
        ):
            raise discovery_error(
                "finite Delta rows matching complete side endpoints", "invalid driver Delta values"
            )


def execute_driver(
    frame: pd.DataFrame,
    spec: DriverCandidateSpecV1,
    *,
    parts: tuple[PartFrame, ...] = (),
    original: pd.DataFrame | None = None,
    original_parts: tuple[PartFrame, ...] = (),
    check: Callable[[], None] | None = None,
) -> tuple[pd.DataFrame, DriverCandidateEvaluationSummary]:
    """Validate complete additive partitions, score each axis, then apply the search limit."""
    if check is not None:
        check()
    if any(f.role_id == "entity_identity" for f in spec.input_row.schema.columns):
        raise discovery_error(
            "source-required Entity driver screening", "local identity computation is not admitted"
        )
    authorities, states = prepare_partition_states(frame, spec.input_row, parts, check)
    _validate_input_values(frame, spec.input_row, check)
    for authority, side_states in zip(authorities, states, strict=True):
        for index, state in enumerate(side_states):
            if check is not None and index % 1024 == 0:
                check()
            _partition_values(authority, state, "additive_difference@v1")
    scope_names = tuple(f.name for f in spec.scope_fields)
    groups: dict[tuple[object, ...], list[int]] = {}
    for index, scope in enumerate(frame_keys(frame, scope_names)):
        groups.setdefault(scope, []).append(index)
    original_groups: dict[tuple[object, ...], list[int]] = {}
    original_authorities, original_states = authorities, states
    if spec.original_input_row is not None:
        if original is None:
            raise discovery_error(
                "original selected Delta and retained state", "missing original endpoints"
            )
        original_authorities, original_states = prepare_partition_states(
            original, spec.original_input_row, original_parts, check
        )
        _validate_input_values(original, spec.original_input_row, check)
        for index, scope in enumerate(frame_keys(original, scope_names)):
            original_groups.setdefault(scope, []).append(index)
        if set(groups) - set(original_groups):
            raise discovery_error(
                "expanded scopes within original selection", "unselected expansion scope"
            )
        for scope in original_groups:
            groups.setdefault(scope, [])
    records: list[dict[str, object]] = []
    zero_count = 0
    axis_keys_by_name = {axis.name: frame_keys(frame, (axis.name,)) for axis in spec.axis_fields}
    for scope in sorted(groups, key=cmp_to_key(compare_value)):
        if check is not None:
            check()
        indices = groups[scope]
        endpoints = partition_endpoints(authorities, states, indices)
        if spec.original_input_row is not None:
            original_endpoints = partition_endpoints(
                original_authorities, original_states, original_groups[scope]
            )
            if any(
                not reconciles(a, b) for a, b in zip(endpoints, original_endpoints, strict=True)
            ):
                raise discovery_error(
                    "expanded partitions reproducing independent original endpoints",
                    "expanded endpoint mismatch",
                )
        times: dict[str, object] = {}
        for field in spec.definition.paired_time_fields:
            time_values = frame.iloc[indices][field.name].tolist()
            if not time_values and original is not None:
                time_values = original.iloc[original_groups[scope]][field.name].tolist()
            if not time_values or any(
                compare_value(v, time_values[0]) != 0 for v in time_values[1:]
            ):
                raise discovery_error(
                    "one exact paired time value per screening scope", "inconsistent scope times"
                )
            times[field.name] = time_values[0]
        for axis, axis_ref in zip(spec.axis_fields, spec.definition.search_space, strict=True):
            if check is not None:
                check()
            members: dict[tuple[object, ...], list[int]] = {}
            axis_keys = axis_keys_by_name[axis.name]
            for index in indices:
                members.setdefault(axis_keys[index], []).append(index)
            contributions: list[tuple[tuple[object, ...], int | float | Decimal]] = []
            side_totals: tuple[list[int | float | Decimal], list[int | float | Decimal]] = ([], [])
            for member, positions in members.items():
                if check is not None:
                    check()
                current, baseline = partition_endpoints(authorities, states, positions)
                side_totals[0].append(current)
                side_totals[1].append(baseline)
                contributions.append((member, exact_magnitude(_difference(current, baseline))))
            # Shared reconciliation allows floating rounding between grouped
            # and whole-scope folds; integer/Decimal comparisons remain exact.
            # It does not relax partition completeness or the 50% score boundary.
            if any(
                not reconciles(_sum(values), expected)
                for values, expected in zip(side_totals, endpoints, strict=True)
            ):
                raise discovery_error(
                    "complete axis partitions reproducing independent endpoints",
                    "driver partition reconciliation failed",
                )
            total = _sum([value for _, value in contributions])
            if total == 0:
                zero_count += 1
                continue

            def compare_members(
                a: tuple[tuple[object, ...], int | float | Decimal],
                b: tuple[tuple[object, ...], int | float | Decimal],
            ) -> int:
                value = compare_value(a[1], b[1])
                return -value if value else compare_value(a[0], b[0])

            ranked = sorted(contributions, key=cmp_to_key(compare_members))
            magnitudes = [value for _, value in ranked]
            total_fraction = Fraction(total)
            low, high = 1, len(magnitudes)
            # Each prefix receives the same single rounding as the complete sum.
            # Binary search avoids repeatedly rounding a cumulative accumulator.
            while low < high:
                if check is not None:
                    check()
                middle = (low + high) // 2
                if Fraction(_sum(magnitudes[:middle])) * 2 >= total_fraction:
                    high = middle
                else:
                    low = middle + 1
            count = low
            cumulative = _sum(magnitudes[:count])
            if isinstance(cumulative, float) and isinstance(total, float):
                share = cumulative / total
            else:
                with localcontext() as context:
                    context.prec = 80
                    share = float(Decimal(cumulative) / Decimal(total))
            record: dict[str, object] = dict(zip(scope_names, scope, strict=True))
            record.update(times)
            record.update(
                axis_ref=axis_ref,
                axis_cardinality=len(members),
                concentration_member_count=count,
                concentration_share=share,
                score=1.0 / (count + len(members) / 1000.0),
                reason_codes=("axis_concentration",),
            )
            record["item_id"] = driver_item_id(spec.definition, spec.output_row, record)
            records.append(record)
    if not groups:
        raise discovery_error(
            "at least one evaluable complete scope-axis partition", "no evaluable driver partition"
        )
    output = _typed_output(records, spec, frame)
    keys = frame_keys(output, row_key_names(spec.output_row))
    digests = output["item_id"].tolist()
    if len(keys) != len(set(keys)) or len(digests) != len(set(digests)):
        raise discovery_error(
            "unique complete driver keys and digests before limit", "duplicate driver identity"
        )
    output = ordered(output, spec.output_row, spec.output_rows)
    selected = output.iloc[: spec.definition.limit].copy(deep=True).reset_index(drop=True)
    scores = output["score"].tolist()
    searched = len(groups) * len(spec.axis_fields)
    summary = DriverCandidateEvaluationSummary(
        len(frame),
        len(groups),
        searched,
        searched,
        zero_count,
        len(output),
        len(selected),
        (min(scores), max(scores)) if scores else None,
        (("axis_concentration", len(output)),),
    )
    if check is not None:
        check()
    return selected, summary
