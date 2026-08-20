"""Base observe planner: builds a single-metric BaseObservePlan.

Internal to ``marivo.analysis.intents`` — extracted from ``observe_planner``.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo

from marivo._temporal import TimeAxisTimeZoneV1
from marivo.analysis.executor.runner import apply_slice_to_dataset
from marivo.analysis.executor.windowing import (
    apply_window_to_dataset,
    datasource_engine_profile,
    datasource_read_timezone,
    effective_time_context,
    resolve_window_time_field,
)
from marivo.analysis.intents._observe_planner_catalog import (
    _entity,
    _entity_id,
    _fields_for_entity,
    _from_entity_id,
    _input_ref_id,
    _relationship_id,
    _to_entity_id,
    resolve_metric_root,
)
from marivo.analysis.intents._observe_planner_fields import (
    _effective_key,
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
    PlannedPhysicalWhereField,
    PlannedWhere,
    RawWhereKey,
    SampledStatusFoldPlan,
    SnapshotSelectionFoldPlan,
    TemporalFoldPlan,
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
from marivo.refs import ref as ref_factory
from marivo.semantic.catalog import SemanticCatalog, TimeDimensionDetails
from marivo.semantic.ir import SnapshotVersioningIR, ValidityVersioningIR

if TYPE_CHECKING:
    from marivo.analysis.intents._subject_cohort import ResolvedSubjectCohort


def _resolved_time_axis_timezone(
    *,
    dataset_ir: Any,
    table: Any,
    resolved_window: Any | None,
    report_tz: ZoneInfo,
    datasource_read_tz: ZoneInfo,
) -> TimeAxisTimeZoneV1 | None:
    """Project the executor-resolved timezone authority for one observe axis."""
    if resolved_window is None:
        return None
    time_field = resolve_window_time_field(dataset_ir, window=resolved_window)
    time_meta = getattr(time_field, "time_meta", None)
    if time_meta is None:
        return None
    field_expr = time_field.fn(table)
    context = effective_time_context(
        time_meta,
        report_tz=report_tz,
        datasource_read_tz=datasource_read_tz,
        field_expr=field_expr,
    )
    effective_tz = context.effective_column_tz
    if effective_tz is None:
        return None
    if context.actual_field_tz is not None:
        source: Literal["declared", "physical", "datasource_read"] = "physical"
    elif context.declared_tz is not None:
        source = "declared"
    else:
        source = "datasource_read"
    timezone = getattr(effective_tz, "key", None) or str(effective_tz)
    return TimeAxisTimeZoneV1(
        time_dimension=time_field.semantic_id,
        timezone=timezone,
        source=source,
    )


def _plan_temporal_fold(
    *,
    catalog: SemanticCatalog,
    metric_ir: Any,
    root_entity: str,
) -> TemporalFoldPlan | None:
    """Choose the closed temporal execution strategy for one physical metric leaf."""

    time_fold = getattr(metric_ir, "time_fold", None)
    if time_fold is None:
        return None
    status_time_dimension = getattr(metric_ir, "status_time_dimension", None)
    if not isinstance(status_time_dimension, str) or not status_time_dimension:
        raise_observe_planning_error(
            code="status-time-dimension-unresolved",
            message=f"Metric {metric_ir.semantic_id!r} has no resolved status_time_dimension.",
            candidates={"metric": metric_ir.semantic_id},
            repair=[],
        )
    time_details = catalog.require(ref_factory.time_dimension(status_time_dimension)).details()
    if not isinstance(time_details, TimeDimensionDetails):
        raise_observe_planning_error(
            code="status-time-dimension-unresolved",
            message=f"Metric {metric_ir.semantic_id!r} status time dimension is unresolved.",
            candidates={"status_time_dimension": status_time_dimension},
            repair=[],
        )
    if time_details.sample_interval is not None:
        return SampledStatusFoldPlan(
            strategy="sampled_status",
            status_time_dimension=status_time_dimension,
        )

    fold_kind = getattr(time_fold, "kind", None)
    grain_token = time_details.granularity or "day"
    # A sample_interval's unit is minute/hour, so it is only declarable when the
    # status-time grain is at least as fine as hour; day and coarser grains have
    # no legal sample_interval. Snapshot versioning, by contrast, only supports
    # grain='day'.
    interval_declarable = grain_token in {"hour", "minute", "second"}

    root_details = _entity(catalog, root_entity)
    versioning = root_details.versioning
    partition_field = (
        versioning.partition_field if isinstance(versioning, SnapshotVersioningIR) else None
    )
    has_snapshot_identity = partition_field == status_time_dimension

    if not has_snapshot_identity:
        # Deadlock: without a sample_interval, first/last folds require snapshot
        # versioning and every other fold requires a sample_interval. Neither is
        # declared, so no single fold choice escapes. Surface the missing
        # declarations at once rather than ping-ponging between two errors. The
        # available repair paths depend on the status-time grain: snapshot
        # versioning only supports grain='day', while a sample_interval unit is
        # minute/hour and cannot be declared on a day-or-coarser grain.
        local_status_time = status_time_dimension.rsplit(".", 1)[-1]
        versioning_repair = RepairAction(
            action="add_snapshot_versioning",
            target=root_entity,
            arg="versioning",
            value=f"ms.snapshot(partition_field={local_status_time}, grain='day')",
            safety=RepairSafety.MODELING_DECISION,
            why=(
                "binds the root entity's snapshot identity to the status time "
                "dimension so first/last selection becomes legal"
            ),
        )
        if not interval_declarable:
            # day or coarser: no legal sample_interval, so snapshot versioning is
            # the only declaration that breaks a first/last deadlock; a
            # non-selection fold must switch to a selection fold first.
            if fold_kind in {"first", "last"}:
                message = (
                    f"Metric {metric_ir.semantic_id!r} cannot be observed as-is: its status "
                    f"time dimension {status_time_dimension!r} has no sample_interval, and its "
                    f"root entity {root_entity!r} declares no snapshot versioning bound to that "
                    f"dimension. At {grain_token!r} grain no sample_interval can be declared "
                    "(sample_interval units are minute/hour), so first/last selection needs "
                    "snapshot versioning. Declare snapshot versioning bound to the status time "
                    "dimension to break the deadlock."
                )
                repair = [versioning_repair]
            else:
                message = (
                    f"Metric {metric_ir.semantic_id!r} cannot be observed as-is: its status "
                    f"time dimension {status_time_dimension!r} has no sample_interval, and the "
                    f"{fold_kind!r} fold is unsupported without one. At {grain_token!r} grain "
                    "no sample_interval can be declared (sample_interval units are minute/hour), "
                    "so the only path is a first/last selection fold, which requires snapshot "
                    "versioning. Switch to a selection fold and declare snapshot versioning."
                )
                repair = [
                    RepairAction(
                        action="use_first_last_fold",
                        target=metric_ir.semantic_id,
                        arg="fold",
                        value="last",
                        safety=RepairSafety.MODELING_DECISION,
                        why=(
                            "this grain cannot declare a sample_interval, so the only "
                            "observable strategy is snapshot first/last selection"
                        ),
                    ),
                    versioning_repair,
                ]
        else:
            # sub-day: sample_interval is declarable; snapshot versioning only
            # supports grain='day' and only legalizes first/last folds.
            sample_interval_repair = RepairAction(
                action="add_sample_interval",
                target=status_time_dimension,
                arg="sample_interval",
                value=(1, "hour"),
                safety=RepairSafety.MODELING_DECISION,
                why=(
                    "declares a periodic sampling floor so any fold "
                    "(mean/min/max/first/last) can be observed"
                ),
            )
            if fold_kind in {"first", "last"}:
                message = (
                    f"Metric {metric_ir.semantic_id!r} cannot be observed as-is: its status "
                    f"time dimension {status_time_dimension!r} has no sample_interval, and the "
                    f"root entity {root_entity!r} declares no snapshot versioning bound to that "
                    f"dimension. Snapshot versioning only supports grain='day' (this dimension "
                    f"is {grain_token!r}), so a first/last fold has no versioning path here. "
                    "Declare a sample_interval to break the deadlock."
                )
            else:
                message = (
                    f"Metric {metric_ir.semantic_id!r} cannot be observed as-is: its status "
                    f"time dimension {status_time_dimension!r} has no sample_interval, and the "
                    f"{fold_kind!r} fold is unsupported without one. Snapshot versioning only "
                    "legalizes first/last folds, so declare a sample_interval to break the "
                    "deadlock."
                )
            repair = [sample_interval_repair]
        raise_observe_planning_error(
            code="snapshot-fold-deadlock",
            message=message,
            candidates={
                "metric": metric_ir.semantic_id,
                "fold_kind": fold_kind,
                "status_time_dimension": status_time_dimension,
                "root_entity": root_entity,
                "snapshot_partition_field": partition_field,
            },
            repair=repair,
        )

    if fold_kind not in {"first", "last"}:
        repair = [
            RepairAction(
                action="use_first_last_fold",
                target=metric_ir.semantic_id,
                arg="fold",
                value="last",
                safety=RepairSafety.MODELING_DECISION,
                why=(
                    "snapshot versioning is already bound to the status time "
                    "dimension, so first/last selection is legal without a "
                    "sample_interval"
                ),
            ),
        ]
        if interval_declarable:
            repair.append(
                RepairAction(
                    action="add_sample_interval",
                    target=status_time_dimension,
                    arg="sample_interval",
                    value=(1, "hour"),
                    safety=RepairSafety.MODELING_DECISION,
                    why=(
                        f"declares a periodic sampling floor so the {fold_kind} fold "
                        "can be observed"
                    ),
                ),
            )
        raise_observe_planning_error(
            code="unsampled-time-fold-unsupported",
            message=(
                "A status time dimension without sample_interval supports only "
                "snapshot first/last selection."
            ),
            candidates={
                "metric": metric_ir.semantic_id,
                "fold_kind": fold_kind,
                "status_time_dimension": status_time_dimension,
            },
            repair=repair,
        )

    identity_columns = _effective_key(catalog, root_entity)
    if not identity_columns:
        partition_name = partition_field.rsplit(".", 1)[-1] if partition_field else None
        available_identity_columns = sorted(
            field.name
            for field in _fields_for_entity(catalog, root_entity)
            if field.name != partition_name
        )
        raise_observe_planning_error(
            code="snapshot-fold-identity-missing",
            message=(
                "Snapshot fold requires a non-empty business entity identity beyond the "
                "snapshot partition."
            ),
            candidates={
                "root_entity": root_entity,
                "primary_key": list(root_details.primary_key),
                "partition_field": partition_field,
                "available_identity_columns": available_identity_columns,
            },
            repair=[
                RepairAction(
                    action="declare_entity_identity",
                    target=root_entity,
                    arg="primary_key",
                    value="<business_identity_columns>",
                    safety=RepairSafety.MODELING_DECISION,
                    why=(
                        "snapshot selection needs a non-empty business identity to "
                        "deduplicate rows per status-time partition; add one or more "
                        "non-partition columns (see available_identity_columns) to "
                        "primary_key"
                    ),
                ),
            ],
        )
    return SnapshotSelectionFoldPlan(
        strategy="snapshot_selection",
        status_time_dimension=status_time_dimension,
        identity_columns=identity_columns,
        selection=fold_kind,
    )


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
    subject_cohort: ResolvedSubjectCohort | None = None,
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
    temporal_fold = _plan_temporal_fold(
        catalog=catalog,
        metric_ir=metric_ir,
        root_entity=root,
    )
    required_datasets = {root, *metric_ir.entities}
    required_datasets.update(_entity_id(field) for field in resolved_fields.dimensions)
    required_datasets.update(_entity_id(field) for field in resolved_fields.where_fields.values())
    if subject_cohort is not None:
        from marivo.analysis.intents._subject_cohort import validate_metric_cohort_path

        validate_metric_cohort_path(
            catalog=catalog,
            root_entity=root,
            cohort=subject_cohort,
        )
        required_datasets.add(subject_cohort.subject_entity_ref.path)

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
    window_timezone = session.report_tz
    temporal_snapshot = getattr(resolved_window, "temporal_snapshot", None)
    temporal_set_snapshot = getattr(resolved_window, "temporal_set_snapshot", None)
    semantic_grain = getattr(resolved_window, "grain", None)
    semantic_scope = getattr(resolved_window, "semantic_scope", None)
    has_semantic_window = (
        getattr(semantic_grain, "kind", None) == "semantic"
        or getattr(semantic_scope, "calendar", None) is not None
        or getattr(semantic_scope, "temporal_set", None) is not None
    )
    if temporal_set_snapshot is not None and has_semantic_window:
        # A certified occurrence owns the exact local-day boundary used to
        # materialize its half-open window.  Keep that authority separate from
        # any semantic calendar snapshot on the same observe call.
        window_timezone = ZoneInfo(temporal_set_snapshot.boundary_timezone)
    elif temporal_snapshot is not None and has_semantic_window:
        # A certified semantic snapshot owns civil-date membership. The report
        # timezone remains presentation policy and must not move facts across
        # custom fiscal boundaries.
        window_timezone = ZoneInfo(temporal_snapshot.boundary_timezone)
    read_timezone = datasource_read_timezone(
        session._connection_runtime, dataset_irs[root].datasource_name
    )
    root_table = apply_window_to_dataset(
        root_table,
        resolved_window,
        dataset_ir=dataset_irs[root],
        report_tz=window_timezone,
        datasource_read_tz=read_timezone,
        profile=datasource_engine_profile(
            session._connection_runtime, dataset_irs[root].datasource_name
        ),
    )
    time_axis_timezone = _resolved_time_axis_timezone(
        dataset_ir=dataset_irs[root],
        table=root_table,
        resolved_window=resolved_window,
        report_tz=window_timezone,
        datasource_read_tz=read_timezone,
    )

    planned_where: list[PlannedWhere] = []
    root_where: dict[str, Any] = {}
    physical_root_where: dict[str, Any] = {}
    joined_where: dict[str, Any] = {}
    raw_root_keys = set(resolved_fields.raw_root_where_keys)
    for raw_key, value in (where or {}).items():
        if isinstance(raw_key, RawWhereKey):
            key = raw_key.column
            physical_root_where[key] = value
            planned_where.append(
                PlannedWhere(
                    original_key=key,
                    field=PlannedPhysicalWhereField(
                        semantic_id=f"physical:{root}.{key}",
                        name=key,
                        entity=root,
                    ),
                    value=value,
                    phase="root",
                )
            )
            continue
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
    for column, value in physical_root_where.items():
        root_table = root_table.filter(root_table[column] == value)
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
    if subject_cohort is not None:
        from marivo.analysis.intents._subject_cohort import apply_subject_membership

        widened_table = apply_subject_membership(
            catalog=catalog,
            table=widened_table,
            cohort=subject_cohort,
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
    physical_sources: list[dict[str, object]] = []
    for entity_id in sorted(required_datasets):
        entity_details = _entity(catalog, entity_id)
        physical_sources.append(
            {
                "entity": entity_id,
                "datasource": entity_details.datasource.path,
                "source": entity_details.source.to_dict(),
            }
        )
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
            "physical_sources": physical_sources,
            "additivity": metric_ir.additivity,
            "fanout_policy": metric_ir.fanout_policy,
            "fanouts": fanout_meta_collector,
            "relationships": edge_metadata,
            "snapshots": snapshot_metadata,
            "version_resolutions": version_resolutions,
            "cohort": (
                subject_cohort.binding.model_dump(mode="json")
                if subject_cohort is not None
                else None
            ),
            "time_fold": metric_ir.time_fold.label() if metric_ir.time_fold is not None else None,
            "status_time_dimension": metric_ir.status_time_dimension,
        },
        warnings=plan_warnings,
        datasource_name=datasource_name,
        time_axis_timezone=time_axis_timezone,
        status_time_dimension=metric_ir.status_time_dimension,
        time_fold=metric_ir.time_fold,
        temporal_fold=temporal_fold,
    )
