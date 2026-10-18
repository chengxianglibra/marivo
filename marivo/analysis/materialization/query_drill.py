"""Schema-only new-field templates keyed by complete original row identity."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import ibis
import ibis.expr.types as ir

from marivo._definition.expressions import MemberCount
from marivo._definition.graph import fingerprint, walk
from marivo._definition.observations import ContributionBinding
from marivo._definition.types import AxisBinding, ExactRef, FieldRead, field_read_key
from marivo.analysis.compiler.observation import FormulaProgram, data_type, ibis_type
from marivo.analysis.compiler.original import DrillSchema, contribution_root
from marivo.analysis.core.data_types import DataColumn
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.query_preparation import prepare_fields
from marivo.datasource.timezone import probe_engine_timezone
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._numeric_lowering import numeric_failure
from marivo.semantic.validator import normalize_target_entity, normalize_target_relationship

if TYPE_CHECKING:
    from marivo.analysis.compiler.graph_plan import SourceRead


@dataclass(frozen=True, slots=True)
class PreparedDrill:
    schemas: tuple[DrillSchema, ...]
    relations: dict[str, ir.Table]


def drill_entities(
    roots: tuple[ExactRef, ...],
    added: tuple[FieldRead, ...],
    state: CompiledSemanticState,
) -> tuple[str, ...]:
    """Discover every root, new field owner, and explicit relationship endpoint."""
    paths = {root.ref.path for root in roots}
    for root in roots:
        for read in _bound_fields(root, added, state):
            paths.add(read.entity.ref.path)
            for step in read.path:
                relation = normalize_target_relationship(state.registry, step.ref.path)
                paths.update((relation.from_entity_ref.path, relation.to_entity_ref.path))
    return tuple(sorted(paths))


def _bound_fields(
    root: ExactRef, added: tuple[FieldRead, ...], state: CompiledSemanticState
) -> tuple[FieldRead, ...]:
    from marivo.analysis.binding.coordinates import bind_read
    from marivo.analysis.observation.relationship_binding import RelationshipResolver
    from marivo.semantic.expressions import _Resolver

    resolver = _Resolver(state)
    paths = RelationshipResolver.build(state.registry)
    return tuple(bind_read(read, root, state.registry, resolver, paths) for read in added)


def _invalid(received: str) -> MaterializationError:
    return MaterializationError(
        expected="a schema-bound new attribute read over selected complete original identities",
        received=received,
        repair="Preserve the original contribution row keys, version coordinates and field paths.",
        stage="query_drill",
        help_target="query.drill",
    )


def _template(
    identifier: str,
    slot: str,
    root: ExactRef,
    identity: tuple[tuple[DataColumn, str], ...],
    added: tuple[FieldRead, ...],
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
    *,
    anchor: AxisBinding | None = None,
) -> tuple[DrillSchema, ir.Table]:
    from marivo._definition.expressions import EntityCount

    contract = normalize_target_entity(state.registry, root.ref.path)
    if not identity or tuple(name for _, name in identity) != (
        contract.primary_key if anchor is not None else contract.version_row_key
    ):
        raise _invalid(
            f"incomplete original row identity for {root.ref.key}: {tuple(name for _, name in identity)!r}"
        )
    parameter = "__marivo_drill_" + fingerprint((identifier, slot))
    columns = tuple(column for column, _ in identity)
    keys = tuple(column.slot_id for column in columns)
    selected = ibis.table(
        {column.slot_id: ibis_type(column.dtype) for column in columns}, name=parameter
    )
    source = entities[root.ref.path].view()
    added = _bound_fields(root, added, state)
    if anchor is not None and contract.version is not None:
        from marivo.analysis.compiler.member_version import version_predicate
        from marivo.semantic.validator import normalize_target_version_selection

        added = tuple(
            replace(read, at=read.at or anchor.at) if read.entity.ref == root.ref else read
            for read in added
        )
        if anchor.at is not None:
            version = normalize_target_version_selection(
                contract, boundary=anchor.at, interpretation="instant"
            )
            source = source.filter(version_predicate(source, contract.version, version))
        elif all(
            read.at is not None and not read.path and read.entity.ref == root.ref for read in added
        ):
            source = source.select(*contract.primary_key).distinct()
        else:
            numeric_failure(
                "the explicit original member or direct attribute version anchor", root.ref.key
            )
    # Restrict the physical root before preparing any newly requested scalar
    # field or relationship. Its checks therefore cover only selected fibers.
    source = source.semi_join(
        selected, [source[name].identical_to(selected[column.slot_id]) for column, name in identity]
    )
    contribution = ContributionBinding(
        (), EntityCount(root, None), root, (), None, added, (), None, None
    )
    prepared = prepare_fields(
        contribution,
        entities,
        state,
        probe_engine_timezone(entities[root.ref.path]._find_backend()),
        root_table=source,
    )
    by_path = dict(prepared.columns)
    fields = tuple(
        DataColumn(
            f"__drill_value.{index}",
            field_read_key(read),
            data_type(prepared.table[by_path[field_read_key(read)]].type()),
            True,
        )
        for index, read in enumerate(added)
    )
    source_id = f"drill:{identifier}:{slot}"
    return (
        DrillSchema(
            source_id,
            slot,
            root,
            identity,
            (*columns, *fields),
            keys,
            (
                *((column.slot_id, name) for column, name in identity),
                *(
                    (column.slot_id, by_path[field_read_key(read)])
                    for column, read in zip(fields, added, strict=True)
                ),
            ),
            prepared.checks,
            parameter,
        ),
        prepared.table,
    )


def prepare_drill(
    identifier: str,
    original: FormulaProgram,
    source_stages: tuple[SourceRead, ...],
    added: tuple[FieldRead, ...],
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
) -> PreparedDrill:
    """Prepare typed attribute templates; the existing parameter stage reads them."""
    stages = {
        stage.component_layout.reduction.value_slot: stage
        for stage in source_stages
        if stage.component_layout is not None
    }
    schemas: dict[str, DrillSchema] = {}
    relations: dict[str, ir.Table] = {}
    for binding in original.values:
        stage = stages.get(binding.column.slot_id)
        if stage is None or stage.component_layout is None:
            raise _invalid(f"missing original source occurrence {binding.stage_id}")
        layout = stage.component_layout
        root = contribution_root(layout.reduction.node)
        schema, table = _template(
            identifier, binding.column.slot_id, root, layout.identity, added, entities, state
        )
        schemas[schema.value_slot] = schema
        relations[schema.source_id] = table
    has_members = any(
        isinstance(node, MemberCount)
        for node in walk(
            (node for _, node in original.outputs), resolve=dict(original.declared).get
        ).order
    )
    if has_members and original.members is not None:
        member = original.members.population.axes[0]
        if not isinstance(member, AxisBinding) or member.role != "identity":
            raise _invalid("member_count does not carry a complete leading member identity")
        contract = normalize_target_entity(state.registry, member.entity.ref.path)
        table = entities[member.entity.ref.path]
        identity = tuple(
            (
                DataColumn(
                    f"__original_member.{index}", name, data_type(table[name].type()), False
                ),
                name,
            )
            for index, name in enumerate(contract.primary_key)
        )
        schema, relation = _template(
            identifier,
            "__original_member_count",
            member.entity,
            identity,
            added,
            entities,
            state,
            anchor=member,
        )
        schemas[schema.value_slot] = schema
        relations[schema.source_id] = relation
    return PreparedDrill(tuple(schemas.values()), relations)
