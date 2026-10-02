"""Closed source-private state dispatch through existing compiler boundaries."""

from __future__ import annotations

from collections.abc import Mapping

import ibis.expr.types as ir

from marivo.analysis.compiler import distinct, distribution
from marivo.analysis.compiler.nodes import CompiledValidation, RetainedRelationSpec
from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.operators.contracts import CompareSpecV1

PrivateRelations = tuple[tuple[str, ir.Table], ...]


def selected_private_parts(
    parts: PrivateRelations, row: DatasetRowContract, primary: ir.Table, *, required: bool = True
) -> PrivateRelations:
    return (
        *distinct.selected_memberships(parts, row, primary, required=required),
        *distribution.selected_distributions(parts, row, primary, required=required),
    )


def private_part_specs(
    row: DatasetRowContract, parts: PrivateRelations
) -> tuple[RetainedRelationSpec, ...]:
    return (*distinct.membership_specs(row, parts), *distribution.distribution_specs(row, parts))


def comparison_private_parts(
    comparison: ir.Table,
    current_parts: PrivateRelations,
    baseline_parts: PrivateRelations,
    spec: CompareSpecV1,
) -> PrivateRelations:
    return (
        *distinct.comparison_memberships(comparison, current_parts, baseline_parts, spec),
        *distribution.comparison_distributions(comparison, current_parts, baseline_parts, spec),
    )


def private_part_validations(
    row: DatasetRowContract,
    primary: ir.Table,
    parts: Mapping[str, ir.Table],
    *,
    required: bool = True,
) -> tuple[CompiledValidation, ...]:
    return (
        *distinct.membership_validations(row, primary, parts, required=required),
        *distribution.distribution_validations(row, primary, parts, required=required),
    )
