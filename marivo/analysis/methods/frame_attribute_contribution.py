"""Exact Attribute rule composition over independently checked role inputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import pandas as pd

from marivo.analysis.binding.attribute_rules import (
    AttributeRule,
    DirectRatio,
    NativeMean,
    NativeWeightedMean,
    RatioComponentRule,
    rule_effects,
)
from marivo.analysis.core.attribute import (
    AttributeContributionProgram,
    AttributeInputError,
    AttributeMethodPayload,
    _logical,
    contribution_schema,
)
from marivo.analysis.core.frame_level_facts import (
    FrameLevelAdditiveMethod,
    FrameLevelDivisionMethod,
    FrameLevelLinearMethod,
    FrameLevelRatioMethod,
)
from marivo.analysis.core.frame_method_facts import (
    FrameAdditiveMethod,
    FrameDivisionMethod,
    FrameLinearMethod,
    FrameMethodCoordinate,
    FrameRatioMethod,
)
from marivo.analysis.core.frozen_calendar import frozen_key_rows
from marivo.analysis.methods.attribute_contribution_effects import effects
from marivo.analysis.methods.attribute_contribution_evaluation import ContributionEvaluation
from marivo.analysis.methods.attribute_contribution_facts import level_method, method
from marivo.analysis.methods.attribute_contribution_inputs import prepare_contribution_inputs
from marivo.analysis.methods.attribute_numeric import (
    _Value,
)
from marivo.analysis.methods.carriers.stages import StageOutput, source_columns
from marivo.analysis.methods.frame_attribute_report import report_attribute
from marivo.analysis.model.data_types import (
    DataColumn,
)
from marivo.analysis.model.frozen_keys import FrozenKey
from marivo.analysis.model.method_values import FrameMethodValue


def _original_endpoints(
    evaluation: ContributionEvaluation,
    name: str,
    left_rule: AttributeRule,
    right_rule: AttributeRule,
    wanted: tuple[FrozenKey, ...],
    level: bool,
) -> tuple[tuple[_Value, _Value], tuple[_Value, _Value]]:
    program, captured = evaluation.program, evaluation.inputs
    metric_index = evaluation.metric_index
    parent_columns, parent_rows = captured.parent_columns, captured.parent_rows
    full_columns = (*parent_columns, *captured.fine_columns)
    endpoint_values: list[_Value] = []
    total_values: list[_Value] = []
    for side, rule in (
        (("left", left_rule),) if level else (("left", left_rule), ("right", right_rule))
    ):
        column = (
            program.input_program.left_schema
            if side == "left"
            else program.input_program.right_schema
        ).values[metric_index]
        for scope, keys, destination in (
            ("fine", full_columns, endpoint_values),
            ("parent", parent_columns, total_values),
        ):
            frame = captured.originals[(side, scope)]
            by_key = dict(zip(frozen_key_rows(frame, keys), range(len(frame)), strict=True))
            target_keys = wanted if scope == "fine" else parent_rows
            source_index = (
                program.operation.source_indices[0 if side == "left" else 1]
                if program.operation.source_indices is not None
                else metric_index
            )
            series = pd.Series(
                [
                    frame[f"value.{source_index}"].iloc[by_key[key]] if key in by_key else None
                    for key in target_keys
                ],
                dtype=object,
            )
            if scope == "parent" and series.isna().any():
                index = int(series.isna().to_numpy().nonzero()[0][0])
                raise AttributeInputError(
                    reason="undefined_input",
                    received=f"{name}: undefined required original parent total",
                    context=evaluation.context(name, rule, side, scope, index, "original"),
                )
            actual = source_columns(
                frame,
                (replace(column, slot_id=f"value.{source_index}"),),
            )[0]
            destination.append(_Value(series, replace(column, dtype=actual.dtype)))
    if level:
        endpoint_values.append(endpoint_values[0])
        total_values.append(total_values[0])
    return (endpoint_values[0], endpoint_values[1]), (total_values[0], total_values[1])


def _emit_target_effects(
    evaluation: ContributionEvaluation,
    name: str,
    left_rule: AttributeRule,
    right_rule: AttributeRule,
    level: bool,
    directory: Mapping[tuple[str, ...], DataColumn],
    contribution: _Value,
    emitted: dict[str, _Value],
) -> None:
    if not level and rule_effects(left_rule):
        calculated = effects(evaluation, left_rule, right_rule, (evaluation.metric_index,))
        combined_effect: _Value | None = None
        for role in rule_effects(left_rule):
            value = calculated[role]
            zero = _Value(pd.Series([0] * len(value.values)), value.column)
            value = evaluation.arithmetic(
                "add",
                value,
                zero,
                left_rule,
                "both",
                "fine",
                output=directory[("effect_" + role, name)].dtype,
            )
            emitted["effect_" + role] = value
            combined_effect = (
                value
                if combined_effect is None
                else evaluation.arithmetic("add", combined_effect, value, left_rule, "both", "fine")
            )
        assert combined_effect is not None
        evaluation.assert_same(
            combined_effect, contribution, left_rule, "both", "fine", rounded=True
        )


def _target_values(
    evaluation: ContributionEvaluation,
    name: str,
    left_rule: AttributeRule,
    right_rule: AttributeRule,
    endpoint_values: tuple[_Value, _Value],
    total_values: tuple[_Value, _Value],
    level: bool,
    directory: Mapping[tuple[str, ...], DataColumn],
) -> dict[str, _Value]:
    program = evaluation.program
    values = evaluation.inputs.values
    # Evaluate parent masses even when there are no fine rows. This retains
    # every required denominator/child check and complete parent facts.
    total_change = (
        total_values[0]
        if level
        else evaluation.arithmetic(
            "sub",
            total_values[0],
            total_values[1],
            left_rule,
            "both",
            "parent",
        )
    )
    contribution = (
        evaluation.evaluate(left_rule, "left", "fine")
        if level
        else evaluation.arithmetic(
            "sub",
            evaluation.evaluate(left_rule, "left", "fine"),
            evaluation.evaluate(right_rule, "right", "fine"),
            left_rule,
            "both",
            "fine",
            output=program.output_schema.values[
                program.output_schema.value_paths.index(("contribution", name))
            ].dtype,
        )
    )
    if contribution.values.isna().any():
        index = int(contribution.values.isna().to_numpy().nonzero()[0][0])
        raise AttributeInputError(
            reason="undefined_input",
            received=f"{name}: undefined required contribution",
            context=evaluation.context(name, left_rule, "left", "fine", index, "contribution"),
        )
    change = (
        endpoint_values[0]
        if level
        else evaluation.arithmetic(
            "sub",
            endpoint_values[0],
            endpoint_values[1],
            left_rule,
            "both",
            "fine",
            output=directory[("absolute", name)].dtype,
        )
    )
    share = evaluation.arithmetic(
        "div",
        contribution,
        evaluation.broadcast(total_change),
        left_rule,
        "both",
        "fine",
        output=directory[("contribution_share", name)].dtype,
    )
    emitted = {
        "left": endpoint_values[0],
        "right": endpoint_values[1],
        "absolute": change,
        "contribution": contribution,
        "contribution_share": share,
    }
    _emit_target_effects(
        evaluation, name, left_rule, right_rule, level, directory, contribution, emitted
    )
    if isinstance(left_rule, RatioComponentRule):
        for side, rule in (
            (("left", left_rule),) if level else (("left", left_rule), ("right", right_rule))
        ):
            assert isinstance(rule, RatioComponentRule)
            operation = rule.operation
            if isinstance(operation, DirectRatio):
                n, d = (
                    evaluation.evaluate(operation.numerator, side, "fine"),
                    evaluation.evaluate(operation.denominator, side, "fine"),
                )
            else:
                roles = (
                    ("valid_value_sum", "valid_value_count")
                    if isinstance(operation, NativeMean)
                    else ("pair_product_sum", "pair_weight_sum")
                )
                n, d = (values[(side, "fine", _logical(operation.source), role)] for role in roles)
            emitted["numerator_" + side], emitted["denominator_" + side] = n, d
            if isinstance(operation, NativeWeightedMean):
                emitted["valid_count_" + side] = values[
                    (side, "fine", _logical(operation.source), "pair_count")
                ]
    return emitted


def _parent_method(
    evaluation: ContributionEvaluation,
    left_rule: AttributeRule,
    right_rule: AttributeRule,
    name: str,
    index: int,
    total_values: tuple[_Value, _Value],
    directory: Mapping[tuple[str, ...], DataColumn],
) -> FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod | FrameDivisionMethod:
    root_method = method(evaluation, left_rule, right_rule, index, total_values)
    root_method = replace(
        root_method,
        left=FrameMethodValue(
            replace(directory[("left", name)], dtype=root_method.left.column.dtype),
            root_method.left.value,
        ),
        right=FrameMethodValue(
            replace(directory[("right", name)], dtype=root_method.right.column.dtype),
            root_method.right.value,
        ),
        change=FrameMethodValue(
            replace(directory[("absolute", name)], dtype=root_method.change.column.dtype),
            root_method.change.value,
        ),
    )
    if isinstance(root_method, FrameRatioMethod):
        root_method = replace(
            root_method,
            numerator_left=FrameMethodValue(
                replace(
                    directory[("numerator_left", name)],
                    dtype=root_method.numerator_left.column.dtype,
                ),
                root_method.numerator_left.value,
            ),
            numerator_right=FrameMethodValue(
                replace(
                    directory[("numerator_right", name)],
                    dtype=root_method.numerator_right.column.dtype,
                ),
                root_method.numerator_right.value,
            ),
            denominator_left=FrameMethodValue(
                replace(
                    directory[("denominator_left", name)],
                    dtype=root_method.denominator_left.column.dtype,
                ),
                root_method.denominator_left.value,
            ),
            denominator_right=FrameMethodValue(
                replace(
                    directory[("denominator_right", name)],
                    dtype=root_method.denominator_right.column.dtype,
                ),
                root_method.denominator_right.value,
            ),
        )
    return root_method


def attribute_contribution_data(
    program: AttributeContributionProgram, inputs: tuple[StageOutput, ...]
) -> tuple[pd.DataFrame, AttributeMethodPayload]:
    """Consume all originals, required parent facts and checks before returning."""
    report_operation = program.operation
    assert report_operation.target is not None
    report_output = program.output_schema
    level = report_operation.target.mode == "level"
    program = replace(
        program,
        output_schema=contribution_schema(
            program.input_program, replace(report_operation, target=None)
        ),
    )
    prepared, parent, _, _ = inputs
    captured = prepare_contribution_inputs(program, inputs, level=level)
    parent_rows = captured.parent_rows
    level_methods: list[
        FrameLevelAdditiveMethod
        | FrameLevelRatioMethod
        | FrameLevelLinearMethod
        | FrameLevelDivisionMethod
    ] = []

    rows = prepared.table.loc[:, list(program.output_schema.key_fields)].copy()
    wanted = frozen_key_rows(rows, (*captured.parent_columns, *captured.fine_columns))
    paired_parent_methods: list[
        FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod | FrameDivisionMethod
    ] = []
    for metric_index, ((name, left_rule), (_, right_rule)) in enumerate(
        zip(*program.operation.rules, strict=True)
    ):
        evaluation = ContributionEvaluation(
            program, prepared, captured, metric_index, ".".join(report_operation.target.path)
        )
        # Original error-policy formulas have already run above. A nullable
        # original parent caused by D=0 now gets the contribution-specific reason.
        evaluation.evaluate(left_rule, "left", "parent")
        if not level:
            evaluation.evaluate(right_rule, "right", "parent")
        endpoint_values, total_values = _original_endpoints(
            evaluation, name, left_rule, right_rule, wanted, level
        )
        directory = dict(
            zip(program.output_schema.value_paths, program.output_schema.values, strict=True)
        )
        emitted = _target_values(
            evaluation, name, left_rule, right_rule, endpoint_values, total_values, level, directory
        )
        for prefix, value in emitted.items():
            rows[
                program.output_schema.values[
                    program.output_schema.value_paths.index((prefix, name))
                ].slot_id
            ] = value.values
        for index in range(len(parent_rows)):
            if level:
                level_methods.append(level_method(evaluation, left_rule, index, total_values[0]))
                continue
            paired_parent_methods.append(
                _parent_method(
                    evaluation, left_rule, right_rule, name, index, total_values, directory
                )
            )
    for column in program.output_schema.presence:
        rows[column.slot_id] = prepared.table[column.slot_id]
    # Filling value columns preserves the prepared canonical key-domain order.
    coordinates = tuple(
        FrameMethodCoordinate(
            a.name,
            a.schema,
            tuple(tuple(v for _, v in key) for key in frozen_key_rows(a.rows, a.schema.columns)),
        )
        for a in sorted(parent.frame_attachments, key=lambda a: a.name)
    )
    payload = AttributeMethodPayload(
        program.output_schema,
        len(program.input_program.parent_schema.axes),
        (),
        coordinates,
        parent_schema=program.input_program.parent_schema,
    )
    return report_attribute(
        replace(program, operation=report_operation),
        rows,
        payload,
        parent,
        report_output,
        level_methods=tuple(level_methods),
        paired_parent_methods=tuple(paired_parent_methods),
    )
