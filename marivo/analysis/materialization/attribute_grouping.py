"""Share one proven-complete Attribute source aggregation across matching roles."""

from __future__ import annotations

from dataclasses import replace

import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.analysis.compiler.attribute_input_lowering import lower_attribute_grouping
from marivo.analysis.compiler.attribute_inputs import attribute_parent_key
from marivo.analysis.compiler.lowered_query import AttributeGroupingBatch, LoweredQuery, LoweredRead
from marivo.analysis.compiler.query_stages import (
    RuleDecision,
    SourceReadStage,
    read_attribute,
    require_attribute,
)
from marivo.analysis.materialization.ordinary_key_admission import stored_key_field
from marivo.analysis.materialization.prepared_sources import PreparedSources
from marivo.datasource.spi import GroupingSetsAggregate, SourceSession


def _stored_attribute_input(
    prepared: PreparedSources, owner: SourceSession, expression: ir.Table
) -> bool:
    # A view or generated column may fail only when the fine classifier is
    # demanded. Its failure must remain after the original parent checks.
    relations = set(expression.op().find(ops.Relation))
    bindings = tuple(
        bound
        for path, bound in prepared.bound.items()
        if prepared.sources.get(path, prepared.source) is owner and bound.relation.op() in relations
    )
    if not bindings or not all(owner.stored_projection.proves(bound) for bound in bindings):
        return False
    # The original parent can finish or fail before the fine tuple is decoded.
    # Prove total transport for the complete key exposed by the grouped read.
    (grouping,) = expression.op().find(GroupingSetsAggregate)
    stored_relations = {bound.relation.op() for bound in bindings}
    return all(
        (field := stored_key_field(value)) is not None
        and field.rel in stored_relations
        and isinstance(field.dtype, (dt.SignedInteger, dt.Boolean, dt.String))
        for value in grouping.groups.values()
    )


def _group_attribute_pair(
    prepared: PreparedSources,
    fine: SourceReadStage,
    parent: SourceReadStage,
    fine_read: LoweredRead,
    parent_read: LoweredRead,
) -> tuple[LoweredRead, LoweredRead] | None:
    owner = prepared.sources.get(fine.source_id, prepared.source)
    dialect = owner.provider.dialect
    if (
        not dialect.supports_grouping_sets
        or fine.source_id != parent.source_id
        or fine.preparation_checks
        or parent.preparation_checks
        or fine_read.population_checks
        or parent_read.population_checks
        or fine.predicates != parent.predicates
        or fine.inputs != parent.inputs
        or fine.parameters != parent.parameters
    ):
        return None
    expression = lower_attribute_grouping(
        fine,
        require_attribute(parent),
        prepared.relations[fine.stage_id],
        dialect,
        parent_has_checks=bool(parent_read.numeric_checks),
    )
    if expression is None or not _stored_attribute_input(prepared, owner, expression):
        return None
    # Permit only schema-preserving layouts. The ordinary lowered stages
    # already own the role-specific types, normalization and check ordering.
    schema = expression.schema()
    if any(
        name not in schema or schema[name].copy(nullable=True) != dtype.copy(nullable=True)
        for read in (fine_read, parent_read)
        for name, dtype in read.expression.schema().items()
    ):
        return None
    batch = AttributeGroupingBatch(require_attribute(fine).kind)
    return (
        replace(fine_read, expression=expression, attribute_grouping=batch),
        replace(parent_read, expression=expression, attribute_grouping=batch),
    )


def group_attribute_sources(
    prepared: PreparedSources, lowered: LoweredQuery, grouping_roots: frozenset[str]
) -> LoweredQuery:
    """Replace compatible read payloads while preserving their logical schedule."""
    plan = lowered.plan
    if not plan.optimization_policy.allows("attribute_grouping_sets"):
        return lowered
    reads = {read.stage_id: read for read in lowered.reads}
    decisions: list[RuleDecision] = []
    for stage in plan.stages:
        if (
            not isinstance(stage, SourceReadStage)
            or stage.source_id not in grouping_roots
            or (layout := read_attribute(stage)) is None
            or layout.scope != "fine"
        ):
            continue
        parent_id = attribute_parent_key(stage.stage_id)
        pair = _group_attribute_pair(
            prepared,
            stage,
            plan.source_read_stage(parent_id),
            reads[stage.stage_id],
            reads[parent_id],
        )
        if pair is None:
            continue
        reads[stage.stage_id], reads[parent_id] = pair
        decisions.append(
            RuleDecision(
                "attribute_grouping_sets",
                "adopted",
                (parent_id, stage.stage_id),
                "Share one exact native aggregation over the same selected contribution rows.",
            )
        )
    return LoweredQuery(
        replace(plan, optimization_decisions=(*plan.optimization_decisions, *decisions)),
        tuple(reads.values()),
    )
