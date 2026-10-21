"""Publish checked source contributions as a target report with complete inert facts."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, localcontext
from math import fsum, isfinite, ulp

import pandas as pd
import pyarrow as pa

from marivo.analysis.core.attribute import AttributeContributionProgram, AttributeMethodPayload
from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.data_types import DecimalDataType, DurationDataType
from marivo.analysis.core.data_validation import stage_arrow_type
from marivo.analysis.core.frame_method_facts import (
    FrameAdditiveMethod,
    FrameAttributeMetric,
    FrameLevelAdditiveMethod,
    FrameLevelLinearMethod,
    FrameLevelRatioMethod,
    FrameLinearMethod,
    FrameMethodMetric,
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
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.methods.frame_attribute_contribution import _numeric_binary, _Value


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


def _closure(values: _Value, total: FrameMethodValue) -> None:
    """Compare independently evaluated totals within the captured carrier's rounding."""
    array = pa.array(values.values, type=stage_arrow_type(values.column.dtype))
    if isinstance(values.column.dtype, DurationDataType):
        array = array.cast(pa.int64())
    cells = array.to_pylist()
    if any(cell is None for cell in cells):
        reject(
            "known complete fine contributions",
            total.column.name,
            "Inspect unknown source components before attribution.",
            "analysis_frame.attribute",
        )
    expected = total.value
    assert isinstance(expected, (int, float, Decimal))
    dtype = values.column.dtype
    if isinstance(dtype, DecimalDataType):
        with localcontext() as context:
            context.prec = 100
            actual = sum(cells, Decimal(0))
            bound = Decimal(len(cells) + 1) * Decimal(1).scaleb(-dtype.scale) / 2
            assert isinstance(expected, Decimal)
            valid = abs(actual - expected) <= bound
    elif isinstance(expected, float):
        floating = fsum(cells)
        magnitude = max(abs(expected), fsum(abs(value) for value in cells))
        tolerance = 16 * (len(cells) + 1) * ulp(magnitude)
        valid = isfinite(floating) and abs(floating - expected) <= tolerance
    else:
        valid = sum(cells) == expected
    if not valid:
        reject(
            "fine contributions closing to the independently evaluated parent total",
            total.column.name,
            "Inspect complete source coverage and the original parent/fine formula; residual repair is unsupported.",
            "analysis_frame.attribute",
        )


def report_attribute(
    program: AttributeContributionProgram,
    rows: pd.DataFrame,
    payload: AttributeMethodPayload,
    parent: StageOutput,
    report_schema: DataSchema,
    *,
    legacy_methods: tuple[_Method, ...] = (),
    level_methods: tuple[
        FrameLevelAdditiveMethod | FrameLevelRatioMethod | FrameLevelLinearMethod, ...
    ] = (),
) -> tuple[pd.DataFrame, AttributeMethodPayload]:
    """Bind original parent observations, fresh totals and all report paths by exact keys."""
    target = program.operation.target
    assert target is not None
    level = target.mode == "level"
    old = dict(zip(program.output_schema.value_paths, program.output_schema.values, strict=True))
    output = dict(zip(report_schema.value_paths, report_schema.values, strict=True))
    parent_schema = program.input_program.parent_schema
    parent_columns = tuple(column for axis in parent_schema.axes for column in axis.columns)
    parent_keys = tuple(frozen_key_rows(parent.table, parent_columns))
    parent_indices = {key: index for index, key in enumerate(parent_keys)}
    fine_keys = tuple(frozen_key_rows(rows, parent_columns))
    fine_indices = tuple(parent_indices[key] for key in fine_keys)
    original = parent_schema.values[target.observed]
    observed = _Value(parent.table[original.slot_id], original)
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
    metrics_by_key = {
        tuple(value.value for value in item.key): item.metrics[0] for item in payload.parents
    }
    methods: list[_Method | _LevelMethod] = []
    paired_methods: list[FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod] = []
    totals: list[FrameMethodValue] = []
    for index, key in enumerate(parent_keys):
        method: _Method | _LevelMethod
        if level:
            method = level_methods[index]
            fresh = _series(method.value)
        else:
            if legacy_methods:
                paired = legacy_methods[index]
            else:
                legacy = metrics_by_key[tuple(value for _, value in key)]
                assert isinstance(legacy, FrameMethodMetric)
                paired = legacy.method
            method = paired
            paired_methods.append(paired)
            fresh = _series(paired.change)
            if target.mode == "relative":
                baseline = _series(paired.right)
                baseline = _Value(baseline.values.abs(), baseline.column)
                fresh = _numeric_binary(
                    "div", fresh, baseline, output=output[("total", *target.path)].dtype
                )
        methods.append(method)
        totals.append(_scalar(_Value(fresh.values, output[("total", *target.path)]), 0))
    contribution_column = old[("left" if level else "contribution", "target")]
    contributions = _Value(rows[contribution_column.slot_id], contribution_column)
    if target.mode == "relative":
        baselines = [paired_methods[index].right.value for index in fine_indices]
        baseline_column = paired_methods[0].right.column if paired_methods else contribution_column
        array = pa.array(baselines, type=stage_arrow_type(baseline_column.dtype))
        denominator = _Value(pd.Series(pd.arrays.ArrowExtensionArray(array)).abs(), baseline_column)
        contributions = _numeric_binary(
            "div", contributions, denominator, output=output[("contribution", *target.path)].dtype
        )
    result[output[("contribution", *target.path)].slot_id] = contributions.values
    result[output[("total", *target.path)].slot_id] = pd.Series(
        [totals[index].value for index in fine_indices], dtype=contributions.values.dtype
    )
    observed_column = output[("observed_total", *target.path)]
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
        result[output[path].slot_id] = rows[old[(source_role, "target")].slot_id]
    for column, source in zip(report_schema.presence, program.output_schema.presence, strict=False):
        result[column.slot_id] = rows[source.slot_id]
    parents = []
    for index, key in enumerate(parent_keys):
        indices = [row for row, parent_index in enumerate(fine_indices) if parent_index == index]
        _closure(
            _Value(contributions.values.take(indices), output[("contribution", *target.path)]),
            totals[index],
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
                    for column, (_, cell) in zip(parent_columns, key, strict=True)
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
    )
    return result.loc[:, [column.slot_id for column in report_schema.columns]].reset_index(
        drop=True
    ), new_payload
