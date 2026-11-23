"""Native reductions over completed, exactly aligned Metric sample values."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.expressions import (
    BuiltinSampleCoordinate,
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    FieldCalendarSample,
    MemberCount,
    MetricExpression,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.observations import SampleAxis, SampleAxisMatches, sample_key_matches
from marivo._definition.relations import PopulationDependency, sample_population
from marivo._definition.types import AxisBinding, CalendarCoordinate
from marivo._kernel.temporal import Grain
from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.native_formula import normalized_returned_key
from marivo.analysis.compiler.source_predicates import (
    aggregate_returned_keys,
    distinct_returned_keys,
    same_returned_key,
    string_bytes,
)
from marivo.analysis.errors import AnalysisStageUnavailableError
from marivo.analysis.model.data_types import DurationDataType
from marivo.analysis.model.programs import FormulaInput
from marivo.analysis.model.unique_keys import UniqueKeys
from marivo.datasource.spi import aggregate_operation, native_aggregate, query_dialect
from marivo.semantic.spi import (
    NumericCheck,
    NumericValue,
    finish_nullable_sum,
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
    unique_keys: UniqueKeys = field(default_factory=UniqueKeys)

    def __post_init__(self) -> None:
        object.__setattr__(self, "unique_keys", self.unique_keys.project(tuple(self.table.columns)))


@dataclass(frozen=True, slots=True)
class _SampleInputs:
    bindings: tuple[FormulaInput, ...]
    axes: tuple[SampleAxis, ...]
    sample_groups: tuple[tuple[str, ...], ...]
    complete_keys: tuple[str, ...]
    parent: ir.Table
    table: ir.Table
    operands: tuple[SampleRelation, ...]
    implicit: bool


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
        identities: Mapping[str, str] | None = None,
        unique_keys: Mapping[str, UniqueKeys] | None = None,
        shared_relations: frozenset[ops.Relation] = frozenset(),
    ) -> None:
        self.program = program
        self.relations = relations
        self.keys = keys
        self.provider = provider
        self.population = population
        self.identities = identities or {}
        self.unique_keys = unique_keys or {}
        self.shared_relations = shared_relations
        self.declared = dict(program.declared)
        self.sample_matches = dict(program.sample_matches)
        self.serial = 0
        self.cache: dict[
            tuple[
                MetricNode,
                tuple[str, ...],
                tuple[str, ...],
                tuple[tuple[tuple[int, ...], SampleAxisMatches], ...],
                ops.Relation | None,
            ],
            SampleRelation,
        ] = {}

    def name(self, role: str = "value") -> str:
        self.serial += 1
        return f"__sample_{role}_{self.serial}"

    def bindings(self, path: tuple[int, ...]) -> tuple[FormulaInput, ...]:
        return tuple(item for item in self.program.values if item.occurrence[: len(path)] == path)

    def sample_domain(self, keys: tuple[str, ...], path: tuple[int, ...]) -> ir.Table:
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

    def shared_input(
        self, values: tuple[SampleRelation, ...], keys: tuple[str, ...]
    ) -> ir.Table | None:
        """Compose projections of the exact same source rows without domain joins.

        Only projection edges may be crossed. Filters, sample completion and
        aggregates terminate the proof unless they are the shared source itself.
        Coordinate expressions must agree, including returned-key normalization.
        """
        if len(values) < 2:
            return None
        parent: ops.Relation | None = None
        coordinates: tuple[ops.Value, ...] | None = None
        projected: dict[str, ops.Value] = {}
        for value in values:
            relation = value.table.op()
            fields = {
                name: value.table[name].op()
                for name in (*keys, value.value, *(name for name, _, _ in value.checks))
            }
            while relation not in self.shared_relations:
                if not isinstance(relation, ops.Project):
                    return None
                replacements = {
                    ops.Field(relation, name): expression
                    for name, expression in relation.values.items()
                }
                fields = {
                    name: expression.replace(replacements) for name, expression in fields.items()
                }
                relation = relation.parent
            current = tuple(fields[key] for key in keys)
            if parent is not None and (relation != parent or current != coordinates):
                return None
            parent, coordinates = relation, current
            projected.update(fields)
        assert parent is not None
        return parent.to_expr().select(
            **{name: expression.to_expr() for name, expression in projected.items()}
        )

    @staticmethod
    def aligned_keys(
        table: ir.Table, values: tuple[SampleRelation, ...], keys: tuple[str, ...]
    ) -> UniqueKeys:
        """A distinct domain left join retains its key only for unique complete matches."""
        return UniqueKeys(
            tuple(table.columns),
            (keys,) if all(value.unique_keys.proves(keys) for value in values) else (),
        )

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
            tuple(
                (occurrence[len(path) :], matches)
                for occurrence, matches in self.sample_matches.items()
                if occurrence[: len(path)] == path
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
            return self._lower_contribution(path, keys, forced)
        if isinstance(node, MemberCount):
            raise SampleReductionUnavailableError(
                "member counts retain their independent complete domain"
            )
        if isinstance(node, MetricExpression):
            return self._lower_expression(node, path, keys, forced)
        if not isinstance(node, (PerReduce, PerWeightedMean)):
            raise SampleReductionUnavailableError(
                f"{type(node).__name__} has no native sample relation"
            )
        return self._lower_sample(node, path, keys, forced)

    def _lower_contribution(
        self, path: tuple[int, ...], keys: tuple[str, ...], forced: ir.Table | None
    ) -> SampleRelation:
        bindings = self.bindings(path)
        if any(
            name.startswith("predicate_check.")
            and expected != "non-NULL internal calendar sample time"
            for binding in bindings
            for name, expected, _ in binding.checks
        ):
            # Calendar-time assertions retain sample-scope presence. Input
            # predicates can instead remove every sample carrying a check.
            raise SampleReductionUnavailableError(
                "input predicate checks retain their source validation boundary"
            )
        if any(binding.keys != keys for binding in bindings):
            raise SampleReductionUnavailableError(
                "contribution has a different complete sample grain"
            )
        binding = bindings[0]
        if any(isinstance(item.column.dtype, DurationDataType) for item in bindings):
            raise SampleReductionUnavailableError("Duration retains its exact local tick finisher")
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
        facts = (
            self.unique_keys.get(binding.stage_id, UniqueKeys()).project(tuple(table.columns))
            if len(bindings) == 1
            else UniqueKeys(tuple(table.columns))
        )
        result = SampleRelation(table, value, checks, binding.zero_when_absent, facts)
        return (
            result
            if forced is None
            else SampleRelation(
                self.align(forced, (result,), keys),
                value,
                checks,
                binding.zero_when_absent,
                self.aligned_keys(forced, (result,), keys),
            )
        )

    def _lower_expression(
        self,
        node: MetricExpression,
        path: tuple[int, ...],
        keys: tuple[str, ...],
        forced: ir.Table | None,
    ) -> SampleRelation:
        operands = tuple(
            self.lower(child, (*path, i), keys, forced) for i, child in enumerate(node.operands)
        )
        table = self.shared_input(operands, keys) if forced is None else None
        if table is None:
            table = self.align(
                self.sample_domain(keys, path) if forced is None else forced, operands, keys
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
            unique_keys=self.aligned_keys(table, operands, keys),
        )

    def _sample_parent(
        self,
        keys: tuple[str, ...],
        path: tuple[int, ...],
        forced: ir.Table | None,
        definition: PopulationDependency | None,
    ) -> ir.Table:
        parent = self.sample_domain(keys, path) if forced is None else forced
        if definition is not None and keys == self.keys and forced is None:
            # Explicit internal domains consume the complete enclosing output domain.
            parent = self.sample_domain(keys, ())
            if (
                isinstance(self.program.calendar, CalendarCoordinate)
                and not self.program.calendar.actual_only
                and self.program.output_population is None
            ):
                raise SampleReductionUnavailableError(
                    "explicit internal Population over report time remains local"
                )
            if self.program.output_population is not None and keys:
                selected_domain = self.population(self.program.output_population, keys)
                parent = distinct_returned_keys(
                    parent.union(selected_domain, distinct=False), self.provider
                )
        return parent

    def _sample_domain_relation(
        self,
        parent: ir.Table,
        definition: PopulationDependency | None,
        groups: tuple[tuple[str, ...], ...],
        sample_keys: tuple[str, ...],
        complete_keys: tuple[str, ...],
        path: tuple[int, ...],
    ) -> ir.Table:
        if definition is None:
            sample_domain = self.sample_domain(complete_keys, path)
        else:
            selected_samples = self.population(definition, sample_keys)
            matches = sample_key_matches(groups, self.sample_matches.get(path, ()))
            if any(match.interval_keys == () for match in matches):
                sample_domain = parent.cross_join(selected_samples).filter(ibis.literal(False))
            elif matches:
                predicates = []
                for match in matches:
                    outer, inner = parent[match.outer], selected_samples[match.inner]
                    if match.interval_keys is not None:
                        inner = ibis.cases(
                            *(
                                (inner == sample, ibis.literal(report, type="int64"))
                                for report, sample in match.interval_keys
                            ),
                            else_=ibis.null().cast("int64"),
                        )
                    predicates.append(same_returned_key(outer, inner, self.provider))
                sample_domain = parent.inner_join(selected_samples, predicates)
            else:
                sample_domain = parent.cross_join(selected_samples)
        return sample_domain

    def _sample_inputs(
        self,
        node: PerReduce | PerWeightedMean,
        path: tuple[int, ...],
        keys: tuple[str, ...],
        forced: ir.Table | None,
    ) -> _SampleInputs:
        bindings = self.bindings(path)
        groups = bindings[0].key_groups
        remaining = tuple(group for group in groups if not all(key in keys for key in group))
        calendar = dict(self.program.calendar_samples).get(path)
        axes: tuple[SampleAxis, ...]
        if isinstance(node.samples, (Grain, FieldCalendarSample)):
            if calendar is None:
                raise SampleReductionUnavailableError(
                    "calendar sample has no captured contribution selection"
                )
            axes = (calendar,)
        else:
            axes = node.samples if isinstance(node.samples, tuple) else node.samples.axes
        sample_groups = remaining[: len(axes)]
        if len(sample_groups) != len(axes):
            raise SampleReductionUnavailableError(
                "sample identity has no complete physical key groups"
            )
        sample_keys = tuple(key for group in sample_groups for key in group)
        complete_keys = (*keys, *sample_keys)
        children = (node.operand,) if isinstance(node, PerReduce) else (node.value, node.weight)
        definition = sample_population(node.samples)
        parent = self._sample_parent(keys, path, forced, definition)
        sample_domain = self._sample_domain_relation(
            parent, definition, groups, sample_keys, complete_keys, path
        )
        operands = tuple(
            self.lower(
                child, (*path, i), complete_keys, None if definition is None else sample_domain
            )
            for i, child in enumerate(children)
        )
        table = (
            operands[0].table
            if len(operands) == 1
            else self.align(sample_domain, operands, complete_keys)
        )
        return _SampleInputs(
            bindings,
            axes,
            sample_groups,
            complete_keys,
            parent,
            table,
            operands,
            definition is None,
        )

    def _sample_checks(
        self,
        node: PerReduce | PerWeightedMean,
        table: ir.Table,
        operands: tuple[SampleRelation, ...],
    ) -> tuple[dict[str, ir.Value], list[tuple[str, str, str]]]:
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
        return metrics, fold_checks

    def _ordered_sample(
        self,
        node: PerReduce,
        output: str,
        captured: _SampleInputs,
        keys: tuple[str, ...],
        metrics: dict[str, ir.Value],
        fold_checks: list[tuple[str, str, str]],
    ) -> SampleRelation:
        table, operands, parent = captured.table, captured.operands, captured.parent
        axes, sample_groups, complete_keys = (
            captured.axes,
            captured.sample_groups,
            captured.complete_keys,
        )
        value = table[operands[0].value]
        order = next(
            (
                group[0]
                for group, axis in zip(sample_groups, axes, strict=True)
                if isinstance(axis, (CalendarCoordinate, BuiltinSampleCoordinate))
                or (isinstance(axis, AxisBinding) and axis.field == node.order_by)
            ),
            None,
        )
        if order is None:
            raise SampleReductionUnavailableError("ordered samples have no unique bound time key")
        # The input relation must prove parent + order before NULL filtering.
        input_facts = (
            operands[0].unique_keys
            if len(operands) == 1
            else self.aligned_keys(table, operands, complete_keys)
        )
        if not input_facts.proves((*keys, order)):
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
        position, origin = query_dialect(self.provider).row_number()
        ordered = candidate.mutate(
            **{
                rank: position.over(
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
        selected = ordered.filter(ordered[rank] == origin)
        result = (
            table.group_by(*keys).aggregate(**metrics)
            if keys
            else table.aggregate(**metrics)
            if metrics
            else ibis.literal(True).name("__sample_unit").as_table()
        )
        endpoint = SampleRelation(
            selected.select(*keys, **{output: selected[operands[0].value]}),
            output,
            unique_keys=UniqueKeys((*keys, output), (keys,)),
        )
        aligned = self.align(result, (endpoint,), keys)
        ordered_result = SampleRelation(
            aligned,
            output,
            tuple(fold_checks),
            unique_keys=UniqueKeys(tuple(aligned.columns), (keys,)),
        )
        # An enclosing explicit domain may exclude observed parents or add
        # absent ones whose NULL endpoints must reach the outer reducer.
        completed = self.align(parent, (ordered_result,), keys)
        return SampleRelation(
            completed,
            output,
            tuple(fold_checks),
            unique_keys=UniqueKeys(tuple(completed.columns), (keys,)),
        )

    def _lower_sample(
        self,
        node: PerReduce | PerWeightedMean,
        path: tuple[int, ...],
        keys: tuple[str, ...],
        forced: ir.Table | None,
    ) -> SampleRelation:
        captured = self._sample_inputs(node, path, keys, forced)
        parent, table, operands = captured.parent, captured.table, captured.operands
        metrics, fold_checks = self._sample_checks(node, table, operands)
        output = self.name()
        if isinstance(node, PerReduce) and node.reducer in ("first", "last"):
            return self._ordered_sample(node, output, captured, keys, metrics, fold_checks)
        reduced, zero = _reduce_sample_value(node, table, operands)
        metrics[output] = reduced
        result = aggregate_returned_keys(table, keys, metrics, self.provider)
        if (
            captured.implicit
            and forced is None
            and all(
                not any(
                    name.startswith("__sample_scope_")
                    or name in ("__sample_present", "__numeric_present")
                    for name in self.relations[binding.stage_id].columns
                )
                for binding in captured.bindings
            )
        ):
            return SampleRelation(
                result, output, tuple(fold_checks), zero, UniqueKeys(tuple(result.columns), (keys,))
            )
        # Preserve parent coordinates even if a selected sample domain is empty.
        completed = self.align(
            parent,
            (
                SampleRelation(
                    result,
                    output,
                    tuple(fold_checks),
                    zero,
                    UniqueKeys(tuple(result.columns), (keys,)),
                ),
            ),
            keys,
        )
        return SampleRelation(
            completed,
            output,
            tuple(fold_checks),
            zero,
            UniqueKeys(tuple(completed.columns), (keys,)),
        )


def _reduce_sample_value(
    node: PerReduce | PerWeightedMean, table: ir.Table, operands: tuple[SampleRelation, ...]
) -> tuple[ir.Value, bool]:
    value = table[operands[0].value]
    zero = False
    if isinstance(node, PerWeightedMean):
        weight = table[operands[1].value]
        pair = value.notnull() & weight.notnull()
        product = numeric_binary("multiply", NumericValue(value), NumericValue(weight)).value
        numerator = pair.ifelse(product, _null(product)).sum().fill_null(0)
        denominator = pair.ifelse(weight, _null(weight)).sum().fill_null(0)
        reduced = numeric_binary("divide", NumericValue(numerator), NumericValue(denominator)).value
    elif node.reducer == "mean":
        reduced = native_mean(value)
    elif node.reducer == "sum":
        reduced = finish_nullable_sum(value.sum(), table.count(), value.count())
        zero = True
    elif node.reducer in ("count", "count_distinct"):
        reduced = value.count() if node.reducer == "count" else value.nunique()
        zero = True
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
    return reduced, zero
