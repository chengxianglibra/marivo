"""Complete source-owned player mapping and bounded distribution coalition values."""

from __future__ import annotations

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.attribution import _close, _finite, _group
from marivo.analysis.compiler.distribution import distribution_validations, frequency_quantile
from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.observation.distribution_contracts import FREQUENCY, VALUE
from marivo.analysis.observation.fold_contracts import decode_fold_authority
from marivo.analysis.operators.attribution_contracts import AttributeSpecV1

COALITION = "__mv_coalition"
PLAYERS = "__mv_players"
PLAYER_COUNT = "__mv_player_count"
COALITION_VALUE = "__mv_coalition_value"
CURRENT = "__mv_current_endpoint"
BASELINE = "__mv_baseline_endpoint"


def _join(left: ir.Table, right: ir.Table, keys: tuple[str, ...]) -> ir.Table:
    return left.join(
        right,
        [left[name].identical_to(right[name]) for name in keys],
        how="left" if keys else "cross",
    )


def lower_distribution_attribute(
    table: ir.Table,
    spec: AttributeSpecV1,
    *,
    current: ir.Table,
    baseline: ir.Table,
    original: ir.Table | None = None,
) -> tuple[ir.Table, tuple[CompiledValidation, ...]]:
    """Return only coalition values and non-identity coordinates, never distributions."""
    authority = decode_fold_authority(spec.current_fold_authority).metrics[0]
    assert authority.distribution is not None
    method = authority.distribution.quantile
    table = table.view()
    axes = tuple(field.name for field in spec.axis_fields)
    scopes = tuple(field.name for field in spec.scope_fields)
    keys = (*scopes, *axes)
    times = tuple(name for name in ("current_time", "baseline_time") if name in table.columns)
    checks = list(
        distribution_validations(
            spec.input_row,
            table,
            {"delta_distribution.current": current, "delta_distribution.baseline": baseline},
        )
    )

    def assertion(
        name: str, invalid: ir.Table, *, expected: str | None = None, repair: str | None = None
    ) -> None:
        checks.append(
            CompiledValidation(
                "attribution.distribution." + name,
                invalid.aggregate(violations=invalid.count()),
                expected,
                repair,
            )
        )

    endpoints = (
        table.select(*scopes, *times).distinct()
        if scopes or times
        else table.aggregate(__mv_anchor=table.count())
    ).view()
    for side, relation in (("current", current), ("baseline", baseline)):
        endpoint = frequency_quantile(relation, scopes, method).view()
        joined = _join(endpoints, endpoint, scopes)
        endpoints = joined.select(
            *endpoints.columns,
            **{CURRENT if side == "current" else BASELINE: endpoint.__mv_quantile},
        ).view()
    assertion(
        "finite_endpoints",
        endpoints.filter(~_finite(endpoints[CURRENT]) | ~_finite(endpoints[BASELINE])),
    )
    if scopes:
        counts = _group(endpoints, scopes, {"__mv_count": endpoints.count()})
        assertion("unique_scope_times", counts.filter(counts.__mv_count != 1))
    if original is not None:
        original = original.view()
        original_keys = (
            tuple(
                field.name
                for field in spec.original_input_row.schema.columns
                if field.field_id in spec.original_input_row.key_field_ids
            )
            if spec.original_input_row is not None
            else ()
        )
        for side, relation in (("current", current), ("baseline", baseline)):
            values = frequency_quantile(relation, original_keys, method).view()
            matched = _join(original, values, original_keys)
            assertion(
                "original_" + side,
                matched.filter(~_close(original[side + "_value"], values.__mv_quantile)),
            )

    masks = tuple("__mv_other_" + str(index) for index in range(len(axes)))
    raw_names = tuple("__mv_raw_" + str(index) for index in range(len(keys)))
    mapped = (
        table.select(*keys)
        .distinct()
        .mutate(
            **dict.fromkeys(masks, False),
            **{alias: table[name] for alias, name in zip(raw_names, keys, strict=True)},
        )
    )

    def mapped_distribution(relation: ir.Table) -> ir.Table:
        source, mapping = relation.view(), mapped.view()
        joined = source.join(
            mapping,
            [
                source[name].identical_to(mapping[alias])
                for name, alias in zip(keys, raw_names, strict=True)
            ],
            how="inner",
        )
        return joined.select(
            **{name: mapping[name] for name in (*keys, *masks)},
            **{VALUE: source[VALUE], FREQUENCY: source[FREQUENCY]},
        )

    if spec.top_k is not None:
        for index, axis in enumerate(axes):
            parent = (*scopes, *axes[:index], *masks[:index])
            selection = (*parent, axis)
            a, b = mapped_distribution(current), mapped_distribution(baseline)
            union = a.select(*selection, FREQUENCY).union(b.select(*selection, FREQUENCY))
            scores = _group(union, selection, {"__mv_score": union[FREQUENCY].sum()}).view()
            candidates = mapped.select(*selection).distinct().view()
            joined = _join(candidates, scores, selection)
            scores = joined.select(*candidates.columns, __mv_score=scores.__mv_score.fill_null(0))
            scores = scores.mutate(
                __mv_position=ibis.row_number().over(
                    ibis.window(
                        group_by=[scores[name] for name in parent],
                        order_by=[scores.__mv_score.desc(), scores[axis].asc(nulls_first=False)],
                    )
                )
            ).view()
            mapped = mapped.view()
            joined = _join(mapped, scores, selection)
            excluded = scores.__mv_position >= spec.top_k
            mapped = joined.select(
                **{
                    name: excluded.ifelse(ibis.null().cast(mapped[name].type()), mapped[name])
                    if name == axis
                    else excluded
                    if name == masks[index]
                    else mapped[name]
                    for name in mapped.columns
                }
            )

    current_mapped, baseline_mapped = mapped_distribution(current), mapped_distribution(baseline)
    outputs = []
    resolutions = (len(axes),) if spec.mode == "joint" else tuple(range(1, len(axes) + 1))
    for resolution in resolutions:
        active = tuple(index < resolution for index in range(len(axes)))
        partition = (*scopes, *axes[:resolution], *masks[:resolution])
        players = mapped.select(*partition).distinct().view()
        players = players.mutate(
            __mv_player=ibis.row_number().over(
                ibis.window(
                    group_by=[players[name] for name in scopes],
                    order_by=[
                        players[name].asc(nulls_first=False)
                        for name in (*axes[:resolution], *masks[:resolution])
                    ],
                )
            )
        ).view()
        counts = _group(players, scopes, {PLAYER_COUNT: players.count()}).view()
        assertion(
            f"players_{resolution}",
            counts.filter(counts[PLAYER_COUNT] > 8),
            expected="at most eight mapped players in every comparison scope and resolution",
            repair="Lower top_k or choose a coarser attribution axis; Other counts as a player.",
        )
        # The relation itself also prevents exponential work before preflight fails.
        counts = counts.filter(counts[PLAYER_COUNT] <= 8).view()
        player_struct = ibis.struct(
            {
                **{
                    axis: players[axis]
                    if index < resolution
                    else ibis.null().cast(table[axis].type())
                    for index, axis in enumerate(axes)
                },
                "other_mask": ibis.array(
                    [
                        players[masks[index]] if index < resolution else ibis.literal(False)
                        for index in range(len(axes))
                    ]
                ),
            }
        )
        inventory = _group(
            players, scopes, {PLAYERS: player_struct.collect(order_by=players.__mv_player)}
        ).view()
        base = _join(counts, inventory, scopes).select(*counts.columns, inventory[PLAYERS]).view()
        base = (
            _join(base, endpoints, scopes)
            .select(*base.columns, *[endpoints[name] for name in (*times, CURRENT, BASELINE)])
            .view()
        )
        base = base.mutate(
            **{COALITION: ibis.range((2 ** base[PLAYER_COUNT]).cast("int64")).unnest()}
        ).view()
        selected_sides = []
        for side, source in (("current", current_mapped), ("baseline", baseline_mapped)):
            source = source.view()
            joined = _join(source, players, partition)
            assigned = joined.select(
                *[source[name] for name in (*scopes, VALUE, FREQUENCY)], players.__mv_player
            ).view()
            candidates = assigned.join(
                base,
                [assigned[name].identical_to(base[name]) for name in scopes],
                how="inner" if scopes else "cross",
            )
            selected = ((base[COALITION] // ((2**assigned.__mv_player).cast("int64"))) % 2) == (
                1 if side == "current" else 0
            )
            candidates = candidates.filter(selected)
            selected_sides.append(
                candidates.select(
                    *[base[name] for name in (*scopes, COALITION)],
                    assigned[VALUE],
                    assigned[FREQUENCY],
                )
            )
        coalition = selected_sides[0].union(selected_sides[1])
        values = frequency_quantile(coalition, (*scopes, COALITION), method).view()
        joined = _join(base, values, (*scopes, COALITION))
        result = joined.select(
            *base.columns,
            **{COALITION_VALUE: values.__mv_quantile},
            active_axis_mask=ibis.array(list(active)),
        ).view()
        assertion(f"coalitions_{resolution}", result.filter(~_finite(result[COALITION_VALUE])))
        assertion(
            f"baseline_{resolution}",
            result.filter(
                (result[COALITION] == 0) & ~_close(result[COALITION_VALUE], result[BASELINE])
            ),
        )
        assertion(
            f"current_{resolution}",
            result.filter(
                (result[COALITION] == (2 ** result[PLAYER_COUNT]) - 1)
                & ~_close(result[COALITION_VALUE], result[CURRENT])
            ),
        )
        outputs.append(result)
    result = outputs[0]
    for output in outputs[1:]:
        result = result.union(output)
    return result.order_by(
        [result[name].asc(nulls_first=False) for name in (*scopes, "active_axis_mask", COALITION)]
    ), tuple(checks)
