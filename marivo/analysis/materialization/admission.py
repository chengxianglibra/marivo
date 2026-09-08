"""Private first-route execution from pure definition to committed Dataset authority."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import sqlglot
from duckdb import DuckDBPyConnection
from duckdb import __version__ as _duckdb_version
from ibis.backends.duckdb import Backend
from sqlglot import expressions as sge

from marivo.analysis.compiler import captured_parameters, compile_dataset, required_entities
from marivo.analysis.compiler.nodes import (
    CompiledArtifactScan,
    CompiledDataset,
    CompiledSampleFence,
)
from marivo.analysis.compiler.normalize import artifact_inputs, logical_roots
from marivo.analysis.compiler.placement import (
    ArtifactReadStep,
    EngineBinding,
    ExecutionBinding,
    PhysicalStageGraph,
    SourceBinding,
    SourceStep,
    place,
    source_binding,
)
from marivo.analysis.datasets.base import LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.datasets.handles import LogicalRootHandle, _validate_logical_root
from marivo.analysis.evidence.artifact_reads import Finding, FindingPage
from marivo.analysis.evidence.types import ArtifactDigest
from marivo.analysis.materialization import recovery
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    ArtifactRecord,
    EngineReceipt,
    LocalReceipt,
    ResourceRecord,
    RunDatasetInput,
    RunFailure,
    RunRecord,
    SamplingRealization,
    StorageReceipt,
    run_failure_phase,
)
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
    RecoveryPendingError,
)
from marivo.analysis.materialization.execution_key import execution_key
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.materialization.local import LocalPolicy
from marivo.analysis.materialization.local_worker import (
    ArtifactInput,
    LocalPartInput,
    LocalRequest,
    LocalResult,
    StreamInput,
    supervise,
)
from marivo.analysis.materialization.ownership import owns_resource
from marivo.analysis.materialization.publication import make_descriptor, materialization_contract
from marivo.analysis.materialization.reads import (
    read_preview,
    read_primary,
    validate_sampling_state,
)
from marivo.analysis.materialization.reconciliation import reconcile_session
from marivo.analysis.materialization.resources import (
    backend_reservation,
    discharge_resources,
    prove_local_termination,
    reserve_output,
)
from marivo.analysis.materialization.sampling import (
    admit_sampling,
    execute_sample,
    sample_statement,
)
from marivo.analysis.materialization.storage import (
    DatasetWriteResult,
    PartWriteSpec,
    ReadPolicy,
    sampling_state_read,
    write_local_dataset,
)
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.targets import (
    EngineTarget,
    LocalTarget,
    MaterializationTarget,
    ObjectTarget,
    S3Access,
    engine_domain,
    object_access,
    selection_error,
)
from marivo.analysis.materialization.worker_lifetime import reserve_worker
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.observation.contracts import (
    MetricPayload,
    ObservationOwner,
    PopulationPayload,
    RetainedRowsPayload,
)
from marivo.analysis.observation.fold_contracts import RetainedFoldPayload
from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset
from marivo.analysis.observation.population import (
    LogicalPopulationDataset,
    MaterializedPopulationDataset,
)
from marivo.analysis.operators.row import RowCall
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.datasource.backends import _build_backend_from_effective, _effective_kwargs
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.ir import JsonSourceIR, QueryParamScalar, QueryParamScalarList, TableSourceIR
from marivo.datasource.json_source import read_json_source
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import TargetEntityContract
from marivo.semantic.validator import Registry, normalize_target_entity

_MAX_BATCH_BYTES = 8_388_608
# Engine work and retained collection have separate deadlines and cancellation owners.
_SOURCE_EXECUTION_DEADLINE_SECONDS = 60.0
_PREVIEW_MAX_OUTPUT_BYTES = 8192
_READ_POLICY = ReadPolicy()
_LOCAL_POLICY = LocalPolicy()
_DEFAULT_TARGET = LocalTarget()


@dataclass(slots=True)
class ExecutionStatistics:
    """Ephemeral per-action diagnostics, never a publication authority."""

    primary_queries: int = 0
    validation_queries: int = 0
    source_fences: int = 0
    sampling_fences: int = 0
    transferred_rows: int = 0
    transferred_bytes: int = 0
    events: dict[str, int] = field(default_factory=dict)
    statements: list[tuple[str, str]] = field(default_factory=list)
    local_handoffs: tuple[tuple[int, int], ...] = ()
    worker_pid: int | None = None
    worker_peak_rss: int = 0


@dataclass(frozen=True, slots=True, repr=False)
class _JsonFence:
    entity: TargetEntityContract
    source: JsonSourceIR
    relation_name: str
    reader_name: str
    parameters: Mapping[str, QueryParamScalar | QueryParamScalarList]


@dataclass(frozen=True, slots=True, repr=False)
class _ReservedJsonReader:
    backend: Backend
    name: str
    record: Callable[[str, str], None]

    def raw_sql(self, query: str) -> None:
        self.record("source_setting", query)
        self.backend.raw_sql(query)

    def read_json(self, path: str, *, columns: Mapping[str, str], format: str = "auto") -> ir.Table:
        physical_columns = {
            name: self.backend.compiler.type_mapper.to_string(dt.dtype(kind))
            for name, kind in columns.items()
        }
        options = [
            sge.to_identifier("format").eq(sge.convert(format)),
            sge.to_identifier("maximum_object_size").eq(sge.convert(_MAX_BATCH_BYTES)),
            sge.to_identifier("columns").eq(
                sge.Struct.from_arg_list(
                    [
                        sge.PropertyEQ(this=sge.to_identifier(name), expression=sge.convert(kind))
                        for name, kind in physical_columns.items()
                    ]
                )
            ),
        ]
        reader = sge.Anonymous(this="read_json_auto", expressions=[sge.Placeholder(), *options])
        self.record(
            "source_fence_reader",
            f'CREATE OR REPLACE TEMPORARY VIEW "{self.name}" AS {sge.select("*").from_(reader).sql(dialect="duckdb")}',
        )
        return self.backend.read_json(
            path,
            table_name=self.name,
            columns=physical_columns,
            format=format,
            maximum_object_size=_MAX_BATCH_BYTES,
        )


def _error(stage: str, run_ref: str | None = None) -> MaterializationError:
    return MaterializationError(
        expected="a supported, complete and bounded registered Dataset execution",
        received="the admitted action could not complete its current phase",
        repair="Inspect the safe Run phase, correct its source or resource requirement, and retry.",
        stage=stage,
        run_ref=run_ref,
    )


@contextmanager
def _engine_deadline(backend: Backend) -> Iterator[None]:
    connection: object = backend.con
    if not isinstance(connection, DuckDBPyConnection):
        raise _error("execution_boundary")
    expired = threading.Event()

    def cancel() -> None:
        expired.set()
        connection.interrupt()

    timer = threading.Timer(_SOURCE_EXECUTION_DEADLINE_SECONDS, cancel)
    timer.daemon = True
    timer.start()
    try:
        yield
        if expired.is_set():
            raise _error("stage_execution")
    finally:
        timer.cancel()
        timer.join()


def _declared_table(entity: TargetEntityContract) -> ir.Table:
    source = entity.source
    if not isinstance(source, TableSourceIR) or not source.columns:
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
    physical = ibis.table(
        {binding.source: binding.data_type for _, binding in source.columns},
        name=source.table,
        database=namespace,
        catalog=catalog,
    )
    bindings = dict(source.columns)
    return physical.select(
        *(physical[bindings[name].source].name(name) for name, _ in entity.columns)
    )


class DatasetRuntime:
    """Private assembly owner; construction, execution and reads have distinct boundaries."""

    def __init__(
        self,
        store: SessionStore,
        session_ref: str,
        *,
        event: Callable[[str], None] | None = None,
        local_policy: LocalPolicy = _LOCAL_POLICY,
        target: MaterializationTarget = _DEFAULT_TARGET,
        object_bindings: tuple[S3Access, ...] = (),
    ) -> None:
        if store.session(session_ref) is None:
            raise _error("authority_resolution")
        self.store = store
        self.target = target
        self.object_bindings = object_bindings
        self.local_policy = local_policy
        self.session_ref = session_ref
        self._hook = event
        self.statistics = ExecutionStatistics()
        self.last_run_ref: str | None = None

    @classmethod
    def create(
        cls,
        project_root: Path,
        name: str,
        *,
        event: Callable[[str], None] | None = None,
        target: MaterializationTarget = _DEFAULT_TARGET,
        object_bindings: tuple[S3Access, ...] = (),
    ) -> DatasetRuntime:
        store = SessionStore(project_root)
        record = store.session_by_name(name)
        if record is None:
            candidate_ref = "session_" + uuid4().hex
            with session_writer_guard(
                store.layout.lock_path(candidate_ref), session_ref=candidate_ref
            ):
                record = store.create_session(name, session_ref=candidate_ref)
                if record.session_ref == candidate_ref:
                    return cls(
                        store,
                        record.session_ref,
                        event=event,
                        target=target,
                        object_bindings=object_bindings,
                    )
            # A competing creator won this name. Its guard must be acquired only
            # after the unused candidate guard has been released.
        runtime = cls(
            store, record.session_ref, event=event, target=target, object_bindings=object_bindings
        )
        with session_writer_guard(
            store.layout.lock_path(record.session_ref), session_ref=record.session_ref
        ):
            resolved = store.session_by_name(name)
            if resolved is None or resolved.session_ref != record.session_ref:
                raise _error("authority_resolution")
            reconcile_session(
                store,
                record.session_ref,
                event=runtime._event,
                object_bindings=object_bindings,
            )
            store.activate(record.session_ref)
        return runtime

    @classmethod
    def open(
        cls,
        project_root: Path,
        session_ref: str,
        *,
        event: Callable[[str], None] | None = None,
        target: MaterializationTarget = _DEFAULT_TARGET,
        object_bindings: tuple[S3Access, ...] = (),
    ) -> DatasetRuntime:
        if not MaterializationLayout(project_root).store_db.is_file():
            raise _error("authority_resolution")
        return cls(
            SessionStore(project_root),
            session_ref,
            event=event,
            target=target,
            object_bindings=object_bindings,
        )

    def sources(
        self, *, semantic_registry: Registry, sidecar: CompiledExpressionSidecar
    ) -> LazySources:
        return make_lazy_sources(
            semantic_registry=semantic_registry,
            sidecar=sidecar,
            action_port=self,
            session_id=self.session_ref,
            store_id=self.store.store_id,
        )

    def _event(self, point: str) -> None:
        self.statistics.events[point] = self.statistics.events.get(point, 0) + 1
        if self._hook is not None:
            self._hook(point)

    def _record_statement(self, kind: str, sql: str) -> None:
        if kind.startswith("engine_check."):
            self.statistics.validation_queries += 1
            self._event("source_statement")
        # Diagnostics retain SQL structure only; source parameters and literals stay private.
        expression = sqlglot.parse_one(sql, read="duckdb")
        safe = expression.transform(
            lambda node: sge.Placeholder() if isinstance(node, sge.Literal) else node
        )
        self.statistics.statements.append((kind, safe.sql(dialect="duckdb")))

    def artifact(self, reference: str | ArtifactRef) -> MaterializedDataset:
        record = self.store.artifact(str(reference))
        if record is None:
            raise IntegrityError(
                expected="an exact Artifact in this Store generation",
                received="the selected Artifact is absent",
                repair="Use a committed Artifact ref from this Store.",
                stage="presentation",
            )
        return self._recover(record)

    def _recover(self, record: ArtifactRecord) -> MaterializedDataset:
        return recovery.recover_dataset(
            record, session_ref=self.session_ref, store_id=self.store.store_id, action_port=self
        )

    def _selected(self, dataset: MaterializedDataset) -> ArtifactRecord:
        if (
            dataset._owner.store_id != self.store.store_id
            or dataset._owner.session_id != self.session_ref
        ):
            raise _error("authority_resolution")
        record = self.store.artifact(dataset.state.artifact_ref.ref)
        if (
            record is None
            or record.descriptor.definition_fingerprint != dataset.definition_fingerprint
        ):
            raise _error("presentation")
        return record

    def show(self, dataset: MaterializedDataset, *, max_output_bytes: int | None = None) -> None:
        if max_output_bytes is not None and (
            type(max_output_bytes) is not int or max_output_bytes < 1
        ):
            raise _error("presentation")
        record = self._selected(dataset)
        table = read_preview(
            project_root=self.store.project_root,
            receipt=record.descriptor.storage_receipt,
            row_contract=dataset.row_contract,
            row_set_contract=dataset.row_set_contract,
            policy=_READ_POLICY,
            bindings=self.object_bindings,
        )
        limit = min(
            _PREVIEW_MAX_OUTPUT_BYTES,
            _PREVIEW_MAX_OUTPUT_BYTES if max_output_bytes is None else max_output_bytes,
        )
        header = f"<{type(dataset).__name__} ref={record.artifact_ref} rows={record.descriptor.storage_receipt.realized_row_count}>"
        lines = [
            header,
            f"Preview: {table.num_rows} of {record.descriptor.storage_receipt.realized_row_count} rows (maximum {_READ_POLICY.preview_rows})",
            " | ".join(table.column_names),
        ]
        sampling = record.descriptor.sampling_execution
        if sampling is not None:
            facts = "; ".join(
                f"target={item.target_rows}, realized={item.realized_entity_count}, seeded={item.seed is not None}"
                for item in sampling[:3]
            )
            lines.insert(
                1, f"Sampling: approximate Entity sample; realizations={len(sampling)}; {facts}"
            )
        identities = {
            field.name for field in dataset.schema.columns if field.role_id == "entity_identity"
        }
        for index in range(table.num_rows):
            cells = []
            for name in table.column_names:
                value: object = table[name][index].as_py()
                cells.append("<identity>" if name in identities else repr(value)[:256])
            lines.append(" | ".join(cells))
        output = "\n".join(lines).encode("utf-8")
        print(output[: max(0, limit - 1)].decode("utf-8", errors="ignore"))

    def to_pandas(self, dataset: MaterializedDataset) -> pd.DataFrame:
        record = self._selected(dataset)
        return read_primary(
            project_root=self.store.project_root,
            receipt=record.descriptor.storage_receipt,
            row_contract=dataset.row_contract,
            row_set_contract=dataset.row_set_contract,
            policy=_READ_POLICY,
            bindings=self.object_bindings,
        )

    def evidence_digest(self, dataset: MaterializedDataset) -> ArtifactDigest:
        return recovery.evidence_digest(self._selected(dataset))

    def findings(
        self, dataset: MaterializedDataset, *, limit: int, cursor: str | None
    ) -> FindingPage:
        return recovery.empty_findings(self._selected(dataset), limit=limit, cursor=cursor)

    def finding(self, dataset: MaterializedDataset, finding_id: str) -> Finding:
        return recovery.missing_finding(self._selected(dataset), finding_id)

    def execute_metric(self, dataset: LogicalMetricDataset) -> MaterializedMetricDataset:
        result = self._execute(dataset)
        if not isinstance(result, MaterializedMetricDataset):
            raise _error("presentation")
        return result

    def execute_population(
        self, dataset: LogicalPopulationDataset
    ) -> MaterializedPopulationDataset:
        result = self._execute(dataset)
        if not isinstance(result, MaterializedPopulationDataset):
            raise _error("presentation")
        return result

    def _execute(self, dataset: LogicalDataset) -> MaterializedDataset:
        if (
            dataset._owner.session_id != self.session_ref
            or dataset._owner.store_id != self.store.store_id
        ):
            raise _error("graph_validation")
        root_handle = dataset._root
        if not isinstance(root_handle, LogicalRootHandle):
            raise _error("graph_validation")
        _validate_logical_root(root_handle)
        contract = materialization_contract(dataset)
        roots = tuple(logical_roots(dataset))
        for root in roots:
            if root.contract_versions != producer_contract_versions(root.operator_id):
                raise _error("implementation_registration")
            if isinstance(root.payload, PopulationPayload) and root.payload.sampling is not None:
                admit_sampling(root.payload.sampling)
        key = execution_key(dataset.definition_fingerprint)
        with session_writer_guard(
            self.store.layout.lock_path(self.session_ref), session_ref=self.session_ref
        ):
            self.statistics = ExecutionStatistics()
            self.last_run_ref = None
            reconcile_session(
                self.store,
                self.session_ref,
                event=self._event,
                object_bindings=self.object_bindings,
            )
            hit = self.store.lookup(self.session_ref, key)
            if hit is not None:
                self.last_run_ref = hit.producing_run_ref
                return self._recover(hit)
            retained_inputs = artifact_inputs(dataset)
            records = {
                value.state.artifact_ref.ref: self._selected(value) for value in retained_inputs
            }
            candidate_binding = (
                source_binding(dataset) if isinstance(dataset._owner, ObservationOwner) else None
            )

            def admitted_binding(value: MaterializedDataset) -> ExecutionBinding | None:
                receipt = records[value.state.artifact_ref.ref].descriptor.storage_receipt
                if (
                    candidate_binding is not None
                    and isinstance(receipt, EngineReceipt)
                    and value.kind in ("population", "metric")
                    and receipt.datasource_ref == candidate_binding.datasource_id
                    and receipt.execution_domain_id == engine_domain(candidate_binding)
                ):
                    return candidate_binding
                if candidate_binding is None and isinstance(receipt, EngineReceipt):
                    from marivo.analysis.operators.registry import admit_retained_rows

                    admit_retained_rows(value)
                    return EngineBinding(
                        self,
                        receipt.datasource_ref,
                        receipt.execution_domain_id,
                        adapter_versions=(_duckdb_version, ibis.__version__),
                    )
                return None

            physical = place(dataset, artifact_binding=admitted_binding)
            source_steps = tuple(step for step in physical.steps if isinstance(step, SourceStep))
            artifact_steps = tuple(
                step for step in physical.steps if isinstance(step, ArtifactReadStep)
            )
            if (
                len(source_steps) + len(artifact_steps) != 1
                or physical.steps[-1].output != physical.primary_output
                or physical.steps[-1].dataset is not dataset
            ):
                raise _error("implementation_registration")
            source_step = source_steps[0] if source_steps else None
            source_dataset = source_step.dataset if source_step is not None else None
            entities = (
                required_entities(source_dataset)
                if source_dataset is not None
                and source_step is not None
                and isinstance(source_step.binding, SourceBinding)
                else ()
            )
            captures = captured_parameters(source_dataset) if source_dataset is not None else ()
            inherited = None
            if records and source_step is not None:
                if len(records) != 1:
                    raise _error("implementation_registration")
                inherited = next(iter(records.values())).descriptor
            if artifact_steps:
                inherited = self._selected(artifact_steps[0].dataset).descriptor
            contract = materialization_contract(dataset, inherited=inherited)
            run = self.store.admit(
                self.session_ref,
                key,
                RunDatasetInput(
                    dataset.definition_fingerprint,
                    str(dataset.row_contract.shape_id),
                    dataset._root.row_contract_fingerprint,
                    dataset._root.row_set_contract_fingerprint,
                    tuple(dict.fromkeys(root.operator_id for root in roots))[:64],
                    tuple(f"{entity.ref.kind.value}:{entity.ref.path}" for entity in entities)[:64],
                ),
                input_artifact_refs=tuple(records),
            )
            self.last_run_ref = run.run_ref
            backend: Backend | None = None
            execution: ResourceRecord | None = None
            opening = False
            phase = "storage_selection"
            pending_error: MaterializationError | None = None
            try:
                target = self.target
                object_bindings = self.object_bindings
                self._validate_target(source_step, physical, target, object_bindings)
                phase = "authority_resolution"
                validations: list[tuple[str, int]] = []
                sampling: list[SamplingRealization] = list(
                    inherited.sampling_execution or () if inherited is not None else ()
                )
                if inherited is not None:
                    validate_sampling_state(
                        self.store.project_root, sampling_state_read(inherited), object_bindings
                    )
                if source_step is not None and source_dataset is not None:
                    domain = source_step.binding.datasource_id
                    candidate: object
                    if isinstance(source_step.binding, EngineBinding):
                        from marivo.analysis.materialization.engine import checked_engine_path

                        if len(records) != 1:
                            raise _error("execution_boundary", run.run_ref)
                        selected_receipt = next(iter(records.values())).descriptor.storage_receipt
                        if not isinstance(selected_receipt, EngineReceipt):
                            raise _error("execution_boundary", run.run_ref)
                        path = checked_engine_path(self.store.project_root, selected_receipt)
                        execution = backend_reservation(run.run_ref, domain)
                        self.store.reserve(execution)
                        self._event("resource_create")
                        opening = True
                        candidate = ibis.duckdb.connect(str(path), read_only=True)
                    else:
                        datasource = source_step.binding.owner.semantic_registry.datasources[domain]
                        self._event("profile_resolution")
                        require_profile_for_backend_type(datasource.backend_type)
                        self._event("credential_resolution")
                        effective = _effective_kwargs(datasource)
                        execution = backend_reservation(run.run_ref, domain)
                        self.store.reserve(execution)
                        self._event("resource_create")
                        opening = True
                        candidate = _build_backend_from_effective(
                            datasource, effective, read_only=True
                        ).backend
                    if not isinstance(candidate, Backend):
                        raise _error("execution_boundary", run.run_ref)
                    backend = candidate
                    backend.raw_sql("SET threads=1")
                    backend.raw_sql("SET memory_limit='256MiB'")
                    backend.raw_sql("SET max_temp_directory_size='0B'")
                    backend.raw_sql("BEGIN TRANSACTION")
                    tables: dict[str, ir.Table] = {}
                    fences: list[_JsonFence] = []
                    captured = {item.entity_ref.path: item for item in captures}
                    for entity in entities:
                        source = entity.source
                        if isinstance(source, TableSourceIR):
                            tables[entity.ref.path] = _declared_table(entity)
                        elif (
                            isinstance(source, JsonSourceIR)
                            and source.method == "GET"
                            and source.records_path is None
                        ):
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
                            fences.append(
                                _JsonFence(entity, source, name, name + "_reader", values)
                            )
                            tables[entity.ref.path] = ibis.table(dict(entity.columns), name=name)
                        else:
                            raise _error("source_binding", run.run_ref)
                    phase = "ibis_expression_construction"
                    engine_inputs: list[tuple[ir.Table, EngineReceipt, DatasetRowContract]] = []
                    if isinstance(source_step.binding, EngineBinding):
                        from marivo.analysis.compiler.lowering import compile_retained_rows

                        tables["retained"] = backend.table("rows")
                        descriptor = next(iter(records.values())).descriptor
                        if not isinstance(descriptor.storage_receipt, EngineReceipt):
                            raise _error("execution_boundary", run.run_ref)
                        engine_inputs.append(
                            (
                                tables["retained"],
                                descriptor.storage_receipt,
                                descriptor.row_contract,
                            )
                        )
                        retained_tables = self._engine_parts(backend, descriptor, source_dataset)
                        recipe = compile_retained_rows(
                            source_dataset, tables["retained"], parts=retained_tables
                        )
                    else:
                        scans: dict[str, CompiledArtifactScan] = {}
                        for reference, selected_record in records.items():
                            descriptor = selected_record.descriptor
                            receipt = descriptor.storage_receipt
                            if not isinstance(receipt, EngineReceipt):
                                raise _error("execution_boundary", run.run_ref)
                            from marivo.analysis.materialization.engine import attach_engine_scan

                            table = attach_engine_scan(backend, self.store.project_root, receipt)
                            engine_inputs.append((table, receipt, descriptor.row_contract))
                            entity = normalize_target_entity(
                                source_step.binding.owner.semantic_registry,
                                descriptor.population_authority.entity_ref,
                            )
                            scans[reference] = CompiledArtifactScan(table, entity)
                        recipe = compile_dataset(source_dataset, tables, scans=scans)
                    phase = "ibis_backend_compile"
                    self._event("backend_compile")
                    backend.compile(recipe.expression)
                    for assertion in recipe.validations:
                        backend.compile(assertion.expression)
                    for preparation in recipe.preparations:
                        if isinstance(preparation, CompiledSampleFence):
                            sqlglot.parse_one(sample_statement(backend, preparation), read="duckdb")
                        else:
                            backend.compile(preparation.expression)
                    phase = "source_binding"
                    with _engine_deadline(backend):
                        from marivo.analysis.materialization.engine import validate_engine_relation

                        for table, receipt, row in engine_inputs:
                            validate_engine_relation(
                                backend, table, receipt, row, self._record_statement
                            )
                        for entity in entities:
                            if isinstance(entity.source, TableSourceIR):
                                self._validate_source_schema(backend, entity)
                        for fence in fences:
                            for name in (fence.reader_name, fence.relation_name):
                                self.store.reserve(
                                    ResourceRecord(
                                        run_ref=run.run_ref,
                                        resource_kind="planner_temporary_relation",
                                        execution_domain_id=domain,
                                        ownership_nonce=execution.ownership_nonce,
                                        cleanup_capability_id="duckdb_process_lifetime@v1",
                                        safe_locator=f"{execution.safe_locator}/{name}",
                                    )
                                )
                            self._event("source_statement")
                            source_table = read_json_source(
                                _ReservedJsonReader(
                                    backend, fence.reader_name, self._record_statement
                                ),
                                fence.source,
                                source_params=fence.parameters,
                            )
                            self._record_statement(
                                "source_fence",
                                f'CREATE TEMPORARY TABLE "{fence.relation_name}" AS {backend.compile(source_table)}',
                            )
                            backend.create_table(fence.relation_name, source_table, temp=True)
                            self.statistics.source_fences += 1
                    phase = "stage_execution"
                    with _engine_deadline(backend):
                        for validation in recipe.preparations or recipe.validations:
                            if isinstance(validation, CompiledSampleFence):
                                self.store.reserve(
                                    ResourceRecord(
                                        run_ref=run.run_ref,
                                        resource_kind="planner_temporary_relation",
                                        execution_domain_id=domain,
                                        ownership_nonce=execution.ownership_nonce,
                                        cleanup_capability_id="duckdb_process_lifetime@v1",
                                        safe_locator=f"{execution.safe_locator}/{validation.relation_name}",
                                    )
                                )
                                self._event("sampling_reserved")
                                sampling.append(
                                    execute_sample(
                                        backend,
                                        validation,
                                        ordinal=len(sampling),
                                        record=self._record_statement,
                                        event=self._event,
                                    )
                                )
                                self.statistics.sampling_fences += 1
                                self.statistics.validation_queries += 1
                                validations.append((f"sampling.{len(sampling) - 1}.identity", 0))
                                continue
                            self._event("source_statement")
                            self.statistics.validation_queries += 1
                            self._record_statement(
                                "validation:" + validation.name,
                                backend.compile(validation.expression),
                            )
                            value: object = backend.to_pyarrow(validation.expression)["violations"][
                                0
                            ].as_py()
                            if type(value) is not int or value != 0:
                                raise MaterializationError(
                                    expected="zero violations of the declared source validation",
                                    received=f"source validation failed: {validation.name}",
                                    repair="Repair the governed source identity, temporal coverage or component reconciliation.",
                                    stage="output_validation",
                                    run_ref=run.run_ref,
                                )
                            validations.append((validation.name, value))
                        if isinstance(target, EngineTarget):
                            phase = "storage_staging"
                            artifact_ref, storage = self._write_output(
                                dataset,
                                (),
                                run.run_ref,
                                sampling=tuple(sampling),
                                source_key_validation=True,
                                engine=(backend, source_step.binding, recipe),
                                target=target,
                                object_bindings=object_bindings,
                            )
                        else:
                            batch_rows = self._batch_rows(backend, tables, recipe.expression)
                            incoming = self._batches(backend, recipe.expression, batch_rows)
                            output_parts = tuple(
                                PartWriteSpec(
                                    part.role,
                                    part.contract_id,
                                    part.contract_version,
                                    part.column_names,
                                )
                                for part in recipe.retained_parts
                            )
                            if physical.local_steps:
                                from marivo.analysis.materialization.retained import (
                                    component_schema,
                                )

                                stream_schema = recipe.expression.schema().to_pyarrow()
                                local_parts = tuple(
                                    LocalPartInput(
                                        part.role,
                                        part.contract_id,
                                        part.contract_version,
                                        pa.schema(
                                            [
                                                stream_schema.field(name)
                                                for name in part.column_names
                                            ]
                                        ),
                                        component_schema(
                                            source_dataset.row_contract,
                                            part.role,
                                            pa.schema(
                                                [
                                                    stream_schema.field(name)
                                                    for name in part.column_names
                                                ]
                                            ),
                                        ),
                                    )
                                    for part in recipe.retained_parts
                                )
                                local_result = self._run_local(
                                    physical,
                                    StreamInput(
                                        source_dataset.row_contract,
                                        source_dataset.row_set_contract,
                                        wide_parts=bool(local_parts),
                                    ),
                                    incoming,
                                    run.run_ref,
                                    cancel_source=backend.con.interrupt,
                                    parts=local_parts,
                                )
                                incoming = iter(local_result.table.to_batches(max_chunksize=1024))
                                output_parts = self._local_output_parts(local_result)
                                validations = [
                                    (
                                        "source_prefix.final_row_key_unique"
                                        if name == "dataset.final_row_key_unique"
                                        else name,
                                        value,
                                    )
                                    for name, value in validations
                                ]
                                validations.append(("dataset.final_row_key_unique", 0))
                            phase = "storage_staging"
                            artifact_ref, storage = self._write_output(
                                dataset,
                                incoming,
                                run.run_ref,
                                parts=output_parts,
                                sampling=tuple(sampling),
                                target=target,
                                object_bindings=object_bindings,
                                source_key_validation=("dataset.final_row_key_unique", 0)
                                in validations,
                            )
                else:
                    if inherited is None:
                        raise _error("authority_resolution", run.run_ref)
                    phase = "stage_execution"
                    local_parts, part_batches = self._local_input_parts(
                        inherited, dataset, object_bindings
                    )
                    local_result = self._run_local(
                        physical,
                        ArtifactInput(
                            self.store.project_root,
                            inherited.storage_receipt,
                            inherited.row_contract,
                            inherited.row_set_contract,
                        )
                        if isinstance(inherited.storage_receipt, LocalReceipt)
                        else StreamInput(
                            inherited.row_contract,
                            inherited.row_set_contract,
                        ),
                        self._artifact_batches(inherited, object_bindings)
                        if not isinstance(inherited.storage_receipt, LocalReceipt)
                        else (),
                        run.run_ref,
                        cancel_source=lambda: None,
                        parts=local_parts,
                        part_batches=part_batches,
                    )
                    validations = [("dataset.final_row_key_unique", 0)]
                    phase = "storage_staging"
                    artifact_ref, storage = self._write_output(
                        dataset,
                        local_result.table.to_batches(max_chunksize=1024),
                        run.run_ref,
                        parts=self._local_output_parts(local_result),
                        sampling=tuple(sampling),
                        source_key_validation=True,
                        target=target,
                        object_bindings=object_bindings,
                    )
                for input_record in records.values():
                    if isinstance(input_record.descriptor.storage_receipt, EngineReceipt):
                        from marivo.analysis.materialization.engine import checked_engine_path

                        checked_engine_path(
                            self.store.project_root, input_record.descriptor.storage_receipt
                        )
                        if source_step is not None and isinstance(
                            source_step.binding, EngineBinding
                        ):
                            from marivo.analysis.materialization.retained import selected_parts

                            for part in selected_parts(input_record.descriptor, dataset):
                                if isinstance(part.storage_receipt, EngineReceipt):
                                    checked_engine_path(
                                        self.store.project_root, part.storage_receipt
                                    )
                phase = "quality"
                self._event("quality")
                descriptor = make_descriptor(
                    dataset,
                    contract,
                    storage,
                    tuple(validations),
                    tuple(sampling),
                    inherited=inherited,
                )
                validate_sampling_state(
                    self.store.project_root, sampling_state_read(descriptor), object_bindings
                )
                phase = "evidence"
                self._event("evidence")
                if backend is not None and execution is not None:
                    backend.raw_sql("ROLLBACK")
                    backend.disconnect()
                    backend = None
                    prove_local_termination(execution)
                phase = "publication"
                resources = tuple(
                    item
                    for item in self.store.resources(self.session_ref)
                    if item.run_ref == run.run_ref
                )
                receipts = (
                    descriptor.storage_receipt,
                    *(part.storage_receipt for part in descriptor.retained_parts),
                )
                outputs = tuple(
                    item
                    for item in resources
                    if any(owns_resource(receipt, item) for receipt in receipts)
                )
                garbage = tuple(item for item in resources if item not in outputs)
                resolved = discharge_resources(self.store, garbage, object_bindings)
                record = self.store.publish(
                    run.run_ref,
                    artifact_ref,
                    descriptor,
                    resolved_resources=(*outputs, *resolved),
                    event=self._event,
                )
                phase = "presentation"
                self._event("delivery")
                return self._recover(record)
            except BaseException as exc:
                if backend is not None:
                    try:
                        backend.disconnect()
                        if execution is not None:
                            prove_local_termination(execution)
                    except BaseException:
                        pass
                elif execution is not None and not opening:
                    prove_local_termination(execution)
                # Retain only owner-safe error facts, never a datasource exception chain.
                safe = exc if isinstance(exc, MaterializationError) else _error(phase, run.run_ref)
                try:
                    recovered = self._resolve_outcome(
                        run, safe, run_failure_phase(safe.stage, phase), object_bindings
                    )
                    if recovered is not None:
                        return recovered
                    pending_error = MaterializationError(
                        expected=safe.expected or "a complete registered execution",
                        received=safe.received or "execution failed",
                        repair=safe.hint or "Inspect the safe Run and retry.",
                        stage=safe.stage,
                        run_ref=run.run_ref,
                    )
                except BaseException as recovery_error:
                    if isinstance(recovery_error, IntegrityError):
                        pending_error = IntegrityError(
                            expected=recovery_error.expected or "consistent committed metadata",
                            received=recovery_error.received or "metadata integrity failure",
                            repair="Inspect the selected generation integrity.",
                            stage="reconciliation",
                            run_ref=run.run_ref,
                        )
                    else:
                        pending_error = RecoveryPendingError(
                            expected="authoritative Store readback and execution termination",
                            received="the producing outcome remains unresolved",
                            repair="Restore Store access and termination proof, then retry Session recovery.",
                            stage="reconciliation",
                            run_ref=run.run_ref,
                        )
            if pending_error is None:
                raise _error("presentation", run.run_ref)
            raise pending_error from None

    def _write_output(
        self,
        dataset: LogicalDataset,
        batches: Iterable[pa.RecordBatch],
        run_ref: str,
        *,
        parts: tuple[PartWriteSpec, ...] = (),
        sampling: tuple[SamplingRealization, ...] = (),
        source_key_validation: bool,
        target: MaterializationTarget,
        object_bindings: tuple[S3Access, ...],
        engine: tuple[Backend, ExecutionBinding, CompiledDataset] | None = None,
    ) -> tuple[str, DatasetWriteResult[StorageReceipt]]:
        nonce = uuid4().hex
        artifact_ref = "artifact_" + nonce
        staging, final, _ = reserve_output(
            self.store,
            run_ref=run_ref,
            session_ref=self.session_ref,
            artifact_ref=artifact_ref,
            nonce=nonce,
            storage_kind="engine" if isinstance(target, EngineTarget) else "local",
        )
        self._event("output_reserved")
        if isinstance(target, EngineTarget):
            from marivo.analysis.materialization.engine import write_engine_dataset

            if engine is None:
                raise _error("storage_selection", run_ref)
            backend, binding, recipe = engine
            result = write_engine_dataset(
                store=self.store,
                run_ref=run_ref,
                session_ref=self.session_ref,
                artifact_ref=artifact_ref,
                staging=staging,
                final=final,
                backend=backend,
                binding=binding,
                recipe=recipe,
                row=dataset.row_contract,
                rows=dataset.row_set_contract,
                sampling=sampling,
                policy=target.policy,
                event=self._event,
                record=self._record_statement,
            )
            self.statistics.primary_queries += 1
            return artifact_ref, result
        storage = write_local_dataset(
            project_root=self.store.project_root,
            staging_path=staging,
            final_path=final,
            batches=batches,
            row_contract=dataset.row_contract,
            row_set_contract=dataset.row_set_contract,
            parts=parts,
            sampling=sampling,
            source_key_validation=source_key_validation,
            policy=target.policy,
            event=self._event,
        )
        if isinstance(target, ObjectTarget):
            from marivo.analysis.materialization.object_storage import write_object_dataset

            return artifact_ref, write_object_dataset(
                store=self.store,
                run_ref=run_ref,
                session_ref=self.session_ref,
                artifact_ref=artifact_ref,
                source=storage,
                access=object_access(object_bindings, target.object_store_ref),
                max_stored_bytes=target.policy.max_stored_bytes,
                event=self._event,
            )
        return artifact_ref, storage

    def _validate_target(
        self,
        source: SourceStep | None,
        physical: PhysicalStageGraph,
        target: MaterializationTarget,
        object_bindings: tuple[S3Access, ...],
    ) -> None:
        if not isinstance(target, (LocalTarget, EngineTarget, ObjectTarget)):
            selection_error("one supported configured target", "unknown target")
        if any(
            type(value) is not int or value <= 0
            for value in (
                target.policy.max_stored_bytes,
                target.policy.max_batch_bytes,
                target.policy.row_group_rows,
            )
        ):
            selection_error("positive fixed storage budgets", "invalid storage policy")
        if isinstance(target, EngineTarget) and (
            source is None
            or physical.local_steps
            or source.binding.datasource_id != target.datasource_ref
        ):
            selection_error(
                "an engine target in the exact existing producer domain",
                "incompatible engine output domain",
            )
        if isinstance(target, ObjectTarget):
            from marivo.analysis.materialization.object_storage import validate_target

            validate_target(object_access(object_bindings, target.object_store_ref))

    def _artifact_batches(
        self, descriptor: ArtifactDescriptor, object_bindings: tuple[S3Access, ...]
    ) -> Iterator[pa.RecordBatch]:
        from marivo.analysis.materialization.reads import payload_batches
        from marivo.analysis.materialization.storage import _limited

        if descriptor.storage_receipt.realized_row_count > _READ_POLICY.max_rows:
            _limited("committed row count exceeds the complete local input limit")
        return payload_batches(
            self.store.project_root,
            descriptor.storage_receipt,
            policy=_READ_POLICY,
            bindings=object_bindings,
            row=descriptor.row_contract,
            rows=descriptor.row_set_contract,
        )

    def _engine_parts(
        self, backend: Backend, descriptor: ArtifactDescriptor, dataset: LogicalDataset
    ) -> dict[str, ir.Table]:
        """Attach only consumed immutable states and verify native schema/support."""
        import hashlib

        from marivo.analysis.materialization.engine import attach_engine_scan
        from marivo.analysis.materialization.retained import (
            component_schema,
            metric_parts,
            selected_parts,
        )
        from marivo.analysis.materialization.storage import _integrity
        from marivo.analysis.observation.fold_contracts import fold_part_role, fold_state_columns

        authorities = {fold_part_role(item): item for item in metric_parts(descriptor.row_contract)}
        tables: dict[str, ir.Table] = {}
        with _engine_deadline(backend):
            for part in selected_parts(descriptor, dataset):
                receipt = part.storage_receipt
                if not isinstance(receipt, EngineReceipt):
                    raise _error("execution_boundary", self.last_run_ref)
                table = attach_engine_scan(backend, self.store.project_root, receipt)
                self._record_statement("engine_check.part_schema", backend.compile(table.limit(0)))
                schema = backend.to_pyarrow(table.limit(0)).schema
                if (
                    hashlib.sha256(schema.serialize().to_pybytes()).hexdigest()
                    != receipt.schema_fingerprint
                ):
                    _integrity("the exact immutable part schema", "engine part schema differs")
                component_schema(descriptor.row_contract, part.role, schema)
                self._record_statement("engine_check.part_count", backend.compile(table.count()))
                count: object = backend.execute(table.count())
                if count != receipt.realized_row_count:
                    _integrity("the exact committed part row count", "engine part count differs")
                required = [
                    table[name].isnull()
                    for name, _, nullable in fold_state_columns(authorities[part.role])
                    if not nullable
                ]
                if required:
                    invalid = required[0]
                    for predicate in required[1:]:
                        invalid = invalid | predicate
                    check = table.filter(invalid).count()
                    self._record_statement("engine_check.part_support", backend.compile(check))
                    failures: object = backend.execute(check)
                    if failures != 0:
                        _integrity(
                            "non-null component support and coverage", "null required engine state"
                        )
                tables[part.role] = table
        return tables

    def _local_input_parts(
        self,
        descriptor: ArtifactDescriptor,
        dataset: LogicalDataset,
        object_bindings: tuple[S3Access, ...],
    ) -> tuple[tuple[LocalPartInput, ...], tuple[Iterable[pa.RecordBatch], ...]]:
        from marivo.analysis.materialization.reads import part_schema, read_part_batches
        from marivo.analysis.materialization.retained import (
            checked_component_batches,
            component_schema,
            selected_parts,
        )
        from marivo.analysis.materialization.storage import _limited

        selected = (
            *selected_parts(descriptor, dataset),
            *(
                part
                for part in descriptor.retained_parts
                if part.role == "population_sampling_state"
            ),
        )
        if any(
            part.storage_receipt.realized_row_count > self.local_policy.max_input_rows
            for part in selected
        ):
            _limited("committed part row count exceeds the complete local input limit")
        inputs: list[LocalPartInput] = []
        streams: list[Iterable[pa.RecordBatch]] = []
        for part in selected:
            schema = (
                pa.schema([pa.field("sampling_execution_digest", pa.string(), nullable=False)])
                if part.role == "population_sampling_state"
                else part_schema(self.store.project_root, part, bindings=object_bindings)
            )
            keys = (
                ()
                if part.role == "population_sampling_state"
                else component_schema(descriptor.row_contract, part.role, schema)
            )
            receipt = part.storage_receipt
            inputs.append(
                LocalPartInput(
                    part.role,
                    part.contract_id,
                    part.contract_version,
                    schema,
                    keys,
                    receipt if isinstance(receipt, LocalReceipt) else None,
                )
            )
            if not isinstance(receipt, LocalReceipt):
                incoming = read_part_batches(
                    self.store.project_root,
                    part,
                    expected_schema=schema,
                    policy=_READ_POLICY,
                    bindings=object_bindings,
                )
                streams.append(
                    incoming
                    if part.role == "population_sampling_state"
                    else checked_component_batches(incoming, descriptor.row_contract, part.role)
                )
        return tuple(inputs), tuple(streams)

    @staticmethod
    def _local_output_parts(result: LocalResult) -> tuple[PartWriteSpec, ...]:
        return tuple(
            PartWriteSpec(
                part.role, part.contract_id, part.contract_version, tuple(part.table.column_names)
            )
            for part in result.parts
            if part.contract_id == "metric.sufficient_components"
        )

    def _run_local(
        self,
        physical: PhysicalStageGraph,
        selected: StreamInput | ArtifactInput,
        batches: Iterable[pa.RecordBatch],
        run_ref: str,
        *,
        cancel_source: Callable[[], None],
        parts: tuple[LocalPartInput, ...] = (),
        part_batches: tuple[Iterable[pa.RecordBatch], ...] = (),
    ) -> LocalResult:
        calls: list[RowCall] = []
        for step in physical.local_steps:
            root = step.dataset._root
            if not isinstance(root, LogicalRootHandle) or not isinstance(
                root.payload, (MetricPayload, RetainedRowsPayload, RetainedFoldPayload)
            ):
                raise _error("implementation_registration", run_ref)
            source = step.dataset._inputs[0]
            payload = root.payload
            calls.append(
                RowCall(
                    step.implementation.local_method or "",
                    source.row_contract,
                    source.row_set_contract,
                    step.dataset.row_contract,
                    step.dataset.row_set_contract,
                    payload.predicate if not isinstance(payload, RetainedFoldPayload) else None,
                    payload.rank if not isinstance(payload, RetainedFoldPayload) else None,
                    payload.limit_count if not isinstance(payload, RetainedFoldPayload) else None,
                    payload.spec if isinstance(payload, RetainedFoldPayload) else None,
                )
            )
        reservation = reserve_worker(self.store, run_ref, self.session_ref)
        try:
            self._event("local_worker_reserved")
        except BaseException:
            prove_local_termination(reservation.execution)
            raise
        request = LocalRequest(
            selected,
            tuple(calls),
            self.local_policy,
            time.monotonic() + self.local_policy.deadline_seconds,
            parts,
        )
        result = supervise(
            request,
            batches,
            lifetime=reservation,
            cancel_source=cancel_source,
            terminal=lambda: prove_local_termination(reservation.execution),
            part_batches=part_batches,
        )
        self.statistics.local_handoffs = result.handoffs
        self.statistics.worker_pid = result.worker_pid
        self.statistics.worker_peak_rss = result.peak_rss
        self._event("local_worker_terminal")
        return result

    def _resolve_outcome(
        self,
        run: RunRecord,
        error: MaterializationError,
        phase: str,
        object_bindings: tuple[S3Access, ...],
    ) -> MaterializedDataset | None:
        self._event("readback")
        current = self.store.run(run.run_ref)
        if current is None:
            raise _error("publication", run.run_ref)
        if current.lifecycle == "succeeded":
            if current.output_artifact_ref is None:
                raise _error("publication", run.run_ref)
            return self.artifact(current.output_artifact_ref)
        if current.lifecycle == "failed":
            return None
        resources = tuple(
            item for item in self.store.resources(self.session_ref) if item.run_ref == run.run_ref
        )
        resolved = discharge_resources(self.store, resources, object_bindings)
        self.store.fail(
            run.run_ref,
            RunFailure(
                phase=phase,
                kind="execution_failed",
                safe_message="The Dataset action failed before publication.",
                safe_location=f"dataset.{phase}",
                expected=error.expected or "a complete registered execution",
                received=error.received or "the action failed",
                repair=error.hint or "Inspect the safe Run phase and retry.",
            ),
            resolved_resources=resolved,
        )
        return None

    def _validate_source_schema(self, backend: Backend, entity: TargetEntityContract) -> None:
        source = entity.source
        if not isinstance(source, TableSourceIR):
            raise _error("source_binding", self.last_run_ref)
        database = source.database
        namespace = database if isinstance(database, str) else (database[-1] if database else None)
        catalog = database[0] if isinstance(database, tuple) and len(database) == 2 else None
        self._event("source_statement")
        self.statistics.validation_queries += 1
        self._record_statement(
            "source_schema",
            sge.Describe(
                this=sge.Table(
                    this=sge.to_identifier(source.table, quoted=True),
                    db=None if namespace is None else sge.to_identifier(namespace, quoted=True),
                    catalog=None if catalog is None else sge.to_identifier(catalog, quoted=True),
                )
            ).sql(dialect="duckdb"),
        )
        actual = backend.get_schema(source.table, database=namespace, catalog=catalog)
        for _, binding in source.columns:
            declared = dt.dtype(binding.data_type).copy(nullable=True)
            if (
                binding.source not in actual
                or actual[binding.source].copy(nullable=True) != declared
            ):
                raise MaterializationError(
                    expected="physical source columns matching their exact declared logical types",
                    received="the governed source schema differs from its declaration",
                    repair="Correct the declaration or physical schema before executing this Dataset.",
                    stage="output_validation",
                    run_ref=self.last_run_ref,
                )

    def _batch_rows(
        self, backend: Backend, tables: Mapping[str, ir.Table], expression: ir.Table
    ) -> int:
        maximum_string = 0
        for table in tables.values():
            values = [
                table[name].length().fill_null(0) * 4
                for name, kind in table.schema().items()
                if isinstance(kind, dt.String)
            ]
            if values:
                self._event("source_statement")
                self.statistics.validation_queries += 1
                width = ibis.greatest(*values).max().fill_null(0)
                self._record_statement(
                    "transfer_guard", backend.compile(table.aggregate(maximum_bytes=width))
                )
                value: object = backend.to_pyarrow(table.aggregate(maximum_bytes=width))[
                    "maximum_bytes"
                ][0].as_py()
                if type(value) is not int:
                    raise _error("transfer_guard")
                maximum_string = max(maximum_string, value)
        conservative_row = len(expression.columns) * (maximum_string + 64)
        if conservative_row > _MAX_BATCH_BYTES:
            raise _error("transfer_guard")
        return max(1, min(1024, _MAX_BATCH_BYTES // max(1, conservative_row * 2)))

    def _batches(
        self, backend: Backend, expression: ir.Table, batch_rows: int
    ) -> Iterator[pa.RecordBatch]:
        self._event("source_statement")
        self.statistics.primary_queries += 1
        self._record_statement("primary", backend.compile(expression))
        reader = backend.to_pyarrow_batches(expression, chunk_size=batch_rows)
        seen = False
        try:
            for batch in reader:
                self._event("transfer")
                if batch.nbytes > _MAX_BATCH_BYTES:
                    raise _error("transfer_guard")
                seen = True
                self.statistics.transferred_rows += batch.num_rows
                self.statistics.transferred_bytes += batch.nbytes
                yield batch
            if not seen:
                yield pa.RecordBatch.from_arrays(
                    [pa.array([], type=field.type) for field in reader.schema], schema=reader.schema
                )
        finally:
            reader.close()


def producer_contract_versions(operator_id: str) -> tuple[tuple[str, str], ...]:
    from marivo.analysis.observation.contracts import producer_contract

    return producer_contract(operator_id).versions
