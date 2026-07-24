"""Identity/digest, input normalization, and meta helpers for observe.

Internal to ``marivo.analysis.intents`` — extracted from ``observe``.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Mapping
from typing import Any, Literal

from marivo.analysis.errors import (
    AnalysisRepair,
    GrainUnsupportedError,
    SemanticKindMismatchError,
    TemporalSuitabilityError,
)
from marivo.analysis.intents._observe_catalog import (
    _build_entity_adapter,
    _catalog_object,
    _entity_details,
)
from marivo.analysis.intents._runtime_metric_lowering import lower_metric_inputs
from marivo.analysis.intents.observe_planner import _planned_metric
from marivo.analysis.semantic_inputs import (
    normalize_dimension_input,
    normalize_metric_input,
    normalize_time_dimension_input,
    normalize_where_inputs,
)
from marivo.analysis.session.core import Session
from marivo.analysis.slice_types import SliceValue
from marivo.analysis.windows.grain import Grain, ensure_grain_supported
from marivo.analysis.windows.spec import (
    AbsoluteWindow,
    GrainInput,
    TimeScopeInput,
    make_absolute_window,
    normalize_timescope_input,
)
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import DimensionKind, MetricKind, Ref, TimeDimensionKind
from marivo.refs import ref as ref_factory
from marivo.semantic.catalog import (
    CatalogEntry,
    DerivedMetricDetails,
    EntityDetails,
    SemanticKind,
    SimpleMetricDetails,
    TimeDimensionDetails,
    _SemanticInput,
)
from marivo.semantic.ir import SemiAdditive
from marivo.semantic.runtime_metric import RuntimeMetricExpr, runtime_metric_leaf_refs


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


def _normalize_metric_boundary(catalog: Any, metric: _SemanticInput[MetricKind]) -> str:
    return normalize_metric_input(catalog, metric)


def _normalize_dimension_boundary(
    catalog: Any,
    dimension: _SemanticInput[DimensionKind | TimeDimensionKind],
    *,
    argument: str,
    scoped_entity_refs: set[str] | None = None,
) -> str:
    return normalize_dimension_input(catalog, dimension, argument=argument)


def _normalize_dimension_list_boundary(
    catalog: Any,
    dimensions: list[_SemanticInput[DimensionKind | TimeDimensionKind]] | None,
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
    where: Mapping[_SemanticInput[DimensionKind | TimeDimensionKind], SliceValue] | None,
    *,
    scoped_entity_refs: set[str],
) -> dict[str, SliceValue]:
    return normalize_where_inputs(catalog, where)


def _normalize_time_dimension_boundary(
    catalog: Any,
    time_dimension: _SemanticInput[TimeDimensionKind],
) -> str:
    received_ref = (
        time_dimension.ref if isinstance(time_dimension, CatalogEntry) else time_dimension
    )
    if type(received_ref) is Ref and received_ref.kind is SemanticKind.DIMENSION:
        # Preserve an exact ordinary dimension long enough for the temporal
        # suitability preflight to explain why it cannot act as a time axis.
        # All other invalid forms retain the stricter time-dimension boundary.
        return normalize_dimension_input(catalog, time_dimension, argument="time_dimension")
    return normalize_time_dimension_input(catalog, time_dimension)


def _temporal_candidates(
    catalog: Any,
    metric_inputs: tuple[Ref[MetricKind] | RuntimeMetricExpr, ...],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    candidates: dict[str, tuple[str, ...]] = {}
    preferred: dict[str, tuple[str, ...]] = {}
    registry = catalog._require_index().registry
    for index, metric_input in enumerate(metric_inputs):
        if type(metric_input) is Ref:
            metric_id = metric_input.path
            details = _catalog_object(catalog, metric_id, SemanticKind.METRIC).details()
            assert isinstance(details, (SimpleMetricDetails, DerivedMetricDetails))
            candidates[metric_id] = tuple(ref.path for ref in details.candidate_time_dimensions)
            preferred[metric_id] = (
                (details.status_time_dimension,)
                if details.status_time_dimension is not None
                else ()
            )
            continue

        root_id = f"runtime_root[{index}]"
        root_candidates: list[str] = []
        root_preferred: list[str] = []
        for leaf_ref in runtime_metric_leaf_refs(metric_input):
            if leaf_ref.kind is SemanticKind.METRIC:
                details = _catalog_object(
                    catalog,
                    leaf_ref.path,
                    SemanticKind.METRIC,
                ).details()
                assert isinstance(details, (SimpleMetricDetails, DerivedMetricDetails))
                for candidate in details.candidate_time_dimensions:
                    if candidate.path not in root_candidates:
                        root_candidates.append(candidate.path)
                if (
                    details.status_time_dimension is not None
                    and details.status_time_dimension not in root_preferred
                ):
                    root_preferred.append(details.status_time_dimension)
            elif leaf_ref.kind is SemanticKind.MEASURE:
                measure = registry.measures.get(leaf_ref.path)
                if measure is None:
                    continue
                for dimension in registry.dimensions.values():
                    if (
                        dimension.entity == measure.entity
                        and dimension.is_time_dimension
                        and dimension.semantic_id not in root_candidates
                    ):
                        root_candidates.append(dimension.semantic_id)
                if (
                    isinstance(measure.additivity, SemiAdditive)
                    and measure.additivity.over not in root_preferred
                ):
                    root_preferred.append(measure.additivity.over)
        candidates[root_id] = tuple(root_candidates)
        preferred[root_id] = tuple(root_preferred)
    return candidates, preferred


def _time_dimension_details(catalog: Any, path: str) -> TimeDimensionDetails:
    details = _catalog_object(catalog, path, SemanticKind.TIME_DIMENSION).details()
    assert isinstance(details, TimeDimensionDetails)
    return details


def _temporal_repair_context(
    *,
    catalog: Any,
    metric_ids: tuple[str, ...],
    candidates_by_metric: dict[str, tuple[str, ...]],
    supplied_axis: str | None,
) -> dict[str, object]:
    return {
        "capability": "session.observe temporal analysis",
        "metric_roots": metric_ids,
        "supplied_axis": supplied_axis,
        "required_semantic_kind": SemanticKind.TIME_DIMENSION.value,
        "candidate_time_dimensions": candidates_by_metric,
        "catalog_definition_fingerprint": catalog.definition_fingerprint,
    }


def _semantic_authoring_temporal_repair(
    *,
    catalog: Any,
    action: str,
    candidates: tuple[str, ...] = (),
) -> AnalysisRepair:
    return AnalysisRepair(
        kind="semantic_authoring",
        action=f"{action} Current catalog fingerprint: {catalog.definition_fingerprint}.",
        help_target=LiveHelpTarget(surface="semantic", canonical_id="readiness"),
        candidates=tuple(f"time_dimension:{candidate}" for candidate in candidates),
    )


def _metric_expression(metric_ids: tuple[str, ...]) -> str:
    expressions = [f'session.catalog.metrics.get("{metric_id}")' for metric_id in metric_ids]
    if len(expressions) == 1:
        return expressions[0]
    return f"[{', '.join(expressions)}]"


def _grain_retry_repair(
    *,
    metric_ids: tuple[str, ...],
    window: AbsoluteWindow,
    axis: str,
    grain: Grain,
    include_time_dimension: bool,
) -> AnalysisRepair:
    metric_expression = _metric_expression(metric_ids)
    snippet_lines = [
        f"frame = session.observe({metric_expression},\n"
        f'    time_scope={{"start": "{window.start}", "end": "{window.end}"}},\n'
        f'    grain="{grain.to_token()}",'
    ]
    if include_time_dimension:
        snippet_lines.append(f'    time_dimension=session.catalog.time_dimensions.get("{axis}"))')
    else:
        snippet_lines[-1] = f"{snippet_lines[-1][:-1]})"
    snippet = "\n".join(snippet_lines)
    return AnalysisRepair(
        kind="retry",
        action=(
            f"Retry the same observation with the mechanically supported "
            f"{grain.to_token()!r} grain on {axis!r}."
        ),
        help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
        snippet=snippet,
        candidates=(f"time_dimension:{axis}",),
    )


def _grain_incompatibility_repair(
    *,
    metric_ids: tuple[str, ...],
    window: AbsoluteWindow,
    axis: str,
    grain: Grain,
    allow_mechanical_retry: bool,
) -> AnalysisRepair:
    if allow_mechanical_retry:
        return _grain_retry_repair(
            metric_ids=metric_ids,
            window=window,
            axis=axis,
            grain=grain,
            include_time_dimension=True,
        )
    return AnalysisRepair(
        kind="inspect",
        action=(
            "Inspect every metric root's selected time dimension and choose one "
            "grain supported by the complete metric forest."
        ),
        help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
        candidates=(f"time_dimension:{axis}",),
    )


def _selected_candidate(
    catalog: Any,
    *,
    candidates: tuple[str, ...],
    preferred: tuple[str, ...],
) -> str | None:
    valid_preferred = tuple(
        dict.fromkeys(candidate for candidate in preferred if candidate in candidates)
    )
    if len(valid_preferred) == 1:
        return valid_preferred[0]
    if len(valid_preferred) > 1:
        return None
    defaults = [
        candidate
        for candidate in candidates
        if _time_dimension_details(catalog, candidate).is_default
    ]
    if len(defaults) == 1:
        return defaults[0]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _validate_compiled_time_encoding(
    catalog: Any,
    *,
    metric_ids: tuple[str, ...],
    retry_metric_ids: tuple[str, ...] | None,
    window: AbsoluteWindow,
    axis: str,
    include_time_dimension_in_retry: bool,
) -> None:
    requested_grain = window.grain
    if requested_grain is None:
        return
    details = _time_dimension_details(catalog, axis)
    base = details.granularity
    context = {
        "metric_roots": metric_ids,
        "time_dimension": axis,
        "requested_grain": requested_grain.to_token(),
        "base_granularity": base,
        "parse_kind": details.parse_kind,
        "data_type": details.data_type,
        "catalog_definition_fingerprint": catalog.definition_fingerprint,
    }
    if base is None:
        raise TemporalSuitabilityError(
            message=(
                f"time dimension {axis!r} has no declared granularity, so Marivo cannot "
                f"prove that grain {requested_grain.to_token()!r} is executable"
            ),
            expected="a time dimension with a supported declared granularity",
            received=f"time_dimension:{axis}",
            location="session.observe grain",
            repair=_semantic_authoring_temporal_repair(
                catalog=catalog,
                action=(
                    f"Author and verify the granularity for time dimension {axis!r} "
                    "before retrying temporal analysis."
                ),
                candidates=(axis,),
            ),
            context=context,
        )

    base_grain: Grain
    try:
        base_grain = Grain(count=1, unit=base)  # type: ignore[arg-type]
        ensure_grain_supported(requested_grain, base)
    except (TypeError, ValueError):
        raise TemporalSuitabilityError(
            message=(f"time dimension {axis!r} declares unsupported granularity {base!r}"),
            expected="year, quarter, month, week, day, hour, minute, or second",
            received=repr(base),
            location="session.observe time dimension encoding",
            repair=_semantic_authoring_temporal_repair(
                catalog=catalog,
                action=(
                    f"Repair and verify the granularity declaration for time dimension "
                    f"{axis!r} before retrying."
                ),
                candidates=(axis,),
            ),
            context=context,
        ) from None
    except GrainUnsupportedError as exc:
        raise GrainUnsupportedError(
            message=exc.message,
            expected=f"grain {base_grain.to_token()!r} or coarser",
            received=requested_grain.to_token(),
            location="session.observe grain",
            repair=_grain_incompatibility_repair(
                metric_ids=retry_metric_ids or metric_ids,
                window=window,
                axis=axis,
                grain=base_grain,
                allow_mechanical_retry=(
                    include_time_dimension_in_retry and retry_metric_ids is not None
                ),
            ),
            context=context,
        ) from exc

    encoding_is_date = details.parse_kind == "date" or details.data_type == "date"
    if encoding_is_date and requested_grain.is_subday:
        if base_grain.is_subday:
            raise TemporalSuitabilityError(
                message=(
                    f"time dimension {axis!r} has date encoding but declares sub-day "
                    f"granularity {base!r}"
                ),
                expected="a datetime/timestamp encoding for sub-day analysis",
                received=f"date encoding at {base!r} granularity",
                location="session.observe time dimension encoding",
                repair=_semantic_authoring_temporal_repair(
                    catalog=catalog,
                    action=(
                        f"Repair the encoding or granularity of time dimension {axis!r} "
                        "and verify semantic readiness before retrying."
                    ),
                    candidates=(axis,),
                ),
                context=context,
            )
        raise GrainUnsupportedError(
            message=(
                f"requested grain {requested_grain.to_token()!r} requires time-of-day "
                f"resolution, but time dimension {axis!r} has date encoding"
            ),
            expected=f"grain {base_grain.to_token()!r} or coarser",
            received=requested_grain.to_token(),
            location="session.observe grain",
            repair=_grain_incompatibility_repair(
                metric_ids=retry_metric_ids or metric_ids,
                window=window,
                axis=axis,
                grain=base_grain,
                allow_mechanical_retry=(
                    include_time_dimension_in_retry and retry_metric_ids is not None
                ),
            ),
            context=context,
        )


def _preflight_observe_temporal_suitability(
    catalog: Any,
    *,
    metric_inputs: tuple[Ref[MetricKind] | RuntimeMetricExpr, ...],
    resolved_window: AbsoluteWindow | None,
    supplied_time_dimension: str | None,
) -> None:
    """Reject compiled temporal incompatibilities before runtime acquisition."""

    if resolved_window is None or not metric_inputs:
        return
    if any(type(metric_input) is not Ref for metric_input in metric_inputs):
        lower_metric_inputs(
            catalog._require_index().registry,
            metric_inputs,
            sidecar=catalog._state.sidecar,
        )
    candidates_by_metric, preferred_by_metric = _temporal_candidates(
        catalog,
        metric_inputs,
    )
    metric_ids = tuple(candidates_by_metric)
    catalog_metric_ids = tuple(
        metric_input.path for metric_input in metric_inputs if type(metric_input) is Ref
    )
    retry_metric_ids = catalog_metric_ids if len(catalog_metric_ids) == len(metric_inputs) else None
    context = _temporal_repair_context(
        catalog=catalog,
        metric_ids=metric_ids,
        candidates_by_metric=candidates_by_metric,
        supplied_axis=supplied_time_dimension,
    )

    if supplied_time_dimension is not None:
        registry_dimension = catalog._require_index().registry.dimensions.get(
            supplied_time_dimension
        )
        if registry_dimension is not None and not registry_dimension.is_time_dimension:
            all_candidates = tuple(
                dict.fromkeys(
                    candidate
                    for candidates in candidates_by_metric.values()
                    for candidate in candidates
                )
            )
            raise TemporalSuitabilityError(
                message=(
                    f"ordinary dimension {supplied_time_dimension!r} cannot be used as "
                    "session.observe's temporal axis"
                ),
                expected="time_dimension",
                received=f"dimension:{supplied_time_dimension}",
                location="session.observe time_dimension",
                repair=_semantic_authoring_temporal_repair(
                    catalog=catalog,
                    action=(
                        f"Do not reinterpret ordinary dimension {supplied_time_dimension!r}; "
                        "author and verify the required time dimension before retrying."
                    ),
                    candidates=all_candidates,
                ),
                context=context,
            )

    missing = tuple(
        metric_id for metric_id, candidates in candidates_by_metric.items() if not candidates
    )
    if missing:
        raise TemporalSuitabilityError(
            message=(
                "temporal observation requires a candidate time dimension, but none is "
                f"available for metric roots {list(missing)!r}"
            ),
            expected="at least one candidate time dimension per metric root",
            received="none",
            location="session.observe temporal preflight",
            repair=_semantic_authoring_temporal_repair(
                catalog=catalog,
                action=(
                    f"Author and verify a time dimension reachable from metric roots "
                    f"{list(missing)!r} before retrying temporal analysis."
                ),
            ),
            context=context,
        )

    explicit_axis = resolved_window.time_dimension
    if explicit_axis is not None:
        incompatible_roots = tuple(
            metric_id
            for metric_id, candidates in candidates_by_metric.items()
            if explicit_axis not in candidates
        )
        if incompatible_roots:
            exact_candidates = tuple(
                f"{metric_id} -> {candidate}"
                for metric_id in incompatible_roots
                for candidate in candidates_by_metric[metric_id]
            )
            raise TemporalSuitabilityError(
                message=(
                    f"time dimension {explicit_axis!r} is not a candidate for metric roots "
                    f"{list(incompatible_roots)!r}"
                ),
                expected="an exact candidate time dimension for every metric root",
                received=f"time_dimension:{explicit_axis}",
                location="session.observe time_dimension",
                repair=AnalysisRepair(
                    kind="inspect",
                    action=(
                        "Inspect the per-root candidate time dimensions and choose an "
                        "explicit axis only when it is valid for the complete metric forest."
                    ),
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
                    candidates=exact_candidates,
                ),
                context=context,
            )
        _validate_compiled_time_encoding(
            catalog,
            metric_ids=metric_ids,
            retry_metric_ids=retry_metric_ids,
            window=resolved_window,
            axis=explicit_axis,
            include_time_dimension_in_retry=True,
        )
        return

    selected_by_metric = {
        metric_id: _selected_candidate(
            catalog,
            candidates=candidates,
            preferred=preferred_by_metric[metric_id],
        )
        for metric_id, candidates in candidates_by_metric.items()
    }
    ambiguous = tuple(
        metric_id for metric_id, selected in selected_by_metric.items() if selected is None
    )
    if ambiguous:
        exact_candidates = tuple(
            f"{metric_id} -> time_dimension:{candidate}"
            for metric_id in ambiguous
            for candidate in candidates_by_metric[metric_id]
        )
        raise TemporalSuitabilityError(
            message=(
                "metric roots require an explicit time_dimension because their temporal "
                f"candidates are ambiguous: {list(ambiguous)!r}"
            ),
            expected="one explicit current-catalog time dimension",
            received="no explicit time_dimension",
            location="session.observe time_dimension",
            repair=AnalysisRepair(
                kind="inspect",
                action=(
                    "Inspect the exact per-root candidates below; do not choose a time "
                    "axis without confirming the intended temporal meaning."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
                candidates=exact_candidates,
            ),
            context=context,
        )

    selected_axes = tuple(dict.fromkeys(selected_by_metric.values()))
    if len(selected_axes) > 1:
        exact_candidates = tuple(
            f"{metric_id} -> time_dimension:{candidate}"
            for metric_id, candidates in candidates_by_metric.items()
            for candidate in candidates
        )
        raise TemporalSuitabilityError(
            message=(
                "metric roots resolve to different implicit time dimensions and cannot "
                "share one temporal result axis"
            ),
            expected="one shared time dimension for the complete metric forest",
            received="different implicit time dimensions",
            location="session.observe time_dimension",
            repair=AnalysisRepair(
                kind="inspect",
                action=(
                    "Inspect the exact per-root candidates below; only combine metric "
                    "roots when one time dimension is valid for the complete forest."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
                candidates=exact_candidates,
            ),
            context=context,
        )
    for selected in selected_axes:
        assert selected is not None
        _validate_compiled_time_encoding(
            catalog,
            metric_ids=metric_ids,
            retry_metric_ids=retry_metric_ids,
            window=resolved_window,
            axis=selected,
            include_time_dimension_in_retry=len(selected_axes) == 1,
        )


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
            ref_factory.measure(runtime_measure_id),
            dataset_tables[metric_datasets[0]],
            metric_ir.aggregation,
        )
    return resolver.metric_on(
        _catalog_object(catalog, metric_id, SemanticKind.METRIC).ref,
        *(dataset_tables[dataset_name] for dataset_name in metric_datasets),
    )
