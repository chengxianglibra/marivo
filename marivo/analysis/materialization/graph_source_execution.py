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

if TYPE_CHECKING:
    from marivo.analysis.compiler.frame_plan import CompiledFramePlan
    from marivo.analysis.compiler.optimization import InvocationMemo

from marivo._definition.relations import LiteralPopulation, PopulationDependency, PopulationProgram
from marivo.analysis.compiler.graph_lowering import (
    LoweredQuery,
    LoweredRead,
)
from marivo.analysis.compiler.graph_plan import (
    FrameCompute,
    FrameInput,
    LocalCompute,
    PlannedCheck,
    QueryStage,
    SourceField,
    SourceRead,
    SourceSchema,
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
from marivo.analysis.materialization.graph_exchange import (
    StageOutput,
    normalize_complete_frame,
)
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
        if read.component_layout is not None:
            coordinate_columns.extend(name for _, name in read.component_layout.identity)
        if read.reduction is not None:
            coordinate_columns.extend(
                dict(read.field_columns)[path] for _, path in read.reduction.axes
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
        reduction = read.reduction
        if (
            reduction is None
            or read.component_layout is None
            or not read.binding_id
            or reduction.duration is not None
            or (
                not sampled
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
        if any(not raw[fields[path]].type().is_string() for _, path in reduction.axes):
            return "Remote original numeric coordinates retain their pandas key boundary"
        if any(
            not physical_value(raw[name], role="identity")
            for _, name in read.component_layout.identity
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


def _preparation_error(expected: str, received: str) -> MaterializationError:
    return MaterializationError(
        expected=expected,
        received=received,
        repair="Use a supported physical field type while preserving the requested key, time and unit contract; no business row was read.",
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
    if pa.types.is_timestamp(physical) and physical.tz is not None:
        return TimestampDataType(physical.unit, physical.tz)
    if pa.types.is_duration(physical):
        return DurationDataType(physical.unit)
    raise _preparation_error(
        f"{path}: a column type in the supported stage type families",
        f"{path}: {physical}",
    )


def _admitted(schema: pa.Schema, path: str, column: str) -> SourceField:
    index = schema.get_field_index(column)
    if index < 0:
        raise _preparation_error(
            f"{path}: column {column!r} on the bound source", repr(schema.names)
        )
    item = schema.field(index)
    return SourceField(path, column, admit_source_type(item, path), item.nullable)


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
            if physical.rel != bound.relation.op():
                continue
            field = bound.facts.schema.field(physical.name)
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
    from marivo._definition.relations import FrameKeys

    populations: dict[str, PopulationDependency] = {}
    seen: set[int] = set()

    def visit(node: DefinitionNode) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, (PopulationProgram, LiteralPopulation)):
            populations[computation_fingerprint(node)] = node
            if isinstance(node, PopulationProgram) and isinstance(node.root, FrameKeys):
                return
        if isinstance(node, DeclaredMetricRef):
            visit(dict(graph.signature.declared_metrics)[node.ref])
        for edge in children(node):
            visit(edge.child)

    visit(graph.root)
    if (
        graph.signature.observation is not None
        and graph.signature.observation.coordinates.population is not None
    ):
        visit(graph.signature.observation.coordinates.population)
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
    from marivo._definition.relations import FrameKeys
    from marivo._definition.types import FieldRead
    from marivo.analysis.compiler.populations import frame_key_groups
    from marivo.analysis.compiler.query_program import FrameParameter
    from marivo.analysis.materialization.population_preparation import _field

    for parameter in parameters:
        if not isinstance(parameter, FrameParameter):
            continue
        key_definition = parameter.definition.root
        assert isinstance(key_definition, FrameKeys)
        columns = {column.slot_id: column for column in parameter.columns}
        for axis, group in zip(
            key_definition.bindings, frame_key_groups(key_definition), strict=True
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
                        "Select an axis with the target's exact physical type, Duration unit and timestamp interpretation.",
                        "frame.population",
                    )
    observation = graph.signature.observation
    from marivo.analysis.materialization.query_preparation import SourceInputContract

    source_bindings: dict[SourceInputContract, str] = {}
    for contribution in () if observation is None else observation.contributions:
        root = contribution.root.ref.path
        prepared = prepare_fields(
            contribution, entities, state, probe_engine_timezone(owners[root]._backend)
        )
        for dependency in prepared.dependencies:
            for physical in physical_fields(dependency):
                for bound in bounds.values():
                    if physical.rel == bound.relation.op():
                        raw = bound.facts.schema.field(physical.name)
                        metadata = raw.metadata or {}
                        if TIMESTAMP_UNIT_METADATA_KEY in metadata:
                            _validate_timestamp_precision(raw, f"{root}: {physical.name}")
                        if DURATION_UNIT_METADATA_KEY in metadata:
                            admit_source_type(raw, f"{root}: {physical.name}")
        table = prepared.table
        source_contract = SourceInputContract(
            id(owners[root]),
            owners[root].datasource.semantic_id,
            normalize_target_entity(registry, root).dependency_fingerprint,
            contribution.root,
            contribution.reads,
            contribution.coordinates,
            contribution.time,
            contribution.period,
            (
                *contribution.intrinsic_conditions,
                *(
                    (contribution.common_condition,)
                    if contribution.common_condition is not None
                    else ()
                ),
            ),
            contribution.memberships,
            contribution.population_conditions,
            contribution.population_reads,
            contribution.window_key,
            contribution.sample_axes,
            contribution.calendars,
            contribution.coverage,
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
                    if physical.rel == bound.relation.op():
                        raw = bound.facts.schema.field(physical.name)
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
        schemas.append(
            SourceSchema(
                root,
                keys,
                tuple(fields),
                contribution.occurrence,
                prepared.checks,
                binding_id=binding_id,
                original_identity=tuple(
                    _admitted(schema, name, name)
                    for name in normalize_target_entity(registry, root).version_row_key
                ),
            )
        )
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
    from marivo._definition.relations import FrameKeys
    from marivo._definition.types import AxisBinding
    from marivo.analysis.compiler.populations import frame_key_groups
    from marivo.analysis.materialization.population_preparation import prepare_population
    from marivo.analysis.materialization.query_parameters import prepare_frame_parameters

    entities: dict[str, ir.Table] = {}
    parameters = prepare_frame_parameters(graph, frame_schemas or {}, entities)
    frame_types: dict[AxisBinding, tuple[DataColumn, ...]] = {}
    for parameter in parameters:
        definition = parameter.definition.root
        assert isinstance(definition, FrameKeys)
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
    prepared: PreparedSources, lowered: LoweredRead
) -> tuple[QualifiedSource, ...]:
    stage = lowered.stage
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
        from marivo.analysis.materialization.query_parameters import instantiate

        expression = read.expression
        for parameter in read.stage.parameters:
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
                provider=prepared.sources.get(read.stage.source_id, prepared.source).provider.name,
                source=prepared.sources.get(read.stage.source_id, prepared.source),
            )
        read = replace(read, expression=expression)
        _admit_numeric_method(prepared.sources.get(read.stage.source_id, prepared.source), read)
        read = replace(read, expression=_native_query_expression(prepared, read))
        expression = read.expression
        qualified = _qualify_query_read(prepared, read)
        source = prepared.sources.get(read.stage.source_id, prepared.source)
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


def _admit_numeric_method(source: SourceSession, read: LoweredRead) -> None:
    from marivo._definition.expressions import SourceAggregate

    if source.provider.name == "duckdb":
        return
    reductions = (
        *(
            stage.reduction
            for stage in read.stage.common_reads or (read.stage,)
            if stage.reduction is not None
        ),
        *read.stage.native_reductions,
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
            if source.provider.name == "postgres" and node.reducer in ("median", "quantile"):
                continue
            _route_gap(
                "a qualified native algorithm for this source reduction",
                f"{source.provider.name}: {node.reducer}",
                "Use an admitted native reduction, or export its input for pandas. Exact and approximate algorithms are not interchangeable.",
            )


def _native_query_expression(prepared: PreparedSources, read: LoweredRead) -> ir.Table:
    from marivo.analysis.materialization.temporal_sql import lower_temporal

    source = prepared.sources.get(read.stage.source_id, prepared.source)
    expression = lower_temporal(read.expression, source.provider.name)
    assert isinstance(expression, ir.Table)
    if source.provider.name == "mysql":
        # MySQL has no IS_NAN / IS_INF functions. Native finite comparisons
        # retain the check without requiring routines or source-side casts.
        replacements = {
            node: (
                node.arg.to_expr() != node.arg.to_expr()
                if isinstance(node, ops.IsNan)
                else node.arg.to_expr().abs() > float.fromhex("0x1.fffffffffffffp+1023")
            ).op()
            for node in expression.op().find((ops.IsNan, ops.IsInf))
        }
        rewritten = expression.op().replace(replacements).to_expr()
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


def read_stage(
    prepared: PreparedSources, lowered: LoweredRead, *, value_keys: tuple[str, ...] = ()
) -> StageOutput:
    """Submit one lowered SourceRead and normalize its pandas result."""
    stage = lowered.stage
    expression = _native_query_expression(prepared, lowered)
    lowered = replace(lowered, expression=expression)
    source = prepared.sources.get(stage.source_id, prepared.source)
    qualified = _qualify_query_read(prepared, lowered)
    issued = source.compile(
        qualified,
        expression,
        purpose=f"query.{stage.stage_id}",
        expected_schema=expression.schema().to_pyarrow(),
    )
    from decimal import DecimalException

    try:
        frame = source.execute(issued)
    except (TypeError, ValueError, OverflowError, DecimalException) as error:
        raise MaterializationError(
            expected="native Ibis pandas values compatible with the compiled expression types",
            received=f"{source.provider.name}: {type(error).__name__}: {error}",
            repair="Use a method and value range supported by the backend's native Ibis pandas conversion, or author an explicit business-specific expression.",
            stage="source_conversion",
            help_target="analysis_frame.compute",
        ) from error
    if stage.common_reads:
        return _complete_common_read(stage, frame, lowered.numeric_checks)
    return _complete_source_frame(stage, frame, lowered.numeric_checks, value_keys=value_keys)


def _complete_source_frame(
    stage: SourceRead,
    frame: pd.DataFrame,
    numeric_checks: tuple[tuple[str, NumericCheck], ...],
    *,
    value_keys: tuple[str, ...] = (),
) -> StageOutput:
    """Complete one logical source occurrence and its original fine checks."""
    source_rows = len(frame)
    from marivo.analysis.materialization.query_components import ComponentFrame

    components: tuple[ComponentFrame, ...] = ()
    if stage.component_layout is not None:
        from marivo.analysis.materialization.query_components import complete_components

        frame, component = complete_components(stage.component_layout, frame)
        components = (component,)
    from marivo.semantic._numeric_lowering import numeric_failure

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
        if frame[name].fillna(False).any():
            coordinates = frame.loc[frame[name].fillna(False), list(stage.keys)].head(6)
            received = f"{check.received}; source={stage.source_id}; coordinates={coordinates.to_dict(orient='records')}"
            if any(name == column for column, _, _ in stage.preparation_checks):
                raise MaterializationError(
                    expected=check.expected,
                    received=received,
                    repair=(
                        "Include every observed output coordinate in by=Population, or explicitly filter contributions before choosing that output domain."
                        if name == "__query_population_uncovered"
                        else "Inspect the relationship keys and exact snapshot/validity selection. Correct missing matches or overlapping rows so each selected contribution has exactly one matching field row, then retry."
                    ),
                    stage="population_coverage"
                    if name == "__query_population_uncovered"
                    else "scalar_field_lookup",
                    help_target="analysis_frame.compute",
                )
            numeric_failure(check.expected, received)
    if "__numeric_present" in frame.columns and "__numeric_present" not in retained_checks:
        frame = frame.loc[frame["__numeric_present"].fillna(False)]
    if stage.native_query_finish is not None:
        from marivo.analysis.materialization.query_key_fusion import finish_native_query

        frame = finish_native_query(stage.native_query_finish, frame)
    from fractions import Fraction

    from marivo._definition.expressions import SourceAggregate, SourceWeightedMean

    reduction = stage.reduction
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
        from decimal import Decimal

        from marivo.analysis.methods.numeric_state import finish_division
        from marivo.analysis.methods.physical import DecimalType

        assert isinstance(reduction.node, SourceAggregate)
        node = reduction.node
        dtype = next(
            column.dtype for column in stage.columns if column.slot_id == reduction.value_slot
        )
        assert isinstance(dtype, DecimalDataType)
        q = 0.5 if node.reducer == "median" else node.q
        assert q is not None
        decimal_values: list[int | float | Decimal | None] = []
        for row in frame.to_dict("records"):
            count = int(row["__quantile_count"])
            if count == 0:
                decimal_values.append(None)
                continue
            position = Fraction(str(q)) * (count - 1)
            fraction = position - (position.numerator // position.denominator)
            lower = row[reduction.value_slot]
            upper = row["__quantile_upper"]
            if isinstance(lower, float) or isinstance(upper, float):
                decimal_values.append(
                    float(lower) + float(fraction) * (float(upper) - float(lower))
                )
                continue
            assert isinstance(lower, Decimal) and isinstance(upper, Decimal)
            exact = Fraction(lower) + fraction * (Fraction(upper) - Fraction(lower))
            decimal_values.append(
                finish_division(
                    exact.numerator,
                    exact.denominator,
                    DecimalType(dtype.precision, dtype.scale),
                )
            )
        frame[reduction.value_slot] = pd.Series(decimal_values, dtype=object, index=frame.index)
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
    if stage.keys and stage.native_query_finish is None:
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
    if stage.native_query_finish is not None:
        completed = replace(completed, layout=stage.native_query_finish.layout)
    return completed


def _complete_common_read(
    stage: SourceRead,
    frame: pd.DataFrame,
    numeric_checks: tuple[tuple[str, NumericCheck], ...],
) -> StageOutput:
    """Split one issued physical block into its ordered logical occurrences."""
    from marivo.analysis.compiler.observation import common_slot
    from marivo.analysis.materialization.query_components import ComponentFrame

    tables: list[pd.DataFrame] = []
    components: list[ComponentFrame] = []
    for index, member in enumerate(stage.common_reads):
        assert member.reduction is not None
        names = {
            column.slot_id: column.slot_id
            if column.slot_id in member.keys or column.slot_id == member.reduction.value_slot
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
        if member.component_layout is not None:
            names.update(
                {
                    column.slot_id: common_slot(index, column.slot_id)
                    for column in member.component_layout.raw_columns
                    if column.slot_id not in member.keys
                }
            )
            names[member.component_layout.present] = common_slot(
                index, member.component_layout.present
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
        output = _complete_source_frame(member, local, checks)
        components.extend(output.components)
        tables.append(
            output.table.rename(
                columns={
                    column.slot_id: common_slot(index, column.slot_id)
                    for column in member.columns
                    if column.slot_id not in member.keys
                    and column.slot_id != member.reduction.value_slot
                }
            )
        )
    result = tables[0]
    for other in tables[1:]:
        result = (
            result.merge(other, on=list(stage.keys), how="outer", validate="one_to_one", sort=False)
            if stage.keys
            else result.join(other)
        )
    result = result.loc[:, [column.slot_id for column in stage.columns]]
    retained_checks = {name for name, _ in numeric_checks}
    retained_checks.update(
        common_slot(index, name)
        for index in range(len(stage.common_reads))
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
    observe_read: Callable[[LoweredRead, StageOutput], None] | None = None,
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

    def check(requirement: PlannedCheck, output: StageOutput) -> None:
        reject(
            "an implemented Query check algorithm",
            requirement.check_id,
            "Bind a qualified check route before executing this plan.",
            "analysis.query_check",
        )

    if lowered.plan.checks:
        reject(
            "implemented Query check algorithms",
            repr(tuple(item.check_id for item in lowered.plan.checks)),
            "Use the qualified numeric checks emitted by lowering; no business row was read.",
            "analysis.query_check",
        )

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
        output = read_stage(selected, request, value_keys=value_keys.get(stage.stage_id, ()))
        if observe_read is not None:
            observe_read(request, output)
        return output

    def local(stage: LocalCompute, inputs: tuple[StageOutput, ...]) -> StageOutput:
        from marivo.analysis.materialization.query_runtime_types import effective_local

        stage = effective_local(stage, inputs) if not stage.fused_steps else stage
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
        output = read_stage(selected, request, value_keys=value_keys.get(stage.stage_id, ()))
        if observe_read is not None:
            observe_read(request, output)
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
        checked = CompletedFrameData(data.schema, data.copy_rows(), data.copy_attachments())
        return complete(
            StageOutput.from_frame(
                checked.copy_rows(),
                stage.columns,
                stage.keys,
                key_groups=tuple(tuple(c.slot_id for c in a.columns) for a in stage.schema.axes),
                frame_attachments=checked.copy_attachments(),
            )
        )

    def complete(output: StageOutput) -> StageOutput:
        return replace(
            output,
            table=normalize_complete_frame(output.table, output.columns, output.keys),
            frame_attachments=tuple(
                replace(
                    attachment,
                    rows=normalize_complete_frame(
                        attachment.rows, attachment.schema.columns, attachment.schema.key_fields
                    ),
                )
                for attachment in output.frame_attachments
            ),
        )

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
            output = complete(adapt_source(source_outputs[stage.stage_id], output))
        return transport(stage.stage_id, output)

    result = run_query_plan(
        lowered.plan, read, local, check, dependent, fixed=fixed, frame=frame, finish=finish
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
    observe_read: Callable[[LoweredRead, StageOutput], None] | None = None,
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
