"""Evaluate admitted Attribute components and numeric closure."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from math import fsum
from typing import Literal

import pandas as pd

from marivo.analysis.binding.attribute_rules import (
    AdditiveSource,
    AttributeRule,
    DirectRatio,
    NativeMean,
    NativeWeightedMean,
    RatioComponentRule,
    RuleAdd,
    RuleDivide,
    RuleNegate,
    RuleRescale,
    RuleScale,
    RuleSubtract,
    component_requests,
)
from marivo.analysis.core.attribute import (
    AttributeContributionProgram,
    AttributeInputError,
    _coefficient_column,
    _logical,
    _rule_column,
    _type,
)
from marivo.analysis.core.frozen_calendar import frozen_key_rows
from marivo.analysis.methods.attribute_contribution_inputs import (
    ContributionInputs,
)
from marivo.analysis.methods.attribute_numeric import (
    _consistent,
    _numeric_binary,
    _Value,
    capture_method_value,
)
from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.methods.numeric_state import (
    finish_float64,
)
from marivo.analysis.model.attribute_failures import (
    AttributeFailureContext,
    AttributeFineFiber,
    AttributeNumericWitness,
    AttributeParentFiber,
)
from marivo.analysis.model.data_types import (
    DataType,
    ScalarDataType,
)
from marivo.analysis.model.errors import CoreRuleError
from marivo.analysis.model.method_values import FrameMethodValue
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.spi import numeric_failure


def _binary(
    operation: Literal["add", "sub", "mul", "div"],
    a: _Value,
    b: _Value,
    *,
    output: DataType | None = None,
    contexts: tuple[AttributeFailureContext, ...] = (),
) -> _Value:
    try:
        return _numeric_binary(operation, a, b, output=output)
    except SemanticExpressionError as error:
        # Existing numeric owners validate vectors. On failure, their same
        # scalar check identifies the exact typed diagnostic fiber, without
        # changing a result, source read or arithmetic policy.
        for index, captured in enumerate(contexts):
            try:
                _numeric_binary(
                    operation,
                    _Value(
                        a.values.take([index]).reset_index(drop=True),
                        a.column,
                        None
                        if a.roundoff is None
                        else a.roundoff.take([index]).reset_index(drop=True),
                    ),
                    _Value(
                        b.values.take([index]).reset_index(drop=True),
                        b.column,
                        None
                        if b.roundoff is None
                        else b.roundoff.take([index]).reset_index(drop=True),
                    ),
                    output=output,
                )
            except SemanticExpressionError:
                error.details["attribute_context"] = captured
                break
        raise


@dataclass(frozen=True, slots=True)
class ContributionEvaluation:
    """Evaluate one target against its admitted prepared parent and fine values."""

    program: AttributeContributionProgram
    prepared: StageOutput
    inputs: ContributionInputs
    metric_index: int
    current_name: str

    def broadcast(self, value: _Value) -> _Value:
        return _Value(
            value.values.take(self.inputs.fine_to_parent).reset_index(drop=True),
            value.column,
            None
            if value.roundoff is None
            else value.roundoff.take(self.inputs.fine_to_parent).reset_index(drop=True),
        )

    def context(
        self,
        name: str,
        rule: AttributeRule,
        side: str,
        scope: str,
        index: int,
        operation: Literal["original", "add", "sub", "mul", "div", "denominator", "contribution"],
        observed: tuple[FrameMethodValue, ...] = (),
    ) -> AttributeFailureContext:
        occurrences = {_logical(request) for request in component_requests(rule)}
        uses = tuple(
            leaf.use
            for root in self.prepared.attribute_inputs
            for leaf in root.layout.leaves
            if (
                side == "both"
                or any(
                    item.side == side and any(source.use == leaf.use for source in item.sources)
                    for item in self.program.input_program.matrix
                )
            )
            and leaf.use.logical_occurrence in occurrences
        )
        if scope == "parent":
            fiber: AttributeParentFiber | AttributeFineFiber = AttributeParentFiber(
                self.inputs.parent_rows[index], self.inputs.parent_columns
            )
        else:
            fiber = AttributeFineFiber(
                self.inputs.fine_parents[index],
                self.inputs.parent_columns,
                frozen_key_rows(self.prepared.table, self.inputs.fine_columns)[index],
                self.inputs.fine_columns,
            )
        return AttributeFailureContext(
            uses,
            fiber,
            AttributeNumericWitness(
                uses[0].logical_occurrence if uses else (), operation, observed
            ),
        )

    def arithmetic(
        self,
        operation: Literal["add", "sub", "mul", "div"],
        a: _Value,
        b: _Value,
        rule: AttributeRule,
        side: str,
        scope: str,
        *,
        output: DataType | None = None,
    ) -> _Value:

        captured_contexts: list[AttributeFailureContext] = []
        for index in range(len(a.values)):
            try:
                observed = (capture_method_value(a, index), capture_method_value(b, index))
            except CoreRuleError as error:
                assert error.expected is not None and error.received is not None
                try:
                    numeric_failure(error.expected, error.received)
                except SemanticExpressionError as failure:
                    failure.details["attribute_context"] = self.context(
                        self.current_name, rule, side, scope, index, operation
                    )
                    raise
            captured_contexts.append(
                self.context(self.current_name, rule, side, scope, index, operation, observed)
            )
        contexts = tuple(captured_contexts)
        return _binary(operation, a, b, output=output, contexts=contexts)

    def native_value(
        self, rule: AttributeRule, side: str, scope: str, path: tuple[int, ...]
    ) -> _Value | None:
        if self.program.operation.source_indices is not None:
            path = (self.program.operation.source_indices[0 if side == "left" else 1], *path[1:])
        native = self.inputs.values.get((side, scope, path, "native_value"))
        if native is None:
            return None
        column = _rule_column(self.program.input_program, rule, side)
        return _Value(native.values, replace(column, dtype=native.column.dtype))

    def evaluate(
        self,
        rule: AttributeRule,
        side: str,
        scope: str,
        *,
        original: bool = False,
        path: tuple[int, ...] | None = None,
    ) -> _Value:
        if path is None:
            path = (self.metric_index,)
        native = self.native_value(rule, side, scope, path)
        if original and native is not None:
            return native
        output = None if native is None else native.column.dtype
        operation = rule.operation
        if isinstance(operation, AdditiveSource):
            role = "value" if original else "additive_value"
            if original and operation.source.window is not None:
                # Original expanded leaves are already composed by FormulaProgram;
                # additive child facts use their mathematically identical mass.
                role = "additive_value"
            return self.inputs.values[(side, scope, _logical(operation.source), role)]
        if isinstance(rule, RatioComponentRule):
            return self._evaluate_ratio(rule, side, scope, original, path, output)
        if isinstance(operation, (RuleAdd, RuleSubtract)):
            return self.arithmetic(
                "add" if isinstance(operation, RuleAdd) else "sub",
                self.evaluate(operation.left, side, scope, original=original, path=(*path, 0)),
                self.evaluate(operation.right, side, scope, original=original, path=(*path, 1)),
                rule,
                side,
                scope,
                output=output,
            )
        if isinstance(operation, (RuleDivide, RuleRescale)):
            value = self.evaluate(
                operation.operand, side, scope, original=original, path=(*path, 0)
            )
            divisor = _Value(
                pd.Series([operation.scalar.value] * len(value.values), dtype=object),
                _coefficient_column(operation.scalar.value),
            )
            if (output or _type("div", value.column, divisor.column)) == ScalarDataType("float64"):
                # Native Metric division rounds operands into Float64 before
                # dividing, including exact integer/Decimal additive masses.
                value, divisor = (
                    _Value(
                        finish_float64(item.values, "Attribute division operand"),
                        replace(item.column, dtype=ScalarDataType("float64")),
                    )
                    for item in (value, divisor)
                )
            divided = self.arithmetic(
                "div",
                value,
                divisor,
                rule,
                side,
                scope,
                output=output,
            )
            return _Value(
                divided.values,
                replace(
                    divided.column,
                    unit=operation.unit
                    if isinstance(operation, RuleRescale)
                    else value.column.unit,
                ),
            )
        assert isinstance(operation, (RuleNegate, RuleScale))
        value = self.evaluate(operation.operand, side, scope, original=original, path=(*path, 0))
        scalar = -1 if isinstance(operation, RuleNegate) else operation.scalar.value
        return self.arithmetic(
            "mul",
            value,
            _Value(
                pd.Series([scalar] * len(value.values), dtype=object),
                _coefficient_column(scalar),
            ),
            rule,
            side,
            scope,
            output=output,
        )

    def _evaluate_ratio(
        self,
        rule: RatioComponentRule,
        side: str,
        scope: str,
        original: bool,
        path: tuple[int, ...],
        output: DataType | None,
    ) -> _Value:
        operation = rule.operation
        if isinstance(operation, (NativeMean, NativeWeightedMean)) and original:
            return self.inputs.values[(side, scope, _logical(operation.source), "value")]
        if isinstance(operation, DirectRatio):
            numerator = self.evaluate(
                operation.numerator, side, scope, original=original, path=(*path, 0)
            )
            denominator = self.evaluate(
                operation.denominator,
                side,
                scope if original else "parent",
                original=original,
                path=(*path, 1),
            )
        else:
            assert isinstance(operation, (NativeMean, NativeWeightedMean))
            roles = (
                ("valid_value_sum", "valid_value_count")
                if isinstance(operation, NativeMean)
                else ("pair_product_sum", "pair_weight_sum")
            )
            numerator = self.inputs.values[(side, scope, _logical(operation.source), roles[0])]
            denominator = self.inputs.values[(side, "parent", _logical(operation.source), roles[1])]
        if not original:
            invalid = denominator.values.isna() | (denominator.values == 0).fillna(False)
            if invalid.any():
                index = int(invalid.to_numpy().nonzero()[0][0])
                raise AttributeInputError(
                    reason="root_denominator_zero"
                    if pd.notna(denominator.values.iloc[index])
                    and denominator.values.iloc[index] == 0
                    else "undefined_input",
                    received="a required complete-parent denominator is zero or undefined",
                    context=self.context(
                        self.current_name,
                        rule,
                        side,
                        "parent",
                        index,
                        "denominator",
                        (capture_method_value(denominator, index),),
                    ),
                )
            if scope == "fine":
                denominator = self.broadcast(denominator)
        return self.arithmetic(
            "div",
            numerator,
            denominator,
            rule,
            side,
            scope,
            output=output or _rule_column(self.program.input_program, rule, side).dtype,
        )

    def components(
        self, rule: RatioComponentRule, side: str, scope: str, path: tuple[int, ...]
    ) -> tuple[_Value, _Value]:
        operation = rule.operation
        if isinstance(operation, DirectRatio):
            return self.evaluate(operation.numerator, side, scope, path=(*path, 0)), self.evaluate(
                operation.denominator, side, scope, path=(*path, 1)
            )
        roles = (
            ("valid_value_sum", "valid_value_count")
            if isinstance(operation, NativeMean)
            else ("pair_product_sum", "pair_weight_sum")
        )
        return (
            self.inputs.values[(side, scope, _logical(operation.source), roles[0])],
            self.inputs.values[(side, scope, _logical(operation.source), roles[1])],
        )

    def assert_same(
        self,
        actual: _Value,
        expected: _Value,
        rule: AttributeRule,
        side: str,
        scope: str,
        *,
        grouped: bool = False,
        rounded: bool = False,
    ) -> None:
        for index in range(len(expected.values)):
            indices = (
                [
                    i
                    for i, parent_index in enumerate(self.inputs.fine_to_parent)
                    if parent_index == index
                ]
                if grouped
                else [index]
            )
            captured = tuple(capture_method_value(actual, i) for i in indices)
            wanted_value = capture_method_value(expected, index)
            numeric = tuple(item.value for item in captured)
            wanted = wanted_value.value
            if not isinstance(wanted, (int, float, Decimal)) or any(
                not isinstance(item, (int, float, Decimal)) for item in numeric
            ):
                valid = False
            else:
                checked: list[int | float | Decimal] = []
                for item in numeric:
                    assert isinstance(item, (int, float, Decimal))
                    checked.append(item)

                try:
                    propagated = (
                        0.0
                        if actual.roundoff is None
                        else fsum(float(actual.roundoff.iloc[i]) for i in indices)
                    )
                    if expected.roundoff is not None:
                        propagated += float(expected.roundoff.iloc[index])
                    valid = _consistent(
                        checked, wanted, expected.column, rounded=rounded, roundoff=propagated
                    )
                except SemanticExpressionError as failure:
                    failure.details["attribute_context"] = self.context(
                        self.current_name,
                        rule,
                        side,
                        scope,
                        index,
                        "original",
                        (*captured[:4], wanted_value),
                    )
                    raise
            if not valid:
                raise AttributeInputError(
                    reason="component_mismatch",
                    received=f"{self.current_name}: {side} {scope} components or original N/D disagree under attribute_components/v1",
                    context=self.context(
                        self.current_name,
                        rule,
                        side,
                        scope,
                        index,
                        "original",
                        (*captured[:4], wanted_value),
                    ),
                )

    def rate_endpoint(
        self, current: RatioComponentRule, side: Literal["left", "right"], path: tuple[int, ...]
    ) -> tuple[_Value, _Value, _Value]:
        for request in component_requests(current):
            occurrence = _logical(request)
            for role in (*request.roles, "input_count", "effective_count"):
                self.assert_same(
                    self.inputs.values[(side, "fine", occurrence, role)],
                    self.inputs.values[(side, "parent", occurrence, role)],
                    current,
                    side,
                    "parent",
                    grouped=True,
                )
        n, d = self.components(current, side, "fine", path)
        parent_n, parent_d = self.components(current, side, "parent", path)
        invalid_parent = parent_d.values.isna() | (parent_d.values <= 0).fillna(False)
        if invalid_parent.any():
            index = int(invalid_parent.to_numpy().nonzero()[0][0])
            raise AttributeInputError(
                reason="invalid_rate_support",
                received=f"{self.current_name}: {side} parent denominator must be positive",
                context=self.context(
                    self.current_name,
                    current,
                    side,
                    "parent",
                    index,
                    "denominator",
                    (capture_method_value(parent_d, index),),
                ),
            )
        invalid = (
            d.values.isna()
            | n.values.isna()
            | (d.values < 0).fillna(False)
            | ((d.values == 0) & (n.values != 0)).fillna(False)
        )
        operation = current.operation
        if isinstance(operation, NativeWeightedMean):
            negative = self.inputs.values[
                (side, "fine", _logical(operation.source), "pair_negative_count")
            ]
            invalid = invalid | (negative.values > 0).fillna(False)
        if invalid.any():
            index = int(invalid.to_numpy().nonzero()[0][0])
            raise AttributeInputError(
                reason="invalid_rate_support",
                received=f"{self.current_name}: {side} requires nonnegative denominators and effective weights, and zero numerator mass at zero support",
                context=self.context(
                    self.current_name,
                    current,
                    side,
                    "fine",
                    index,
                    "denominator",
                    (capture_method_value(n, index), capture_method_value(d, index)),
                ),
            )
        self.assert_same(n, parent_n, current, side, "parent", grouped=True)
        self.assert_same(d, parent_d, current, side, "parent", grouped=True)
        endpoint_dtype = _rule_column(self.program.input_program, current, side).dtype
        quotient = self.arithmetic("div", n, d, current, side, "fine", output=endpoint_dtype)
        original = self.evaluate(current, side, "fine", original=True, path=path)
        positive = [i for i, cell in enumerate(d.values) if pd.notna(cell) and cell > 0]
        # Keep the original nullable diagnostics intact. Only supported groups
        # participate in this identity check; zero support has no group rate.
        for index in positive:
            actual_value, original_value = (
                capture_method_value(quotient, index),
                capture_method_value(original, index),
            )
            actual_scalar, original_scalar = actual_value.value, original_value.value
            if (
                not isinstance(actual_scalar, (int, float, Decimal))
                or not isinstance(original_scalar, (int, float, Decimal))
                or not _consistent([actual_scalar], original_scalar, original.column, rounded=True)
            ):
                raise AttributeInputError(
                    reason="component_mismatch",
                    received=f"{self.current_name}: {side} positive-support original group Metric is undefined or differs from N/D",
                    context=self.context(
                        self.current_name,
                        current,
                        side,
                        "fine",
                        index,
                        "original",
                        (actual_value, original_value),
                    ),
                )
        parent_quotient = self.arithmetic(
            "div", parent_n, parent_d, current, side, "parent", output=endpoint_dtype
        )
        self.assert_same(
            parent_quotient,
            self.evaluate(current, side, "parent", original=True, path=path),
            current,
            side,
            "parent",
            rounded=True,
        )
        weight = self.arithmetic("div", d, self.broadcast(parent_d), current, side, "fine")
        return quotient, weight, d
