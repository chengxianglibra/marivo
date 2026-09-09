"""Exact source-private distinct membership mapping and fractional allocation."""

from __future__ import annotations

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.attribution import _close, _finite, _group, _numeric
from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.observation.distinct_contracts import DISTINCT_KEY_COLUMN as _KEY
from marivo.analysis.operators.attribution_contracts import AttributeSpecV1, AttributionSemantics


def _join(left: ir.Table, right: ir.Table, keys: tuple[str, ...]) -> ir.Table:
    return left.join(
        right,
        [left[name].identical_to(right[name]) for name in keys],
        how="left" if keys else "cross",
    )


def lower_distinct_attribute(
    table: ir.Table,
    spec: AttributeSpecV1,
    *,
    current_membership: ir.Table,
    baseline_membership: ir.Table,
    original: ir.Table | None = None,
) -> tuple[ir.Table, tuple[CompiledValidation, ...]]:
    """Keep raw keys in source relations and prove every independent resolution."""
    semantics = spec.output_row.family_semantics
    if not isinstance(semantics, AttributionSemantics) or spec.method != "distinct_membership@v1":
        raise compilation_error("distinct membership Attribution", "invalid method authority")
    table = table.view()
    axes = tuple(field.name for field in spec.axis_fields)
    scopes = tuple(field.name for field in spec.scope_fields)
    keys = (*scopes, *axes)
    times = tuple(name for name in ("current_time", "baseline_time") if name in table.columns)
    checks: list[CompiledValidation] = []
    check_counts: dict[str, int] = {}

    def assertion(name: str, invalid: ir.Table) -> None:
        occurrence = check_counts.get(name, 0)
        check_counts[name] = occurrence + 1
        suffix = "" if occurrence == 0 else f".{occurrence + 1}"
        checks.append(
            CompiledValidation(
                "attribution.distinct." + name + suffix,
                invalid.aggregate(violations=invalid.count()),
            )
        )

    members: dict[str, ir.Table] = {}
    for side, raw in (("current", current_membership), ("baseline", baseline_membership)):
        if not {*keys, _KEY}.issubset(raw.columns):
            raise compilation_error("complete distinct membership relation", "missing keyed state")
        membership = raw.filter(raw[_KEY].notnull()).select(*keys, _KEY).distinct().view()
        members[side] = membership
        missing = membership.join(
            table,
            [membership[name].identical_to(table[name]) for name in keys],
            how="anti",
        )
        assertion(side + ".membership_coordinates", missing)
        counts = _group(membership, keys, {"__mv_count": membership.count()}).view()
        matched = _join(table, counts, keys)
        expected = counts.__mv_count.fill_null(0)
        assertion(
            side + ".row_endpoint", matched.filter(~table[side + "_value"].identical_to(expected))
        )
        absent = table.coordinate_presence == (
            "baseline_only" if side == "current" else "current_only"
        )
        assertion(side + ".absent_membership", matched.filter(absent & (expected != 0)))

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

    # Endpoints are counted from the original complete memberships, before mapping.
    endpoints = _group(
        table,
        scopes,
        {"__mv_scope_count": table.count(), **{name: table[name].max() for name in times}},
    )
    for side, membership in members.items():
        unique = membership.select(*scopes, _KEY).distinct()
        counts = _group(unique, scopes, {"__mv_endpoint_" + side: unique.count()}).view()
        combined = _join(endpoints, counts, scopes)
        name = "__mv_endpoint_" + side
        endpoints = combined.select(*endpoints.columns, **{name: counts[name].fill_null(0)})
    endpoints = endpoints.mutate(
        __mv_overall_delta=endpoints.__mv_endpoint_current - endpoints.__mv_endpoint_baseline
    )
    if original is not None:
        original_row = spec.original_input_row
        if original_row is None:
            raise compilation_error("original Delta selection contract", "missing expansion anchor")
        original_keys = tuple(
            field.name
            for field in original_row.schema.columns
            if field.field_id in original_row.key_field_ids
        )
        original = original.view()
        for side, membership in members.items():
            unique = membership.select(*original_keys, _KEY).distinct()
            counts = _group(unique, original_keys, {"__mv_count": unique.count()}).view()
            matched = _join(original, counts, original_keys)
            assertion(
                "expanded_" + side + "_endpoint",
                matched.filter(
                    ~original[side + "_value"].identical_to(counts.__mv_count.fill_null(0))
                ),
            )

    masks = tuple("__mv_other_" + str(index) for index in range(len(axes)))
    raw_keys = tuple("__mv_coordinate_" + str(index) for index in range(len(keys)))
    mapped = (
        table.select(*keys)
        .distinct()
        .mutate(
            **dict.fromkeys(masks, False),
            **{alias: table[name] for alias, name in zip(raw_keys, keys, strict=True)},
        )
    )

    def mapped_membership(membership: ir.Table) -> ir.Table:
        source = membership.view()
        mapping = mapped.view()
        combined = source.join(
            mapping,
            [
                source[name].identical_to(mapping[alias])
                for name, alias in zip(keys, raw_keys, strict=True)
            ],
            how="inner",
        )
        return combined.select(
            **{name: mapping[name] for name in (*keys, *masks)},
            **{_KEY: source[_KEY]},
        )

    if spec.top_k is not None:
        for index, axis in enumerate(axes):
            parent = (*scopes, *axes[:index], *masks[:index])
            selection_keys = (*parent, axis)
            current = mapped_membership(members["current"]).select(*selection_keys, _KEY)
            baseline = mapped_membership(members["baseline"]).select(*selection_keys, _KEY)
            union = current.union(baseline, distinct=True)
            score = _group(union, selection_keys, {"__mv_score": union.count()}).view()
            candidates = mapped.select(*selection_keys).distinct().view()
            joined = _join(candidates, score, selection_keys)
            scores = joined.select(*candidates.columns, __mv_score=score.__mv_score.fill_null(0))
            scores = scores.mutate(
                __mv_position=ibis.row_number().over(
                    ibis.window(
                        group_by=[scores[name] for name in parent],
                        order_by=[scores.__mv_score.desc(), scores[axis].asc(nulls_first=False)],
                    )
                )
            ).view()
            mapped = mapped.view()
            ranked = _join(mapped, scores, selection_keys)
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

    mapped_sides = {side: mapped_membership(membership) for side, membership in members.items()}
    resolutions = (len(axes),) if spec.mode == "joint" else tuple(range(1, len(axes) + 1))
    outputs: list[ir.Table] = []
    for size in resolutions:
        partition_keys = (*scopes, *axes[:size], *masks[:size])
        partitions = mapped.select(*partition_keys).distinct()
        for side, membership in mapped_sides.items():
            unique = membership.select(*partition_keys, _KEY).distinct().view()
            degrees = _group(unique, (*scopes, _KEY), {"__mv_degree": unique.count()}).view()
            joined = _join(unique, degrees, (*scopes, _KEY))
            weighted = joined.select(
                *unique.columns,
                __mv_weight=1.0 / _numeric(degrees.__mv_degree.cast("float64")),
            )
            allocation = _group(
                weighted, partition_keys, {side + "_value": _numeric(weighted.__mv_weight).sum()}
            ).view()
            combined = _join(partitions, allocation, partition_keys)
            partitions = combined.select(
                *partitions.columns, **{side + "_value": allocation[side + "_value"].fill_null(0.0)}
            )
        overall = endpoints.select(
            *scopes,
            "__mv_endpoint_current",
            "__mv_endpoint_baseline",
            "__mv_overall_delta",
            **{"__mv_scope_" + name: endpoints[name] for name in times},
        ).view()
        combined = _join(partitions, overall, scopes)
        joined = combined.select(
            *partitions.columns, *(overall[name] for name in overall.columns if name not in scopes)
        )
        joined = joined.mutate(contribution=joined.current_value - joined.baseline_value)
        totals = _group(
            joined,
            scopes,
            {
                "__mv_total": _numeric(joined.contribution).sum(),
                "__mv_positive": ibis.greatest(
                    _numeric(joined.contribution), ibis.literal(0.0)
                ).sum(),
                "__mv_negative": ibis.greatest(
                    -_numeric(joined.contribution), ibis.literal(0.0)
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
        for side in members:
            assertion(
                side + ".partition_reconciliation",
                totals.filter(
                    ~_close(totals["__mv_" + side + "_total"], totals["__mv_" + side + "_endpoint"])
                ),
            )
        pools = totals.view()
        combined = _join(joined, pools, scopes)
        result = combined.select(*joined.columns, pools.__mv_positive, pools.__mv_negative)
        contribution = _numeric(result.contribution.cast("float64"))
        output: dict[str, ir.Value] = {name: result[name] for name in scopes}
        output.update({name: result["__mv_scope_" + name] for name in times})
        output.update(
            {
                axis: result[axis] if index < size else ibis.null().cast(table[axis].type())
                for index, axis in enumerate(axes)
            }
        )
        output.update(
            active_axis_mask=ibis.literal(
                [index < size for index in range(len(axes))], type="array<boolean>"
            ),
            other_mask=ibis.array(
                [
                    result[masks[index]] if index < size else ibis.literal(False)
                    for index in range(len(axes))
                ]
            ),
            current_value=result.current_value,
            baseline_value=result.baseline_value,
            overall_delta=result.__mv_overall_delta,
            contribution=result.contribution,
            share_of_total_delta=contribution
            / _numeric(result.__mv_overall_delta.cast("float64")).nullif(0),
            share_of_positive_pool=ibis.greatest(contribution, ibis.literal(0.0))
            / _numeric(result.__mv_positive).nullif(0),
            share_of_negative_pool=ibis.greatest(-contribution, ibis.literal(0.0))
            / _numeric(result.__mv_negative).nullif(0),
            contribution_rank=(
                ibis.row_number().over(
                    ibis.window(
                        group_by=[result[name] for name in scopes],
                        order_by=[
                            contribution.abs().desc(),
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
        for name in ("current_value", "baseline_value", "overall_delta", "contribution"):
            assertion("output_finite", projected.filter(~_finite(projected[name])))
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
