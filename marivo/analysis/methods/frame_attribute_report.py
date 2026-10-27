"""Publish checked source contributions as a target report with complete inert facts."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, localcontext
from math import fsum, isfinite, ulp
from typing import Literal

import pandas as pd
import pyarrow as pa

from marivo.analysis.core.attribute import (
    AttributeClosureWitness,
    AttributeContributionProgram,
    AttributeFailureContext,
    AttributeInputError,
    AttributeMethodPayload,
    AttributeParentFiber,
    AttributeUseContext,
)
from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.data_types import (
    DataColumn,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.core.data_validation import stage_arrow_type
from marivo.analysis.core.frame_method_facts import (
    FrameAdditiveMethod,
    FrameAttributeMetric,
    FrameDivisionMethod,
    FrameLevelAdditiveMethod,
    FrameLevelDivisionMethod,
    FrameLevelLinearMethod,
    FrameLevelRatioMethod,
    FrameLinearMethod,
    FrameMethodOutput,
    FrameMethodParent,
    FrameMethodTarget,
    FrameMethodValue,
    FrameRatioMethod,
    _key_order,
    _LevelMethod,
    _Method,
)
from marivo.analysis.core.frozen_calendar import frozen_key_rows
from marivo.analysis.core.model import reject
from marivo.analysis.materialization.graph_exchange import StageOutput, source_columns
from marivo.analysis.methods.frame_attribute_contribution import _numeric_binary, _Value
from marivo.semantic._numeric_lowering import numeric_failure


def _scalar(value: _Value, index: int) -> FrameMethodValue:
    array = pa.array(value.values, type=stage_arrow_type(value.column.dtype))
    if isinstance(value.column.dtype, DurationDataType):
        array = array.cast(pa.int64())
    return FrameMethodValue(value.column, array[index].as_py())


def _series(value: FrameMethodValue) -> _Value:
    physical = stage_arrow_type(value.column.dtype)
    array = (
        pa.array([value.value], type=pa.int64()).cast(physical)
        if isinstance(value.column.dtype, DurationDataType)
        else pa.array([value.value], type=physical)
    )
    return _Value(pd.Series(pd.arrays.ArrowExtensionArray(array)), value.column)


def _report_value(value: _Value, column: DataColumn) -> _Value:
    """Align report values through a safe cast, without rounding their inputs."""
    if isinstance(column.dtype, DurationDataType) and value.column.dtype == column.dtype:
        array = pa.array(value.values)
        if array.type != stage_arrow_type(column.dtype):
            # Additive rules can retain integral mass in a Decimal carrier.
            # Publish that already typed Duration quantity in its exact ticks.
            if not (
                pa.types.is_integer(array.type)
                or pa.types.is_null(array.type)
                or pa.types.is_decimal(array.type)
            ):
                numeric_failure("exact integral Attribute Duration ticks", str(array.type))
            try:
                exact = array.cast(pa.int64(), safe=True).cast(stage_arrow_type(column.dtype))
            except (pa.ArrowException, ArithmeticError, TypeError, ValueError) as error:
                numeric_failure("exact signed int64 Attribute Duration ticks", str(error))
            return _Value(pd.Series(pd.arrays.ArrowExtensionArray(exact)), column)
    if value.column.dtype == column.dtype:
        return _Value(value.values, column)
    source = value.column.dtype
    if (
        not isinstance(column.dtype, DecimalDataType)
        or not (isinstance(source, DecimalDataType) or source == ScalarDataType("int64"))
        or (isinstance(source, DecimalDataType) and column.dtype.scale < source.scale)
    ):
        numeric_failure(
            "a lossless common Attribute report carrier",
            f"{column.name}: incompatible {source!r} to {column.dtype!r}",
        )
    try:
        original = pa.array(value.values, type=stage_arrow_type(value.column.dtype))
        exact = original.cast(stage_arrow_type(column.dtype), safe=True)
    except (pa.ArrowException, ArithmeticError, TypeError, ValueError) as error:
        numeric_failure(
            "a lossless common Attribute report carrier",
            f"{column.name}: {value.column.dtype!r} to {column.dtype!r}: {error}",
        )
    return _Value(pd.Series(pd.arrays.ArrowExtensionArray(exact)), column)


def _report_method(
    method: _Method | _LevelMethod,
    output: dict[tuple[str, ...], DataColumn],
    target: tuple[str, ...],
) -> _Method | _LevelMethod:
    """Retain parent scalar values in their matching lossless diagnostic carriers."""

    def value(captured: FrameMethodValue, *path: str) -> FrameMethodValue:
        column = output[(*path, *target)]
        aligned = replace(captured.column, dtype=column.dtype, nullable=column.nullable)
        return _scalar(_report_value(_series(captured), aligned), 0)

    if isinstance(
        method, (FrameLevelAdditiveMethod, FrameLevelLinearMethod, FrameLevelDivisionMethod)
    ):
        return replace(method, value=value(method.value, "detail", "value"))
    if isinstance(method, FrameLevelRatioMethod):
        return replace(
            method,
            value=value(method.value, "detail", "value"),
            numerator=value(method.numerator, "component", "value", "numerator"),
            denominator=value(method.denominator, "component", "value", "denominator"),
        )
    if isinstance(method, FrameRatioMethod):
        return replace(
            method,
            left=value(method.left, "detail", "current"),
            right=value(method.right, "detail", "baseline"),
            change=value(method.change, "detail", "absolute"),
            numerator_left=value(method.numerator_left, "component", "current", "numerator"),
            numerator_right=value(method.numerator_right, "component", "baseline", "numerator"),
            denominator_left=value(method.denominator_left, "component", "current", "denominator"),
            denominator_right=value(
                method.denominator_right, "component", "baseline", "denominator"
            ),
        )
    return replace(
        method,
        left=value(method.left, "detail", "current"),
        right=value(method.right, "detail", "baseline"),
        change=value(method.change, "detail", "absolute"),
    )


def _closure(
    values: _Value,
    total: FrameMethodValue,
    method: _Method | _LevelMethod,
    *,
    relative: bool,
    exact_integer: bool = False,
    parent: AttributeParentFiber,
    target: FrameMethodTarget,
    uses: tuple[AttributeUseContext, ...] = (),
) -> None:
    """Check only the requested final target, retaining exact typed failure values."""
    array = pa.array(values.values, type=stage_arrow_type(values.column.dtype))
    if isinstance(values.column.dtype, DurationDataType):
        array = array.cast(pa.int64())
    cells = array.to_pylist()
    if any(cell is None for cell in cells):
        reject(
            "known complete fine contributions",
            total.column.name,
            "Inspect unknown source components before attribution.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    expected = total.value
    assert isinstance(expected, (int, float, Decimal))
    dtype = values.column.dtype
    actual: int | float | Decimal
    residual: int | float | Decimal
    tolerance: int | float | Decimal
    policy: Literal["exact_integer", "duration_ticks", "decimal_rounding", "float64"]
    if isinstance(dtype, DecimalDataType):
        assert isinstance(expected, (int, Decimal))
        assert all(isinstance(cell, (int, Decimal)) for cell in cells)
        total_scale = (
            total.column.dtype.scale if isinstance(total.column.dtype, DecimalDataType) else 0
        )
        scale = max(dtype.scale, total_scale)
        with localcontext() as context:
            # Physical values have at most 38 digits. The count adds only its
            # decimal width; preserve every input digit before comparing.
            context.prec = max(120, 38 + len(str(len(cells) + 1)) + scale)
            actual = sum(cells, Decimal(0))
            residual = actual - expected
            tolerance = (
                Decimal(0)
                if exact_integer
                else Decimal(len(cells) + 1) * Decimal(1).scaleb(-scale) / 2
            )
            valid = abs(residual) <= tolerance
        policy = "exact_integer" if exact_integer else "decimal_rounding"
    elif isinstance(expected, float):
        assert not exact_integer

        def precision_error(rule: _Method | _LevelMethod) -> float:
            captured = (
                (rule.left, rule.right)
                if isinstance(
                    rule,
                    (FrameAdditiveMethod, FrameRatioMethod, FrameLinearMethod, FrameDivisionMethod),
                )
                else (rule.value,)
            )
            error = fsum(
                ulp(abs(value.value)) / 2 for value in captured if type(value.value) is float
            )
            if isinstance(rule, (FrameLinearMethod, FrameLevelLinearMethod)):
                error += fsum(
                    abs(float(term.coefficient)) * precision_error(term.method)
                    for term in rule.terms
                )
            if isinstance(rule, (FrameDivisionMethod, FrameLevelDivisionMethod)):
                error += precision_error(rule.method) / abs(float(rule.divisor))
            return error

        try:
            actual = fsum(cells)
            # Native endpoint rounding survives cancellation; relative targets
            # express that same budget in units of the fresh absolute baseline.
            source_error = precision_error(method)
            if relative:
                assert isinstance(
                    method,
                    (FrameAdditiveMethod, FrameRatioMethod, FrameLinearMethod, FrameDivisionMethod),
                )
                assert isinstance(method.right.value, (int, float, Decimal))
                baseline = abs(float(method.right.value))
                source_error /= baseline
                if type(method.right.value) is float:
                    source_error += abs(expected) * ((ulp(abs(method.right.value)) / baseline) / 2)
            magnitude = max(abs(expected), fsum(abs(value) for value in cells))
            tolerance = 16 * (len(cells) + 1) * (source_error + ulp(magnitude))
            # Accept business closure within 1% of the requested final target,
            # retaining the numeric floor for zero totals and cancellation.
            tolerance = max(tolerance, abs(expected) * 0.01)
            residual = actual - expected
            if not all(
                isfinite(value)
                for value in (actual, expected, source_error, magnitude, tolerance, residual)
            ):
                raise ValueError("nonfinite captured closure budget")
        except (ArithmeticError, ValueError) as error:
            numeric_failure(
                "finite Attribute closure total, sum, residual and rounding budget", str(error)
            )
        valid = abs(residual) <= tolerance
        policy = "float64"
    else:
        assert isinstance(expected, int)
        assert all(isinstance(cell, int) for cell in cells)
        actual = sum(cells)
        residual = actual - expected
        tolerance = 0
        valid = residual == 0
        policy = "duration_ticks" if isinstance(dtype, DurationDataType) else "exact_integer"
    if not valid:
        raise AttributeInputError(
            reason="closure_mismatch",
            received="independent parent and fine results do not close within numeric tolerance",
            context=AttributeFailureContext(
                uses,
                parent,
                AttributeClosureWitness(
                    target.target,
                    target.kind,
                    expected,
                    actual,
                    residual,
                    tolerance,
                    policy,
                    total.column,
                ),
            ),
        )


def report_attribute(
    program: AttributeContributionProgram,
    rows: pd.DataFrame,
    payload: AttributeMethodPayload,
    parent: StageOutput,
    report_schema: DataSchema,
    *,
    paired_parent_methods: tuple[_Method, ...] = (),
    level_methods: tuple[
        FrameLevelAdditiveMethod
        | FrameLevelRatioMethod
        | FrameLevelLinearMethod
        | FrameLevelDivisionMethod,
        ...,
    ] = (),
) -> tuple[pd.DataFrame, AttributeMethodPayload]:
    """Bind original parent observations, fresh totals and all report paths by exact keys."""
    target = program.operation.target
    assert target is not None
    level = target.mode == "level"
    if target.mode == "relative" and any(
        method.right.value == 0 for method in paired_parent_methods
    ):
        reject(
            "a nonzero complete-parent baseline for a defined relative target",
            ".".join(target.path),
            "Select the absolute comparison output, or filter parents with a valid nonzero baseline before requesting attribution.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    old = dict(zip(program.output_schema.value_paths, program.output_schema.values, strict=True))
    output = dict(zip(report_schema.value_paths, report_schema.values, strict=True))
    parent_schema = program.input_program.parent_schema
    parent_columns = tuple(column for axis in parent_schema.axes for column in axis.columns)
    parent_keys = tuple(frozen_key_rows(parent.table, parent_columns))
    parent_indices = {key: index for index, key in enumerate(parent_keys)}
    fine_keys = tuple(frozen_key_rows(rows, parent_columns))
    fine_indices = tuple(parent_indices[key] for key in fine_keys)
    original = parent_schema.values[target.observed]
    observed_column = output[("observed_total", *target.path)]
    observed = _report_value(_Value(parent.table[original.slot_id], original), observed_column)
    result = rows.loc[:, list(report_schema.key_fields)].copy()
    mappings = [
        FrameMethodOutput("contribution", ("contribution", *target.path)),
        FrameMethodOutput("total", ("total", *target.path)),
        FrameMethodOutput("observed_total", ("observed_total", *target.path)),
        FrameMethodOutput("share", ("contribution_share", *target.path)),
    ]
    diagnostic = {
        ("detail", "value"): FrameMethodOutput("value", ("detail", "value", *target.path)),
        ("detail", "current"): FrameMethodOutput("current", ("detail", "current", *target.path)),
        ("detail", "baseline"): FrameMethodOutput("baseline", ("detail", "baseline", *target.path)),
        ("detail", "absolute"): FrameMethodOutput("absolute", ("detail", "absolute", *target.path)),
        ("component", "value", "numerator"): FrameMethodOutput(
            "numerator", ("component", "value", "numerator", *target.path)
        ),
        ("component", "value", "denominator"): FrameMethodOutput(
            "denominator", ("component", "value", "denominator", *target.path)
        ),
        ("component", "current", "numerator"): FrameMethodOutput(
            "numerator_current", ("component", "current", "numerator", *target.path)
        ),
        ("component", "current", "denominator"): FrameMethodOutput(
            "denominator_current", ("component", "current", "denominator", *target.path)
        ),
        ("component", "baseline", "numerator"): FrameMethodOutput(
            "numerator_baseline", ("component", "baseline", "numerator", *target.path)
        ),
        ("component", "baseline", "denominator"): FrameMethodOutput(
            "denominator_baseline", ("component", "baseline", "denominator", *target.path)
        ),
    }
    diagnostic.update(
        {
            ("component", "value", "valid_count"): FrameMethodOutput(
                "valid_count", ("component", "value", "valid_count", *target.path)
            ),
            ("component", "current", "valid_count"): FrameMethodOutput(
                "valid_count_current", ("component", "current", "valid_count", *target.path)
            ),
            ("component", "baseline", "valid_count"): FrameMethodOutput(
                "valid_count_baseline", ("component", "baseline", "valid_count", *target.path)
            ),
        }
    )
    for path in report_schema.value_paths[4:]:
        mappings.append(diagnostic[path[: -len(target.path)]])
    presence_mapping = {
        "value": FrameMethodOutput("present", ("detail", "value", "present", *target.path)),
        "current": FrameMethodOutput(
            "current_present", ("detail", "current", "present", *target.path)
        ),
        "baseline": FrameMethodOutput(
            "baseline_present", ("detail", "baseline", "present", *target.path)
        ),
    }
    for path in report_schema.presence_paths:
        mappings.append(presence_mapping[path[1]])
    descriptor = FrameMethodTarget(target.path, target.mode, tuple(mappings))
    methods: list[_Method | _LevelMethod] = []
    paired_methods: list[
        FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod | FrameDivisionMethod
    ] = []
    totals: list[FrameMethodValue] = []
    for index, _key in enumerate(parent_keys):
        method: _Method | _LevelMethod
        if level:
            method = level_methods[index]
            fresh = _series(method.value)
        else:
            paired = paired_parent_methods[index]
            method = paired
            paired_methods.append(paired)
            fresh = _series(paired.change)
            if target.mode == "relative":
                baseline = _series(paired.right)
                baseline = _Value(baseline.values.abs(), baseline.column)
                fresh = _numeric_binary(
                    "div", fresh, baseline, output=output[("total", *target.path)].dtype
                )
        methods.append(_report_method(method, output, target.path))
        totals.append(_scalar(_report_value(fresh, output[("total", *target.path)]), 0))
    contribution_column = source_columns(rows, (old[("contribution", "target")],))[0]
    contributions = _Value(rows[contribution_column.slot_id], contribution_column)
    if target.mode == "relative":
        baselines = [paired_methods[index].right.value for index in fine_indices]
        baseline_column = paired_methods[0].right.column if paired_methods else contribution_column
        array = pa.array(baselines, type=stage_arrow_type(baseline_column.dtype))
        denominator = _Value(pd.Series(pd.arrays.ArrowExtensionArray(array)).abs(), baseline_column)
        contributions = _numeric_binary(
            "div", contributions, denominator, output=output[("contribution", *target.path)].dtype
        )
    contributions = _report_value(contributions, output[("contribution", *target.path)])
    result[output[("contribution", *target.path)].slot_id] = contributions.values
    result[output[("total", *target.path)].slot_id] = pd.Series(
        [totals[index].value for index in fine_indices], dtype=contributions.values.dtype
    )
    observed_values = observed.values.take(list(fine_indices)).reset_index(drop=True)
    result[observed_column.slot_id] = observed_values
    share_column = output[("contribution_share", *target.path)]
    if level:
        total_series = result[output[("total", *target.path)].slot_id]
        shares = _numeric_binary(
            "div",
            contributions,
            _Value(total_series, output[("total", *target.path)]),
            output=share_column.dtype,
        )
        result[share_column.slot_id] = shares.values
    else:
        result[share_column.slot_id] = rows[old[("contribution_share", "target")].slot_id]
    for path in report_schema.value_paths[4:]:
        if path[0] == "detail":
            source_role = {
                "value": "left",
                "current": "left",
                "baseline": "right",
                "absolute": "absolute",
            }[path[1]]
        else:
            endpoint, component = path[1:3]
            source_role = (
                component + "_" + ("left" if endpoint in ("value", "current") else "right")
            )
        source_column = source_columns(rows, (old[(source_role, "target")],))[0]
        result[output[path].slot_id] = _report_value(
            _Value(rows[source_column.slot_id], source_column), output[path]
        ).values
    for column, source in zip(report_schema.presence, program.output_schema.presence, strict=False):
        result[column.slot_id] = rows[source.slot_id]
    parents = []
    uses = tuple(
        dict.fromkeys(
            source.use for item in program.input_program.matrix for source in item.sources
        )
    )
    for index, _key in enumerate(parent_keys):
        indices = [row for row, parent_index in enumerate(fine_indices) if parent_index == index]
        _closure(
            _Value(contributions.values.take(indices), output[("contribution", *target.path)]),
            totals[index],
            methods[index],
            relative=target.mode == "relative",
            exact_integer=program.exact_integer_closure,
            parent=AttributeParentFiber(_key, parent_columns),
            target=descriptor,
            uses=uses,
        )
        observed_value = _scalar(observed, index)
        metric = FrameAttributeMetric(
            target.path,
            target.mode,
            methods[index],
            replace(observed_value, column=observed_column),
            totals[index],
            tuple(mappings),
            "zero_total" if totals[index].value == 0 else "defined",
        )
        parents.append(
            FrameMethodParent(
                tuple(
                    FrameMethodValue(column, cell)
                    for column, (_, cell) in zip(parent_columns, _key, strict=True)
                ),
                (metric,),
            )
        )
    new_payload = AttributeMethodPayload(
        report_schema,
        payload.parent_axis_count,
        tuple(sorted(parents, key=_key_order)),
        payload.coordinates,
        (descriptor,),
        parent_schema=parent_schema,
    )
    return result.loc[:, [column.slot_id for column in report_schema.columns]].reset_index(
        drop=True
    ), new_payload
