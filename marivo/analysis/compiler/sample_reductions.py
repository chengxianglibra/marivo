"""Native reductions over completed, exactly aligned Metric sample values."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    MemberCount,
    MetricExpression,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.relations import PopulationDependency, sample_population
from marivo._definition.types import AxisBinding, CalendarCoordinate, IntervalCoordinate
from marivo._source_aggregates import aggregate_operation, native_aggregate
from marivo._temporal import Grain
from marivo.analysis.compiler.native_formula import normalized_returned_key
from marivo.analysis.compiler.observation import FormulaInput, FormulaProgram
from marivo.analysis.compiler.source_predicates import (
    aggregate_returned_keys,
    distinct_returned_keys,
    same_returned_key,
    string_bytes,
)
from marivo.analysis.core.calendar_keys import time_keys
from marivo.analysis.core.data_types import DurationDataType
from marivo.analysis.errors import AnalysisStageUnavailableError
from marivo.semantic._numeric_lowering import (
    NumericCheck,
    NumericValue,
    force_cast,
    lower_metric_formula,
    native_mean,
    numeric_binary,
)


@dataclass(frozen=True)
class SampleRelation:
    """One completed Metric at exactly its retained coordinate grain."""

    table: ir.Table
    value: str
    checks: tuple[tuple[str, str, str], ...] = ()
    zero_when_absent: bool = False


class SampleReductionUnavailableError(AnalysisStageUnavailableError):
    """A schema-bound refusal; never an execution failure or retry signal."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            stage="sample_reduction_binding",
            expected="a qualified native relation preserving the complete sample contract",
            received=reason,
            repair="Retain the existing local sample reduction for this subtree.",
            target="analysis_frame.compute",
        )


def _null(value: ir.Value) -> ir.Value:
    return ibis.null().cast(value.type().copy(nullable=True))


def _present(table: ir.Table, width: int) -> ir.BooleanValue | None:
    name = next(
        (
            name
            for name in (f"__sample_scope_{width}", "__sample_present", "__numeric_present")
            if name in table.columns
        ),
        None,
    )
    if name is None:
        return None
    value = table[name].fill_null(False)
    assert isinstance(value, ir.BooleanValue)
    return value


class SampleLowerer:
    """Lower an existing FormulaProgram without reconstructing source contributions."""

    def __init__(
        self,
        program: FormulaProgram,
        relations: Mapping[str, ir.Table],
        keys: tuple[str, ...],
        provider: str,
        population: Callable[[PopulationDependency, tuple[str, ...]], ir.Table],
        grid: Callable[[tuple[str, ...], tuple[tuple[object, ...], ...]], ir.Table],
        identities: Mapping[str, str] | None = None,
    ) -> None:
        self.program = program
        self.relations = relations
        self.keys = keys
        self.provider = provider
        self.population = population
        self.grid = grid
        self.identities = identities or {}
        self.declared = dict(program.declared)
        self.serial = 0
        self.cache: dict[
            tuple[MetricNode, tuple[str, ...], tuple[str, ...], ops.Relation | None], SampleRelation
        ] = {}

    def name(self, role: str = "value") -> str:
        self.serial += 1
        return f"__sample_{role}_{self.serial}"

    def bindings(self, path: tuple[int, ...]) -> tuple[FormulaInput, ...]:
        return tuple(item for item in self.program.values if item.occurrence[: len(path)] == path)

    def domain(self, keys: tuple[str, ...], path: tuple[int, ...]) -> ir.Table:
        while path and not self.bindings(path):
            path = path[:-1]
        if not keys:
            # The scalar coordinate exists independently of fact/sample presence.
            return ibis.literal(True).name("__sample_unit").as_table()
        tables = []
        for binding in self.bindings(path):
            if binding.stage_id not in self.relations:
                raise SampleReductionUnavailableError(
                    "complete enclosing domain crosses source execution owners"
                )
            table = self.relations[binding.stage_id]
            if not all(key in table.columns for key in keys):
                raise SampleReductionUnavailableError(
                    "sample inputs have different coordinate prefixes"
                )
            present = _present(table, len(keys))
            if present is not None:
                table = table.filter(present)
            tables.append(
                distinct_returned_keys(
                    table.select(
                        **{key: normalized_returned_key(table[key], self.provider) for key in keys}
                    ),
                    self.provider,
                )
            )
        result = tables[0]
        for table in tables[1:]:
            if table.op() != result.op():
                result = distinct_returned_keys(result.union(table, distinct=False), self.provider)
        return result

    def align(
        self, domain: ir.Table, values: tuple[SampleRelation, ...], keys: tuple[str, ...]
    ) -> ir.Table:
        result = domain
        for value in {value.value: value for value in values}.values():
            table = value.table.mutate(
                **{
                    value.value: force_cast(
                        value.table[value.value],
                        value.table[value.value].type().copy(nullable=True),
                    )
                },
                __sample_match=ibis.literal(True),
            ).view()
            parent = result.view()
            joined = parent.left_join(
                table,
                [same_returned_key(parent[key], table[key], self.provider) for key in keys]
                or ibis.literal(True),
            )
            numeric = table[value.value]
            if value.zero_when_absent:
                numeric = (
                    table["__sample_match"]
                    .fill_null(False)
                    .ifelse(numeric, ibis.literal(0).cast(numeric.type()))
                )
            result = joined.select(
                **{name: parent[name] for name in parent.columns},
                **{value.value: numeric},
                **{name: table[name].fill_null(False) for name, _, _ in value.checks},
            )
        return result

    def lower(
        self,
        node: MetricNode,
        path: tuple[int, ...],
        keys: tuple[str, ...],
        forced: ir.Table | None = None,
    ) -> SampleRelation:
        if not self.bindings(path):
            return self._lower(node, path, keys, forced)
        identity = (
            node,
            keys,
            tuple(
                self.identities.get(binding.stage_id, binding.stage_id)
                for binding in self.bindings(path)
            ),
            None if forced is None else forced.op(),
        )
        cached = self.cache.get(identity)
        if cached is not None:
            return cached
        result = self._lower(node, path, keys, forced)
        self.cache[identity] = result
        return result

    def _lower(
        self,
        node: MetricNode,
        path: tuple[int, ...],
        keys: tuple[str, ...],
        forced: ir.Table | None = None,
    ) -> SampleRelation:
        if isinstance(node, DeclaredMetricRef):
            return self.lower(self.declared[node.ref], (*path, 0), keys, forced)
        if isinstance(node, (ContributionFilter, MetricWindow)):
            return self.lower(node.operand, (*path, 0), keys, forced)
        if isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount)):
            bindings = self.bindings(path)
            if any(binding.keys != keys for binding in bindings):
                raise SampleReductionUnavailableError(
                    "contribution has a different complete sample grain"
                )
            binding = bindings[0]
            if any(isinstance(item.column.dtype, DurationDataType) for item in bindings):
                raise SampleReductionUnavailableError(
                    "Duration retains its exact local tick finisher"
                )
            if any(
                item.column.dtype != binding.column.dtype
                or tuple((expected, received) for _, expected, received in item.checks)
                != tuple((expected, received) for _, expected, received in binding.checks)
                for item in bindings
            ):
                raise SampleReductionUnavailableError(
                    "window contributions retain different carriers or check contracts"
                )
            value = self.name()
            checks = tuple(
                (self.name("check"), expected, received) for _, expected, received in binding.checks
            )
            tables = []
            for contribution_input in bindings:
                source = self.relations[contribution_input.stage_id]
                present = _present(source, len(keys))
                table = source.select(
                    **{key: normalized_returned_key(source[key], self.provider) for key in keys},
                    **{value: source[contribution_input.column.slot_id]},
                    **{
                        name: source[column].fill_null(False)
                        for (name, _, _), (column, _, _) in zip(
                            checks, contribution_input.checks, strict=True
                        )
                    },
                    __sample_present=ibis.literal(True) if present is None else present,
                )
                tables.append(table if present is None else table.filter(table["__sample_present"]))
            table = tables[0]
            for other in tables[1:]:
                table = table.union(other, distinct=False)
            result = SampleRelation(table, value, checks, binding.zero_when_absent)
            return (
                result
                if forced is None
                else SampleRelation(
                    self.align(forced, (result,), keys), value, checks, binding.zero_when_absent
                )
            )
        if isinstance(node, MemberCount):
            raise SampleReductionUnavailableError(
                "member counts retain their independent complete domain"
            )
        if isinstance(node, MetricExpression):
            operands = tuple(
                self.lower(child, (*path, i), keys, forced) for i, child in enumerate(node.operands)
            )
            table = self.align(
                self.domain(keys, path) if forced is None else forced, operands, keys
            )
            values = {
                (*path, i): NumericValue(
                    table[operand.value],
                    tuple(
                        NumericCheck(table[name], expected, received)
                        for name, expected, received in operand.checks
                    ),
                )
                for i, operand in enumerate(operands)
            }
            item = lower_metric_formula(node, path, values, self.declared)
            value = self.name()
            checks = tuple(
                (self.name("check"), check.expected, check.received) for check in item.checks
            )
            return SampleRelation(
                table.select(
                    *keys,
                    **{value: item.value},
                    **{
                        name: check.violation
                        for (name, _, _), check in zip(checks, item.checks, strict=True)
                    },
                ),
                value,
                checks,
            )
        if not isinstance(node, (PerReduce, PerWeightedMean)):
            raise SampleReductionUnavailableError(
                f"{type(node).__name__} has no native sample relation"
            )
        bindings = self.bindings(path)
        groups = bindings[0].key_groups
        remaining = tuple(group for group in groups if not all(key in keys for key in group))
        calendar = dict(self.program.calendar_samples).get(path)
        if isinstance(node.samples, Grain) and calendar is None:
            raise SampleReductionUnavailableError("calendar sample has no captured grid")
        axes = (
            (calendar,)
            if isinstance(node.samples, Grain)
            else node.samples
            if isinstance(node.samples, tuple)
            else node.samples.axes
        )
        sample_groups = remaining[: len(axes)]
        if len(sample_groups) != len(axes):
            raise SampleReductionUnavailableError(
                "sample identity has no complete physical key groups"
            )
        sample_keys = tuple(key for group in sample_groups for key in group)
        complete_keys = (*keys, *sample_keys)
        children = (node.operand,) if isinstance(node, PerReduce) else (node.value, node.weight)
        parent = self.domain(keys, path) if forced is None else forced
        sample_domain = self.domain(complete_keys, path)
        definition = sample_population(node.samples)
        if (
            (definition is not None or calendar is not None)
            and keys == self.keys
            and forced is None
        ):
            # Explicit internal domains consume the complete enclosing output domain.
            parent = self.domain(keys, ())
            if (
                self.program.calendar is not None
                and self.program.complete_calendar
                and self.program.output_population is None
            ):
                coordinate = self.program.calendar
                calendar_values = time_keys(coordinate)
                slot = self.program.calendar_key
                grid = self.grid((slot,), tuple((value,) for value in calendar_values))
                fields = tuple(key for key in keys if key != slot)
                parent = (
                    distinct_returned_keys(parent.select(*fields), self.provider).cross_join(grid)
                    if fields
                    else grid
                )
            if self.program.output_population is not None and keys:
                selected_domain = self.population(self.program.output_population, keys)
                parent = distinct_returned_keys(
                    parent.union(selected_domain, distinct=False), self.provider
                )
        if definition is not None:
            sample_domain = parent.cross_join(self.population(definition, sample_keys))
        elif calendar is not None:
            parent_groups = groups[: len(groups) - len(remaining)]
            parent_axes = self.enclosing_axes(path)
            enclosing = next(
                (
                    (group[0], axis)
                    for group, axis in reversed(
                        tuple(zip(parent_groups, parent_axes, strict=False))
                    )
                    if isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
                ),
                None,
            )
            if enclosing is None:
                raise SampleReductionUnavailableError(
                    "calendar samples have no bound enclosing coordinate"
                )
            parent_slot, parent_axis = enclosing
            cells = (
                parent_axis.cells
                if isinstance(parent_axis, CalendarCoordinate)
                else ((parent_axis.start, parent_axis.end, parent_axis.start, parent_axis.end),)
            )
            pairs = tuple(
                (cell[0] if isinstance(parent_axis, CalendarCoordinate) else 0, sample[0])
                for cell in cells
                for sample in calendar.cells
                if cell[2] <= sample[2] and sample[3] <= cell[3]
            )
            grid = self.grid((parent_slot, sample_keys[0]), pairs)
            sample_domain = parent.inner_join(
                grid, parent[parent_slot] == grid[parent_slot]
            ).select(*[parent[key] for key in keys], grid[sample_keys[0]])
        implicit = definition is None and calendar is None
        operands = tuple(
            self.lower(child, (*path, i), complete_keys, None if implicit else sample_domain)
            for i, child in enumerate(children)
        )
        table = (
            operands[0].table
            if len(operands) == 1
            else self.align(sample_domain, operands, complete_keys)
        )
        value = table[operands[0].value]
        if not value.type().is_numeric():
            raise SampleReductionUnavailableError("sample reducer retains a nonnumeric carrier")
        if (
            isinstance(node, PerReduce)
            and aggregate_operation(node.reducer) is not None
            and native_aggregate(self.provider, node.reducer, value.type()) is None
        ):
            raise SampleReductionUnavailableError(
                f"{self.provider} has no qualified native {node.reducer} for {value.type()}"
            )
        fold_checks = [item for operand in operands for item in operand.checks]
        metrics: dict[str, ir.Value] = {
            name: table[name].any().fill_null(False) for name, _, _ in fold_checks
        }
        if node.nulls == "error":
            name = self.name("check")
            missing = value.isnull()
            if isinstance(node, PerWeightedMean):
                missing = missing | table[operands[1].value].isnull()
            metrics[name] = missing.any().fill_null(False)
            fold_checks.append(
                (
                    name,
                    "non-NA internal samples",
                    f"{node.reducer if isinstance(node, PerReduce) else 'weighted_mean'}(nulls='error')",
                )
            )
        output = self.name()
        zero = False
        if isinstance(node, PerWeightedMean):
            weight = table[operands[1].value]
            pair = value.notnull() & weight.notnull()
            product = numeric_binary("multiply", NumericValue(value), NumericValue(weight)).value
            numerator = pair.ifelse(product, _null(product)).sum().fill_null(0)
            denominator = pair.ifelse(weight, _null(weight)).sum().fill_null(0)
            reduced = numeric_binary(
                "divide", NumericValue(numerator), NumericValue(denominator)
            ).value
        elif node.reducer == "mean":
            reduced = native_mean(value)
        elif node.reducer == "sum":
            total = value.sum()
            reduced = (table.count() == 0).ifelse(
                ibis.literal(0).cast(total.type()), (value.count() == 0).ifelse(_null(total), total)
            )
            zero = True
        elif node.reducer in ("count", "count_distinct"):
            reduced = value.count() if node.reducer == "count" else value.nunique()
            zero = True
        elif node.reducer in ("first", "last"):
            order = next(
                (
                    group[0]
                    for group, axis in zip(sample_groups, axes, strict=True)
                    if isinstance(axis, CalendarCoordinate)
                    or (isinstance(axis, AxisBinding) and axis.field == node.order_by)
                ),
                None,
            )
            if order is None:
                raise SampleReductionUnavailableError(
                    "ordered samples have no unique bound time key"
                )
            # Window selection retains NULL endpoints; all samples still enter the uniqueness check.
            name = self.name("check")
            metrics[name] = (
                table[order].nunique() + table[order].isnull().any().fill_null(False).ifelse(1, 0)
                != table.count()
            )
            fold_checks.append(
                (name, "one internal sample per time key", "first/last duplicate time keys")
            )
            candidate = table if node.include_null else table.filter(value.notnull())
            rank = self.name("rank")
            ordered = candidate.mutate(
                **{
                    rank: ibis.row_number().over(
                        ibis.window(
                            group_by=[
                                string_bytes(candidate[key], self.provider)
                                if candidate[key].type().is_string()
                                else candidate[key]
                                for key in keys
                            ],
                            order_by=candidate[order].asc(nulls_first=False)
                            if node.reducer == "first"
                            else candidate[order].desc(nulls_first=True),
                        )
                    )
                }
            )
            selected = ordered.filter(ordered[rank] == 0)
            result = (
                table.group_by(*keys).aggregate(**metrics) if keys else table.aggregate(**metrics)
            )
            endpoint = SampleRelation(
                selected.select(*keys, **{output: selected[operands[0].value]}), output
            )
            ordered_result = SampleRelation(
                self.align(result, (endpoint,), keys), output, tuple(fold_checks)
            )
            # An enclosing explicit domain may exclude observed parents or add
            # absent ones whose NULL endpoints must reach the outer reducer.
            return SampleRelation(
                self.align(parent, (ordered_result,), keys), output, tuple(fold_checks)
            )
        elif node.reducer in ("approx_median", "approx_quantile"):
            reduced = value.cast("float64").approx_quantile(node.q if node.q is not None else 0.5)
            if value.type().is_decimal():
                reduced = force_cast(reduced, value.type())
            reduced = (value.count() == 0).ifelse(_null(reduced), reduced)
        else:
            reduced = (
                value.min()
                if node.reducer == "min"
                else value.max()
                if node.reducer == "max"
                else value.quantile(node.q if node.q is not None else 0.5)
            )
            if value.type().is_decimal() and node.reducer in ("median", "quantile"):
                reduced = force_cast(reduced, reduced.type())
            reduced = (value.count() == 0).ifelse(_null(reduced), reduced)
        metrics[output] = reduced
        result = aggregate_returned_keys(table, keys, metrics, self.provider)
        if (
            implicit
            and forced is None
            and all(
                not any(
                    name.startswith("__sample_scope_")
                    or name in ("__sample_present", "__numeric_present")
                    for name in self.relations[binding.stage_id].columns
                )
                for binding in bindings
            )
        ):
            return SampleRelation(result, output, tuple(fold_checks), zero)
        # Preserve parent coordinates even if a selected sample domain is empty.
        completed = self.align(
            parent, (SampleRelation(result, output, tuple(fold_checks), zero),), keys
        )
        return SampleRelation(completed, output, tuple(fold_checks), zero)

    def enclosing_axes(
        self, path: tuple[int, ...]
    ) -> tuple[AxisBinding | CalendarCoordinate | IntervalCoordinate, ...]:
        axes = list(self.program.axes)
        node = self.program.outputs[path[0]][1]
        prefix: tuple[int, ...] = (path[0],)
        for index in path[1:]:
            if isinstance(node, DeclaredMetricRef):
                node = self.declared[node.ref]
            elif isinstance(node, (ContributionFilter, MetricWindow)):
                node = node.operand
            elif isinstance(node, MetricExpression):
                node = node.operands[index]
            elif isinstance(node, (PerReduce, PerWeightedMean)):
                if isinstance(node.samples, Grain):
                    axes.append(dict(self.program.calendar_samples)[prefix])
                else:
                    axes.extend(
                        node.samples if isinstance(node.samples, tuple) else node.samples.axes
                    )
                node = (
                    node.operand
                    if isinstance(node, PerReduce)
                    else node.value
                    if index == 0
                    else node.weight
                )
            prefix = (*prefix, index)
        return tuple(axes)
