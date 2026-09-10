"""Native Event ratio-mix allocation without journey or identity transfer."""

from __future__ import annotations

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.attribution import _close
from marivo.analysis.compiler.event_comparison import lower_compare
from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.domains.event_attribution import (
    COMPONENT_COLUMNS,
    COMPONENT_NAMES,
    METHOD,
    FunnelAttributeSpec,
)


def lower_attribute(
    original: ir.Table, current: ir.Table, baseline: ir.Table, spec: FunnelAttributeSpec
) -> tuple[ir.Table, tuple[CompiledValidation, ...]]:
    paired, incoming_checks = lower_compare(current, baseline, spec.expanded)
    table = paired.filter(paired.step_key == spec.step_key)
    selected = original.filter(original.step_key == spec.step_key)
    axes = tuple(f.name for f in spec.axis_fields)
    checks = list(incoming_checks)

    def check(name: str, invalid: ir.Table) -> None:
        checks.append(
            CompiledValidation(
                "event.attribute." + name, invalid.aggregate(violations=invalid.count())
            )
        )

    def aggregate(value: ir.Table, keys: tuple[str, ...]) -> ir.Table:
        metrics = {name: value[name].sum().fill_null(0).cast("int64") for name in COMPONENT_NAMES}
        return value.group_by(*keys).aggregate(**metrics) if keys else value.aggregate(**metrics)

    totals = aggregate(table, ())
    check("target_count", selected.aggregate(n=selected.count()).filter(lambda t: t.n != 1))
    check("target_defined", selected.filter(selected.calculation_status != "ok"))
    endpoint = totals.cross_join(
        selected.select(
            **{
                f"__original_{name}": selected[name]
                for name in (*COMPONENT_NAMES, "loss_rate_delta")
            }
        )
    )
    mismatch = ibis.literal(False)
    for name in COMPONENT_NAMES:
        mismatch = mismatch | (endpoint[name] != endpoint[f"__original_{name}"])
    check("endpoint_components", endpoint.filter(mismatch))
    check(
        "positive_denominators",
        totals.filter(
            (totals.current_resolved_entry_count <= 0) | (totals.baseline_resolved_entry_count <= 0)
        ),
    )
    totals = totals.mutate(
        __total_delta=totals.current_lost_count / totals.current_resolved_entry_count
        - totals.baseline_lost_count / totals.baseline_resolved_entry_count
    )
    check(
        "endpoint_delta",
        endpoint.filter(
            ~_close(
                endpoint.current_lost_count / endpoint.current_resolved_entry_count
                - endpoint.baseline_lost_count / endpoint.baseline_resolved_entry_count,
                endpoint.__original_loss_rate_delta,
            )
        ),
    )
    masks = tuple(f"__other_{i}" for i in range(len(axes)))
    mapped = table.select(*axes, *COMPONENT_NAMES).mutate(**dict.fromkeys(masks, False))
    if spec.top_k is not None:
        for index, axis in enumerate(axes):
            mapped = mapped.view()
            parent = (*axes[:index], *masks[:index])
            keys = (*parent, axis)
            scores = aggregate(mapped, keys)
            scores = scores.mutate(
                __score=scores.current_resolved_entry_count.cast("decimal(38,0)")
                + scores.baseline_resolved_entry_count.cast("decimal(38,0)")
            )
            scores = scores.mutate(
                __position=ibis.row_number().over(
                    ibis.window(
                        group_by=[scores[n] for n in parent],
                        order_by=[scores.__score.desc(), scores[axis].asc(nulls_first=False)],
                    )
                )
            ).view()
            joined = mapped.join(
                scores, [mapped[n].identical_to(scores[n]) for n in keys], how="left"
            )
            excluded = scores.__position >= spec.top_k
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
    outputs = []
    sizes = (len(axes),) if spec.mode == "joint" else tuple(range(1, len(axes) + 1))
    for size in sizes:
        partitions = aggregate(mapped, (*axes[:size], *masks[:size]))
        joined = partitions.cross_join(
            totals.select(
                **{f"__total_{name}": totals[name] for name in COMPONENT_NAMES},
                __total_delta=totals.__total_delta,
            )
        )
        denominator_a, denominator_b = (
            joined.__total_current_resolved_entry_count,
            joined.__total_baseline_resolved_entry_count,
        )
        loss_a, loss_b = (
            joined.current_lost_count / denominator_a,
            joined.baseline_lost_count / denominator_a,
        )
        mix_a = joined.baseline_lost_count * (1.0 / denominator_a - 1.0 / denominator_b)
        values: dict[str, ir.Value] = {
            **{
                name: joined[name] if i < size else ibis.null().cast(current[name].type())
                for i, name in enumerate(axes)
            },
            "active_axis_mask": ibis.array([ibis.literal(i < size) for i in range(len(axes))]),
            "other_mask": ibis.array(
                [joined[name] if i < size else ibis.literal(False) for i, name in enumerate(masks)]
            ),
            "overall_delta": joined.__total_delta,
            **{f"__event_{name}": joined[name] for name in COMPONENT_NAMES},
            **{f"__event_total_{name}": joined[f"__total_{name}"] for name in COMPONENT_NAMES},
        }
        loss = joined.select(
            **values,
            contribution_kind=ibis.literal("loss"),
            current_value=loss_a,
            baseline_value=loss_b,
            contribution=loss_a - loss_b,
        )
        mix = joined.select(
            **values,
            contribution_kind=ibis.literal("denominator_mix"),
            current_value=ibis.literal(0.0),
            baseline_value=-mix_a,
            contribution=mix_a,
        )
        output = ibis.union(loss, mix)
        reconciliation = output.aggregate(
            total=output.contribution.sum().fill_null(0), expected=output.overall_delta.max()
        )
        check(
            f"reconciliation_{size}",
            reconciliation.filter(~_close(reconciliation.total, reconciliation.expected)),
        )
        side_proof = output.aggregate(
            current=output.current_value.sum(), baseline=output.baseline_value.sum()
        ).cross_join(totals)
        check(
            f"side_endpoints_{size}",
            side_proof.filter(
                ~_close(
                    side_proof.current,
                    side_proof.current_lost_count / side_proof.current_resolved_entry_count,
                )
                | ~_close(
                    side_proof.baseline,
                    side_proof.baseline_lost_count / side_proof.baseline_resolved_entry_count,
                )
            ),
        )
        pools = output.aggregate(
            positive=(output.contribution > 0).ifelse(output.contribution, 0).sum(),
            negative=(output.contribution < 0).ifelse(-output.contribution, 0).sum(),
        )
        output = output.cross_join(pools)
        value = output.contribution
        output = output.mutate(
            share_of_total_delta=(output.overall_delta != 0).ifelse(
                value / output.overall_delta, ibis.null().cast("float64")
            ),
            share_of_positive_pool=(pools.positive > 0).ifelse(
                (value > 0).ifelse(value, 0) / pools.positive, ibis.null().cast("float64")
            ),
            share_of_negative_pool=(pools.negative > 0).ifelse(
                (value < 0).ifelse(-value, 0) / pools.negative, ibis.null().cast("float64")
            ),
            contribution_rank=(
                ibis.row_number().over(
                    ibis.window(
                        order_by=[
                            value.abs().desc(),
                            *(output[n].asc(nulls_first=False) for n in axes),
                            output.other_mask.asc(),
                            output.contribution_kind.asc(),
                        ]
                    )
                )
                + 1
            ).cast("int64"),
            method=ibis.literal(METHOD),
            causal_claim=ibis.literal("none"),
            status=(output.overall_delta == 0).ifelse("zero_total_delta", "ok"),
        )
        outputs.append(
            output.select(*(f.name for f in spec.output_row.schema.columns), *COMPONENT_COLUMNS)
        )
    return ibis.union(*outputs), tuple(checks)
