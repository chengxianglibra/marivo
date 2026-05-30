"""Materialize a semantic metric into a MetricFrame."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from marivo.analysis.errors import (
    AmbiguousDimensionError,
    CrossBackendMetricError,
    DimensionAcrossDatasetsError,
    DimensionFieldNotFoundError,
    MetricNotFoundError,
    MetricShapeUnsupportedError,
    SemanticKindMismatchError,
)
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis.evidence.types import Subject
from marivo.analysis.executor.runner import (
    apply_slice_to_dataset,
    apply_time_series_bucket,
    apply_window_to_dataset,
    execute,
    normalize_slice_for_storage,
    resolve_window_time_field,
)
from marivo.analysis.frames.component import ComponentFrame, ComponentFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents._shape import SemanticShape, observe_output_shape
from marivo.analysis.intents._types import SliceValue
from marivo.analysis.intents._validate import raise_first, validate_observe
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.refs import DimensionRef, MetricRef
from marivo.analysis.session.attach import active as session_active
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.analysis.session.persistence import (
    read_session_meta,
    write_frame_to_disk,
    write_job_record,
    write_session_meta,
)
from marivo.analysis.windows.spec import (
    AbsoluteWindow,
    TimeGrain,
    TimeScopeInput,
    dump_window,
    make_absolute_window,
    normalize_timescope_input,
)
from marivo.semantic.authoring import (
    _BinOpSentinel,
    _ComponentSentinel,
    _UnaryNegSentinel,
)

# ---------------------------------------------------------------------------
# v1.1 -> runner adapter types
# ---------------------------------------------------------------------------
# The runner.py functions expect old-style IR objects with attributes like
# ``fn``, ``fields``, ``datasource_name``, ``is_time``, ``time_meta``.
# The new v1.1 semantic stores callables in a sidecar map and uses
# different IR dataclass shapes.  These adapter classes bridge the gap
# without modifying runner.py.


class _TimeFieldMetaAdapter:
    """Adapter that mimics the old TimeFieldMeta for runner.py."""

    def __init__(
        self,
        data_type: str,
        granularity: str,
        format: str | None = None,
        required_prefix: str | None = None,
        timezone: str | None = None,
    ) -> None:
        self.data_type = data_type
        self.granularity = granularity
        self.format = format
        self.required_prefix = required_prefix
        self.timezone = timezone


class _FieldIRAdapter:
    """Adapter that mimics the old FieldIR for runner.py."""

    def __init__(
        self,
        semantic_id: str,
        name: str,
        dataset_name: str,
        fn: Callable[..., Any],
        *,
        is_time: bool = False,
        time_meta: _TimeFieldMetaAdapter | None = None,
    ) -> None:
        self.semantic_id = semantic_id
        self.name = name
        self.dataset_name = dataset_name
        self.fn = fn
        self.is_time = is_time
        self.time_meta = time_meta


class _DatasetIRAdapter:
    """Adapter that mimics the old DatasetIR for runner.py."""

    def __init__(
        self,
        name: str,
        fn: Callable[..., Any],
        datasource_name: str,
        fields: dict[str, _FieldIRAdapter],
    ) -> None:
        self.name = name
        self.fn = fn
        self.datasource_name = datasource_name
        self.fields = fields


def _build_dataset_adapter(
    sp: Any,
    dataset_ir: Any,
) -> _DatasetIRAdapter:
    """Build a _DatasetIRAdapter from a v1.1 DatasetIR + sidecar."""
    sidecar = sp.sidecar()
    dataset_fn = sidecar.get(dataset_ir.semantic_id) if sidecar else None

    def _default_fn(backend: Any) -> Any:
        raise RuntimeError(f"No sidecar callable for dataset {dataset_ir.semantic_id!r}")

    fn = dataset_fn if dataset_fn is not None else _default_fn

    # Build field adapters for this dataset
    field_adapters: dict[str, _FieldIRAdapter] = {}
    for field_ir in sp.list_fields(dataset=dataset_ir.semantic_id):
        field_fn = sidecar.get(field_ir.semantic_id) if sidecar else None
        _captured_field_sid = field_ir.semantic_id

        def _default_field_fn(table: Any, *, _sid: str = _captured_field_sid) -> Any:
            raise RuntimeError(f"No sidecar callable for field {_sid!r}")

        adapter = _FieldIRAdapter(
            semantic_id=field_ir.semantic_id,
            name=field_ir.name,
            dataset_name=dataset_ir.name,
            fn=field_fn if field_fn is not None else _default_field_fn,
            is_time=False,
        )
        field_adapters[field_ir.name] = adapter

    # Add time fields
    for tf_ir in sp.list_time_fields(dataset=dataset_ir.semantic_id):
        tf_fn = sidecar.get(tf_ir.semantic_id) if sidecar else None
        _captured_tf_sid = tf_ir.semantic_id

        def _default_tf_fn(table: Any, *, _sid: str = _captured_tf_sid) -> Any:
            raise RuntimeError(f"No sidecar callable for time_field {_sid!r}")

        time_meta = _TimeFieldMetaAdapter(
            data_type=tf_ir.data_type or "date",
            granularity=tf_ir.granularity or "day",
            format=tf_ir.format,
            required_prefix=tf_ir.required_prefix,
            timezone=tf_ir.timezone,
        )
        adapter = _FieldIRAdapter(
            semantic_id=tf_ir.semantic_id,
            name=tf_ir.name,
            dataset_name=dataset_ir.name,
            fn=tf_fn if tf_fn is not None else _default_tf_fn,
            is_time=True,
            time_meta=time_meta,
        )
        field_adapters[tf_ir.name] = adapter

    return _DatasetIRAdapter(
        name=dataset_ir.name,
        fn=fn,
        datasource_name=dataset_ir.datasource,
        fields=field_adapters,
    )


# ---------------------------------------------------------------------------
# Observe intent
# ---------------------------------------------------------------------------


def _gen_ref(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def _params_digest(params: dict[str, Any]) -> str:
    body = json.dumps(params, sort_keys=True, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


# ---------------------------------------------------------------------------
# Component-aware decomposition helpers
# ---------------------------------------------------------------------------

_COMPONENT_AWARE_DECOMPOSITIONS = {"ratio", "weighted_average"}


def _is_component_aware_decomposition(metric_ir: Any) -> bool:
    decomposition = getattr(metric_ir, "decomposition", None)
    kind = getattr(decomposition, "kind", None)
    return isinstance(kind, str) and kind in _COMPONENT_AWARE_DECOMPOSITIONS


def _decomposition_payload(metric_ir: Any) -> dict[str, Any] | None:
    if not _is_component_aware_decomposition(metric_ir):
        return None
    return {
        "kind": metric_ir.decomposition.kind,
        "components": dict(metric_ir.decomposition.components),
    }


def _component_parent_columns(metric_ir: Any) -> list[str]:
    kind = metric_ir.decomposition.kind
    if kind == "ratio":
        return ["numerator", "denominator"]
    if kind == "weighted_average":
        return ["numerator", "weight"]
    return []


def _component_metric_columns(metric_ir: Any) -> dict[str, str]:
    return {
        role: component_ref.rsplit(".", 1)[1]
        for role, component_ref in metric_ir.decomposition.components.items()
    }


def _component_frame_df(
    *,
    raw_df: Any,
    metric_ir: Any,
    axes_columns: list[str],
    metric_value_column: str,
) -> Any:
    role_to_metric_column = _component_metric_columns(metric_ir)
    role_columns = _component_parent_columns(metric_ir)
    rename_map = {role_to_metric_column[role]: role for role in role_columns}
    selected = [*axes_columns, *rename_map.keys(), metric_value_column]
    component_df = raw_df[selected].rename(
        columns={**rename_map, metric_value_column: "metric_value"}
    )
    return component_df[[*axes_columns, *role_columns, "metric_value"]]


def _persist_metric_component_frame(
    *,
    session: Session,
    df: Any,
    parent: MetricFrame,
    metric_ir: Any,
    axes: dict[str, Any],
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"],
    job_ref: str,
) -> ComponentFrame:
    frame_ref = _gen_ref("frame")
    component = ComponentFrame(
        _df=df.copy(),
        meta=ComponentFrameMeta(
            ref=frame_ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=job_ref,
            created_at=datetime.now(UTC),
            row_count=len(df),
            byte_size=0,
            lineage=parent.lineage,
            parent_ref=parent.ref,
            parent_kind="metric_frame",
            metric_id=parent.meta.metric_id,
            decomposition_kind=metric_ir.decomposition.kind,
            components=dict(metric_ir.decomposition.components),
            axes=axes,
            semantic_kind=semantic_kind,
            semantic_model=parent.meta.semantic_model,
        ),
    )
    component.meta = cast("ComponentFrameMeta", write_frame_to_disk(session.layout, component))
    return component


def _attach_metric_component_ref(
    *,
    session: Session,
    parent: MetricFrame,
    component: ComponentFrame,
    metric_ir: Any,
) -> MetricFrame:
    parent.meta = parent.meta.model_copy(
        update={
            "component_ref": component.ref,
            "decomposition": _decomposition_payload(metric_ir),
        }
    )
    parent.meta = cast("MetricFrameMeta", write_frame_to_disk(session.layout, parent))
    return parent


def _resolve_timescope(
    timescope: TimeScopeInput,
    *,
    grain: TimeGrain | None,
    time_field: str | None,
) -> tuple[AbsoluteWindow | None, dict[str, Any] | None]:
    timescope_in = normalize_timescope_input(timescope)
    resolved = make_absolute_window(timescope_in, grain=grain, time_field=time_field)
    original = timescope_in.model_dump(mode="json") if timescope_in is not None else None
    return resolved, original


def _validate_dimension_refs(dimensions: list[Any] | None) -> list[DimensionRef]:
    if dimensions is None:
        return []
    if len(dimensions) == 0:
        raise SemanticKindMismatchError(
            message="observe dimensions must be omitted or contain at least one DimensionRef",
            details={
                "expected_kind": "list[DimensionRef] | None",
                "got_kind": "list[]",
            },
        )

    validated: list[DimensionRef] = []
    seen: set[str] = set()
    duplicate_ids: set[str] = set()
    for dimension in dimensions:
        if not isinstance(dimension, DimensionRef):
            raise SemanticKindMismatchError(
                message="observe dimensions requires DimensionRef entries",
                details={
                    "expected_kind": "DimensionRef",
                    "got_kind": type(dimension).__name__,
                },
            )
        if dimension.id in seen:
            duplicate_ids.add(dimension.id)
        seen.add(dimension.id)
        validated.append(dimension)
    if duplicate_ids:
        raise SemanticKindMismatchError(
            message="observe dimensions must not contain duplicate DimensionRef ids",
            details={
                "expected_kind": "unique DimensionRef ids",
                "got_kind": "duplicate DimensionRef ids",
                "duplicate_dimensions": sorted(duplicate_ids),
            },
        )
    return validated


def _resolve_dimensions(
    dimensions: list[Any] | None, *, dataset_irs: dict[str, Any]
) -> list[tuple[str, Any]]:
    dimension_refs = _validate_dimension_refs(dimensions)
    resolved: list[tuple[str, Any]] = []
    for dimension in dimension_refs:
        matches = [
            (dataset_name, field_ir)
            for dataset_name, dataset_ir in dataset_irs.items()
            for field_ir in dataset_ir.fields.values()
            if dimension.id in {field_ir.name, field_ir.semantic_id}
        ]
        if not matches:
            available_ids = sorted(
                {
                    field_ir.name
                    for dataset_ir in dataset_irs.values()
                    for field_ir in dataset_ir.fields.values()
                }
            )
            raise DimensionFieldNotFoundError(
                message=f"dimension '{dimension.id}' not found",
                details={
                    "dimension_id": dimension.id,
                    "searched_datasets": sorted(dataset_irs),
                    "available_ids": available_ids,
                },
            )
        if len(matches) > 1:
            raise AmbiguousDimensionError(
                message=f"dimension '{dimension.id}' is ambiguous",
                details={
                    "dimension_id": dimension.id,
                    "candidates": sorted(
                        f"{dataset_name}.{field_ir.name}" for dataset_name, field_ir in matches
                    ),
                },
            )
        resolved.append(matches[0])

    dimensions_by_dataset: dict[str, list[str]] = {}
    for dataset_name, field_ir in resolved:
        dimensions_by_dataset.setdefault(dataset_name, []).append(field_ir.name)
    if len(dimensions_by_dataset) > 1:
        raise DimensionAcrossDatasetsError(
            message="observe dimensions must resolve to one dataset",
            details={"dimensions_by_dataset": dimensions_by_dataset},
        )
    return resolved


def _resolve_dimensions_across_project(
    dimensions: list[Any] | None, *, sp: Any
) -> list[tuple[str, Any]]:
    dimension_refs = _validate_dimension_refs(dimensions)
    resolved: list[tuple[str, Any]] = []
    for dimension in dimension_refs:
        matches = [
            (field_ir.dataset, field_ir)
            for field_ir in [*sp.list_fields(), *sp.list_time_fields()]
            if dimension.id in {field_ir.name, field_ir.semantic_id}
        ]
        if not matches:
            available_ids = sorted(
                {field_ir.name for field_ir in [*sp.list_fields(), *sp.list_time_fields()]}
            )
            raise DimensionFieldNotFoundError(
                message=f"dimension '{dimension.id}' not found",
                details={
                    "dimension_id": dimension.id,
                    "searched_datasets": sorted(d.semantic_id for d in sp.list_datasets()),
                    "metric_shape": "derived",
                    "available_ids": available_ids,
                },
            )
        if len(matches) > 1:
            raise AmbiguousDimensionError(
                message=f"dimension '{dimension.id}' is ambiguous",
                details={
                    "dimension_id": dimension.id,
                    "candidates": sorted(
                        f"{dataset_name}.{field_ir.name}" for dataset_name, field_ir in matches
                    ),
                },
            )
        resolved.append(matches[0])

    dimensions_by_dataset: dict[str, list[str]] = {}
    for dataset_name, field_ir in resolved:
        dimensions_by_dataset.setdefault(dataset_name, []).append(field_ir.name)
    if len(dimensions_by_dataset) > 1:
        raise DimensionAcrossDatasetsError(
            message="observe dimensions must resolve to one dataset",
            details={"dimensions_by_dataset": dimensions_by_dataset},
        )
    return resolved


def _relationship_neighbors(sp: Any, dataset_id: str) -> list[tuple[str, Any]]:
    neighbors: list[tuple[str, Any]] = []
    for relationship in sp.list_relationships():
        if relationship.from_dataset == dataset_id:
            neighbors.append((relationship.to_dataset, relationship))
        elif relationship.to_dataset == dataset_id:
            neighbors.append((relationship.from_dataset, relationship))
    return neighbors


def _unique_relationship_path(sp: Any, start_dataset: str, end_dataset: str) -> list[Any]:
    if start_dataset == end_dataset:
        return []
    queue: list[tuple[str, list[Any]]] = [(start_dataset, [])]
    paths: list[list[Any]] = []
    shortest_len: int | None = None
    while queue:
        current, path = queue.pop(0)
        if shortest_len is not None and len(path) >= shortest_len:
            continue
        for next_dataset, relationship in _relationship_neighbors(sp, current):
            if any(relationship.semantic_id == existing.semantic_id for existing in path):
                continue
            next_path = [*path, relationship]
            if next_dataset == end_dataset:
                shortest_len = len(next_path)
                paths.append(next_path)
                continue
            queue.append((next_dataset, next_path))
    if not paths:
        raise MetricShapeUnsupportedError(
            message=(
                f"dimension dataset '{end_dataset}' is not reachable from "
                f"component dataset '{start_dataset}'"
            ),
            details={
                "kind": "DerivedDimensionRelationshipMissing",
                "from_dataset": start_dataset,
                "to_dataset": end_dataset,
            },
        )
    shortest_paths = [path for path in paths if len(path) == shortest_len]
    if len(shortest_paths) > 1:
        raise MetricShapeUnsupportedError(
            message=(
                f"dimension dataset '{end_dataset}' has multiple relationship paths "
                f"from component dataset '{start_dataset}'"
            ),
            details={
                "kind": "DerivedDimensionRelationshipAmbiguous",
                "from_dataset": start_dataset,
                "to_dataset": end_dataset,
                "paths": [[rel.semantic_id for rel in path] for path in shortest_paths],
            },
        )
    return shortest_paths[0]


def _field_fn(sp: Any, field_id: str) -> Callable[..., Any]:
    sidecar = sp.sidecar()
    fn = sidecar.get(field_id) if sidecar else None
    if fn is None:
        raise MetricNotFoundError(
            message=f"field callable for '{field_id}' not found",
            details={"field": field_id},
        )
    return cast("Callable[..., Any]", fn)


def _join_related_dimension_table(
    table: Any,
    *,
    sp: Any,
    session: Session,
    dataset_irs: dict[str, _DatasetIRAdapter],
    base_dataset: str,
    dimension_dataset: str,
) -> Any:
    current_table = table
    current_dataset = base_dataset
    for relationship in _unique_relationship_path(sp, base_dataset, dimension_dataset):
        if relationship.from_dataset == current_dataset:
            next_dataset = relationship.to_dataset
            left_fields = relationship.from_fields
            right_fields = relationship.to_fields
        else:
            next_dataset = relationship.from_dataset
            left_fields = relationship.to_fields
            right_fields = relationship.from_fields

        ds_adapter = dataset_irs[next_dataset]
        _datasource_name, backend = _backend_for_datasource(session, ds_adapter.datasource_name)
        next_table = ds_adapter.fn(backend)
        predicates = [
            _field_fn(sp, left_field)(current_table) == _field_fn(sp, right_field)(next_table)
            for left_field, right_field in zip(left_fields, right_fields, strict=True)
        ]
        current_table = current_table.join(next_table, predicates)
        current_dataset = next_dataset
    return current_table


def _evaluate_sentinel_on_frame(node: Any, metric_ir: Any, frame: Any) -> Any:
    if isinstance(node, _ComponentSentinel):
        component_metric_id = metric_ir.decomposition.components[node.name]
        component_col = component_metric_id.rsplit(".", 1)[1]
        return frame[component_col]
    if isinstance(node, _BinOpSentinel):
        left = _evaluate_sentinel_or_literal_on_frame(node.left, metric_ir, frame)
        right = _evaluate_sentinel_or_literal_on_frame(node.right, metric_ir, frame)
        if node.op == "+":
            return left + right
        if node.op == "-":
            return left - right
        if node.op == "*":
            return left * right
        if node.op == "/":
            return left / right
    if isinstance(node, _UnaryNegSentinel):
        return -_evaluate_sentinel_on_frame(node.operand, metric_ir, frame)
    raise MetricShapeUnsupportedError(
        message=f"unsupported derived metric expression node {type(node).__name__}",
        details={"kind": "DerivedMetricExpressionUnsupported"},
    )


def _evaluate_sentinel_or_literal_on_frame(node: Any, metric_ir: Any, frame: Any) -> Any:
    if isinstance(node, (int, float)):
        return node
    return _evaluate_sentinel_on_frame(node, metric_ir, frame)


def _observe_derived_grouped(
    metric_ir: Any,
    metric_name: str,
    *,
    sp: Any,
    session: Session,
    dimensions: list[Any] | None,
    resolved_window: AbsoluteWindow,
    where: dict[str, Any] | None,
) -> tuple[Any, Any | None, dict[str, Any], Literal["time_series", "panel"]]:
    sidecar = sp.sidecar()
    sentinel_tree = sidecar.get(metric_ir.semantic_id) if sidecar else None
    if sentinel_tree is None:
        raise MetricNotFoundError(
            message=f"derived metric expression for '{metric_ir.semantic_id}' not found",
            details={"metric": metric_ir.semantic_id},
        )

    resolved_dimensions = _resolve_dimensions_across_project(dimensions, sp=sp)
    dimension_names = [field_ir.name for _, field_ir in resolved_dimensions]
    component_ids = list(metric_ir.decomposition.components.values())
    component_irs: list[Any] = []
    dataset_ids: list[str] = []
    dimension_dataset = resolved_dimensions[0][0] if resolved_dimensions else None

    for component_id in component_ids:
        component_ir = sp.get_metric(component_id)
        if component_ir is None:
            raise MetricNotFoundError(message=f"component metric '{component_id}' not found")
        if component_ir.is_derived:
            raise MetricShapeUnsupportedError(
                message="nested derived time-aware metrics are not supported yet",
                details={"kind": "NestedDerivedTimeAwareUnsupported", "metric": component_id},
            )
        if len(component_ir.datasets) != 1:
            raise MetricShapeUnsupportedError(
                message="derived time-aware metrics require single-dataset component metrics",
                details={
                    "kind": "DerivedComponentMultiDatasetUnsupported",
                    "metric": component_id,
                    "datasets": sorted(component_ir.datasets),
                },
            )
        component_irs.append(component_ir)
        for dataset_id in component_ir.datasets:
            if dataset_id not in dataset_ids:
                dataset_ids.append(dataset_id)
        if dimension_dataset is not None and dimension_dataset not in dataset_ids:
            dataset_ids.append(dimension_dataset)

    dataset_irs: dict[str, _DatasetIRAdapter] = {}
    primary_datasource: str | None = None
    for dataset_id in dataset_ids:
        dataset_ir = sp.get_dataset(dataset_id)
        if dataset_ir is None:
            raise MetricNotFoundError(message=f"dataset '{dataset_id}' not found")
        adapter = _build_dataset_adapter(sp, dataset_ir)
        dataset_irs[dataset_id] = adapter
        datasource_name, _backend = _backend_for_datasource(session, adapter.datasource_name)
        if primary_datasource is None:
            primary_datasource = datasource_name
        elif primary_datasource != datasource_name:
            raise CrossBackendMetricError(
                message=(
                    f"derived metric '{metric_ir.semantic_id}' spans multiple "
                    "datasources; v1 does not support federation"
                ),
            )

    pandas = __import__("pandas")
    component_frames: list[Any] = []
    for component_ir in component_irs:
        base_dataset = component_ir.datasets[0]
        ds_adapter = dataset_irs[base_dataset]
        datasource_name, backend = _backend_for_datasource(session, ds_adapter.datasource_name)
        session.known_datasources.add(datasource_name)
        table = ds_adapter.fn(backend)
        table = apply_slice_to_dataset(table, where, dataset_ir=ds_adapter)
        table = apply_window_to_dataset(
            table,
            resolved_window,
            dataset_ir=ds_adapter,
            session_tz=cast("ZoneInfo", session.tz),
        )
        if dimension_dataset is not None and base_dataset != dimension_dataset:
            table = _join_related_dimension_table(
                table,
                sp=sp,
                session=session,
                dataset_irs=dataset_irs,
                base_dataset=base_dataset,
                dimension_dataset=dimension_dataset,
            )
        time_field_ir = resolve_window_time_field(ds_adapter, window=resolved_window)
        table = apply_time_series_bucket(
            table,
            field_ir=time_field_ir,
            window=resolved_window,
            session_tz=cast("ZoneInfo", session.tz),
        )
        if resolved_dimensions:
            dimension_exprs = {
                field_ir.name: _field_fn(sp, field_ir.semantic_id)(table).name(field_ir.name)
                for _, field_ir in resolved_dimensions
            }
            table = table.mutate(**dimension_exprs)
        component_fn = sidecar.get(component_ir.semantic_id) if sidecar else None
        if component_fn is None:
            raise MetricNotFoundError(
                message=f"metric callable for '{component_ir.semantic_id}' not found",
                details={"metric": component_ir.semantic_id},
            )
        component_name = component_ir.name
        metric_expr = component_fn(table)
        group_names = ["bucket_start", *dimension_names]
        grouped_expr = (
            table.group_by(group_names)
            .aggregate(**{component_name: metric_expr})
            .order_by(group_names)
            .select(*group_names, component_name)
        )
        result_df = execute(
            grouped_expr,
            datasource_name=datasource_name,
            cache=session.backend_cache,
            session_id=session.id,
        ).df
        if resolved_window.grain == "day" and "bucket_start" in result_df:
            with suppress(AttributeError):
                result_df["bucket_start"] = result_df["bucket_start"].dt.date
        component_frames.append(result_df)

    merge_keys = ["bucket_start", *dimension_names]
    merged = component_frames[0]
    for frame in component_frames[1:]:
        merged = pandas.merge(merged, frame, on=merge_keys, how="outer")
    merged[metric_name] = _evaluate_sentinel_on_frame(sentinel_tree, metric_ir, merged)
    result_df = merged[[*merge_keys, metric_name]].sort_values(merge_keys).reset_index(drop=True)

    component_df: Any | None = None
    if _is_component_aware_decomposition(metric_ir):
        component_df = _component_frame_df(
            raw_df=merged,
            metric_ir=metric_ir,
            axes_columns=merge_keys,
            metric_value_column=metric_name,
        )
        component_df = component_df.sort_values(merge_keys).reset_index(drop=True)

    class _Result:
        def __init__(self, df: Any) -> None:
            self.df = df
            self.row_count = len(df)

    axes: dict[str, Any] = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": resolved_window.grain,
            "time_field": resolve_window_time_field(
                dataset_irs[component_irs[0].datasets[0]],
                window=resolved_window,
            ).name,
        }
    }
    axes.update(
        {
            field_ir.name: {"role": "dimension", "column": field_ir.name}
            for _, field_ir in resolved_dimensions
        }
    )
    semantic_kind: Literal["time_series", "panel"] = (
        "panel" if resolved_dimensions else "time_series"
    )
    return _Result(result_df), component_df, axes, semantic_kind


def _observe_derived_segmented(
    metric_ir: Any,
    metric_name: str,
    *,
    sp: Any,
    session: Session,
    dimensions: list[Any] | None,
    resolved_window: AbsoluteWindow | None,
    where: dict[str, Any] | None,
) -> tuple[Any, Any | None, dict[str, Any], Literal["segmented"]]:
    sidecar = sp.sidecar()
    sentinel_tree = sidecar.get(metric_ir.semantic_id) if sidecar else None
    if sentinel_tree is None:
        raise MetricNotFoundError(
            message=f"derived metric expression for '{metric_ir.semantic_id}' not found",
            details={"metric": metric_ir.semantic_id},
        )
    resolved_dimensions = _resolve_dimensions_across_project(dimensions, sp=sp)
    dimension_dataset = resolved_dimensions[0][0]
    dimension_names = [field_ir.name for _, field_ir in resolved_dimensions]
    component_ids = list(metric_ir.decomposition.components.values())
    component_irs = []
    dataset_ids: list[str] = []
    for component_id in component_ids:
        component_ir = sp.get_metric(component_id)
        if component_ir is None:
            raise MetricNotFoundError(message=f"component metric '{component_id}' not found")
        if component_ir.is_derived:
            raise MetricShapeUnsupportedError(
                message="nested derived metric dimensions are not supported yet",
                details={"kind": "NestedDerivedDimensionsUnsupported", "metric": component_id},
            )
        if len(component_ir.datasets) != 1:
            raise MetricShapeUnsupportedError(
                message="derived metric dimensions require single-dataset component metrics",
                details={
                    "kind": "DerivedComponentMultiDatasetUnsupported",
                    "metric": component_id,
                    "datasets": sorted(component_ir.datasets),
                },
            )
        component_irs.append(component_ir)
        for dataset_id in [*component_ir.datasets, dimension_dataset]:
            if dataset_id not in dataset_ids:
                dataset_ids.append(dataset_id)

    dataset_irs: dict[str, _DatasetIRAdapter] = {}
    primary_datasource: str | None = None
    for dataset_id in dataset_ids:
        dataset_ir = sp.get_dataset(dataset_id)
        if dataset_ir is None:
            raise MetricNotFoundError(message=f"dataset '{dataset_id}' not found")
        adapter = _build_dataset_adapter(sp, dataset_ir)
        dataset_irs[dataset_id] = adapter
        datasource_name, _backend = _backend_for_datasource(session, adapter.datasource_name)
        if primary_datasource is None:
            primary_datasource = datasource_name
        elif primary_datasource != datasource_name:
            raise CrossBackendMetricError(
                message=(
                    f"derived metric '{metric_ir.semantic_id}' dimensions span multiple "
                    "datasources; v1 does not support federation"
                ),
            )

    pandas = __import__("pandas")
    component_frames: list[Any] = []
    for component_ir in component_irs:
        base_dataset = component_ir.datasets[0]
        ds_adapter = dataset_irs[base_dataset]
        datasource_name, backend = _backend_for_datasource(session, ds_adapter.datasource_name)
        table = ds_adapter.fn(backend)
        table = apply_slice_to_dataset(table, where, dataset_ir=ds_adapter)
        if base_dataset != dimension_dataset:
            table = _join_related_dimension_table(
                table,
                sp=sp,
                session=session,
                dataset_irs=dataset_irs,
                base_dataset=base_dataset,
                dimension_dataset=dimension_dataset,
            )
        dimension_exprs = {
            field_ir.name: _field_fn(sp, field_ir.semantic_id)(table).name(field_ir.name)
            for _, field_ir in resolved_dimensions
        }
        table = table.mutate(**dimension_exprs)
        component_fn = sidecar.get(component_ir.semantic_id) if sidecar else None
        if component_fn is None:
            raise MetricNotFoundError(
                message=f"metric callable for '{component_ir.semantic_id}' not found",
                details={"metric": component_ir.semantic_id},
            )
        component_name = component_ir.name
        metric_expr = component_fn(table)
        grouped_expr = (
            table.group_by(dimension_names)
            .aggregate(**{component_name: metric_expr})
            .order_by(dimension_names)
            .select(*dimension_names, component_name)
        )
        component_frames.append(
            execute(
                grouped_expr,
                datasource_name=datasource_name,
                cache=session.backend_cache,
                session_id=session.id,
            ).df
        )

    merged = component_frames[0]
    for frame in component_frames[1:]:
        merged = pandas.merge(merged, frame, on=dimension_names, how="outer")
    merged[metric_name] = _evaluate_sentinel_on_frame(sentinel_tree, metric_ir, merged)
    result_df = merged[[*dimension_names, metric_name]].sort_values(dimension_names)
    result_df = result_df.reset_index(drop=True)

    # Build component_df for component-aware decompositions
    component_df: Any | None = None
    if _is_component_aware_decomposition(metric_ir):
        component_df = _component_frame_df(
            raw_df=merged,
            metric_ir=metric_ir,
            axes_columns=dimension_names,
            metric_value_column=metric_name,
        )
        component_df = component_df.sort_values(dimension_names).reset_index(drop=True)

    class _Result:
        def __init__(self, df: Any) -> None:
            self.df = df
            self.row_count = len(df)

    axes = {
        field_ir.name: {"role": "dimension", "column": field_ir.name}
        for _, field_ir in resolved_dimensions
    }
    return _Result(result_df), component_df, axes, "segmented"


def _observe_derived_scalar(
    metric_ir: Any,
    metric_name: str,
    *,
    sp: Any,
    session: Session,
    resolved_window: AbsoluteWindow | None,
    where: dict[str, Any] | None,
) -> tuple[Any, Any | None, dict[str, Any], Literal["scalar"]]:
    sidecar = sp.sidecar()
    sentinel_tree = sidecar.get(metric_ir.semantic_id) if sidecar else None
    if sentinel_tree is None:
        raise MetricNotFoundError(
            message=f"derived metric expression for '{metric_ir.semantic_id}' not found",
            details={"metric": metric_ir.semantic_id},
        )

    dataset_irs: dict[str, _DatasetIRAdapter] = {}
    primary_datasource: str | None = None
    component_values: dict[str, Any] = {}
    for component_id in metric_ir.decomposition.components.values():
        component_ir = sp.get_metric(component_id)
        if component_ir is None:
            raise MetricNotFoundError(message=f"component metric '{component_id}' not found")
        if component_ir.is_derived:
            raise MetricShapeUnsupportedError(
                message="nested derived scalar metrics are not supported yet",
                details={"kind": "NestedDerivedScalarUnsupported", "metric": component_id},
            )
        if len(component_ir.datasets) == 0:
            raise MetricShapeUnsupportedError(
                message="derived scalar metric components must reference datasets",
                details={"kind": "DerivedComponentDatasetMissing", "metric": component_id},
            )

        dataset_tables: dict[str, Any] = {}
        component_datasource: str | None = None
        for dataset_id in component_ir.datasets:
            ds_adapter = dataset_irs.get(dataset_id)
            if ds_adapter is None:
                dataset_ir = sp.get_dataset(dataset_id)
                if dataset_ir is None:
                    raise MetricNotFoundError(message=f"dataset '{dataset_id}' not found")
                ds_adapter = _build_dataset_adapter(sp, dataset_ir)
                dataset_irs[dataset_id] = ds_adapter

            datasource_name, backend = _backend_for_datasource(session, ds_adapter.datasource_name)
            if primary_datasource is None:
                primary_datasource = datasource_name
            elif primary_datasource != datasource_name:
                raise CrossBackendMetricError(
                    message=(
                        f"derived metric '{metric_ir.semantic_id}' spans multiple "
                        "datasources; v1 does not support federation"
                    ),
                )
            component_datasource = datasource_name
            table = ds_adapter.fn(backend)
            table = apply_slice_to_dataset(table, where, dataset_ir=ds_adapter)
            table = apply_window_to_dataset(
                table,
                resolved_window,
                dataset_ir=ds_adapter,
                session_tz=cast("ZoneInfo", session.tz),
            )
            dataset_tables[dataset_id] = table
            session.known_datasources.add(datasource_name)

        component_fn = sidecar.get(component_ir.semantic_id) if sidecar else None
        if component_fn is None:
            raise MetricNotFoundError(
                message=f"metric callable for '{component_ir.semantic_id}' not found",
                details={"metric": component_ir.semantic_id},
            )
        if component_datasource is None:
            raise MetricNotFoundError(
                message=f"component metric '{component_id}' references no datasets"
            )
        metric_expr = _call_metric(
            component_fn,
            metric_datasets=tuple(component_ir.datasets),
            dataset_tables=dataset_tables,
        )
        result = execute(
            metric_expr,
            datasource_name=component_datasource,
            cache=session.backend_cache,
            session_id=session.id,
        )
        if result.df.empty or len(result.df.columns) == 0:
            raise MetricShapeUnsupportedError(
                message=f"component metric '{component_id}' did not return a scalar value",
                details={"kind": "DerivedComponentScalarEmpty", "metric": component_id},
            )
        component_values[component_ir.name] = result.df.iloc[0, 0]

    pandas = __import__("pandas")
    component_frame = pandas.DataFrame([component_values])
    metric_value = _evaluate_sentinel_on_frame(sentinel_tree, metric_ir, component_frame)
    if hasattr(metric_value, "iloc"):
        metric_value = metric_value.iloc[0]
    result_df = pandas.DataFrame([{metric_name: metric_value}])

    # Build component_df for component-aware decompositions
    component_df: Any | None = None
    if _is_component_aware_decomposition(metric_ir):
        raw_df = component_frame.copy()
        raw_df[metric_name] = metric_value
        component_df = _component_frame_df(
            raw_df=raw_df,
            metric_ir=metric_ir,
            axes_columns=[],
            metric_value_column=metric_name,
        )

    class _Result:
        def __init__(self, df: Any) -> None:
            self.df = df
            self.row_count = len(df)

    return _Result(result_df), component_df, {}, "scalar"


def _dump_dimensions(dimensions: list[DimensionRef] | None) -> list[dict[str, Any]] | None:
    if dimensions is None:
        return None
    return [dimension.model_dump(mode="json") for dimension in dimensions]


def _backend_for_datasource(session: Session, datasource_name: str) -> tuple[str, Any]:
    return datasource_name, session.backend_cache.get_or_create(datasource_name)


def _call_metric(
    metric_fn: Callable[..., Any],
    *,
    metric_datasets: tuple[str, ...],
    dataset_tables: dict[str, Any],
) -> Any:
    return metric_fn(*(dataset_tables[dataset_name] for dataset_name in metric_datasets))


def observe(
    metric: MetricRef,
    *,
    timescope: TimeScopeInput = None,
    grain: TimeGrain | None = None,
    dimensions: list[DimensionRef] | None = None,
    where: dict[str, SliceValue] | None = None,
    time_field: str | None = None,
    expect_shape: SemanticShape | None = None,
    session: Session | None = None,
) -> MetricFrame:
    """Materialize a metric into a typed MetricFrame.

    When to use: starting point for any metric analysis workflow.

    Resolves ``metric`` against the active semantic project, applies the
    optional ``timescope`` / ``grain`` / ``dimensions`` / ``where`` filters, executes against
    the session's backend, and persists the result as a MetricFrame on disk.

    Args:
        metric: Wrap the registered metric id with ``mv.MetricRef("<model>.<metric>")``.
            Bare strings are rejected.
        timescope: Absolute time range as ``{"start": ..., "end": ...}``.
        grain: Optional time bucket grain. When present, observe returns a time
            series or panel depending on ``dimensions``.
        dimensions: Segment axes. In v1 all dimensions must resolve to the same
            dataset as ``metric``.
        where: Pre-aggregation row filter. Values are either a scalar (``==``),
            a list (``in``), or ``{"op": "<op>", "value": ...}`` where op is
            one of ``==, !=, in, >, >=, <, <=, between``.
        expect_shape: Optional guard. If set, observe predicts the output shape
            from ``grain``/``dimensions`` and raises ``SemanticKindMismatchError``
            before any backend work when the prediction differs.
        session: Defaults to the currently-attached session.

    Raises:
        MetricNotFoundError: The metric id is unknown or not ``<model>.<metric>``.
        SemanticKindMismatchError: ``metric`` is not a ``MetricRef``.
        AmbiguousDimensionError: A dimension resolves to multiple datasets.
        DimensionAcrossDatasetsError: Dimensions span more than one dataset.
        DimensionFieldNotFoundError: A dimension field does not exist on the dataset.
        CrossBackendMetricError: ``metric`` and ``dimensions`` resolve to different backends.

    Example:
        >>> frame = session.observe(
        ...     mv.MetricRef("sales.revenue"),
        ...     timescope={"start": "2026-07-01", "end": "2026-09-30"},
        ...     grain="day",
        ...     dimensions=[mv.DimensionRef("country")],
        ... )
        >>> frame.summary()
    """
    if session is None:
        session = session_active()
    ensure_session_writable(session)
    if not isinstance(metric, MetricRef):
        raise SemanticKindMismatchError(
            message="observe requires metric=MetricRef(...)",
            details={
                "expected_kind": "MetricRef",
                "got_kind": type(metric).__name__,
            },
        )
    metric_id = metric.id
    if "." not in metric_id:
        raise MetricNotFoundError(message=f"metric '{metric_id}' is not '<model>.<metric>'")
    model_name, metric_name = metric_id.split(".", 1)
    resolved_window, original_timescope = _resolve_timescope(
        timescope,
        grain=grain,
        time_field=time_field,
    )
    is_time_series = resolved_window is not None and resolved_window.grain is not None

    # Access semantic layer through session.semantic_project (SemanticProject instance)
    sp = session.semantic_project
    if not sp.is_ready():
        sp.load()
    metric_semantic_id = f"{model_name}.{metric_name}"
    metric_ir = sp.get_metric(metric_semantic_id)
    if metric_ir is None:
        available_ids = sorted(m.semantic_id for m in sp.list_metrics())
        raise MetricNotFoundError(
            message=f"metric '{metric_id}' not found",
            hint="Check <project_root>/.marivo/semantic/.",
            details={
                "model": model_name,
                "metric": metric_name,
                "available_ids": available_ids,
            },
        )

    # Get the metric callable from the sidecar
    sidecar = sp.sidecar()
    metric_fn = sidecar.get(metric_semantic_id) if sidecar else None
    if metric_fn is None:
        raise MetricNotFoundError(
            message=f"metric callable for '{metric_id}' not found",
            details={"model": model_name, "metric": metric_name},
        )

    started_at = datetime.now(UTC)
    started = monotonic()
    dataset_tables: dict[str, Any] = {}
    dataset_irs: dict[str, _DatasetIRAdapter] = {}
    primary_datasource: str | None = None
    stored_where = normalize_slice_for_storage(where)
    metric_datasets = tuple(metric_ir.datasets)
    dimension_refs = _validate_dimension_refs(dimensions)
    if expect_shape is not None:
        predicted_shape = observe_output_shape(
            has_grain=is_time_series, has_dimensions=bool(dimension_refs)
        )
        if predicted_shape != expect_shape:
            raise SemanticKindMismatchError(
                message=(
                    f"observe will produce semantic_shape {predicted_shape!r} for these "
                    f"inputs, but expect_shape={expect_shape!r} was requested"
                ),
                details={
                    "intent": "observe",
                    "predicted_semantic_shape": predicted_shape,
                    "expect_shape": expect_shape,
                },
            )
    if metric_ir.is_derived and is_time_series and resolved_window is not None:
        result, component_df, grouped_axes, grouped_kind = _observe_derived_grouped(
            metric_ir,
            metric_name,
            sp=sp,
            session=session,
            dimensions=dimensions,
            resolved_window=resolved_window,
            where=where,
        )
        _persist_known_datasources(session)
        finished_at = datetime.now(UTC)
        frame_ref = _gen_ref("frame")
        job_ref = _gen_ref("job")
        params_timescope = None
        if resolved_window is not None:
            params_timescope = {
                "original": original_timescope,
                "resolved": dump_window(resolved_window),
                "session_tz": str(session.tz),
            }
        params = {
            "metric": metric_id,
            "timescope": params_timescope,
            "dimensions": _dump_dimensions(dimensions),
            "where": stored_where,
        }
        meta = MetricFrameMeta(
            kind="metric_frame",
            ref=frame_ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=job_ref,
            created_at=finished_at,
            row_count=result.row_count,
            byte_size=0,
            lineage=Lineage(
                steps=[
                    LineageStep(
                        intent="observe",
                        job_ref=job_ref,
                        inputs=[],
                        params_digest=_params_digest(params),
                    )
                ]
            ),
            metric_id=metric_id,
            axes=grouped_axes,
            measure={"name": metric_name},
            window=dump_window(resolved_window),
            where=stored_where,
            semantic_kind=grouped_kind,
            semantic_model=model_name,
        )
        frame = MetricFrame(_df=result.df, meta=meta)
        frame = _commit_observe_metric_frame(
            session=session,
            frame=frame,
            params=params,
            metric_id=metric_id,
            model_name=model_name,
            stored_where=stored_where,
            semantic_kind=grouped_kind,
        )
        if component_df is not None:
            component = _persist_metric_component_frame(
                session=session,
                df=component_df,
                parent=frame,
                metric_ir=metric_ir,
                axes=grouped_axes,
                semantic_kind=grouped_kind,
                job_ref=job_ref,
            )
            frame = _attach_metric_component_ref(
                session=session,
                parent=frame,
                component=component,
                metric_ir=metric_ir,
            )
        write_job_record(
            session.layout,
            {
                "id": job_ref,
                "session_id": session.id,
                "intent": "observe",
                "params": params,
                "input_frame_refs": [],
                "output_frame_ref": frame.meta.artifact_id or frame.ref,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_ms": int((monotonic() - started) * 1000),
                "status": "succeeded",
                "error": None,
                "semantic_project_root": session.semantic_project.root,
                "semantic_model": model_name,
            },
        )
        return frame
    if metric_ir.is_derived and not dimension_refs:
        result, component_df, scalar_axes, scalar_kind = _observe_derived_scalar(
            metric_ir,
            metric_name,
            sp=sp,
            session=session,
            resolved_window=resolved_window,
            where=where,
        )
        _persist_known_datasources(session)
        finished_at = datetime.now(UTC)
        frame_ref = _gen_ref("frame")
        job_ref = _gen_ref("job")
        params_timescope = None
        if resolved_window is not None:
            params_timescope = {
                "original": original_timescope,
                "resolved": dump_window(resolved_window),
                "session_tz": str(session.tz),
            }
        params = {
            "metric": metric_id,
            "timescope": params_timescope,
            "dimensions": _dump_dimensions(dimensions),
            "where": stored_where,
        }
        meta = MetricFrameMeta(
            kind="metric_frame",
            ref=frame_ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=job_ref,
            created_at=finished_at,
            row_count=result.row_count,
            byte_size=0,
            lineage=Lineage(
                steps=[
                    LineageStep(
                        intent="observe",
                        job_ref=job_ref,
                        inputs=[],
                        params_digest=_params_digest(params),
                    )
                ]
            ),
            metric_id=metric_id,
            axes=scalar_axes,
            measure={"name": metric_name},
            window=dump_window(resolved_window),
            where=stored_where,
            semantic_kind=scalar_kind,
            semantic_model=model_name,
        )
        frame = MetricFrame(_df=result.df, meta=meta)
        frame = _commit_observe_metric_frame(
            session=session,
            frame=frame,
            params=params,
            metric_id=metric_id,
            model_name=model_name,
            stored_where=stored_where,
            semantic_kind=scalar_kind,
        )
        if component_df is not None:
            component = _persist_metric_component_frame(
                session=session,
                df=component_df,
                parent=frame,
                metric_ir=metric_ir,
                axes=scalar_axes,
                semantic_kind=scalar_kind,
                job_ref=job_ref,
            )
            frame = _attach_metric_component_ref(
                session=session,
                parent=frame,
                component=component,
                metric_ir=metric_ir,
            )
        write_job_record(
            session.layout,
            {
                "id": job_ref,
                "session_id": session.id,
                "intent": "observe",
                "params": params,
                "input_frame_refs": [],
                "output_frame_ref": frame.meta.artifact_id or frame.ref,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_ms": int((monotonic() - started) * 1000),
                "status": "succeeded",
                "error": None,
                "semantic_project_root": session.semantic_project.root,
                "semantic_model": model_name,
            },
        )
        return frame
    if metric_ir.is_derived and dimension_refs:
        result, component_df, segmented_axes, segmented_kind = _observe_derived_segmented(
            metric_ir,
            metric_name,
            sp=sp,
            session=session,
            dimensions=dimensions,
            resolved_window=resolved_window,
            where=where,
        )
        finished_at = datetime.now(UTC)
        frame_ref = _gen_ref("frame")
        job_ref = _gen_ref("job")
        params_timescope = None
        if resolved_window is not None:
            params_timescope = {
                "original": original_timescope,
                "resolved": dump_window(resolved_window),
                "session_tz": str(session.tz),
            }
        params = {
            "metric": metric_id,
            "timescope": params_timescope,
            "dimensions": _dump_dimensions(dimensions),
            "where": stored_where,
        }
        meta = MetricFrameMeta(
            kind="metric_frame",
            ref=frame_ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=job_ref,
            created_at=finished_at,
            row_count=result.row_count,
            byte_size=0,
            lineage=Lineage(
                steps=[
                    LineageStep(
                        intent="observe",
                        job_ref=job_ref,
                        inputs=[],
                        params_digest=_params_digest(params),
                    )
                ]
            ),
            metric_id=metric_id,
            axes=segmented_axes,
            measure={"name": metric_name},
            window=dump_window(resolved_window),
            where=stored_where,
            semantic_kind=segmented_kind,
            semantic_model=model_name,
        )
        frame = MetricFrame(_df=result.df, meta=meta)
        frame = _commit_observe_metric_frame(
            session=session,
            frame=frame,
            params=params,
            metric_id=metric_id,
            model_name=model_name,
            stored_where=stored_where,
            semantic_kind=segmented_kind,
        )
        if component_df is not None:
            component = _persist_metric_component_frame(
                session=session,
                df=component_df,
                parent=frame,
                metric_ir=metric_ir,
                axes=segmented_axes,
                semantic_kind=segmented_kind,
                job_ref=job_ref,
            )
            frame = _attach_metric_component_ref(
                session=session,
                parent=frame,
                component=component,
                metric_ir=metric_ir,
            )
        write_job_record(
            session.layout,
            {
                "id": job_ref,
                "session_id": session.id,
                "intent": "observe",
                "params": params,
                "input_frame_refs": [],
                "output_frame_ref": frame_ref,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_ms": int((monotonic() - started) * 1000),
                "status": "succeeded",
                "error": None,
                "semantic_project_root": session.semantic_project.root,
                "semantic_model": model_name,
            },
        )
        return frame
    raise_first(
        validate_observe(
            metric_id=metric_id,
            metric_datasets=metric_datasets,
            is_time_series=is_time_series,
            has_dimensions=bool(dimension_refs),
            dimensions_dump=_dump_dimensions(dimensions),
        )
    )

    for dataset_name in metric_datasets:
        dataset_ir = sp.get_dataset(dataset_name)
        if dataset_ir is None:
            raise MetricNotFoundError(
                message=f"dataset '{dataset_name}' not found for metric '{metric_id}'",
                details={"dataset": dataset_name},
            )
        dataset_irs[dataset_name] = _build_dataset_adapter(sp, dataset_ir)

    resolved_dimensions = _resolve_dimensions(dimensions, dataset_irs=dataset_irs)

    for dataset_name in metric_datasets:
        ds_adapter = dataset_irs[dataset_name]
        datasource_name, backend = _backend_for_datasource(session, ds_adapter.datasource_name)
        if primary_datasource is None:
            primary_datasource = datasource_name
        elif primary_datasource != datasource_name:
            raise CrossBackendMetricError(
                message=f"metric '{metric_id}' spans multiple datasources; v1 does not support it",
            )
        table = ds_adapter.fn(backend)
        table = apply_slice_to_dataset(table, where, dataset_ir=ds_adapter)
        table = apply_window_to_dataset(
            table,
            resolved_window,
            dataset_ir=ds_adapter,
            session_tz=cast("ZoneInfo", session.tz),
        )
        dataset_tables[dataset_name] = table
        session.known_datasources.add(datasource_name)
    _persist_known_datasources(session)

    if primary_datasource is None:
        raise MetricNotFoundError(message=f"metric '{metric_id}' references no datasets")

    axes: dict[str, Any] = {}
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = "scalar"
    if is_time_series and resolved_window is not None and resolved_dimensions:
        dataset_name = metric_datasets[0]
        ds_adapter = dataset_irs[dataset_name]
        time_field_ir = resolve_window_time_field(ds_adapter, window=resolved_window)
        bucketed_table = apply_time_series_bucket(
            dataset_tables[dataset_name],
            field_ir=time_field_ir,
            window=resolved_window,
            session_tz=cast("ZoneInfo", session.tz),
        )
        dimension_names = [field_ir.name for _, field_ir in resolved_dimensions]
        dimension_exprs = {
            field_ir.name: field_ir.fn(bucketed_table).name(field_ir.name)
            for _, field_ir in resolved_dimensions
        }
        bucketed_table = bucketed_table.mutate(**dimension_exprs)
        dataset_tables[dataset_name] = bucketed_table
        metric_expr = _call_metric(
            metric_fn,
            metric_datasets=metric_datasets,
            dataset_tables=dataset_tables,
        )
        group_names = ["bucket_start", *dimension_names]
        grouped_expr = (
            bucketed_table.group_by(group_names)
            .aggregate(**{metric_name: metric_expr})
            .order_by(group_names)
            .select(*group_names, metric_name)
        )
        result = execute(
            grouped_expr,
            datasource_name=primary_datasource,
            cache=session.backend_cache,
            session_id=session.id,
        )
        if resolved_window.grain == "day" and "bucket_start" in result.df:
            with suppress(AttributeError):
                result.df["bucket_start"] = result.df["bucket_start"].dt.date
        axes = {
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": resolved_window.grain,
                "time_field": time_field_ir.name,
            },
            **{
                field_ir.name: {"role": "dimension", "column": field_ir.name}
                for _, field_ir in resolved_dimensions
            },
        }
        semantic_kind = "panel"
    elif is_time_series and resolved_window is not None:
        dataset_name = metric_datasets[0]
        ds_adapter = dataset_irs[dataset_name]
        time_field_ir = resolve_window_time_field(ds_adapter, window=resolved_window)
        bucketed_table = apply_time_series_bucket(
            dataset_tables[dataset_name],
            field_ir=time_field_ir,
            window=resolved_window,
            session_tz=cast("ZoneInfo", session.tz),
        )
        dataset_tables[dataset_name] = bucketed_table
        metric_expr = _call_metric(
            metric_fn,
            metric_datasets=metric_datasets,
            dataset_tables=dataset_tables,
        )
        grouped_expr = (
            bucketed_table.group_by("bucket_start")
            .aggregate(**{metric_name: metric_expr})
            .order_by("bucket_start")
            .select("bucket_start", metric_name)
        )
        result = execute(
            grouped_expr,
            datasource_name=primary_datasource,
            cache=session.backend_cache,
            session_id=session.id,
        )
        axes = {
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": resolved_window.grain,
                "time_field": time_field_ir.name,
            }
        }
        semantic_kind = "time_series"
    elif resolved_dimensions:
        dataset_name = resolved_dimensions[0][0]
        table = dataset_tables[dataset_name]
        dimension_names = [field_ir.name for _, field_ir in resolved_dimensions]
        dimension_exprs = {
            field_ir.name: field_ir.fn(table).name(field_ir.name)
            for _, field_ir in resolved_dimensions
        }
        table = table.mutate(**dimension_exprs)
        dataset_tables[dataset_name] = table
        metric_expr = _call_metric(
            metric_fn,
            metric_datasets=metric_datasets,
            dataset_tables=dataset_tables,
        )
        grouped_expr = (
            table.group_by(dimension_names)
            .aggregate(**{metric_name: metric_expr})
            .order_by(dimension_names)
            .select(*dimension_names, metric_name)
        )
        result = execute(
            grouped_expr,
            datasource_name=primary_datasource,
            cache=session.backend_cache,
            session_id=session.id,
        )
        axes = {
            field_ir.name: {"role": "dimension", "column": field_ir.name}
            for _, field_ir in resolved_dimensions
        }
        semantic_kind = "segmented"
    else:
        metric_expr = _call_metric(
            metric_fn,
            metric_datasets=metric_datasets,
            dataset_tables=dataset_tables,
        )
        result = execute(
            metric_expr,
            datasource_name=primary_datasource,
            cache=session.backend_cache,
            session_id=session.id,
        )
    finished_at = datetime.now(UTC)

    frame_ref = _gen_ref("frame")
    job_ref = _gen_ref("job")
    params_timescope = None
    if resolved_window is not None:
        params_timescope = {
            "original": original_timescope,
            "resolved": dump_window(resolved_window),
            "session_tz": str(session.tz),
        }
    params = {
        "metric": metric_id,
        "timescope": params_timescope,
        "dimensions": _dump_dimensions(dimensions),
        "where": stored_where,
    }
    meta = MetricFrameMeta(
        kind="metric_frame",
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        created_at=finished_at,
        row_count=result.row_count,
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="observe",
                    job_ref=job_ref,
                    inputs=[],
                    params_digest=_params_digest(params),
                )
            ]
        ),
        metric_id=metric_id,
        axes=axes,
        measure={"name": metric_name},
        window=dump_window(resolved_window),
        where=stored_where,
        semantic_kind=semantic_kind,
        semantic_model=model_name,
    )
    frame = MetricFrame(_df=result.df, meta=meta)

    # --- Evidence pipeline: commit_result replaces write_frame_to_disk ---
    _grain_raw = resolved_window.grain if resolved_window is not None else None
    _subject_grain: Literal["hour", "day", "week", "month"] | None = (
        cast("Literal['hour', 'day', 'week', 'month'] | None", _grain_raw)
        if _grain_raw in ("hour", "day", "week", "month")
        else None
    )
    frame = _commit_observe_metric_frame(
        session=session,
        frame=frame,
        params=params,
        metric_id=metric_id,
        model_name=model_name,
        stored_where=stored_where,
        semantic_kind=semantic_kind,
        subject_grain=_subject_grain,
    )

    write_job_record(
        session.layout,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "observe",
            "params": params,
            "input_frame_refs": [],
            "output_frame_ref": frame.meta.artifact_id or frame.ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": session.semantic_project.root,
            "semantic_model": model_name,
        },
    )
    return frame


def _persist_known_datasources(session: Session) -> None:
    meta = read_session_meta(session.layout)
    meta["known_datasources"] = sorted(session.known_datasources)
    meta["updated_at"] = datetime.now(UTC).isoformat()
    write_session_meta(session.layout, meta)


def _commit_observe_metric_frame(
    *,
    session: Session,
    frame: MetricFrame,
    params: dict[str, Any],
    metric_id: str,
    model_name: str,
    stored_where: dict[str, Any],
    semantic_kind: str,
    subject_grain: Literal["hour", "day", "week", "month"] | None = None,
) -> MetricFrame:
    """Commit an observe MetricFrame through the evidence pipeline (shared tail)."""
    return cast(
        "MetricFrame",
        commit_result(
            store=session.evidence_store(),
            frames_dir=session.layout.frames_dir,
            frame=frame,
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors(
                values={"metric_id": metric_id, "model": model_name}
            ),
            subject=Subject(
                metric=metric_id,
                slice=stored_where or {},
                grain=subject_grain,
                analysis_axis=_analysis_axis_for_kind(semantic_kind),
            ),
            extractor_family="metric_frame",
        ),
    )


def _analysis_axis_for_kind(
    semantic_kind: str,
) -> Literal[
    "scalar",
    "time",
    "segment",
    "panel",
    "change",
    "decomposition",
    "correlation",
    "forecast",
    "anomaly",
]:
    """Map semantic_kind to the Subject.analysis_axis literal."""
    mapping: dict[
        str,
        Literal[
            "scalar",
            "time",
            "segment",
            "panel",
            "change",
            "decomposition",
            "correlation",
            "forecast",
            "anomaly",
        ],
    ] = {
        "scalar": "scalar",
        "time_series": "time",
        "segmented": "segment",
        "panel": "panel",
    }
    return mapping.get(semantic_kind, "scalar")
