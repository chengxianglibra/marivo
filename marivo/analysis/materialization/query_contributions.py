"""Bind contributor fields and inspect conditional scan eligibility within one source invocation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from uuid import uuid4

import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa

from marivo._definition.observations import ContributionBinding
from marivo.analysis.binding.graph import BoundGraph
from marivo.analysis.compiler.population_factorization import PopulationOptimizations
from marivo.analysis.compiler.query_program import PopulationParameterPlan
from marivo.analysis.compiler.query_stages import SourceField, SourceSchema
from marivo.analysis.materialization.prepared_fields import PreparedFields
from marivo.analysis.materialization.query_preparation import (
    SourceInputContract,
    candidate_conditional_domains,
    direct_conditional_projection,
    prepare_fields,
    proves_conditional_predicates,
    share_source_projections,
)
from marivo.analysis.materialization.source_types import (
    _admitted,
    _bound_physical_field,
    _preparation_error,
    _validate_timestamp_precision,
    admit_source_type,
)
from marivo.analysis.model.data_types import DecimalDataType, DurationDataType, ScalarDataType
from marivo.analysis.model.time_facts import FrameSourceTime
from marivo.datasource.spi import (
    DURATION_UNIT_METADATA_KEY,
    TIMESTAMP_UNIT_METADATA_KEY,
    BoundSource,
    SourceSession,
    physical_fields,
)
from marivo.semantic.spi import CompiledSemanticState, normalize_target_entity


def _physical_identity_bound(
    relation: ir.Table,
    name: str,
    owner: SourceSession,
    bounds: Mapping[str, BoundSource],
    owners: Mapping[str, SourceSession],
) -> bool:
    origins = physical_fields(relation[name])
    if len(origins) != 1:
        return False
    field = origins[0]
    # Semantic primary_key owns non-null identity integrity. Physical
    # nullable metadata does not prevent sharing this exact source binding.
    return any(
        field.rel == bound.relation.op() and owners[path] is owner for path, bound in bounds.items()
    )


def _contribution_fields(
    prepared: PreparedFields,
    schema: pa.Schema,
    bounds: Mapping[str, BoundSource],
) -> list[SourceField]:
    fields: list[SourceField] = []
    originals = dict(prepared.originals)
    for path, column in prepared.columns:
        admitted = _admitted(schema, path, column)
        expression = originals[column]
        duration = DurationDataType("us") if expression.type().is_interval() else None
        for physical in physical_fields(expression):
            for bound in bounds.values():
                raw = _bound_physical_field(physical, bound)
                if raw is not None:
                    metadata = raw.metadata or {}
                    if not (
                        DURATION_UNIT_METADATA_KEY in metadata
                        or TIMESTAMP_UNIT_METADATA_KEY in metadata
                    ):
                        continue
                    if TIMESTAMP_UNIT_METADATA_KEY in metadata:
                        _validate_timestamp_precision(raw, path)
                    if DURATION_UNIT_METADATA_KEY not in metadata:
                        continue
                    dtype = admit_source_type(raw, path)
                    if isinstance(dtype, DurationDataType):
                        if duration is not None and duration != dtype:
                            raise _preparation_error("one exact Duration unit", path)
                        duration = dtype
        if duration is not None and (
            isinstance(admitted.dtype, DecimalDataType)
            or admitted.dtype in (ScalarDataType("int64"), ScalarDataType("float64"))
        ):
            admitted = replace(admitted, dtype=duration)
        fields.append(admitted)
    return fields


def _prepare_contribution_read(
    contribution: ContributionBinding,
    input_contract: SourceInputContract,
    state: CompiledSemanticState,
    entities: Mapping[str, ir.Table],
    bounds: Mapping[str, BoundSource],
    owners: Mapping[str, SourceSession],
    parameters: tuple[PopulationParameterPlan, ...],
    source_bindings: dict[SourceInputContract, str],
    source_times: list[FrameSourceTime],
    consumption_timezone: str,
    *,
    population_optimizations: PopulationOptimizations,
    conditional: bool,
) -> tuple[SourceSchema, ir.Table]:
    registry = state.registry
    input_contract = replace(input_contract, reads=contribution.reads)
    root = contribution.root.ref.path
    prepared = prepare_fields(
        contribution,
        entities,
        state,
        consumption_timezone=consumption_timezone,
        population_optimizations=population_optimizations,
    )
    source_times.extend(prepared.source_times)
    for dependency in prepared.dependencies:
        for physical in physical_fields(dependency):
            for bound in bounds.values():
                raw = _bound_physical_field(physical, bound)
                if raw is not None:
                    metadata = raw.metadata or {}
                    if TIMESTAMP_UNIT_METADATA_KEY in metadata:
                        _validate_timestamp_precision(raw, f"{root}: {physical.name}")
                    if DURATION_UNIT_METADATA_KEY in metadata:
                        admit_source_type(raw, f"{root}: {physical.name}")
    table = prepared.table
    source_contract = replace(
        input_contract,
        time_authorities=prepared.time_authorities,
    )
    schema = table.schema().to_pyarrow()

    logical_binding = owners[root].provider.query_policy.key_fusion == "local" or (
        owners[root].provider.query_policy.key_fusion != "none"
        and owners[root].uses_native_provider()
        and all(
            _physical_identity_bound(table, name, owners[root], bounds, owners)
            for name in registry.entities[root].primary_key
        )
    )
    binding_id = (
        source_bindings.setdefault(source_contract, f"source:{uuid4().hex}")
        if logical_binding
        else ""
    )
    conditional_contract = (
        source_contract.conditional_domain()
        if conditional
        and binding_id
        and not prepared.checks
        and all(direct_conditional_projection(value) for _, value in prepared.originals)
        and proves_conditional_predicates(source_contract.predicates, prepared)
        else None
    )
    conditional_binding_id = (
        source_bindings.setdefault(conditional_contract, f"source:{uuid4().hex}")
        if conditional_contract is not None
        else ""
    )
    keys = tuple(_admitted(schema, name, name) for name in registry.entities[root].primary_key)
    fields = _contribution_fields(prepared, schema, bounds)
    schema_facts = SourceSchema(
        root,
        keys,
        tuple(fields),
        contribution.occurrence,
        prepared.checks,
        binding_id=binding_id,
        conditional_binding_id=conditional_binding_id,
        time_authorities=prepared.time_authorities,
        provider=owners[root].provider.name,
        parameters=tuple(
            parameter
            for parameter in parameters
            if parameter.placeholder in {node.name for node in table.op().find(ops.UnboundTable)}
        ),
        original_identity=tuple(
            _admitted(schema, name, name)
            for name in normalize_target_entity(registry, root).version_row_key
        ),
    )
    return schema_facts, table


def prepare_contribution_reads(
    graph: BoundGraph,
    state: CompiledSemanticState,
    entities: Mapping[str, ir.Table],
    bounds: Mapping[str, BoundSource],
    owners: Mapping[str, SourceSession],
    parameters: tuple[PopulationParameterPlan, ...],
    schemas: list[SourceSchema],
    relations: dict[str, ir.Table],
    source_times: list[FrameSourceTime],
    consumption_timezone: str,
    *,
    population_optimizations: PopulationOptimizations,
) -> None:
    registry = state.registry
    observation = graph.signature.observation
    source_bindings: dict[SourceInputContract, str] = {}
    contributions = () if observation is None else observation.contributions
    contracts = tuple(
        SourceInputContract.capture(
            contribution,
            source_owner=id(owners[contribution.root.ref.path]),
            datasource=owners[contribution.root.ref.path].datasource.semantic_id,
            source_fingerprint=normalize_target_entity(
                registry, contribution.root.ref.path
            ).dependency_fingerprint,
        )
        for contribution in contributions
    )
    candidates = (
        candidate_conditional_domains(contributions, contracts)
        if population_optimizations.policy.allows("conditional_source_merge")
        else ()
    )
    domains = frozenset(
        domain
        for domain in candidates
        if owners[domain.root.ref.path].stored_projection.proves(bounds[domain.root.ref.path])
    )
    ordinary = share_source_projections(contributions, contracts)
    shared = (
        share_source_projections(contributions, contracts, conditional=domains)
        if domains
        else ordinary
    )
    for contribution, fallback, input_contract in zip(shared, ordinary, contracts, strict=True):
        allow = bool(domains) and input_contract.conditional_domain() in domains
        bound_times: list[FrameSourceTime] = []
        schema_facts, table = _prepare_contribution_read(
            contribution,
            input_contract,
            state,
            entities,
            bounds,
            owners,
            parameters,
            source_bindings,
            bound_times,
            consumption_timezone,
            population_optimizations=population_optimizations,
            conditional=allow,
        )
        if (
            allow
            and not schema_facts.conditional_binding_id
            and contribution.reads != fallback.reads
        ):
            bound_times.clear()
            schema_facts, table = _prepare_contribution_read(
                fallback,
                input_contract,
                state,
                entities,
                bounds,
                owners,
                parameters,
                source_bindings,
                bound_times,
                consumption_timezone,
                population_optimizations=population_optimizations,
                conditional=False,
            )
        source_times.extend(bound_times)
        schemas.append(schema_facts)
        stage_id = "read:" + ".".join(str(i) for i in contribution.occurrence)
        relations[stage_id] = table
