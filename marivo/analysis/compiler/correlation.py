"""Source-private coordinate preparation and exact Pearson/Spearman reduction."""

from __future__ import annotations

from itertools import combinations

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.compiler.predicates import _boolean
from marivo.analysis.observation.fold_contracts import decode_fold_authority
from marivo.analysis.operators.association_contracts import (
    COUNT_NAMES,
    MAX_CANDIDATES,
    PAIR_NAMES,
    SELECTION_TERMS,
    CorrelateSpecV1,
    candidate_count,
)


def _check(name: str, bad: ir.Table) -> CompiledValidation:
    return CompiledValidation(
        name,
        bad.aggregate(violations=bad.count()),
        expected="complete finite correlation inputs and a valid candidate per pair/series",
        repair="Narrow Metrics or lags, or repair missing, constant and non-finite observations.",
    )


def _shift(value: ir.Value, lag: int, spec: CorrelateSpecV1) -> ir.Value:
    authority = decode_fold_authority(spec.semantics.fold_authority)
    grain = authority.time_grain()
    if grain is None:
        raise compilation_error("exact correlation time grain", "missing temporal authority")
    if grain.kind == "semantic" and grain.level != "day":
        snapshot = authority.temporal_snapshot()
        if snapshot is None:
            raise compilation_error("bound certified calendar", "missing lag calendar")
        periods = tuple(
            p
            for p in sorted(snapshot.periods, key=lambda p: p.start_date)
            if p.level_name == grain.level
        )
        cases = tuple(
            (
                _boolean(value == p.start_date),
                ibis.literal(periods[i + lag].start_date).cast(value.type()),
            )
            for i, p in enumerate(periods)
            if 0 <= i + lag < len(periods)
        )
        return (
            ibis.cases(*cases, else_=ibis.null().cast(value.type()))
            if cases
            else ibis.null().cast(value.type())
        )
    unit = grain.unit if grain.kind == "builtin" else "day"
    count = (grain.count or 1) * lag
    if not -(2**31) <= count < 2**31:
        # No timestamp in the supported range can match this offset.
        return ibis.null().cast(value.type())
    return (value.cast("timestamp") + ibis.interval(**{str(unit) + "s": count})).cast(value.type())


def prepare_pairs(
    table: ir.Table, spec: CorrelateSpecV1
) -> tuple[ir.Table, tuple[CompiledValidation, ...]]:
    dims = spec.dimensions
    parts = []
    bad = ibis.literal(False)
    for name in spec.metric_names:
        value = table[name].cast("float64")
        bad = bad | (table[name].notnull() & (value.isnan() | value.isinf()))
    total = table.aggregate(__total=table.count())
    checks = [
        _check("correlate.finite_input", table.filter(bad)),
        _check("correlate.nonempty_input", total.filter(total.__total == 0)),
    ]
    per_series = candidate_count(len(spec.metric_names), len(spec.semantics.lag_offsets))
    if dims:
        series = table.select(*dims).distinct()
        cardinality = series.aggregate(__candidates=series.count() * per_series)
    else:
        cardinality = table.aggregate(__candidates=ibis.literal(per_series, type="int64"))
    checks.append(
        _check(
            "correlate.candidate_ceiling",
            cardinality.filter(cardinality.__candidates > MAX_CANDIDATES),
        )
    )
    for a, b in combinations(range(len(spec.metric_names)), 2):
        for lag in spec.semantics.lag_offsets:
            columns = [*dims]
            if spec.time_name is not None:
                columns.append(spec.time_name)
            left = table.select(*columns, __a=table[spec.metric_names[a]])
            if lag == 0:
                paired = table.select(
                    *dims,
                    __a=table[spec.metric_names[a]],
                    __b=table[spec.metric_names[b]],
                    __matched=ibis.literal(True),
                )
            else:
                assert spec.time_name is not None
                right = table.view()
                right = right.select(
                    *columns, __b=right[spec.metric_names[b]], __matched=ibis.literal(True)
                )
                conditions = [left[name].identical_to(right[name]) for name in dims]
                conditions.append(_shift(left[spec.time_name], lag, spec) == right[spec.time_name])
                paired = left.left_join(right, conditions).select(
                    *[left[name] for name in dims], left.__a, right.__b, right.__matched
                )
            good = paired.__matched.fill_null(False) & paired.__a.notnull() & paired.__b.notnull()
            summary = paired.aggregate(
                by=list(dims),
                input_observation_count=paired.count().cast("int64"),
                matched_observation_count=paired.__matched.fill_null(False)
                .cast("int64")
                .sum()
                .fill_null(0),
                complete_pair_count=good.cast("int64").sum().fill_null(0),
            )
            summary = summary.mutate(
                null_pair_count=summary.matched_observation_count - summary.complete_pair_count
            )
            complete = paired.filter(good).select(*dims, value_a=paired.__a, value_b=paired.__b)
            # Normalize each complete pair before UNION can coerce unlike Metric types.
            # Centering preserves Pearson; average ranks preserve Spearman and tau-b.
            values = {}
            for name in ("value_a", "value_b"):
                value = complete[name]
                if spec.semantics.method == "pearson":
                    if value.type().is_integer():
                        value = value.cast("decimal(38,0)")
                    values[name] = value - value.min().over(ibis.window(group_by=list(dims)))
                else:
                    first = ibis.rank().over(ibis.window(group_by=list(dims), order_by=value)) + 1
                    ties = complete.count().over(ibis.window(group_by=[*dims, name]))
                    values[name] = first + (ties - 1) / 2
            complete = complete.mutate(
                **{name: value.cast("float64") for name, value in values.items()}
            )
            summary = summary.mutate(__join=ibis.literal(1))
            complete = complete.mutate(__join=ibis.literal(1))
            predicates = [summary[name].identical_to(complete[name]) for name in dims]
            predicates.append(summary.__join == complete.__join)
            joined = summary.left_join(complete, predicates)
            part = joined.select(
                *[summary[name] for name in dims],
                metric_key_a=ibis.literal(spec.semantics.metric_keys[a]),
                metric_key_b=ibis.literal(spec.semantics.metric_keys[b]),
                lag_offset=ibis.literal(lag, type="decimal(19,0)").cast("int64"),
                **{name: summary[name].cast("decimal(38,0)").cast("int64") for name in COUNT_NAMES},
                value_a=complete.value_a,
                value_b=complete.value_b,
            )
            parts.append(part)
    prepared = ibis.union(*parts) if len(parts) > 1 else parts[0]
    return prepared.select(*dims, *PAIR_NAMES), tuple(checks)


def lower_correlate(
    table: ir.Table, spec: CorrelateSpecV1
) -> tuple[ir.Table, tuple[CompiledValidation, ...]]:
    if spec.semantics.method == "kendall":
        raise compilation_error(
            "registered bounded Kendall continuation", "Kendall cannot reduce in the source"
        )
    prepared, checks = prepare_pairs(table, spec)
    keys = [*spec.dimensions, "metric_key_a", "metric_key_b", "lag_offset"]
    complete = prepared.filter(prepared.complete_pair_count > 0)
    numeric = complete.group_by(keys).aggregate(
        __coefficient=complete.value_a.corr(complete.value_b, how="pop"),
        __a_count=complete.value_a.nunique(),
        __b_count=complete.value_b.nunique(),
    )
    metadata = prepared.select(*keys, *COUNT_NAMES).distinct()
    joined = metadata.left_join(numeric, [metadata[k].identical_to(numeric[k]) for k in keys])
    result = joined.select(
        *[metadata[k] for k in (*keys, *COUNT_NAMES)],
        numeric.__coefficient,
        numeric.__a_count,
        numeric.__b_count,
    )
    status = ibis.cases(
        (result.complete_pair_count < 2, "insufficient_pairs"),
        ((result.__a_count == 1) & (result.__b_count == 1), "constant_both"),
        (result.__a_count == 1, "constant_a"),
        (result.__b_count == 1, "constant_b"),
        else_="valid",
    )
    result = result.mutate(
        status=status,
        # DuckDB corr can exceed the closed interval by floating-point roundoff.
        # Validate the original result below before accepting the normalized value.
        coefficient=(status == "valid").ifelse(
            (result.__coefficient.abs() >= 1 - 1e-12).ifelse(
                (result.__coefficient >= 0).ifelse(1.0, -1.0), result.__coefficient
            ),
            ibis.null().cast("float64"),
        ),
    )
    groups = [*spec.dimensions, "metric_key_a", "metric_key_b"]
    valid = result.filter(result.status == "valid")
    count = result.group_by(groups).aggregate(
        __valid=(result.status == "valid").cast("int64").sum()
    )
    checks = (
        *checks,
        _check("correlate.valid_candidate", count.filter(count.__valid == 0)),
        _check(
            "correlate.finite_coefficient",
            valid.filter(
                valid.__coefficient.isnull()
                | valid.__coefficient.isnan()
                | valid.__coefficient.isinf()
                | (valid.__coefficient.abs() > 1 + 1e-12)
            ),
        ),
    )
    if spec.time_name is not None:
        values = {"coefficient": result.coefficient, "lag_offset": result.lag_offset}
        order = [ibis.desc(result.status == "valid")]
        for name, transform, direction in SELECTION_TERMS:
            value = values[name]
            if name == "lag_offset":
                # abs(INT64_MIN) needs one extra bit while the retained lag stays int64.
                value = value.cast("decimal(38,0)")
            key = value.abs() if transform == "absolute" else value
            order.append(key.desc() if direction == "descending" else key.asc())
        selected = ibis.row_number().over(ibis.window(group_by=groups, order_by=order)) == 0
        result = result.mutate(
            selected_for_pair=selected & (result.status == "valid"),
            lag_boundary_drop_count=result.input_observation_count
            - result.matched_observation_count,
        )
    return result.select(*[f.name for f in spec.output_row.schema.columns]), checks
