"""Qualify the original-contribution components required by a query consumer."""

from __future__ import annotations

from dataclasses import replace

import ibis

from marivo.analysis.compiler.original import component_layout
from marivo.analysis.compiler.query_stages import (
    ComponentRead,
    QueryPlan,
    SourceReadStage,
    SourceSchema,
    read_components,
    read_reduction,
    require_reduction,
)
from marivo.analysis.core.value_types import ibis_type
from marivo.analysis.model.data_types import DataColumn


def retain_components(plan: QueryPlan, schemas: tuple[SourceSchema, ...]) -> QueryPlan:
    """Add sufficient source inputs only when an original consumer demands them."""
    stages = []
    for stage in plan.stages:
        if (
            not isinstance(stage, SourceReadStage)
            or read_reduction(stage) is None
            or read_components(stage) is not None
        ):
            stages.append(stage)
            continue
        schema = next(
            item
            for item in schemas
            if item.source_id == stage.source_id and item.occurrence == stage.occurrence
        )
        physical = {
            column.column: ibis_type(column.dtype)
            for column in (*schema.keys, *schema.fields, *schema.original_identity)
        }
        table = ibis.table(physical, name="__original_schema")
        identity = tuple(
            (DataColumn(f"__original_id.{index}", column.field, column.dtype, False), column.column)
            for index, column in enumerate(schema.original_identity or schema.keys)
        )
        # Original-contribution consumers require every completed sample.
        reduction = require_reduction(stage)
        layout = component_layout(
            reduction, stage.columns, identity, table, dict(stage.field_columns)
        )
        stages.append(replace(stage, program=ComponentRead(layout)))
    return replace(plan, stages=tuple(stages))
