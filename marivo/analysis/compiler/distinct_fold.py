"""Exact retained membership folds over the admitted original row and endpoint authority."""

from __future__ import annotations

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.distinct import row_keys, selected_memberships
from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.temporal import bucket
from marivo.analysis.observation.contracts import (
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
)
from marivo.analysis.observation.distinct_contracts import DISTINCT_KEY_COLUMN as KEY
from marivo.analysis.observation.distinct_contracts import membership_part_authorities
from marivo.analysis.observation.fold_contracts import FoldSpecV1, coverage_columns


def fold_memberships(
    parts: tuple[tuple[str, ir.Table], ...],
    original: ir.Table,
    output: ir.Table,
    spec: FoldSpecV1,
) -> tuple[tuple[str, ir.Table], ...]:
    """Keep the final evaluation's members before replacing original coordinates."""
    authorities = membership_part_authorities(spec.input_row)
    if not authorities:
        return ()
    semantics = spec.input_row.family_semantics
    if not isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        raise compilation_error(
            "registered retained Metric membership authority", "invalid fold input"
        )
    original_keys = row_keys(spec.input_row)
    output_keys = row_keys(spec.output_row)
    source_fields = {field.field_id: field.name for field in spec.input_row.schema.columns}
    output_fields = {field.name: field for field in spec.output_row.schema.columns}
    original_time = next(
        (
            field.name
            for field in spec.input_row.schema.columns
            if field.role_id == "time_dimension"
        ),
        None,
    )
    original = original.view()
    aliases = {name: f"__mv_fold_original_{index}" for index, name in enumerate(original_keys)}
    groups = {name: f"__mv_fold_group_{index}" for index, name in enumerate(output_keys)}
    projection: dict[str, ir.Value] = {alias: original[name] for name, alias in aliases.items()}
    for name, alias in groups.items():
        field = output_fields[name]
        if spec.axis == "time" and spec.grain is not None and field.role_id == "time_dimension":
            if original_time is None:
                raise compilation_error(
                    "one original time coordinate", "missing membership fold time"
                )
            projection[alias] = bucket(
                original[original_time], spec.grain, semantics.fold_temporal_snapshot
            )
        else:
            source = source_fields.get(field.field_id)
            if source is None:
                raise compilation_error(
                    "retained output coordinates from original row keys", "unknown fold coordinate"
                )
            projection[alias] = original[source]
    available = dict(parts)
    folded: list[tuple[str, ir.Table]] = []
    for role, authority in authorities:
        incoming = available.get(role)
        if incoming is None:
            raise compilation_error(
                "complete retained membership for the original Metric", "missing fold membership"
            )
        merges = {
            component.time_merge if spec.axis == "time" else component.spatial_merge
            for component in authority.components
        }
        if "blocked" in merges:
            raise compilation_error(
                "an admitted retained membership fold", "blocked membership fold"
            )
        selected = original.select(**projection)
        if spec.axis == "time":
            if merges != {"last"} or not authority.cumulative:
                raise compilation_error(
                    "registered cumulative time-last membership", "unsupported time membership fold"
                )
            endpoint = coverage_columns(authority)[0]
            selected = original.select(**projection, __mv_fold_evaluation_end=original[endpoint])
            latest = selected.__mv_fold_evaluation_end.max().over(
                ibis.window(group_by=[selected[name] for name in groups.values()])
            )
            selected = selected.filter(selected.__mv_fold_evaluation_end.identical_to(latest))
        anchor = selected.view()
        incoming = incoming.view()
        joined = incoming.join(
            anchor,
            [incoming[name].identical_to(anchor[alias]) for name, alias in aliases.items()],
            how="inner" if aliases else "cross",
        )
        folded.append(
            (
                role,
                joined.select(
                    **{name: anchor[alias] for name, alias in groups.items()},
                    **{KEY: incoming[KEY]},
                ).distinct(),
            )
        )
    return selected_memberships(tuple(folded), spec.output_row, output)
