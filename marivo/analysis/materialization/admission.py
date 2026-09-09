"""Private first-route execution from pure definition to committed Dataset authority."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import sqlglot
from duckdb import DuckDBPyConnection, TransactionException
from duckdb import __version__ as _duckdb_version
from ibis.backends.duckdb import Backend
from sqlglot import expressions as sge

from marivo.analysis.compiler import captured_parameters, compile_dataset, required_entities
from marivo.analysis.compiler.nodes import (
    CompiledArtifactScan,
    CompiledDataset,
    CompiledSampleFence,
    RetainedPartSpec,
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
from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.datasets.handles import LogicalRootHandle, _validate_logical_root
from marivo.analysis.evidence import _dataset_reads
from marivo.analysis.evidence._dataset_types import (
    ArtifactDigest,
    ArtifactRevalidation,
    Finding,
    FindingPage,
)
from marivo.analysis.materialization import recovery
from marivo.analysis.materialization.attribution_publication import AttributionSourceSummary
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
    LocalBoundary,
    LocalGraphRequest,
    LocalInputStreams,
    LocalPartInput,
    LocalResult,
    LocalStage,
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
from marivo.analysis.materialization.validation import compile_preparations, execute_batch
from marivo.analysis.materialization.worker_lifetime import reserve_worker
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.observation.contracts import (
    MetricPayload,
    PopulationPayload,
    RetainedRowsPayload,
)
from marivo.analysis.observation.fold_contracts import RetainedFoldPayload
from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset
from marivo.analysis.observation.population import (
    LogicalPopulationDataset,
    MaterializedPopulationDataset,
)
from marivo.analysis.observation.private_parts import source_private_part_authorities
from marivo.analysis.operators.association import (
    LogicalAssociationDataset,
    MaterializedAssociationDataset,
)
from marivo.analysis.operators.association_contracts import (
    MAX_CANDIDATES,
    AssociationSearchSummary,
    CorrelatePayload,
    CorrelateSpecV1,
    selection_description,
)
from marivo.analysis.operators.attribution import (
    LogicalAttributionDataset,
    MaterializedAttributionDataset,
)
from marivo.analysis.operators.delta import LogicalDeltaDataset, MaterializedDeltaDataset
from marivo.analysis.operators.row import RowCall
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session import _lazy_graph, _lazy_history, _lazy_runtime_reads
from marivo.analysis.session._lazy_read_model import (
    GraphDirection,
    RunLifecycle,
    RunPage,
    SessionGraph,
    SessionInspection,
    SessionSummaryPage,
)
from marivo.analysis.session._lazy_read_model import (
    RunRecord as ReadRunRecord,
)
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
        reader = sge.Anonymous(this="read_json_auto", expressions=[sge.convert(path), *options])
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


def _declared_table(
    entity: TargetEntityContract, physical_schema: ibis.Schema | None = None
) -> ir.Table:
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
        {
            binding.source: binding.data_type
            if physical_schema is None
            else physical_schema[binding.source]
            for _, binding in source.columns
        },
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
            SessionStore.open_existing(project_root),
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

    @staticmethod
    def recent(
        project_root: Path, *, limit: int = 20, cursor: str | None = None
    ) -> SessionSummaryPage:
        """Read existing v3 Session history without creating or activating a Session."""
        return _lazy_history.recent(
            SessionStore.open_existing(project_root), limit=limit, cursor=cursor
        )

    @staticmethod
    def inspect(
        project_root: Path, name: str, *, run_limit: int = 5, run_cursor: str | None = None
    ) -> SessionInspection:
        """Read a named existing v3 Session and one bounded Run page."""
        return _lazy_history.inspect(
            SessionStore.open_existing(project_root),
            name,
            run_limit=run_limit,
            run_cursor=run_cursor,
        )

    def _event(self, point: str) -> None:
        self.statistics.events[point] = self.statistics.events.get(point, 0) + 1
        if self._hook is not None:
            self._hook(point)

    def _record_statement(self, kind: str, sql: str) -> None:
        if kind.startswith("engine_check."):
            self.statistics.validation_queries += 1
            self._event("source_statement")
        self.statistics.statements.append((kind, sql))

    def artifact(self, reference: str | ArtifactRef) -> MaterializedDataset:
        record = self.store.artifact(str(reference))
        if record is None:
            raise _lazy_runtime_reads.missing_artifact(str(reference))
        return self._recover(record)

    def runs(
        self, *, status: RunLifecycle | None = None, limit: int = 20, cursor: str | None = None
    ) -> RunPage:
        """Read a bounded newest-first page without admitting or reconciling work."""
        return _lazy_runtime_reads.runs(
            self.store, self.session_ref, status=status, limit=limit, cursor=cursor
        )

    def get_run(self, run_id: str) -> ReadRunRecord:
        """Read one exact Run owned by this execution Session."""
        return _lazy_runtime_reads.get_run(self.store, self.session_ref, run_id)

    def graph(
        self,
        *,
        artifact_ref: str | ArtifactRef | None = None,
        direction: GraphDirection = "ancestors",
        max_nodes: int = 100,
    ) -> SessionGraph:
        """Read bounded local topology and exact consumed foreign boundaries."""
        ref = ArtifactRef(ref=artifact_ref) if isinstance(artifact_ref, str) else artifact_ref
        return _lazy_graph.graph(
            self.store, self.session_ref, artifact_ref=ref, direction=direction, max_nodes=max_nodes
        )

    def show_session(self) -> None:
        """Render the bounded Session recap from one read-only snapshot."""
        _lazy_runtime_reads.recap(self.store, self.session_ref).show()

    def revalidate(self, reference: str | ArtifactRef) -> ArtifactRevalidation:
        """Explicitly inspect metadata, all committed storage and complete Evidence."""
        from marivo.analysis.materialization.inspection import revalidate

        return revalidate(self.store, reference, bindings=self.object_bindings)

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
        from marivo.analysis.observation.distribution_contracts import distribution_part_authorities
        from marivo.analysis.observation.fold_contracts import decode_fold_authority

        quantiles = tuple(
            item.distribution.quantile
            for _, item in distribution_part_authorities(dataset.row_contract)
            if item.distribution is not None
        )
        if record.descriptor.attribution_fold_authority is not None:
            quantiles = tuple(
                item.distribution.quantile
                for payload in record.descriptor.attribution_fold_authority
                for item in decode_fold_authority(payload).metrics
                if item.distribution is not None
            )
        lines[1:1] = [
            f"Percentile: method={quantile.method}; q={quantile.q}; "
            + (
                "semantic approximation; error_bound=unknown"
                if quantile.method == "duckdb_tdigest@v1"
                else "exact linear interpolation"
            )
            for quantile in dict.fromkeys(quantiles)
        ]
        if record.descriptor.association_evidence is not None:
            from marivo.analysis.operators.association_contracts import AssociationSemantics

            meaning = dataset.row_contract.family_semantics
            evidence = record.descriptor.association_evidence
            if isinstance(meaning, AssociationSemantics):
                lines[1:1] = [
                    f"Association: method={meaning.method}; observation_unit={meaning.input_shape}",
                    f"Approximation: {', '.join(dict.fromkeys(meaning.approximations))}",
                    f"Search: pairs={evidence.searched_pair_count}; lags={evidence.searched_lag_count}; series={evidence.searched_series_count}; candidates={evidence.original_candidate_count}",
                    f"Lag scope: first={meaning.lag_offsets[0]}; last={meaning.lag_offsets[-1]}",
                    f"Complete pairs: {evidence.complete_pair_range}; null loss: {evidence.null_pair_range}",
                    f"Selection: {selection_description()}; rule={evidence.selection_rule_id}",
                    f"Findings: eligible={evidence.eligible_finding_count}; emitted={evidence.emitted_finding_count}; truncated={evidence.finding_truncated}",
                    "Descriptive and exploratory; no significance or causal claim. Positive lag describes coordinate order only.",
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
        return _dataset_reads.evidence_digest(self._selected(dataset))

    def findings(
        self, dataset: MaterializedDataset, *, limit: int, cursor: str | None
    ) -> FindingPage:
        self._validate_reader_owner(dataset)
        with self.store._read() as conn:
            record = self.store._artifact(conn, dataset.state.artifact_ref.ref)
            if record is None:
                raise _error("presentation")
            return _dataset_reads.findings(conn, record, limit=limit, cursor=cursor)

    def finding(self, dataset: MaterializedDataset, finding_id: str) -> Finding:
        self._validate_reader_owner(dataset)
        with self.store._read() as conn:
            record = self.store._artifact(conn, dataset.state.artifact_ref.ref)
            if record is None:
                raise _error("presentation")
            return _dataset_reads.finding(conn, record, finding_id)

    def _validate_reader_owner(self, dataset: MaterializedDataset) -> None:
        if (
            dataset._owner.store_id != self.store.store_id
            or dataset._owner.session_id != self.session_ref
        ):
            raise _error("authority_resolution")

    def execute_association(
        self, dataset: LogicalAssociationDataset
    ) -> MaterializedAssociationDataset:
        result = self._execute(dataset)
        if not isinstance(result, MaterializedAssociationDataset):
            raise _error("publication", None)
        return result

    def execute_delta(self, dataset: LogicalDeltaDataset) -> MaterializedDeltaDataset:
        result = self._execute(dataset)
        if not isinstance(result, MaterializedDeltaDataset):
            raise _error("presentation")
        return result

    def execute_attribution(
        self, dataset: LogicalAttributionDataset
    ) -> MaterializedAttributionDataset:
        result = self._execute(dataset)
        if not isinstance(result, MaterializedAttributionDataset):
            raise _error("presentation")
        return result

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
            source_candidates: list[SourceBinding] = []

            def discover_candidates(value: LogicalDataset | MaterializedDataset) -> None:
                if isinstance(value, LogicalDataset):
                    if isinstance(value._root, LogicalRootHandle) and isinstance(
                        value._root.payload, (PopulationPayload, MetricPayload)
                    ):
                        binding = source_binding(value)
                        if not any(binding.same_domain(item) for item in source_candidates):
                            source_candidates.append(binding)
                    for child in value._inputs:
                        if isinstance(child, (LogicalDataset, MaterializedDataset)):
                            discover_candidates(child)

            discover_candidates(dataset)

            def admitted_binding(value: MaterializedDataset) -> ExecutionBinding | None:
                receipt = records[value.state.artifact_ref.ref].descriptor.storage_receipt
                if not isinstance(receipt, EngineReceipt):
                    return None
                candidates = tuple(
                    candidate
                    for candidate in source_candidates
                    if receipt.datasource_ref == candidate.datasource_id
                    and receipt.execution_domain_id == engine_domain(candidate)
                )
                if len(candidates) == 1:
                    return candidates[0]
                from marivo.analysis.operators.registry import admit_retained_rows

                admit_retained_rows(value)
                return EngineBinding(
                    self,
                    receipt.datasource_ref,
                    receipt.execution_domain_id,
                    adapter_versions=(_duckdb_version, ibis.__version__),
                )

            physical = place(dataset, artifact_binding=admitted_binding)
            source_steps = tuple(step for step in physical.steps if isinstance(step, SourceStep))
            if (
                physical.steps[-1].output != physical.primary_output
                or physical.steps[-1].dataset is not dataset
            ):
                raise _error("implementation_registration")
            source_step = source_steps[0] if source_steps else None
            entities = tuple(
                entity
                for step in source_steps
                if isinstance(step.binding, SourceBinding)
                for entity in required_entities(
                    step.dataset, registry=step.binding.owner.semantic_registry
                )
            )
            inherited = next(iter(records.values())).descriptor if records else None
            contract = materialization_contract(
                dataset,
                inherited=inherited,
                input_descriptors=tuple(
                    records[value.state.artifact_ref.ref].descriptor for value in retained_inputs
                ),
            )
            from marivo.analysis.materialization.retained import (
                reject_source_private_transfer,
                required_part_roles,
                source_private_role,
            )

            if source_private_part_authorities(dataset.row_contract) and not isinstance(
                self.target, EngineTarget
            ):
                selection_error(
                    "a compatible engine target for exact private membership or distribution",
                    "a local or object checkpoint target",
                )
            if physical.local_steps and any(
                source_private_role(role)
                for step in physical.steps
                for role in required_part_roles(dataset, input_dataset=step.dataset)
            ):
                reject_source_private_transfer()
            run = self.store.admit(
                self.session_ref,
                key,
                RunDatasetInput(
                    dataset.definition_fingerprint,
                    dataset.row_contract.shape_id,
                    dataset._root.row_contract_fingerprint,
                    dataset._root.row_set_contract_fingerprint,
                    tuple(dict.fromkeys(root.operator_id for root in roots))[:64],
                    tuple(
                        dict.fromkeys(
                            f"{entity.ref.kind.value}:{entity.ref.path}" for entity in entities
                        )
                    )[:64],
                ),
                input_artifact_refs=tuple(
                    value.state.artifact_ref.ref for value in retained_inputs
                ),
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
                sampling: list[SamplingRealization] = []
                sampling_by_root: dict[int, SamplingRealization] = {}
                attribution_summary: AttributionSourceSummary | None = None
                association_summary: AssociationSearchSummary | None = None
                for input_record in records.values():
                    descriptor = input_record.descriptor
                    if descriptor.association_evidence is not None:
                        evidence = descriptor.association_evidence
                        association_summary = AssociationSearchSummary(
                            evidence.searched_series_count,
                            evidence.original_candidate_count,
                            evidence.complete_pair_range,
                            evidence.null_pair_range,
                        )
                    for realization in descriptor.sampling_execution or ():
                        if realization not in sampling:
                            sampling.append(realization)
                    validate_sampling_state(
                        self.store.project_root, sampling_state_read(descriptor), object_bindings
                    )
                with ExitStack() as source_contexts:
                    prepared: dict[int, tuple[Backend, CompiledDataset, dict[str, ir.Table]]] = {}
                    for source_boundary in source_steps:
                        boundary_validations: list[tuple[str, int]] = []
                        prepared[source_boundary.output] = source_contexts.enter_context(
                            self._prepared_source(
                                run.run_ref,
                                source_boundary,
                                records,
                                sampling,
                                boundary_validations,
                                sampling_by_root,
                            )
                        )
                        proof_backend, proof_recipe, _ = prepared[source_boundary.output]
                        if proof_recipe.association_proof is not None:
                            from marivo.analysis.operators.association_values import (
                                summarize_search,
                            )

                            with _engine_deadline(proof_backend):
                                proof_sql = proof_backend.compile(proof_recipe.association_proof)
                                self._record_statement("association.search_summary", proof_sql)
                                self._event("source_statement")
                                proof_table = proof_backend.to_pyarrow(
                                    proof_recipe.association_proof
                                )
                                if proof_table.num_rows > MAX_CANDIDATES:
                                    raise _error("output_validation", run.run_ref)
                                association_summary = summarize_search(
                                    proof_table.to_pandas(types_mapper=pd.ArrowDtype),
                                    source_boundary.dataset.row_contract,
                                )
                        if (
                            proof_recipe.attribution_proof is not None
                            and source_boundary.dataset.kind == "attribution"
                        ):
                            with _engine_deadline(proof_backend):
                                attribution_summary = self._attribution_source_summary(
                                    proof_backend,
                                    proof_recipe.attribution_proof,
                                    source_boundary.dataset.row_contract,
                                )
                        validations.extend(
                            (
                                f"source.{source_boundary.output}.{name}"
                                if len(source_steps) > 1
                                else name,
                                value,
                            )
                            for name, value in boundary_validations
                        )
                    if not physical.local_steps:
                        if source_step is None:
                            raise _error("execution_boundary", run.run_ref)
                        current_backend, recipe, tables = prepared[source_step.output]
                        with _engine_deadline(current_backend):
                            if isinstance(target, EngineTarget):
                                phase = "storage_staging"
                                artifact_ref, storage = self._write_output(
                                    dataset,
                                    (),
                                    run.run_ref,
                                    sampling=tuple(sampling),
                                    source_key_validation=True,
                                    engine=(current_backend, source_step.binding, recipe),
                                    target=target,
                                    object_bindings=object_bindings,
                                )
                            else:
                                self._require_projected_parts(recipe)
                                incoming = self._batches(
                                    current_backend,
                                    recipe.expression,
                                    self._batch_rows(current_backend, tables, recipe.expression),
                                )
                                output_parts = tuple(
                                    PartWriteSpec(
                                        part.role,
                                        part.contract_id,
                                        part.contract_version,
                                        part.column_names,
                                    )
                                    for part in recipe.retained_parts
                                    if isinstance(part, RetainedPartSpec)
                                )
                                phase = "storage_staging"
                                artifact_ref, storage = self._write_output(
                                    dataset,
                                    incoming,
                                    run.run_ref,
                                    parts=output_parts,
                                    sampling=tuple(sampling),
                                    source_key_validation=True,
                                    target=target,
                                    object_bindings=object_bindings,
                                )
                    else:
                        boundaries: list[LocalBoundary] = []
                        streams: list[LocalInputStreams] = []
                        for step in physical.steps:
                            if isinstance(step, SourceStep):
                                current_backend, recipe, tables = prepared[step.output]
                                if step.correlation_preparation:
                                    from marivo.analysis.materialization.local_worker import (
                                        PairInput,
                                    )

                                    pair_root = step.dataset._root
                                    if not isinstance(
                                        pair_root, LogicalRootHandle
                                    ) or not isinstance(pair_root.payload, CorrelatePayload):
                                        raise _error("implementation_registration", run.run_ref)
                                    count_sql = current_backend.compile(
                                        recipe.expression.aggregate(
                                            __mv_rows=recipe.expression.count()
                                        )
                                    )
                                    self._record_statement("correlation_cardinality", count_sql)
                                    with _engine_deadline(current_backend):
                                        pair_count = current_backend.raw_sql(count_sql).fetchone()[
                                            0
                                        ]
                                    if type(pair_count) is not int or pair_count > min(
                                        self.local_policy.max_input_rows,
                                        self.local_policy.max_method_rows,
                                    ):
                                        raise MaterializationError(
                                            expected="complete correlation pairs within local budgets",
                                            received="correlation input count exceeds budget",
                                            repair="Narrow Metrics, lags or observation scope.",
                                            stage="transfer_guard",
                                            run_ref=run.run_ref,
                                        )
                                    boundaries.append(
                                        LocalBoundary(
                                            step.output,
                                            PairInput(pair_root.payload.spec, pair_count),
                                        )
                                    )
                                    streams.append(
                                        LocalInputStreams(
                                            self._batches(
                                                current_backend,
                                                recipe.expression,
                                                self._batch_rows(
                                                    current_backend, tables, recipe.expression
                                                ),
                                            )
                                        )
                                    )
                                    continue
                                if step.distribution_preparation:
                                    from marivo.analysis.materialization.local_worker import (
                                        CoalitionInput,
                                    )
                                    from marivo.analysis.operators.attribution_contracts import (
                                        AttributePayload,
                                    )

                                    preparation_root = step.dataset._root
                                    if (
                                        not isinstance(preparation_root, LogicalRootHandle)
                                        or not isinstance(
                                            preparation_root.payload, AttributePayload
                                        )
                                        or recipe.numerical_input != "distribution_coalitions"
                                    ):
                                        raise _error("implementation_registration", run.run_ref)
                                    count_sql = current_backend.compile(
                                        recipe.expression.aggregate(
                                            __mv_rows=recipe.expression.count()
                                        )
                                    )
                                    self._record_statement("distribution_cardinality", count_sql)
                                    with _engine_deadline(current_backend):
                                        expected_count: object = current_backend.raw_sql(
                                            count_sql
                                        ).fetchone()[0]
                                    if (
                                        not isinstance(expected_count, int)
                                        or isinstance(expected_count, bool)
                                        or expected_count < 0
                                        or expected_count
                                        > min(
                                            self.local_policy.max_input_rows,
                                            self.local_policy.max_method_rows,
                                        )
                                    ):
                                        raise MaterializationError(
                                            expected="complete coalition input within registered row budgets",
                                            received="distribution coalition count exceeds the action budget",
                                            repair="Narrow comparison scopes or lower top_k before retrying.",
                                            stage="transfer_guard",
                                            run_ref=run.run_ref,
                                        )
                                    boundaries.append(
                                        LocalBoundary(
                                            step.output,
                                            CoalitionInput(
                                                preparation_root.payload.spec, expected_count
                                            ),
                                        )
                                    )
                                    streams.append(
                                        LocalInputStreams(
                                            self._batches(
                                                current_backend,
                                                recipe.expression,
                                                self._batch_rows(
                                                    current_backend, tables, recipe.expression
                                                ),
                                            )
                                        )
                                    )
                                    continue
                                from marivo.analysis.materialization.retained import (
                                    required_part_roles,
                                )

                                needed_roles = required_part_roles(
                                    dataset, input_dataset=step.dataset
                                )
                                selected_specs = tuple(
                                    part
                                    for part in recipe.retained_parts
                                    if part.role in needed_roles
                                )
                                self._require_projected_parts(
                                    replace(recipe, retained_parts=selected_specs)
                                )
                                names = tuple(
                                    dict.fromkeys(
                                        (
                                            *recipe.primary_columns,
                                            *(
                                                name
                                                for part in selected_specs
                                                if isinstance(part, RetainedPartSpec)
                                                for name in part.column_names
                                            ),
                                        )
                                    )
                                )
                                recipe = replace(
                                    recipe,
                                    expression=recipe.expression.select(*names),
                                    retained_parts=selected_specs,
                                )
                                local_parts = self._source_local_parts(step.dataset, recipe)
                                boundaries.append(
                                    LocalBoundary(
                                        step.output,
                                        StreamInput(
                                            step.dataset.row_contract,
                                            step.dataset.row_set_contract,
                                            wide_parts=bool(local_parts),
                                        ),
                                        local_parts,
                                    )
                                )
                                streams.append(
                                    LocalInputStreams(
                                        self._batches(
                                            current_backend,
                                            recipe.expression,
                                            self._batch_rows(
                                                current_backend, tables, recipe.expression
                                            ),
                                        )
                                    )
                                )
                            elif isinstance(step, ArtifactReadStep):
                                selected_descriptor = records[
                                    step.dataset.state.artifact_ref.ref
                                ].descriptor
                                local_parts, part_batches = self._local_input_parts(
                                    selected_descriptor,
                                    dataset,
                                    object_bindings,
                                    input_dataset=step.dataset,
                                )
                                receipt = selected_descriptor.storage_receipt
                                selected = (
                                    ArtifactInput(
                                        self.store.project_root,
                                        receipt,
                                        step.dataset.row_contract,
                                        step.dataset.row_set_contract,
                                    )
                                    if isinstance(receipt, LocalReceipt)
                                    else StreamInput(
                                        step.dataset.row_contract, step.dataset.row_set_contract
                                    )
                                )
                                boundaries.append(LocalBoundary(step.output, selected, local_parts))
                                streams.append(
                                    LocalInputStreams(
                                        ()
                                        if isinstance(receipt, LocalReceipt)
                                        else self._artifact_batches(
                                            selected_descriptor, object_bindings
                                        ),
                                        part_batches,
                                    )
                                )
                        phase = "stage_execution"

                        def cancel_sources() -> None:
                            for current_backend, _, _ in prepared.values():
                                current_backend.con.interrupt()

                        local_result = self._run_local_graph(
                            physical,
                            tuple(boundaries),
                            tuple(streams),
                            run.run_ref,
                            cancel_source=cancel_sources,
                        )
                        attribution_summary = (
                            local_result.attribution_summary or attribution_summary
                        )
                        association_summary = (
                            local_result.association_summary or association_summary
                        )
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
                            local_result.table.to_batches(max_chunksize=1024),
                            run.run_ref,
                            parts=self._local_output_parts(local_result),
                            sampling=tuple(sampling),
                            source_key_validation=True,
                            target=target,
                            object_bindings=object_bindings,
                        )
                for reference, input_record in records.items():
                    if isinstance(input_record.descriptor.storage_receipt, EngineReceipt):
                        from marivo.analysis.materialization.engine import checked_engine_path

                        checked_engine_path(
                            self.store.project_root, input_record.descriptor.storage_receipt
                        )
                        if any(
                            value.state.artifact_ref.ref == reference
                            for boundary in source_steps
                            for value in artifact_inputs(boundary.dataset)
                        ):
                            from marivo.analysis.materialization.retained import selected_parts

                            for part in selected_parts(
                                input_record.descriptor,
                                dataset,
                                input_dataset=next(
                                    value
                                    for value in retained_inputs
                                    if value.state.artifact_ref.ref == reference
                                ),
                            ):
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
                    sampling_by_root=sampling_by_root,
                    input_descriptors=tuple(
                        records[value.state.artifact_ref.ref].descriptor
                        for value in retained_inputs
                    ),
                )
                validate_sampling_state(
                    self.store.project_root, sampling_state_read(descriptor), object_bindings
                )
                findings: tuple[Finding, ...] = ()
                if dataset.kind == "association":
                    from marivo.analysis.materialization.association_publication import (
                        build_association_publication,
                    )
                    from marivo.analysis.materialization.reads import payload_batches

                    descriptor, findings = build_association_publication(
                        descriptor,
                        payload_batches(
                            self.store.project_root,
                            descriptor.storage_receipt,
                            policy=_READ_POLICY,
                            bindings=object_bindings,
                            row=descriptor.row_contract,
                            rows=descriptor.row_set_contract,
                            audit=True,
                        ),
                        artifact_ref=artifact_ref,
                        session_ref=self.session_ref,
                        search_summary=association_summary,
                    )
                elif dataset.kind == "delta":
                    from marivo.analysis.materialization.comparison_publication import (
                        build_delta_publication,
                    )
                    from marivo.analysis.materialization.reads import payload_batches

                    rows = payload_batches(
                        self.store.project_root,
                        descriptor.storage_receipt,
                        policy=_READ_POLICY,
                        bindings=object_bindings,
                        row=descriptor.row_contract,
                        rows=descriptor.row_set_contract,
                        audit=True,
                    )
                    descriptor, findings = build_delta_publication(
                        descriptor,
                        rows,
                        artifact_ref=artifact_ref,
                        session_ref=self.session_ref,
                    )
                elif dataset.kind == "attribution":
                    from marivo.analysis.materialization.attribution_publication import (
                        build_attribution_publication,
                    )
                    from marivo.analysis.materialization.reads import payload_batches
                    from marivo.analysis.operators.attribution_contracts import AttributePayload

                    entity = any(
                        field.role_id == "entity_identity" for field in dataset.schema.columns
                    )
                    payload = (
                        dataset._root.payload
                        if isinstance(dataset._root, LogicalRootHandle)
                        else None
                    )
                    continuation = not isinstance(payload, AttributePayload)
                    proof_definition = next(
                        (
                            root.definition_fingerprint
                            for root in reversed(roots)
                            if isinstance(root.payload, AttributePayload)
                        ),
                        None,
                    )
                    top_k = next(
                        (
                            root.payload.spec.top_k
                            for root in reversed(roots)
                            if isinstance(root.payload, AttributePayload)
                        ),
                        None,
                    )
                    descriptor, findings = build_attribution_publication(
                        descriptor,
                        None
                        if entity or continuation
                        else payload_batches(
                            self.store.project_root,
                            descriptor.storage_receipt,
                            policy=_READ_POLICY,
                            bindings=object_bindings,
                            row=descriptor.row_contract,
                            rows=descriptor.row_set_contract,
                            audit=True,
                        ),
                        artifact_ref=artifact_ref,
                        session_ref=self.session_ref,
                        top_k=top_k,
                        source_summary=attribution_summary,
                        continuation=continuation,
                        proof_definition_fingerprint=proof_definition,
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
                    findings=findings,
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

    @contextmanager
    def _prepared_source(
        self,
        run_ref: str,
        source_step: SourceStep,
        all_records: Mapping[str, ArtifactRecord],
        sampling: list[SamplingRealization],
        validations: list[tuple[str, int]],
        sampling_by_root: dict[int, SamplingRealization],
    ) -> Iterator[tuple[Backend, CompiledDataset, dict[str, ir.Table]]]:
        source_dataset: Dataset = source_step.dataset
        if source_step.correlation_preparation:
            source_dataset = source_dataset._inputs[0]

        records = {
            value.state.artifact_ref.ref: all_records[value.state.artifact_ref.ref]
            for value in artifact_inputs(source_dataset)
        }
        entities = (
            required_entities(source_dataset, registry=source_step.binding.owner.semantic_registry)
            if isinstance(source_step.binding, SourceBinding)
            and isinstance(source_dataset, LogicalDataset)
            else ()
        )
        captures = (
            captured_parameters(source_dataset)
            if isinstance(source_dataset, LogicalDataset)
            else ()
        )
        backend: Backend | None = None
        execution: ResourceRecord | None = None
        opening = False
        phase = "authority_resolution"
        yielded = False
        try:
            domain = source_step.binding.datasource_id
            candidate: object
            if isinstance(source_step.binding, EngineBinding):
                from marivo.analysis.materialization.engine import checked_engine_path

                selected_receipt = next(iter(records.values())).descriptor.storage_receipt
                if not isinstance(selected_receipt, EngineReceipt):
                    raise _error("execution_boundary", run_ref)
                path = checked_engine_path(self.store.project_root, selected_receipt)
                execution = backend_reservation(run_ref, domain)
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
                execution = backend_reservation(run_ref, domain)
                self.store.reserve(execution)
                self._event("resource_create")
                opening = True
                candidate = _build_backend_from_effective(
                    datasource, effective, read_only=True
                ).backend
            if not isinstance(candidate, Backend):
                raise _error("execution_boundary", run_ref)
            backend = candidate
            backend.raw_sql("SET threads=1")
            backend.raw_sql("SET memory_limit='256MiB'")
            backend.raw_sql("SET max_temp_directory_size='0B'")
            backend.raw_sql("BEGIN TRANSACTION")
            tables: dict[str, ir.Table] = {}
            fences: list[_JsonFence] = []
            checked_schemas: set[str] = set()
            captured = {item.entity_ref.path: item for item in captures}
            for entity in entities:
                source = entity.source
                if isinstance(source, TableSourceIR):
                    physical_schema = None
                    if any(binding.data_type == "decimal" for _, binding in source.columns):
                        physical_schema = self._validate_source_schema(backend, entity)
                        checked_schemas.add(entity.ref.path)
                    tables[entity.ref.path] = _declared_table(entity, physical_schema)
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
                    fences.append(_JsonFence(entity, source, name, name + "_reader", values))
                    tables[entity.ref.path] = ibis.table(dict(entity.columns), name=name)
                else:
                    raise _error("source_binding", run_ref)
            phase = "ibis_expression_construction"
            engine_inputs: list[tuple[ir.Table, EngineReceipt, DatasetRowContract]] = []
            if isinstance(source_step.binding, EngineBinding):
                from marivo.analysis.compiler.lowering import compile_retained_rows
                from marivo.analysis.materialization.engine import attach_engine_scan

                retained_scans: dict[str, ir.Table] = {}
                retained_parts: dict[str, dict[str, ir.Table]] = {}
                for index, (reference, selected_record) in enumerate(records.items()):
                    descriptor = selected_record.descriptor
                    receipt = descriptor.storage_receipt
                    if not isinstance(receipt, EngineReceipt):
                        raise _error("execution_boundary", run_ref)
                    table = (
                        backend.table("rows")
                        if index == 0
                        else attach_engine_scan(backend, self.store.project_root, receipt)
                    )
                    retained_scans[reference] = table
                    tables[reference] = table
                    engine_inputs.append((table, receipt, descriptor.row_contract))
                    retained_parts[reference] = self._engine_parts(
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
                    source_dataset, retained_scans, input_parts=retained_parts
                )
            else:
                scans: dict[str, CompiledArtifactScan] = {}
                for reference, selected_record in records.items():
                    descriptor = selected_record.descriptor
                    receipt = descriptor.storage_receipt
                    if not isinstance(receipt, EngineReceipt):
                        raise _error("execution_boundary", run_ref)
                    from marivo.analysis.materialization.engine import attach_engine_scan

                    table = attach_engine_scan(backend, self.store.project_root, receipt)
                    engine_inputs.append((table, receipt, descriptor.row_contract))
                    entity = normalize_target_entity(
                        source_step.binding.owner.semantic_registry,
                        descriptor.population_authority.entity_ref,
                    )
                    retained_parts = (
                        self._engine_parts(
                            backend,
                            descriptor,
                            source_dataset,
                            input_dataset=next(
                                value
                                for value in artifact_inputs(source_dataset)
                                if value.state.artifact_ref.ref == reference
                            ),
                        )
                        if descriptor.row_contract.shape_id.family_id == "metric"
                        else {}
                    )
                    scans[reference] = CompiledArtifactScan(
                        table, entity, tuple(retained_parts.items())
                    )
                if not isinstance(source_dataset, LogicalDataset):
                    raise _error("implementation_registration", run_ref)
                recipe = compile_dataset(
                    source_dataset, tables, scans=scans, source_owner=source_step.binding.owner
                )
            if source_step.correlation_preparation:
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
                    preparations=(*recipe.preparations, *pair_checks)
                    if recipe.preparations
                    else (),
                )
            phase = "ibis_backend_compile"
            self._event("backend_compile")
            backend.compile(recipe.expression)
            preparations = compile_preparations(
                backend, recipe.preparations or recipe.validations, run_ref=run_ref
            )
            for preparation in preparations:
                if isinstance(preparation, CompiledSampleFence):
                    sqlglot.parse_one(sample_statement(backend, preparation), read="duckdb")
            phase = "source_binding"
            with _engine_deadline(backend):
                from marivo.analysis.materialization.engine import validate_engine_relation

                for table, receipt, row in engine_inputs:
                    validate_engine_relation(backend, table, receipt, row, self._record_statement)
                for entity in entities:
                    if (
                        isinstance(entity.source, TableSourceIR)
                        and entity.ref.path not in checked_schemas
                    ):
                        self._validate_source_schema(backend, entity)
                for fence in fences:
                    for name in (fence.reader_name, fence.relation_name):
                        self.store.reserve(
                            ResourceRecord(
                                run_ref=run_ref,
                                resource_kind="planner_temporary_relation",
                                execution_domain_id=domain,
                                ownership_nonce=execution.ownership_nonce,
                                cleanup_capability_id="duckdb_process_lifetime@v1",
                                safe_locator=f"{execution.safe_locator}/{name}",
                            )
                        )
                    self._event("source_statement")
                    source_table = read_json_source(
                        _ReservedJsonReader(backend, fence.reader_name, self._record_statement),
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
                for validation in preparations:
                    if isinstance(validation, CompiledSampleFence):
                        self.store.reserve(
                            ResourceRecord(
                                run_ref=run_ref,
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
                        sampling_by_root[validation.root_identity] = sampling[-1]
                        self.statistics.sampling_fences += 1
                        self.statistics.validation_queries += 1
                        validations.append((f"sampling.{len(sampling) - 1}.identity", 0))
                        continue
                    self._event("source_statement")
                    self.statistics.validation_queries += 1
                    self._record_statement("validation_batch", validation.sql)
                    validations.extend(execute_batch(backend, validation, run_ref=run_ref))
            yielded = True
            yield backend, recipe, tables
        except MaterializationError:
            raise
        except BaseException:
            if yielded:
                raise
            raise _error(phase, run_ref) from None
        finally:
            if backend is not None:
                try:
                    backend.raw_sql("ROLLBACK")
                except TransactionException as error:
                    # Engine publication may have committed its fence already.
                    # Disconnect below still proves the owned connection ended.
                    if (
                        str(error)
                        != "TransactionContext Error: cannot rollback - no transaction is active"
                    ):
                        raise
                finally:
                    backend.disconnect()
                    if execution is not None:
                        prove_local_termination(execution)
            elif execution is not None and not opening:
                prove_local_termination(execution)

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

    def _attribution_source_summary(
        self, backend: Backend, table: ir.Table, row: DatasetRowContract
    ) -> AttributionSourceSummary:
        """Reduce complete Attribution proof inside its engine; return only global facts."""
        from marivo.analysis.operators.attribution_contracts import AttributionSemantics

        semantics = row.family_semantics
        if not isinstance(semantics, AttributionSemantics):
            raise _error("output_validation")
        names = {field.field_id: field.name for field in row.schema.columns}

        def quote(name: str) -> str:
            return sge.to_identifier(name, quoted=True).sql(dialect="duckdb")

        scope = tuple(quote(names[key]) for key in semantics.scope_field_ids)
        keys = tuple(quote(names[key]) for key in row.key_field_ids)
        resolution = (*scope, quote("active_axis_mask"))
        identity = "struct_pack(" + ", ".join(f"{name} := {name}" for name in keys) + ")"
        grouping = ", ".join(resolution)
        scoped = (
            "struct_pack(" + ", ".join(f"{name} := {name}" for name in scope) + ")"
            if scope
            else "1"
        )
        sql = (
            f"WITH attributed AS ({backend.compile(table)}), reconciled AS ("
            f"SELECT sum(contribution) AS total, max(overall_delta) AS delta FROM attributed GROUP BY {grouping}) "
            f"SELECT count(DISTINCT {scoped}), (SELECT count(*) FROM reconciled), "
            "count(*) FILTER (WHERE status = 'ok'), count(*) FILTER (WHERE status = 'zero_total_delta'), "
            "CAST(coalesce((SELECT max(abs(total - delta)) FROM reconciled), 0) AS DOUBLE), "
            f"sha256(coalesce(string_agg(sha256(to_json({identity})), '' ORDER BY {', '.join(keys)}), '')) FROM attributed"
        )
        self._record_statement("attribution.source_summary", sql)
        self._event("source_statement")
        result: object = backend.raw_sql(sql).fetchone()
        if not isinstance(result, tuple) or len(result) != 6:
            raise _error("output_validation")
        scopes, resolutions, ok, zero, error, digest = result
        if (
            not all(type(value) is int and value >= 0 for value in (scopes, resolutions, ok, zero))
            or not isinstance(error, (int, float))
            or not isinstance(digest, str)
        ):
            raise _error("output_validation")
        return AttributionSourceSummary(
            scopes, resolutions, (("ok", ok), ("zero_total_delta", zero)), float(error), digest
        )

    def _engine_parts(
        self,
        backend: Backend,
        descriptor: ArtifactDescriptor,
        dataset: Dataset,
        *,
        input_dataset: MaterializedDataset | None = None,
    ) -> dict[str, ir.Table]:
        """Attach only consumed immutable states and verify native schema/support."""
        import hashlib

        from marivo.analysis.materialization.engine import attach_engine_scan
        from marivo.analysis.materialization.retained import (
            _part_state_columns,
            component_schema,
            selected_parts,
            source_private_part,
            validate_source_private_relation,
        )
        from marivo.analysis.materialization.storage import _integrity

        tables: dict[str, ir.Table] = {}
        with _engine_deadline(backend):
            for part in selected_parts(descriptor, dataset, input_dataset=input_dataset):
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
                if source_private_part(part):
                    primary_receipt = descriptor.storage_receipt
                    if not isinstance(primary_receipt, EngineReceipt):
                        _integrity(
                            "an engine primary for private membership", "invalid primary sink"
                        )
                    primary = attach_engine_scan(backend, self.store.project_root, primary_receipt)
                    validate_source_private_relation(
                        backend,
                        table,
                        primary,
                        descriptor.row_contract,
                        part.role,
                        self._record_statement,
                    )
                else:
                    component_schema(descriptor.row_contract, part.role, schema)
                self._record_statement("engine_check.part_count", backend.compile(table.count()))
                count: object = backend.execute(table.count())
                if count != receipt.realized_row_count:
                    _integrity("the exact committed part row count", "engine part count differs")
                required = (
                    []
                    if source_private_part(part)
                    else [
                        table[name].isnull()
                        for name, _, nullable in _part_state_columns(
                            descriptor.row_contract, part.role
                        )
                        if not nullable
                    ]
                )
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
        *,
        input_dataset: MaterializedDataset | None = None,
    ) -> tuple[tuple[LocalPartInput, ...], tuple[Iterable[pa.RecordBatch], ...]]:
        from marivo.analysis.materialization.reads import part_schema, read_part_batches
        from marivo.analysis.materialization.retained import (
            checked_component_batches,
            component_schema,
            selected_parts,
        )
        from marivo.analysis.materialization.storage import _limited

        selected = (
            *selected_parts(descriptor, dataset, input_dataset=input_dataset),
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
            if part.contract_id in ("metric.sufficient_components", "delta.sufficient_components")
        )

    @staticmethod
    def _require_projected_parts(recipe: CompiledDataset) -> None:
        from marivo.analysis.materialization.retained import reject_source_private_transfer

        if any(not isinstance(part, RetainedPartSpec) for part in recipe.retained_parts):
            reject_source_private_transfer()

    @staticmethod
    def _source_local_parts(
        dataset: LogicalDataset, recipe: CompiledDataset
    ) -> tuple[LocalPartInput, ...]:
        from marivo.analysis.materialization.retained import component_schema

        DatasetRuntime._require_projected_parts(recipe)
        schema = recipe.expression.schema().to_pyarrow()
        return tuple(
            LocalPartInput(
                part.role,
                part.contract_id,
                part.contract_version,
                pa.schema([schema.field(name) for name in part.column_names]),
                component_schema(
                    dataset.row_contract,
                    part.role,
                    pa.schema([schema.field(name) for name in part.column_names]),
                ),
            )
            for part in recipe.retained_parts
            if isinstance(part, RetainedPartSpec)
        )

    def _run_local_graph(
        self,
        physical: PhysicalStageGraph,
        boundaries: tuple[LocalBoundary, ...],
        streams: tuple[LocalInputStreams, ...],
        run_ref: str,
        *,
        cancel_source: Callable[[], None],
    ) -> LocalResult:
        from marivo.analysis.operators.attribution_contracts import (
            AttributePayload,
            AttributeSpecV1,
        )
        from marivo.analysis.operators.contracts import ComparePayload, CompareSpecV1

        stages: list[LocalStage] = []
        for step in physical.local_steps:
            root = step.dataset._root
            if not isinstance(root, LogicalRootHandle):
                raise _error("implementation_registration", run_ref)
            payload = root.payload
            call: RowCall | CompareSpecV1 | AttributeSpecV1 | CorrelateSpecV1
            if isinstance(payload, (ComparePayload, AttributePayload, CorrelatePayload)):
                call = payload.spec
            elif isinstance(payload, (MetricPayload, RetainedRowsPayload, RetainedFoldPayload)):
                if len(step.inputs) != 1:
                    raise _error("implementation_registration", run_ref)
                source = step.dataset._inputs[0]
                call = RowCall(
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
            else:
                raise _error("implementation_registration", run_ref)
            stages.append(LocalStage(step.output, step.inputs, call))
        reservation = reserve_worker(self.store, run_ref, self.session_ref)
        try:
            self._event("local_worker_reserved")
        except BaseException:
            prove_local_termination(reservation.execution)
            raise
        request = LocalGraphRequest(
            boundaries,
            tuple(stages),
            physical.primary_output,
            self.local_policy,
            time.monotonic() + self.local_policy.deadline_seconds,
        )
        result = supervise(
            request,
            (),
            lifetime=reservation,
            cancel_source=cancel_source,
            terminal=lambda: prove_local_termination(reservation.execution),
            input_streams=streams,
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
                phase=run_failure_phase(phase, phase),
                kind="execution_failed",
                safe_message="The Dataset action failed before publication.",
                safe_location=f"dataset.{phase}",
                expected=error.expected or "a complete registered execution",
                received=error.received or "the action failed",
                repair=error.repair,
            ),
            resolved_resources=resolved,
        )
        return None

    def _validate_source_schema(
        self, backend: Backend, entity: TargetEntityContract
    ) -> ibis.Schema:
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
            physical_type = (
                actual[binding.source].copy(nullable=True) if binding.source in actual else None
            )
            decimal_match = (
                binding.data_type == "decimal"
                and isinstance(physical_type, dt.Decimal)
                and physical_type.precision is not None
                and physical_type.scale is not None
                and 0 <= physical_type.scale <= physical_type.precision <= 38
            )
            if physical_type != declared and not decimal_match:
                raise MaterializationError(
                    expected="physical source columns matching their exact declared logical types",
                    received="the governed source schema differs from its declaration",
                    repair="Correct the declaration or physical schema before executing this Dataset.",
                    stage="output_validation",
                    run_ref=self.last_run_ref,
                )
        return actual

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
