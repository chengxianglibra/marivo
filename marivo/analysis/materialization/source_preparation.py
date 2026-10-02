"""Private first-route execution from pure definition to committed Dataset authority."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from uuid import uuid4

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.compiler import captured_parameters, compile_dataset
from marivo.analysis.compiler.nodes import (
    CompiledArtifactScan,
    CompiledDataset,
    CompiledRelationFence,
)
from marivo.analysis.compiler.normalize import (
    artifact_inputs,
    logical_roots,
    required_source_dependencies,
)
from marivo.analysis.compiler.placement import (
    ExecutionBinding,
    ParquetBinding,
    SourceBinding,
    SourceStep,
)
from marivo.analysis.compiler.source_dependencies import EntitySourceDependency, SourceDependencies
from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.completeness import (
    EventCoverageResolution,
)
from marivo.analysis.domains.contracts import (
    EventFunnelPayload,
    EventPayload,
    EventSelectionPayload,
    EventTimeToEventPayload,
)
from marivo.analysis.materialization import contracts as codec
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    ArtifactRecord,
    ResourceRecord,
    StorageReceipt,
)
from marivo.analysis.materialization.errors import (
    SourceSchemaError,
)
from marivo.analysis.materialization.errors import _execution_error as _error
from marivo.analysis.materialization.execution import ExecutionAdapter
from marivo.analysis.materialization.resources import (
    backend_reservation,
)
from marivo.analysis.materialization.validation import compile_preparations, execute_batch
from marivo.analysis.observation.source_bindings import BoundSourceParametersV1
from marivo.analysis.operators.association_contracts import (
    CorrelatePayload,
)
from marivo.datasource.backends import _build_backend_from_effective, _effective_kwargs
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.ir import (
    CsvSourceIR,
    JsonSourceIR,
    QueryParamScalar,
    QueryParamScalarList,
    TableSourceIR,
)
from marivo.datasource.json_source import read_json_source
from marivo.datasource.timezone import DatasourceEngineTimezone
from marivo.semantic.ir import TargetEntityContract
from marivo.semantic.validator import normalize_target_entity

if TYPE_CHECKING:
    from marivo.analysis.materialization.admission import DatasetRuntime


def _engine_domain(binding: ExecutionBinding) -> str:
    """Persist a declaration digest, never credentials or a Python owning object."""
    if isinstance(binding, ParquetBinding):
        return binding.domain_digest
    source = binding.owner.semantic_registry.datasources[binding.datasource_id]
    return codec.digest(
        (binding.datasource_id, source.backend_type, source.fields, source.env_refs)
    )


@dataclass(frozen=True, slots=True, repr=False)
class _ReservedJsonReader:
    backend: ExecutionAdapter
    name: str

    def raw_sql(self, query: str) -> None:
        raise _error("source_admission")

    @property
    def _marivo_duckdb_http_auth(self) -> object:
        connection = getattr(self.backend, "_backend", self.backend)
        return getattr(connection, "_marivo_duckdb_http_auth", None)

    def read_json(
        self,
        path: str,
        *,
        format: str = "auto",
    ) -> ir.Table:
        return self.backend.read_json(
            path,
            table_name=self.name,
            format=format,
        )

    def create_table(self, _name: str, *, obj: pa.Table, temp: bool) -> ir.Table:
        if not temp:
            raise _error("source_binding")
        reader = pa.RecordBatchReader.from_batches(obj.schema, obj.to_batches())
        return self.backend.freeze_reader(self.name, reader)


def _declared_table(
    entity: TargetEntityContract,
    physical_schema: ibis.Schema,
    *,
    dependency: EntitySourceDependency,
) -> ir.Table:
    source = entity.source
    if not isinstance(source, TableSourceIR):
        raise _error("source_binding")
    database = source.database
    catalog: str | None = None
    namespace: str | None = None
    if isinstance(database, str):
        namespace = database
    elif isinstance(database, tuple):
        if len(database) == 2:
            catalog, namespace = database
        elif len(database) == 1:
            namespace = database[0]
        else:
            raise _error("source_binding")
    bindings = dict(source.columns)
    physical = ibis.table(
        {column.physical: physical_schema[column.physical] for column in dependency.columns},
        name=source.table,
        database=namespace,
        catalog=catalog,
    )
    return physical.select(
        *(
            physical[bindings[column.logical] if bindings else column.logical].name(column.logical)
            for column in dependency.columns
        )
    )


def _validate_inferred_source_types(
    table: ir.Table,
    dependency: EntitySourceDependency,
    *,
    run_ref: str,
) -> None:
    for column in dependency.columns:
        dtype = table[column.logical].type()
        supported = (
            dtype.is_boolean()
            or dtype.is_integer()
            or dtype.is_floating()
            or dtype.is_string()
            or dtype.is_date()
            or dtype.is_timestamp()
            or (
                isinstance(dtype, dt.Decimal)
                and dtype.precision is not None
                and dtype.precision <= 38
            )
        )
        if not supported:
            raise SourceSchemaError(
                dependency,
                column.physical,
                "unsupported_physical_type",
                str(dtype),
                run_ref=run_ref,
            )


@contextmanager
def prepared_source(
    self: DatasetRuntime,
    run_ref: str,
    source_step: SourceStep,
    all_records: Mapping[str, ArtifactRecord],
    validations: list[tuple[str, int]],
) -> Iterator[tuple[ExecutionAdapter, CompiledDataset, dict[str, ir.Table]]]:
    if isinstance(source_step.binding, SourceBinding):
        raise _error("source_admission", run_ref)
    source_dataset: Dataset = source_step.dataset
    if source_step.operation == "correlation":
        source_dataset = source_dataset._inputs[0]

    records = {
        value.state.artifact_ref.ref: all_records[value.state.artifact_ref.ref]
        for value in artifact_inputs(source_dataset)
    }
    dependencies = (
        required_source_dependencies(
            source_dataset, registry=source_step.binding.owner.semantic_registry
        )
        if isinstance(source_step.binding, SourceBinding)
        and isinstance(source_dataset, LogicalDataset)
        else SourceDependencies(())
    )
    entities = tuple(entry.entity for entry in dependencies.entries)
    captures = (
        captured_parameters(source_dataset) if isinstance(source_dataset, LogicalDataset) else ()
    )
    backend: ExecutionAdapter | None = None
    execution: ResourceRecord | None = None
    try:
        domain = source_step.binding.datasource_id
        selected = source_step.implementation
        if selected.backend != source_step.binding.adapter:
            raise _error("source_binding", run_ref)
        from marivo.analysis.materialization.execution import resolve_execution

        implementation = resolve_execution(selected.backend)
        if implementation is None:
            raise _error("implementation_registration", run_ref)
        candidate: object
        if isinstance(source_step.binding, ParquetBinding):
            if implementation.open_retained is None:
                raise _error("implementation_registration", run_ref)
            execution = backend_reservation(run_ref, domain)
            self.store.reserve(execution)
            self._event("resource_create")
            candidate = implementation.open_retained()
        else:
            datasource = source_step.binding.owner.semantic_registry.datasources[domain]
            if datasource.backend_type != selected.backend:
                raise _error("source_binding", run_ref)
            self._event("profile_resolution")
            require_profile_for_backend_type(selected.backend)
            self._event("credential_resolution")
            effective = _effective_kwargs(datasource)
            execution = backend_reservation(run_ref, domain)
            self.store.reserve(execution)
            self._event("resource_create")
            candidate = _build_backend_from_effective(datasource, effective, read_only=True).backend

        def reserve_preparation(name: str) -> None:
            if execution is None:
                raise _error("execution_boundary", run_ref)
            self.store.reserve(
                ResourceRecord(
                    run_ref=run_ref,
                    resource_kind="planner_temporary_relation",
                    execution_domain_id=domain,
                    ownership_nonce=execution.ownership_nonce,
                    cleanup_capability_id=execution.cleanup_capability_id,
                    safe_locator=f"{execution.safe_locator}/{name}",
                )
            )

        backend = implementation.bind(candidate, reserve=reserve_preparation, run_ref=run_ref)
        backend.observe(
            self._observe_submission,
            "source" if isinstance(source_step.binding, SourceBinding) else "local",
        )
        read_time = None
        from marivo.analysis.compiler.source_time import needs_reader_timezone

        if isinstance(source_step.binding, SourceBinding) and needs_reader_timezone(
            source_step.dataset
        ):
            self._event("source_timezone")
            read_time = backend.timezone()
        backend.initialize()
        backend.prepare_dataset(source_step.dataset)
        tables, checked_schemas = _build_source_tables(
            self, backend, entities, dependencies, captures, run_ref
        )
        recipe, engine_inputs = _compile_recipe(
            self,
            source_step,
            source_dataset,
            records,
            dependencies,
            backend,
            tables,
            read_time,
            run_ref,
        )
        recipe = _prepare_correlation(recipe, source_step, run_ref)
        if _open_special_relations(
            self,
            source_step,
            backend,
            recipe,
            entities,
            dependencies,
            checked_schemas,
            run_ref,
            validations,
        ):
            yield backend, recipe, tables
            return
        _run_preparations(
            self,
            backend,
            recipe,
            engine_inputs,
            entities,
            dependencies,
            checked_schemas,
            reserve_preparation,
            run_ref,
            validations,
        )
        yield backend, recipe, tables
    finally:
        failure = sys.exc_info()[1]
        failed = failure is not None
        if failed and execution is not None:
            self._event("remote_read_status_unknown")
            if backend is not None:
                try:
                    backend.interrupt()
                except BaseException:
                    self._event("cancel_failed")
        try:
            if backend is not None:
                backend.finish()
        except BaseException:
            self._event("close_failed")
            if not failed:
                self._event("remote_read_status_unknown")
                raise


def _build_source_tables(
    self: DatasetRuntime,
    backend: ExecutionAdapter,
    entities: tuple[TargetEntityContract, ...],
    dependencies: SourceDependencies,
    captures: tuple[BoundSourceParametersV1, ...],
    run_ref: str,
) -> tuple[dict[str, ir.Table], set[str]]:
    tables: dict[str, ir.Table] = {}
    checked_schemas: set[str] = set()
    captured = {item.entity_ref.path: item for item in captures}
    for entity in entities:
        source = entity.source
        if isinstance(source, TableSourceIR):
            physical_schema = validate_source_schema(
                self, backend, entity, dependency=dependencies.for_entity(entity)
            )
            checked_schemas.add(entity.ref.path)
            table = _declared_table(
                entity, physical_schema, dependency=dependencies.for_entity(entity)
            )
        elif isinstance(source, CsvSourceIR):
            name = "mv_source_" + uuid4().hex
            source_table = backend.read_csv(
                source.path,
                table_name=name,
                header=source.header,
                delimiter=source.delimiter,
            )
            bindings = dict(source.columns)
            dependency = dependencies.for_entity(entity)
            table = source_table.select(
                *(
                    source_table[bindings[column.logical] if bindings else column.logical].name(
                        column.logical
                    )
                    for column in dependency.columns
                )
            )
        elif isinstance(source, JsonSourceIR):
            name = "mv_source_" + uuid4().hex
            capture = captured.get(entity.ref.path)
            values: dict[str, QueryParamScalar | QueryParamScalarList] = {}
            if capture is not None:
                values = dict(
                    zip(
                        capture.ordered_parameter_names,
                        capture.private_canonical_typed_values,
                        strict=True,
                    )
                )
            dependency = dependencies.for_entity(entity)
            read_source = source
            if source.columns:
                needed = {column.logical for column in dependency.columns}
                read_source = replace(
                    source,
                    columns=tuple(
                        (output, path) for output, path in source.columns if output in needed
                    ),
                )
            source_table = read_json_source(
                _ReservedJsonReader(backend, name),
                read_source,
                source_params=values,
            )
            table = source_table.select(
                *(source_table[column.logical] for column in dependency.columns)
            )
            self.statistics.source_fences += 1
        else:
            raise _error("source_binding", run_ref)
        dependency = dependencies.for_entity(entity)
        _validate_inferred_source_types(table, dependency, run_ref=run_ref)
        tables[entity.ref.path] = table
    return tables, checked_schemas


def _compile_recipe(
    self: DatasetRuntime,
    source_step: SourceStep,
    source_dataset: Dataset,
    records: Mapping[str, ArtifactRecord],
    dependencies: SourceDependencies,
    backend: ExecutionAdapter,
    tables: dict[str, ir.Table],
    read_time: DatasourceEngineTimezone | None,
    run_ref: str,
) -> tuple[CompiledDataset, list[tuple[ir.Table, StorageReceipt, DatasetRowContract]]]:
    event_coverages: dict[str, EventCoverageResolution] = {
        reference: record.descriptor.event_evidence.coverage
        for reference, record in records.items()
        if record.descriptor.event_evidence is not None
    }
    if isinstance(source_dataset, LogicalDataset):
        from marivo.analysis.datasets.descriptors import _canonical_digest

        for event_root in logical_roots(source_dataset):
            if isinstance(event_root.payload, (EventPayload,)):
                event_coverages[event_root.definition_fingerprint] = backend.resolve_coverage(
                    event_root.payload.definition,
                    require_source_origin=False,
                    provider=self.event_coverage_provider,
                    source_binding_fingerprint=_canonical_digest(
                        tuple(capture.identity_payload() for capture in event_root.payload.captures)
                    ),
                    execution_domain_id=_engine_domain(source_step.binding),
                )
    from marivo.analysis.materialization.retained import required_primary_input

    primary_inputs = {
        value.state.artifact_ref.ref: required_primary_input(source_dataset, value)
        for value in artifact_inputs(source_dataset)
    }
    engine_inputs: list[tuple[ir.Table, StorageReceipt, DatasetRowContract]] = []
    if isinstance(source_step.binding, ParquetBinding):
        from marivo.analysis.compiler.lowering import compile_retained_rows
        from marivo.analysis.compiler.ordering import ordered_relation
        from marivo.analysis.materialization.parquet_scan import attach_parquet_scan

        retained_scans: dict[str, ir.Table] = {}
        retained_parts: dict[str, dict[str, ir.Table]] = {}
        for reference, selected_record in records.items():
            descriptor = selected_record.descriptor
            receipt = descriptor.storage_receipt
            table = attach_parquet_scan(backend, self.store.project_root, receipt)
            table = ordered_relation(table, descriptor.row_contract, descriptor.row_set_contract)
            retained_scans[reference] = table
            tables[reference] = table
            if primary_inputs[reference]:
                engine_inputs.append((table, receipt, descriptor.row_contract))
            retained_parts[reference] = parquet_parts(
                self,
                backend,
                descriptor,
                source_dataset,
                input_dataset=next(
                    value
                    for value in artifact_inputs(source_dataset)
                    if value.state.artifact_ref.ref == reference
                ),
            )
        recipe = compile_retained_rows(
            source_dataset,
            retained_scans,
            input_parts=retained_parts,
            event_coverages=event_coverages,
        )
    else:
        scans: dict[str, CompiledArtifactScan] = {}
        for reference, selected_record in records.items():
            descriptor = selected_record.descriptor
            receipt = descriptor.storage_receipt
            from marivo.analysis.materialization.parquet_scan import attach_parquet_scan

            table = attach_parquet_scan(backend, self.store.project_root, receipt)
            if primary_inputs[reference]:
                engine_inputs.append((table, receipt, descriptor.row_contract))
            entity = normalize_target_entity(
                source_step.binding.owner.semantic_registry,
                descriptor.population_authority.entity_ref,
            )
            retained_parts = (
                parquet_parts(
                    self,
                    backend,
                    descriptor,
                    source_dataset,
                    input_dataset=next(
                        value
                        for value in artifact_inputs(source_dataset)
                        if value.state.artifact_ref.ref == reference
                    ),
                )
                if descriptor.row_contract.shape_id.family_id in ("metric",)
                else {}
            )
            scans[reference] = CompiledArtifactScan(table, entity, tuple(retained_parts.items()))
        if not isinstance(source_dataset, LogicalDataset):
            raise _error("implementation_registration", run_ref)
        recipe = compile_dataset(
            source_dataset,
            tables,
            scans=scans,
            source_owner=source_step.binding.owner,
            dependencies=dependencies,
            read_timezone=None if read_time is None else read_time.engine_timezone_name,
            read_timezone_source="engine" if read_time is None else read_time.read_tz_resolution,
            event_coverages=event_coverages,
            replay_exact_quantile=source_step.binding.adapter != "duckdb",
            scalar_identity_distinct=source_step.binding.adapter in {"sqlite", "mysql"},
            explicit_correlation=source_step.binding.adapter in {"sqlite", "mysql", "clickhouse"},
            emulate_full_join=source_step.binding.adapter == "postgres",
            scalar_masks=source_step.binding.adapter in {"sqlite", "mysql"},
            event_dialect="postgres"
            if source_step.binding.adapter == "postgres"
            else "trino"
            if source_step.binding.adapter == "trino"
            else "clickhouse"
            if source_step.binding.adapter == "clickhouse"
            else "duckdb",
            ranked_event_successors=source_step.binding.adapter == "trino",
        )
    return recipe, engine_inputs


def _prepare_correlation(
    recipe: CompiledDataset, source_step: SourceStep, run_ref: str
) -> CompiledDataset:
    if source_step.operation == "correlation":
        from marivo.analysis.compiler.correlation import prepare_pairs

        root = source_step.dataset._root
        if not isinstance(root, LogicalRootHandle) or not isinstance(
            root.payload, CorrelatePayload
        ):
            raise _error("implementation_registration", run_ref)
        pair_expression, pair_checks = prepare_pairs(recipe.expression, root.payload.spec)
        recipe = replace(
            recipe,
            expression=pair_expression,
            primary_columns=tuple(pair_expression.columns),
            retained_parts=(),
            validations=(*recipe.validations, *pair_checks),
            preparations=(*recipe.preparations, *pair_checks) if recipe.preparations else (),
        )
    return recipe


def _open_special_relations(
    self: DatasetRuntime,
    source_step: SourceStep,
    backend: ExecutionAdapter,
    recipe: CompiledDataset,
    entities: tuple[TargetEntityContract, ...],
    dependencies: SourceDependencies,
    checked_schemas: set[str],
    run_ref: str,
    validations: list[tuple[str, int]],
) -> bool:
    selected = source_step.implementation
    root = source_step.dataset._root
    if (
        selected.backend in ("postgres", "clickhouse", "trino")
        and isinstance(root, LogicalRootHandle)
        and (
            isinstance(root.payload, EventPayload)
            or (
                selected.backend in ("postgres", "trino")
                and isinstance(
                    root.payload,
                    (EventFunnelPayload, EventTimeToEventPayload, EventSelectionPayload),
                )
            )
        )
    ):
        for entity in entities:
            if isinstance(entity.source, TableSourceIR) and entity.ref.path not in checked_schemas:
                validate_source_schema(
                    self, backend, entity, dependency=dependencies.for_entity(entity)
                )
        if selected.backend == "trino":
            from marivo.analysis.materialization.trino_execution import (
                TrinoExecutionAdapter,
            )

            if not isinstance(backend, TrinoExecutionAdapter):
                raise _error("implementation_registration", run_ref)
            validations.extend(backend.open_event_relations(recipe))
        elif selected.backend == "postgres":
            from marivo.analysis.materialization.postgres_execution import (
                PostgresExecutionAdapter,
            )

            if not isinstance(backend, PostgresExecutionAdapter):
                raise _error("implementation_registration", run_ref)
            if isinstance(root.payload, EventPayload):
                validations.extend(
                    backend.open_event_bundle(
                        recipe,
                        step_keys=tuple(step.step.key for step in root.payload.definition.steps),
                    )
                )
            else:
                validations.extend(backend.open_event_relations(recipe))
        else:
            from marivo.analysis.materialization.clickhouse_execution import (
                ClickHouseExecutionAdapter,
            )

            if not isinstance(backend, ClickHouseExecutionAdapter):
                raise _error("implementation_registration", run_ref)
            if isinstance(root.payload, EventPayload):
                validations.extend(
                    backend.open_event_bundle(
                        recipe,
                        step_keys=tuple(step.step.key for step in root.payload.definition.steps),
                    )
                )
            else:
                raise _error("implementation_registration", run_ref)
        return True
    return False


def _run_preparations(
    self: DatasetRuntime,
    backend: ExecutionAdapter,
    recipe: CompiledDataset,
    engine_inputs: list[tuple[ir.Table, StorageReceipt, DatasetRowContract]],
    entities: tuple[TargetEntityContract, ...],
    dependencies: SourceDependencies,
    checked_schemas: set[str],
    reserve_preparation: Callable[[str], None],
    run_ref: str,
    validations: list[tuple[str, int]],
) -> None:
    self._event("backend_compile")
    backend.compile(recipe.expression)
    preparations = compile_preparations(
        backend, recipe.preparations or recipe.validations, run_ref=run_ref
    )
    relation_statements = {
        preparation.relation_name: backend.table_statement(
            preparation.relation_name, preparation.expression
        )
        for preparation in preparations
        if isinstance(preparation, CompiledRelationFence)
    }
    from marivo.analysis.materialization.parquet_scan import validate_parquet_relation

    for table, receipt, row in engine_inputs:
        validate_parquet_relation(backend, table, receipt, row)
    for entity in entities:
        if isinstance(entity.source, TableSourceIR) and entity.ref.path not in checked_schemas:
            validate_source_schema(
                self, backend, entity, dependency=dependencies.for_entity(entity)
            )
    for validation in preparations:
        if isinstance(validation, CompiledRelationFence):
            reserve_preparation(validation.relation_name)
            fence_statement = relation_statements[validation.relation_name]
            backend.submit(fence_statement)
            self.statistics.source_fences += 1
            continue
        validations.extend(execute_batch(backend, validation, run_ref=run_ref))


def validate_source_schema(
    self: DatasetRuntime,
    backend: ExecutionAdapter,
    entity: TargetEntityContract,
    *,
    dependency: EntitySourceDependency,
) -> ibis.Schema:
    source = entity.source
    if not isinstance(source, TableSourceIR):
        raise _error("source_binding", self.last_run_ref)
    database = source.database
    namespace = database if isinstance(database, str) else (database[-1] if database else None)
    catalog = database[0] if isinstance(database, tuple) and len(database) == 2 else None
    actual = backend.get_schema(
        source.table,
        database=namespace,
        catalog=catalog,
        dependency=dependency,
    )
    for column in dependency.columns:
        physical_type = actual.get(column.physical)
        if physical_type is None:
            raise SourceSchemaError(
                dependency,
                column.physical,
                "missing_column",
                None,
                run_ref=self.last_run_ref,
            )
    return actual


def parquet_parts(
    self: DatasetRuntime,
    backend: ExecutionAdapter,
    descriptor: ArtifactDescriptor,
    dataset: Dataset,
    *,
    input_dataset: MaterializedDataset | None = None,
) -> dict[str, ir.Table]:
    """Attach only consumed immutable states and verify native schema/support."""
    from marivo.analysis.materialization.parquet_scan import attach_parquet_scan
    from marivo.analysis.materialization.retained import (
        _part_state_columns,
        component_schema,
        selected_parts,
        source_private_part,
        validate_source_private_relation,
    )
    from marivo.analysis.materialization.storage import _integrity

    tables: dict[str, ir.Table] = {}
    for part in selected_parts(descriptor, dataset, input_dataset=input_dataset):
        receipt = part.storage_receipt
        table = attach_parquet_scan(
            backend,
            self.store.project_root,
            receipt,
            verify_schema=True,
        )
        # Native scans erase Arrow nullability. The exact stored schema is
        # checked before attachment; validate native types and data below.
        schema = backend.read_table(
            backend.prepare(table.limit(0), role="engine_check.part_schema")
        ).schema
        if source_private_part(part):
            primary_receipt = descriptor.storage_receipt
            primary = attach_parquet_scan(
                backend,
                self.store.project_root,
                primary_receipt,
            )
            validate_source_private_relation(
                backend,
                table,
                primary,
                descriptor.row_contract,
                part.role,
            )
        else:
            component_schema(descriptor.row_contract, part.role, schema)
        count: object = backend.read_scalar(
            backend.prepare(table.count(), role="engine_check.part_count")
        )
        if count != receipt.realized_row_count:
            _integrity("the exact committed part row count", "engine part count differs")
        required = (
            []
            if source_private_part(part)
            else [
                table[name].isnull()
                for name, _, nullable in _part_state_columns(descriptor.row_contract, part.role)
                if not nullable
            ]
        )
        if required:
            invalid = required[0]
            for predicate in required[1:]:
                invalid = invalid | predicate
            check = table.filter(invalid).count()
            failures: object = backend.read_scalar(
                backend.prepare(check, role="engine_check.part_support")
            )
            if failures != 0:
                _integrity("non-null component support and coverage", "null required engine state")
        tables[part.role] = table
    return tables
