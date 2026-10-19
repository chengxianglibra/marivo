"""Qualified source-native recomputation of selected original contribution fibers.

This pass changes transport only after the complete original input, its parent
finishes, and every selection have a qualified source owner. Grouped sample and
calendar leaves use the same pass through its finite spatial-rollup helper;
other shapes retain the ordinary component transport.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd

from marivo._definition.expressions import (
    RowField,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.graph import walk
from marivo._definition.query import (
    DrillValues,
    FilterValues,
    RollupValues,
    SelectValues,
    ShareValues,
    TopValues,
    ValueSlot,
)
from marivo._definition.types import field_read_key
from marivo.analysis.compiler.graph_lowering import LoweredQuery, LoweredRead
from marivo.analysis.compiler.graph_plan import LocalCompute, SourceRead
from marivo.analysis.compiler.native_formula import (
    NativeMeanState,
    NativeOriginalBatchFinish,
    NativeOriginalFinish,
    NativeOriginalFinisher,
    NativeOriginalPhaseFinish,
    NativeOriginalState,
    NativeQuantileState,
    NativeShareFinish,
    align_native_inputs,
    lower_native_formula,
)
from marivo.analysis.compiler.observation import (
    FormulaProgram,
    lower_observation,
    temporal_bound,
)
from marivo.analysis.compiler.optimization import RuleDecision, required_for, required_slots
from marivo.analysis.compiler.original import share_additive
from marivo.analysis.compiler.queries import expression_value
from marivo.analysis.compiler.source_predicates import string_bytes
from marivo.analysis.core.data_types import DataColumn
from marivo.semantic._numeric_lowering import (
    NumericCheck,
    NumericValue,
    lower_row,
    numeric_binary,
)

if TYPE_CHECKING:
    from marivo.analysis.materialization.graph_source_execution import PreparedSources
    from marivo.datasource.adapters import SourceSession


def _same_key(left: ir.Value, right: ir.Value, provider: str) -> ir.BooleanValue:
    if provider != "duckdb" and left.type().is_string() and right.type().is_string():
        left, right = string_bytes(left, provider), string_bytes(right, provider)
    return left.identical_to(right)


def _restrict_keys(
    raw: ir.Table,
    selected: ir.Table,
    columns: tuple[tuple[str, str], ...],
    provider: str,
) -> ir.Table:
    """Apply complete-key membership without multiplying contribution rows."""
    raw = raw.view()
    domain = (
        (
            selected.select(*(key for _, key in columns))
            if columns
            else selected.select(__original_membership=ibis.literal(True))
        )
        .distinct()
        .view()
    )
    joined = raw.inner_join(
        domain,
        [_same_key(raw[column], domain[key], provider) for column, key in columns]
        if columns
        else ibis.literal(True),
    )
    return joined.select(*(raw[name] for name in raw.columns))


@dataclass(frozen=True, slots=True, eq=False)
class _Fiber:
    raw: ir.Table
    axes: tuple[tuple[str, str], ...]
    reads: tuple[SourceRead, ...]
    checks: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True, slots=True, eq=False)
class _Relation:
    expression: ir.Table
    raw: ir.Table
    axes: tuple[tuple[str, str], ...]
    formula: FormulaProgram
    reads: tuple[SourceRead, ...]
    owner: SourceSession
    fibers: tuple[_Fiber, ...]
    recomputed: bool = False


class _NativeOriginal:
    def __init__(self, prepared: PreparedSources, lowered: LoweredQuery) -> None:
        self.prepared = prepared
        self.lowered = lowered
        self.stages = {stage.stage_id: stage for stage in lowered.plan.stages}
        for stage in lowered.plan.stages:
            if isinstance(stage, LocalCompute) and stage.fused_steps:
                self.stages.update((step.stage_id, step) for step in stage.fused_steps)
        self.summaries: list[ir.Table] = []
        self.checks: list[tuple[str, NumericCheck]] = []
        self.members: set[str] = set()
        self.operations: list[str] = []
        self.finish: NativeOriginalFinisher | None = None
        self.overrides: dict[str, _Relation] = {}
        self.allowed_parameters: frozenset[ops.Node] = frozenset()

    def summary(self, table: ir.Table, checks: tuple[tuple[str, NumericCheck], ...]) -> None:
        for column, check in checks:
            name = f"__original_check.{len(self.checks)}"
            summary = table.aggregate(**{name: table[column].any().fill_null(False)})
            self.summaries.append(summary)
            self.checks.append((name, NumericCheck(summary[name], check.expected, check.received)))

    def complete_leaf(
        self,
        read: SourceRead,
        raw: ir.Table,
        axes: tuple[tuple[str, str], ...],
        *,
        terminal: bool = False,
        state_offset: int = 0,
        retain_checks: bool = False,
        row_checks: tuple[tuple[str, str, str], ...] = (),
    ) -> (
        tuple[ir.Table, tuple[NativeOriginalState, ...], tuple[tuple[str, NumericCheck], ...]] | str
    ):
        """Lower one captured source leaf, preserving its checks and compact states."""
        states: list[NativeOriginalState] = []
        target_keys = tuple(key for key, _ in axes)
        additions: dict[str, ir.Value] = {}
        assert read.reduction is not None
        fields = {path: raw[name] for path, name in read.field_columns if name in raw.columns}
        node = read.reduction.node
        integer_mean = (
            all(
                lower_row(operand, fields).value.type().is_integer()
                for operand in (node.value, node.weight)
            )
            if isinstance(node, SourceWeightedMean)
            else isinstance(node, SourceAggregate)
            and node.reducer == "mean"
            and lower_row(node.operand, fields).value.type().is_integer()
        )
        integer_quantile = (
            isinstance(node, SourceAggregate)
            and node.reducer in ("median", "quantile")
            and lower_row(node.operand, fields).value.type().is_integer()
        )
        decimal_quantile = (
            isinstance(node, SourceAggregate)
            and node.reducer in ("median", "quantile")
            and lower_row(node.operand, fields).value.type().is_decimal()
        )
        if decimal_quantile and self.prepared.sources.get(
            read.source_id, self.prepared.source
        ).provider.name not in ("duckdb", "postgres"):
            return "Decimal quantile has no qualified exact native method on this backend"
        reduction = replace(
            read.reduction,
            axes=axes,
            retain_quantile_bounds=terminal and integer_quantile,
            native_quantile_value=decimal_quantile,
        )
        expression, checks = lower_observation(
            reduction,
            raw,
            {path: name for path, name in read.field_columns if name in raw.columns},
            (),
            row_checks,
        )
        if terminal and isinstance(reduction.node, SourceWeightedMean):
            sums = {
                node: node.arg.to_expr().cast("decimal(38,0)").sum().op()
                for node in expression.op().find(ops.Sum)
                if node.arg.dtype.is_integer()
            }
            widened = expression.op().replace(sums).to_expr()
            assert isinstance(widened, ir.Table)
            expression = widened
        if not retain_checks:
            self.summary(expression, checks)
        if terminal:
            additions = {}
            if (
                integer_mean
                and not (reduction.calendar or reduction.calendars)
                and (
                    isinstance(node, SourceWeightedMean)
                    or (isinstance(node, SourceAggregate) and node.reducer == "mean")
                )
            ):
                value = lower_row(
                    node.value if isinstance(node, SourceWeightedMean) else node.operand, fields
                ).value
                numerator_name = f"__original_numerator.{state_offset + len(states)}"
                denominator_name = f"__original_denominator.{state_offset + len(states)}"
                if isinstance(node, SourceWeightedMean):
                    weight = lower_row(node.weight, fields).value
                    pair = value.notnull() & weight.notnull()
                    product = value.cast("decimal(38,0)") * weight.cast("decimal(38,0)")
                    valid = pair & product.between(-(2**63), 2**63 - 1)
                    numerator = valid.ifelse(product, ibis.null().cast("decimal(38,0)")).sum()
                    denominator = pair.ifelse(
                        weight.cast("decimal(38,0)"), ibis.null().cast("decimal(38,0)")
                    ).sum()
                else:
                    numerator, denominator = value.cast("decimal(38,0)").sum(), value.count()
                sums = (
                    raw.group_by(**{key: fields[path] for key, path in axes}).aggregate(
                        **{
                            numerator_name: numerator,
                            denominator_name: denominator,
                        }
                    )
                    if axes
                    else raw.aggregate(**{numerator_name: numerator, denominator_name: denominator})
                )
                expression = expression.join(
                    sums,
                    [expression[key].identical_to(sums[key]) for key in target_keys]
                    if target_keys
                    else ibis.literal(True),
                ).select(*expression.columns, numerator_name, denominator_name)
                additions = {
                    numerator_name: expression[numerator_name],
                    denominator_name: expression[denominator_name],
                }
                states.append(
                    NativeMeanState(reduction.value_slot, numerator_name, denominator_name)
                )
            elif isinstance(node, SourceAggregate) and integer_quantile:
                upper_name, count_name = (
                    f"__original_upper.{state_offset + len(states)}",
                    f"__original_count.{state_offset + len(states)}",
                )
                additions = {
                    upper_name: expression["__quantile_upper"],
                    count_name: expression["__quantile_count"],
                }
                states.append(
                    NativeQuantileState(
                        reduction.value_slot,
                        upper_name,
                        count_name,
                        0.5 if node.reducer == "median" else node.q if node.q is not None else 0.5,
                    )
                )
        if additions:
            expression = expression.mutate(**additions)
        return expression, tuple(states), checks

    def complete(
        self,
        program: FormulaProgram,
        reads: tuple[SourceRead, ...],
        raw: ir.Table,
        axes: tuple[tuple[str, str], ...],
        *,
        terminal: bool = False,
        output_columns: tuple[DataColumn, ...] = (),
        fibers: tuple[_Fiber, ...] = (),
    ) -> ir.Table | str:
        """Finish every original formula, keeping its pre-selection failures."""
        relations: dict[str, ir.Table] = {}
        target_keys = tuple(key for key, _ in axes)
        mapped_values = []
        transport: list[ir.Table] = []
        states: list[NativeOriginalState] = []
        inputs = {read.stage_id: fiber for fiber in fibers for read in fiber.reads}
        for binding, read in zip(program.values, reads, strict=True):
            if read.stage_id in inputs:
                fiber = inputs[read.stage_id]
                raw, axes = fiber.raw, fiber.axes
            completed = self.complete_leaf(
                read, raw, axes, terminal=terminal, state_offset=len(states)
            )
            if isinstance(completed, str):
                return completed
            expression, leaf_states, _ = completed
            states.extend(leaf_states)
            relations[binding.stage_id] = expression
            mapped_values.append(replace(binding, keys=target_keys))
            if terminal:
                state_columns = tuple(
                    name
                    for state in leaf_states
                    for name in (
                        (state.numerator, state.denominator)
                        if isinstance(state, NativeMeanState)
                        else (state.upper, state.count)
                    )
                )
                transport.append(
                    expression.select(*target_keys, binding.column.slot_id, *state_columns)
                )
        if terminal:
            expression = align_native_inputs(
                tuple(transport),
                target_keys,
                tuple(binding.column.slot_id for binding in program.values if binding.empty_zero),
                alignment="domain"
                if self.prepared.sources.get(reads[0].source_id, self.prepared.source).provider.name
                != "duckdb"
                else "outer",
            )
            self.finish = NativeOriginalFinish(
                replace(
                    program, values=tuple(replace(binding, checks=()) for binding in mapped_values)
                ),
                target_keys,
                tuple(column for column in output_columns if column.slot_id not in target_keys),
                tuple(states),
            )
            return expression
        result = lower_native_formula(
            replace(program, values=tuple(mapped_values)),
            relations,
            target_keys,
            alignment="domain"
            if self.prepared.sources.get(reads[0].source_id, self.prepared.source).provider.name
            != "duckdb"
            else "outer",
        )
        if isinstance(result, str):
            return result
        expression, checks = result
        self.summary(expression, checks)
        return expression.select(
            *target_keys, *(f"value.{index}" for index in range(len(program.outputs)))
        )

    def prepare_fiber(
        self,
        reads: tuple[SourceRead, ...],
        selected_domain: ir.Table | None = None,
        *,
        sampled: bool = False,
        selected_axes: tuple[tuple[str, str], ...] | None = None,
    ) -> _Fiber | str:
        first = reads[0]
        assert first.reduction is not None
        raw = self.prepared.relations.get(first.stage_id)
        if raw is None:
            return "original occurrence has no prepared source relation"
        from marivo.analysis.materialization.graph_source_execution import qualify_original_source

        qualification = qualify_original_source(self.prepared, reads, raw, sampled=sampled)
        if qualification is not None:
            return qualification
        from marivo.analysis.materialization.query_key_fusion import _physical_owner

        owner = self.prepared.sources.get(first.source_id, self.prepared.source)

        if not _physical_owner(raw, owner, self.prepared):
            return "original relation includes a different physical source owner"
        if selected_domain is not None:
            raw = _restrict_keys(
                raw,
                selected_domain,
                tuple(
                    (dict(first.field_columns)[path], key)
                    for key, path in (
                        first.reduction.axes if selected_axes is None else selected_axes
                    )
                ),
                owner.provider.name,
            )
        # Preparation facts cover the complete original demand, including rows
        # later discarded by where/top. Keep those summaries outside selection.
        for read in reads:
            self.members.add(read.stage_id)
            if read.preparation_checks:
                summary = raw.aggregate(
                    **{
                        name: raw[name].any().fill_null(False)
                        for name, _, _ in read.preparation_checks
                    }
                )
                self.summary(
                    summary,
                    tuple(
                        (name, NumericCheck(summary[name], expected, received))
                        for name, expected, received in read.preparation_checks
                    ),
                )
        if first.period is not None:
            time = raw[first.period.column]
            predicates = [time < temporal_bound(time, first.period.end, first.period.timezone)]
            if first.period.start is not None:
                predicates.append(
                    time >= temporal_bound(time, first.period.start, first.period.timezone)
                )
            raw = raw.filter(*predicates)
        from marivo.semantic._numeric_lowering import lower_field_predicate

        fields = {path: raw[name] for path, name in first.field_columns}
        selections = tuple(
            lower_field_predicate(predicate, fields) for predicate in first.predicates
        )
        if any(selection.checks for selection in selections):
            return "contribution predicate retains independently scoped numeric checks"
        if selections:
            raw = raw.filter(*(selection.value for selection in selections))
        assert first.component_layout is not None
        missing_identity = ibis.literal(False)
        for _, name in first.component_layout.identity:
            missing_identity = missing_identity | raw[name].isnull()
        identity_check = raw.select(__original_identity_missing=missing_identity)
        self.summary(
            identity_check,
            (
                (
                    "__original_identity_missing",
                    NumericCheck(
                        identity_check["__original_identity_missing"],
                        "complete nonnull contribution identities for original recomputation",
                        "missing original contribution identity",
                    ),
                ),
            ),
        )
        support = tuple(
            dict.fromkeys(
                (
                    *(dict(first.field_columns)[path] for _, path in first.reduction.axes),
                    *(name for _, name in first.component_layout.identity),
                )
            )
        )
        counts = raw.group_by(*support).aggregate(__original_count=raw.count())
        counts = counts.mutate(__original_duplicate=counts["__original_count"] > 1)
        self.summary(
            counts,
            (
                (
                    "__original_duplicate",
                    NumericCheck(
                        counts["__original_duplicate"],
                        "one complete contribution identity per fine coordinate",
                        "duplicate original contribution identity",
                    ),
                ),
            ),
        )
        return _Fiber(raw, first.reduction.axes, reads)

    def root(
        self,
        stage: LocalCompute | SourceRead,
        *,
        selected_domain: ir.Table | None = None,
        complete_parent: bool = True,
    ) -> _Relation | str:
        final = self.stages[self.lowered.plan.publish]
        program = (
            stage.program
            if isinstance(stage, LocalCompute)
            else (
                final.result.original
                if isinstance(final, LocalCompute) and final.result is not None
                else None
            )
        )
        if program is None:
            return "original source has no captured formula"
        if program.calendar or program.populations or program.output_population or program.members:
            return "original formula retains a calendar, sample or explicit output domain"
        reads: list[SourceRead] = []
        for binding in program.values:
            item = self.stages.get(binding.stage_id)
            if isinstance(item, SourceRead) and item.common_reads:
                item = next(
                    (read for read in item.common_reads if read.stage_id == binding.stage_id), None
                )
            if not isinstance(item, SourceRead):
                item = next(
                    (
                        read
                        for candidate in self.stages.values()
                        if isinstance(candidate, SourceRead)
                        for read in candidate.common_reads
                        if read.stage_id == binding.stage_id
                    ),
                    None,
                )
            if (
                not isinstance(item, SourceRead)
                or item.reduction is None
                or item.component_layout is None
            ):
                return "original formula lacks complete source-bound contribution identity"
            reduction = item.reduction
            if (
                item.inputs
                or item.parameters
                or reduction.calendar
                or reduction.calendars
                or reduction.sampled
                or reduction.preserve_sample_presence
                or reduction.window_key is not None
                or reduction.duration is not None
            ):
                return "original source retains dynamic parameters, temporal or internal sample semantics"
            if isinstance(reduction.node, SourceAggregate) and reduction.node.reducer.startswith(
                "approx_"
            ):
                return "approximate algorithms have no original native qualification"
            reads.append(item)
        if not reads:
            return "original formula has no source reductions"
        first = reads[0]
        owner = self.prepared.sources.get(first.source_id, self.prepared.source)
        # Each backend retains its own numeric and relational qualification.
        # Recomputations may consume current source data under the captured keys.
        if owner.provider.name not in ("duckdb", "postgres", "mysql", "trino", "clickhouse"):
            return "original recomputation has no qualified source statement route"
        assert first.reduction is not None
        groups: dict[str, list[SourceRead]] = {}
        for read in reads:
            if (
                not read.binding_id
                or read.keys != first.keys
                or self.prepared.sources.get(read.source_id, self.prepared.source) is not owner
            ):
                return "original roots require one physical source owner and identical complete output keys"
            groups.setdefault(read.binding_id, []).append(read)
        fibers: list[_Fiber] = []
        for group in groups.values():
            head = group[0]
            if any(
                read.field_columns != head.field_columns
                or read.predicates != head.predicates
                or read.period != head.period
                or (
                    read.reduction != head.reduction
                    and read.reduction is not None
                    and head.reduction is not None
                    and read.reduction.axes != head.reduction.axes
                )
                for read in group
            ):
                return "one original input binding has inconsistent source facts"
            fiber = self.prepare_fiber(tuple(group), selected_domain)
            if isinstance(fiber, str):
                return fiber
            fibers.append(fiber)
        raw = fibers[0].raw
        expression = (
            self.complete(program, tuple(reads), raw, first.reduction.axes, fibers=tuple(fibers))
            if complete_parent
            else (
                selected_domain
                if selected_domain is not None
                else raw.select(
                    **{
                        key: raw[dict(first.field_columns)[path]]
                        for key, path in first.reduction.axes
                    }
                ).distinct()
            ).mutate(
                **{
                    f"value.{index}": ibis.null().cast("int64")
                    for index in range(len(program.outputs))
                }
            )
        )
        if isinstance(expression, str):
            return expression
        return _Relation(
            expression,
            raw,
            first.reduction.axes,
            program,
            tuple(reads),
            owner,
            tuple(fibers),
        )

    def selected(self, relation: _Relation, expression: ir.Table) -> _Relation:
        fibers = tuple(
            replace(
                fiber,
                raw=_restrict_keys(
                    fiber.raw,
                    expression,
                    tuple(
                        (dict(fiber.reads[0].field_columns)[path], key) for key, path in fiber.axes
                    ),
                    relation.owner.provider.name,
                ),
            )
            for fiber in relation.fibers
        )
        return replace(relation, expression=expression, raw=fibers[0].raw, fibers=fibers)

    def deduplicate(
        self, relation: _Relation, axes: tuple[tuple[str, str], ...], fiber: _Fiber | None = None
    ) -> ir.Table:
        """Union complete identities within each target, without value DISTINCT."""
        reads = relation.reads if fiber is None else fiber.reads
        first = reads[0]
        assert first.component_layout is not None
        fields = dict(first.field_columns)
        paths = {path for _, path in axes}
        for read in reads:
            assert read.reduction is not None
            for node in walk(
                (read.reduction.node,), resolve=dict(relation.formula.declared).get
            ).order:
                if isinstance(node, RowField):
                    paths.add(
                        field_read_key(node.read) if node.read is not None else node.ref.ref.path
                    )
        columns = tuple(
            dict.fromkeys(
                (
                    *(name for _, name in first.component_layout.identity),
                    *(fields[path] for path in sorted(paths)),
                    *((name for name, _, _ in fiber.checks) if fiber is not None else ()),
                )
            )
        )
        return (relation.raw if fiber is None else fiber.raw).select(*columns).distinct()

    def drill_fiber(
        self,
        stage: LocalCompute,
        relation: _Relation,
        fiber: _Fiber,
        *,
        retain_checks: bool = False,
    ) -> _Fiber | str:
        from marivo.analysis.materialization.query_parameters import _replace_parameter

        assert stage.result is not None
        schemas = tuple(
            schema
            for schema in stage.result.drills
            if any(
                read.reduction is not None and schema.value_slot == read.reduction.value_slot
                for read in fiber.reads
            )
        )
        first = fiber.reads[0]
        assert first.reduction is not None
        schema = next(
            (
                schema
                for schema in stage.result.drills
                if schema.value_slot == first.reduction.value_slot
            ),
            None,
        )
        assert first.component_layout is not None
        if schema is None or len(schemas) != len(fiber.reads):
            return "drill lacks one exact attribute template for each original occurrence"
        if any(
            item.root != schema.root
            or item.identity != schema.identity
            or item.projections != schema.projections
            for item in schemas
        ):
            return "drill occurrences do not share one complete attribute mapping"
        template = self.prepared.relations.get(schema.source_id)
        if template is None:
            return "drill attribute relation is not prepared"
        identities = fiber.raw.select(
            **{column.slot_id: fiber.raw[name] for column, name in schema.identity}
        ).distinct()
        attributes = _replace_parameter(template, schema.parameter, identities)
        from marivo.analysis.materialization.query_key_fusion import _physical_owner

        if not _physical_owner(
            attributes, relation.owner, self.prepared, allowed_parameters=self.allowed_parameters
        ):
            return "drill attribute physical inputs belong to a different source owner"
        if schema.preparation_checks and not retain_checks:
            self.summary(
                attributes,
                tuple(
                    (name, NumericCheck(attributes[name], expected, received))
                    for name, expected, received in schema.preparation_checks
                ),
            )
        additions = tuple(
            (slot, name) for slot, name in schema.projections if slot not in schema.keys
        )
        if relation.owner.provider.name != "duckdb" and any(
            not attributes[name].type().is_string() for _, name in additions
        ):
            return "Remote original drill requires qualified string attributes"
        if relation.owner.provider.name != "duckdb":
            from marivo.analysis.materialization.graph_source_execution import (
                qualify_original_strings,
            )

            qualification = qualify_original_strings(
                self.prepared, attributes, tuple(name for _, name in additions)
            )
            if qualification is not None:
                return qualification
        attributes = attributes.select(
            **{
                **{column.slot_id: attributes[name] for column, name in schema.identity},
                **{slot: attributes[name] for slot, name in additions},
                **(
                    {name: attributes[name] for name, _, _ in schema.preparation_checks}
                    if retain_checks
                    else {}
                ),
            }
        )
        raw = fiber.raw.left_join(
            attributes,
            [
                _same_key(fiber.raw[name], attributes[column.slot_id], relation.owner.provider.name)
                for column, name in schema.identity
            ],
        ).select(
            *fiber.raw.columns,
            *(slot for slot, _ in additions),
            *((name for name, _, _ in schema.preparation_checks) if retain_checks else ()),
        )
        added_fields = tuple(
            (f"__source_drill.{index}", slot) for index, (slot, _) in enumerate(additions)
        )
        axes = (
            *fiber.axes,
            *(
                (f"key.{len(fiber.axes) + index}", path)
                for index, (path, _) in enumerate(added_fields)
            ),
        )
        reads = tuple(
            replace(read, field_columns=(*read.field_columns, *added_fields))
            for read in fiber.reads
        )
        target = replace(relation, raw=raw, reads=reads)
        checks = (*fiber.checks, *schema.preparation_checks) if retain_checks else fiber.checks
        target_fiber = _Fiber(raw, axes, reads, checks)
        raw = self.deduplicate(target, axes, target_fiber)
        return _Fiber(raw, axes, reads, checks)

    def lower(self, identifier: str) -> _Relation | str:
        if identifier in self.overrides:
            return self.overrides[identifier]
        stage = self.stages[identifier]
        self.members.add(identifier)
        if isinstance(stage, SourceRead):
            return self.root(stage)
        if not isinstance(stage, LocalCompute):
            return "fixed Frame data is outside source fusion"
        if stage.fused_steps:
            return "original route retains a source or fused intermediate boundary"
        if stage.program is not None:
            return self.root(stage)
        if stage.result is None or not stage.inputs:
            return "original route has no result program"
        operation = stage.result.operation
        if not isinstance(
            operation,
            (FilterValues, TopValues, SelectValues, RollupValues, DrillValues, ShareValues),
        ):
            return "original route retains a local relation operation"
        relation = self.lower(stage.inputs[0])
        if isinstance(relation, str):
            return relation
        table = relation.expression
        values = tuple(table[f"value.{index}"] for index in range(len(relation.formula.outputs)))
        keys = tuple(key for key, _ in relation.axes)
        coordinates = tuple(table[key] for key in keys)
        if isinstance(operation, FilterValues):
            if relation.owner.provider.name != "duckdb":
                return "Remote parent selection retains its pandas value boundary"
            if relation.recomputed:
                return (
                    "selection after original host numeric completion retains its existing boundary"
                )
            from marivo._definition.query import ValueExpression

            if operation.domains or not isinstance(operation.predicate, ValueExpression):
                return "original selector retains population inputs"
            from marivo.analysis.materialization.query_key_fusion import (
                _predicate_value,
                _safe_expression,
            )

            if not _safe_expression(operation.predicate):
                return "original selector expression has no exact native predicate route"
            condition = _predicate_value(
                operation.predicate, values, coordinates, relation.owner.provider.name
            )
            if not isinstance(condition, ir.BooleanValue):
                return "original selector is not boolean"
            return self.selected(relation, table.filter(condition.fill_null(False)))
        if isinstance(operation, TopValues):
            if relation.owner.provider.name != "duckdb":
                return "Remote parent ranking retains its pandas value boundary"
            if relation.recomputed:
                return (
                    "ranking after original host numeric completion retains its existing boundary"
                )
            from marivo.analysis.materialization.query_key_fusion import _safe_expression

            if not _safe_expression(operation.score):
                return "original ranking expression has no native order route"
            score = expression_value(operation.score, values, coordinates)
            if score.type().is_string():
                score = string_bytes(score, relation.owner.provider.name)
            order = [
                score.desc(nulls_first=False)
                if operation.descending
                else score.asc(nulls_first=False)
            ]
            for key in coordinates:
                ordered = (
                    string_bytes(key, relation.owner.provider.name)
                    if key.type().is_string()
                    else key
                )
                order.append(ordered.asc(nulls_first=True))
            partitions = tuple(
                table[key]
                for position in operation.partitions
                for key in stage.result.key_groups[position]
            )
            ranked = table.mutate(
                __original_rank=ibis.row_number().over(
                    ibis.window(group_by=partitions, order_by=order)
                )
            )
            selected = ranked.filter(ranked["__original_rank"] < operation.n).drop(
                "__original_rank"
            )
            return self.selected(relation, selected)
        if isinstance(operation, SelectValues):
            if not all(isinstance(value, ValueSlot) for _, value in operation.values):
                return "original selection transforms a formula instead of retaining its values"
            indexes = tuple(
                value.index for _, value in operation.values if isinstance(value, ValueSlot)
            )
            if indexes != tuple(range(len(relation.formula.outputs))):
                return "original selection changes reduction occurrences"
            return relation
        if isinstance(operation, RollupValues):
            if operation.calendar_maps:
                return "original rollup retains a calendar coordinate mapping"
            selected = tuple(
                key for position in operation.positions for key in stage.result.key_groups[position]
            )
            fibers = []
            for fiber in relation.fibers:
                axes = tuple(
                    (f"key.{index}", dict(fiber.axes)[key]) for index, key in enumerate(selected)
                )
                fibers.append(
                    replace(fiber, raw=self.deduplicate(relation, axes, fiber), axes=axes)
                )
            raw, axes = fibers[0].raw, fibers[0].axes
            expression = self.complete(
                relation.formula,
                relation.reads,
                raw,
                axes,
                terminal=identifier == self.lowered.plan.publish,
                output_columns=stage.columns,
                fibers=tuple(fibers),
            )
            if isinstance(expression, str):
                return expression
            self.operations.append("rollup")
            return replace(
                relation,
                expression=expression,
                raw=raw,
                axes=axes,
                fibers=tuple(fibers),
                recomputed=True,
            )
        if isinstance(operation, DrillValues):
            fibers = []
            for fiber in relation.fibers:
                target = self.drill_fiber(stage, relation, fiber)
                if isinstance(target, str):
                    return target
                fibers.append(target)
            read_by_id = {read.stage_id: read for fiber in fibers for read in fiber.reads}
            reads = tuple(read_by_id[read.stage_id] for read in relation.reads)
            raw, axes = fibers[0].raw, fibers[0].axes
            expression = self.complete(
                relation.formula,
                reads,
                raw,
                axes,
                terminal=identifier == self.lowered.plan.publish,
                output_columns=stage.columns,
                fibers=tuple(fibers),
            )
            if isinstance(expression, str):
                return expression
            self.operations.append("drill")
            return replace(
                relation,
                expression=expression,
                raw=raw,
                axes=axes,
                reads=reads,
                fibers=tuple(fibers),
                recomputed=True,
            )
        if len(relation.fibers) != 1:
            return "multi-root share retains its per-occurrence support and host denominator checks"
        if operation.calendar_maps:
            return "share retains a calendar denominator mapping"
        node = relation.formula.outputs[operation.value_index][1]
        merged_axes = tuple(
            axis
            for position, axis in enumerate(stage.result.axes)
            if position not in operation.partitions
        )
        if not share_additive(node, dict(relation.formula.declared), merged_axes):
            return "share retains a nonadditive original formula"
        partition_keys = tuple(
            key for position in operation.partitions for key in stage.result.key_groups[position]
        )
        axes = tuple(
            (f"key.{index}", dict(relation.axes)[key]) for index, key in enumerate(partition_keys)
        )
        first = relation.reads[0]
        assert first.component_layout is not None
        fields = dict(first.field_columns)
        support_keys = tuple(
            dict.fromkeys(
                (
                    *(fields[path] for _, path in axes),
                    *(name for _, name in first.component_layout.identity),
                )
            )
        )
        support = relation.raw.group_by(*support_keys).aggregate(
            __original_count=relation.raw.count()
        )
        support = support.mutate(__original_overlap=support["__original_count"] > 1)
        self.summary(
            support,
            (
                (
                    "__original_overlap",
                    NumericCheck(
                        support["__original_overlap"],
                        "disjoint fine contribution support within every share denominator group",
                        "overlapping original contribution identity",
                    ),
                ),
            ),
        )
        selected = tuple(
            (binding, read)
            for binding, read in zip(relation.formula.values, relation.reads, strict=True)
            if binding.occurrence[0] == operation.value_index
        )
        program = replace(
            relation.formula,
            outputs=(relation.formula.outputs[operation.value_index],),
            values=tuple(
                replace(binding, occurrence=(0, *binding.occurrence[1:])) for binding, _ in selected
            ),
        )
        raw = self.deduplicate(relation, axes)
        denominator = self.complete(program, tuple(read for _, read in selected), raw, axes)
        if isinstance(denominator, str):
            return denominator
        denominator = denominator.select(
            **{
                **{f"__share_key.{index}": denominator[key] for index, (key, _) in enumerate(axes)},
                "__share_denominator": denominator["value.0"],
            }
        )
        combined = table.left_join(
            denominator,
            [
                table[key].identical_to(denominator[f"__share_key.{index}"])
                for index, key in enumerate(partition_keys)
            ]
            if partition_keys
            else ibis.literal(True),
        ).select(*table.columns, "__share_denominator")
        shared = numeric_binary(
            "divide",
            NumericValue(combined[f"value.{operation.value_index}"]),
            NumericValue(combined["__share_denominator"]),
            zero_denominator=operation.zero_denominator,
        )
        expression = combined.select(
            *table.columns,
            "__share_denominator",
            **{f"value.share.{operation.value_index}": shared.value.cast("float64")},
            **{
                f"__share_check.{index}": check.violation
                for index, check in enumerate(shared.checks)
            },
        )
        self.summary(
            expression,
            tuple((f"__share_check.{index}", check) for index, check in enumerate(shared.checks)),
        )
        self.operations.append("share")
        self.finish = NativeShareFinish(
            f"value.{operation.value_index}",
            "__share_denominator",
            f"value.share.{operation.value_index}",
            operation.zero_denominator,
        )
        return replace(
            relation,
            expression=expression.select(
                *table.columns, f"value.share.{operation.value_index}", "__share_denominator"
            ),
        )


def _original_read(
    native: _NativeOriginal, relation: _Relation, final: LocalCompute
) -> LoweredRead:
    """Preserve compact finishers, logical reads and source check summaries."""
    expression = relation.expression.mutate(__numeric_present=ibis.literal(True))
    for summary in native.summaries:
        expression = summary.left_join(expression, ibis.literal(True)).select(
            *summary.columns, *expression.columns
        )
    finish = native.finish
    if isinstance(finish, NativeOriginalFinish):
        finish = replace(
            finish,
            program=replace(
                finish.program,
                values=tuple(
                    replace(binding, stage_id=final.stage_id) for binding in finish.program.values
                ),
            ),
        )
    first = relation.reads[0]
    read = SourceRead(
        final.stage_id,
        first.source_id,
        first.occurrence,
        final.columns,
        final.keys,
        (),
        (),
        first.predicates,
        first.period,
        native_reductions=tuple(
            read.reduction for read in relation.reads if read.reduction is not None
        ),
        native_original_finish=finish,
        native_reads=relation.reads,
        binding_id=first.binding_id,
    )
    return LoweredRead(read, expression, tuple(native.checks))


def build_dependent_original_parent(
    prepared: PreparedSources, lowered: LoweredQuery, root: LocalCompute
) -> LoweredRead | str:
    """Keep the first checked parent on its original native numeric method."""
    if root.program is None:
        return "dependent original parent requires a captured observation formula"
    native = _NativeOriginal(prepared, lowered)
    relation = native.root(root)
    if isinstance(relation, str):
        from marivo.analysis.materialization.source_original_complex import (
            build_dependent_complex_parent,
        )

        return build_dependent_complex_parent(prepared, lowered, root)
    return _original_read(native, relation, root)


def build_dependent_original_target(
    prepared: PreparedSources, lowered: LoweredQuery, target: LocalCompute, selected_keys: ir.Table
) -> LoweredRead | str:
    """Recompute a captured ordinary target using already checked parent keys."""
    if target.result is None or not isinstance(
        target.result.operation, (RollupValues, DrillValues)
    ):
        return "dependent original target requires an ordinary rollup or drill"
    native = _NativeOriginal(prepared, lowered)
    native.allowed_parameters = frozenset(selected_keys.op().find(ops.UnboundTable))
    parent = native.stages[target.inputs[0]]
    while (
        isinstance(parent, LocalCompute)
        and parent.result is not None
        and isinstance(parent.result.operation, (FilterValues, TopValues, SelectValues))
    ):
        parent = native.stages[parent.inputs[0]]
    if not isinstance(parent, LocalCompute) or parent.program is None:
        return "dependent original target requires one plain captured observation"
    if tuple(selected_keys.columns) != parent.keys:
        return "dependent original target requires the complete selected parent keys"
    relation = native.root(parent, selected_domain=selected_keys, complete_parent=False)
    if isinstance(relation, str):
        from marivo.analysis.materialization.source_original_complex import (
            build_dependent_complex_target,
        )

        return build_dependent_complex_target(prepared, lowered, target, selected_keys)
    native.overrides[target.inputs[0]] = relation
    relation = native.lower(target.stage_id)
    if isinstance(relation, str):
        return relation
    operation = target.result.operation
    if isinstance(operation, RollupValues) and target.keys:
        kept = tuple(
            key for position in operation.positions for key in target.result.key_groups[position]
        )
        domain = selected_keys.select(
            **{
                key: selected_keys[parent_key]
                for key, parent_key in zip(target.keys, kept, strict=True)
            }
        ).distinct()
        table = relation.expression
        joined = domain.left_join(
            table,
            [
                _same_key(domain[key], table[key], relation.owner.provider.name)
                for key in target.keys
            ],
        )
        expression = joined.select(
            *[domain[key] for key in target.keys],
            *[table[name] for name in table.columns if name not in target.keys],
        )
        zeros = tuple(
            binding.column.slot_id for binding in relation.formula.values if binding.empty_zero
        )
        expression = (
            expression.mutate(**{name: expression[name].fill_null(0) for name in zeros})
            if zeros
            else expression
        )
        relation = replace(relation, expression=expression)
    return _original_read(native, relation, target)


def _fuse_original(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Replace a qualified terminal original chain with compact native output."""
    final = next(stage for stage in lowered.plan.stages if stage.stage_id == lowered.plan.publish)
    if (
        not isinstance(final, LocalCompute)
        or final.result is None
        or not isinstance(final.result.operation, (RollupValues, DrillValues, ShareValues))
    ):
        return lowered
    native = _NativeOriginal(prepared, lowered)
    relation = native.lower(final.stage_id)
    if isinstance(relation, str):
        from marivo.analysis.materialization.source_original_complex import fuse_complex_original

        complex_result = fuse_complex_original(prepared, lowered)
        if complex_result is not None:
            return complex_result
        return replace(
            lowered,
            plan=replace(
                lowered.plan,
                optimization_decisions=(
                    *lowered.plan.optimization_decisions,
                    RuleDecision("source_original", "refused", (final.stage_id,), relation),
                ),
            ),
        )
    read_result = _original_read(native, relation, final)
    read = read_result.stage
    stages = (read,)
    plan = replace(
        lowered.plan,
        stages=stages,
        consumers=((read.stage_id, ()),),
        original=None,
        required_slots=(),
        fusion_groups=(),
        optimization_decisions=(
            *lowered.plan.optimization_decisions,
            RuleDecision(
                "source_original",
                "adopted",
                tuple(sorted(native.members)),
                f"Single {relation.owner.provider.name} statement completes parent checks, selected complete contribution fibers and original native formulas.",
            ),
        ),
    )
    plan = replace(plan, required_slots=required_slots(plan))
    return LoweredQuery(plan, (read_result,))


def fuse_source_original(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Compact a closed original prefix before its remaining local consumers."""
    from marivo.analysis.materialization.source_original_calendar import fuse_calendar_original

    calendar = fuse_calendar_original(prepared, lowered)
    if calendar is not None:
        return calendar
    plan = lowered.plan
    candidates = tuple(
        stage
        for stage in reversed(plan.stages)
        if isinstance(stage, LocalCompute)
        and stage.result is not None
        and isinstance(stage.result.operation, (RollupValues, DrillValues, ShareValues))
    )
    for stage in candidates:
        if not any(item.stage_id == stage.stage_id for item in plan.stages):
            continue
        # A later original consumer needs contribution fibers, so compact only
        # the last original boundary of each branch. Ordinary local consumers
        # retain their exact value arithmetic, predicates and display ordering.
        if required_for(plan, stage.stage_id).components:
            continue
        by_id = {item.stage_id: item for item in plan.stages}
        ancestors: set[str] = set()

        pending = [stage.stage_id]
        while pending:
            identifier = pending.pop()
            if identifier not in ancestors:
                ancestors.add(identifier)
                pending.extend(by_id[identifier].inputs)
        if any(
            consumer not in ancestors
            for identifier in ancestors - {stage.stage_id}
            for consumer in plan.consumers_of(identifier)
        ):
            continue
        if any(check.stage_id in ancestors for check in plan.checks):
            continue
        assert stage.result is not None
        prefix = replace(
            lowered,
            plan=replace(plan, publish=stage.stage_id, output_groups=stage.result.output_groups),
        )
        compact = _fuse_original(prepared, prefix)
        if not isinstance(compact.plan.stages[0], SourceRead) or len(compact.plan.stages) != 1:
            plan = replace(plan, optimization_decisions=compact.plan.optimization_decisions)
            lowered = replace(lowered, plan=plan)
            continue
        replacement = compact.plan.stages[0]
        stages = tuple(
            replacement if item.stage_id == stage.stage_id else item
            for item in plan.stages
            if item.stage_id == stage.stage_id or item.stage_id not in ancestors
        )
        consumers: dict[str, list[str]] = {item.stage_id: [] for item in stages}
        for item in stages:
            for dependency in item.inputs:
                consumers[dependency].append(item.stage_id)
        plan = replace(
            plan,
            stages=stages,
            consumers=tuple((identifier, tuple(items)) for identifier, items in consumers.items()),
            required_slots=(),
            fusion_groups=tuple(
                group for group in plan.fusion_groups if not ancestors.intersection(group.members)
            ),
            optimization_decisions=compact.plan.optimization_decisions,
        )
        plan = replace(plan, required_slots=required_slots(plan))
        lowered = LoweredQuery(
            plan,
            (
                *tuple(read for read in lowered.reads if read.stage.stage_id not in ancestors),
                *compact.reads,
            ),
        )
    return lowered


def finish_native_states(
    states: tuple[NativeOriginalState, ...], frame: pd.DataFrame
) -> pd.DataFrame:
    """Finish one leaf batch with the established host arithmetic."""
    from fractions import Fraction

    data = frame.copy()
    for state in states:
        values: list[float | None] = []
        if isinstance(state, NativeMeanState):
            for numerator, denominator in zip(
                data[state.numerator], data[state.denominator], strict=True
            ):
                values.append(
                    None
                    if pd.isna(denominator) or denominator == 0
                    else int(numerator) / int(denominator)
                )
        else:
            for lower, upper, count in zip(
                data[state.value], data[state.upper], data[state.count], strict=True
            ):
                if pd.isna(count) or count == 0:
                    values.append(None)
                else:
                    position = Fraction(str(state.probability)) * (int(count) - 1)
                    fraction = float(position - position.numerator // position.denominator)
                    values.append(int(lower) + (int(upper) - int(lower)) * fraction)
        data[state.value] = pd.Series(values, index=data.index, dtype=object)
    return data


def finish_native_original(finish: NativeOriginalFinisher, frame: pd.DataFrame) -> pd.DataFrame:
    """Finish integer states and native leaf values with the owning host formula."""
    from marivo.analysis.methods.local import _combine_program
    from marivo.semantic._numeric_lowering import numeric_failure

    if isinstance(finish, NativeOriginalBatchFinish):
        from marivo.analysis.materialization.source_original_complex import finish_original_batch

        return finish_original_batch(finish, frame)
    if isinstance(finish, NativeOriginalPhaseFinish):
        from marivo.analysis.materialization.source_original_complex import finish_original_phase

        return finish_original_phase(finish, frame)
    data = frame.copy()
    if isinstance(finish, NativeShareFinish):
        from marivo.analysis.methods.numeric_state import finish_float64, nullable_arithmetic

        divided = nullable_arithmetic(
            "divide",
            data[finish.numerator],
            data[finish.denominator],
            zero_denominator=finish.zero_denominator,
        )
        data[finish.output] = finish_float64(divided, "share")
        return data
    data = finish_native_states(finish.states, data)
    inputs = tuple(
        data.loc[:, [*finish.keys, binding.column.slot_id]] for binding in finish.program.values
    )
    result, checks = _combine_program(finish.program, inputs, finish.keys, finish.outputs)
    for name, expected, received in checks:
        if result[name].astype("boolean").fillna(False).any():
            numeric_failure(expected, received)
    return result.loc[:, [*finish.keys, *(column.slot_id for column in finish.outputs)]]
