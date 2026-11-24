"""Acquire and prepare invocation-local source relations."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from json import dumps
from typing import TYPE_CHECKING
from urllib.error import HTTPError

import ibis.expr.operations as ops
import ibis.expr.types as ir

if TYPE_CHECKING:
    from marivo.analysis.compiler.optimization import InvocationMemo

from marivo._definition.graph import computation_fingerprint
from marivo._definition.query import ResultQuery
from marivo._definition.relations import (
    AttributeKeys,
    FrameKeys,
    PopulationDependency,
    ReadQuery,
    finite_population_domain,
)
from marivo._definition.types import (
    AxisBinding,
    FieldRead,
)
from marivo._kernel.errors import MarivoError
from marivo.analysis.binding.graph import BoundGraph, read_input_graph
from marivo.analysis.compiler.lowered_query import PopulationCheck
from marivo.analysis.compiler.population_factorization import PopulationOptimizations
from marivo.analysis.compiler.populations import frame_key_groups
from marivo.analysis.compiler.query_program import FrameParameter, PopulationParameterPlan
from marivo.analysis.compiler.query_stages import (
    SourceField,
    SourceSchema,
)
from marivo.analysis.materialization import (
    query_parameters,
)
from marivo.analysis.materialization.graph_preflight import (
    _local_file,
    bind_entity_output,
)
from marivo.analysis.materialization.literal_preparation import literal_schemas
from marivo.analysis.materialization.native_calendar import bind_native_calendars
from marivo.analysis.materialization.population_domains import PreparedPopulation
from marivo.analysis.materialization.population_layout import population_field
from marivo.analysis.materialization.population_preparation import population_entities
from marivo.analysis.materialization.prepared_sources import PreparedSources
from marivo.analysis.materialization.query_contributions import prepare_contribution_reads
from marivo.analysis.materialization.query_preparation import (
    prepare_population,
)
from marivo.analysis.materialization.query_source_dependencies import (
    _query_entities,
    _query_populations,
)
from marivo.analysis.materialization.source_qualification import _route_gap
from marivo.analysis.materialization.source_types import (
    _admitted,
    _population_field,
)
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.data_types import (
    DataColumn,
)
from marivo.analysis.model.errors import reject
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.optimization_policy import (
    DEFAULT_OPTIMIZATION_POLICY,
    OptimizationPolicy,
)
from marivo.analysis.model.time_facts import FrameSourceTime, merge_source_times
from marivo.datasource.spi import (
    BoundSource,
    CsvSourceIR,
    DatasourceConnectionService,
    JsonResponseCache,
    JsonSourceIR,
    SourceSession,
    json_source_param_names,
    query_policy,
)
from marivo.semantic.spi import (
    CompiledSemanticState,
    normalize_target_entity,
)


def _bind_query_entities(
    graph: BoundGraph,
    state: CompiledSemanticState,
    available: Mapping[str, SourceSession],
    reuse_sources: PreparedSources | None,
) -> tuple[dict[str, ir.Table], dict[str, BoundSource], dict[str, SourceSession]]:
    registry, sidecar = state.registry, state.sidecar
    entities: dict[str, ir.Table] = {} if reuse_sources is None else dict(reuse_sources.entities)
    bounds: dict[str, BoundSource] = {} if reuse_sources is None else dict(reuse_sources.bound)
    owners: dict[str, SourceSession] = {} if reuse_sources is None else dict(reuse_sources.sources)
    for path in _query_entities(graph, state):
        if path in entities:
            continue
        contract = normalize_target_entity(registry, path)
        owner = available[contract.datasource_ref.path]
        try:
            bound = owner.bind(contract.source, source_identity=path)
        except MarivoError:
            raise
        except Exception as error:
            if not isinstance(
                contract.source, JsonSourceIR
            ) or not contract.source.path.lower().startswith(("http://", "https://")):
                raise
            raise MaterializationError(
                expected=f"{path}: a successful JSON response with the declared record and field paths",
                received=f"HTTP status {error.code}"
                if isinstance(error, HTTPError)
                else str(error)
                if isinstance(error, ValueError)
                else type(error).__name__,
                repair=f"Check {path}'s URL, GET/POST parameters, datasource credentials and response paths, then retry compute(); no result was completed or published.",
                stage="json_acquisition",
                help_target="analysis_frame.compute",
            ) from error
        entities[path] = bind_entity_output(
            registry, sidecar, path, owner, bound, contract.dependency_fingerprint
        )
        bounds[path] = bound
        owners[path] = owner
    return entities, bounds, owners


def _check_frame_parameter_types(
    parameters: tuple[PopulationParameterPlan, ...],
    entities: Mapping[str, ir.Table],
    bounds: Mapping[str, BoundSource],
    state: CompiledSemanticState,
) -> None:
    for parameter in parameters:
        if not isinstance(parameter, FrameParameter):
            continue
        key_definition = parameter.definition.root
        assert isinstance(key_definition, (FrameKeys, AttributeKeys))
        columns = {column.slot_id: column for column in parameter.columns}
        for axis, group in zip(
            key_definition.bindings,
            frame_key_groups(key_definition)[: len(key_definition.bindings)],
            strict=True,
        ):
            target = axis.target
            target_table = entities.get(target.entity.ref.path)
            if target_table is None:
                continue  # A keys-only projection has no new source read.
            values = (
                tuple(
                    target_table[name]
                    for name in state.registry.entities[target.entity.ref.path].primary_key
                )
                if target.field is None
                else (
                    population_field(
                        FieldRead(target.field, target.entity, target.path, target.at),
                        target_table,
                        state,
                    ),
                )
            )
            typed = target_table.select(**dict(zip(group, values, strict=True)))
            for name in group:
                expected = _population_field(typed, name, bounds)
                if expected.dtype != columns[name].dtype:
                    reject(
                        "compatible complete Frame and explicit target physical key types",
                        repr((columns[name].dtype, expected.dtype)),
                        "Select an axis with the target's exact physical type, Duration unit and timestamp interpretation. Explicitly unify naive and aware identity values in the Entity output; TimeDimension parse does not rewrite primary_key.",
                        "frame.population",
                    )


def _prepare_population_reads(
    populations: tuple[PopulationDependency, ...],
    state: CompiledSemanticState,
    source: SourceSession,
    entities: Mapping[str, ir.Table],
    bounds: Mapping[str, BoundSource],
    owners: dict[str, SourceSession],
    parameters: tuple[PopulationParameterPlan, ...],
    schemas: list[SourceSchema],
    relations: dict[str, ir.Table],
    source_times: list[FrameSourceTime],
    consumption_timezone: str,
    population_optimizations: PopulationOptimizations,
) -> dict[str, tuple[PopulationCheck, ...]]:
    population_checks: dict[str, tuple[PopulationCheck, ...]] = {}
    prepared_populations: dict[str, PreparedPopulation] = {}
    for population in populations:
        required = population_entities(population, state)
        datasources = {state.registry.entities[path].datasource for path in required}
        finite = finite_population_domain(population)
        if not finite and len(datasources) != 1 and not parameters:
            _route_gap(
                "one datasource for a source-driven Population",
                "multiple datasources",
                "Build independent source domains before combining their finite keys.",
            )
        prepared_population = prepare_population(
            population,
            entities,
            state,
            source_times=source_times,
            consumption_timezone=consumption_timezone,
            prepared=prepared_populations,
            population_optimizations=population_optimizations,
        )
        source_id = "population:" + computation_fingerprint(population)
        keys = tuple(
            _population_field(prepared_population.table, name, bounds)
            for name in prepared_population.key_columns
        )
        schemas.append(
            SourceSchema(
                source_id,
                keys,
                (),
                unique_keys=prepared_population.unique_keys,
                key_groups=prepared_population.key_groups,
                presence=prepared_population.presence,
            )
        )
        if not finite:
            relations[source_id] = prepared_population.table
            population_checks[source_id] = prepared_population.checks
            owners[source_id] = owners[required[0]] if required else source
    return population_checks


def _scope_source_schemas(
    schemas: list[SourceSchema],
    relations: Mapping[str, ir.Table],
    bounds: Mapping[str, BoundSource],
    population_checks: Mapping[str, tuple[PopulationCheck, ...]],
    populations: tuple[PopulationDependency, ...],
    parameters: tuple[PopulationParameterPlan, ...],
    scope_id: str,
) -> tuple[SourceSchema, ...]:
    own_schemas = []
    population_definitions = {
        "population:" + computation_fingerprint(population): population
        for population in populations
    }
    for schema in schemas:
        relation = (
            relations.get("read:" + ".".join(str(index) for index in schema.occurrence))
            if schema.occurrence is not None
            else relations.get(schema.source_id)
        )
        scopes = (() if relation is None else (relation,)) + tuple(
            check.relation for check in population_checks.get(schema.source_id, ())
        )
        names = {node.name for scope in scopes for node in scope.op().find(ops.UnboundTable)}
        inline = (
            population_definitions.get(schema.source_id)
            if relation is not None
            and not any(
                bound.relation.op() in set(relation.op().find(ops.Relation))
                for bound in bounds.values()
            )
            else None
        )
        own_schemas.append(
            replace(
                schema,
                scope_id=scope_id,
                parameters=tuple(
                    parameter for parameter in parameters if parameter.placeholder in names
                ),
                inline_population=inline,
            )
        )
    return tuple(own_schemas)


def _population_optimizations(
    policy: OptimizationPolicy,
    entities: Mapping[str, ir.Table],
    bounds: Mapping[str, BoundSource],
    owners: Mapping[str, SourceSession],
) -> PopulationOptimizations:
    def stored_projection(path: str) -> bool:
        bound = bounds.get(path)
        owner = owners.get(path)
        return (
            bound is not None
            and owner is not None
            and entities.get(path) is bound.relation
            and owner.stored_projection.proves(bound)
        )

    return PopulationOptimizations(policy, stored_projection=stored_projection)


def prepare_query_sources(
    graph: BoundGraph,
    state: CompiledSemanticState,
    source: SourceSession,
    *,
    sources: dict[str, SourceSession] | None = None,
    memo: InvocationMemo | None = None,
    consumption_timezone: str = "UTC",
    frame_schemas: Mapping[str, DataSchema] | None = None,
    reuse_sources: PreparedSources | None = None,
    policy: OptimizationPolicy = DEFAULT_OPTIMIZATION_POLICY,
) -> PreparedSources:
    """Prepare fields and relationships, acquiring HTTP JSON to infer its types."""
    if isinstance(graph.root, ReadQuery):
        return prepare_query_sources(
            read_input_graph(graph),
            state,
            source,
            sources=sources,
            memo=memo,
            frame_schemas=frame_schemas,
            consumption_timezone=consumption_timezone,
            reuse_sources=reuse_sources,
            policy=policy,
        )

    available = sources or {source.datasource.semantic_id: source}
    entities, bounds, owners = _bind_query_entities(graph, state, available, reuse_sources)
    if isinstance(graph.root, ResultQuery):
        return PreparedSources(
            source,
            literal_schemas(graph, entities, state),
            {},
            bounds,
            owners,
            entities,
            optimization_policy=policy,
        )
    schemas: list[SourceSchema] = []
    relations: dict[str, ir.Table] = {}
    parameters, dependency_inputs = query_parameters.prepare_parameters(
        graph, state, source, available, entities, memo, frame_schemas
    )
    _check_frame_parameter_types(parameters, entities, bounds, state)
    source_times: list[FrameSourceTime] = []
    population_optimizations = _population_optimizations(policy, entities, bounds, owners)
    prepare_contribution_reads(
        graph,
        state,
        entities,
        bounds,
        owners,
        parameters,
        schemas,
        relations,
        source_times,
        consumption_timezone,
        population_optimizations=population_optimizations,
    )
    populations = _query_populations(graph)
    population_checks = _prepare_population_reads(
        populations,
        state,
        source,
        entities,
        bounds,
        owners,
        parameters,
        schemas,
        relations,
        source_times,
        consumption_timezone,
        population_optimizations,
    )
    own_schemas = _scope_source_schemas(
        schemas,
        relations,
        bounds,
        population_checks,
        populations,
        parameters,
        graph.fingerprint if memo is None else memo.fingerprint(graph),
    )
    for index, dependency in enumerate(dependency_inputs):
        relations.update(dependency.relations)
        population_checks.update(dependency.population_checks)
        bounds.update(
            {f"parameter:{index}:{path}": bound for path, bound in dependency.bound.items()}
        )
        owners.update(dependency.sources)
        owners.update(
            {
                f"parameter:{index}:{path}": dependency.sources.get(path, dependency.source)
                for path in dependency.bound
            }
        )
    return PreparedSources(
        source,
        (
            *tuple(schema for prepared in dependency_inputs for schema in prepared.schemas),
            *own_schemas,
        ),
        relations,
        bounds,
        owners,
        entities,
        population_checks=population_checks,
        population_decisions=tuple(dict.fromkeys(population_optimizations.decisions)),
        source_times=merge_source_times(
            tuple(source_times), *(dependency.source_times for dependency in dependency_inputs)
        ),
        optimization_policy=policy,
    )


def prepare_source_free_populations(
    graph: BoundGraph,
    state: CompiledSemanticState,
    *,
    frame_schemas: Mapping[str, DataSchema] | None = None,
) -> tuple[SourceSchema, ...]:
    """Admit intrinsic calendar/unit key types without opening a datasource."""
    entities: dict[str, ir.Table] = {}
    parameters = query_parameters.prepare_frame_parameters(graph, frame_schemas or {}, entities)
    frame_types: dict[AxisBinding, tuple[DataColumn, ...]] = {}
    for parameter in parameters:
        definition = parameter.definition.root
        assert isinstance(definition, (FrameKeys, AttributeKeys))
        columns = {column.slot_id: column for column in parameter.columns}
        for binding, group in zip(definition.bindings, frame_key_groups(definition), strict=True):
            current = tuple(columns[name] for name in group)
            previous = frame_types.get(binding.target)
            if previous is not None:
                if tuple(column.dtype for column in previous) != tuple(
                    column.dtype for column in current
                ):
                    reject(
                        "compatible complete types for a shared Population target axis",
                        repr((previous, current)),
                        "Map compatible complete Frame key types before combining their domains.",
                        "frame.population",
                    )
                current = tuple(
                    replace(column, nullable=column.nullable or other.nullable)
                    for column, other in zip(current, previous, strict=True)
                )
            frame_types[binding.target] = current
    schemas: list[SourceSchema] = []
    for population in _query_populations(graph):
        prepared = prepare_population(population, entities, state)
        schema = prepared.table.schema().to_pyarrow()
        names = {node.name for node in prepared.table.op().find(ops.UnboundTable)}
        fields: list[SourceField] = []
        for axis, group in zip(population.axes, prepared.key_groups, strict=True):
            exact = frame_types.get(axis) if isinstance(axis, AxisBinding) else None
            fields.extend(
                tuple(
                    SourceField(name, name, column.dtype, column.nullable)
                    for name, column in zip(group, exact, strict=True)
                )
                if exact is not None
                else tuple(_admitted(schema, name, name) for name in group)
            )
        schemas.append(
            SourceSchema(
                "population:" + computation_fingerprint(population),
                tuple(fields),
                (),
                key_groups=prepared.key_groups,
                presence=prepared.presence,
                parameters=tuple(
                    parameter for parameter in parameters if parameter.placeholder in names
                ),
                inline_population=population if names else None,
            )
        )
    return tuple(schemas)


def qualify_source_inputs(graph: BoundGraph, state: CompiledSemanticState) -> tuple[str, ...]:
    """Check the closed source declarations without connections or HTTP requests."""
    entities = _query_entities(graph, state)
    for path in entities:
        contract = normalize_target_entity(state.registry, path)
        physical = contract.source
        datasource = state.registry.datasources[contract.datasource_ref.path]
        if isinstance(physical, JsonSourceIR):
            missing = json_source_param_names(physical)
            if missing:
                raise MaterializationError(
                    expected=f"{path}: concrete values for every JSON request parameter",
                    received=f"{path}: missing_parameters={missing!r}",
                    repair=f"Replace md.source_param(...) placeholders {missing!r} with concrete non-secret values in {path}'s md.json(query_params=..., body=...) definition; Analysis consumes complete source definitions. No request was sent.",
                    stage="execute_preparation",
                    help_target="analysis_frame.compute",
                )
            if query_policy(
                datasource.backend_type
            ).supports_file_parameters and physical.path.lower().startswith(
                ("http://", "https://")
            ):
                continue
        if isinstance(physical, (CsvSourceIR, JsonSourceIR)) and (
            not query_policy(datasource.backend_type).supports_file_parameters
            or not _local_file(physical.path)
            or (
                isinstance(physical, JsonSourceIR)
                and (
                    physical.method != "GET"
                    or physical.query_params
                    or physical.body_json is not None
                    or physical.body_params
                )
            )
        ):
            facts = (
                f", method={physical.method}, query_parameters={len(physical.query_params)}, body_parameters={len(physical.body_params)}"
                if isinstance(physical, JsonSourceIR)
                else ""
            )
            raise MaterializationError(
                expected="an existing local CSV/GET JSON file or a complete HTTP(S) JSON request on DuckDB",
                received=f"{type(physical).__name__}: existing_local_file={_local_file(physical.path)}, backend={datasource.backend_type}{facts}",
                repair="Use an existing local file, a complete GET/POST HTTP JSON source on DuckDB, or a qualified table/Parquet source.",
                stage="execute_preparation",
                help_target="analysis_frame.compute",
            )
    return entities


@contextmanager
def open_query_sources(
    service: DatasourceConnectionService,
    graph: BoundGraph,
    state: CompiledSemanticState,
    *,
    memo: InvocationMemo | None = None,
    consumption_timezone: str = "UTC",
    frame_schemas: Mapping[str, DataSchema] | None = None,
    json_responses: dict[str, JsonResponseCache] | None = None,
    reuse_sources: PreparedSources | None = None,
    shared_sessions: dict[str, SourceSession] | None = None,
    policy: OptimizationPolicy = DEFAULT_OPTIMIZATION_POLICY,
) -> Iterator[PreparedSources]:
    """Open sources, sharing HTTP responses only inside the caller's evaluation."""
    entities = qualify_source_inputs(graph, state)
    responses = {} if json_responses is None else json_responses
    datasources = tuple(
        dict.fromkeys(state.registry.entities[path].datasource for path in entities)
    )
    binding_scope = object()
    with ExitStack() as stack:
        sources: dict[str, SourceSession] = (
            {}
            if reuse_sources is None
            else {
                source.datasource.semantic_id: source
                for source in (reuse_sources.source, *reuse_sources.sources.values())
            }
        )
        for name in datasources:
            if name in sources:
                continue
            if shared_sessions is not None and name in shared_sessions:
                sources[name] = shared_sessions[name]
                continue
            datasource = state.registry.datasources[name]
            namespace = dumps(
                (name, datasource.backend_type, dict(datasource.fields), dict(datasource.env_refs)),
                sort_keys=True,
                allow_nan=False,
            )
            sources[name] = stack.enter_context(
                service.source_session(
                    name,
                    datasource,
                    binding_scope=binding_scope,
                    json_responses=responses.setdefault(namespace, {}),
                )
            )
            if shared_sessions is not None:
                shared_sessions[name] = sources[name]
        if not sources:
            _route_gap(
                "a source contribution",
                "source-free query",
                "Execute the local-only plan directly.",
            )
        native_graph = bind_native_calendars(graph, state, sources)
        prepared = prepare_query_sources(
            native_graph,
            state,
            next(iter(sources.values())),
            consumption_timezone=consumption_timezone,
            sources=sources,
            memo=memo,
            frame_schemas=frame_schemas,
            reuse_sources=reuse_sources,
            policy=policy,
        )
        yield replace(prepared, graph=native_graph)
