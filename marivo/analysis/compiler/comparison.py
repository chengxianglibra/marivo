"""Exact source lowering for the private Metric comparison contract."""

from __future__ import annotations

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.operators.contracts import CompareSpecV1, DeltaSemantics


def _numeric(value: ir.Value) -> ir.NumericValue:
    if not isinstance(value, ir.NumericValue):
        raise TypeError("comparison operands require numeric Ibis values")
    return value


def _finite(value: ir.Value) -> ir.BooleanValue:
    valid = value.notnull()
    if isinstance(value, ir.FloatingValue):
        valid = valid & ~value.isnan() & ~value.isinf()
    return valid


def _full_join(
    left: ir.Table, right: ir.Table, predicates: list[ir.BooleanValue], nonnull_left_column: str
) -> ir.Table:
    """Preserve null-safe FULL JOIN using a guaranteed non-null left column."""
    matched_or_left = left.join(right, predicates, how="left")
    right_only = left.join(right, predicates, how="right").filter(
        lambda table: table[nonnull_left_column].isnull()
    )
    return matched_or_left.union(right_only)


def lower_compare(
    current: ir.Table,
    baseline: ir.Table,
    spec: CompareSpecV1,
    *,
    ordinal_preassigned: bool = False,
    emulate_full_join: bool = False,
) -> tuple[ir.Table, tuple[CompiledValidation, ...]]:
    """Compose exact operands and own their output coordinates and paired times.

    Dependent relations consume these output coordinates, including any preassigned
    comparison ordinal, instead of independently deriving time alignment.
    """
    semantics = spec.output_row.family_semantics
    if not isinstance(semantics, DeltaSemantics):
        raise TypeError("comparison requires Delta semantics")
    current_fields = {field.field_id: field.name for field in spec.current_row.schema.columns}
    baseline_fields = {field.field_id: field.name for field in spec.baseline_row.schema.columns}
    output_keys = tuple(
        field
        for field in spec.output_row.schema.columns
        if field.field_id in spec.output_row.key_field_ids
    )
    current_names = {
        current_fields[field.field_id]: field.name
        for field in output_keys
        if field.field_id in current_fields
    }
    baseline_names = {
        baseline_fields[field.field_id]: field.name
        for field in output_keys
        if field.field_id in baseline_fields
    }
    current = current.select(
        **{current_names.get(name, name): current[name] for name in current.columns}
    )
    baseline = baseline.select(
        **{baseline_names.get(name, name): baseline[name] for name in baseline.columns}
    )
    validations: list[CompiledValidation] = []

    def assertion(name: str, invalid: ir.Table) -> None:
        validations.append(CompiledValidation(name, invalid.aggregate(violations=invalid.count())))

    keys = tuple(
        field.name
        for field in spec.output_row.schema.columns
        if field.field_id in spec.output_row.key_field_ids
    )
    dimensions = tuple(name for name in keys if name != "comparison_ordinal")
    time_name = next(
        (
            field.name
            for field in spec.current_row.schema.columns
            if field.role_id == "time_dimension"
        ),
        None,
    )
    if time_name is not None and not ordinal_preassigned:
        baseline_time = next(
            (
                field.name
                for field in spec.baseline_row.schema.columns
                if field.role_id == "time_dimension"
            ),
            None,
        )
        if baseline_time is None:
            raise TypeError("paired comparison time coordinates are required")
        current_counts = (current.group_by(list(dimensions)) if dimensions else current).aggregate(
            __mv_current_count=current.count()
        )
        baseline_counts = (
            baseline.group_by(list(dimensions)) if dimensions else baseline
        ).aggregate(__mv_baseline_count=baseline.count())
        count_predicates = [
            current_counts[name].identical_to(baseline_counts[name]) for name in dimensions
        ]
        paired_counts = (
            _full_join(current_counts, baseline_counts, count_predicates, "__mv_current_count")
            if dimensions and emulate_full_join
            else current_counts.join(
                baseline_counts, count_predicates, how="outer" if dimensions else "cross"
            )
        )
        assertion(
            "compare.equal_time_bucket_counts",
            paired_counts.filter(
                ~paired_counts.__mv_current_count.identical_to(paired_counts.__mv_baseline_count)
            ),
        )
        current = current.mutate(
            comparison_ordinal=(
                ibis.row_number().over(
                    ibis.window(
                        group_by=[current[name] for name in dimensions], order_by=current[time_name]
                    )
                )
            ).cast("int64")
        )
        baseline = baseline.mutate(
            comparison_ordinal=(
                ibis.row_number().over(
                    ibis.window(
                        group_by=[baseline[name] for name in dimensions],
                        order_by=baseline[baseline_time],
                    )
                )
            ).cast("int64")
        )
    left = current.select(
        **{f"__mv_current_{name}": current[name] for name in current.columns}
    ).mutate(__mv_current_present=True)
    right = baseline.view()
    right = right.select(**{f"__mv_baseline_{name}": right[name] for name in right.columns}).mutate(
        __mv_baseline_present=True
    )
    predicates = [
        left[f"__mv_current_{name}"].identical_to(right[f"__mv_baseline_{name}"]) for name in keys
    ]
    joined = (
        _full_join(left, right, predicates, "__mv_current_present")
        if keys and emulate_full_join
        else left.join(right, predicates, how="outer" if keys else "cross")
    )
    current_present = joined.__mv_current_present.fill_null(False)
    baseline_present = joined.__mv_baseline_present.fill_null(False)
    if spec.promoted_type == "decimal":
        left_type = current[spec.current_metric_name].type()
        right_type = baseline[spec.baseline_metric_name].type()
        if not isinstance(left_type, dt.Decimal) or not isinstance(right_type, dt.Decimal):
            raise TypeError("comparison requires exact physical Decimal types")
        kind: dt.DataType = dt.Decimal(38, max(left_type.scale or 0, right_type.scale or 0))
    else:
        kind = dt.dtype(spec.promoted_type)
    current_value = joined[f"__mv_current_{spec.current_metric_name}"].cast(kind)
    baseline_value = joined[f"__mv_baseline_{spec.baseline_metric_name}"].cast(kind)
    if spec.exact_empty_zero:
        current_value = current_present.ifelse(current_value, ibis.literal(0).cast(kind))
        baseline_value = baseline_present.ifelse(baseline_value, ibis.literal(0).cast(kind))
    assertion(
        "compare.current_finite", joined.filter(current_value.notnull() & ~_finite(current_value))
    )
    assertion(
        "compare.baseline_finite",
        joined.filter(baseline_value.notnull() & ~_finite(baseline_value)),
    )
    calculation_ok = current_value.notnull() & baseline_value.notnull()
    if kind.is_integer():
        raw_delta = _numeric(current_value.cast("decimal(38,0)")) - _numeric(
            baseline_value.cast("decimal(38,0)")
        )
        assertion(
            "compare.signed_integer_range",
            joined.filter((raw_delta < -(2**63)) | (raw_delta > 2**63 - 1)),
        )
    else:
        raw_delta = _numeric(current_value) - _numeric(baseline_value)
    delta = raw_delta.cast(kind)
    assertion("compare.delta_finite", joined.filter(delta.notnull() & ~_finite(delta)))
    if kind.is_decimal():
        assertion("compare.lossless_decimal_delta", joined.filter(~raw_delta.identical_to(delta)))
    delta_float = _numeric(delta.cast("float64"))
    baseline_float = _numeric(baseline_value.cast("float64"))
    relative = delta_float / baseline_float.abs().nullif(0)
    relative_ok = (
        calculation_ok & _finite(delta_float) & _finite(baseline_float) & _finite(relative)
    )
    missing = ~(current_present & baseline_present) & ibis.literal(not spec.exact_empty_zero)
    output: dict[str, ir.Value] = {
        name: ibis.coalesce(joined[f"__mv_current_{name}"], joined[f"__mv_baseline_{name}"])
        for name in keys
    }
    if time_name is not None:
        baseline_time = next(
            field.name
            for field in spec.baseline_row.schema.columns
            if field.role_id == "time_dimension"
        )
        output["current_time"] = joined[f"__mv_current_{time_name}"]
        output["baseline_time"] = joined[f"__mv_baseline_{baseline_time}"]
    output.update(
        coordinate_presence=(current_present & baseline_present).ifelse(
            "matched", current_present.ifelse("current_only", "baseline_only")
        ),
        current_value=current_value,
        baseline_value=baseline_value,
        delta=delta,
        relative_delta=relative_ok.ifelse(relative, ibis.null().cast("float64")),
        calculation_status=missing.ifelse(
            "missing_side", calculation_ok.ifelse("ok", "null_input")
        ),
        relative_delta_status=(calculation_ok & (baseline_value == 0)).ifelse(
            "baseline_zero", relative_ok.ifelse("ok", "delta_unavailable")
        ),
    )
    from marivo.analysis.compiler.lowering import retained_part_specs

    output.update(
        __mv_current_present=current_present,
        __mv_baseline_present=baseline_present,
    )
    for side, row, present in (
        ("current", spec.current_row, current_present),
        ("baseline", spec.baseline_row, baseline_present),
    ):
        visible = {field.name for field in row.schema.columns}
        for part in retained_part_specs(row):
            for name in part.column_names:
                if name in visible:
                    continue
                target = f"__mv_{side}_{name}"
                if target not in joined.columns:
                    raise TypeError("comparison requires complete admitted sufficient state")
                value = joined[target]
                output[target] = present.ifelse(value, ibis.null().cast(value.type()))
    retained = tuple(
        name
        for part in retained_part_specs(spec.output_row)
        for name in part.column_names
        if name not in {field.name for field in spec.output_row.schema.columns}
    )
    return joined.select(
        **{
            name: output[name]
            for name in (*[field.name for field in spec.output_row.schema.columns], *retained)
        }
    ), tuple(validations)
