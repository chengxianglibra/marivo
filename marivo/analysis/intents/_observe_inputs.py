"""Identity/digest, input normalization, and meta helpers for observe.

Internal to ``marivo.analysis.intents`` — extracted from ``observe``.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Mapping
from typing import Any, Literal

from marivo.analysis.errors import SemanticKindMismatchError
from marivo.analysis.intents._observe_catalog import (
    _build_entity_adapter,
    _catalog_object,
    _entity_details,
)
from marivo.analysis.intents.observe_planner import _planned_metric
from marivo.analysis.semantic_inputs import (
    normalize_dimension_input,
    normalize_metric_input,
    normalize_time_dimension_input,
)
from marivo.analysis.session.core import Session
from marivo.analysis.slice_types import SliceValue
from marivo.analysis.windows.spec import (
    AbsoluteWindow,
    GrainInput,
    TimeScopeInput,
    make_absolute_window,
    normalize_timescope_input,
)
from marivo.refs import FieldKind, MetricKind, Ref
from marivo.semantic.catalog import (
    DerivedMetricDetails,
    EntityDetails,
    SemanticKind,
    SimpleMetricDetails,
)


def _gen_ref(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def _params_digest(params: dict[str, Any]) -> str:
    body = json.dumps(params, sort_keys=True, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _resolve_timescope(
    timescope: TimeScopeInput,
    *,
    grain: GrainInput,
    time_dimension: str | None,
) -> tuple[AbsoluteWindow | None, dict[str, Any] | None]:
    timescope_in = normalize_timescope_input(timescope)
    resolved = make_absolute_window(timescope_in, grain=grain, time_dimension=time_dimension)
    original = timescope_in.model_dump(mode="json") if timescope_in is not None else None
    return resolved, original


def _validate_dimension_ids(dimensions: list[str] | None) -> list[str]:
    if dimensions is None:
        return []

    seen: set[str] = set()
    duplicate_ids: set[str] = set()
    for dimension in dimensions:
        if dimension in seen:
            duplicate_ids.add(dimension)
        seen.add(dimension)
    if duplicate_ids:
        raise SemanticKindMismatchError(
            message="observe dimensions must not contain duplicate dimension ids",
            context={
                "expected_kind": "unique dimension ids",
                "got_kind": "duplicate dimension ids",
                "duplicate_dimensions": sorted(duplicate_ids),
            },
        )
    return dimensions


class _Result:
    """Minimal result holder used by _execute_base and _execute_derived."""

    def __init__(self, df: Any) -> None:
        self.df = df
        self.row_count = len(df)


def _dump_dimensions(dimensions: list[str] | None) -> list[dict[str, Any]] | None:
    if dimensions is None:
        return None
    return [{"semantic_id": dimension} for dimension in dimensions]


def _backend_for_datasource(session: Session, datasource_name: str) -> tuple[str, Any]:
    return datasource_name, session._connection_runtime.get_or_create(datasource_name)


def _entity_adapter_maps(
    *,
    catalog: Any,
    resolver: Any,
    entity_refs: set[str],
) -> tuple[dict[str, EntityDetails], dict[str, Any], dict[str, Any], dict[str, Any]]:
    entity_details = {
        entity_ref: _entity_details(catalog, entity_ref) for entity_ref in entity_refs
    }
    dataset_irs = {
        entity_ref: _build_entity_adapter(catalog, resolver, entity)
        for entity_ref, entity in entity_details.items()
    }
    dataset_fns = {entity_ref: adapter.fn for entity_ref, adapter in dataset_irs.items()}
    return entity_details, {}, dataset_irs, dataset_fns


def _normalize_metric_boundary(catalog: Any, metric: Ref[MetricKind]) -> str:
    return normalize_metric_input(catalog, metric)


def _normalize_dimension_boundary(
    catalog: Any,
    dimension: Ref[FieldKind],
    *,
    argument: str,
    scoped_entity_refs: set[str] | None = None,
) -> str:
    return normalize_dimension_input(catalog, dimension, argument=argument)


def _normalize_dimension_list_boundary(
    catalog: Any,
    dimensions: list[Ref[FieldKind]] | None,
    *,
    scoped_entity_refs: set[str],
) -> list[str] | None:
    if dimensions is None:
        return None
    return [
        _normalize_dimension_boundary(
            catalog,
            dimension,
            argument="dimensions",
            scoped_entity_refs=scoped_entity_refs,
        )
        for dimension in dimensions
    ]


def _normalize_where_boundary(
    catalog: Any,
    where: Mapping[Ref[FieldKind], SliceValue] | None,
    *,
    scoped_entity_refs: set[str],
) -> dict[str, SliceValue]:
    if where is None:
        return {}
    return {
        _normalize_dimension_boundary(
            catalog,
            key,
            argument="slice_by",
            scoped_entity_refs=scoped_entity_refs,
        ): value
        for key, value in where.items()
    }


def _normalize_time_dimension_boundary(catalog: Any, time_dimension: Any) -> str:
    return normalize_time_dimension_input(catalog, time_dimension)


def _metric_planner_scope(catalog: Any, metric_ir: Any) -> set[str]:
    scoped = set(metric_ir.entities)
    root = getattr(metric_ir, "root_entity", None)
    if isinstance(root, str) and root:
        scoped.add(root)
    if metric_ir.metric_type == "derived":
        for component_id in metric_ir.composition.components.values():
            component_details = _catalog_object(
                catalog, component_id, SemanticKind.METRIC
            ).details()
            if isinstance(component_details, (SimpleMetricDetails, DerivedMetricDetails)):
                component_ir = _planned_metric(component_details)
                scoped.update(component_ir.entities)
                component_root = getattr(component_ir, "root_entity", None)
                if isinstance(component_root, str) and component_root:
                    scoped.add(component_root)
    return scoped


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


def _metric_expr(
    catalog: Any,
    resolver: Any,
    metric_id: str,
    metric_datasets: tuple[str, ...],
    dataset_tables: dict[str, Any],
    *,
    metric_ir: Any | None = None,
) -> Any:
    runtime_measure_id = getattr(metric_ir, "runtime_measure_id", None)
    if isinstance(runtime_measure_id, str):
        assert metric_ir is not None
        return resolver.aggregate_measure_on(
            Ref.measure(runtime_measure_id),
            dataset_tables[metric_datasets[0]],
            metric_ir.aggregation,
        )
    return resolver.metric_on(
        _catalog_object(catalog, metric_id, SemanticKind.METRIC).ref,
        *(dataset_tables[dataset_name] for dataset_name in metric_datasets),
    )
