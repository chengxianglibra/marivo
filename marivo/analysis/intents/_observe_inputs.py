"""Identity/digest, input normalization, and meta helpers for observe.

Internal to ``marivo.analysis.intents`` — extracted from ``observe``.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Mapping
from datetime import date, datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo

from marivo._temporal import Grain as TemporalGrain
from marivo._temporal import TemporalSetSnapshotStore
from marivo.analysis.errors import (
    AnalysisRepair,
    GrainUnsupportedError,
    SemanticKindMismatchError,
    TemporalSuitabilityError,
    WindowInvalidError,
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
    bind_temporal_set_window,
    bind_temporal_window,
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
from marivo.semantic.ir import (
    CumulativeComposition,
    LinearComposition,
    RatioComposition,
    SemiAdditive,
)
from marivo.semantic.runtime_metric import (
    RuntimeAggregateExpr,
    RuntimeLinearExpr,
    RuntimeMetricExpr,
    RuntimeRatioExpr,
    RuntimeSliceExpr,
    RuntimeWeightedMeanExpr,
    runtime_metric_leaf_refs,
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
    catalog: Any | None = None,
) -> tuple[AbsoluteWindow | None, dict[str, Any] | None]:
    timescope_in = normalize_timescope_input(timescope)
    resolved = make_absolute_window(timescope_in, grain=grain, time_dimension=time_dimension)
    if catalog is not None and resolved is not None:
        semantic_grain = resolved.grain
        semantic_scope = resolved.semantic_scope
        calendar_ref = (
            semantic_grain.calendar
            if isinstance(semantic_grain, TemporalGrain) and semantic_grain.kind == "semantic"
            else semantic_scope.calendar
            if semantic_scope is not None and semantic_scope.kind == "calendar_period"
            else None
        )
        if calendar_ref is not None:
            from marivo.refs import SemanticKind

            if calendar_ref.kind is not SemanticKind.PERIOD_CALENDAR:
                raise TypeError("temporal analysis requires Ref[period_calendar]")
            calendar = catalog.period_calendars.get(calendar_ref.path)
            snapshot = calendar._snapshot()
            if (
                isinstance(semantic_grain, TemporalGrain)
                and semantic_grain.kind == "semantic"
                and semantic_grain.level not in snapshot.levels
            ):
                raise ValueError(
                    f"calendar level {semantic_grain.level!r} is not certified by {calendar_ref.path!r}"
                )
            if (
                semantic_scope is not None
                and semantic_scope.kind == "calendar_period"
                and semantic_scope.snapshot_digest != snapshot.snapshot_digest
            ):
                raise ValueError(
                    "time_scope belongs to a stale period-calendar snapshot; reacquire the exact current scope"
                )
            if semantic_scope is not None and semantic_scope.kind == "calendar_period":
                try:
                    exact_scope = snapshot.period_scope(
                        semantic_scope.level or "",
                        semantic_scope.key,
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise WindowInvalidError(
                        message="time_scope period key is not present in its certified snapshot",
                        context={
                            "kind": "TemporalPeriodMissing",
                            "calendar_ref": calendar_ref.path,
                            "level": semantic_scope.level,
                            "key": semantic_scope.key,
                        },
                    ) from exc
                if (
                    exact_scope.start != semantic_scope.start
                    or exact_scope.end != semantic_scope.end
                    or exact_scope.calendar != semantic_scope.calendar
                    or exact_scope.snapshot_digest != semantic_scope.snapshot_digest
                    or exact_scope.boundary_timezone != semantic_scope.boundary_timezone
                    or exact_scope.level != semantic_scope.level
                    or exact_scope.key != semantic_scope.key
                ):
                    raise WindowInvalidError(
                        message="time_scope period bounds do not match its certified period",
                        context={
                            "kind": "TemporalPeriodBindingMismatch",
                            "calendar_ref": calendar_ref.path,
                            "level": semantic_scope.level,
                            "key": semantic_scope.key,
                        },
                    )
                resolved = resolved.model_copy(update={"semantic_scope": exact_scope})
            _validate_semantic_window_coverage(resolved, snapshot=snapshot)
            resolved = bind_temporal_window(resolved, snapshot=snapshot)
        if semantic_scope is not None and semantic_scope.kind == "temporal_occurrence":
            from marivo.refs import SemanticKind

            temporal_set_ref = semantic_scope.temporal_set
            if temporal_set_ref is None or temporal_set_ref.kind is not SemanticKind.TEMPORAL_SET:
                raise TypeError("temporal occurrence scope requires Ref[temporal_set]")
            if not semantic_scope.snapshot_digest:
                raise WindowInvalidError(
                    message="temporal occurrence scope has no snapshot digest",
                    context={"kind": "TemporalSetSnapshotMissing"},
                )
            if semantic_scope.key is None:
                raise WindowInvalidError(
                    message="temporal occurrence scope has no key",
                    context={"kind": "TemporalOccurrenceKeyMissing"},
                )
            try:
                temporal_set_snapshot = TemporalSetSnapshotStore(catalog.workspace_dir).load_exact(
                    temporal_set_ref,
                    snapshot_digest=semantic_scope.snapshot_digest,
                )
            except (KeyError, OSError, TypeError, ValueError) as exc:
                raise WindowInvalidError(
                    message="temporal occurrence scope requires its exact certified snapshot",
                    expected="the snapshot named by TimeScope.contract()",
                    received=repr(semantic_scope.snapshot_digest),
                    context={
                        "kind": "TemporalSetSnapshotUnavailable",
                        "temporal_set_ref": temporal_set_ref.path,
                        "snapshot_digest": semantic_scope.snapshot_digest,
                    },
                ) from exc
            try:
                exact_scope = temporal_set_snapshot.occurrence_scope(semantic_scope.key)
            except (KeyError, TypeError, ValueError) as exc:
                raise WindowInvalidError(
                    message="temporal occurrence scope key is not present in its certified snapshot",
                    context={"kind": "TemporalOccurrenceMissing", "key": semantic_scope.key},
                ) from exc
            if exact_scope != semantic_scope:
                raise WindowInvalidError(
                    message="temporal occurrence scope bounds do not match its certified occurrence",
                    context={
                        "kind": "TemporalOccurrenceBindingMismatch",
                        "key": semantic_scope.key,
                    },
                )
            resolved = bind_temporal_set_window(resolved, snapshot=temporal_set_snapshot)
    original = (
        timescope_in.contract().model_dump(mode="json")
        if timescope_in is not None and timescope_in.kind != "absolute"
        else timescope_in.model_dump(mode="json")
        if timescope_in is not None
        else None
    )
    return resolved, original


def _parse_calendar_bound(value: object, *, boundary_timezone: str) -> datetime:
    """Normalize one execution bound to the calendar's civil timeline."""
    raw = value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    try:
        if len(raw) == 10 and "T" not in raw and " " not in raw:
            parsed = datetime.combine(date.fromisoformat(raw), time.min)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WindowInvalidError(
            message=f"invalid semantic time_scope bound {value!r}",
            context={"kind": "SemanticTimeScopeBoundInvalid", "bound": repr(value)},
        ) from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo(boundary_timezone)).replace(tzinfo=None)
    return parsed


def _validate_semantic_window_coverage(
    window: AbsoluteWindow,
    *,
    snapshot: Any,
) -> None:
    """Reject semantic execution windows that exceed certified civil coverage."""
    start = _parse_calendar_bound(window.start, boundary_timezone=snapshot.boundary_timezone)
    end = _parse_calendar_bound(window.end, boundary_timezone=snapshot.boundary_timezone)
    coverage_start = datetime.combine(snapshot.coverage[0], time.min)
    coverage_end = datetime.combine(snapshot.coverage[1], time.min)
    if start < coverage_start or end > coverage_end or end <= start:
        raise WindowInvalidError(
            message="semantic time_scope is outside the certified period-calendar coverage",
            hint="Choose a scope fully contained by the certified calendar coverage.",
            context={
                "kind": "SemanticTimeScopeOutOfCoverage",
                "scope_start": window.start,
                "scope_end": window.end,
                "coverage_start": snapshot.coverage[0].isoformat(),
                "coverage_end": snapshot.coverage[1].isoformat(),
                "calendar": snapshot.calendar_ref.path,
            },
        )


def _bind_metric_temporal_context(
    catalog: Any,
    window: AbsoluteWindow | None,
    metric_ir: Any,
) -> AbsoluteWindow | None:
    """Bind every semantic cumulative reset in one metric graph to one snapshot."""
    reset_grains = _semantic_reset_grains_for_metric(catalog, metric_ir)
    if not reset_grains:
        return window
    if window is None:
        raise WindowInvalidError(
            message="semantic grain_to_date requires an explicit time_scope",
            hint="Pass a certified absolute or calendar-period time_scope before observing.",
            context={
                "kind": "SemanticResetTimeScopeMissing",
                "reset_grains": tuple(grain.to_token() for grain in reset_grains),
                "reset_grain": reset_grains[0].to_token(),
            },
        )

    snapshots = []
    for reset_grain in reset_grains:
        assert reset_grain.calendar is not None and reset_grain.level is not None
        calendar = catalog.period_calendars.get(reset_grain.calendar.path)
        snapshot = calendar._snapshot()
        if reset_grain.level not in snapshot.levels:
            raise ValueError(
                f"calendar level {reset_grain.level!r} is not certified by "
                f"{reset_grain.calendar.path!r}"
            )
        _validate_semantic_window_coverage(window, snapshot=snapshot)
        snapshots.append(snapshot)

    snapshot = snapshots[0]
    if any(candidate != snapshot for candidate in snapshots[1:]):
        raise ValueError("metric graph semantic resets use different period snapshots")
    if window.temporal_snapshot is not None and window.temporal_snapshot != snapshot:
        raise ValueError("observation and cumulative reset use different period snapshots")
    return bind_temporal_window(window, snapshot=snapshot)


def _semantic_reset_grains_for_metric(
    catalog: Any,
    metric_ir: Any,
) -> tuple[TemporalGrain, ...]:
    """Collect semantic grain-to-date resets across an authoritative metric graph."""
    try:
        registry_metrics = catalog._require_index().registry.metrics
    except AttributeError:
        registry_metrics = {}

    grains: list[TemporalGrain] = []
    seen: set[str] = set()

    def resolve_metric(value: Any) -> Any | None:
        if isinstance(value, str):
            return registry_metrics.get(value)
        return value

    def visit(value: Any) -> None:
        resolved = resolve_metric(value)
        metric_id = getattr(resolved, "semantic_id", None)
        if isinstance(metric_id, str):
            if metric_id in seen:
                return
            seen.add(metric_id)
            resolved = registry_metrics.get(metric_id, resolved)
        composition = getattr(resolved, "composition", None)
        if isinstance(composition, CumulativeComposition):
            anchor = composition.anchor
            if (
                isinstance(anchor, tuple)
                and len(anchor) == 2
                and anchor[0] == "grain_to_date"
                and isinstance(anchor[1], TemporalGrain)
                and anchor[1].kind == "semantic"
            ):
                grains.append(anchor[1])
            visit(composition.base)
            return
        if isinstance(composition, RatioComposition):
            visit(composition.numerator)
            visit(composition.denominator)
            return
        if isinstance(composition, LinearComposition):
            for term in composition.terms:
                visit(term.metric)
            return

        # Runtime/planner adapters may expose the same graph as a small
        # namespace instead of the authoritative IR classes.
        kind = getattr(composition, "kind", None)
        if kind == "cumulative":
            anchor_value: Any = getattr(composition, "anchor", None)
            if (
                isinstance(anchor_value, tuple)
                and len(anchor_value) == 2
                and anchor_value[0] == "grain_to_date"
                and isinstance(anchor_value[1], TemporalGrain)
                and anchor_value[1].kind == "semantic"
            ):
                grains.append(anchor_value[1])
            visit(getattr(composition, "base", None))
        elif kind == "ratio":
            components = getattr(composition, "components", {}) or {}
            visit(components.get("numerator"))
            visit(components.get("denominator"))
        elif kind == "linear":
            components = getattr(composition, "components", {}) or {}
            for component in components.values():
                visit(component)

    visit(metric_ir)
    return tuple(grains)


def _bind_metric_forest_temporal_context(
    catalog: Any,
    window: AbsoluteWindow | None,
    metric_inputs: tuple[Any, ...],
) -> AbsoluteWindow | None:
    """Bind semantic resets from every catalog root in a metric forest."""
    bound = window
    for metric_input in metric_inputs:
        refs: tuple[Any, ...]
        if type(metric_input) is Ref:
            refs = (metric_input,)
        elif isinstance(
            metric_input,
            RuntimeAggregateExpr
            | RuntimeSliceExpr
            | RuntimeRatioExpr
            | RuntimeWeightedMeanExpr
            | RuntimeLinearExpr,
        ):
            refs = tuple(
                ref
                for ref in runtime_metric_leaf_refs(metric_input)
                if getattr(ref, "kind", None) is SemanticKind.METRIC
            )
        else:
            refs = ()
        for metric_ref in refs:
            metric_id = metric_ref.path
            details = _catalog_object(catalog, metric_id, SemanticKind.METRIC).details()
            metric_ir = _planned_metric(details)
            bound = _bind_metric_temporal_context(catalog, bound, metric_ir)
    return bound


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
    grain_expression = (
        f'mv.grain("{grain.unit}"' + (f", count={grain.count}" if grain.count != 1 else "") + ")"
    )
    snippet_lines = [
        "import marivo.analysis as mv",
        f"frame = session.observe({metric_expression},\n"
        f'    time_scope=mv.time_scope(start="{window.start}", end="{window.end}"),\n'
        f"    grain={grain_expression},",
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
                + ("" if metric_id not in incompatible_roots else " [incompatible]")
                for metric_id in candidates_by_metric
                for candidate in candidates_by_metric[metric_id]
            )
            shared_candidates = set.intersection(
                *(set(candidates) for candidates in candidates_by_metric.values())
            )
            if shared_candidates:
                # A non-empty candidate intersection is necessary but not sufficient
                # for "omit time_dimension" to auto-select a shared axis: the omit
                # only converges when every root's implicit selection resolves to the
                # same axis. Reuse the implicit branch's selection rule to decide
                # whether the omit suggestion is actually executable.
                selected_by_metric = {
                    metric_id: _selected_candidate(
                        catalog,
                        candidates=candidates,
                        preferred=preferred_by_metric[metric_id],
                    )
                    for metric_id, candidates in candidates_by_metric.items()
                }
                selections = set(selected_by_metric.values())
                converged = None not in selections and len(selections) == 1
                if converged:
                    action = (
                        "Inspect the per-root candidate time dimensions listed below, "
                        "then choose an explicit axis valid for the complete metric "
                        "forest or omit time_dimension to auto-select a shared axis."
                    )
                else:
                    action = (
                        "Inspect the per-root candidate time dimensions listed below "
                        "and choose an explicit axis valid for the complete metric "
                        "forest."
                    )
            else:
                action = (
                    "Inspect the per-root candidate time dimensions listed below; no "
                    "single time dimension is valid for the complete metric forest, so "
                    "split the metrics into separate observe() calls grouped by a "
                    "shared time dimension."
                )
            raise TemporalSuitabilityError(
                message=(
                    f"time dimension {explicit_axis!r} is not a valid candidate for all "
                    f"metric roots; incompatible roots: {list(incompatible_roots)!r}"
                ),
                expected="an exact candidate time dimension for every metric root",
                received=f"time_dimension:{explicit_axis}",
                location="session.observe time_dimension",
                repair=AnalysisRepair(
                    kind="inspect",
                    action=action,
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
