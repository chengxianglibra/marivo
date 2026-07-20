"""Base observe planner: builds a single-metric BaseObservePlan.

Internal to ``marivo.analysis.intents`` — extracted from ``observe_planner``.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from marivo.analysis.executor.runner import apply_slice_to_dataset
from marivo.analysis.executor.windowing import (
    apply_window_to_dataset,
    datasource_engine_profile,
    datasource_read_timezone,
)
from marivo.analysis.intents._observe_planner_catalog import (
    _entity,
    _entity_id,
    _from_entity_id,
    _input_ref_id,
    _relationship_id,
    _to_entity_id,
    resolve_metric_root,
)
from marivo.analysis.intents._observe_planner_fields import (
    resolve_observe_fields,
    resolved_edge_safety,
    unique_shortest_relationship_path,
)
from marivo.analysis.intents._observe_planner_joins import (
    _aggregate_then_join_pre_aggregate,
    _field_fn,
    _join_table,
    _validate_field_expr,
)
from marivo.analysis.intents._observe_planner_types import (
    BaseObservePlan,
    JoinSafety,
    PlannedDimension,
    PlannedWhere,
    _planned_field,
)
from marivo.analysis.intents._observe_planner_versioning import (
    _resolve_snapshot_versioning,
    _resolve_validity_as_of_predicate,
    _resolve_validity_versioning,
    _root_time_dimension,
)
from marivo.analysis.intents.observe_errors import (
    RepairAction,
    RepairSafety,
    raise_observe_planning_error,
)
from marivo.analysis.intents.sampled_fold import ensure_status_time_dimension_matches
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.ir import SnapshotVersioningIR, ValidityVersioningIR


def plan_base_observe(
    *,
    catalog: SemanticCatalog | None = None,
    session: Any,
    metric_ir: Any,
    dataset_irs: dict[str, Any],
    dataset_fns: dict[str, Any],
    dimensions: list[Any] | None,
    where: dict[Any, Any] | None,
    resolved_window: Any | None,
    time_dimension: str | None,
    allow_unqualified_outside_scope: bool = False,
) -> BaseObservePlan:
    if catalog is None:
        catalog = session.catalog
    root = resolve_metric_root(metric_ir)
    ensure_status_time_dimension_matches(metric_ir, time_dimension)
    if metric_ir.additivity is None:
        raise_observe_planning_error(
            code="missing-additivity",
            message=f"Base metric {metric_ir.semantic_id!r} must declare additivity.",
            candidates={"metric": metric_ir.semantic_id},
            repair=[],
        )
    resolved_fields = resolve_observe_fields(
        catalog,
        metric_ir,
        dimensions=dimensions,
        where=where,
        time_dimension=time_dimension,
        allow_unqualified_outside_scope=allow_unqualified_outside_scope,
    )
    root_time_dimension = _root_time_dimension(
        catalog, root, explicit_time_dimension=resolved_fields.time_dimension
    )
    required_datasets = {root, *metric_ir.entities}
    required_datasets.update(_entity_id(field) for field in resolved_fields.dimensions)
    required_datasets.update(_entity_id(field) for field in resolved_fields.where_fields.values())

    datasource_names = {dataset_irs[dataset_id].datasource_name for dataset_id in required_datasets}
    if len(datasource_names) != 1:
        raise_observe_planning_error(
            code="cross-datasource-plan",
            message="A base observe plan must use one datasource.",
            candidates={"datasources": sorted(datasource_names)},
            repair=[],
        )
    datasource_name = next(iter(datasource_names))
    _, backend = (
        session._connection_runtime.get_or_create(datasource_name),
        session._connection_runtime.get_or_create(datasource_name),
    )
    root_table = dataset_fns[root](backend)
    root_table = apply_window_to_dataset(
        root_table,
        resolved_window,
        dataset_ir=dataset_irs[root],
        report_tz=session.report_tz,
        datasource_read_tz=datasource_read_timezone(
            session._connection_runtime, dataset_irs[root].datasource_name
        ),
        profile=datasource_engine_profile(
            session._connection_runtime, dataset_irs[root].datasource_name
        ),
    )

    planned_where: list[PlannedWhere] = []
    root_where: dict[str, Any] = {}
    joined_where: dict[str, Any] = {}
    raw_root_keys = set(resolved_fields.raw_root_where_keys)
    for raw_key, value in (where or {}).items():
        key = _input_ref_id(raw_key)
        if key in raw_root_keys:
            # Root-phase raw key: forwarded as-is so apply_slice_to_dataset
            # resolves it via the dataset_ir physical-column fallback.
            root_where[key] = value
            continue
        field = resolved_fields.where_fields[key]
        phase: Literal["root", "joined"] = "root" if _entity_id(field) == root else "joined"
        planned_where.append(
            PlannedWhere(original_key=key, field=_planned_field(field), value=value, phase=phase)
        )
        if phase == "root":
            root_where[field.name] = value
        else:
            joined_where[field.name] = value
    if root_where:
        root_table = apply_slice_to_dataset(root_table, root_where, dataset_ir=dataset_irs[root])

    widened_table = root_table
    materialized: dict[str, Any] = {root: widened_table}
    edge_metadata: list[dict[str, Any]] = []
    snapshot_metadata: list[dict[str, Any]] = []
    version_resolutions: list[dict[str, Any]] = []
    plan_warnings: list[dict[str, Any]] = []
    fanout_meta_collector: list[dict[str, Any]] = []
    pre_aggregated_tables: dict[str, Any] = {}
    fanout_join_types: dict[str, Literal["left", "inner"]] = {}
    for dataset_id in sorted(required_datasets - {root}):
        current_dataset = root
        for relationship in unique_shortest_relationship_path(catalog, root, dataset_id):
            safety = resolved_edge_safety(catalog, relationship, from_entity=current_dataset)
            if safety == JoinSafety.ONE_TO_MANY:
                policy = getattr(metric_ir, "fanout_policy", "block")
                if policy == "aggregate_then_join":
                    unsafe_dataset_id = (
                        _to_entity_id(relationship)
                        if _from_entity_id(relationship) == current_dataset
                        else _from_entity_id(relationship)
                    )
                    # Where predicates on the unsafe side must filter before the
                    # distinct reduction; leaving them for the post-join slice
                    # would keep one merge-grain row per matching many-side
                    # value and double-count the root measure.
                    unsafe_where: dict[str, Any] = {}
                    for where_field in resolved_fields.where_fields.values():
                        if (
                            _entity_id(where_field) == unsafe_dataset_id
                            and where_field.name in joined_where
                        ):
                            unsafe_where[where_field.name] = joined_where.pop(where_field.name)
                    pre_table, merge_grain_meta = _aggregate_then_join_pre_aggregate(
                        catalog=catalog,
                        metric_ir=metric_ir,
                        unsafe_dataset_id=unsafe_dataset_id,
                        relationship=relationship,
                        from_dataset=current_dataset,
                        dataset_fns=dataset_fns,
                        backend=backend,
                        resolved_fields=resolved_fields,
                        dataset_ir=dataset_irs[unsafe_dataset_id],
                        where_values=unsafe_where,
                    )
                    pre_aggregated_tables[unsafe_dataset_id] = pre_table
                    # A pre-applied where slice means semi-join membership:
                    # roots without any matching many-side row must drop, so
                    # the reduced table joins inner instead of left. setdefault
                    # keeps the first traversal's type: later traversals of an
                    # already-joined dataset find joined_where drained.
                    fanout_join_types.setdefault(
                        unsafe_dataset_id, "inner" if unsafe_where else "left"
                    )
                    fanout_meta_collector.append(merge_grain_meta)
                    safety = JoinSafety.MANY_TO_ONE
                else:
                    candidate_safe_roots = sorted(
                        {_from_entity_id(relationship), _to_entity_id(relationship)}
                        - {current_dataset}
                    )
                    raise_observe_planning_error(
                        code="unsafe-fanout",
                        message=(
                            f"Traversal through {_relationship_id(relationship)!r} is one-to-many; "
                            "the metric must re-root, remodel the entity key, or opt into "
                            "fanout_policy='aggregate_then_join'."
                        ),
                        candidates={
                            "relationship": _relationship_id(relationship),
                            "safe_roots": candidate_safe_roots,
                            "fanout_policies": ["aggregate_then_join"],
                        },
                        repair=[
                            RepairAction(
                                action="set_metric_root",
                                target=metric_ir.semantic_id,
                                arg="root_entity",
                                value=candidate_safe_roots[0] if candidate_safe_roots else None,
                                safety=RepairSafety.MODELING_DECISION,
                                why=(
                                    "the substantive measure may live on the many side; "
                                    "re-root makes the metric definition match its measure space"
                                ),
                            ),
                            RepairAction(
                                action="set_fanout_policy",
                                target=metric_ir.semantic_id,
                                arg="fanout_policy",
                                value="aggregate_then_join",
                                safety=RepairSafety.MODELING_DECISION,
                                why=(
                                    "keep the current root and reduce the many side to merge "
                                    "grain before join; only correct if the merge grain has "
                                    "business meaning and every measure is additive there"
                                ),
                            ),
                        ],
                    )
            if safety == JoinSafety.UNKNOWN:
                raise_observe_planning_error(
                    code="unknown-join-safety",
                    message=(
                        f"Join safety for {_relationship_id(relationship)!r} cannot be derived "
                        "from dataset keys; planning fails."
                    ),
                    candidates={"relationship": _relationship_id(relationship)},
                    repair=[],
                )
            next_dataset = (
                _to_entity_id(relationship)
                if _from_entity_id(relationship) == current_dataset
                else _from_entity_id(relationship)
            )
            if next_dataset not in materialized:
                next_table = pre_aggregated_tables.get(next_dataset)
                if next_table is None:
                    next_table = dataset_fns[next_dataset](backend)
                next_dataset_meta = _entity(catalog, next_dataset)
                versioning = next_dataset_meta.versioning
                mapping: dict[date, date] | None = None
                if isinstance(versioning, SnapshotVersioningIR):
                    next_table, version_meta, mapping = _resolve_snapshot_versioning(
                        catalog=catalog,
                        session=session,
                        datasource_name=datasource_name,
                        snapshot_dataset_id=next_dataset,
                        snapshot_versioning=versioning,
                        snapshot_table=next_table,
                        snapshot_dataset_ir=dataset_irs[next_dataset],
                        root_table=root_table,
                        root_time_dimension=root_time_dimension,
                        resolved_window=resolved_window,
                    )
                    snapshot_metadata.append(version_meta)
                    version_resolutions.append(version_meta)
                elif isinstance(versioning, ValidityVersioningIR):
                    next_table, version_meta, is_as_of = _resolve_validity_versioning(
                        root_table=root_table,
                        root_time_dimension=root_time_dimension,
                        validity_table=next_table,
                        validity_versioning=versioning,
                        validity_dataset_id=next_dataset,
                        resolved_window=resolved_window,
                    )
                    version_resolutions.append(version_meta)
                    if is_as_of:
                        plan_warnings.append(
                            {"code": "validity_overlap_unverified", "dataset": next_dataset}
                        )
                        validity_predicate = _resolve_validity_as_of_predicate(
                            catalog=catalog,
                            current_table=widened_table,
                            root_time_dimension=root_time_dimension,
                            validity_table=next_table,
                            validity_versioning=versioning,
                            validity_dataset_id=next_dataset,
                        )
                        extra_predicates = [validity_predicate]
                    else:
                        extra_predicates = None
                    pre_join_dataset = current_dataset
                    widened_table, current_dataset = _join_table(
                        widened_table,
                        next_table,
                        catalog=catalog,
                        relationship=relationship,
                        current_entity=current_dataset,
                        extra_predicates=extra_predicates,
                    )
                    materialized[next_dataset] = widened_table
                    edge_metadata.append(
                        {
                            "relationship": _relationship_id(relationship),
                            "from_dataset": pre_join_dataset,
                            "to_dataset": next_dataset,
                            "join_safety": safety.value,
                            "join_type": "left",
                        }
                    )
                    continue
                extra_predicates = None
                if mapping is not None:
                    # mapping is non-None only in as_of_root_time mode, which
                    # requires root_time_dimension to be non-None.
                    assert root_time_dimension is not None
                    anchor_expr = _field_fn(catalog, root_time_dimension.ref.path)(
                        widened_table
                    ).cast("date")
                    extra_predicates = [anchor_expr == next_table.anchor_date]
                pre_join_dataset = current_dataset
                widened_table, current_dataset = _join_table(
                    widened_table,
                    next_table,
                    catalog=catalog,
                    relationship=relationship,
                    current_entity=current_dataset,
                    extra_predicates=extra_predicates,
                    join_type=fanout_join_types.get(next_dataset, "left"),
                )
                materialized[next_dataset] = widened_table
            else:
                pre_join_dataset = current_dataset
                current_dataset = next_dataset
            edge_metadata.append(
                {
                    "relationship": _relationship_id(relationship),
                    "from_dataset": pre_join_dataset,
                    "to_dataset": next_dataset,
                    "join_safety": safety.value,
                    "join_type": fanout_join_types.get(next_dataset, "left"),
                }
            )
    if joined_where:
        widened_table = apply_slice_to_dataset(
            widened_table, joined_where, dataset_ir=dataset_irs[root]
        )

    planned_dimensions = [
        PlannedDimension(field=_planned_field(field), column=field.name)
        for field in resolved_fields.dimensions
    ]
    for planned_dimension in planned_dimensions:
        widened_table = widened_table.mutate(
            **{
                planned_dimension.column: _validate_field_expr(
                    _field_fn(catalog, planned_dimension.field.semantic_id)(widened_table),
                    field_id=planned_dimension.field.semantic_id,
                ).name(planned_dimension.column)
            }
        )
    dataset_tables = dict.fromkeys(metric_ir.entities, widened_table)
    # Populate axes_metadata with a "time" entry when this plan will produce a
    # time-series bucket at execution time.  This lets _execute_derived detect
    # per-component time availability without re-running the planner.
    has_time_axis = (
        root_time_dimension is not None
        and resolved_window is not None
        and getattr(resolved_window, "grain", None) is not None
    )
    axes_meta: dict[str, Any] = {
        dimension.column: {
            "role": "dimension",
            "column": dimension.column,
            "ref": dimension.field.semantic_id,
        }
        for dimension in planned_dimensions
    }
    if has_time_axis:
        _grain_token = (
            resolved_window.grain.to_token()
            if resolved_window is not None and resolved_window.grain is not None
            else None
        )
        axes_meta["time"] = {
            "role": "time",
            "column": "bucket_start",
            "grain": _grain_token,
            "time_dimension": root_time_dimension.name,  # type: ignore[union-attr]
            "ref": root_time_dimension.ref.path,  # type: ignore[union-attr]
        }
    return BaseObservePlan(
        root_entity=root,
        additivity=metric_ir.additivity,
        table=widened_table,
        dataset_tables=dataset_tables,
        dimensions=planned_dimensions,
        where=planned_where,
        axes_metadata=axes_meta,
        lineage_metadata={
            "root_entity": root,
            "additivity": metric_ir.additivity,
            "fanout_policy": metric_ir.fanout_policy,
            "fanouts": fanout_meta_collector,
            "relationships": edge_metadata,
            "snapshots": snapshot_metadata,
            "version_resolutions": version_resolutions,
            "time_fold": metric_ir.time_fold.label() if metric_ir.time_fold is not None else None,
            "status_time_dimension": metric_ir.status_time_dimension,
        },
        warnings=plan_warnings,
        datasource_name=datasource_name,
        status_time_dimension=metric_ir.status_time_dimension,
        time_fold=metric_ir.time_fold,
    )
