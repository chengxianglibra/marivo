"""Exact source-only membership relations across immutable row boundaries."""

from __future__ import annotations

from collections.abc import Mapping

import ibis.expr.types as ir

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.nodes import CompiledValidation, RetainedRelationSpec
from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.observation.distinct_contracts import DISTINCT_KEY_COLUMN as KEY
from marivo.analysis.observation.distinct_contracts import (
    membership_endpoint_name,
    membership_part_authorities,
)
from marivo.analysis.operators.contracts import CompareSpecV1

MembershipRelations = tuple[tuple[str, ir.Table], ...]


def row_keys(row: DatasetRowContract) -> tuple[str, ...]:
    return tuple(field.name for field in row.schema.columns if field.field_id in row.key_field_ids)


def selected_memberships(
    parts: MembershipRelations, row: DatasetRowContract, primary: ir.Table, *, required: bool = True
) -> MembershipRelations:
    """Select complete membership support by the exact current primary coordinates."""
    roles = {role for role, _ in membership_part_authorities(row)}
    keys = row_keys(row)
    output: list[tuple[str, ir.Table]] = []
    for role, relation in parts:
        if role not in roles:
            continue
        selected = primary.select(*keys).distinct().view() if keys else primary
        if keys:
            relation = relation.join(
                selected,
                [relation[name].identical_to(selected[name]) for name in keys],
                how="semi",
            )
        else:
            relation = relation.filter(selected.count() > 0)
        output.append((role, relation.select(*keys, KEY).distinct()))
    if required and {role for role, _ in output} != roles:
        raise compilation_error(
            "complete exact source membership roles", "missing membership state"
        )
    return tuple(output)


def membership_specs(
    row: DatasetRowContract, parts: MembershipRelations
) -> tuple[RetainedRelationSpec, ...]:
    available = dict(parts)
    result: list[RetainedRelationSpec] = []
    for role, _authority in membership_part_authorities(row):
        relation = available.get(role)
        if relation is None:
            raise compilation_error(
                "registered source membership relation", "missing retained basis"
            )
        result.append(
            RetainedRelationSpec(
                role,
                "delta.distinct_membership"
                if row.shape_id.family_id == "delta"
                else "metric.distinct_membership",
                1,
                relation,
            )
        )
    return tuple(result)


def comparison_memberships(
    comparison: ir.Table,
    current_parts: MembershipRelations,
    baseline_parts: MembershipRelations,
    spec: CompareSpecV1,
) -> MembershipRelations:
    """Consume the comparison's exact side-time and output-coordinate mapping."""
    result: list[tuple[str, ir.Table]] = []
    output = {field.field_id: field.name for field in spec.output_row.schema.columns}
    output_keys = row_keys(spec.output_row)
    for side, row, parts, absent in (
        ("current", spec.current_row, current_parts, "baseline_only"),
        ("baseline", spec.baseline_row, baseline_parts, "current_only"),
    ):
        authorities = membership_part_authorities(row)
        if not authorities:
            continue
        if len(authorities) != 1:
            raise compilation_error("one exact comparison membership basis", "invalid Metric arity")
        relation = dict(parts).get(authorities[0][0])
        if relation is None:
            raise compilation_error("complete comparison side membership", "missing side basis")
        coordinates = {
            field.name: f"{side}_time"
            if field.role_id == "time_dimension"
            else output[field.field_id]
            for field in row.schema.columns
            if field.field_id in row.key_field_ids
        }
        selected = comparison.filter(comparison.coordinate_presence != absent).view()
        joined = relation.join(
            selected,
            [relation[name].identical_to(selected[target]) for name, target in coordinates.items()],
            how="inner" if coordinates else "cross",
        )
        projection = {name: selected[name] for name in output_keys}
        projection[KEY] = relation[KEY]
        result.append((f"delta_membership.{side}", joined.select(**projection).distinct()))
    return tuple(result)


def membership_validations(
    row: DatasetRowContract,
    primary: ir.Table,
    parts: Mapping[str, ir.Table],
    *,
    required: bool = True,
) -> tuple[CompiledValidation, ...]:
    """Validate exact source relations using only scalar violation outputs."""
    checks: list[CompiledValidation] = []
    keys = row_keys(row)
    primary = primary.view()

    def assertion(name: str, invalid: ir.Table) -> None:
        checks.append(CompiledValidation(name, invalid.aggregate(violations=invalid.count())))

    for role, _authority in membership_part_authorities(row):
        relation = parts.get(role)
        if relation is None:
            if not required:
                continue
            raise compilation_error("complete retained membership basis", "missing membership role")
        relation = relation.view()
        if tuple(relation.columns) != (*keys, KEY):
            raise compilation_error(
                "exact membership coordinate and key schema", "invalid membership schema"
            )
        assertion(f"{role}.key_non_null", relation.filter(relation[KEY].isnull()))
        counts = relation.group_by([*keys, KEY]).aggregate(__mv_memberships=relation.count())
        assertion(f"{role}.pair_unique", counts.filter(counts.__mv_memberships != 1))
        if keys:
            anchor = primary.select(*keys).view()
            assertion(
                f"{role}.coordinate_support",
                relation.join(
                    anchor, [relation[name].identical_to(anchor[name]) for name in keys], how="anti"
                ),
            )
            grouped = relation.group_by(list(keys)).aggregate(__mv_members=relation.count()).view()
            joined = primary.join(
                grouped, [primary[name].identical_to(grouped[name]) for name in keys], how="left"
            )
        else:
            grouped = relation.aggregate(__mv_members=relation.count()).view()
            joined = primary.cross_join(grouped)
        endpoint = membership_endpoint_name(row, role)
        assertion(
            f"{role}.endpoint_reproduction",
            joined.filter(~primary[endpoint].identical_to(grouped.__mv_members.fill_null(0))),
        )
    return tuple(checks)
