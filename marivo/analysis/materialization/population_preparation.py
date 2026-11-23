"""Schema-bound relational preparation of complete Population programs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import ibis
import ibis.expr.types as ir

from marivo._definition.correspondence import coordinate_position
from marivo._definition.expressions import ContributionFilter, EntityCount
from marivo._definition.graph import computation_fingerprint
from marivo._definition.observations import ContributionBinding
from marivo._definition.population_builders import predicate_fields
from marivo._definition.relations import (
    PopulationClassify,
    PopulationDependency,
    PopulationFilter,
    PopulationImage,
    PopulationPreimage,
    PopulationProduct,
    PopulationProgram,
    PopulationProject,
    PopulationSet,
    SourceIdentities,
    condition_populations,
    population_keys,
)
from marivo._definition.types import (
    AxisBinding,
    EntityIdentityRead,
    FieldRead,
    IntervalCoordinate,
)
from marivo._kernel.validation import is_instance
from marivo.analysis.binding.observe import bind_observation
from marivo.analysis.compiler.lowered_query import PopulationCheck
from marivo.analysis.compiler.member_version import prepare_version_filter
from marivo.analysis.compiler.predicate import lower_condition, population_key
from marivo.analysis.materialization.population_domains import (
    PrepareDomain,
    PreparedPopulation,
    _domain_table,
    _retain_checks,
    scoped_checks,
)
from marivo.analysis.materialization.population_layout import finite_population, population_layout
from marivo.analysis.materialization.population_relations import (
    prepare_member_fields,
    prepare_relationship_population,
)
from marivo.analysis.materialization.prepared_fields import PrepareFields
from marivo.analysis.model.errors import reject
from marivo.analysis.model.time_facts import FrameSourceTime
from marivo.analysis.model.unique_keys import UniqueKeys
from marivo.semantic.spi import (
    CompiledSemanticState,
    normalize_target_entity,
    normalize_target_version_selection,
)


def _include_read_entities(
    entities: set[str],
    reads: Iterable[FieldRead | EntityIdentityRead],
    state: CompiledSemanticState | None,
) -> None:
    for read in reads:
        entities.add(read.entity.ref.path)
        if state is not None:
            for step in read.path:
                relation = state.registry.relationships[step.ref.path]
                entities.update((relation.from_entity, relation.to_entity))


def _include_identity_entities(
    entities: set[str], node: SourceIdentities, state: CompiledSemanticState | None
) -> None:
    entities.add(node.entity.ref.path)
    if node.predicate is not None:
        for domain in condition_populations(node.predicate):
            entities.update(population_entities(domain, state))
        if state is not None:
            contribution = _identity_condition(node, state)
            _include_read_entities(
                entities,
                (*contribution.reads, *(read for _, read in contribution.population_reads)),
                state,
            )


def population_entities(
    definition: PopulationDependency, state: CompiledSemanticState | None = None
) -> tuple[str, ...]:
    """Return the declared source closure needed for domain schema and reads."""
    if not isinstance(definition, PopulationProgram):
        return tuple(
            sorted(
                {axis.entity.ref.path for axis in definition.axes if is_instance(axis, AxisBinding)}
            )
        )
    entities: set[str] = set()
    node = definition.root
    if isinstance(node, SourceIdentities):
        _include_identity_entities(entities, node, state)
    elif isinstance(node, PopulationSet):
        entities.update(population_entities(node.left, state))
        entities.update(population_entities(node.right, state))
    elif isinstance(node, (PopulationProject, PopulationClassify)):
        entities.update(population_entities(node.operand, state))
        if isinstance(node, PopulationClassify):
            subject = node.operand.axes[node.subject]
            assert isinstance(subject, AxisBinding)
            entities.add(subject.entity.ref.path)
            _include_read_entities(entities, node.reads, state)
    elif isinstance(node, PopulationFilter):
        entities.update(population_entities(node.operand, state))
        for domain in condition_populations(node.predicate):
            entities.update(population_entities(domain, state))
    elif isinstance(node, (PopulationImage, PopulationPreimage)):
        entities.update(population_entities(node.operand, state))
        for step in node.path.relationships:
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

    assert node.predicate is not None
    binding = bind_observation(
        (("identity", ContributionFilter(EntityCount(node.entity, None), node.predicate)),),
        {},
        by=(),
        where=None,
        timezone="UTC",
        state=state,
    )
    assert len(binding.contributions) == 1
    return binding.contributions[0]


def _identity_source(
    node: SourceIdentities,
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
    prepare_fields: PrepareFields,
    *,
    source_times: list[FrameSourceTime] | None = None,
    consumption_timezone: str = "UTC",
) -> tuple[ir.Table, tuple[PopulationCheck, ...]]:
    """Prepare the exact selected source and its pre-filter check summary."""
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
        source = prepare_version_filter(
            source, contract.version, selection, state=state, source_times=source_times
        )
    checks: tuple[PopulationCheck, ...] = ()
    if node.predicate is not None:
        contribution = _identity_condition(node, state)
        identity_fields = prepare_fields(
            contribution,
            entities,
            state,
            root_table=source,
            consumption_timezone=consumption_timezone,
        )
        if source_times is not None:
            source_times.extend(identity_fields.source_times)
        source = identity_fields.table
        fields = {key: source[slot] for key, slot in identity_fields.columns}
        lowered = lower_condition(contribution.intrinsic_conditions[0], fields)
        if lowered.checks:
            reject(
                "a source identity predicate with completed data checks",
                "strict arithmetic checks",
                "Evaluate the checked quantity before selecting its identity domain.",
                "population.prepare",
            )
        checks = scoped_checks(source, identity_fields.checks, contract.primary_key)
        source = source.filter(lowered.value)
    return source, checks


def bind_population(
    definition: PopulationDependency,
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
    prepare_fields: PrepareFields,
    *,
    source_times: list[FrameSourceTime] | None = None,
    consumption_timezone: str = "UTC",
    prepared: dict[str, PreparedPopulation] | None = None,
) -> PreparedPopulation:
    """Share prepared relations within one exact source binding and time context."""

    cache = {} if prepared is None else prepared
    key = computation_fingerprint(definition)
    if key not in cache:
        cache[key] = _prepare_population(
            definition, entities, state, prepare_fields, source_times, consumption_timezone, cache
        )
    return cache[key]


def _prepare_population(
    definition: PopulationDependency,
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
    prepare_fields: PrepareFields,
    source_times: list[FrameSourceTime] | None,
    consumption_timezone: str,
    cache: dict[str, PreparedPopulation],
) -> PreparedPopulation:
    """Build a real Ibis relation without submitting business reads.

    Composite identity axes remain multiple typed columns, with explicit groups
    retaining the one-axis tuple boundary. Literal dtypes come from real bound
    schema, not fabricated numeric/null types.
    """

    def prepare_domain(dependency: PopulationDependency) -> PreparedPopulation:
        return bind_population(
            dependency,
            entities,
            state,
            prepare_fields,
            source_times=source_times,
            consumption_timezone=consumption_timezone,
            prepared=cache,
        )

    parameter = entities.get("population-parameter:" + computation_fingerprint(definition))
    layout = population_layout(definition, entities, state)
    key_groups, columns = layout.key_groups, layout.columns
    if parameter is not None:
        return PreparedPopulation(
            parameter, key_groups, presence="__population_unit" if not columns else None
        )
    finite = population_keys(definition)
    if finite is not None:
        return finite_population(definition, finite, layout)
    assert isinstance(definition, PopulationProgram)
    node = definition.root
    if isinstance(node, PopulationFilter):
        return _filter_domain(definition, node, key_groups, columns, prepare_domain)
    if isinstance(node, (PopulationImage, PopulationPreimage)):
        return prepare_relationship_population(
            node,
            entities,
            state,
            key_groups,
            source_times=source_times,
            consumption_timezone=consumption_timezone,
            prepare_domain=prepare_domain,
        )
    if isinstance(node, SourceIdentities):
        source, source_checks = _identity_source(
            node,
            entities,
            state,
            prepare_fields,
            source_times=source_times,
            consumption_timezone=consumption_timezone,
        )
        keys = state.registry.entities[node.entity.ref.path].primary_key
        null_key = "__identity_null"
        # The declared identity/version grain already makes this projection unique.
        output = source.select(
            **{slot: source[key] for slot, key in zip(columns, keys, strict=True)},
            **{null_key: ibis.or_(*(source[key].isnull() for key in keys))},
        )
        return PreparedPopulation(
            output.select(*columns),
            key_groups,
            (
                *source_checks,
                *scoped_checks(
                    output,
                    ((null_key, "non-null complete identity keys", node.entity.ref.path),),
                    columns,
                ),
            ),
            unique_keys=UniqueKeys(columns, (columns,)),
            member_source=source,
        )
    if isinstance(node, PopulationSet):
        return _set_domain(node, key_groups, columns, prepare_domain)
    if isinstance(node, PopulationProject):
        return _project_domain(node, key_groups, columns, prepare_domain)
    if isinstance(node, PopulationProduct):
        return _product_domain(node, key_groups, columns, prepare_domain)
    if isinstance(node, PopulationClassify):
        return prepare_member_fields(
            definition,
            node,
            entities,
            state,
            key_groups,
            source_times=source_times,
            consumption_timezone=consumption_timezone,
            prepare_domain=prepare_domain,
            prepare_fields=prepare_fields,
        )
    reject(
        "an executable captured population program",
        type(node).__name__,
        "Use a finite or source identity/classification domain.",
        "population.prepare",
    )


def _filter_domain(
    definition: PopulationProgram,
    node: PopulationFilter,
    key_groups: tuple[tuple[str, ...], ...],
    columns: tuple[str, ...],
    prepare_domain: PrepareDomain,
) -> PreparedPopulation:
    prepared = prepare_domain(node.operand)
    table = _domain_table(prepared)
    marks: dict[PopulationDependency, str] = {}
    dependencies = [prepared]
    facts = prepared.unique_keys
    for index, domain in enumerate(condition_populations(node.predicate)):
        selected = prepare_domain(domain)
        dependencies.append(selected)
        marker = f"__population_selected_{index}"
        rename = {
            name: f"__population_test_{index}_{offset}"
            for offset, name in enumerate(selected.key_columns)
        }
        allowed = _domain_table(selected).view()
        if rename:
            allowed = allowed.select(**{new: allowed[old] for old, new in rename.items()})
        allowed = allowed.mutate(**{marker: ibis.literal(True)})
        pairs = [
            (
                left,
                rename[right],
                definition.axes[coordinate_position(definition.axes, axis)],
                axis,
            )
            for axis, group in zip(domain.axes, selected.key_groups, strict=True)
            for left, right in zip(
                prepared.key_groups[coordinate_position(definition.axes, axis)],
                group,
                strict=True,
            )
        ]
        predicates: list[ir.BooleanValue] = []
        for left, right, actual, expected in pairs:
            if isinstance(actual, IntervalCoordinate) and isinstance(expected, IntervalCoordinate):
                mapped = tuple(
                    (index, expected.bounds.index(bounds))
                    for index, bounds in enumerate(actual.bounds)
                    if bounds in expected.bounds
                )
                predicates.append(
                    ibis.or_(*((table[left] == a) & (allowed[right] == b) for a, b in mapped))
                    if mapped
                    else ibis.literal(False)
                )
            else:
                predicates.append(table[left].identical_to(allowed[right]))
        comparable_keys = tuple(
            key
            for key in selected.key_columns
            if not allowed[rename[key]].type().is_string()
            and not allowed[rename[key]].type().is_floating()
        )
        if domain.axes and not selected.unique_keys.proves(comparable_keys):
            # Population.where uses native comparisons and may have no
            # at-most-one match proof for the marker join.
            facts = UniqueKeys(prepared.key_columns)
        table = table.left_join(allowed, predicates or ibis.literal(True)).select(
            table, allowed[marker]
        )
        marks[domain] = marker

    markers = {
        population_key(domain): table[marker].fill_null(False) for domain, marker in marks.items()
    }
    fields = {
        name: table[prepared.key_groups[position][0]]
        for name, position in predicate_fields(definition.axes, node.predicate)
    }
    lowered = lower_condition(node.predicate, {**fields, **markers})
    if lowered.checks:
        presence = "__population_predicate_selected"
        checks = tuple(
            (f"__population_predicate_check_{i}", check.expected, check.received)
            for i, check in enumerate(lowered.checks)
        )
        table = table.select(
            *prepared.key_columns,
            **{presence: lowered.value.fill_null(False)},
            **{
                name: check.violation
                for (name, _, _), check in zip(checks, lowered.checks, strict=True)
            },
        )
        return _retain_checks(
            PreparedPopulation(
                table,
                key_groups,
                scoped_checks(table, checks, columns),
                presence,
                facts,
            ),
            *dependencies,
        )
    filtered = table.filter(lowered.value)
    result = PreparedPopulation(
        filtered.select(*prepared.key_columns)
        if columns
        else filtered.select(__population_unit=ibis.literal(True)).distinct(),
        key_groups,
        presence="__population_unit" if not columns else None,
        unique_keys=facts,
    )
    return _retain_checks(result, *dependencies)


def _set_domain(
    node: PopulationSet,
    key_groups: tuple[tuple[str, ...], ...],
    columns: tuple[str, ...],
    prepare_domain: PrepareDomain,
) -> PreparedPopulation:
    left_operand = prepare_domain(node.left)
    right_operand = prepare_domain(node.right)
    left, right = _domain_table(left_operand), _domain_table(right_operand)
    if node.operation == "union":
        table = left.union(right, distinct=True)
    elif node.operation == "intersection":
        table = left.intersect(right, distinct=True)
    else:
        table = left.difference(right, distinct=True)
    return _retain_checks(
        PreparedPopulation(
            table,
            key_groups,
            presence="__population_unit" if not columns else None,
            unique_keys=UniqueKeys(columns, (columns,)),
        ),
        left_operand,
        right_operand,
    )


def _project_domain(
    node: PopulationProject,
    key_groups: tuple[tuple[str, ...], ...],
    columns: tuple[str, ...],
    prepare_domain: PrepareDomain,
) -> PreparedPopulation:
    source = prepare_domain(node.operand)
    old = tuple(column for index in node.positions for column in source.key_groups[index])
    table = _domain_table(source)
    if not columns:
        return _retain_checks(
            PreparedPopulation(
                table.select(__population_unit=ibis.literal(True)).distinct(),
                key_groups,
                presence="__population_unit",
            ),
            source,
        )
    projected_columns = {new: table[old_name] for new, old_name in zip(columns, old, strict=True)}
    for position, interval_keys in node.interval_keys:
        (name,) = key_groups[position]
        projected_columns[name] = (
            projected_columns[name]
            .cases(*((index, key) for index, key in enumerate(interval_keys)))
            .cast("int64")
        )
    projected = table.select(**projected_columns)
    remapped = {key_groups[position][0] for position, _ in node.interval_keys}
    mapping = {
        prior: name for prior, name in zip(old, columns, strict=True) if name not in remapped
    }
    facts = source.unique_keys.project(tuple(mapping)).project(columns, mapping)
    if len(node.positions) != len(source.key_groups) and not facts.proves(columns):
        projected = projected.distinct()
        facts = facts.add(columns)
    return _retain_checks(PreparedPopulation(projected, key_groups, unique_keys=facts), source)


def _product_domain(
    node: PopulationProduct,
    key_groups: tuple[tuple[str, ...], ...],
    columns: tuple[str, ...],
    prepare_domain: PrepareDomain,
) -> PreparedPopulation:
    factors = tuple(prepare_domain(factor) for factor in node.factors)
    tables: list[ir.Table] = []
    candidates: tuple[str, ...] = ()
    proven = True
    offset = 0
    for index, factor in enumerate(factors):
        width = len(factor.key_columns)
        names = columns[offset : offset + width]
        factor_table = _domain_table(factor)
        facts = factor.unique_keys.project(names, dict(zip(factor.key_columns, names, strict=True)))
        if facts.keys:
            candidates = (*candidates, *facts.keys[0])
        elif width:
            proven = False
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
        return _retain_checks(
            PreparedPopulation(
                table.select(__population_unit=ibis.literal(True)),
                key_groups,
                presence="__population_unit",
            ),
            *factors,
        )
    # Each factor is a set, and all factor keys survive the Cartesian product.
    return _retain_checks(
        PreparedPopulation(
            table.select(*columns),
            key_groups,
            unique_keys=UniqueKeys(columns, (candidates,) if proven else ()),
        ),
        *factors,
    )
