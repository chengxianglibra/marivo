"""Prepared Query source execution through issued Ibis reads and pandas stages."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal, NoReturn
from uuid import uuid4

import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
from ibis.common.exceptions import IbisError

from marivo._definition.types import IntervalCoordinate
from marivo.analysis.compiler.graph_plan import (
    AttributeRead,
    ReductionRead,
    local_steps,
    read_attribute,
    read_components,
    read_finish,
    read_members,
    read_reduction,
    require_attribute,
    require_components,
    require_finish,
    require_reduction,
)

if TYPE_CHECKING:
    from marivo._definition.observations import ObservationPeriod
    from marivo._definition.types import CalendarCoordinate
    from marivo.analysis.binding.attribute_rules import AttributeRule
    from marivo.analysis.compiler.attribute_inputs import AttributeRootLayout
    from marivo.analysis.compiler.frame_plan import CompiledFramePlan
    from marivo.analysis.compiler.graph_plan import QueryPlan
    from marivo.analysis.compiler.optimization import InvocationMemo
    from marivo.analysis.core.attribute import AttributeFailureContext
    from marivo.analysis.core.frame_expression import AttributeFrameValues

from marivo._definition.relations import LiteralPopulation, PopulationDependency, PopulationProgram
from marivo.analysis.compiler.graph_lowering import (
    LoweredQuery,
    LoweredRead,
)
from marivo.analysis.compiler.graph_plan import (
    FrameCompute,
    FrameInput,
    LocalCompute,
    QueryStage,
    SourceField,
    SourceRead,
    SourceSchema,
    read_native_reductions,
)
from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_data import CompletedFrameData
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import reject
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import StageOutput, source_columns
from marivo.analysis.materialization.graph_preflight import bind_entity_output, relation_operations
from marivo.datasource.adapters import (
    DURATION_UNIT_METADATA_KEY,
    TIMESTAMP_UNIT_METADATA_KEY,
    BoundSource,
    PhysicalRequirement,
    QualifiedSource,
    SourceSession,
    provider_for,
)
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._numeric_lowering import NumericCheck
from marivo.semantic.validator import normalize_target_entity


@dataclass(frozen=True, slots=True, eq=False)
class PreparedSources:
    """Schema-only bindings for one Query invocation and its open source session."""

    source: SourceSession
    schemas: tuple[SourceSchema, ...]
    relations: dict[str, ir.Table]
    bound: dict[str, BoundSource]
    sources: dict[str, SourceSession] = field(default_factory=dict)
    entities: dict[str, ir.Table] = field(default_factory=dict)
    original_input_qualifications: dict[
        tuple[SourceSession, ops.Node], Literal["qualified", "other", "unavailable"]
    ] = field(default_factory=dict)
    collation_qualifications: dict[
        tuple[SourceSession, ops.Node, str], Literal["deterministic", "other", "unavailable"]
    ] = field(default_factory=dict)


def _original_input_fact(
    prepared: PreparedSources, owner: SourceSession, bound: BoundSource, leaf: ops.Node
) -> Literal["qualified", "other", "unavailable"]:
    from marivo.datasource.ir import CsvSourceIR, JsonSourceIR, ParquetSourceIR, TableSourceIR

    key = (owner, leaf)
    if key not in prepared.original_input_qualifications:
        try:
            if owner.provider.name == "duckdb":
                from marivo.datasource.engines.duckdb import uncollated_original_input

                qualified = uncollated_original_input(
                    owner._backend,
                    leaf.to_expr(),
                    file_source=isinstance(
                        bound.source, (CsvSourceIR, JsonSourceIR, ParquetSourceIR)
                    ),
                )
            elif not isinstance(bound.source, TableSourceIR) or not isinstance(
                leaf, ops.DatabaseTable
            ):
                qualified = False
            elif owner.provider.name == "clickhouse":
                qualified = owner.clickhouse_table_engine(bound) == "MergeTree"
            else:
                qualified = owner.provider.name in ("postgres", "mysql", "trino")
            prepared.original_input_qualifications[key] = "qualified" if qualified else "other"
        except Exception:
            prepared.original_input_qualifications[key] = "unavailable"
    return prepared.original_input_qualifications[key]


def qualify_original_strings(
    prepared: PreparedSources, table: ir.Table, columns: tuple[str, ...]
) -> str | None:
    """Require qualified native collations for consumed remote string keys."""
    from marivo.analysis.materialization.query_preparation import physical_fields
    from marivo.datasource.engines.postgres import deterministic_collation
    from marivo.datasource.ir import TableSourceIR

    for column in columns:
        value = table[column]
        if not value.type().is_string():
            continue
        fields = physical_fields(value)
        if not fields:
            return "original string coordinate has no qualified physical collation"
        for physical_field in fields:
            matched = False
            for path, bound in prepared.bound.items():
                if physical_field.rel != bound.relation.op():
                    continue
                matched = True
                owner = prepared.sources.get(path, prepared.source)
                if owner.provider.name not in ("postgres", "mysql", "trino", "clickhouse"):
                    continue
                if not isinstance(bound.source, TableSourceIR):
                    return "Remote original strings require a physical table collation"
                key = (owner, bound.relation.op(), physical_field.name)
                if key not in prepared.collation_qualifications:
                    try:
                        if owner.provider.name == "clickhouse":
                            from marivo.datasource.engines.clickhouse import decoded_string_columns

                            identity = bound.facts.source_identity
                            if identity not in owner._query_decoded_strings:
                                owner._query_decoded_strings[identity] = decoded_string_columns(
                                    owner._backend, owner.datasource, bound.source
                                )
                            deterministic = (
                                physical_field.name in owner._query_decoded_strings[identity]
                            )
                        elif owner.provider.name == "trino":
                            from marivo.datasource.engines.trino import (
                                _trino_namespace,
                                _trino_rows,
                            )

                            catalog, schema = _trino_namespace(
                                bound.source.database,
                                catalog=str(owner.datasource.fields["catalog"]),
                                default_schema=str(owner.datasource.fields.get("schema", "")),
                            )
                            rows = _trino_rows(
                                owner._backend,
                                "trino.columns",
                                values={
                                    "catalog": catalog,
                                    "schema": schema,
                                    "table": bound.source.table,
                                },
                            )
                            for row in rows:
                                metadata_key = (
                                    owner,
                                    bound.relation.op(),
                                    str(row.get("column_name")),
                                )
                                prepared.collation_qualifications[metadata_key] = (
                                    "deterministic"
                                    if str(row.get("data_type", "")).lower().startswith("varchar")
                                    else "other"
                                )
                            deterministic = (
                                prepared.collation_qualifications.get(key) == "deterministic"
                            )
                        elif owner.provider.name == "mysql":
                            from marivo.datasource.engines.mysql import original_string_column

                            deterministic = original_string_column(
                                owner._backend, owner.datasource, bound.source, physical_field.name
                            )
                        else:
                            deterministic = deterministic_collation(
                                owner._backend, owner.datasource, bound.source, physical_field.name
                            )
                        prepared.collation_qualifications[key] = (
                            "deterministic" if deterministic else "other"
                        )
                    except Exception:
                        prepared.collation_qualifications[key] = "unavailable"
                fact = prepared.collation_qualifications[key]
                if fact == "unavailable":
                    return (
                        "Remote original strings require visible deterministic collation metadata"
                    )
                if fact != "deterministic":
                    return "Remote original strings retain a nondeterministic collation boundary"
            if not matched:
                return "original string coordinate has an unqualified physical source"
    return None


def qualify_original_source(
    prepared: PreparedSources,
    reads: tuple[SourceRead, ...],
    raw: ir.Table,
    *,
    sampled: bool = False,
    attribute: bool = False,
) -> str | None:
    """Qualify typed numeric recipes independently of complete identity carriers."""
    from marivo._definition.expressions import (
        EntityCount,
        RowNode,
        SourceAggregate,
        SourceWeightedMean,
    )
    from marivo.analysis.materialization.query_preparation import physical_fields
    from marivo.datasource.ir import TableSourceIR
    from marivo.semantic._numeric_lowering import lower_row

    if not reads:
        return "original fiber has no source reads"
    owner = prepared.sources.get(reads[0].source_id, prepared.source)
    if owner.provider.name == "duckdb":
        physical = (ops.DatabaseTable, ops.UnboundTable, ops.InMemoryTable, ops.SQLQueryResult)
        leaves = set(raw.op().find(physical))
        qualified: set[ops.Node] = set()
        for path, bound in prepared.bound.items():
            if prepared.sources.get(path, prepared.source) is not owner:
                continue
            for leaf in bound.relation.op().find(physical):
                if leaf not in leaves:
                    continue
                fact = _original_input_fact(prepared, owner, bound, leaf)
                if fact == "unavailable":
                    return "original recomputation requires visible input collation metadata"
                if fact != "qualified":
                    return "original recomputation retains an explicit, default or unknown inherited collation boundary"
                qualified.add(leaf)
        if not leaves or not leaves <= qualified:
            return "original recomputation requires typed physical inputs from one source owner"
        return None
    if (
        owner.provider.name not in ("postgres", "mysql", "trino", "clickhouse")
        or owner._backend.name != owner.provider.name
        or owner.datasource.backend_type != owner.provider.name
    ):
        return "original fiber has no qualified physical backend route"

    def physical_value(value: ir.Value, *, role: Literal["identity", "recipe"]) -> bool:
        fields = physical_fields(value)
        if len(fields) != 1:
            return False
        field = fields[0]
        for path, bound in prepared.bound.items():
            if (
                prepared.sources.get(path, prepared.source) is not owner
                or field.rel != bound.relation.op()
            ):
                continue
            if not isinstance(bound.source, TableSourceIR):
                return False
            if _original_input_fact(prepared, owner, bound, field.rel) != "qualified":
                return False
            physical = bound.facts.schema.field(field.name)
            if role == "identity":
                return not physical.nullable and (
                    pa.types.is_signed_integer(physical.type) or pa.types.is_string(physical.type)
                )
            # Each recipe field has already passed source type admission. Its
            # numeric/result and control types are owned by the closed lowerer.
            return True
        return False

    coordinate_columns: list[str] = []
    for read in reads:
        if read_components(read) is not None:
            coordinate_columns.extend(name for _, name in require_components(read).identity)
        if read_reduction(read) is not None:
            coordinate_columns.extend(
                dict(read.field_columns)[path] for _, path in require_reduction(read).axes
            )
        coordinate_columns.extend(
            name for _, name in read.field_columns if raw[name].type().is_string()
        )
    issue = qualify_original_strings(
        prepared,
        raw,
        tuple(dict.fromkeys(name for name in coordinate_columns if raw[name].type().is_string())),
    )
    if issue is not None:
        return issue
    for read in reads:
        reduction = read_reduction(read)
        if (
            reduction is None
            or read_components(read) is None
            or (not read.binding_id and not attribute)
            or (reduction.duration is not None and not attribute)
            or (
                not sampled
                and not attribute
                and (
                    reduction.calendar is not None
                    or reduction.calendars
                    or reduction.sampled
                    or reduction.preserve_sample_presence
                    or reduction.window_key is not None
                )
            )
            or prepared.sources.get(read.source_id, prepared.source) is not owner
        ):
            return "Remote original fiber lacks an exact prepared physical binding"
        fields = dict(read.field_columns)
        if not attribute and any(
            not raw[fields[path]].type().is_string() for _, path in reduction.axes
        ):
            return "Remote original numeric coordinates retain their pandas key boundary"
        if any(
            not physical_value(raw[name], role="identity")
            for _, name in require_components(read).identity
        ):
            return (
                "Remote original identity requires direct physically nonnull integer or string keys"
            )
        node = reduction.node
        if isinstance(node, EntityCount):
            continue
        operands: tuple[RowNode, ...]
        if isinstance(node, SourceWeightedMean):
            operands = (node.value, node.weight)
        elif isinstance(node, SourceAggregate) and (
            node.reducer
            in (
                "sum",
                "count",
                "count_distinct",
                "min",
                "max",
                "median",
                "quantile",
            )
            or node.reducer == "mean"
        ):
            operands = (node.operand,)
        else:
            return "Remote original method retains its established host numeric finisher"
        for operand in operands:
            value = lower_row(operand, {path: raw[name] for path, name in read.field_columns}).value
            if not value.type().is_numeric():
                return "Remote original recipes require a numeric result"
            origins = physical_fields(value)
            if not origins or any(
                not physical_value(field.to_expr(), role="recipe") for field in origins
            ):
                return "Remote original recipes require typed physical inputs from one source owner"
    return None


def _preparation_error(
    expected: str,
    received: str,
    repair: str = "Use a supported physical field type while preserving the requested key, time and unit contract; no business row was read.",
) -> MaterializationError:
    return MaterializationError(
        expected=expected,
        received=received,
        repair=repair,
        stage="execute_preparation",
        help_target="analysis_frame.compute",
    )


def _route_gap(expected: str, received: str, repair: str) -> NoReturn:
    from marivo.analysis.errors import AnalysisStageUnavailableError

    raise AnalysisStageUnavailableError(
        stage="query_execution",
        expected=expected,
        received=received,
        repair=f"{repair} No business row was read and no Run was admitted.",
        target="analysis_frame.compute",
    )


def _validate_timestamp_precision(field: pa.Field, path: str) -> None:
    original_unit = (field.metadata or {}).get(TIMESTAMP_UNIT_METADATA_KEY)
    if original_unit is None:
        return
    physical = field.type
    units = (b"s", b"ms", b"us", b"ns")
    if not pa.types.is_timestamp(physical) or original_unit not in units:
        raise _preparation_error("a timestamp with an exact source unit", repr(field))
    if units.index(original_unit) > units.index(physical.unit.encode("ascii")):
        raise _preparation_error(
            f"{path}: a lossless timestamp[{original_unit.decode('ascii')}] source carrier",
            f"{path}: {physical} loses source precision",
            f"Expose {path} through a source or Entity projection retaining the original timestamp unit; do not truncate identity values. No business row was read.",
        )


def admit_source_type(field: pa.Field, path: str) -> DataType:
    """Admit one bound source type family before reading native pandas values."""
    physical = field.type
    _validate_timestamp_precision(field, path)
    unit = (field.metadata or {}).get(DURATION_UNIT_METADATA_KEY)
    if unit is not None:
        if physical != pa.int64() or unit not in (b"s", b"ms", b"us", b"ns"):
            raise _preparation_error("int64 ticks with a fixed Duration unit", repr(field))
        return {
            b"s": DurationDataType("s"),
            b"ms": DurationDataType("ms"),
            b"us": DurationDataType("us"),
            b"ns": DurationDataType("ns"),
        }[unit]
    if pa.types.is_signed_integer(physical) or physical in (pa.uint8(), pa.uint16(), pa.uint32()):
        return ScalarDataType("int64")
    if pa.types.is_floating(physical):
        return ScalarDataType("float64")
    if pa.types.is_boolean(physical):
        return ScalarDataType("boolean")
    if pa.types.is_string(physical) or pa.types.is_large_string(physical):
        return ScalarDataType("string")
    if physical == pa.date32():
        return ScalarDataType("date")
    if pa.types.is_decimal128(physical):
        return DecimalDataType(physical.precision, physical.scale)
    if pa.types.is_timestamp(physical):
        return TimestampDataType(physical.unit, physical.tz)
    if pa.types.is_duration(physical):
        return DurationDataType(physical.unit)
    raise _preparation_error(
        f"{path}: a column type in the supported stage type families",
        f"{path}: {physical}",
        (
            f"Expose {path} as Decimal(20,0) in the source view or Entity body with raw[{path.rsplit('.', 1)[-1]!r}].cast('decimal(20,0)'); UInt64 requires an exact Decimal carrier, never float or unchecked int64. No business row was read."
            if physical == pa.uint64()
            else f"Project {path} to a supported scalar type in the Entity body using an inline cast such as raw['column'].cast('string'), preserving its identity, time and unit meaning. Read marivo.help('semantic.entity.define'). No business row was read."
        ),
    )


def _admitted(schema: pa.Schema, path: str, column: str) -> SourceField:
    index = schema.get_field_index(column)
    if index < 0:
        raise _preparation_error(
            f"{path}: column {column!r} on the bound source", repr(schema.names)
        )
    item = schema.field(index)
    return SourceField(path, column, admit_source_type(item, path), item.nullable)


def _bound_physical_field(physical: ops.Field, bound: BoundSource) -> pa.Field | None:
    """Recover captured carrier metadata through the source's declared projection."""
    from marivo.analysis.materialization.query_preparation import physical_fields

    for captured in bound.facts.schema:
        if physical in physical_fields(bound.relation[captured.name]):
            return captured
    return None


def _population_field(
    table: ir.Table, column: str, bounds: Mapping[str, BoundSource]
) -> SourceField:
    """Restore fixed Duration metadata through selected attribute projections."""
    from marivo.analysis.materialization.query_preparation import physical_fields

    admitted = _admitted(table.schema().to_pyarrow(), column, column)
    if admitted.dtype != ScalarDataType("int64"):
        return admitted
    duration: DurationDataType | None = None
    for physical in physical_fields(table[column]):
        for bound in bounds.values():
            field = _bound_physical_field(physical, bound)
            if field is None:
                continue
            if DURATION_UNIT_METADATA_KEY not in (field.metadata or {}):
                continue
            dtype = admit_source_type(field, column)
            assert isinstance(dtype, DurationDataType)
            if duration is not None and duration != dtype:
                raise _preparation_error("one exact Duration unit", column)
            duration = dtype
    return replace(admitted, dtype=duration) if duration is not None else admitted


def _query_populations(graph: BoundGraph) -> tuple[PopulationDependency, ...]:
    from marivo._definition.expressions import DeclaredMetricRef
    from marivo._definition.graph import DefinitionNode, children, computation_fingerprint
    from marivo._definition.relations import AttributeKeys, FrameKeys

    populations: dict[str, PopulationDependency] = {}
    seen: set[int] = set()

    def visit(node: DefinitionNode) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, (PopulationProgram, LiteralPopulation)):
            populations[computation_fingerprint(node)] = node
            if isinstance(node, PopulationProgram) and isinstance(
                node.root, (FrameKeys, AttributeKeys)
            ):
                return
        if isinstance(node, DeclaredMetricRef):
            visit(dict(graph.signature.declared_metrics)[node.ref])
        for edge in children(node):
            visit(edge.child)

    visit(graph.root)
    observation = graph.signature.observation
    if observation is not None:
        if observation.coordinates.population is not None:
            visit(observation.coordinates.population)
        if observation.condition is not None:
            visit(observation.condition)
        if observation.coordinates.parent_scope is not None:
            visit(observation.coordinates.parent_scope)
    return tuple(populations.values())


def _query_entities(graph: BoundGraph, state: CompiledSemanticState) -> tuple[str, ...]:
    """Resolve every required Entity before opening connections."""
    from marivo._definition.query import ResultQuery
    from marivo._definition.relations import ReadQuery
    from marivo._definition.types import AxisBinding
    from marivo.analysis.core.graph import read_input_graph

    if isinstance(graph.root, ReadQuery):
        return _query_entities(read_input_graph(graph), state)
    if isinstance(graph.root, ResultQuery):
        return tuple(
            dict.fromkeys(
                axis.entity.ref.path
                for axis in graph.signature.axes
                if isinstance(axis, AxisBinding)
            )
        )

    observation = graph.signature.observation
    entities: dict[str, None] = {}
    for contribution in () if observation is None else observation.contributions:
        root = contribution.root.ref.path
        entities[root] = None
        for read in (
            *contribution.reads,
            *contribution.coordinates,
            *(read for _, read in contribution.population_reads),
        ):
            from marivo._definition.types import IdentityRead
            from marivo.analysis.binding.coordinates import identity_projection

            if (
                isinstance(read, IdentityRead)
                and identity_projection(read, contribution.root, state.registry) is not None
            ):
                continue
            entities[read.entity.ref.path] = None
            for step in read.path:
                relation = state.registry.relationships[step.ref.path]
                entities[relation.from_entity] = None
                entities[relation.to_entity] = None
            if (
                state.registry.entities[root].datasource
                != state.registry.entities[read.entity.ref.path].datasource
            ):
                _route_gap(
                    "scalar relationship endpoints on one datasource",
                    read.entity.ref.path,
                    "Reduce independent datasource roots separately before combining Metrics.",
                )
    from marivo.analysis.materialization.population_preparation import population_entities

    for population in _query_populations(graph):
        entities.update(dict.fromkeys(population_entities(population, state)))
    return tuple(entities)


def prepare_query_sources(
    graph: BoundGraph,
    state: CompiledSemanticState,
    source: SourceSession,
    *,
    sources: dict[str, SourceSession] | None = None,
    memo: InvocationMemo | None = None,
    frame_schemas: Mapping[str, DataSchema] | None = None,
) -> PreparedSources:
    """Prepare all physical fields and checked relationships without business reads."""
    from marivo._definition.relations import ReadQuery
    from marivo.analysis.core.graph import read_input_graph

    if isinstance(graph.root, ReadQuery):
        return prepare_query_sources(
            read_input_graph(graph),
            state,
            source,
            sources=sources,
            memo=memo,
            frame_schemas=frame_schemas,
        )

    from marivo.analysis.materialization.query_preparation import physical_fields, prepare_fields
    from marivo.datasource.timezone import probe_engine_timezone

    available = sources or {source.datasource.semantic_id: source}
    registry, sidecar = state.registry, state.sidecar
    entities: dict[str, ir.Table] = {}
    bounds: dict[str, BoundSource] = {}
    owners: dict[str, SourceSession] = {}
    for path in _query_entities(graph, state):
        contract = normalize_target_entity(registry, path)
        owner = available[contract.datasource_ref.path]
        bound = owner.bind(contract.source, source_identity=path)
        entities[path] = bind_entity_output(
            registry, sidecar, path, owner, bound, contract.dependency_fingerprint
        )
        bounds[path] = bound
        owners[path] = owner
    from marivo._definition.query import ResultQuery

    if isinstance(graph.root, ResultQuery):
        from marivo.analysis.materialization.literal_preparation import literal_schemas

        return PreparedSources(
            source,
            literal_schemas(graph, entities, state),
            {},
            bounds,
            owners,
            entities,
        )
    schemas: list[SourceSchema] = []
    relations: dict[str, ir.Table] = {}
    from marivo.analysis.materialization.query_parameters import prepare_parameters

    parameters, dependency_inputs = prepare_parameters(
        graph, state, source, available, entities, memo, frame_schemas
    )
    from marivo._definition.relations import AttributeKeys, FrameKeys
    from marivo._definition.types import FieldRead
    from marivo.analysis.compiler.populations import frame_key_groups
    from marivo.analysis.compiler.query_program import FrameParameter
    from marivo.analysis.materialization.population_preparation import _field

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
                    _field(
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
    observation = graph.signature.observation
    from marivo.analysis.materialization.query_preparation import (
        SourceInputContract,
        share_source_projections,
    )

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
    contributions = share_source_projections(contributions, contracts)
    for contribution, input_contract in zip(contributions, contracts, strict=True):
        input_contract = replace(input_contract, reads=contribution.reads)
        root = contribution.root.ref.path
        reader = probe_engine_timezone(owners[root]._backend)
        prepared = prepare_fields(
            contribution,
            entities,
            state,
            reader,
        )
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
            coverage=prepared.coverage,
            time_authorities=prepared.time_authorities,
        )
        schema = table.schema().to_pyarrow()

        def physical_identity_nonnull(relation: ir.Table, name: str, owner: SourceSession) -> bool:
            origins = physical_fields(relation[name])
            if len(origins) != 1:
                return False
            field = origins[0]
            return any(
                field.rel == bound.relation.op()
                and owners[path] is owner
                and not bound.facts.schema.field(field.name).nullable
                for path, bound in bounds.items()
            )

        logical_binding = owners[root].provider.name == "duckdb" or (
            owners[root].provider.name in ("postgres", "mysql", "trino", "clickhouse")
            and owners[root]._backend.name == owners[root].provider.name
            and all(
                physical_identity_nonnull(table, name, owners[root])
                for name in registry.entities[root].primary_key
            )
        )
        binding_id = (
            source_bindings.setdefault(source_contract, f"source:{uuid4().hex}")
            if logical_binding
            else ""
        )
        keys = tuple(_admitted(schema, name, name) for name in registry.entities[root].primary_key)
        fields = []
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
        schema_facts = SourceSchema(
            root,
            keys,
            tuple(fields),
            contribution.occurrence,
            prepared.checks,
            binding_id=binding_id,
            coverage=prepared.coverage,
            time_authorities=prepared.time_authorities,
            provider=owners[root].provider.name,
            parameters=tuple(
                parameter
                for parameter in parameters
                if parameter.placeholder
                in {node.name for node in table.op().find(ops.UnboundTable)}
            ),
            original_identity=tuple(
                _admitted(schema, name, name)
                for name in normalize_target_entity(registry, root).version_row_key
            ),
        )
        schemas.append(schema_facts)
        stage_id = "read:" + ".".join(str(i) for i in contribution.occurrence)
        relations[stage_id] = table
    from marivo._definition.graph import computation_fingerprint
    from marivo.analysis.dsl.population import population_keys
    from marivo.analysis.materialization.population_preparation import (
        population_entities,
        prepare_population,
    )

    for population in _query_populations(graph):
        required = population_entities(population, state)
        datasources = {state.registry.entities[path].datasource for path in required}
        finite = population_keys(population) is not None
        if not finite and len(datasources) != 1 and not parameters:
            _route_gap(
                "one datasource for a source-driven Population",
                "multiple datasources",
                "Build independent source domains before combining their finite keys.",
            )
        prepared_population = prepare_population(population, entities, state)
        source_id = "population:" + computation_fingerprint(population)
        schema = prepared_population.table.schema().to_pyarrow()
        keys = tuple(
            _population_field(prepared_population.table, name, bounds)
            for name in prepared_population.key_columns
        )
        schemas.append(
            SourceSchema(
                source_id,
                keys,
                (),
                preparation_checks=prepared_population.checks,
                key_groups=prepared_population.key_groups,
                presence=prepared_population.presence,
            )
        )
        if not finite:
            relations[source_id] = prepared_population.table
            owners[source_id] = owners[required[0]] if required else source
    own_schemas = []
    population_definitions = {
        "population:" + computation_fingerprint(population): population
        for population in _query_populations(graph)
    }
    for schema in schemas:
        relation = (
            relations.get("read:" + ".".join(str(index) for index in schema.occurrence))
            if schema.occurrence is not None
            else relations.get(schema.source_id)
        )
        names = (
            {node.name for node in relation.op().find(ops.UnboundTable)}
            if relation is not None
            else set()
        )
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
                scope_id=graph.fingerprint,
                parameters=tuple(
                    parameter for parameter in parameters if parameter.placeholder in names
                ),
                inline_population=inline,
            )
        )
    for index, dependency in enumerate(dependency_inputs):
        relations.update(dependency.relations)
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
    )


def prepare_source_free_populations(
    graph: BoundGraph,
    state: CompiledSemanticState,
    *,
    frame_schemas: Mapping[str, DataSchema] | None = None,
) -> tuple[SourceSchema, ...]:
    """Admit intrinsic calendar/unit key types without opening a datasource."""
    from marivo._definition.graph import computation_fingerprint
    from marivo._definition.relations import AttributeKeys, FrameKeys
    from marivo._definition.types import AxisBinding
    from marivo.analysis.compiler.populations import frame_key_groups
    from marivo.analysis.materialization.population_preparation import prepare_population
    from marivo.analysis.materialization.query_parameters import prepare_frame_parameters

    entities: dict[str, ir.Table] = {}
    parameters = prepare_frame_parameters(graph, frame_schemas or {}, entities)
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


@contextmanager
def open_query_sources(
    service: DatasourceConnectionService,
    graph: BoundGraph,
    state: CompiledSemanticState,
    *,
    memo: InvocationMemo | None = None,
    frame_schemas: Mapping[str, DataSchema] | None = None,
) -> Iterator[PreparedSources]:
    """Open the declared datasource sessions used by this invocation."""
    entities = _query_entities(graph, state)
    from marivo.analysis.materialization.graph_preflight import _local_file
    from marivo.datasource.ir import CsvSourceIR, JsonSourceIR

    for path in entities:
        contract = normalize_target_entity(state.registry, path)
        physical = contract.source
        datasource = state.registry.datasources[contract.datasource_ref.path]
        if isinstance(physical, (CsvSourceIR, JsonSourceIR)) and (
            datasource.backend_type != "duckdb"
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
                expected="an existing local CSV or unparameterized GET JSON file on DuckDB",
                received=f"{type(physical).__name__}: existing_local_file={_local_file(physical.path)}, backend={datasource.backend_type}{facts}",
                repair="Use an existing local file without runtime request parameters, or a qualified table/Parquet source.",
                stage="execute_preparation",
                help_target="analysis_frame.compute",
            )
    datasources = tuple(
        dict.fromkeys(state.registry.entities[path].datasource for path in entities)
    )
    with ExitStack() as stack:
        sources: dict[str, SourceSession] = {}
        for name in datasources:
            datasource = state.registry.datasources[name]
            backend = stack.enter_context(service.use_backend(datasource.name, read_only=True))
            sources[name] = stack.enter_context(
                SourceSession(provider_for(datasource.backend_type), datasource, backend)
            )
        if not sources:
            _route_gap(
                "a source contribution",
                "source-free query",
                "Execute the local-only plan directly.",
            )
        yield prepare_query_sources(
            graph,
            state,
            next(iter(sources.values())),
            sources=sources,
            memo=memo,
            frame_schemas=frame_schemas,
        )


def _qualify_query_read(
    prepared: PreparedSources, stage: SourceRead, lowered: LoweredRead
) -> tuple[QualifiedSource, ...]:
    expression = lowered.expression
    source = prepared.sources.get(stage.source_id, prepared.source)
    relations = set(expression.op().find(ops.Relation))
    return tuple(
        source.qualify(
            bound, PhysicalRequirement("query.source_read", 1, relation_operations(expression))
        )
        for path, bound in prepared.bound.items()
        if prepared.sources.get(path, prepared.source) is source
        and bound.relation.op() in relations
    )


def qualify_query_sources(prepared: PreparedSources, lowered: LoweredQuery) -> None:
    """Qualify every schema-bound read template before a Run is admitted."""
    for read in lowered.reads:
        stage = lowered.plan.source_read(read.stage_id)
        from marivo.analysis.materialization.query_parameters import instantiate

        expression = read.expression
        for parameter in stage.parameters:
            empty = StageOutput.from_frame(
                pd.DataFrame(columns=[column.slot_id for column in parameter.columns]),
                parameter.columns,
                tuple(column.slot_id for column in parameter.columns),
            )
            expression = instantiate(
                expression,
                parameter.placeholder,
                parameter.columns,
                empty,
                provider=prepared.sources.get(stage.source_id, prepared.source).provider.name,
                source=prepared.sources.get(stage.source_id, prepared.source),
            )
        read = replace(read, expression=expression)
        _admit_numeric_method(prepared.sources.get(stage.source_id, prepared.source), stage, read)
        read = replace(read, expression=_native_query_expression(prepared, stage, read))
        expression = read.expression
        qualified = _qualify_query_read(prepared, stage, read)
        source = prepared.sources.get(stage.source_id, prepared.source)
        _qualify_clickhouse_joins(source, qualified)
        try:
            source.compile(
                qualified,
                expression,
                purpose="query.prepare",
                expected_schema=expression.schema().to_pyarrow(),
            )
        except (IbisError, NotImplementedError, TypeError, ValueError) as error:
            _route_gap(
                "a natively compiled Query expression for the selected backend and types",
                f"{source.provider.name}: {type(error).__name__}: {error}",
                "Use a supported native method/type or export an admitted Query for pandas processing.",
            )


def _qualify_clickhouse_joins(
    source: SourceSession, qualified: tuple[QualifiedSource, ...]
) -> None:
    """Qualify physical ClickHouse join topology without reading business rows."""
    if source.provider.name != "clickhouse" or not any(
        "join" in item.requirement.operations for item in qualified
    ):
        return
    from marivo.datasource.ir import TableSourceIR

    for item in qualified:
        physical = item.binding.source
        if not isinstance(physical, TableSourceIR):
            _route_gap(
                "a known ClickHouse physical source profile for this join",
                type(physical).__name__,
                "Use a source with an explicitly qualified physical join topology.",
            )
        try:
            engine = source.clickhouse_table_engine(item.binding)
        except Exception as error:
            _route_gap(
                "visible ClickHouse physical metadata for this join",
                f"{item.binding.facts.source_identity}: {type(error).__name__}",
                "Grant the reader access to the source's system.tables metadata or use a qualified join source.",
            )
        if engine is None:
            _route_gap(
                "a known ClickHouse physical table profile for this join",
                item.binding.facts.source_identity,
                "Use a source whose table profile is visible to the reader.",
            )
        if engine == "Distributed":
            _route_gap(
                "a qualified ClickHouse Distributed join route",
                f"Distributed table {physical.table}: this Query requires IN/JOIN subqueries",
                "Use a Query without Distributed joins, or an explicitly declared source with a qualified join topology. Marivo does not change distributed_product_mode or substitute local tables.",
            )
        if not engine.endswith("MergeTree"):
            _route_gap(
                "a qualified ClickHouse physical join topology",
                f"{physical.table}: engine={engine}",
                "Use a qualified physical MergeTree table for this join; a view or another engine does not establish its underlying join topology.",
            )


def _admit_numeric_method(source: SourceSession, stage: SourceRead, read: LoweredRead) -> None:
    from marivo._definition.expressions import SourceAggregate
    from marivo._quantiles import source_quantile_algorithm

    if source.provider.name == "duckdb":
        return
    reductions = (
        *(
            require_reduction(stage)
            for stage in read_members(stage) or (stage,)
            if read_reduction(stage) is not None
        ),
        *read_native_reductions(stage),
    )
    for reduction in reductions:
        node = reduction.node
        if isinstance(node, SourceAggregate) and node.reducer not in (
            "count",
            "count_distinct",
            "sum",
            "min",
            "max",
            "mean",
        ):
            if (
                node.reducer in ("median", "quantile", "approx_median", "approx_quantile")
                and source_quantile_algorithm(
                    source.provider.name, approximate=node.reducer.startswith("approx_")
                )
                is not None
            ):
                continue
            _route_gap(
                "a qualified native algorithm for this source reduction",
                f"{source.provider.name}: {node.reducer}",
                "Use an admitted native reduction, or export its input for pandas. Exact and approximate algorithms are not interchangeable.",
            )


def _native_query_expression(
    prepared: PreparedSources, stage: SourceRead, read: LoweredRead
) -> ir.Table:
    from marivo.analysis.materialization.temporal_sql import lower_temporal

    source = prepared.sources.get(stage.source_id, prepared.source)
    expression = lower_temporal(read.expression, source.provider.name)
    assert isinstance(expression, ir.Table)
    if source.provider.name == "mysql":
        # MySQL has no IS_NAN / IS_INF functions. Native finite comparisons
        # retain the check without requiring routines or source-side casts.
        def mysql_rewrite(
            node: ops.Node, _results: Mapping[ops.Node, ops.Node], **kwargs: object
        ) -> ops.Node:
            value = node.copy(**kwargs)
            if (
                isinstance(value, ops.Cast)
                and isinstance(value.arg, ops.Sum)
                and value.to.is_decimal()
                and value.arg.dtype == value.to
            ):
                # MySQL widens native SUM, but a redundant result CAST clips an
                # overflowing Decimal to its endpoint with only a warning. Keep
                # the widened value until safe conversion to the bound carrier.
                return value.arg
            if isinstance(value, (ops.IsNan, ops.IsInf)):
                argument = value.arg.to_expr()
                return (
                    argument != argument
                    if isinstance(value, ops.IsNan)
                    else argument.abs() > float.fromhex("0x1.fffffffffffffp+1023")
                ).op()
            return value

        rewritten = expression.op().map(mysql_rewrite)[expression.op()].to_expr()
        assert isinstance(rewritten, ir.Table)
        expression = rewritten
    if source.provider.name == "clickhouse":
        import ibis
        import ibis.expr.datatypes as dt

        def declaration(value: object) -> int:
            raise NotImplementedError("native ClickHouse uniqExact")

        exact_nodes: dict[ops.Node, ops.Node] = {}
        for node in expression.op().find((ops.IsNan, ops.IsInf)):
            quotient = node.arg
            while isinstance(quotient, ops.Field) and isinstance(
                quotient.rel, (ops.Project, ops.Aggregate)
            ):
                quotient = quotient.rel.values[quotient.name]
            if (
                isinstance(quotient, ops.Divide)
                and (quotient.left.dtype.is_decimal() or quotient.right.dtype.is_decimal())
                and not (quotient.left.dtype.is_floating() or quotient.right.dtype.is_floating())
            ):
                # ClickHouse Decimal division stays Decimal although Ibis infers
                # float64. Decimal has no NaN/infinity; native overflow still
                # fails the statement, and zero/error checks remain separate.
                exact_nodes[node] = ibis.literal(False).op()
        for node in expression.op().find(ops.CountDistinct):
            exact = ibis.udf.agg.builtin(name="uniqExact", signature=((node.arg.dtype,), dt.int64))(
                declaration
            )
            exact_nodes[node] = exact(
                node.arg.to_expr(), where=node.where.to_expr() if node.where is not None else None
            ).op()
        rewritten = expression.op().replace(exact_nodes).to_expr()
        assert isinstance(rewritten, ir.Table)
        expression = rewritten
    return expression


def _actual_attribute_layout(
    layout: AttributeRootLayout, frame: pd.DataFrame
) -> AttributeRootLayout:
    """Capture returned native physical columns before Attribute normalization."""
    return replace(
        layout,
        columns=source_columns(
            frame.loc[frame[layout.kind] == (1 if layout.scope == "parent" else 0)],
            layout.columns,
            layout.keys,
        ),
    )


def _mysql_source_timezones(sql: str) -> tuple[str, ...]:
    """Inspect required native conversion zones without changing issued SQL."""
    import sqlglot
    from sqlglot import expressions as sge
    from sqlglot.optimizer.pushdown_projections import pushdown_projections

    statement = sqlglot.parse_one(sql, read="mysql")
    # Projection pruning does not resolve positional grouping references.
    # Expand those references while the original SELECT list is still intact.
    for select in statement.find_all(sge.Select):
        group = select.args.get("group")
        if not isinstance(group, sge.Group):
            continue
        values = select.expressions
        references: list[sge.Expression] = []
        for item in group.expressions:
            if isinstance(item, sge.Literal) and item.is_int and 1 <= int(item.this) <= len(values):
                selected = values[int(item.this) - 1]
                references.append(
                    (selected.this if isinstance(selected, sge.Alias) else selected).copy()
                )
            else:
                references.append(item)
        group.set("expressions", references)
    # Ibis can retain unused values in inner SELECTs. Inspect only the required
    # projection closure; raw-only membership needs no named zone tables.
    statement = pushdown_projections(statement)
    return tuple(
        sorted(
            {
                zone.this
                for conversion in statement.find_all(sge.ConvertTimezone)
                for name in ("source_tz", "target_tz")
                for zone in (conversion.args.get(name),)
                if isinstance(zone, sge.Literal) and zone.is_string
            }
        )
    )


def read_stage(
    prepared: PreparedSources,
    stage: SourceRead,
    lowered: LoweredRead,
    *,
    value_keys: tuple[str, ...] = (),
) -> StageOutput:
    """Submit one lowered SourceRead and normalize its pandas result."""
    if read_attribute(stage) is not None and require_attribute(stage).parent_context is None:
        from marivo.analysis.compiler.attribute_inputs import input_failure

        input_failure(
            "missing_component_proof",
            "the source read lacks its checked original-parent association",
        )
    expression = _native_query_expression(prepared, stage, lowered)
    lowered = replace(lowered, expression=expression)
    source = prepared.sources.get(stage.source_id, prepared.source)
    qualified = _qualify_query_read(prepared, stage, lowered)
    issued = source.compile(
        qualified,
        expression,
        purpose=f"query.{stage.stage_id}",
        expected_schema=expression.schema().to_pyarrow(),
    )
    if source.provider.name == "mysql" and "CONVERT_TZ(" in issued.sql.upper():
        source.verify_source_timezones(_mysql_source_timezones(issued.sql))
    from decimal import DecimalException

    try:
        frame = source.execute(issued)
    except (TypeError, ValueError, OverflowError, DecimalException) as error:
        raise MaterializationError(
            expected="native Ibis pandas values compatible with the compiled expression types",
            received=f"{source.provider.name}: {type(error).__name__}: {error}",
            repair="Inspect the native Ibis conversion error and result dtype. If a wider numeric carrier is intended, author .cast(ibis.dtype('decimal(38,0)')) in the Entity expression before reduction, or rescale inputs to the intended unit. Retry frame.compute(); Marivo does not widen or switch kernels automatically.",
            stage="source_conversion",
            help_target="analysis_frame.compute",
        ) from error
    except Exception as error:
        from marivo._errors import MarivoError

        if isinstance(error, MarivoError):
            raise
        raise MaterializationError(
            expected="successful native source execution for the compiled expression",
            received=f"{source.provider.name}: {type(error).__name__}: {error}",
            repair="Inspect the native backend failure and correct the input range or expression. If a wider carrier is intended, author .cast(ibis.dtype('decimal(38,0)')) in the Entity expression before reduction, or rescale inputs to the intended unit. Decimal transport supports precision <=38. Retry frame.compute(); source failures do not switch execution paths.",
            stage="source_execution",
            help_target="analysis_frame.compute",
        ) from error
    if read_attribute(stage) is not None:
        from marivo.analysis.materialization.query_components import complete_attribute_inputs

        _validate_source_checks(stage, frame, lowered.numeric_checks)
        from marivo.analysis.compiler.attribute_inputs import original_input_slot

        layout = require_attribute(stage)
        originals: dict[tuple[int, str], pd.DataFrame] = {}
        for kind, keys in (
            ((1, layout.parent_keys),) if layout.scope == "parent" else ((0, layout.keys),)
        ):
            selected = frame.loc[frame[layout.kind] == kind].reset_index(drop=True)
            for leaf in layout.leaves:
                selected = frame.loc[
                    (frame[layout.kind] == kind) & (frame[layout.fiber] == leaf.fiber)
                ].reset_index(drop=True)
                reduction = read_reduction(leaf.read)
                assert reduction is not None
                prefix = original_input_slot(kind, leaf.read.occurrence, "")
                names = {
                    name: name.removeprefix(prefix)
                    for name in selected.columns
                    if name.startswith(prefix)
                }
                names.update({name: name for name in (*keys, reduction.value_slot)})
                raw = selected.loc[:, list(names)].rename(columns=names)
                original_stage = replace(
                    leaf.read,
                    keys=keys,
                    columns=tuple(
                        column
                        for column in leaf.read.columns
                        if column.slot_id not in leaf.read.keys or column.slot_id in keys
                    ),
                    program=ReductionRead(require_reduction(leaf.read)),
                )
                checks = tuple(
                    (name.removeprefix(prefix), check)
                    for name, check in lowered.numeric_checks
                    if name.startswith(prefix)
                )
                completed = _complete_source_frame(original_stage, raw, checks)
                originals[(kind, reduction.value_slot)] = completed.table
        # Formula originals are actual native outputs of this same statement.
        # Capture their physical carriers before the root normalizer consumes
        # any conservative compiler metadata.
        layout = _actual_attribute_layout(layout, frame)
        fine, inputs = complete_attribute_inputs(layout, stage.stage_id, frame, originals)
        from marivo.analysis.materialization.graph_exchange import CheckReceipt

        inputs = replace(
            inputs,
            receipts=(
                *(CheckReceipt(name, stage.stage_id) for name, _ in lowered.numeric_checks),
                *inputs.receipts,
            ),
        )
        return StageOutput.from_frame(
            fine,
            source_columns(fine, stage.columns, stage.keys),
            stage.keys,
            receipts=inputs.receipts,
            attribute_inputs=(inputs,),
            source_rows=len(frame),
        )
    if read_members(stage):
        return _complete_common_read(stage, frame, lowered.numeric_checks)
    return _complete_source_frame(stage, frame, lowered.numeric_checks, value_keys=value_keys)


def _validate_source_checks(
    stage: SourceRead,
    frame: pd.DataFrame,
    numeric_checks: tuple[tuple[str, NumericCheck], ...],
) -> None:
    """Dispatch source checks with their original owner, coordinates and repair."""
    retained_checks = {column.slot_id for column in stage.columns}
    for name in (
        "__numeric_present",
        "__sample_present",
        *(name for name, _ in numeric_checks),
    ):
        if name in retained_checks and name in frame.columns:
            frame[name] = frame[name].astype("boolean").fillna(False)
    for name, check in numeric_checks:
        if name in retained_checks:
            continue
        layout = read_attribute(stage)
        if layout is not None:
            from marivo.analysis.core.attribute import AttributeCheckWitness
            from marivo.analysis.core.data_validation import normalize_stage_frame
            from marivo.analysis.core.frozen_calendar import frozen_key_rows
            from marivo.analysis.materialization.query_components import (
                attribute_failure_context,
                attribute_witness_order,
            )

            for descriptor in (item for item in layout.checks if item.name == name):
                kind = (
                    4
                    if descriptor.scope == "preparation"
                    else 1
                    if descriptor.scope == "parent"
                    else 0
                )
                scoped = frame.loc[
                    (frame[layout.kind] == kind)
                    & (frame[layout.fiber] == descriptor.leaves[0].fiber)
                ]
                failing = scoped.loc[scoped[name].astype("boolean").fillna(False)]
                if len(failing):
                    columns = descriptor.columns
                    coordinates = normalize_stage_frame(
                        failing.loc[:, [column.slot_id for column in columns]], columns, ()
                    )
                    coordinates = coordinates.iloc[
                        list(attribute_witness_order(coordinates, columns))[:6]
                    ].reset_index(drop=True)
                    witness = AttributeCheckWitness(
                        name, stage.source_id, tuple(frozen_key_rows(coordinates, columns)), columns
                    )
                    context = attribute_failure_context(
                        layout,
                        coordinates,
                        descriptor.leaves,
                        columns,
                        witness,
                        fine=descriptor.scope == "fine",
                    )
                    _attribute_source_check_failure(
                        stage, name, check.expected, check.received, coordinates, context
                    )
            continue
        violation = frame[name].astype("boolean").fillna(False)
        if violation.any():
            reduction = read_reduction(stage)
            if name == "sample_check.coverage" and reduction is not None:
                from marivo.analysis.methods._sample_coverage import (
                    native_sampling_witness,
                    sampling_context,
                    sampling_error,
                )

                sampling_witness = native_sampling_witness(reduction.coverage, frame)
                if sampling_witness is not None:
                    raise sampling_error(sampling_context(sampling_witness))
            finish = read_finish(stage)
            witness_slots = (
                dict(finish.frame_top.numeric_witnesses).get(name)
                if finish is not None and finish.frame_top is not None
                else None
            )
            witness_slots = witness_slots or tuple(
                sorted(
                    (column for column in frame.columns if column.startswith(name + ".witness.")),
                    key=lambda column: int(column.rsplit(".", 1)[1]),
                )
            )
            coordinates = (
                frame.loc[violation, list(witness_slots)]
                .head(1)
                .rename(columns=dict(zip(witness_slots, stage.keys, strict=False)))
                if witness_slots
                else frame.loc[violation, list(stage.keys)].head(6)
            )
            _source_check_failure(
                stage,
                name,
                check.expected,
                check.received,
                coordinates,
            )


def _source_check_failure(
    stage: SourceRead,
    name: str,
    expected: str,
    received: str,
    coordinates: pd.DataFrame,
) -> NoReturn:
    """Keep the owning preparation/numeric failure for actual or known-empty input."""
    from marivo.semantic._numeric_lowering import numeric_failure

    received = (
        f"{received}; source={stage.source_id}; coordinates={coordinates.to_dict(orient='records')}"
    )
    if any(name == column for column, _, _ in stage.preparation_checks):
        if expected == "an output Population covering every required complete coordinate":
            raise MaterializationError(
                expected=expected,
                received=received,
                repair="Include every observed output coordinate in by=Population, or explicitly filter contributions before choosing that output domain.",
                stage="population_coverage",
                help_target="analysis_frame.compute",
            )
        if expected == "non-null complete identity keys":
            raise MaterializationError(
                expected=expected,
                received=received,
                repair="Supply every non-null primary-key component for each represented Entity identity.",
                stage="population_identity",
                help_target="analysis_frame.compute",
            )
    numeric_failure(expected, received)


def _attribute_source_check_failure(
    stage: SourceRead,
    name: str,
    expected: str,
    received: str,
    coordinates: pd.DataFrame,
    context: AttributeFailureContext,
) -> NoReturn:
    """Attach typed evidence without replacing the original source failure owner."""
    from marivo.semantic.errors import SemanticExpressionError

    try:
        _source_check_failure(stage, name, expected, received, coordinates)
    except MaterializationError as error:
        error.attribute_context = context
        raise
    except SemanticExpressionError as error:
        error.details["attribute_context"] = context
        raise


def _complete_source_frame(
    stage: SourceRead,
    frame: pd.DataFrame,
    numeric_checks: tuple[tuple[str, NumericCheck], ...],
    *,
    value_keys: tuple[str, ...] = (),
    ordered: bool = True,
) -> StageOutput:
    """Complete one logical source occurrence and its original fine checks."""
    source_rows = len(frame)
    from marivo.analysis.materialization.query_components import ComponentFrame

    components: tuple[ComponentFrame, ...] = ()
    if read_components(stage) is not None:
        from marivo.analysis.materialization.query_components import complete_components

        frame, component = complete_components(require_components(stage), frame)
        components = (component,)
    _validate_source_checks(stage, frame, numeric_checks)
    from marivo.semantic._numeric_lowering import numeric_failure

    retained_checks = {column.slot_id for column in stage.columns}
    if "__numeric_present" in frame.columns and "__numeric_present" not in retained_checks:
        frame = frame.loc[frame["__numeric_present"].astype("boolean").fillna(False)]
    if read_finish(stage) is not None:
        from marivo.analysis.materialization.query_key_fusion import finish_native_query

        frame = finish_native_query(require_finish(stage), frame)
    from fractions import Fraction

    from marivo._definition.expressions import SourceAggregate, SourceWeightedMean

    reduction = read_reduction(stage)
    if (
        reduction is not None
        and reduction.duration is not None
        and isinstance(reduction.node, SourceAggregate)
    ):
        node = reduction.node
        if node.reducer in ("mean", "median", "quantile"):
            finished: list[int | None] = []
            for row in frame.to_dict("records"):
                count = int(row["__duration_count"])
                if count == 0:
                    finished.append(None)
                    continue
                lower = int(row[reduction.value_slot])
                if node.reducer == "mean":
                    exact = Fraction(lower, count)
                else:
                    q = 0.5 if node.reducer == "median" else node.q
                    assert q is not None
                    position = Fraction(str(q)) * (count - 1)
                    fraction = position - (position.numerator // position.denominator)
                    exact = lower + fraction * (int(row["__duration_upper"]) - lower)
                finished.append(round(exact))
            frame[reduction.value_slot] = pd.Series(
                finished, dtype="int64[pyarrow]", index=frame.index
            )
    if reduction is not None and "__quantile_count" in frame.columns:
        import math
        from decimal import Decimal

        from marivo.analysis.methods.numeric_state import finish_division
        from marivo.analysis.methods.physical import DecimalType

        assert isinstance(reduction.node, SourceAggregate)
        node = reduction.node
        dtype = next(
            column.dtype for column in stage.columns if column.slot_id == reduction.value_slot
        )
        q = 0.5 if node.reducer == "median" else node.q
        assert q is not None
        quantile_values: list[int | float | Decimal | None] = []
        for row in frame.to_dict("records"):
            raw_count = row["__quantile_count"]
            # Coverage-only rows and predicate groups without numeric inputs
            # carry no bounds. Keep their presence/checks for local completion.
            if pd.isna(raw_count) or raw_count == 0:
                quantile_values.append(None)
                continue
            count = int(raw_count)
            position = Fraction(str(q)) * (count - 1)
            fraction = position - (position.numerator // position.denominator)
            lower = row["__quantile_lower"]
            upper = row["__quantile_upper"]
            assert isinstance(lower, (int, float, Decimal))
            assert isinstance(upper, (int, float, Decimal))
            if any(
                isinstance(bound, float) and not math.isfinite(bound) for bound in (lower, upper)
            ):
                # Keep native nonfinite bounds as values. Exact rational
                # interpolation applies only to finite carriers.
                left, right = float(lower), float(upper)
                if math.isnan(left) or math.isnan(right):
                    interpolated = float("nan")
                elif fraction == 0 or left == right:
                    interpolated = left
                elif math.isinf(left) and math.isinf(right):
                    interpolated = float("nan")
                else:
                    interpolated = left if math.isinf(left) else right
                quantile_values.append(interpolated)
                continue
            exact = Fraction(lower) + fraction * (Fraction(upper) - Fraction(lower))
            quantile_values.append(
                finish_division(
                    exact.numerator,
                    exact.denominator,
                    DecimalType(dtype.precision, dtype.scale),
                )
                if isinstance(dtype, DecimalDataType)
                else float(exact)
            )
        frame[reduction.value_slot] = (
            pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array(quantile_values, type=pa.float64(), from_pandas=False)
                ),
                index=frame.index,
            )
            if dtype == ScalarDataType("float64")
            else pd.Series(quantile_values, dtype=object, index=frame.index)
        )
    if (
        reduction is not None
        and reduction.duration is not None
        and isinstance(reduction.node, SourceWeightedMean)
    ):
        values: list[int | None] = []
        for numerator, denominator in zip(
            frame[reduction.value_slot], frame["__duration_weight_sum"], strict=True
        ):
            ticks = None if denominator == 0 else round(Fraction(numerator) / Fraction(denominator))
            if ticks is not None and not -(2**63) <= ticks < 2**63:
                numeric_failure("signed int64 Duration ticks", "weighted_mean output overflow")
            values.append(ticks)
        frame[reduction.value_slot] = pd.Series(values, dtype="int64[pyarrow]", index=frame.index)
    for column in stage.columns:
        if isinstance(column.dtype, DurationDataType):
            try:
                tick_array = pa.array(frame[column.slot_id], type=pa.int64(), from_pandas=True)
                frame[column.slot_id] = pd.Series(
                    pd.arrays.ArrowExtensionArray(tick_array.cast(pa.duration(column.dtype.unit))),
                    index=frame.index,
                )
            except (pa.ArrowException, OverflowError, ValueError, TypeError) as error:
                numeric_failure("lossless signed int64 Duration ticks", str(error))
    output = frame.loc[:, [c.slot_id for c in stage.columns]]
    if ordered and stage.keys and read_finish(stage) is None:
        output = output.sort_values(
            list(stage.keys), kind="stable", na_position="last"
        ).reset_index(drop=True)
    completed = StageOutput.from_source_frame(
        output,
        stage.columns,
        stage.keys,
        components=components,
        value_keys=value_keys,
        source_rows=source_rows,
    )
    if read_finish(stage) is not None:
        completed = replace(completed, layout=require_finish(stage).layout)
    return completed


def _complete_common_read(
    stage: SourceRead,
    frame: pd.DataFrame,
    numeric_checks: tuple[tuple[str, NumericCheck], ...],
) -> StageOutput:
    """Complete logical occurrences while retaining an unchanged joint row layout."""
    from marivo.analysis.compiler.observation import common_slot
    from marivo.analysis.materialization.query_components import ComponentFrame

    members = read_members(stage)
    same_rows = all(
        read_components(member) is None
        and read_finish(member) is None
        and member.keys == stage.keys
        and (
            common_slot(index, "__numeric_present") not in frame.columns
            or "__numeric_present" in {column.slot_id for column in member.columns}
        )
        for index, member in enumerate(members)
    )
    tables: list[pd.DataFrame] = []
    components: list[ComponentFrame] = []
    for index, member in enumerate(members):
        assert read_reduction(member) is not None
        names = {
            column.slot_id: column.slot_id
            if column.slot_id in member.keys
            or column.slot_id == require_reduction(member).value_slot
            else common_slot(index, column.slot_id)
            for column in member.columns
        }
        presence = common_slot(index, "__numeric_present")
        if presence in frame.columns and "__numeric_present" not in names:
            names["__numeric_present"] = presence
        suffix = f".block{index}"
        names.update(
            {name.removesuffix(suffix): name for name, _ in numeric_checks if name.endswith(suffix)}
        )
        if read_components(member) is not None:
            names.update(
                {
                    column.slot_id: common_slot(index, column.slot_id)
                    for column in require_components(member).raw_columns
                    if column.slot_id not in member.keys
                }
            )
            names[require_components(member).present] = common_slot(
                index, require_components(member).present
            )
        local = (
            frame.loc[:, list(names.values())]
            .rename(columns={value: key for key, value in names.items()})
            .copy()
        )
        checks = tuple(
            (name.removesuffix(suffix), check)
            for name, check in numeric_checks
            if name.endswith(suffix)
        )
        output = _complete_source_frame(member, local, checks, ordered=not same_rows)
        components.extend(output.components)
        tables.append(
            output.table.rename(
                columns={
                    column.slot_id: common_slot(index, column.slot_id)
                    for column in member.columns
                    if column.slot_id not in member.keys
                    and column.slot_id != require_reduction(member).value_slot
                }
            )
        )
    if same_rows:
        # Every member finished the same physical rows without reordering or
        # filtering. Preserve that joint layout instead of rebuilding it by key.
        result = frame.loc[:, [column.slot_id for column in stage.columns]].reset_index(drop=True)
        for table in tables:
            for name in table.columns:
                if name not in stage.keys:
                    result[name] = table[name]
    else:
        result = tables[0]
        for other in tables[1:]:
            result = (
                result.merge(
                    other, on=list(stage.keys), how="outer", validate="one_to_one", sort=False
                )
                if stage.keys
                else result.join(other)
            )
    result = result.loc[:, [column.slot_id for column in stage.columns]]
    retained_checks = {name for name, _ in numeric_checks}
    retained_checks.update(
        common_slot(index, name)
        for index in range(len(read_members(stage)))
        for name in ("__numeric_present", "__sample_present")
    )
    for column in stage.columns:
        if column.slot_id in retained_checks and column.dtype == ScalarDataType("boolean"):
            result[column.slot_id] = result[column.slot_id].astype("boolean").fillna(False)
    if stage.keys:
        result = result.sort_values(
            list(stage.keys), kind="stable", na_position="last"
        ).reset_index(drop=True)
    return StageOutput.from_source_frame(
        result,
        stage.columns,
        stage.keys,
        components=tuple(components),
        source_rows=len(frame),
    )


def execute_numeric_query(
    prepared: PreparedSources | None,
    lowered: LoweredQuery,
    *,
    observe_read: Callable[[SourceRead, LoweredRead, StageOutput], None] | None = None,
    observe_local: Callable[[LocalCompute, StageOutput], None] | None = None,
    prepared_by_stage: Mapping[str, PreparedSources] | None = None,
    read_fixed: Callable[[FrameInput], CompletedFrameData] | None = None,
    adapt_source: Callable[[str, StageOutput], StageOutput] | None = None,
    source_outputs: Mapping[str, str] | None = None,
    observe_frame: Callable[[FrameCompute, StageOutput], None] | None = None,
) -> StageOutput:
    """Execute the fixed numeric schedule and return invocation-local pandas data."""
    from marivo.analysis.core.model import reject
    from marivo.analysis.materialization.graph_execution import run_query_plan
    from marivo.analysis.materialization.graph_local_execution import run_local

    required_by_stage = {required.stage_id: required for required in lowered.plan.required_slots}
    from marivo.analysis.materialization.query_runtime_types import source_value_keys

    value_keys = source_value_keys(lowered.plan)
    parameter_relations: dict[tuple[SourceSession, str], ir.Table] = {}

    def transport(stage_id: str, output: StageOutput) -> StageOutput:
        if not lowered.plan.required_slots:
            return output
        required = required_by_stage[stage_id]
        columns = tuple(column for column in output.columns if column.slot_id in required.columns)
        return replace(
            output,
            table=output.table.loc[:, [column.slot_id for column in columns]],
            columns=columns,
            layout=replace(output.layout, columns=columns) if output.layout is not None else None,
            components=tuple(
                component
                for component in output.components
                if component.layout.reduction.value_slot in required.components
            ),
            population_frames=output.population_frames if required.retain_populations else (),
        )

    def read(stage: SourceRead) -> StageOutput:
        selected = (
            prepared_by_stage.get(stage.stage_id) if prepared_by_stage is not None else prepared
        )
        if selected is None:
            reject(
                "prepared source connections",
                stage.stage_id,
                "Prepare the source before execution.",
                "analysis.query_source",
            )
        request = lowered.read(stage.stage_id)
        output = read_stage(selected, stage, request, value_keys=value_keys.get(stage.stage_id, ()))
        if observe_read is not None:
            observe_read(stage, request, output)
        return output

    def local(stage: LocalCompute, inputs: tuple[StageOutput, ...]) -> StageOutput:
        from marivo.analysis.materialization.query_runtime_types import effective_local

        stage = effective_local(stage, inputs) if not local_steps(stage) else stage
        output = run_local(stage, inputs)
        if observe_local is not None:
            observe_local(stage, output)
        return output

    def dependent(stage: SourceRead, inputs: tuple[StageOutput, ...]) -> StageOutput:
        from marivo.analysis.core.domain_captures import DomainPreparationError
        from marivo.analysis.materialization.query_parameters import instantiate, parameter_data

        selected = (
            prepared_by_stage.get(stage.stage_id) if prepared_by_stage is not None else prepared
        )
        assert selected is not None
        request = lowered.read(stage.stage_id)
        expression = request.expression
        owner = selected.sources.get(stage.source_id, selected.source)
        for parameter in stage.parameters:
            source = inputs[stage.inputs.index(parameter.stage_id)]
            try:
                relation = None
                if owner.provider.name == "duckdb" and parameter.columns:
                    key = (owner, parameter.placeholder)
                    relation = parameter_relations.get(key)
                    if relation is None:
                        relation = owner._bind_key_relation(
                            parameter_data(parameter.columns, source)
                        )
                        parameter_relations[key] = relation
                expression = instantiate(
                    expression,
                    parameter.placeholder,
                    parameter.columns,
                    source,
                    relation=relation,
                    provider=owner.provider.name,
                    source=owner,
                )
            except (
                TypeError,
                ValueError,
                OverflowError,
                RecursionError,
                NotImplementedError,
            ) as error:
                raise DomainPreparationError(
                    "query.population_parameters",
                    "prepare",
                    "a native typed complete-tuple Population parameter",
                    str(error),
                    "Reduce the explicit domain or use a datasource/type with an admitted native expression route.",
                ) from error
        request = replace(request, expression=expression)
        if read_attribute(stage) is not None:
            from marivo.analysis.compiler.attribute_inputs import input_failure
            from marivo.analysis.core.attribute import AttributeParentContext
            from marivo.analysis.methods.frame_attribute import attribute_parent_mapping

            binding = stage.attribute_parent
            if binding is None:
                input_failure(
                    "missing_component_proof",
                    "the Attribute source lacks its exact parent projection dependency",
                )
            layout = require_attribute(stage)
            parent_columns = {column.slot_id: column for column in layout.columns}
            projection = inputs[binding.projection_input_index]
            parent_context = AttributeParentContext(
                tuple(column for axis in binding.parent_schema.axes for column in axis.columns),
                attribute_parent_mapping(
                    binding.parent_schema,
                    projection,
                    tuple(parent_columns[name] for name in layout.parent_keys),
                ),
            )
            stage = replace(
                stage,
                program=AttributeRead(
                    replace(layout, parent_context=parent_context),
                    require_reduction(stage),
                    read_members(stage),
                ),
            )
        output = read_stage(selected, stage, request, value_keys=value_keys.get(stage.stage_id, ()))
        if observe_read is not None:
            observe_read(stage, request, output)
        return output

    def fixed(stage: FrameInput) -> StageOutput:
        if read_fixed is None:
            reject(
                "an available complete fixed-input reader",
                stage.input_id,
                "Bind memory data or the qualified stored reader.",
                "frame.input",
            )
        data = read_fixed(stage)
        if data.schema != stage.schema:
            reject(
                "the exact fixed input schema",
                repr(data.schema),
                "Use this input's complete descriptor.",
                "frame.input",
            )
        return StageOutput.from_completed(data)

    def frame(stage: FrameCompute, inputs: tuple[StageOutput, ...]) -> StageOutput:
        output = run_local(stage, inputs)
        if observe_frame is not None:
            observe_frame(stage, output)
        return output

    def finish(stage: QueryStage, output: StageOutput) -> StageOutput:
        if source_outputs is not None and stage.stage_id in source_outputs:
            if adapt_source is None:
                reject(
                    "a complete source-to-data boundary adapter",
                    stage.stage_id,
                    "Freeze the explicit source output.",
                    "frame.source",
                )
            output = adapt_source(source_outputs[stage.stage_id], output)
        return transport(stage.stage_id, output)

    result = run_query_plan(
        lowered.plan, read, local, dependent, fixed=fixed, frame=frame, finish=finish
    )
    from marivo.analysis.materialization.graph_exchange import source_columns

    planned_output = next(
        stage for stage in lowered.plan.stages if stage.stage_id == lowered.plan.publish
    )
    source_columns(result.table, planned_output.columns, result.keys)
    return replace(result, key_groups=lowered.plan.output_groups)


def run_frame_plan(
    compiled: CompiledFramePlan,
    *,
    source_prepared: Mapping[str, PreparedSources],
    read_fixed: Callable[[FrameInput], CompletedFrameData],
    adapt_source: Callable[[str, StageOutput], StageOutput],
    observe_read: Callable[[SourceRead, LoweredRead, StageOutput], None] | None = None,
    observe_local: Callable[[LocalCompute | FrameCompute, StageOutput], None] | None = None,
) -> StageOutput:
    """Bind explicit inputs to the existing one-invocation numeric scheduler."""
    prepared = {
        stage_id: source_prepared[input_id]
        for stage_id, input_id in compiled.source_stages
        if input_id in source_prepared
    }
    return execute_numeric_query(
        None,
        compiled.lowered,
        observe_read=observe_read,
        observe_local=observe_local,
        prepared_by_stage=prepared,
        read_fixed=read_fixed,
        adapt_source=adapt_source,
        source_outputs=dict(compiled.source_outputs),
        observe_frame=observe_local,
    )


def _attribute_calendar_value(
    table: ir.Table,
    columns: dict[str, str],
    time_column: str,
    calendar: CalendarCoordinate | IntervalCoordinate,
    window_key: int | None,
    slot: str,
) -> ir.Value:
    import ibis

    from marivo.analysis.compiler.observation import calendar_field, temporal_bound

    if window_key is not None:
        return ibis.literal(window_key).cast("int64")
    if calendar_field(slot) in columns:
        return table[columns[calendar_field(slot)]]
    time = table[columns[time_column]]
    if isinstance(calendar, IntervalCoordinate):
        return ibis.ifelse(
            (time >= temporal_bound(time, calendar.start, calendar.timezone))
            & (time < temporal_bound(time, calendar.end, calendar.timezone)),
            ibis.literal(0),
            ibis.null().cast("int64"),
        )
    return ibis.cases(
        *(
            (
                (time >= temporal_bound(time, cell[2], calendar.timezone))
                & (time < temporal_bound(time, cell[3], calendar.timezone)),
                index,
            )
            for index, cell in enumerate(calendar.cells)
        ),
        else_=ibis.null().cast("int64"),
    )


def prepare_attribute_endpoint(
    prepared: PreparedSources,
    graph: BoundGraph,
    state: CompiledSemanticState,
    mapping: AttributeFrameValues,
    side: int,
    rules: tuple[tuple[str, AttributeRule], ...],
    *,
    memo: InvocationMemo,
) -> tuple[PreparedSources, QueryPlan]:
    """Prepare and qualify this captured endpoint's original contribution inputs."""
    from marivo.analysis.compiler.attribute_inputs import bundle_attribute_plan, input_failure
    from marivo.analysis.compiler.graph_plan import plan_query

    positions = mapping.left_parents if side == 0 else mapping.right_parents
    prepared, scope_columns, parent_checks = prepare_attribute_sources(
        prepared, graph, state, len(positions)
    )
    original = plan_query(graph, prepared.schemas, memo=memo, require_original=True)
    reads = tuple(
        stage
        for stage in original.stages
        if isinstance(stage, SourceRead) and read_components(stage) is not None
    )
    for root in dict.fromkeys(stage.source_id for stage in reads):
        group = tuple(stage for stage in reads if stage.source_id == root)
        owner = prepared.sources.get(root, prepared.source)
        relations = (
            tuple(bound.relation for bound in prepared.bound.values())
            if owner.provider.name == "duckdb"
            else (prepared.relations[group[0].stage_id],)
        )
        for relation in relations:
            issue = qualify_original_source(prepared, group, relation, attribute=True)
            if issue is not None:
                input_failure("unqualified_contribution_route", issue)
    plan = bundle_attribute_plan(
        original,
        rules,
        positions,
        scope_columns,
        mapping.left_projection if side == 0 else mapping.right_projection,
        "left" if side == 0 else "right",
        parent_checks,
    )
    return prepared, plan


def prepare_attribute_sources(
    prepared: PreparedSources,
    graph: BoundGraph,
    state: CompiledSemanticState,
    parent_count: int,
) -> tuple[
    PreparedSources,
    dict[str, tuple[tuple[str, str], ...]],
    dict[str, tuple[tuple[str, str, str], ...]],
]:
    """Prepare independent unclassified parent and classified fine root streams."""
    import ibis

    from marivo._definition.types import AxisBinding, coordinate_read_keys
    from marivo.analysis.binding.membership import membership_reads
    from marivo.analysis.compiler.attribute_inputs import (
        attribute_parent_key,
        attribute_preparation_key,
        input_failure,
    )
    from marivo.analysis.materialization.query_preparation import prepare_fields
    from marivo.datasource.ir import TableSourceIR
    from marivo.datasource.timezone import probe_engine_timezone

    observation = graph.signature.observation
    assert observation is not None
    if any(not isinstance(bound.source, TableSourceIR) for bound in prepared.bound.values()):
        input_failure(
            "unqualified_contribution_route",
            "Attribute requires native physical tables",
        )
    # ContributionBinding remains the only source scope authority.
    from marivo._definition.observations import ContributionBinding

    contributions: dict[str, list[ContributionBinding]] = {}
    for contribution in observation.contributions:
        contributions.setdefault(contribution.root.ref.path, []).append(contribution)
    relations = dict(prepared.relations)
    schemas = list(prepared.schemas)
    scoped_columns: dict[str, tuple[tuple[str, str], ...]] = {}
    parent_checks: dict[str, tuple[tuple[str, str, str], ...]] = {}
    for root, leaves in contributions.items():
        owner = prepared.sources.get(root, prepared.source)
        scope_groups: dict[
            tuple[ObservationPeriod | None, int | None], list[ContributionBinding]
        ] = {}
        for leaf in leaves:
            scope_groups.setdefault((leaf.period, leaf.window_key), []).append(leaf)
        first_root = leaves[0]
        if any(
            (
                leaf.time,
                leaf.intrinsic_conditions,
                leaf.common_condition,
                leaf.coordinates,
                leaf.memberships,
                leaf.population_reads,
                leaf.calendars,
                leaf.sample_axes,
            )
            != (
                first_root.time,
                first_root.intrinsic_conditions,
                first_root.common_condition,
                first_root.coordinates,
                first_root.memberships,
                first_root.population_reads,
                first_root.calendars,
                first_root.sample_axes,
            )
            for leaf in leaves
        ):
            input_failure(
                "unqualified_contribution_route", f"{root}: incompatible non-temporal source scopes"
            )
        raw_fibers: list[ir.Table] = []
        parent_fibers: list[ir.Table] = []
        check_fibers: list[ir.Table] = []
        parent_check_fibers: list[ir.Table] = []
        all_leaves = leaves
        for fiber, leaves in enumerate(scope_groups.values()):
            first = leaves[0]

            if first.sample_axes:
                input_failure(
                    "missing_component_proof", f"{root}: unproved calendar/window/sample sequence"
                )
            merged = replace(
                first, reads=tuple(dict.fromkeys(read for leaf in leaves for read in leaf.reads))
            )
            parent_axes = (
                ()
                if observation.coordinates.parent_scope is None
                else observation.coordinates.parent_scope.axes
            )
            from marivo._definition.relations import AttributeKeys, PopulationProgram

            scope = observation.coordinates.parent_scope
            fixed_parent_axes = (
                tuple(axis for axis, _ in scope.root.fixed)
                if isinstance(scope, PopulationProgram) and isinstance(scope.root, AttributeKeys)
                else ()
            )
            parent_memberships = tuple(
                rule
                for rule in first.memberships
                if all(
                    AxisBinding(read.entity, read.field, read.path, "field", read.at)
                    in (*parent_axes, *fixed_parent_axes)
                    for read in rule.fields
                )
            )
            new_memberships = tuple(
                binding for binding in first.memberships if binding not in parent_memberships
            )
            member_reads = {
                read
                for binding in new_memberships
                for read in membership_reads(binding, first.root)
            }
            if any(read in member_reads for read in merged.reads):
                input_failure(
                    "unqualified_contribution_route",
                    f"{root}: a new membership field is also an input/filter value",
                )
            parent_reads = first.coordinates[: parent_count - len(first.calendars)]
            baseline = replace(
                merged,
                reads=tuple(read for read in merged.reads if read not in member_reads),
                coordinates=parent_reads,
                memberships=parent_memberships,
            )
            reader = probe_engine_timezone(owner._backend)
            scoped = prepare_fields(baseline, prepared.entities, state, reader)
            classified = prepare_fields(merged, prepared.entities, state, reader)
            scoped_map, assigned_map = dict(scoped.columns), dict(classified.columns)
            parent_paths = tuple(
                path for read in parent_reads for path in coordinate_read_keys(read)
            )
            from marivo.analysis.compiler.observation import reduction_input

            reduction = reduction_input(
                first, "attribute.value", first.calendars[0][1] if first.calendars else None
            )
            parent_slots = tuple(
                (slot, scoped_map[path]) for slot, path in reduction.axes if path in parent_paths
            )
            for slot, calendar in first.calendars:
                assert reduction.time_column is not None
                name = "__attribute_calendar_" + slot.replace(".", "_")

                scoped = replace(
                    scoped,
                    table=scoped.table.mutate(
                        **{
                            name: _attribute_calendar_value(
                                scoped.table,
                                scoped_map,
                                reduction.time_column,
                                calendar,
                                first.window_key,
                                slot,
                            )
                        }
                    ),
                )
                classified = replace(
                    classified,
                    table=classified.table.mutate(
                        **{
                            name: _attribute_calendar_value(
                                classified.table,
                                assigned_map,
                                reduction.time_column,
                                calendar,
                                first.window_key,
                                slot,
                            )
                        }
                    ),
                )
                parent_slots = (*parent_slots, (slot, name))
            parent_slots = tuple(sorted(parent_slots, key=lambda item: int(item[0].split(".")[1])))
            scoped_columns[root] = parent_slots
            base = scoped.table
            from marivo.analysis.compiler.predicate import lower_condition

            base_fields = {path: base[column] for path, column in scoped.columns}
            source_predicates = (
                *first.intrinsic_conditions,
                *((first.common_condition,) if first.common_condition is not None else ()),
            )
            if source_predicates:
                base = base.filter(
                    *(
                        lower_condition(predicate, base_fields).value
                        for predicate in source_predicates
                    )
                )
            # Parent selection is a typed complete-key relation. Temporal keys are
            # not legal ordinary predicates and must retain their joint fibers here.
            parent_scope = observation.coordinates.parent_scope
            if parent_scope is not None:
                from marivo._definition.graph import computation_fingerprint
                from marivo._definition.relations import AttributeKeys, PopulationProgram
                from marivo._definition.types import CalendarCoordinate
                from marivo.analysis.compiler.populations import frame_key_groups

                assert isinstance(parent_scope, PopulationProgram) and isinstance(
                    parent_scope.root, AttributeKeys
                )
                parameter_relation = prepared.entities[
                    "population-parameter:" + computation_fingerprint(parent_scope)
                ]
                parent_predicates: list[ir.BooleanValue] = []
                parent_pairs: list[tuple[str, str]] = []
                source_columns: tuple[str, ...]
                for axis, group in zip(
                    parent_scope.axes, frame_key_groups(parent_scope.root), strict=True
                ):
                    if isinstance(axis, (CalendarCoordinate, IntervalCoordinate)):
                        slot = next(slot for slot, calendar in first.calendars if calendar == axis)
                        source_columns = (dict(parent_slots)[slot],)
                    else:
                        ordinary_axes = tuple(
                            a for a in graph.signature.axes if isinstance(a, AxisBinding)
                        )[: len(parent_reads)]
                        read = parent_reads[ordinary_axes.index(axis)]
                        source_columns = tuple(
                            scoped_map[path] for path in coordinate_read_keys(read)
                        )
                    parent_pairs.extend(zip(source_columns, group, strict=True))
                    parent_predicates.extend(
                        base[column].identical_to(parameter_relation[name])
                        for column, name in zip(source_columns, group, strict=True)
                    )
                base = (
                    base.semi_join(parameter_relation, parent_predicates)
                    if parent_predicates
                    else base.filter(parameter_relation.count() > 0)
                )
            mapping = dict(scoped_map)
            classification_columns = tuple(
                (path, name) for path, name in assigned_map.items() if path not in mapping
            )
            if new_memberships:
                fine = classified.table.mutate(__attribute_assigned=ibis.literal(True))
                renamed = {
                    name: f"__attribute_fine_{index}" for index, name in enumerate(fine.columns)
                }
                fine = fine.rename(**{new: old for old, new in renamed.items()})
                identity = (
                    normalize_target_entity(state.registry, root).version_row_key
                    or normalize_target_entity(state.registry, root).primary_key
                )
                predicates = [base[name].identical_to(fine[renamed[name]]) for name in identity]
                predicates.extend(
                    base[column].identical_to(fine[renamed[column]]) for _, column in parent_slots
                )
                joined = base.left_join(fine, predicates)
                mapping = dict(scoped_map)
                projections = {name: base[name] for name in base.columns}
                classification_columns = tuple(
                    (path, name) for path, name in assigned_map.items() if path not in mapping
                )
                for path, source_name in classification_columns:
                    name = f"__attribute_member_{len(mapping)}"
                    projections[name] = fine[renamed[source_name]]
                    mapping[path] = name
                projections["__attribute_assigned"] = fine[
                    renamed["__attribute_assigned"]
                ].fill_null(False)
                raw = joined.select(**projections).mutate(__attribute_fiber=ibis.literal(fiber))
            else:
                # Ordinary scalar classification trusts identity, relationship and
                # version declarations. Apply the same scope without a root self-join.
                fine = classified.table
                fine_fields = {path: fine[column] for path, column in classified.columns}
                if source_predicates:
                    fine = fine.filter(
                        *(lower_condition(p, fine_fields).value for p in source_predicates)
                    )
                if parent_scope is not None:
                    fine_parent_columns = {
                        column: assigned_map[path] for path, column in scoped.columns
                    }
                    fine_predicates = [
                        fine[fine_parent_columns.get(column, column)].identical_to(
                            parameter_relation[name]
                        )
                        for column, name in parent_pairs
                    ]
                    fine = (
                        fine.semi_join(parameter_relation, fine_predicates)
                        if fine_predicates
                        else fine.filter(parameter_relation.count() > 0)
                    )
                for path, _ in classification_columns:
                    mapping[path] = f"__attribute_member_{len(mapping)}"
                raw = fine.select(
                    *(
                        fine[name]
                        for name in scoped.table.columns
                        if name not in {v for _, v in scoped.columns}
                    ),
                    **{name: fine[assigned_map[path]] for path, name in mapping.items()},
                ).mutate(__attribute_fiber=ibis.literal(fiber))
            parent = base.mutate(
                **{
                    mapping[path]: ibis.null().cast(
                        classified.table[name].type().copy(nullable=True)
                    )
                    for path, name in classification_columns
                },
                __attribute_fiber=ibis.literal(fiber),
            )
            parent_fibers.append(parent)
            from marivo._definition.graph import computation_fingerprint
            from marivo.analysis.compiler.query_program import FrameParameter

            physical = (ops.DatabaseTable, ops.UnboundTable, ops.InMemoryTable, ops.SQLQueryResult)
            allowed = {
                id(leaf)
                for bound in prepared.bound.values()
                for leaf in bound.relation.op().find(physical)
            }
            parameters = tuple(
                parameter
                for schema in prepared.schemas
                for parameter in schema.parameters
                if isinstance(parameter, FrameParameter)
            )
            for parameter in parameters:
                relation = prepared.entities.get(
                    "population-parameter:" + computation_fingerprint(parameter.definition)
                )
                if relation is not None:
                    allowed.add(id(relation.op()))
            if any(id(leaf) not in allowed for leaf in raw.op().find(physical)):
                input_failure(
                    "unqualified_contribution_route",
                    f"{root}: an unprepared physical or parameter leaf",
                )
            schema = raw.schema().to_pyarrow()
            original_types = {
                field.field: field.dtype
                for item in prepared.schemas
                if item.source_id == root
                for field in item.fields
            }
            fields = tuple(
                replace(_admitted(schema, path, name), dtype=original_types[path])
                if isinstance(original_types.get(path), DurationDataType)
                else _admitted(schema, path, name)
                for path, name in mapping.items()
            )
            raw_fibers.append(raw)
            fine_preparation = classified.table.select(
                **{name: classified.table[assigned_map[path]] for path, name in mapping.items()},
                **{
                    name: classified.table[name]
                    for _, name in parent_slots
                    if name not in mapping.values()
                },
                **{name: classified.table[name] for name, _, _ in classified.checks},
            )
            check_fibers.append(fine_preparation.mutate(__attribute_fiber=ibis.literal(fiber)))
            parent_check_fibers.append(scoped.table.mutate(__attribute_fiber=ibis.literal(fiber)))
            parent_checks[root] = scoped.checks
            for leaf in leaves:
                stage_id = "read:" + ".".join(map(str, leaf.occurrence))
                relations[stage_id] = raw
                relations[attribute_preparation_key(stage_id)] = scoped.table
                for index, item in enumerate(schemas):
                    if item.source_id == root and item.occurrence == leaf.occurrence:
                        schemas[index] = replace(
                            item,
                            fields=fields,
                            preparation_checks=classified.checks,
                            parameters=tuple(
                                dict.fromkeys(
                                    (
                                        *item.parameters,
                                        *(
                                            parameter
                                            for schema in prepared.schemas
                                            for parameter in schema.parameters
                                            if observation.coordinates.parent_scope is not None
                                            and parameter.definition
                                            == observation.coordinates.parent_scope
                                        ),
                                    )
                                )
                            ),
                        )
        raw = (
            raw_fibers[0].union(*raw_fibers[1:], distinct=False)
            if len(raw_fibers) > 1
            else raw_fibers[0]
        )
        checks = (
            check_fibers[0].union(*check_fibers[1:], distinct=False)
            if len(check_fibers) > 1
            else check_fibers[0]
        )
        parent = (
            parent_fibers[0].union(*parent_fibers[1:], distinct=False)
            if len(parent_fibers) > 1
            else parent_fibers[0]
        )
        parent_preparation = (
            parent_check_fibers[0].union(*parent_check_fibers[1:], distinct=False)
            if len(parent_check_fibers) > 1
            else parent_check_fibers[0]
        )
        for leaf in all_leaves:
            stage_id = "read:" + ".".join(map(str, leaf.occurrence))
            relations[stage_id] = raw
            relations[attribute_parent_key(stage_id)] = parent
            relations[attribute_preparation_key(stage_id)] = checks
            relations[attribute_preparation_key(attribute_parent_key(stage_id))] = (
                parent_preparation
            )
    return (
        replace(prepared, schemas=tuple(schemas), relations=relations),
        scoped_columns,
        parent_checks,
    )
