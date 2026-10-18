"""Schema-bound relational preparation of complete Population programs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo._definition.expressions import ContributionFilter, EntityCount, RowExpression
from marivo._definition.observations import ContributionBinding
from marivo._definition.relations import (
    PopulationAnd,
    PopulationClassify,
    PopulationCondition,
    PopulationContains,
    PopulationDependency,
    PopulationFilter,
    PopulationImage,
    PopulationNot,
    PopulationPreimage,
    PopulationProduct,
    PopulationProgram,
    PopulationProject,
    PopulationSet,
    QueryKeys,
    SourceIdentities,
    condition_populations,
    population_keys,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    CoordinateValue,
    FieldRead,
    LiteralValue,
)
from marivo.analysis.compiler.member_version import version_predicate
from marivo.analysis.core.model import reject
from marivo.refs import ref
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._expression_binding import evaluate_expression_body
from marivo.semantic.validator import normalize_target_entity, normalize_target_version_selection


@dataclass(frozen=True, slots=True)
class PreparedPopulation:
    table: ir.Table
    key_groups: tuple[tuple[str, ...], ...]
    checks: tuple[tuple[str, str, str], ...] = ()
    presence: str | None = None

    @property
    def key_columns(self) -> tuple[str, ...]:
        return tuple(column for group in self.key_groups for column in group)


def _domain_table(prepared: PreparedPopulation) -> ir.Table:
    """Select the key set, retaining the unit tuple for a nonempty zero-axis domain."""
    table = prepared.table
    if prepared.presence is not None:
        table = table.filter(table[prepared.presence].fill_null(False))
    if prepared.key_columns:
        return table.select(*prepared.key_columns)
    return table.select(__population_unit=ibis.literal(True)).distinct()


def population_entities(
    definition: PopulationDependency, state: CompiledSemanticState | None = None
) -> tuple[str, ...]:
    """Return the declared source closure needed for domain schema and reads."""
    entities = {axis.entity.ref.path for axis in definition.axes if isinstance(axis, AxisBinding)}
    if isinstance(definition, PopulationProgram):
        node = definition.root
        if isinstance(node, SourceIdentities):
            entities.add(node.entity.ref.path)
            if node.predicate is not None:
                if not isinstance(node.predicate, RowExpression):
                    for domain in condition_populations(node.predicate):
                        entities.update(population_entities(domain, state))
                if state is not None:
                    contribution = _identity_condition(node, state)
                    for read in (
                        *contribution.reads,
                        *(read for _, read in contribution.population_reads),
                    ):
                        entities.add(read.entity.ref.path)
                        for identity_step in read.path:
                            lookup_relation = state.registry.relationships[identity_step.ref.path]
                            entities.update(
                                (lookup_relation.from_entity, lookup_relation.to_entity)
                            )
        elif isinstance(node, PopulationSet):
            entities.update(population_entities(node.left, state))
            entities.update(population_entities(node.right, state))
        elif isinstance(node, (PopulationProject, PopulationClassify)):
            entities.update(population_entities(node.operand, state))
            if isinstance(node, PopulationClassify):
                for read in node.fields:
                    entities.add(read.entity.ref.path)
                    if state is not None:
                        for field_step in read.path:
                            relation = state.registry.relationships[field_step.ref.path]
                            entities.update((relation.from_entity, relation.to_entity))
        elif isinstance(node, QueryKeys) and state is not None:
            from marivo.analysis.materialization.query_population import query_population_entities

            entities.update(query_population_entities(node, state))
        elif isinstance(node, PopulationFilter):
            entities.update(population_entities(node.operand, state))
            for domain in condition_populations(node.predicate):
                entities.update(population_entities(domain, state))
        elif isinstance(node, (PopulationImage, PopulationPreimage)):
            entities.update(population_entities(node.operand, state))
            for step in node.path:
                entities.update((step.from_entity.ref.path, step.to_entity.ref.path))
            if isinstance(node, PopulationPreimage):
                entities.update(population_entities(node.targets, state))
        elif isinstance(node, PopulationProduct):
            for factor in node.factors:
                entities.update(population_entities(factor, state))
    return tuple(sorted(entities))


def _identity_condition(
    node: SourceIdentities, state: CompiledSemanticState
) -> ContributionBinding:
    from marivo.analysis.binding.observe import bind_observation

    assert node.predicate is not None
    binding = bind_observation(
        (("identity", ContributionFilter(EntityCount(node.entity, None), node.predicate)),),
        {},
        during=None,
        by=(),
        where=None,
        timezone="UTC",
        state=state,
    )
    assert len(binding.contributions) == 1
    return binding.contributions[0]


def _field(read: FieldRead, table: ir.Table, state: CompiledSemanticState) -> ir.Value:
    body = state.sidecar.bodies.get(read.field.ref)
    if body is None:
        reject(
            "a captured field body",
            read.field.ref.key,
            "Load the field's owning model.",
            "population.prepare",
        )
    return evaluate_expression_body(
        catalog_definition_fingerprint=state.definition_fingerprint,
        expression_sidecar=state.sidecar,
        owning_ref=read.field.ref,
        body=body,
        entity_refs=(ref.entity(read.entity.ref.path),),
        aliases=(table,),
    )


def prepare_population(
    definition: PopulationDependency,
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
) -> PreparedPopulation:
    """Build a real Ibis relation without submitting business reads.

    Composite identity axes remain multiple typed columns, with explicit groups
    retaining the one-axis tuple boundary. Literal dtypes come from real bound
    schema, not fabricated numeric/null types.
    """
    groups: list[tuple[str, ...]] = []
    types: dict[str, dt.DataType] = {}
    group: tuple[str, ...]
    for index, axis in enumerate(definition.axes):
        if isinstance(axis, CalendarCoordinate):
            group = (f"__population_{index}_0",)
            types[group[0]] = dt.int64
            groups.append(group)
            continue
        if not isinstance(axis, AxisBinding):
            reject(
                "source field or identity axes",
                "a correspondence axis",
                "Select a complete original endpoint projection before reading a source.",
                "population.prepare",
            )
        table = entities[axis.entity.ref.path]
        if axis.field is None:
            keys = state.registry.entities[axis.entity.ref.path].primary_key
            group = tuple(f"__population_{index}_{component}" for component in range(len(keys)))
            for name, key in zip(group, keys, strict=True):
                types[name] = table[key].type()
        else:
            group = (f"__population_{index}_0",)
            types[group[0]] = _field(
                FieldRead(axis.field, axis.entity, axis.path, axis.at), table, state
            ).type()
        groups.append(group)
    key_groups = tuple(groups)
    columns = tuple(column for group in groups for column in group)
    from marivo._definition.graph import computation_fingerprint

    parameter = entities.get("population-parameter:" + computation_fingerprint(definition))
    if parameter is not None:
        return PreparedPopulation(parameter, key_groups)
    finite = population_keys(definition)
    if finite is not None:
        data: dict[str, list[LiteralValue]] = {column: [] for column in columns}
        for row in finite:
            for axis, group, value in zip(definition.axes, groups, row, strict=True):
                values: tuple[CoordinateValue, ...] = (
                    value
                    if isinstance(axis, AxisBinding)
                    and axis.role == "identity"
                    and isinstance(value, tuple)
                    else (value,)
                )
                if len(values) != len(group):
                    reject(
                        "a complete primary-key tuple",
                        str(value),
                        "Supply every declared primary-key component in order.",
                        "population.prepare",
                    )
                for column, item in zip(group, values, strict=True):
                    if isinstance(item, tuple):
                        reject(
                            "scalar coordinate components",
                            str(item),
                            "Remove nested key tuples.",
                            "population.prepare",
                        )
                    if isinstance(item, bool) and types[column].is_integer():
                        reject(
                            "an integer coordinate",
                            "bool",
                            "Supply a value with the field's real type.",
                            "population.prepare",
                        )
                    data[column].append(item)
        unit = ibis.literal(True).name("__population_unit").as_table()
        if not columns:
            return PreparedPopulation(
                unit if finite else unit.filter(ibis.literal(False)),
                key_groups,
                presence="__population_unit",
            )
        # Literal-only scalar relations are governed inline constants, not a
        # second physical table or a registered in-memory backend resource.
        rows = tuple(
            unit.select(
                **{
                    column: ibis.literal(data[column][index], type=types[column])
                    for column in columns
                }
            )
            for index in range(len(finite))
        )
        if not rows:
            return PreparedPopulation(
                unit.select(
                    **{column: ibis.literal(None, type=types[column]) for column in columns}
                ).filter(ibis.literal(False)),
                key_groups,
            )
        table = ibis.union(rows[0], *rows[1:])
        return PreparedPopulation(table, key_groups)
    assert isinstance(definition, PopulationProgram)
    node = definition.root
    if isinstance(node, QueryKeys):
        from marivo.analysis.materialization.query_population import prepare_query_keys

        return prepare_query_keys(node, entities, state)
    if isinstance(node, PopulationFilter):
        prepared = prepare_population(node.operand, entities, state)
        table = prepared.table
        marks: dict[PopulationDependency, str] = {}
        for index, domain in enumerate(condition_populations(node.predicate)):
            selected = prepare_population(domain, entities, state)
            marker = f"__population_selected_{index}"
            rename = {
                name: f"__population_test_{index}_{offset}"
                for offset, name in enumerate(selected.key_columns)
            }
            allowed = selected.table.select(
                **{new: selected.table[old] for old, new in rename.items()}
            ).mutate(**{marker: ibis.literal(True)})
            pairs = [
                (left, rename[right])
                for axis, group in zip(domain.axes, selected.key_groups, strict=True)
                for left, right in zip(
                    prepared.key_groups[definition.axes.index(axis)], group, strict=True
                )
            ]
            predicates = [table[left].identical_to(allowed[right]) for left, right in pairs]
            table = table.left_join(allowed, predicates).select(table, allowed[marker])
            marks[domain] = marker

        def condition_value(condition: PopulationCondition) -> ir.BooleanValue:
            if isinstance(condition, PopulationContains):
                value = table[marks[condition.population]].fill_null(False)
                assert isinstance(value, ir.BooleanValue)
                return value
            if isinstance(condition, PopulationNot):
                return ~condition_value(condition.operand)
            if isinstance(condition, PopulationAnd):
                return condition_value(condition.left) & condition_value(condition.right)
            return condition_value(condition.left) | condition_value(condition.right)

        return PreparedPopulation(
            table.filter(condition_value(node.predicate)).select(*prepared.key_columns), key_groups
        )
    if isinstance(node, (PopulationImage, PopulationPreimage)):
        from marivo.analysis.materialization.population_relations import (
            prepare_relationship_population,
        )

        return prepare_relationship_population(definition, node, entities, state, key_groups)
    if isinstance(node, SourceIdentities):
        source = entities[node.entity.ref.path].view()
        contract = normalize_target_entity(state.registry, node.entity.ref.path)
        if contract.version is not None:
            if node.at is None:
                reject(
                    "an exact identity version anchor",
                    node.entity.ref.path,
                    "Pass at=session.instant(...).",
                    "population.prepare",
                )
            selection = normalize_target_version_selection(
                contract, boundary=node.at, interpretation="instant"
            )
            source = source.filter(version_predicate(source, contract.version, selection))
        checks: tuple[tuple[str, str, str], ...] = ()
        summary: ir.Table | None = None
        if node.predicate is not None:
            from marivo.analysis.materialization.population_condition import filter_contribution
            from marivo.analysis.materialization.query_preparation import prepare_fields
            from marivo.datasource.timezone import probe_engine_timezone

            contribution = _identity_condition(node, state)
            identity_fields = prepare_fields(
                contribution,
                entities,
                state,
                probe_engine_timezone(source._find_backend()),
                root_table=source,
                defer_population_conditions=True,
            )
            source = identity_fields.table
            if isinstance(node.predicate, RowExpression):
                from marivo.semantic._numeric_lowering import lower_field_predicate

                fields = {key: source[slot] for key, slot in identity_fields.columns}
                lowered = lower_field_predicate(node.predicate, fields)
                if lowered.checks:
                    reject(
                        "a source identity predicate with completed data checks",
                        "strict arithmetic checks",
                        "Evaluate the checked quantity before selecting its identity domain.",
                        "population.prepare",
                    )
                source = source.filter(lowered.value)
            checks = identity_fields.checks
            if checks:
                summary = source.aggregate(
                    **{name: source[name].any().fill_null(False) for name, _, _ in checks}
                )
            if contribution.population_conditions:
                source = filter_contribution(
                    source, contribution, dict(identity_fields.columns), entities, state
                )
        keys = state.registry.entities[node.entity.ref.path].primary_key
        null_key = "__identity_null"
        output = source.select(
            **{slot: source[key] for slot, key in zip(columns, keys, strict=True)},
            **{null_key: ibis.or_(*(source[key].isnull() for key in keys))},
        ).distinct()
        presence = None
        if summary is not None:
            presence = "__identity_selected"
            output = summary.left_join(
                output.mutate(**{presence: ibis.literal(True)}), ibis.literal(True)
            )
        return PreparedPopulation(
            output,
            key_groups,
            (*checks, (null_key, "non-null complete identity keys", node.entity.ref.path)),
            presence,
        )
    if isinstance(node, PopulationSet):
        left = _domain_table(prepare_population(node.left, entities, state))
        right = _domain_table(prepare_population(node.right, entities, state))
        if node.operation == "union":
            table = left.union(right, distinct=True)
        elif node.operation == "intersection":
            table = left.intersect(right, distinct=True)
        else:
            table = left.difference(right, distinct=True)
        return PreparedPopulation(
            table, key_groups, presence="__population_unit" if not columns else None
        )
    if isinstance(node, PopulationProject):
        source = prepare_population(node.operand, entities, state)
        old = tuple(column for index in node.positions for column in source.key_groups[index])
        table = _domain_table(source)
        if not columns:
            return PreparedPopulation(
                table.select(__population_unit=ibis.literal(True)).distinct(),
                key_groups,
                presence="__population_unit",
            )
        return PreparedPopulation(
            table.select(
                **{new: table[old_name] for new, old_name in zip(columns, old, strict=True)}
            ).distinct(),
            key_groups,
        )
    if isinstance(node, PopulationProduct):
        factors = tuple(prepare_population(factor, entities, state) for factor in node.factors)
        tables: list[ir.Table] = []
        offset = 0
        for index, factor in enumerate(factors):
            width = len(factor.key_columns)
            names = columns[offset : offset + width]
            factor_table = _domain_table(factor)
            tables.append(
                factor_table.select(
                    **{
                        name: factor_table[old]
                        for name, old in zip(names, factor.key_columns, strict=True)
                    }
                )
                if width
                else factor_table.select(**{f"__product_unit_{index}": ibis.literal(True)})
            )
            offset += width
        table = tables[0]
        for factor_table in tables[1:]:
            table = table.cross_join(factor_table)
        if not columns:
            return PreparedPopulation(
                table.select(__population_unit=ibis.literal(True)).distinct(),
                key_groups,
                presence="__population_unit",
            )
        return PreparedPopulation(table.select(*columns).distinct(), key_groups)
    if isinstance(node, PopulationClassify):
        from marivo.analysis.materialization.population_relations import prepare_member_fields

        return prepare_member_fields(definition, node, entities, state, key_groups)
    reject(
        "an executable captured population program",
        type(node).__name__,
        "Use a finite or source identity/classification domain.",
        "population.prepare",
    )
