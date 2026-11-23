"""Private Frame entry builders sharing the current pure source capture."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ibis.common.deferred import Deferred

from marivo._definition.graph import direct_refs
from marivo._definition.population_types import population_definition
from marivo._definition.types import (
    Owner,
)
from marivo._kernel.grouping import Grouping, grouping_identity_keys
from marivo._kernel.input_context import captured_context
from marivo._kernel.temporal import TimeScope
from marivo._kernel.validation import is_instance
from marivo.analysis.binding.graph import BoundGraph
from marivo.analysis.binding.time_guidance import (
    REPORT_CLOCK_GUIDANCE,
    partition_hour_candidates,
    partition_hour_guidance,
)
from marivo.analysis.core.frame_data import (
    FrameKeyDependency,
    LiteralFrameBinding,
    SourceFrameBinding,
)
from marivo.analysis.core.source_frame_schema import pending_schema
from marivo.analysis.dsl._frame_inputs import collect_frame_key_inputs, population_frame_inputs
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_factory import frame_from_expression
from marivo.analysis.dsl.population import (
    Population,
)
from marivo.analysis.dsl.source_capture import (
    ValueRow,
    _MetricInputs,
    capture_metrics,
    capture_population,
    capture_read,
    capture_values,
)
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.model.errors import reject
from marivo.analysis.model.frame_contracts import merge_frame_key_inputs
from marivo.analysis.model.value_names import value_name
from marivo.datasource.spi import DatasourceConnectionConfig
from marivo.semantic import (
    MetricExpression,
    RowExpression,
    TimeDimension,
)
from marivo.semantic.spi import (
    CompiledExpressionSidecar,
    CompiledSemanticState,
    DimensionExpression,
    FieldExpression,
    NumericMetric,
    Registry,
    RowPredicate,
    bind_field_in_state,
    bind_metric_in_state,
    bind_predicate_in_state,
    build_compiled_state,
    expression_role,
    validate_context,
)


def _capture(
    local: LocalFrameServices,
    graph: BoundGraph,
    state: CompiledSemanticState,
    connection: DatasourceConnectionConfig,
    frame_key_inputs: tuple[FrameKeyDependency, ...] = (),
) -> AnalysisFrame:
    schema = pending_schema(graph, state)
    return frame_from_expression(
        local,
        schema,
        SourceFrameBinding(
            graph,
            state,
            connection,
            merge_frame_key_inputs(frame_key_inputs, owner=local.owner),
        ),
    )


def _partition_clock_candidates(registry: Registry, value: object) -> tuple[str, ...]:
    if isinstance(value, TimeDimension):
        return partition_hour_candidates(registry, value.path)
    return ()


def _source_by(
    by: tuple[Deferred | DimensionExpression | Grouping, ...] | Population,
    state: CompiledSemanticState,
    *,
    project_root: Path,
    owner: Owner,
) -> tuple[Deferred | Grouping, ...] | Population:
    """Adapt typed public values only inside a source boundary with explicit scope."""
    if not isinstance(by, (tuple, Population)):
        reject(
            "an ordered tuple of classifications and optional CalendarAxis, or a complete Population",
            type(by).__name__,
            "Pass by=(scope.by(mv.grain('day')), field) or by=population.",
            "Session.observe.by",
            canonical_id="session.observe",
        )
    values = (by,) if isinstance(by, Population) else by
    result: list[Deferred | Grouping] = []
    for value in values:
        if isinstance(value, RowExpression):
            if expression_role(value) != "dimension":
                candidates = _partition_clock_candidates(state.registry, value)
                repair = "Use a Dimension for classification or scope.by(grain) for report time."
                if expression_role(value) == "time":
                    repair += (
                        " A TimeDimension supplies time= on a source reduction or a field predicate; "
                        "it is not an ordinary classification. "
                        + REPORT_CLOCK_GUIDANCE
                        + partition_hour_guidance(candidates)
                    )
                reject(
                    "ordinary classification dimensions",
                    repr(value),
                    repair,
                    "Session.observe.by",
                    canonical_id="session.observe",
                    candidates=candidates,
                )
            result.append(bind_field_in_state(value, state, project_root=project_root, owner=owner))
        elif isinstance(value, Grouping) or (isinstance(value, Population) and value is by):
            validate_context(captured_context(value), project_root=project_root, owner=owner)
            for identity, keys in grouping_identity_keys(value):
                entity = state.registry.entities.get(identity.removeprefix("entity:"))
                if entity is None or tuple(entity.primary_key) != keys:
                    reject(
                        "the captured complete primary-key interpretation",
                        identity,
                        "Rebuild the coordinate domain with the receiving scope's complete identity.",
                        "source capture",
                        canonical_id="session.observe",
                    )
            if isinstance(value, Grouping):
                result.append(value)
        elif isinstance(value, Deferred):
            result.append(value)
        else:
            reject(
                "classification fields or grouping rules and at most one CalendarAxis",
                type(value).__name__,
                "Put a report TimeScope in where and pass an entire Population directly in by.",
                "Session.observe.by",
                canonical_id="session.observe",
            )
    return by if isinstance(by, Population) else tuple(result)


def observe(
    local: LocalFrameServices,
    state: CompiledSemanticState,
    connection: DatasourceConnectionConfig,
    metrics: Mapping[str, Deferred | NumericMetric] | _MetricInputs,
    *,
    by: tuple[Deferred | DimensionExpression | Grouping, ...] | Population = (),
    where: Deferred | RowPredicate | None = None,
    scope: TimeScope | None = None,
    timezone: str = "UTC",
    _captured_frame_inputs: tuple[FrameKeyDependency, ...] = (),
) -> AnalysisFrame:
    selected = tuple(metrics.items()) if isinstance(metrics, Mapping) else metrics
    original_frame_inputs = collect_frame_key_inputs(
        *(value for _, value in selected), where, owner=local.owner
    )
    inputs = tuple(
        (
            name,
            bind_metric_in_state(
                value, state, project_root=connection.project_root, owner=local.owner
            )
            if isinstance(value, MetricExpression)
            else value,
        )
        for name, value in selected
    )
    bound_by = _source_by(by, state, project_root=connection.project_root, owner=local.owner)
    bound_where = (
        bind_predicate_in_state(
            where, state, project_root=connection.project_root, owner=local.owner
        )
        if isinstance(where, RowExpression)
        else where
    )
    frame_inputs = collect_frame_key_inputs(
        *(metric for _, metric in inputs),
        bound_where,
        by if isinstance(by, Population) else None,
        owner=local.owner,
    )
    graph = capture_metrics(
        inputs,
        local.owner,
        by=bound_by,
        where=bound_where,
        timezone=timezone,
        state=state,
        scope=scope,
    )
    return _capture(
        local,
        graph,
        state,
        connection,
        merge_frame_key_inputs(
            _captured_frame_inputs, original_frame_inputs, frame_inputs, owner=local.owner
        ),
    )


def read(
    local: LocalFrameServices,
    state: CompiledSemanticState,
    connection: DatasourceConnectionConfig,
    population: Population,
    fields: Mapping[str, Deferred | FieldExpression],
    *,
    on: Grouping | None = None,
) -> AnalysisFrame:
    validate_context(
        captured_context(population), project_root=connection.project_root, owner=local.owner
    )
    if isinstance(on, Grouping):
        validate_context(
            captured_context(on), project_root=connection.project_root, owner=local.owner
        )
    bound_fields = {
        name: bind_field_in_state(
            value, state, project_root=connection.project_root, owner=local.owner
        )
        if isinstance(value, RowExpression)
        else value
        for name, value in fields.items()
    }
    graph = capture_read(population, bound_fields, local.owner, on=on)
    for name in fields:
        value_name(name, location="frame.read")
    return _capture(
        local,
        graph,
        state,
        connection,
        population_frame_inputs(population),
    )


def population_needs_state(population: Population) -> bool:
    """Resolve a Catalog only when the captured domain consumes definitions."""

    return bool(direct_refs(population_definition(population)))


def frame(
    local: LocalFrameServices,
    state: CompiledSemanticState | None,
    connection: DatasourceConnectionConfig,
    population: Population,
) -> AnalysisFrame:
    validate_context(
        captured_context(population), project_root=connection.project_root, owner=local.owner
    )
    if state is None:
        state = build_compiled_state(
            registry=Registry(),
            sidecar=CompiledExpressionSidecar({}, {}, frozenset()),
            selected_root_roles=(),
            filtered_domains=(),
        )
    return _capture(
        local,
        capture_population(population, local.owner),
        state,
        connection,
        population_frame_inputs(population),
    )


def _validate_value_columns(columns: Mapping[str, str | None], rows: tuple[ValueRow, ...]) -> None:
    if not is_instance(columns, Mapping) or not columns:
        reject(
            "named columns and finite typed literal rows",
            repr((columns, rows)),
            "Provide at least one literal row to establish value kinds; filter the Frame when an empty typed result is needed.",
            "Session.values",
        )
    for name in columns:
        value_name(name, location="frame.values")


def values(
    local: LocalFrameServices, *, columns: Mapping[str, str | None], rows: tuple[ValueRow, ...]
) -> AnalysisFrame:
    """Capture zero-axis literal values without source configuration or scope."""
    _validate_value_columns(columns, rows)
    graph = capture_values(by=(), columns=columns, rows=rows, owner=local.owner)
    schema = pending_schema(graph, None)
    return frame_from_expression(local, schema, LiteralFrameBinding(graph))


def coordinate_values(
    local: LocalFrameServices,
    state: CompiledSemanticState,
    connection: DatasourceConnectionConfig,
    *,
    by: tuple[Deferred | Grouping, ...],
    columns: Mapping[str, str | None],
    rows: tuple[ValueRow, ...],
) -> AnalysisFrame:
    _validate_value_columns(columns, rows)
    return _capture(
        local,
        capture_values(by=by, columns=columns, rows=rows, owner=local.owner),
        state,
        connection,
    )
