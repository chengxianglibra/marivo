"""Pure logical lineage transport over the closed Frame operation family."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import assert_never

from marivo._definition.expressions import SourceAggregate, SourceWeightedMean
from marivo._definition.query import (
    CoordinateSlot,
    FilterValues,
    PairStatistic,
    PresenceSlot,
    ReduceValues,
    SelectValues,
    Statistic,
    TopValues,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValuePartition,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.core.frame_attribute_merge import AttributeMergeValues
from marivo.analysis.core.frame_expression import (
    AlignFrameValues,
    AttributeContributionValues,
    AttributeInputsValues,
    AttributeProjectValues,
    CompareFrameValues,
    FrameOperationKind,
)
from marivo.analysis.core.frame_fix import FixFrameValues
from marivo.analysis.core.lineage import (
    ColumnLineage,
    FrameLineage,
    SemanticObjectIdentity,
    all_origins,
    directory,
    merge,
    union,
)

if TYPE_CHECKING:
    from marivo.analysis.core.data_schema import DataSchema
    from marivo.analysis.core.frame_structure import PendingDataSchema
    from marivo.analysis.dsl.frame import AnalysisFrame


def derive(
    schema: DataSchema | PendingDataSchema,
    operation: FrameOperationKind,
    inputs: tuple[AnalysisFrame, ...],
) -> FrameLineage:
    first = inputs[0].lineage
    values = tuple(first.values.values())
    axes = tuple(first.axes.values())
    presence = tuple(first.presence.values())
    domain = first._domain

    def statistic(value: Statistic | PairStatistic) -> ColumnLineage:
        operands = (
            (value.left, value.right)
            if isinstance(value, PairStatistic)
            else (() if value.value is None else (value.value,))
        )
        return merge(*(expression(e) for e in operands), context=domain)

    def expression(value: ValueExpression) -> ColumnLineage:
        if isinstance(value, ValueSlot):
            return values[value.index]
        if isinstance(value, CoordinateSlot):
            return merge(axes[value.index], context=domain)
        if isinstance(value, PresenceSlot):
            return first.presence[".".join(value.path)]
        if isinstance(value, ValueLiteral):
            return ColumnLineage(context_origins=domain)
        if isinstance(value, ValueUnary):
            return expression(value.operand)
        if isinstance(value, ValueBinary):
            return merge(expression(value.left), expression(value.right))
        if isinstance(value, ValuePartition):
            return merge(
                statistic(value.statistic),
                context=union(*(all_origins(axes[p]) for p in value.positions)),
            )
        assert_never(value)

    if isinstance(operation, SelectValues):
        values = tuple(expression(e) for _, e in operation.values)
    elif isinstance(operation, FilterValues):
        domain = union(domain, all_origins(expression(operation.predicate)))
    elif isinstance(operation, TopValues):
        domain = union(
            domain, all_origins(expression(operation.score)), *(all_origins(a) for a in axes)
        )
    elif isinstance(operation, FixFrameValues):
        domain = union(domain, all_origins(axes[operation.position]))
        axes = tuple(a for i, a in enumerate(axes) if i != operation.position)
    elif isinstance(operation, ReduceValues):
        grouping = union(*(all_origins(axes[p]) for p in operation.positions))
        values = tuple(merge(statistic(s), context=grouping) for _, s in operation.values)
        axes = tuple(axes[p] for p in operation.positions)
        presence = ()
        domain = union(domain, grouping)
    elif isinstance(operation, AlignFrameValues):
        right = inputs[1].lineage
        right_axes = tuple(right.axes.values())
        axes = tuple(merge(a, right_axes[dict(operation.pairs)[i]]) for i, a in enumerate(axes))
        domain = union(domain, right._domain, *(all_origins(a) for a in axes))
        values = (*values, *right.values.values())
        presence = (
            ColumnLineage(context_origins=first._domain),
            ColumnLineage(context_origins=right._domain),
            *presence,
            *right.presence.values(),
        )
    elif isinstance(operation, CompareFrameValues):
        by_path = dict(first.values)
        present_by_path = {"current.present": ColumnLineage(context_origins=domain)}
        present_by_path.update(("current.presence." + p, c) for p, c in first.presence.items())
        for spec, frame in zip(operation.baselines, inputs[1:], strict=True):
            other = frame.lineage
            right_axes = tuple(other.axes.values())
            matched = tuple(merge(a, right_axes[dict(spec.pairs)[i]]) for i, a in enumerate(axes))
            axes = matched
            domain = union(domain, other._domain, *(all_origins(a) for a in matched))
            for path, current in first.values.items():
                baseline = other.values[path]
                for role, column in (
                    ("baseline", baseline),
                    ("absolute", merge(current, baseline)),
                    ("relative", merge(current, baseline)),
                ):
                    by_path[spec.alias + "." + role + "." + path] = column
            present_by_path[spec.alias + ".present"] = ColumnLineage(context_origins=other._domain)
            present_by_path.update(
                (spec.alias + ".presence." + p, c) for p, c in other.presence.items()
            )
        values = tuple(by_path[".".join(p)] for p in schema.value_paths)
        presence = tuple(present_by_path[".".join(p)] for p in schema.presence_paths)
    elif isinstance(operation, AttributeMergeValues):
        values = tuple(c for frame in inputs for c in frame.lineage.values.values())
        presence = tuple(c for frame in inputs for c in frame.lineage.presence.values())
        axes = tuple(
            merge(*(tuple(frame.lineage.axes.values())[i] for frame in inputs))
            for i in range(len(schema.axes))
        )
        domain = union(*(frame.lineage._domain for frame in inputs))
    elif isinstance(operation, AttributeProjectValues):
        positions = (
            *operation.ordinary,
            *((operation.calendar,) if operation.calendar is not None else ()),
        )
        axes = tuple(axes[p] for p in positions)
        if operation.presence is not None:
            domain = union(domain, all_origins(first.presence[".".join(operation.presence)]))
        # Projection carries keys only.
        values = ()
        presence = ()
    elif isinstance(operation, (AttributeContributionValues, AttributeInputsValues)):
        mapping = operation.mapping
        endpoints = tuple(frame.lineage for frame in inputs[1:])
        left = endpoints[0]
        right = endpoints[-1]
        left_axes, right_axes = tuple(left.axes.values()), tuple(right.axes.values())
        left_fine = tuple(a for i, a in enumerate(left_axes) if i not in mapping.left_parents)
        right_fine = tuple(a for i, a in enumerate(right_axes) if i not in mapping.right_parents)
        fine = tuple(merge(a, b) for a, b in zip(left_fine, right_fine, strict=True))
        axes = (*axes, *fine)
        domain = union(
            domain, *(endpoint._domain for endpoint in endpoints), *(all_origins(a) for a in axes)
        )
        indices = operation.source_indices or (0, 0)
        lvalue = tuple(left.values.values())[indices[0]]
        rvalue = tuple(right.values.values())[indices[1]]
        both = merge(lvalue, rvalue)
        if isinstance(operation, AttributeContributionValues) and operation.target is not None:
            target = operation.target
            observed = values[target.observed]

            def component(side: int, name: str) -> ColumnLineage:
                from marivo.analysis.binding.attribute_rules import (
                    AdditiveRule,
                    AdditiveSource,
                    AttributeRule,
                    DirectRatio,
                    NativeMean,
                    NativeWeightedMean,
                    RuleAdd,
                    RuleDivide,
                    RuleNegate,
                    RuleRescale,
                    RuleScale,
                    RuleSubtract,
                )
                from marivo.analysis.binding.lineage import definition_lineage
                from marivo.analysis.core.frame_data import (
                    ExpressionFrameBacking,
                    SourceFrameBinding,
                )

                frame = inputs[1 + min(side, len(endpoints) - 1)]
                backing = frame._backing
                assert isinstance(backing, ExpressionFrameBacking) and isinstance(
                    backing.root, SourceFrameBinding
                )
                source_binding = backing.root
                rule = operation.rules[side][0][1]

                def capture(rule: AttributeRule) -> ColumnLineage:
                    named = (
                        tuple(
                            SemanticObjectIdentity(ref.ref.kind.value, ref.ref.path, ref.version)
                            for ref in rule.origins
                        )
                        if isinstance(rule, AdditiveRule)
                        else ()
                    )
                    return merge(capture_operation(rule), ColumnLineage(union(named)))

                def capture_operation(rule: AttributeRule) -> ColumnLineage:
                    op = rule.operation
                    if isinstance(op, AdditiveSource):
                        return definition_lineage(source_binding, op.source.binding.definition)
                    if isinstance(op, NativeMean):
                        node = op.source.binding.definition
                        assert isinstance(node, SourceAggregate)
                        return (
                            definition_lineage(source_binding, node.operand)
                            if name == "numerator"
                            else ColumnLineage()
                        )
                    if isinstance(op, NativeWeightedMean):
                        node = op.source.binding.definition
                        assert isinstance(node, SourceWeightedMean)
                        if name == "valid_count":
                            return ColumnLineage()
                        weight = definition_lineage(source_binding, node.weight)
                        return (
                            merge(definition_lineage(source_binding, node.value), weight)
                            if name == "numerator"
                            else weight
                        )
                    if isinstance(op, DirectRatio):
                        return merge(capture(op.numerator), capture(op.denominator))
                    if isinstance(op, (RuleAdd, RuleSubtract)):
                        return merge(capture(op.left), capture(op.right))
                    assert isinstance(op, (RuleNegate, RuleScale, RuleDivide, RuleRescale))
                    return capture(op.operand)

                if isinstance(rule.operation, DirectRatio):
                    rule = (
                        rule.operation.numerator
                        if name == "numerator"
                        else rule.operation.denominator
                    )
                return merge(capture(rule), context=all_origins(lvalue if side == 0 else rvalue))

            def report(path: tuple[str, ...]) -> ColumnLineage:
                role = path[: -len(target.path)]
                if role == ("observed_total",):
                    return observed
                if len(role) == 3 and role[0] == "component":
                    return component(1 if role[1] == "baseline" else 0, role[2])
                if role in (
                    ("detail", "baseline"),
                    ("component", "baseline", "numerator"),
                    ("component", "baseline", "denominator"),
                    ("component", "baseline", "valid_count"),
                ):
                    return rvalue
                if role[:1] == ("component",) or role in (
                    ("detail", "value"),
                    ("detail", "current"),
                ):
                    return lvalue
                return both

            values = tuple(report(path) for path in schema.value_paths)
        else:

            def diagnostic(path: tuple[str, ...]) -> ColumnLineage:
                role, *source = path
                key = ".".join(source)
                a = left.values[key]
                b = right.values[key]
                if role == "left":
                    return a
                if role == "right":
                    return b
                return merge(a, b)

            values = tuple(diagnostic(path) for path in schema.value_paths)
        presence = tuple(
            ColumnLineage(context_origins=e._domain)
            for e in ((left,) if len(schema.presence) == 1 else (left, right))
        )
        presence = presence[: len(schema.presence)]
    else:
        assert_never(operation)
    return directory(schema, values, axes, presence, domain)
