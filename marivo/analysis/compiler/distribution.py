"""Source-only exact value-frequency relations and registered percentile recipes."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.attribution import _close, _finite, _group, _numeric
from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.nodes import CompiledValidation, RetainedRelationSpec
from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.observation.distribution_contracts import (
    FREQUENCY,
    VALUE,
    distribution_endpoint_name,
    distribution_part_authorities,
)
from marivo.analysis.operators.contracts import CompareSpecV1
from marivo.semantic._quantile import QuantileMethodV1


def _ordered_tdigest(values: list[float], method: str, q: float) -> float:
    """DuckDB evaluates one canonical sorted vector using its T-Digest aggregate."""
    raise NotImplementedError("source builtin signature only")


_tdigest: Callable[[ir.Value, str, float], ir.Value] = ibis.udf.scalar.builtin(
    _ordered_tdigest, name="list_aggregate"
)


def source_quantile(value: ir.NumericValue, method: QuantileMethodV1) -> ir.Value:
    if method.method == "duckdb_tdigest@v1":
        numeric = value.cast("float64")
        return _tdigest(
            numeric.collect(order_by=numeric, where=numeric.notnull()), "approx_quantile", method.q
        )
    return value.cast("float64").quantile(method.q)


def frequency_quantile(
    table: ir.Table, keys: tuple[str, ...], method: QuantileMethodV1
) -> ir.Table:
    """Evaluate exact interpolation or canonical backend replay without local values."""
    table = table.view()
    if method.method == "duckdb_tdigest@v1":
        expanded = table.mutate(__mv_repeat=ibis.range(table[FREQUENCY]).unnest())
        return _group(
            expanded, keys, {"__mv_quantile": source_quantile(_numeric(expanded[VALUE]), method)}
        )
    grouped = _group(table, (*keys, VALUE), {FREQUENCY: table[FREQUENCY].sum()}).view()
    window = ibis.window(group_by=[grouped[name] for name in keys])
    ordered = ibis.cumulative_window(
        group_by=[grouped[name] for name in keys], order_by=grouped[VALUE]
    )
    ranks = grouped.mutate(
        __mv_total=grouped[FREQUENCY].sum().over(window),
        __mv_end=grouped[FREQUENCY].sum().over(ordered),
    ).view()
    position = (ranks.__mv_total - 1) * method.q
    lower, upper = position.floor(), position.ceil()
    start = ranks.__mv_end - ranks[FREQUENCY]
    value = ranks[VALUE].cast("float64")
    low = ((start <= lower) & (lower < ranks.__mv_end)).ifelse(value, ibis.null().cast("float64"))
    high = ((start <= upper) & (upper < ranks.__mv_end)).ifelse(value, ibis.null().cast("float64"))
    result = _group(
        ranks,
        keys,
        {"__mv_low": low.max(), "__mv_high": high.max(), "__mv_fraction": (position - lower).max()},
    )
    return result.select(
        *keys,
        __mv_quantile=result.__mv_low + (result.__mv_high - result.__mv_low) * result.__mv_fraction,
    )


def row_keys(row: DatasetRowContract) -> tuple[str, ...]:
    return tuple(field.name for field in row.schema.columns if field.field_id in row.key_field_ids)


def selected_distributions(
    parts: tuple[tuple[str, ir.Table], ...],
    row: DatasetRowContract,
    primary: ir.Table,
    *,
    required: bool = True,
) -> tuple[tuple[str, ir.Table], ...]:
    roles = {role for role, _ in distribution_part_authorities(row)}
    keys = row_keys(row)
    result = []
    for role, relation in parts:
        if role not in roles:
            continue
        anchor = primary.select(*keys).distinct().view() if keys else primary
        selected = (
            relation.join(
                anchor, [relation[name].identical_to(anchor[name]) for name in keys], how="semi"
            )
            if keys
            else relation.filter(anchor.count() > 0)
        )
        result.append((role, selected.select(*keys, VALUE, FREQUENCY)))
    if required and {role for role, _ in result} != roles:
        raise compilation_error("complete exact distribution roles", "missing distribution state")
    return tuple(result)


def distribution_specs(
    row: DatasetRowContract, parts: tuple[tuple[str, ir.Table], ...]
) -> tuple[RetainedRelationSpec, ...]:
    available = dict(parts)
    result = []
    for role, _ in distribution_part_authorities(row):
        if role not in available:
            raise compilation_error(
                "registered source distribution relation", "missing distribution basis"
            )
        result.append(
            RetainedRelationSpec(role, f"{row.shape_id.family_id}.distribution", 1, available[role])
        )
    return tuple(result)


def comparison_distributions(
    comparison: ir.Table,
    current_parts: tuple[tuple[str, ir.Table], ...],
    baseline_parts: tuple[tuple[str, ir.Table], ...],
    spec: CompareSpecV1,
) -> tuple[tuple[str, ir.Table], ...]:
    output = {field.field_id: field.name for field in spec.output_row.schema.columns}
    result = []
    for side, row, parts, absent in (
        ("current", spec.current_row, current_parts, "baseline_only"),
        ("baseline", spec.baseline_row, baseline_parts, "current_only"),
    ):
        authorities = distribution_part_authorities(row)
        if not authorities:
            continue
        if len(authorities) != 1 or authorities[0][0] not in dict(parts):
            raise compilation_error(
                "one complete comparison distribution", "missing side distribution"
            )
        relation = dict(parts)[authorities[0][0]].view()
        coordinates = {
            field.name: f"{side}_time"
            if field.role_id == "time_dimension"
            else output[field.field_id]
            for field in row.schema.columns
            if field.field_id in row.key_field_ids
        }
        selected = comparison.filter(comparison.coordinate_presence != absent).view()
        joined = relation.join(
            selected,
            [relation[name].identical_to(selected[target]) for name, target in coordinates.items()],
            how="inner" if coordinates else "cross",
        )
        result.append(
            (
                f"delta_distribution.{side}",
                joined.select(
                    **{name: selected[name] for name in row_keys(spec.output_row)},
                    **{VALUE: relation[VALUE], FREQUENCY: relation[FREQUENCY]},
                ),
            )
        )
    return tuple(result)


def distribution_validations(
    row: DatasetRowContract,
    primary: ir.Table,
    parts: Mapping[str, ir.Table],
    *,
    required: bool = True,
) -> tuple[CompiledValidation, ...]:
    checks = []
    keys = row_keys(row)
    primary = primary.view()

    def assertion(name: str, invalid: ir.Table) -> None:
        checks.append(CompiledValidation(name, invalid.aggregate(violations=invalid.count())))

    for role, authority in distribution_part_authorities(row):
        relation = parts.get(role)
        if relation is None:
            if not required:
                continue
            raise compilation_error(
                "complete retained distribution basis", "missing distribution role"
            )
        relation = relation.view()
        if tuple(relation.columns) != (*keys, VALUE, FREQUENCY):
            raise compilation_error("exact distribution schema", "invalid distribution fields")
        assertion(
            role + ".values",
            relation.filter(
                ~_finite(relation[VALUE])
                | relation[FREQUENCY].isnull()
                | (relation[FREQUENCY] <= 0)
            ),
        )
        counts = _group(relation, (*keys, VALUE), {"__mv_count": relation.count()})
        assertion(role + ".unique", counts.filter(counts.__mv_count != 1))
        if keys:
            assertion(
                role + ".support",
                relation.join(
                    primary,
                    [relation[name].identical_to(primary[name]) for name in keys],
                    how="anti",
                ),
            )
        assert authority.distribution is not None
        endpoints = frequency_quantile(relation, keys, authority.distribution.quantile).view()
        matched = primary.join(
            endpoints,
            [primary[name].identical_to(endpoints[name]) for name in keys],
            how="left" if keys else "cross",
        )
        actual, expected = primary[distribution_endpoint_name(row, role)], endpoints.__mv_quantile
        assertion(
            role + ".endpoint",
            matched.filter(~((actual.isnull() & expected.isnull()) | _close(actual, expected))),
        )
    return tuple(checks)
