"""Qualify and lower single-source sample folds within a Formula program."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import ibis
import ibis.expr.types as ir
import pandas as pd

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    FieldCalendarSample,
    MetricExpression,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
    SourceAggregate,
)
from marivo._definition.graph import computation_fingerprint, walk
from marivo._definition.relations import (
    LiteralPopulation,
    PopulationDependency,
    population_keys,
    sample_population,
)
from marivo._definition.types import AxisBinding, IntervalCoordinate
from marivo._kernel.temporal import Grain
from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.lowered_query import LoweredQuery, LoweredRead
from marivo.analysis.compiler.query_program import SourceParameter
from marivo.analysis.compiler.query_stages import (
    LocalCompute,
    NativeRead,
    RuleDecision,
    SourceReadStage,
    read_components,
    read_members,
    read_reduction,
)
from marivo.analysis.compiler.sample_reductions import (
    SampleLowerer,
    SampleReductionUnavailableError,
)
from marivo.analysis.core.value_types import data_type, ibis_type
from marivo.analysis.materialization.native_query_checks import (
    NativeQueryRelation,
    _attach_relation_checks,
)
from marivo.analysis.materialization.native_query_relation import native_query_relation
from marivo.analysis.materialization.prepared_sources import PreparedSources
from marivo.analysis.materialization.query_parameters import instantiate
from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.model.data_types import DataColumn, ScalarDataType
from marivo.analysis.model.programs import FormulaInput
from marivo.datasource.spi import SourceSession
from marivo.semantic.spi import NumericCheck


@dataclass(slots=True)
class _SampleRelation:
    prepared: PreparedSources
    lowered: LoweredQuery
    program: FormulaProgram
    consumer_id: str
    owner: SourceSession
    columns: dict[str, DataColumn]
    active_parameters: dict[SourceSession, dict[str, SourceParameter]]

    def literal(
        self,
        names: tuple[str, ...],
        rows: tuple[tuple[object, ...], ...],
    ) -> ir.Table:
        fields = tuple(self.columns[name] for name in names)
        table = ibis.table(
            {column.slot_id: ibis_type(column.dtype) for column in fields},
            name="__sample_literal",
        )
        frame = pd.DataFrame(rows, columns=list(names), dtype=object)
        return instantiate(
            table,
            "__sample_literal",
            fields,
            StageOutput.from_frame(frame, fields, names),
            provider=self.owner.provider.name,
            source=self.owner,
        )

    def population(self, definition: PopulationDependency, names: tuple[str, ...]) -> ir.Table:
        finite = (
            population_keys(definition)
            if isinstance(definition, LiteralPopulation)
            or any(isinstance(axis, IntervalCoordinate) for axis in definition.axes)
            else None
        )
        if finite is not None:
            rows = tuple(
                tuple(
                    component
                    for axis, value in zip(definition.axes, row, strict=True)
                    for component in (
                        value
                        if isinstance(axis, AxisBinding)
                        and axis.role == "identity"
                        and isinstance(value, tuple)
                        else (value,)
                    )
                )
                for row in finite
            )
            return self.literal(names, rows)
        binding = next(
            (item for item in self.program.populations if item.definition == definition),
            None,
        )
        if binding is None:
            raise SampleReductionUnavailableError(
                "sample Population has no prepared native relation"
            )
        native = native_query_relation(
            self.prepared,
            self.lowered,
            binding.stage_id,
            self.owner,
            shared=True,
            allowed_consumers=frozenset({self.consumer_id}),
        )
        if (
            isinstance(native, str)
            or native.checks
            or any(read.numeric_checks or read.population_checks for read in native.sources)
        ):
            raise SampleReductionUnavailableError(
                "sample Population retains independent checks or a local boundary"
            )
        self.active_parameters[self.owner].update(
            (parameter.stage_id, parameter) for parameter in native.parameters
        )
        return native.expression.select(
            **{
                name: native.expression[source]
                for name, source in zip(names, binding.keys, strict=True)
            }
        )


@dataclass(slots=True)
class SampleCandidates:
    prepared: PreparedSources
    lowered: LoweredQuery
    original: LocalCompute
    program: FormulaProgram
    sources: dict[str, SourceReadStage]
    reads: list[LoweredRead]
    decisions: list[RuleDecision]
    values: list[FormulaInput]
    replacements: list[SourceReadStage] = field(default_factory=list)
    removed: set[str] = field(default_factory=set)
    lowerers: dict[SourceSession, SampleLowerer] = field(default_factory=dict)
    active_parameters: dict[SourceSession, dict[str, SourceParameter]] = field(default_factory=dict)
    cse_keys: dict[str, str] = field(default_factory=dict)

    def visit(self, node: MetricNode, path: tuple[int, ...], keys: tuple[str, ...]) -> None:
        if isinstance(node, DeclaredMetricRef):
            self.visit(dict(self.program.declared)[node.ref], (*path, 0), keys)
            return
        if isinstance(node, (ContributionFilter, MetricWindow)):
            self.visit(node.operand, (*path, 0), keys)
            return
        if isinstance(node, MetricExpression):
            for index, child in enumerate(node.operands):
                self.visit(child, (*path, index), keys)
            return
        if not isinstance(node, (PerReduce, PerWeightedMean)):
            return
        selected = tuple(
            value for value in self.program.values if value.occurrence[: len(path)] == path
        )
        if not selected:
            return
        if any(value.stage_id not in self.sources for value in selected):
            self.decisions.append(
                RuleDecision(
                    "source_sample_reduction",
                    "refused",
                    tuple(value.stage_id for value in selected),
                    "Typed empty or fixed local inputs retain their execution boundary.",
                )
            )
            return
        source_stages = tuple(self.sources[value.stage_id] for value in selected)
        owner = self.prepared.sources.get(source_stages[0].source_id, self.prepared.source)
        try:
            self._qualify_owner(owner, source_stages)
            source, expression, checks, value = self._fold(node, path, keys, source_stages, owner)
            self.replacements.append(source)
            self.reads.append(LoweredRead(source.stage_id, expression, checks))
            if self.lowered.plan.optimization_policy.allows("common_read_cse"):
                closure = walk((node,), resolve=dict(self.program.declared).get)
                if not any(
                    isinstance(item, (SourceAggregate, PerReduce))
                    and item.reducer.startswith("approx_")
                    for item in closure.order
                ):
                    self.cse_keys[source.stage_id] = computation_fingerprint(node)
            self.values = [value for value in self.values if value not in selected]
            self.values.append(
                replace(
                    value,
                    key_groups=tuple(
                        group
                        for group in selected[0].key_groups
                        if all(key in keys for key in group)
                    ),
                )
            )
            self.removed.update(value.stage_id for value in selected)
            self.decisions.append(
                RuleDecision(
                    "source_sample_reduction",
                    "adopted",
                    tuple(value.stage_id for value in selected),
                    "Completed sample Metric, exact domain and qualified reducer execute on one source owner.",
                )
            )
            return
        except SampleReductionUnavailableError as error:
            self.decisions.append(
                RuleDecision(
                    "source_sample_reduction",
                    "refused",
                    tuple(value.stage_id for value in selected),
                    error.received or "sample relation is unavailable",
                )
            )
        # A local explicit parent may supply keys with no source rows.
        # Its child must finish against that completed domain.
        if sample_population(node.samples) is not None:
            return
        remaining = tuple(
            group for group in selected[0].key_groups if not all(key in keys for key in group)
        )
        count = (
            len(node.samples)
            if isinstance(node.samples, tuple)
            else len(node.samples.axes)
            if not isinstance(node.samples, (Grain, FieldCalendarSample))
            else 1
        )
        child_keys = (*keys, *(key for group in remaining[:count] for key in group))
        operands = (node.operand,) if isinstance(node, PerReduce) else (node.value, node.weight)
        for index, child in enumerate(operands):
            self.visit(child, (*path, index), child_keys)

    def _qualify_owner(
        self, owner: SourceSession, source_stages: tuple[SourceReadStage, ...]
    ) -> None:
        prepared = self.prepared
        if not owner.provider.query_policy.sample_fusion:
            raise SampleReductionUnavailableError("provider has no qualified native sample route")
        if any(
            prepared.sources.get(stage.source_id, prepared.source) is not owner
            for stage in source_stages
        ):
            raise SampleReductionUnavailableError(
                "sample formula crosses datasource execution owners"
            )
        if any(
            read_components(stage) is not None or stage.attribute_state or stage.attribute_native
            for stage in source_stages
        ):
            raise SampleReductionUnavailableError(
                "original-contribution consumers retain their complete samples"
            )

    def _lowerer(
        self,
        owner: SourceSession,
        columns: dict[str, DataColumn],
        relations: dict[str, ir.Table],
    ) -> SampleLowerer:
        original, lowered, program, sources = (
            self.original,
            self.lowered,
            self.program,
            self.sources,
        )
        relation = _SampleRelation(
            self.prepared,
            lowered,
            program,
            original.stage_id,
            owner,
            columns,
            self.active_parameters,
        )
        lowerer = self.lowerers.get(owner)
        if lowerer is None:
            lowerer = SampleLowerer(
                program,
                relations,
                original.keys,
                owner.provider.name,
                relation.population,
                {
                    value.stage_id: lowered.plan.source_read_stage(value.stage_id).binding_id
                    or value.stage_id
                    for value in program.values
                    if value.stage_id in sources
                },
                {identifier: source.unique_keys for identifier, source in sources.items()},
                shared_relations=frozenset(
                    relations[identifier].op()
                    for identifier, source in sources.items()
                    if identifier in relations and read_members(source)
                ),
            )
            self.lowerers[owner] = lowerer
        return lowerer

    def _fold(
        self,
        node: PerReduce | PerWeightedMean,
        path: tuple[int, ...],
        keys: tuple[str, ...],
        source_stages: tuple[SourceReadStage, ...],
        owner: SourceSession,
    ) -> tuple[SourceReadStage, ir.Table, tuple[tuple[str, NumericCheck], ...], FormulaInput]:
        prepared, lowered, program = self.prepared, self.lowered, self.program
        original, sources = self.original, self.sources
        relations = {
            read.stage_id: read.expression
            for read in lowered.reads
            if prepared.sources.get(
                lowered.plan.source_read_stage(read.stage_id).source_id, prepared.source
            )
            is owner
        }
        dependencies = {
            parameter.stage_id: parameter
            for stage in source_stages
            for parameter in stage.parameters
        }
        self.active_parameters[owner] = dependencies
        columns = {
            column.slot_id: column
            for value in program.values
            if value.stage_id in sources
            for column in sources[value.stage_id].columns
        }
        lowerer = self._lowerer(owner, columns, relations)
        result = lowerer.lower(node, path, keys)
        identifier = "source:sample:" + original.stage_id + ":" + ".".join(map(str, path))
        slot = "sample:" + ".".join(map(str, path))
        expression = result.table.rename({slot: result.value})
        checks = tuple(
            (name, NumericCheck(expression[name], expected, received))
            for name, expected, received in result.checks
        )

        preparation_sources = tuple(
            replace(
                lowered.read(stage.stage_id),
                numeric_checks=tuple(
                    (name, check)
                    for name, check in lowered.read(stage.stage_id).numeric_checks
                    if name in {name for name, _, _ in stage.preparation_checks}
                ),
            )
            for stage in source_stages
            if stage.preparation_checks
        )
        expression, preparation, checked = _attach_relation_checks(
            expression,
            NativeQueryRelation(expression, frozenset(), preparation_sources),
            lowered.plan,
            numeric=checks,
            prepared=prepared,
        )
        checks = checked
        output_columns = (
            *(columns[key] for key in keys),
            DataColumn(slot, slot, data_type(expression[slot].type()), True),
            *(
                DataColumn(name, name, ScalarDataType("boolean"), False)
                for name, _, _ in result.checks
            ),
        )
        logical_sources = tuple(
            dict.fromkeys(
                member for stage in source_stages for member in (read_members(stage) or (stage,))
            )
        )
        source = SourceReadStage(
            identifier,
            source_stages[0].source_id,
            path,
            output_columns,
            keys,
            NativeRead(
                tuple(
                    reduction
                    for stage in logical_sources
                    if (reduction := read_reduction(stage)) is not None
                )
            ),
            (),
            (),
            None,
            preparation_checks=preparation,
            inputs=tuple(dependencies),
            parameters=tuple(dependencies.values()),
            native_reads=logical_sources,
            unique_keys=result.unique_keys.project(
                tuple(column.slot_id for column in output_columns), {result.value: slot}
            ),
        )
        return (
            source,
            expression,
            checks,
            FormulaInput(
                path,
                identifier,
                output_columns[len(keys)],
                result.zero_when_absent,
                result.checks,
                keys,
            ),
        )
