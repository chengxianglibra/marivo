"""Field-expression and table-join execution primitives for the observe planner.

Internal to ``marivo.analysis.intents`` — extracted from ``observe_planner``.
"""

from __future__ import annotations

from typing import Any, Literal, cast

import ibis.expr.types as ir_types

from marivo.analysis.executor.runner import apply_slice_to_dataset
from marivo.analysis.intents._observe_planner_catalog import (
    _details,
    _entity_id,
    _from_entity_id,
    _relationship_id,
    _to_entity_id,
)
from marivo.analysis.intents._observe_planner_fields import _NoConnectionService
from marivo.analysis.intents._observe_planner_types import (
    RelationshipInfo,
    ResolvedObserveFields,
)
from marivo.analysis.intents.observe_errors import raise_observe_planning_error
from marivo.refs import FieldKind, Ref
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError


def _field_fn(catalog: SemanticCatalog, field_id: str) -> Any:
    resolver = catalog._semantic_resolver(connections=_NoConnectionService())
    field_ref = cast("Ref[FieldKind]", _details(catalog, field_id).ref)
    missing_ref_kinds = {
        ErrorKind.DIMENSION_NOT_FOUND,
        ErrorKind.NOT_FOUND,
    }

    def _resolve(table: Any) -> Any:
        try:
            return _validate_field_expr(resolver.dimension_on(field_ref, table), field_id=field_id)
        except SemanticRuntimeError as exc:
            message = str(exc)
            if exc.kind in {
                ErrorKind.MATERIALIZE_FAILED,
                ErrorKind.BINDING_RESULT_INVALID,
            } and (
                "instead of an ibis expression" in message
                or "must return one Ibis value" in message
            ):
                raise_observe_planning_error(
                    code="field-expr-type-error",
                    message=message,
                    candidates={"field_id": field_id, "actual_type": "unknown"},
                    repair=[],
                )
            if exc.kind in missing_ref_kinds:
                raise_observe_planning_error(
                    code="field-ref-not-found",
                    message=f"Field reference {field_id!r} was not found in observe plan scope.",
                    candidates={"field_id": field_id},
                    repair=[],
                )
            raise

    return _resolve


def _validate_field_expr(value: Any, *, field_id: str) -> Any:
    """Validate that a sidecar callable returned an ibis expression, not a method/function."""
    if isinstance(value, (ir_types.Value, ir_types.Table)):
        return value
    col_name = field_id.rsplit(".", 1)[-1]
    actual_type = type(value).__name__
    raise_observe_planning_error(
        code="field-expr-type-error",
        message=(
            f"Field callable for {field_id!r} returned {actual_type!r} "
            f"instead of an ibis expression. This usually happens when a "
            f"dimension name shadows an ibis Table method (e.g., 'schema', "
            f"'count', 'select'). Use bracket notation in the function body: "
            f'table["{col_name}"] instead of table.{col_name}.'
        ),
        candidates={"field_id": field_id, "actual_type": actual_type},
        repair=[],
    )


def _join_table(
    current_table: Any,
    next_table: Any,
    *,
    catalog: SemanticCatalog,
    relationship: RelationshipInfo,
    current_entity: str,
    extra_predicates: list[Any] | None = None,
    join_type: Literal["left", "inner"] = "left",
) -> tuple[Any, str]:
    if _from_entity_id(relationship) == current_entity:
        next_entity = _to_entity_id(relationship)
        left_fields = relationship.from_keys
        right_fields = relationship.to_keys
    else:
        next_entity = _from_entity_id(relationship)
        left_fields = relationship.to_keys
        right_fields = relationship.from_keys
    predicates = [
        _field_fn(catalog, left_field)(current_table) == _field_fn(catalog, right_field)(next_table)
        for left_field, right_field in zip(left_fields, right_fields, strict=True)
    ]
    if extra_predicates:
        predicates.extend(extra_predicates)
    return current_table.join(next_table, predicates, how=join_type), next_entity


def _aggregate_then_join_pre_aggregate(
    *,
    catalog: SemanticCatalog,
    metric_ir: Any,
    unsafe_dataset_id: str,
    relationship: RelationshipInfo,
    from_dataset: str,
    dataset_fns: dict[str, Any],
    backend: Any,
    resolved_fields: ResolvedObserveFields,
    dataset_ir: Any,
    where_values: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    """Reduce the unsafe-side dataset to the merge grain before joining.

    Merge grain = (join key on unsafe side) ∪ (requested non-root dimensions
    targeting unsafe_dataset_id). Where predicates targeting unsafe_dataset_id
    (``where_values``, keyed by field name) filter the unsafe-side table before
    the distinct reduction and stay out of the grain, so a where slice keeps
    semi-join membership semantics: a root row that has at least one matching
    row on the many side is counted exactly once, even when the predicate
    matches several of its rows. Each grain entry projects through
    ``_field_fn`` so the resulting table keeps the physical column names that
    downstream field bodies expect.
    """
    if _from_entity_id(relationship) == unsafe_dataset_id:
        join_field_ids: tuple[str, ...] = tuple(relationship.from_keys)
    else:
        join_field_ids = tuple(relationship.to_keys)

    grain_field_ids: list[str] = []
    seen_ids: set[str] = set()
    for fid in join_field_ids:
        if fid not in seen_ids:
            grain_field_ids.append(fid)
            seen_ids.add(fid)
    for f in resolved_fields.dimensions:
        if _entity_id(f) != unsafe_dataset_id:
            continue
        field_id = f.ref.path
        if field_id not in seen_ids:
            grain_field_ids.append(field_id)
            seen_ids.add(field_id)

    table = dataset_fns[unsafe_dataset_id](backend)
    if where_values:
        table = apply_slice_to_dataset(table, where_values, dataset_ir=dataset_ir)
    projections: list[Any] = []
    grain_meta_entries: list[dict[str, Any]] = []
    join_field_id_set = set(join_field_ids)
    seen_columns: set[str] = set()
    for fid in grain_field_ids:
        expr = _field_fn(catalog, fid)(table)
        column_name = expr.get_name()
        if column_name in seen_columns:
            continue
        seen_columns.add(column_name)
        projections.append(expr)
        grain_meta_entries.append({"name": column_name, "from_join_key": fid in join_field_id_set})
    pre_aggregated = table.select(*projections).distinct()

    merge_grain_meta = {
        "policy": "aggregate_then_join",
        "unsafe_dataset": unsafe_dataset_id,
        "relationship": _relationship_id(relationship),
        "from_dataset": from_dataset,
        "merge_grain": grain_meta_entries,
        "pre_applied_where": sorted(where_values),
    }
    return pre_aggregated, merge_grain_meta
