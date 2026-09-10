"""Source-native full outer comparison of exact Event funnel cells."""

from __future__ import annotations

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.domains.event_comparison import COUNTS, FunnelCompareSpec, FunnelDeltaSemantics


def complete_check(table: ir.Table) -> CompiledValidation:
    bad = (table.coverage_censored_count != 0) | (table.resolved_cohort_count != table.cohort_count)
    return CompiledValidation(
        "event.compare.complete",
        table.filter(bad).aggregate(violations=lambda t: t.count()),
        expected="complete follow-up classification for every compared Event step",
        repair="Rebuild both journeys with complete coverage before comparison.",
    )


def lower_compare(
    current: ir.Table, baseline: ir.Table, spec: FunnelCompareSpec
) -> tuple[ir.Table, tuple[CompiledValidation, ...]]:
    semantics = spec.output_row.family_semantics
    assert isinstance(semantics, FunnelDeltaSemantics)
    from marivo.analysis.compiler.predicates import lower_bound_predicate

    checks: tuple[CompiledValidation, ...] = (complete_check(current), complete_check(baseline))
    for predicate in spec.current_predicates:
        current = current.filter(lower_bound_predicate(current, predicate))
    for predicate in spec.baseline_predicates:
        baseline = baseline.filter(lower_bound_predicate(baseline, predicate))
    names = {field.field_id: field.name for field in spec.current_row.schema.columns}
    keys = tuple(names[key] for key in spec.current_row.key_field_ids)
    checks += tuple(
        CompiledValidation(
            f"event.compare.{side}.unique_coordinates",
            table.group_by(*keys)
            .having(table.count() > 1)
            .aggregate()
            .aggregate(violations=lambda duplicates: duplicates.count()),
            expected="unique exact funnel coordinates on each comparison side",
            repair="Rebuild the unfiltered funnel from its complete journey and retry.",
        )
        for side, table in (("current", current), ("baseline", baseline))
    )
    a = current.select(**{f"__current_{name}": current[name] for name in current.columns}).mutate(
        __current_present=True
    )
    b = baseline.select(
        **{f"__baseline_{name}": baseline[name] for name in baseline.columns}
    ).mutate(__baseline_present=True)
    joined = a.join(
        b, [a[f"__current_{key}"].identical_to(b[f"__baseline_{key}"]) for key in keys], how="outer"
    )
    present = joined.__current_present.notnull() & joined.__baseline_present.notnull()
    values: dict[str, ir.Value] = {
        key: ibis.coalesce(joined[f"__current_{key}"], joined[f"__baseline_{key}"]) for key in keys
    }
    values["coordinate_presence"] = present.ifelse(
        "matched", joined.__current_present.notnull().ifelse("current_only", "baseline_only")
    )
    for name in COUNTS:
        for side in ("current", "baseline"):
            values[f"{side}_{name}"] = joined[f"__{side}_{name}"].fill_null(0).cast("int64")
    out = joined.select(**values)
    valid = (
        (out.coordinate_presence == "matched")
        & (out.current_resolved_entry_count > 0)
        & (out.baseline_resolved_entry_count > 0)
        & (out.step_key != semantics.current.journey.pattern.steps[0].key)
    )
    noninitial = out.step_key != semantics.current.journey.pattern.steps[0].key
    out = out.mutate(
        current_loss_rate_from_previous=(
            noninitial & (out.current_resolved_entry_count > 0)
        ).ifelse(
            out.current_lost_count / out.current_resolved_entry_count, ibis.null().cast("float64")
        ),
        baseline_loss_rate_from_previous=(
            noninitial & (out.baseline_resolved_entry_count > 0)
        ).ifelse(
            out.baseline_lost_count / out.baseline_resolved_entry_count, ibis.null().cast("float64")
        ),
        calculation_status=(out.coordinate_presence != "matched").ifelse(
            "missing_side", valid.ifelse("ok", "zero_denominator")
        ),
    )
    out = out.mutate(
        loss_rate_delta=out.current_loss_rate_from_previous - out.baseline_loss_rate_from_previous
    )
    return out.select(*(f.name for f in spec.output_row.schema.columns)), checks
