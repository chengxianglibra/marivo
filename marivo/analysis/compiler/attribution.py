"""Exact source partition mapping, allocation and independent reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.comparison import lower_compare
from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.observation.fold_contracts import (
    MetricFoldAuthorityV1,
    coverage_columns,
    decode_fold_authority,
    fold_state_columns,
    fold_state_names,
)
from marivo.analysis.operators.attribution_contracts import (
    AttributeSpecV1,
    AttributionSemantics,
    delta_presence_name,
    delta_state_name,
)

if TYPE_CHECKING:
    from marivo.analysis.operators.driver_contracts import DriverCandidateSpecV1


def _numeric(value: ir.Value) -> ir.NumericValue:
    if not isinstance(value, ir.NumericValue):
        raise compilation_error("numeric Attribution component", "invalid component expression")
    return value


def _finite(value: ir.Value) -> ir.BooleanValue:
    result = value.notnull()
    return (
        result & ~value.isnan() & ~value.isinf() if isinstance(value, ir.FloatingValue) else result
    )


def _close(left: ir.Value, right: ir.Value) -> ir.BooleanValue:
    a, b = _numeric(left.cast("float64")), _numeric(right.cast("float64"))
    tolerance = ibis.greatest(
        ibis.literal(1e-12), 1e-9 * ibis.greatest(a.abs(), b.abs(), ibis.literal(1.0))
    )
    return (_finite(a) & _finite(b) & ((a - b).abs() <= tolerance)).fill_null(False)


def _reconciles(left: ir.Value, right: ir.Value) -> ir.BooleanValue:
    if isinstance(left, ir.FloatingValue) or isinstance(right, ir.FloatingValue):
        return _close(left, right)
    return (_finite(left) & _finite(right) & left.identical_to(right)).fill_null(False)


def _value(
    table: ir.Table, authority: MetricFoldAuthorityV1, side: str, node_id: str | None = None
) -> ir.Value:
    nodes = {node.node_id: node for node in authority.nodes}
    components = {component.node_id: component for component in authority.components}

    def visit(identity: str) -> ir.Value:
        node = nodes[identity]
        if node.kind == "component":
            component = components[identity]
            names = {state: delta_state_name(side, name) for state, name in component.state_columns}
            if component.kind == "count":
                return table[names["count"]].fill_null(0)
            if component.kind == "mean":
                return _numeric(table[names["sum"]]) / _numeric(
                    table[names["non_null_count"]]
                ).nullif(0)
            if component.kind == "weighted_mean":
                return _numeric(table[names["weighted_numerator"]]) / _numeric(
                    table[names["weight_sum"]]
                ).nullif(0)
            result = table[names["sum" if component.kind == "sum" else "value"]]
            if component.empty_rule == "zero":
                return result.fill_null(0)
            if "non_null_count" in names:
                return (table[names["non_null_count"]] > 0).ifelse(
                    result, ibis.null().cast(result.type())
                )
            return result
        if node.kind == "identity":
            return visit(node.children[0])
        if node.kind == "ratio":
            return _numeric(visit(node.children[0])) / _numeric(visit(node.children[1])).nullif(0)
        result = _numeric(visit(node.children[0])) * node.coefficients[0]
        for child, coefficient in zip(node.children[1:], node.coefficients[1:], strict=True):
            result = result + _numeric(visit(child)) * coefficient
        return result

    return visit(authority.root_id if node_id is None else node_id)


def _mix(
    table: ir.Table, authority: MetricFoldAuthorityV1, side: str
) -> tuple[ir.NumericValue, ir.NumericValue]:
    nodes = {node.node_id: node for node in authority.nodes}
    root = nodes[authority.root_id]
    while root.kind == "identity":
        root = nodes[root.children[0]]
    if root.kind == "ratio":
        return _numeric(_value(table, authority, side, root.children[0])), _numeric(
            _value(table, authority, side, root.children[1])
        )
    component = next(item for item in authority.components if item.node_id == root.node_id)
    names = dict(component.state_columns)
    numerator, basis = (
        ("sum", "non_null_count")
        if component.kind == "mean"
        else ("weighted_numerator", "weight_sum")
    )
    numerator_value = _numeric(table[delta_state_name(side, names[numerator])])
    basis_value = _numeric(table[delta_state_name(side, names[basis])])
    empty = numerator_value.isnull() & ((basis_value == 0) | basis_value.isnull())
    return _numeric(empty.ifelse(0, numerator_value)), _numeric(empty.ifelse(0, basis_value))


def _group(table: ir.Table, keys: tuple[str, ...], metrics: dict[str, ir.Value]) -> ir.Table:
    return (table.group_by(list(keys)) if keys else table).aggregate(**metrics)


def exact_partition_fold(
    source: ir.Table,
    authorities: dict[str, MetricFoldAuthorityV1],
    keys: tuple[str, ...],
    times: tuple[str, ...] = (),
    *,
    exact_floating: bool = False,
) -> ir.Table:
    """Fold exact retained side state without computing allocation or shares."""
    values: dict[str, ir.Value] = {}
    for side, authority in authorities.items():
        coverage = set(coverage_columns(authority))
        for name in fold_state_names(authority):
            target = delta_state_name(side, name)
            column = source[target]
            if name in coverage:
                populated = source[delta_state_name(side, coverage_columns(authority)[0])].notnull()
                if isinstance(column, ir.BooleanColumn):
                    values[target] = column.all(where=populated).fill_null(False)
                elif column.type().is_numeric():
                    values[target] = column.max(where=populated).fill_null(0)
                else:
                    values[target] = column.max(where=populated)
            elif column.type().is_numeric():
                numeric = _numeric(column)
                if column.type().is_integer():
                    numeric = _numeric(column.cast("decimal(38,0)"))
                if exact_floating and isinstance(numeric, ir.FloatingValue):
                    from marivo.analysis.compiler.driver_numeric import exact_float_sum

                    values[target] = exact_float_sum(numeric).fill_null(0)
                else:
                    values[target] = numeric.sum().fill_null(0)
            elif column.type().is_boolean():
                if not isinstance(column, ir.BooleanColumn):
                    raise compilation_error("Boolean component column", "invalid coverage column")
                values[target] = column.all()
            else:
                values[target] = column.max()
        presence = source[delta_presence_name(side)]
        if not isinstance(presence, ir.BooleanColumn):
            raise compilation_error("Boolean side presence", "invalid presence column")
        values[delta_presence_name(side)] = presence.any()
    for name in times:
        values[name] = source[name].max()
    return _group(source, keys, values)


@dataclass(frozen=True, slots=True, repr=False)
class ExactPartition:
    table: ir.Table
    authorities: dict[str, MetricFoldAuthorityV1]
    endpoints: ir.Table
    checks: tuple[CompiledValidation, ...]
    axes: tuple[str, ...]
    scopes: tuple[str, ...]
    times: tuple[str, ...]


def prepare_exact_partition(
    table: ir.Table,
    spec: AttributeSpecV1 | DriverCandidateSpecV1,
    *,
    method: str,
    empty_scope_allowed: bool = False,
    exact_floating: bool = False,
    original: ir.Table | None = None,
) -> ExactPartition:
    """Validate complete side state and independently reconcile exact endpoints."""
    table = table.view()
    authorities = {
        "current": decode_fold_authority(spec.current_fold_authority).metrics[0],
        "baseline": decode_fold_authority(spec.baseline_fold_authority).metrics[0],
    }
    checks: list[CompiledValidation] = []
    check_counts: dict[str, int] = {}

    def assertion(name: str, invalid: ir.Table) -> None:
        occurrence = check_counts.get(name, 0)
        check_counts[name] = occurrence + 1
        name = name if occurrence == 0 else f"{name}.{occurrence + 1}"
        checks.append(
            CompiledValidation("attribution." + name, invalid.aggregate(violations=invalid.count()))
        )

    axes = tuple(field.name for field in spec.axis_fields)
    scopes = tuple(field.name for field in spec.scope_fields)
    times = tuple(name for name in ("current_time", "baseline_time") if name in table.columns)
    states = tuple(
        delta_state_name(side, name)
        for side, authority in authorities.items()
        for name in fold_state_names(authority)
    )
    required = {*states, *(delta_presence_name(side) for side in authorities)}
    if not required.issubset(table.columns):
        raise compilation_error(
            "complete Delta side components", "missing retained Attribution state"
        )
    for name in times:
        counts = _group(
            table,
            scopes,
            {
                "__mv_distinct": table[name].nunique(),
                "__mv_null": table[name].isnull().any().cast("int64"),
            },
        )
        assertion(
            "paired_time_constant", counts.filter(counts.__mv_distinct + counts.__mv_null > 1)
        )

    def aggregate(source: ir.Table, keys: tuple[str, ...]) -> ir.Table:
        return exact_partition_fold(source, authorities, keys, times, exact_floating=exact_floating)

    for side, authority in authorities.items():
        presence = table[delta_presence_name(side)]
        expected_presence = table.coordinate_presence != (
            "baseline_only" if side == "current" else "current_only"
        )
        assertion(side + ".presence", table.filter(~presence.identical_to(expected_presence)))
        for name, _, nullable in fold_state_columns(authority):
            column = table[delta_state_name(side, name)]
            invalid = ~presence & column.notnull()
            if not nullable:
                invalid = invalid | (presence & column.isnull())
            if column.type().is_numeric():
                invalid = invalid | (column.notnull() & ~_finite(column))
            assertion(side + ".state_valid", table.filter(invalid))
        for component in authority.components:
            names = dict(component.state_columns)
            for kind, name in component.state_columns:
                if kind.endswith("count"):
                    column = table[delta_state_name(side, name)]
                    invalid = presence & (column < 0)
                    if "row_count" in names and kind != "row_count":
                        invalid = invalid | (
                            presence & (column > table[delta_state_name(side, names["row_count"])])
                        )
                    assertion(side + ".support_valid", table.filter(invalid))
        if authority.cumulative:
            coverage = tuple(delta_state_name(side, name) for name in coverage_columns(authority))
            endpoint, start, end, seconds, complete = coverage
            beginning, ending = table[start].cast("timestamp"), table[end].cast("timestamp")
            if not isinstance(beginning, ir.TimestampValue) or not isinstance(
                ending, ir.TimestampValue
            ):
                raise compilation_error("temporal coverage authority", "invalid coverage types")
            empty = (
                table[endpoint].isnull()
                & table[start].isnull()
                & table[end].isnull()
                & table[seconds].identical_to(0)
                & table[complete].identical_to(False)
            )
            for component in authority.components:
                for kind, name in component.state_columns:
                    if kind.endswith("count"):
                        empty = empty & table[delta_state_name(side, name)].identical_to(0)
            populated_valid = (
                table[endpoint].notnull()
                & table[start].notnull()
                & table[end].notnull()
                & table[seconds].notnull()
                & table[complete].notnull()
                & table[endpoint].identical_to(table[end])
                & (table[start] <= table[end])
                & table[seconds].identical_to(
                    ending.delta(beginning, unit="microsecond") / 1_000_000
                )
            )
            assertion(
                side + ".coverage_valid",
                table.filter(presence & ~(empty | populated_valid).fill_null(False)),
            )
            populated = table.filter(presence & table[endpoint].notnull())
            counts = _group(
                populated, scopes, {name: populated[name].nunique() for name in coverage}
            )
            invalid_coverage = counts[coverage[0]] > 1
            for name in coverage[1:]:
                invalid_coverage = invalid_coverage | (counts[name] > 1)
            assertion(side + ".coverage_aligned", counts.filter(invalid_coverage))
        recomputed = _value(table, authority, side)
        # A present null endpoint remains undefined, including an empty mean.
        equal = table[side + "_value"].identical_to(recomputed)
        assertion(side + ".row_endpoint", table.filter(presence & ~equal))
        if method == "additive_difference@v1":
            assertion(
                side + ".raw_partition_finite", table.filter(~_finite(table[side + "_value"]))
            )
        if method == "component_mix@v1":
            numerator, basis = _mix(table, authority, side)
            assertion(
                side + ".component_finite",
                table.filter(presence & (~_finite(numerator) | ~_finite(basis))),
            )
            assertion(
                side + ".zero_basis", table.filter(presence & (basis == 0) & (numerator != 0))
            )

    endpoints = aggregate(table, scopes)
    if empty_scope_allowed and not scopes and original is None:
        endpoints = endpoints.cross_join(table.aggregate(__mv_input_count=table.count()))
        endpoints = endpoints.filter(endpoints.__mv_input_count > 0)
    endpoint_values: dict[str, ir.Value] = {}
    for side, authority in authorities.items():
        value = _value(endpoints, authority, side)
        endpoint_values["__mv_endpoint_" + side] = value
        if method == "component_mix@v1":
            endpoint_values["__mv_" + side + "_basis"] = _mix(endpoints, authority, side)[1]
        assertion(side + ".endpoint_finite", endpoints.filter(~_finite(value)))
    endpoints = endpoints.mutate(**endpoint_values)
    endpoint_delta = _numeric(endpoints.__mv_endpoint_current) - _numeric(
        endpoints.__mv_endpoint_baseline
    )
    endpoints = endpoints.mutate(__mv_overall_delta=endpoint_delta)
    assertion("overall_delta_finite", endpoints.filter(~_finite(endpoints.__mv_overall_delta)))

    if original is not None:
        original_row = spec.original_input_row
        if original_row is None:
            raise compilation_error("original Delta selection contract", "missing expansion anchor")
        original_keys = tuple(
            field.name
            for field in original_row.schema.columns
            if field.field_id in original_row.key_field_ids
        )
        expanded_by_original = aggregate(table, original_keys)
        right = original.view()
        matched = expanded_by_original.join(
            right,
            [expanded_by_original[name].identical_to(right[name]) for name in original_keys],
            how="outer" if original_keys else "cross",
        )
        for side, authority in authorities.items():
            assertion(
                "expanded_" + side + "_endpoint",
                matched.filter(
                    ~_reconciles(_value(matched, authority, side), right[side + "_value"])
                ),
            )

    return ExactPartition(table, authorities, endpoints, tuple(checks), axes, scopes, times)


def lower_attribute(
    table: ir.Table, spec: AttributeSpecV1, *, original: ir.Table | None = None
) -> tuple[ir.Table, tuple[CompiledValidation, ...]]:
    """Compute every complete scope and resolution in the exact admitted engine."""
    prepared = prepare_exact_partition(table, spec, method=spec.method, original=original)
    table, authorities, endpoints = prepared.table, prepared.authorities, prepared.endpoints
    axes, scopes, times = prepared.axes, prepared.scopes, prepared.times
    checks = list(prepared.checks)
    check_counts: dict[str, int] = {}

    def assertion(name: str, invalid: ir.Table) -> None:
        occurrence = check_counts.get(name, 0)
        check_counts[name] = occurrence + 1
        name = name if occurrence == 0 else f"{name}.{occurrence + 1}"
        checks.append(
            CompiledValidation("attribution." + name, invalid.aggregate(violations=invalid.count()))
        )

    def aggregate(source: ir.Table, keys: tuple[str, ...]) -> ir.Table:
        return exact_partition_fold(source, authorities, keys, times)

    masks = tuple("__mv_other_" + str(index) for index in range(len(axes)))
    mapped = table.mutate(**dict.fromkeys(masks, False))
    if spec.top_k is not None:
        for index, axis in enumerate(axes):
            mapped = mapped.view()
            parent = (*scopes, *axes[:index], *masks[:index])
            selection_keys = (*parent, axis)
            scores = aggregate(mapped, selection_keys)
            values = tuple(
                _value(scores, authority, side)
                if spec.method == "additive_difference@v1"
                else _mix(scores, authority, side)[1]
                for side, authority in authorities.items()
            )
            scores = scores.select(
                *selection_keys,
                __mv_score=_numeric(values[0].fill_null(0)).abs()
                + _numeric(values[1].fill_null(0)).abs(),
            )
            assertion("selection_score_finite", scores.filter(~_finite(scores.__mv_score)))
            scores = scores.mutate(
                __mv_position=ibis.row_number().over(
                    ibis.window(
                        group_by=[scores[name] for name in parent],
                        order_by=[scores.__mv_score.desc(), scores[axis].asc(nulls_first=False)],
                    )
                )
            )
            scores = scores.view()
            ranked = mapped.join(
                scores,
                [mapped[name].identical_to(scores[name]) for name in selection_keys],
                how="left",
            )
            excluded = scores.__mv_position >= spec.top_k
            mapped = ranked.select(
                **{
                    name: excluded.ifelse(ibis.null().cast(mapped[name].type()), mapped[name])
                    if name == axis
                    else excluded
                    if name == masks[index]
                    else mapped[name]
                    for name in mapped.columns
                }
            )

    resolutions = (len(axes),) if spec.mode == "joint" else tuple(range(1, len(axes) + 1))
    outputs: list[ir.Table] = []
    for size in resolutions:
        keys = (*scopes, *axes[:size], *masks[:size])
        partitions = aggregate(mapped, keys)
        endpoint_columns = (
            *scopes,
            "__mv_endpoint_current",
            "__mv_endpoint_baseline",
            "__mv_overall_delta",
            *(
                ("__mv_current_basis", "__mv_baseline_basis")
                if spec.method == "component_mix@v1"
                else ()
            ),
        )
        overall = endpoints.select(
            *endpoint_columns, **{"__mv_scope_" + name: endpoints[name] for name in times}
        ).view()
        joined = partitions.join(
            overall,
            [partitions[name].identical_to(overall[name]) for name in scopes],
            how="left" if scopes else "cross",
        )
        output_values: dict[str, ir.Value] = {}
        for side, authority in authorities.items():
            if spec.method == "additive_difference@v1":
                value = _value(joined, authority, side)
            else:
                numerator, basis = _mix(joined, authority, side)
                total = _numeric(joined["__mv_" + side + "_basis"])
                value = ((numerator == 0) & (basis == 0)).ifelse(
                    0.0,
                    (basis.cast("float64") / total.cast("float64").nullif(0))
                    * (numerator.cast("float64") / basis.cast("float64").nullif(0)),
                )
            output_values[side + "_value"] = value
        joined = joined.mutate(**output_values)
        contribution = _numeric(joined.current_value) - _numeric(joined.baseline_value)
        joined = joined.mutate(contribution=contribution)
        assertion("contribution_finite", joined.filter(~_finite(joined.contribution)))
        totals = _group(
            joined,
            scopes,
            {
                "__mv_total": _numeric(joined.contribution).sum(),
                "__mv_positive": ibis.greatest(
                    _numeric(joined.contribution), ibis.literal(0)
                ).sum(),
                "__mv_negative": ibis.greatest(
                    -_numeric(joined.contribution), ibis.literal(0)
                ).sum(),
                "__mv_delta": joined.__mv_overall_delta.max(),
                "__mv_current_total": _numeric(joined.current_value).sum(),
                "__mv_baseline_total": _numeric(joined.baseline_value).sum(),
                "__mv_current_endpoint": joined.__mv_endpoint_current.max(),
                "__mv_baseline_endpoint": joined.__mv_endpoint_baseline.max(),
                "__mv_partition_count": joined.count(),
            },
        )
        totals = totals.filter(totals.__mv_partition_count > 0)
        assertion("reconciliation", totals.filter(~_close(totals.__mv_total, totals.__mv_delta)))
        assertion("positive_pool_finite", totals.filter(~_finite(totals.__mv_positive)))
        assertion("negative_pool_finite", totals.filter(~_finite(totals.__mv_negative)))
        assertion(
            "current_partition_reconciliation",
            totals.filter(~_close(totals.__mv_current_total, totals.__mv_current_endpoint)),
        )
        assertion(
            "baseline_partition_reconciliation",
            totals.filter(~_close(totals.__mv_baseline_total, totals.__mv_baseline_endpoint)),
        )
        pools = totals.view()
        result = joined.join(
            pools,
            [joined[name].identical_to(pools[name]) for name in scopes],
            how="left" if scopes else "cross",
        )
        result = result.select(*joined.columns, pools.__mv_positive, pools.__mv_negative)
        semantics = spec.output_row.family_semantics
        if not isinstance(semantics, AttributionSemantics):
            raise compilation_error("Attribution row semantics", "invalid output semantics")
        numeric_type = semantics.numeric_type
        for name in ("current_value", "baseline_value", "contribution", "__mv_overall_delta"):
            column = _numeric(result[name])
            if numeric_type == "int64":
                assertion(
                    "signed_integer_range",
                    result.filter((column < -(2**63)) | (column > 2**63 - 1)),
                )
        output: dict[str, ir.Value] = {name: result[name] for name in scopes}
        output.update({name: result["__mv_scope_" + name] for name in times})
        output.update(
            {
                axis: result[axis] if index < size else ibis.null().cast(table[axis].type())
                for index, axis in enumerate(axes)
            }
        )
        output["active_axis_mask"] = ibis.literal(
            [index < size for index in range(len(axes))], type="array<boolean>"
        )
        output["other_mask"] = ibis.array(
            [
                result[masks[index]] if index < size else ibis.literal(False)
                for index in range(len(axes))
            ]
        )
        contribution_float = _numeric(result.contribution.cast("float64"))
        delta_float = _numeric(result.__mv_overall_delta.cast("float64"))
        output.update(
            current_value=result.current_value,
            baseline_value=result.baseline_value,
            overall_delta=result.__mv_overall_delta,
            contribution=result.contribution,
            share_of_total_delta=contribution_float / delta_float.nullif(0),
            share_of_positive_pool=ibis.greatest(contribution_float, ibis.literal(0.0))
            / _numeric(result.__mv_positive.cast("float64")).nullif(0),
            share_of_negative_pool=ibis.greatest(-contribution_float, ibis.literal(0.0))
            / _numeric(result.__mv_negative.cast("float64")).nullif(0),
            contribution_rank=(
                ibis.row_number().over(
                    ibis.window(
                        group_by=[result[name] for name in scopes],
                        order_by=[
                            _numeric(result.contribution).abs().desc(),
                            *(
                                result[name].asc(nulls_first=False)
                                for name in (*axes[:size], *masks[:size])
                            ),
                        ],
                    )
                )
                + 1
            ).cast("int64"),
            status=(result.__mv_overall_delta == 0).ifelse("zero_total_delta", "ok"),
        )
        for field in spec.output_row.schema.columns:
            if field.logical_type_id in ("int64", "float64"):
                output[field.name] = output[field.name].cast(field.logical_type_id)
        projected = result.select(
            **{field.name: output[field.name] for field in spec.output_row.schema.columns}
        )
        for name in ("share_of_total_delta", "share_of_positive_pool", "share_of_negative_pool"):
            assertion(
                "share_finite",
                projected.filter(projected[name].notnull() & ~_finite(projected[name])),
            )
        outputs.append(projected)
    expression = outputs[0]
    for output_table in outputs[1:]:
        expression = expression.union(output_table)
    return expression, tuple(checks)


def _anchor_side(
    original: ir.Table,
    expanded: ir.Table,
    row: DatasetRowContract,
    original_row: DatasetRowContract,
    side: str,
) -> ir.Table:
    fields = {field.field_id: field.name for field in row.schema.columns}
    anchors = tuple(
        field
        for field in original_row.schema.columns
        if field.field_id in original_row.key_field_ids and field.name != "comparison_ordinal"
    )
    time = next(
        (field.name for field in row.schema.columns if field.role_id == "time_dimension"), None
    )
    projection = {fields[field.field_id]: original[field.name] for field in anchors}
    if time is not None:
        projection[time] = original[side + "_time"]
        projection["comparison_ordinal"] = original.comparison_ordinal
    selected = (
        original.select(**projection).distinct().view()
        if projection
        else original.select(__mv_anchor=True).distinct().view()
    )
    keys = tuple(name for name in projection if name != "comparison_ordinal")
    joined = expanded.join(
        selected,
        [expanded[name].identical_to(selected[name]) for name in keys],
        how="inner" if keys else "cross",
    )
    return joined.select(
        *expanded.columns, *((selected.comparison_ordinal,) if time is not None else ())
    )


def prepare_expanded_attribute(
    original: ir.Table,
    current: ir.Table,
    baseline: ir.Table,
    spec: AttributeSpecV1 | DriverCandidateSpecV1,
) -> tuple[ir.Table, tuple[CompiledValidation, ...]]:
    """Keep original selected coordinates and original paired-time ordinal authority."""
    comparison, original_row = spec.expanded_compare, spec.original_input_row
    if comparison is None or original_row is None:
        raise compilation_error(
            "complete logical expansion authority", "missing expansion contract"
        )
    current = _anchor_side(original, current, comparison.current_row, original_row, "current")
    baseline = _anchor_side(original, baseline, comparison.baseline_row, original_row, "baseline")
    delta, compare_checks = lower_compare(current, baseline, comparison, ordinal_preassigned=True)
    if "comparison_ordinal" in delta.columns:
        delta = delta.view()
        keys = tuple(
            field.name
            for field in original_row.schema.columns
            if field.field_id in original_row.key_field_ids
        )
        anchor = original.select(
            *keys,
            current_time_anchor=original.current_time,
            baseline_time_anchor=original.baseline_time,
        ).view()
        matched = delta.join(
            anchor, [delta[name].identical_to(anchor[name]) for name in keys], how="left"
        )
        delta = matched.select(
            **{
                name: anchor.current_time_anchor
                if name == "current_time"
                else anchor.baseline_time_anchor
                if name == "baseline_time"
                else delta[name]
                for name in delta.columns
            }
        )
    return delta, compare_checks


def lower_expanded_attribute(
    original: ir.Table, current: ir.Table, baseline: ir.Table, spec: AttributeSpecV1
) -> tuple[ir.Table, tuple[CompiledValidation, ...]]:
    """Apply additive/component attribution to its exact selected expanded Delta."""
    delta, compare_checks = prepare_expanded_attribute(original, current, baseline, spec)
    result, checks = lower_attribute(delta, spec, original=original)
    return result, (*compare_checks, *checks)
