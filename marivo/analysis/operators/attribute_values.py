"""Exact complete-input additive and component-mix Attribution arithmetic."""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal, localcontext
from functools import cmp_to_key

import pandas as pd
import pyarrow as pa

from marivo.analysis.datasets.descriptors import _bool_tuple_arity
from marivo.analysis.observation.fold_contracts import (
    MetricFoldAuthorityV1,
    coverage_columns,
    fold_state_names,
)
from marivo.analysis.operators.attribution_contracts import (
    AttributeSpecV1,
    AttributionSemantics,
    delta_part_authorities,
    delta_state_name,
)
from marivo.analysis.operators.compare import _number as promoted_number
from marivo.analysis.operators.delta_state import validate_delta_parts
from marivo.analysis.operators.errors import attribution_error
from marivo.analysis.operators.rollup import _value
from marivo.analysis.operators.row import PartFrame, aligned_part_positions, ordered
from marivo.analysis.operators.row_values import _missing, compare_value, frame_keys, row_key_names

Number = int | float | Decimal


def _finite(value: object) -> Number:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float, Decimal))
        or not math.isfinite(value)
    ):
        raise attribution_error(
            "defined finite component or endpoint", "null or non-finite attribution value"
        )
    return value


def exact_magnitude(value: Number) -> Number:
    """Preserve full Decimal precision independent of the ambient context."""
    return value.copy_abs() if isinstance(value, Decimal) else abs(value)


def _negate(value: Number) -> Number:
    return value.copy_negate() if isinstance(value, Decimal) else -value


def _sum(values: list[Number]) -> Number:
    if any(isinstance(value, Decimal) for value in values):
        with localcontext() as context:
            context.prec = 80
            return _finite(sum((Decimal(str(value)) for value in values), Decimal(0)))
    if all(isinstance(value, int) for value in values):
        return sum(value for value in values if isinstance(value, int))
    try:
        return _finite(math.fsum(float(value) for value in values))
    except OverflowError:
        raise attribution_error(
            "finite component sums", "floating component sum overflow"
        ) from None


def reconciles(left: Number, right: Number) -> bool:
    with localcontext() as context:
        context.prec = 80
        a, b = Decimal(str(left)), Decimal(str(right))
        return abs(a - b) <= max(
            Decimal("1e-12"), Decimal("1e-9") * max(abs(a), abs(b), Decimal(1))
        )


def _state_fold(
    authority: MetricFoldAuthorityV1, states: list[dict[str, object]]
) -> dict[str, object]:
    result: dict[str, object] = {}
    for component in authority.components:
        for kind, name in component.state_columns:
            values = [_finite(state[name]) for state in states if not _missing(state[name])]
            result[name] = _sum(values) if values else 0 if kind.endswith("count") else None
    if authority.cumulative:
        names = coverage_columns(authority)
        present = [
            tuple(state[name] for name in names)
            for state in states
            if not all(_missing(state[name]) for name in names[:3])
        ]
        if present and any(values != present[0] for values in present[1:]):
            raise attribution_error(
                "aligned semi-additive evaluation-end authority", "incompatible component endpoints"
            )
        if present:
            endpoint, start, end, seconds, complete = present[0]
            if (
                not isinstance(endpoint, (pd.Timestamp, datetime))
                or endpoint != end
                or not isinstance(start, (pd.Timestamp, datetime))
                or not isinstance(end, (pd.Timestamp, datetime))
                or start > end
                or not isinstance(seconds, (int, float))
                or seconds != (end - start).total_seconds()
                or type(complete) is not bool
            ):
                raise attribution_error(
                    "exact contiguous evaluation-end state", "invalid semi-additive coverage"
                )
            result.update(zip(names, present[0], strict=True))
        else:
            result.update(zip(names, (None, None, None, 0.0, False), strict=True))
    return result


def _components(
    authority: MetricFoldAuthorityV1, state: dict[str, object]
) -> tuple[Number, Number]:
    nodes = {node.node_id: node for node in authority.nodes}
    components = {component.node_id: component for component in authority.components}
    root = nodes[authority.root_id]
    while root.kind == "identity":
        root = nodes[root.children[0]]
    if root.kind == "ratio":
        values: list[Number] = []
        for child in root.children:
            sub = authority.model_copy(update={"root_id": child})
            value = _value(sub, state)
            values.append(_finite(value))
        return values[0], values[1]
    if root.kind == "component":
        component = components[root.node_id]
        names = dict(component.state_columns)
        numerator, basis = (
            ("sum", "non_null_count")
            if component.kind == "mean"
            else ("weighted_numerator", "weight_sum")
        )
        n, w = state[names[numerator]], state[names[basis]]
        if _missing(n) and (w == 0 or _missing(w)):
            n, w = 0, 0
        return _finite(n), _finite(w)
    raise attribution_error("registered numerator and basis closure", "invalid component-mix root")


def _partition_values(
    authority: MetricFoldAuthorityV1, state: dict[str, object], method: str
) -> tuple[Number, Number]:
    if method == "component_mix@v1":
        numerator, basis = _components(authority, state)
        if basis == 0 and numerator != 0:
            raise attribution_error(
                "zero numerator for a zero-basis partition", "contradictory component state"
            )
        return numerator, basis
    value = _finite(_value(authority, state))
    return value, 0


def _difference(current: Number, baseline: Number) -> Number:
    with localcontext() as context:
        context.prec = 80
        if isinstance(current, Decimal) or isinstance(baseline, Decimal):
            return _finite(Decimal(str(current)) - Decimal(str(baseline)))
        return _finite(current - baseline)


def _side_term(numerator: Number, basis: Number, total_basis: Number) -> float:
    if numerator == 0 and basis == 0:
        return 0.0
    weight = float(basis) / float(total_basis)
    value = float(numerator) / float(basis)
    _finite(weight)
    _finite(value)
    result = weight * value
    _finite(result)
    return result


def execute_attribute(
    frame: pd.DataFrame,
    spec: AttributeSpecV1,
    parts: tuple[PartFrame, ...] = (),
    original_frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Return all reconciled rows after complete state validation and fixed Top-K mapping."""
    validate_delta_parts(frame, parts, spec.input_row)
    semantics = spec.output_row.family_semantics
    if not isinstance(semantics, AttributionSemantics):
        raise attribution_error("exact Attribution output contract", "invalid output semantics")
    keys = row_key_names(spec.input_row)
    input_keys = frame_keys(frame, keys)
    scope_names = tuple(field.name for field in spec.scope_fields)
    axis_names = tuple(field.name for field in spec.axis_fields)
    scope_keys = frame_keys(frame, scope_names)
    axis_keys = frame_keys(frame, axis_names)
    groups: dict[tuple[object, ...], list[int]] = {}
    for index, key in enumerate(scope_keys):
        groups.setdefault(key, []).append(index)
    authorities = tuple(authority for _, authority in delta_part_authorities(spec.input_row))
    states_by_side: list[list[dict[str, object]]] = []
    for side, (role, authority) in zip(
        ("current", "baseline"), delta_part_authorities(spec.input_row), strict=True
    ):
        part = next(part for part in parts if part.role == role)
        positions = aligned_part_positions(part, keys, input_keys)
        states_by_side.append(
            [
                {
                    name: part.frame[delta_state_name(side, name)].iloc[positions[key]]
                    for name in fold_state_names(authority)
                }
                for key in input_keys
            ]
        )
    # Mapping must not conceal an unavailable or contradictory original partition.
    for authority, side_states in zip(authorities, states_by_side, strict=True):
        for state in side_states:
            _partition_values(authority, state, spec.method)
    if not groups and not scope_names:
        empty_endpoints = tuple(
            _finite(_value(authority, _state_fold(authority, []))) for authority in authorities
        )
        promoted_number(_difference(empty_endpoints[0], empty_endpoints[1]), semantics.numeric_type)
    records: list[dict[str, object]] = []
    for scope in sorted(groups, key=cmp_to_key(compare_value)):
        indices = groups[scope]
        overall_states = tuple(
            _state_fold(authority, [states[index] for index in indices])
            for authority, states in zip(authorities, states_by_side, strict=True)
        )
        endpoints = tuple(
            _finite(_value(authority, state))
            for authority, state in zip(authorities, overall_states, strict=True)
        )
        overall_delta = _difference(endpoints[0], endpoints[1])
        if promoted_number(overall_delta, semantics.numeric_type) is None:
            raise attribution_error("representable overall Delta", "invalid overall Delta")
        _original_endpoint_check(spec, original_frame, scope_names, scope, endpoints)
        times: dict[str, object] = {}
        for field in spec.input_row.schema.columns:
            if field.role_id == "comparison_time":
                values = frame.iloc[indices][field.name].tolist()
                if any(compare_value(value, values[0]) != 0 for value in values[1:]):
                    raise attribution_error(
                        "one paired time value per scope", "inconsistent scope times"
                    )
                times[field.name] = values[0]
        mapped = {index: axis_keys[index] for index in indices}
        other = {index: (False,) * len(axis_names) for index in indices}
        if spec.top_k is not None:
            for axis_index in range(len(axis_names)):
                parents: dict[tuple[object, ...], dict[object, list[int]]] = {}
                for index in indices:
                    parent = (*mapped[index][:axis_index], other[index][:axis_index])
                    parents.setdefault(parent, {}).setdefault(
                        axis_keys[index][axis_index], []
                    ).append(index)
                for members in parents.values():
                    scores: dict[object, Number] = {}
                    for member, selected in members.items():
                        side_scores: list[Number] = []
                        for authority, states in zip(authorities, states_by_side, strict=True):
                            state = _state_fold(authority, [states[index] for index in selected])
                            numerator, basis = _partition_values(authority, state, spec.method)
                            side_scores.append(
                                _finite(
                                    exact_magnitude(
                                        basis if spec.method == "component_mix@v1" else numerator
                                    )
                                )
                            )
                        scores[member] = _sum(side_scores)

                    def compare_members(
                        a: object, b: object, scores: dict[object, Number] = scores
                    ) -> int:
                        result = compare_value(scores[a], scores[b])
                        return -result if result else compare_value(a, b)

                    retained = set(sorted(members, key=cmp_to_key(compare_members))[: spec.top_k])
                    for member, selected in members.items():
                        if member not in retained:
                            for index in selected:
                                mapped[index] = (
                                    *mapped[index][:axis_index],
                                    None,
                                    *mapped[index][axis_index + 1 :],
                                )
                                other[index] = (
                                    *other[index][:axis_index],
                                    True,
                                    *other[index][axis_index + 1 :],
                                )
        resolutions = (
            (len(axis_names),) if spec.mode == "joint" else tuple(range(1, len(axis_names) + 1))
        )
        for count in resolutions:
            active = tuple(index < count for index in range(len(axis_names)))
            partitions: dict[tuple[object, ...], list[int]] = {}
            for index in indices:
                coordinates = (*mapped[index][:count], *((None,) * (len(axis_names) - count)))
                mask = (*other[index][:count], *((False,) * (len(axis_names) - count)))
                partitions.setdefault((*coordinates, mask), []).append(index)
            resolution_records: list[dict[str, object]] = []
            component_totals = tuple(
                _components(authority, state) if spec.method == "component_mix@v1" else (0, 0)
                for authority, state in zip(authorities, overall_states, strict=True)
            )
            mapped_components: list[list[tuple[Number, Number]]] = [[], []]
            for partition in sorted(partitions, key=cmp_to_key(compare_value)):
                selected = partitions[partition]
                side_values: list[Number] = []
                for side_index, (authority, states) in enumerate(
                    zip(authorities, states_by_side, strict=True)
                ):
                    state = _state_fold(authority, [states[index] for index in selected])
                    numerator, basis = _partition_values(authority, state, spec.method)
                    mapped_components[side_index].append((numerator, basis))
                    if spec.method == "component_mix@v1":
                        total_basis = component_totals[side_index][1]
                        if total_basis == 0:
                            raise attribution_error(
                                "defined overall component-mix endpoint", "zero overall basis"
                            )
                        side_values.append(_side_term(numerator, basis, total_basis))
                    else:
                        side_values.append(numerator)
                contribution = _difference(side_values[0], side_values[1])
                record: dict[str, object] = dict(zip(scope_names, scope, strict=True))
                record.update(zip(axis_names, partition[:-1], strict=True))
                record.update(times)
                record.update(
                    active_axis_mask=active,
                    other_mask=partition[-1],
                    current_value=side_values[0],
                    baseline_value=side_values[1],
                    overall_delta=overall_delta,
                    contribution=contribution,
                    status="zero_total_delta" if overall_delta == 0 else "ok",
                )
                resolution_records.append(record)
            for side_index, parts_values in enumerate(mapped_components):
                if spec.method == "component_mix@v1":
                    for component_index in (0, 1):
                        if not reconciles(
                            _sum([value[component_index] for value in parts_values]),
                            component_totals[side_index][component_index],
                        ):
                            raise attribution_error(
                                "mapped components reproducing independent components",
                                "component reconciliation failed",
                            )
                if not reconciles(
                    _sum(
                        [
                            _finite(
                                record["current_value" if side_index == 0 else "baseline_value"]
                            )
                            for record in resolution_records
                        ]
                    ),
                    endpoints[side_index],
                ):
                    raise attribution_error(
                        "mapped side terms reproducing independent endpoints",
                        "endpoint reconciliation failed",
                    )
            contributions = [_finite(record["contribution"]) for record in resolution_records]
            if not reconciles(_sum(contributions), overall_delta):
                raise attribution_error(
                    "complete resolution contribution sum matching overall Delta",
                    "attribution reconciliation failed",
                )
            positive = _sum([max(value, 0) for value in contributions])
            negative = _sum([max(_negate(value), 0) for value in contributions])

            def compare_records(a: dict[str, object], b: dict[str, object]) -> int:
                result = compare_value(
                    exact_magnitude(_finite(a["contribution"])),
                    exact_magnitude(_finite(b["contribution"])),
                )
                return (
                    -result
                    if result
                    else compare_value(
                        tuple(a[name] for name in (*axis_names, "other_mask")),
                        tuple(b[name] for name in (*axis_names, "other_mask")),
                    )
                )

            for rank, record in enumerate(
                sorted(resolution_records, key=cmp_to_key(compare_records)), 1
            ):
                value = _finite(record["contribution"])
                record.update(
                    share_of_total_delta=None
                    if overall_delta == 0
                    else _finite(float(value) / float(overall_delta)),
                    share_of_positive_pool=None
                    if positive == 0
                    else _finite(float(max(value, 0)) / float(positive)),
                    share_of_negative_pool=None
                    if negative == 0
                    else _finite(float(max(_negate(value), 0)) / float(negative)),
                    contribution_rank=rank,
                )
            records.extend(resolution_records)
    output: dict[str, pd.Series] = {}
    for field in spec.output_row.schema.columns:
        values = [record[field.name] for record in records]
        mask_arity = _bool_tuple_arity(field.logical_type_id)
        if mask_arity is not None:
            output[field.name] = pd.Series(
                values, dtype=pd.ArrowDtype(pa.list_(pa.bool_(), mask_arity))
            )
        elif field.logical_type_id in ("int64", "float64", "string"):
            dtype = {"int64": pa.int64(), "float64": pa.float64(), "string": pa.string()}[
                field.logical_type_id
            ]
            if field.logical_type_id != "string":
                values = [
                    promoted_number(value, field.logical_type_id) if value is not None else None
                    for value in values
                ]
            output[field.name] = pd.Series(values, dtype=pd.ArrowDtype(dtype))
        elif field.logical_type_id == "decimal":
            decimal_values = [value for value in values if isinstance(value, Decimal)]
            exponents = (value.as_tuple().exponent for value in decimal_values)
            scale = max(
                (max(0, -exponent) for exponent in exponents if isinstance(exponent, int)),
                default=0,
            )
            output[field.name] = pd.Series(values, dtype=pd.ArrowDtype(pa.decimal128(38, scale)))
        else:
            output[field.name] = pd.Series(values, dtype=frame[field.name].dtype)
    return ordered(pd.DataFrame(output), spec.output_row, spec.output_rows)


def _original_endpoint_check(
    spec: AttributeSpecV1,
    original: pd.DataFrame | None,
    scope_names: tuple[str, ...],
    scope: tuple[object, ...],
    endpoints: tuple[Number, ...],
) -> None:
    if spec.original_input_row is None:
        return
    if original is None:
        raise attribution_error(
            "original selected Delta endpoints for expanded axes", "missing original endpoints"
        )
    matching = [
        index for index, key in enumerate(frame_keys(original, scope_names)) if key == scope
    ]
    if not matching:
        raise attribution_error(
            "expanded scope belonging to selected original Delta", "unselected expansion scope"
        )
    if len(matching) != 1:
        raise attribution_error(
            "one original endpoint or source-folded original component proof",
            "ambiguous original scope endpoints",
        )
    for side, expected in zip(("current", "baseline"), endpoints, strict=True):
        if not reconciles(_finite(original[f"{side}_value"].iloc[matching[0]]), expected):
            raise attribution_error(
                "expanded partitions reproducing original endpoints",
                "expanded endpoint mismatch",
            )
