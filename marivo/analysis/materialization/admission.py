"""Private first-route execution from pure definition to committed Dataset authority."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal
from uuid import uuid4

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa

from marivo._temporal import PeriodCalendarSnapshotV1
from marivo.analysis.compiler import captured_parameters, compile_dataset, required_entities
from marivo.analysis.compiler.nodes import (
    CompiledArtifactScan,
    CompiledDataset,
    CompiledRelationFence,
    CompiledSampleFence,
    RetainedPartSpec,
)
from marivo.analysis.compiler.normalize import artifact_inputs, logical_roots
from marivo.analysis.compiler.placement import (
    ArtifactReadStep,
    ExecutionBinding,
    ParquetBinding,
    PhysicalStageGraph,
    SourceBinding,
    SourceStep,
    place,
    source_binding,
)
from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.datasets.handles import LogicalRootHandle, _validate_logical_root
from marivo.analysis.domains.completeness import (
    EventCoverageProvider,
    EventCoverageResolution,
)
from marivo.analysis.domains.contracts import (
    EventFunnelPayload,
    EventFunnelSemantics,
    EventPayload,
    EventTimeToEventSemantics,
)
from marivo.analysis.domains.event import LogicalEventDataset, MaterializedEventDataset
from marivo.analysis.domains.event_attribution import FunnelAttributePayload, FunnelAttributeSpec
from marivo.analysis.domains.event_comparison import FunnelComparePayload, FunnelCompareSpec
from marivo.analysis.domains.lifecycle import (
    LifecyclePayload,
    LogicalLifecycleDataset,
    MaterializedLifecycleDataset,
)
from marivo.analysis.domains.lifecycle_reducers import REDUCER_TYPES, LifecycleReducerPayload
from marivo.analysis.evidence import _dataset_reads
from marivo.analysis.evidence._dataset_types import (
    ArtifactDigest,
    ArtifactRevalidation,
    Finding,
    FindingPage,
)
from marivo.analysis.materialization import contracts as codec
from marivo.analysis.materialization import recovery
from marivo.analysis.materialization.attribution_publication import AttributionSourceSummary
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    ArtifactRecord,
    LocalReceipt,
    ObjectReceipt,
    ResourceRecord,
    RunDatasetInput,
    RunFailure,
    RunRecord,
    SamplingRealization,
    StorageReceipt,
    run_failure_phase,
)
from marivo.analysis.materialization.duckdb_execution import (
    json_statement,
)
from marivo.analysis.materialization.duckdb_statements import attribution_summary_sql
from marivo.analysis.materialization.errors import (
    MaterializationError,
)
from marivo.analysis.materialization.event_codec import EventEvidenceSummary
from marivo.analysis.materialization.event_reducer_codec import (
    EventReducerEvidenceSummary,
    EventSelectionEvidenceSummary,
)
from marivo.analysis.materialization.execution import ExecutionAdapter
from marivo.analysis.materialization.execution_key import execution_key
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.materialization.lifecycle_codec import LifecycleEvidenceSummary
from marivo.analysis.materialization.lifecycle_reducer_codec import (
    ContinuationEvidence,
    LifecycleReducerEvidence,
    LifecycleSelectionEvidence,
)
from marivo.analysis.materialization.local_execution import (
    ArtifactInput,
    LocalBoundary,
    LocalGraphRequest,
    LocalInputStreams,
    LocalPartInput,
    LocalResult,
    LocalStage,
    StreamInput,
    execute_local,
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
    reserve_output,
)
from marivo.analysis.materialization.sampling import (
    execute_sample,
    sample_statement,
)
from marivo.analysis.materialization.storage import (
    DatasetWriteResult,
    IndependentPartWrite,
    PartWriteSpec,
    ReadPolicy,
    sampling_state_read,
    write_local_dataset,
)
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.targets import (
    LocalTarget,
    MaterializationTarget,
    ObjectBinding,
    ObjectTarget,
    ProjectTarget,
    engine_domain,
    object_access,
    selection_error,
)
from marivo.analysis.materialization.validation import compile_preparations, execute_batch
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.observation.contracts import (
    MetricPayload,
    ObservationSourceContext,
    PopulationPayload,
    RetainedRowsPayload,
)
from marivo.analysis.observation.fold_contracts import RetainedFoldPayload
from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset
from marivo.analysis.observation.population import (
    LogicalPopulationDataset,
    MaterializedPopulationDataset,
)
from marivo.analysis.observation.temporal import ReportTimeAuthority
from marivo.analysis.operators.association import (
    LogicalAssociationDataset,
    MaterializedAssociationDataset,
)
from marivo.analysis.operators.association_contracts import (
    AssociationSearchSummary,
    CorrelatePayload,
    CorrelateSpecV1,
    selection_description,
)
from marivo.analysis.operators.attribution import (
    LogicalAttributionDataset,
    MaterializedAttributionDataset,
)
from marivo.analysis.operators.candidate_contracts import (
    CandidateDefinition,
    CandidatePayload,
    CandidateSearchSummary,
    CandidateSpecV1,
    EntityCandidateEvaluationSummary,
)
from marivo.analysis.operators.candidate_dataset import (
    LogicalCandidateDataset,
    MaterializedCandidateDataset,
)
from marivo.analysis.operators.delta import LogicalDeltaDataset, MaterializedDeltaDataset
from marivo.analysis.operators.driver_contracts import (
    DriverCandidateDefinition,
    DriverCandidateEvaluationSummary,
    DriverCandidatePayload,
    DriverCandidateSpecV1,
)
from marivo.analysis.operators.forecast_contracts import (
    ForecastPayload,
    ForecastSpecV1,
    ForecastTrainingSummary,
)
from marivo.analysis.operators.forecast_dataset import (
    LogicalForecastDataset,
    MaterializedForecastDataset,
)
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
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.ir import TargetEntityContract
from marivo.semantic.validator import Registry, normalize_target_entity

_PREVIEW_MAX_OUTPUT_BYTES = 8192
_READ_POLICY = ReadPolicy()
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


@dataclass(frozen=True, slots=True, repr=False)
class _JsonFence:
    entity: TargetEntityContract
    source: JsonSourceIR
    relation_name: str
    reader_name: str
    parameters: Mapping[str, QueryParamScalar | QueryParamScalarList]


@dataclass(frozen=True, slots=True, repr=False)
class _ReservedJsonReader:
    backend: ExecutionAdapter
    name: str
    record: Callable[[str, str], None]

    def raw_sql(self, query: str) -> None:
        self.record("source_setting", query)
        self.backend.submit(self.backend.statement(query, role="source_setting"))

    def read_json(self, path: str, *, columns: Mapping[str, str], format: str = "auto") -> ir.Table:
        self.record(
            "source_fence_reader",
            json_statement(self.name, path, columns, format),
        )
        return self.backend.read_json(
            path,
            table_name=self.name,
            columns=columns,
            format=format,
        )


def _local_output_batches(table: pa.Table) -> list[pa.RecordBatch]:
    """Keep the exact schema when a valid local selection produces zero rows."""
    return table.to_batches(max_chunksize=1024) or [
        pa.RecordBatch.from_arrays(
            [pa.array([], type=field.type) for field in table.schema], schema=table.schema
        )
    ]


def _error(stage: str, run_ref: str | None = None) -> MaterializationError:
    return MaterializationError(
        expected="a supported and complete registered Dataset execution",
        received="the admitted action could not complete its current phase; remote read status may be unknown",
        repair="Inspect the safe Run phase, correct its source or resource requirement, and retry.",
        stage=stage,
        run_ref=run_ref,
    )


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
        target: MaterializationTarget | ProjectTarget = _DEFAULT_TARGET,
        object_bindings: tuple[ObjectBinding, ...] = (),
        event_coverage_provider: EventCoverageProvider | None = None,
    ) -> None:
        session_record = store.session(session_ref)
        if session_record is None:
            raise _error("authority_resolution")
        self.report_time = ReportTimeAuthority(
            timezone=session_record.report_timezone_name,
            resolution=session_record.report_timezone_resolution,
        )
        self.store = store
        self.target = target
        self.object_bindings = object_bindings
        self.event_coverage_provider = event_coverage_provider
        self.session_ref = session_ref
        self._hook = event
        self.statistics = ExecutionStatistics()
        self.last_run_ref: str | None = None
        self._source_context = ObservationSourceContext()

    @classmethod
    def create(
        cls,
        project_root: Path,
        name: str,
        *,
        question: str | None = None,
        report_timezone: str | None = None,
        event: Callable[[str], None] | None = None,
        target: MaterializationTarget | ProjectTarget = _DEFAULT_TARGET,
        object_bindings: tuple[ObjectBinding, ...] = (),
        event_coverage_provider: EventCoverageProvider | None = None,
    ) -> DatasetRuntime:
        from marivo.analysis.timezone import resolve_system_timezone, zoneinfo_from_name

        zone = resolve_system_timezone() if report_timezone is None else None
        timezone_name = zone.name if zone is not None else report_timezone
        assert timezone_name is not None
        timezone_resolution: Literal["iana", "fixed_offset"] = (
            "fixed_offset" if zone is not None and zone.resolution == "fixed_offset" else "iana"
        )
        if report_timezone is not None:
            zoneinfo_from_name(report_timezone)
        store = SessionStore(project_root)
        record = store.session_by_name(name)
        if record is None:
            candidate_ref = "session_" + uuid4().hex
            with session_writer_guard(
                store.layout.lock_path(candidate_ref), session_ref=candidate_ref
            ):
                record = store.create_session(
                    name,
                    session_ref=candidate_ref,
                    question=question,
                    report_timezone_name=timezone_name,
                    report_timezone_resolution=timezone_resolution,
                )
                if record.session_ref == candidate_ref:
                    return cls(
                        store,
                        record.session_ref,
                        event=event,
                        target=target,
                        object_bindings=object_bindings,
                        event_coverage_provider=event_coverage_provider,
                    )
            # A competing creator won this name. Its guard must be acquired only
            # after the unused candidate guard has been released.
        runtime = cls(
            store,
            record.session_ref,
            event=event,
            target=target,
            object_bindings=object_bindings,
            event_coverage_provider=event_coverage_provider,
        )
        with session_writer_guard(
            store.layout.lock_path(record.session_ref), session_ref=record.session_ref
        ):
            resolved = store.session_by_name(name)
            if resolved is None or resolved.session_ref != record.session_ref:
                raise _error("authority_resolution")
            if report_timezone is not None and resolved.report_timezone_name != report_timezone:
                from marivo.analysis.errors import SessionTimezoneConflict

                raise SessionTimezoneConflict(
                    message="Session report timezone conflicts with the persisted timezone.",
                    context={
                        "persisted_report_tz": resolved.report_timezone_name,
                        "requested_report_tz": report_timezone,
                    },
                )
            reconcile_session(
                store,
                record.session_ref,
                event=runtime._event,
                object_bindings=object_bindings,
            )
            store.activate(record.session_ref, question=question)
        return runtime

    @classmethod
    def open(
        cls,
        project_root: Path,
        session_ref: str,
        *,
        event: Callable[[str], None] | None = None,
        target: MaterializationTarget | ProjectTarget = _DEFAULT_TARGET,
        object_bindings: tuple[ObjectBinding, ...] = (),
        event_coverage_provider: EventCoverageProvider | None = None,
    ) -> DatasetRuntime:
        if not MaterializationLayout(project_root).store_db.is_file():
            raise _error("authority_resolution")
        return cls(
            SessionStore.open_existing(project_root),
            session_ref,
            event=event,
            target=target,
            object_bindings=object_bindings,
            event_coverage_provider=event_coverage_provider,
        )

    def sources(
        self,
        *,
        semantic_registry: Registry,
        sidecar: CompiledExpressionSidecar,
        catalog: SemanticCatalog | None = None,
        period_calendar_snapshots: tuple[PeriodCalendarSnapshotV1, ...] = (),
    ) -> LazySources:
        sources = make_lazy_sources(
            semantic_registry=semantic_registry,
            sidecar=sidecar,
            action_port=self,
            session_id=self.session_ref,
            store_id=self.store.store_id,
            catalog=catalog,
            period_calendar_snapshots=period_calendar_snapshots,
            report_time=self.report_time,
        )
        self._source_context.current = sources._owner
        return sources

    @staticmethod
    def recent(
        project_root: Path, *, limit: int = 20, cursor: str | None = None
    ) -> SessionSummaryPage:
        """Read existing v5 Session history without creating or activating a Session."""
        _lazy_runtime_reads.page_after(limit, cursor, operation="recent")
        return _lazy_history.recent(
            SessionStore.open_existing(project_root), limit=limit, cursor=cursor
        )

    @staticmethod
    def inspect(
        project_root: Path, name: str, *, run_limit: int = 5, run_cursor: str | None = None
    ) -> SessionInspection:
        """Read a named existing v5 Session and one bounded Run page."""
        _lazy_runtime_reads.page_after(run_limit, run_cursor, operation="inspect")
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
            record,
            session_ref=self.session_ref,
            store_id=self.store.store_id,
            action_port=self,
            source_context=self._source_context,
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
        if record.descriptor.candidate_evidence is not None:
            from marivo.analysis.operators.discovery import _contract_facts as candidate_facts

            candidate_evidence = record.descriptor.candidate_evidence
            evaluation = candidate_evidence.evaluation
            lines.extend(f"Discovery {name}: {value}" for name, value in candidate_facts(dataset))
            if isinstance(evaluation, EntityCandidateEvaluationSummary):
                lines.append(
                    f"Evaluation: input_rows={evaluation.input_row_count}; non_null_values={evaluation.non_null_value_count}; null_values={evaluation.null_value_count}; center={evaluation.center}; scale={evaluation.scale}; scale_method={evaluation.scale_method}"
                )
            elif isinstance(evaluation, DriverCandidateEvaluationSummary):
                lines.append(
                    f"Evaluation: input_rows={evaluation.input_row_count}; scopes={evaluation.scope_count}; evaluated_axes={evaluation.evaluated_axis_count}/{evaluation.searched_axis_count}; zero_contribution_axes={evaluation.zero_contribution_axis_count}"
                )
            else:
                lines.append(
                    f"Evaluation: input_rows={evaluation.input_row_count}; evaluated_series={evaluation.evaluated_series_count}/{evaluation.series_count}; evaluated_units={evaluation.evaluated_unit_count}/{evaluation.searched_unit_count}"
                )
            lines.append(
                f"Candidates: qualifying={evaluation.pre_limit_candidate_count}; discovery_output={evaluation.emitted_candidate_count}; current_rows={candidate_evidence.row_count}"
            )
        if record.descriptor.forecast_evidence is not None:
            from marivo.analysis.operators.forecast import _contract_facts

            forecast_evidence = record.descriptor.forecast_evidence
            lines.extend(f"Forecast {name}: {value}" for name, value in _contract_facts(dataset))
            lines.append(
                f"Training: series={forecast_evidence.training.series_count}; periods={forecast_evidence.training.training_row_count}; residual_df={forecast_evidence.training.residual_df}; zero_residual_series={forecast_evidence.training.zero_residual_series_count}"
            )
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
        duration_fields = tuple(
            field.name for field in dataset.schema.columns if field.logical_type_id == "duration"
        )
        if duration_fields:
            lines.insert(1, "Durations (microseconds): " + ", ".join(duration_fields))
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

    def execute_candidate(self, dataset: LogicalCandidateDataset) -> MaterializedCandidateDataset:
        result = self._execute(dataset)
        if not isinstance(result, MaterializedCandidateDataset):
            raise _error("presentation")
        return result

    def execute_lifecycle(self, dataset: LogicalLifecycleDataset) -> MaterializedLifecycleDataset:
        result = self._execute(dataset)
        if not isinstance(result, MaterializedLifecycleDataset):
            raise _error("publication")
        return result

    def execute_event(self, dataset: LogicalEventDataset) -> MaterializedEventDataset:
        result = self._execute(dataset)
        if not isinstance(result, MaterializedEventDataset):
            raise _error("publication")
        return result

    def execute_forecast(self, dataset: LogicalForecastDataset) -> MaterializedForecastDataset:
        result = self._execute(dataset)
        if not isinstance(result, MaterializedForecastDataset):
            raise _error("publication", None)
        return result

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
        if dataset._owner.runtime_metric_bindings:
            from marivo.analysis.datasets.base import _make_materialized_dataset

            result = _make_materialized_dataset(
                owner=replace(
                    result._owner, runtime_metric_bindings=dataset._owner.runtime_metric_bindings
                ),
                registry=result._registry,
                family_id="metric",
                row_contract=result.row_contract,
                row_set_contract=result.row_set_contract,
                state=result.state,
                definition_fingerprint=result.definition_fingerprint,
            )
            assert isinstance(result, MaterializedMetricDataset)
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
                    if (
                        isinstance(value._root, LogicalRootHandle)
                        and isinstance(
                            value._root.payload,
                            (PopulationPayload, MetricPayload, EventPayload, LifecyclePayload),
                        )
                    ) or (
                        isinstance(value._root, LogicalRootHandle)
                        and isinstance(
                            value._root.payload, (EventFunnelPayload, LifecycleReducerPayload)
                        )
                        and bool(value._root.payload.axes)
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
                if not isinstance(receipt, (LocalReceipt, ObjectReceipt)):
                    raise _error("execution_boundary")
                # A fixed Parquet adapter can participate in the one existing
                # source domain, or own a source-free native retained stage.
                from marivo.analysis.operators.registry import supports_retained_import

                if len(source_candidates) == 1 and supports_retained_import(
                    source_candidates[0].adapter
                ):
                    return source_candidates[0]
                from marivo.analysis.operators.registry import admit_retained_rows

                admit_retained_rows(value)
                return ParquetBinding(
                    self,
                    "parquet",
                    codec.digest(("parquet", 1)),
                )

            physical = place(dataset, artifact_binding=admitted_binding)
            source_steps = tuple(step for step in physical.steps if isinstance(step, SourceStep))
            from marivo.analysis.materialization.execution import resolve_execution

            for admitted_step in source_steps:
                implementation = resolve_execution(admitted_step.implementation.backend)
                if implementation is None:
                    raise _error("implementation_registration")
                implementation.admit(admitted_step.dataset)
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
            backend: ExecutionAdapter | None = None
            execution: ResourceRecord | None = None
            phase = "storage_selection"
            object_bindings = self.object_bindings
            try:
                from marivo.analysis.materialization.project_storage import configured_target

                target = (
                    configured_target(self.target.project_root)
                    if isinstance(self.target, ProjectTarget)
                    else self.target
                )
                object_bindings = self.object_bindings
                self._validate_target(source_step, physical, target, object_bindings)
                phase = "authority_resolution"
                from marivo.analysis.materialization.event_comparison_publication import (
                    validate_checkpoint_inputs,
                )

                validate_checkpoint_inputs(
                    dataset, {ref: record.descriptor for ref, record in records.items()}
                )
                validations: list[tuple[str, int]] = []
                sampling: list[SamplingRealization] = []
                sampling_by_root: dict[int, SamplingRealization] = {}
                attribution_summary: AttributionSourceSummary | None = None
                association_summary: AssociationSearchSummary | None = None
                forecast_summary: ForecastTrainingSummary | None = None
                candidate_summary: CandidateSearchSummary | None = None
                lifecycle_summary: LifecycleEvidenceSummary | ContinuationEvidence | None = None
                event_summary: EventEvidenceSummary | EventReducerEvidenceSummary | None = None
                selection_summary: EventSelectionEvidenceSummary | None = None
                for input_record in records.values():
                    descriptor = input_record.descriptor
                    if descriptor.lifecycle_evidence is not None:
                        lifecycle_summary = descriptor.lifecycle_evidence
                    if descriptor.event_evidence is not None:
                        event_summary = descriptor.event_evidence
                    if descriptor.subject_selection_evidence is not None:
                        selection_summary = descriptor.subject_selection_evidence
                    if descriptor.candidate_evidence is not None:
                        candidate_summary = CandidateSearchSummary(
                            descriptor.candidate_evidence.definition,
                            descriptor.candidate_evidence.evaluation,
                        )
                    if descriptor.forecast_evidence is not None:
                        forecast_summary = descriptor.forecast_evidence.training
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
                    prepared: dict[
                        int, tuple[ExecutionAdapter, CompiledDataset, dict[str, ir.Table]]
                    ] = {}
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
                        if proof_recipe.lifecycle_coverage is not None:
                            from marivo.analysis.materialization.lifecycle_publication import (
                                native_summary,
                            )

                            lifecycle_summary = native_summary(
                                proof_backend,
                                proof_recipe,
                                source_boundary.dataset.row_contract,
                                self._record_statement,
                            )
                        if proof_recipe.lifecycle_reducer_coverage is not None and (
                            isinstance(
                                source_boundary.dataset.row_contract.family_semantics, REDUCER_TYPES
                            )
                            or proof_recipe.lifecycle_selection_payload is not None
                        ):
                            from marivo.analysis.materialization.lifecycle_reducer_publication import (
                                native_summary as continuation_summary,
                            )

                            lifecycle_summary = continuation_summary(
                                proof_backend,
                                proof_recipe,
                                source_boundary.dataset.row_contract,
                                self._record_statement,
                                filtered=isinstance(
                                    source_boundary.dataset._root, LogicalRootHandle
                                )
                                and source_boundary.dataset._root.operator_id == "lifecycle.where",
                            )
                        if proof_recipe.event_proof is not None:
                            from marivo.analysis.materialization.event_codec import (
                                summary_from_proof,
                            )

                            if proof_recipe.event_coverage is None:
                                raise _error("output_validation", run.run_ref)
                            self._record_statement(
                                "event.journey_summary",
                                proof_backend.compile(proof_recipe.event_proof),
                            )
                            self._event("source_statement")
                            checked_event = proof_backend.read_table(
                                proof_backend.prepare(
                                    proof_recipe.event_proof, role="event.journey_summary"
                                )
                            )
                            if checked_event.num_rows != 1:
                                raise _error("output_validation", run.run_ref)
                            event_summary = summary_from_proof(
                                checked_event.to_pylist()[0], proof_recipe.event_coverage
                            )
                            boundary_validations.append(("event.journey_output", 0))
                        if proof_recipe.event_reducer_proof is not None:
                            from marivo.analysis.materialization.event_reducer_codec import (
                                summary_from_proof as reducer_summary,
                            )

                            if proof_recipe.event_reducer_coverage is None:
                                raise _error("output_validation", run.run_ref)
                            self._record_statement(
                                "event.reducer_summary",
                                proof_backend.compile(proof_recipe.event_reducer_proof),
                            )
                            self._event("source_statement")
                            checked_reducer = proof_backend.read_table(
                                proof_backend.prepare(
                                    proof_recipe.event_reducer_proof,
                                    role="event.reducer_summary",
                                )
                            )
                            if checked_reducer.num_rows != 1:
                                raise _error("output_validation", run.run_ref)
                            event_summary = reducer_summary(
                                str(source_boundary.dataset.row_contract.shape_id),
                                checked_reducer.to_pylist()[0],
                                proof_recipe.event_reducer_coverage,
                            )
                            boundary_validations.append(("event.reducer_output", 0))
                        if proof_recipe.selection_proof is not None:
                            from marivo.analysis.materialization.event_reducer_codec import (
                                selection_summary_from_proof,
                            )

                            if (
                                proof_recipe.selection_coverage is None
                                or proof_recipe.selection_payload is None
                                or proof_recipe.selection_input_definition is None
                            ):
                                raise _error("output_validation", run.run_ref)
                            self._record_statement(
                                "event.selection_summary",
                                proof_backend.compile(proof_recipe.selection_proof),
                            )
                            self._event("source_statement")
                            checked_selection = proof_backend.read_table(
                                proof_backend.prepare(
                                    proof_recipe.selection_proof, role="event.selection_summary"
                                )
                            )
                            if checked_selection.num_rows != 1:
                                raise _error("output_validation", run.run_ref)
                            selection_summary = selection_summary_from_proof(
                                checked_selection.to_pylist()[0],
                                proof_recipe.selection_coverage,
                                journey=proof_recipe.selection_payload.journey,
                                step=proof_recipe.selection_payload.selection.step,
                                input_definition=proof_recipe.selection_input_definition,
                            )
                            boundary_validations.append(("event.selection_output", 0))
                        if proof_recipe.candidate_proof is not None:
                            from marivo.analysis.compiler.entity_candidate import (
                                decode_candidate_proof,
                            )

                            if proof_recipe.candidate_definition is None:
                                raise _error("implementation_registration", run.run_ref)
                            self._record_statement(
                                "candidate.driver_summary"
                                if isinstance(
                                    proof_recipe.candidate_definition, DriverCandidateDefinition
                                )
                                else "candidate.entity_summary",
                                proof_backend.compile(proof_recipe.candidate_proof),
                            )
                            self._event("source_statement")
                            scalar_proof = proof_backend.read_table(
                                proof_backend.prepare(
                                    proof_recipe.candidate_proof,
                                    role="candidate.driver_summary"
                                    if isinstance(
                                        proof_recipe.candidate_definition,
                                        DriverCandidateDefinition,
                                    )
                                    else "candidate.entity_summary",
                                )
                            )
                            if scalar_proof.num_rows != 1:
                                raise _error("output_validation", run.run_ref)
                            if isinstance(
                                proof_recipe.candidate_definition, DriverCandidateDefinition
                            ):
                                from marivo.analysis.compiler.driver_candidate import (
                                    decode_driver_candidate_proof,
                                )

                                candidate_summary = decode_driver_candidate_proof(
                                    scalar_proof.to_pylist()[0],
                                    proof_recipe.candidate_definition,
                                )
                            else:
                                candidate_summary = decode_candidate_proof(
                                    scalar_proof.to_pylist()[0],
                                    proof_recipe.candidate_definition,
                                )
                        if proof_recipe.association_proof is not None:
                            from marivo.analysis.operators.association_values import (
                                summarize_search,
                            )

                            proof_sql = proof_backend.compile(proof_recipe.association_proof)
                            self._record_statement("association.search_summary", proof_sql)
                            self._event("source_statement")
                            proof_table = proof_backend.read_table(
                                proof_backend.prepare(
                                    proof_recipe.association_proof,
                                    role="association.search_summary",
                                )
                            )
                            association_summary = summarize_search(
                                proof_table.to_pandas(types_mapper=pd.ArrowDtype),
                                source_boundary.dataset.row_contract,
                            )
                        if (
                            proof_recipe.attribution_proof is not None
                            and source_boundary.dataset.kind == "attribution"
                        ):
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
                        current_backend, recipe, _tables = prepared[source_step.output]
                        if (
                            dataset.kind == "candidate"
                            and dataset.row_contract.shape_id.local_shape_id == "entity-outlier"
                        ):
                            from marivo.analysis.compiler.entity_candidate import (
                                entity_candidate_output_proof,
                            )

                            if (
                                candidate_summary is None
                                or not isinstance(
                                    candidate_summary.evaluation,
                                    EntityCandidateEvaluationSummary,
                                )
                                or not isinstance(candidate_summary.definition, CandidateDefinition)
                            ):
                                raise _error("output_validation", run.run_ref)
                            output_proof = entity_candidate_output_proof(
                                recipe.expression,
                                dataset.row_contract,
                                candidate_summary.definition,
                                evaluation=candidate_summary.evaluation,
                            )
                            self._record_statement(
                                "candidate.entity_output",
                                current_backend.compile(output_proof),
                            )
                            self._event("source_statement")
                            checked = current_backend.read_table(
                                current_backend.prepare(
                                    output_proof, role="candidate.entity_output"
                                )
                            )
                            if (
                                checked.column_names != ["violations"]
                                or checked.num_rows != 1
                                or checked["violations"][0].as_py() != 0
                            ):
                                raise _error("output_validation", run.run_ref)
                            validations.append(("candidate.entity_output", 0))
                        if (
                            dataset.kind == "candidate"
                            and dataset.row_contract.shape_id.local_shape_id == "driver-axis"
                        ):
                            from marivo.analysis.compiler.driver_candidate import (
                                driver_candidate_output_proof,
                            )

                            if (
                                candidate_summary is None
                                or not isinstance(
                                    candidate_summary.definition, DriverCandidateDefinition
                                )
                                or not isinstance(
                                    candidate_summary.evaluation,
                                    DriverCandidateEvaluationSummary,
                                )
                            ):
                                raise _error("output_validation", run.run_ref)
                            driver_proof = driver_candidate_output_proof(
                                recipe.expression,
                                dataset.row_contract,
                                candidate_summary.definition,
                                evaluation=candidate_summary.evaluation,
                            )
                            self._record_statement(
                                "candidate.driver_output", current_backend.compile(driver_proof)
                            )
                            self._event("source_statement")
                            checked_driver = current_backend.read_table(
                                current_backend.prepare(
                                    driver_proof, role="candidate.driver_output"
                                )
                            )
                            if (
                                checked_driver.column_names != ["violations"]
                                or checked_driver.num_rows != 1
                                or checked_driver["violations"][0].as_py() != 0
                            ):
                                raise _error("output_validation", run.run_ref)
                            validations.append(("candidate.driver_output", 0))
                        from marivo.analysis.compiler.nodes import RetainedRelationSpec
                        from marivo.analysis.materialization.retained import (
                            validate_source_private_relation,
                        )

                        for part in recipe.retained_parts:
                            if isinstance(part, RetainedRelationSpec):
                                validate_source_private_relation(
                                    current_backend,
                                    part.expression,
                                    recipe.expression.select(recipe.primary_columns),
                                    dataset.row_contract,
                                    part.role,
                                    self._record_statement,
                                )
                        independent_parts = tuple(
                            IndependentPartWrite(
                                part.role,
                                self._batches(
                                    current_backend,
                                    part.expression,
                                    1024,
                                ),
                            )
                            for part in recipe.retained_parts
                            if isinstance(part, RetainedRelationSpec)
                        )
                        incoming = self._batches(
                            current_backend,
                            recipe.expression,
                            1024,
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
                            independent_parts=independent_parts,
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
                                current_backend, recipe, _tables = prepared[step.output]
                                if step.operation == "correlation":
                                    from marivo.analysis.materialization.local_execution import (
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
                                    pair_count = current_backend.read_scalar(
                                        current_backend.statement(
                                            count_sql,
                                            role="correlation_cardinality",
                                            inputs=(
                                                current_backend.prepare(
                                                    recipe.expression.aggregate(
                                                        __mv_rows=recipe.expression.count()
                                                    )
                                                ),
                                            ),
                                        )
                                    )
                                    if type(pair_count) is not int or pair_count < 0:
                                        raise _error("output_validation", run.run_ref)
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
                                                1024,
                                            )
                                        )
                                    )
                                    continue
                                if step.operation == "distribution":
                                    from marivo.analysis.materialization.local_execution import (
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
                                    expected_count: object = current_backend.read_scalar(
                                        current_backend.statement(
                                            count_sql,
                                            role="distribution_cardinality",
                                            inputs=(
                                                current_backend.prepare(
                                                    recipe.expression.aggregate(
                                                        __mv_rows=recipe.expression.count()
                                                    )
                                                ),
                                            ),
                                        )
                                    )
                                    if (
                                        not isinstance(expected_count, int)
                                        or isinstance(expected_count, bool)
                                        or expected_count < 0
                                    ):
                                        raise MaterializationError(
                                            expected="a non-negative source-certified coalition count",
                                            received="invalid coalition count",
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
                                                1024,
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
                                            1024,
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
                                current_backend.interrupt()

                        local_result = self._run_local_graph(
                            physical,
                            tuple(boundaries),
                            tuple(streams),
                            run.run_ref,
                            cancel_source=cancel_sources,
                        )
                        attribution_summary = (
                            local_result.summaries.attribution or attribution_summary
                        )
                        association_summary = (
                            local_result.summaries.association or association_summary
                        )
                        forecast_summary = local_result.summaries.forecast or forecast_summary
                        candidate_summary = local_result.summaries.candidate or candidate_summary
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
                            _local_output_batches(local_result.table),
                            run.run_ref,
                            parts=self._local_output_parts(local_result),
                            sampling=tuple(sampling),
                            source_key_validation=True,
                            target=target,
                            object_bindings=object_bindings,
                        )
                from marivo.analysis.materialization.parquet_scan import checked_local_path
                from marivo.analysis.materialization.retained import selected_parts

                for reference, input_record in records.items():
                    consumed_receipts = [input_record.descriptor.storage_receipt]
                    if any(
                        value.state.artifact_ref.ref == reference
                        for boundary in source_steps
                        for value in artifact_inputs(boundary.dataset)
                    ):
                        consumed_receipts.extend(
                            part.storage_receipt
                            for part in selected_parts(
                                input_record.descriptor,
                                dataset,
                                input_dataset=next(
                                    value
                                    for value in retained_inputs
                                    if value.state.artifact_ref.ref == reference
                                ),
                            )
                        )
                    for receipt in consumed_receipts:
                        if isinstance(receipt, LocalReceipt):
                            checked_local_path(self.store.project_root, receipt)
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
                temporal = {
                    item.model_dump_json(): item
                    for record in records.values()
                    for item in record.descriptor.temporal_execution
                }
                for _, recipe, _ in prepared.values():
                    if recipe.temporal_execution is not None:
                        temporal[recipe.temporal_execution.model_dump_json()] = (
                            recipe.temporal_execution
                        )
                for _, recipe, _ in prepared.values():
                    selections = dict(recipe.version_selections)
                    population = descriptor.population_authority
                    if population.definition_fingerprint in selections:
                        descriptor = replace(
                            descriptor,
                            population_authority=replace(
                                population,
                                version_selection=selections[population.definition_fingerprint],
                            ),
                        )
                self._event("temporal_authority")
                descriptor = replace(
                    descriptor, temporal_execution=tuple(temporal[key] for key in sorted(temporal))
                )
                validate_sampling_state(
                    self.store.project_root, sampling_state_read(descriptor), object_bindings
                )
                findings: tuple[Finding, ...] = ()
                if dataset.kind == "lifecycle" or (
                    dataset.kind == "population"
                    and isinstance(lifecycle_summary, LifecycleSelectionEvidence)
                ):
                    if (
                        isinstance(lifecycle_summary, LifecycleReducerEvidence)
                        and physical.local_steps
                    ):
                        from marivo.analysis.materialization.lifecycle_reducer_publication import (
                            summary_from_batches as lifecycle_batch_summary,
                        )
                        from marivo.analysis.materialization.reads import payload_batches

                        lifecycle_summary = lifecycle_batch_summary(
                            payload_batches(
                                self.store.project_root,
                                descriptor.storage_receipt,
                                policy=_READ_POLICY,
                                bindings=object_bindings,
                                row=descriptor.row_contract,
                                rows=descriptor.row_set_contract,
                                audit=True,
                            ),
                            lifecycle_summary,
                        )
                    if isinstance(lifecycle_summary, LifecycleSelectionEvidence):
                        lifecycle_summary = replace(
                            lifecycle_summary, row_count=storage.primary_receipt.realized_row_count
                        )
                    from marivo.analysis.materialization.lifecycle_codec import (
                        validate_descriptor as validate_lifecycle,
                    )

                    descriptor = replace(descriptor, lifecycle_evidence=lifecycle_summary)
                    validate_lifecycle(descriptor)
                if dataset.kind == "event":
                    from marivo.analysis.materialization.event_publication import bind_event_summary

                    if event_summary is None:
                        raise _error("output_validation", run.run_ref)
                    if physical.local_steps and isinstance(
                        dataset.row_contract.family_semantics,
                        (EventFunnelSemantics, EventTimeToEventSemantics),
                    ):
                        from marivo.analysis.materialization.event_reducer_publication import (
                            summary_from_batches,
                        )
                        from marivo.analysis.materialization.reads import payload_batches

                        event_summary = summary_from_batches(
                            dataset.row_contract.family_semantics,
                            payload_batches(
                                self.store.project_root,
                                descriptor.storage_receipt,
                                policy=_READ_POLICY,
                                bindings=object_bindings,
                                row=descriptor.row_contract,
                                rows=descriptor.row_set_contract,
                                audit=True,
                            ),
                            event_summary.coverage,
                        )
                    descriptor = bind_event_summary(descriptor, event_summary)
                elif dataset.kind == "population" and selection_summary is not None:
                    from marivo.analysis.materialization.event_reducer_publication import (
                        bind_selection_summary,
                    )

                    descriptor = bind_selection_summary(
                        descriptor,
                        replace(
                            selection_summary, row_count=storage.primary_receipt.realized_row_count
                        ),
                    )
                elif dataset.kind == "candidate":
                    from marivo.analysis.materialization.candidate_publication import (
                        build_candidate_publication,
                    )
                    from marivo.analysis.materialization.reads import payload_batches

                    if candidate_summary is None:
                        raise _error("output_validation", run.run_ref)
                    descriptor, findings = build_candidate_publication(
                        descriptor,
                        None
                        if isinstance(
                            candidate_summary.evaluation, EntityCandidateEvaluationSummary
                        )
                        or any(f.role_id == "entity_identity" for f in dataset.schema.columns)
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
                        definition=candidate_summary.definition,
                        evaluation=candidate_summary.evaluation,
                    )
                elif dataset.kind == "forecast":
                    from marivo.analysis.materialization.forecast_publication import (
                        build_forecast_publication,
                    )
                    from marivo.analysis.materialization.reads import payload_batches

                    descriptor, findings = build_forecast_publication(
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
                        training=forecast_summary,
                    )
                elif dataset.kind == "association":
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
                elif dataset.row_contract.family_semantics.kind in (
                    "delta/funnel@v1",
                    "attribution/funnel-loss-rate@v1",
                ):
                    from marivo.analysis.materialization.event_comparison_publication import (
                        build_publication,
                    )
                    from marivo.analysis.materialization.reads import payload_batches

                    descriptor, findings = build_publication(
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
                    backend.disconnect()
                    backend = None
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
                if isinstance(exc, MaterializationError) and exc.run_ref is None:
                    exc.run_ref = run.run_ref
                if backend is not None:
                    try:
                        backend.disconnect()
                    except BaseException:
                        self._event("close_failed")
                # Retain only owner-safe error facts, never a datasource exception chain.
                safe = exc if isinstance(exc, MaterializationError) else _error(phase, run.run_ref)
                try:
                    recovered = self._resolve_outcome(
                        run, safe, run_failure_phase(safe.stage, phase), object_bindings
                    )
                    if recovered is not None:
                        return recovered
                except BaseException:
                    # Recovery retains unresolved obligations; preserve the original failure.
                    pass
                raise

    @contextmanager
    def _prepared_source(
        self,
        run_ref: str,
        source_step: SourceStep,
        all_records: Mapping[str, ArtifactRecord],
        sampling: list[SamplingRealization],
        validations: list[tuple[str, int]],
        sampling_by_root: dict[int, SamplingRealization],
    ) -> Iterator[tuple[ExecutionAdapter, CompiledDataset, dict[str, ir.Table]]]:
        source_dataset: Dataset = source_step.dataset
        if source_step.operation == "correlation":
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
                candidate = _build_backend_from_effective(
                    datasource, effective, read_only=True
                ).backend

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
            read_time = None
            if isinstance(source_step.binding, SourceBinding):
                self._event("source_timezone")
                profile = require_profile_for_backend_type(selected.backend)
                if profile.timezone_probe_sql is not None:
                    self._record_statement("source_timezone", profile.timezone_probe_sql)
                read_time = backend.timezone()
            backend.initialize()
            backend.prepare_dataset(source_step.dataset)
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
            event_coverages: dict[str, EventCoverageResolution] = {
                reference: record.descriptor.event_evidence.coverage
                for reference, record in records.items()
                if record.descriptor.event_evidence is not None
            }
            event_coverages.update(
                {
                    reference: record.descriptor.lifecycle_evidence.coverage
                    for reference, record in records.items()
                    if record.descriptor.lifecycle_evidence is not None
                }
            )
            if isinstance(source_dataset, LogicalDataset):
                from marivo.analysis.datasets.descriptors import _canonical_digest

                for event_root in logical_roots(source_dataset):
                    if isinstance(event_root.payload, (EventPayload, LifecyclePayload)):
                        event_coverages[event_root.definition_fingerprint] = (
                            backend.resolve_coverage(
                                event_root.payload.definition,
                                require_source_origin=isinstance(
                                    event_root.payload, LifecyclePayload
                                ),
                                provider=self.event_coverage_provider,
                                source_binding_fingerprint=_canonical_digest(
                                    tuple(
                                        capture.identity_payload()
                                        for capture in event_root.payload.captures
                                    )
                                ),
                                execution_domain_id=engine_domain(source_step.binding),
                            )
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
                    table = attach_parquet_scan(
                        backend, self.store.project_root, receipt, bindings=self.object_bindings
                    )
                    table = ordered_relation(
                        table, descriptor.row_contract, descriptor.row_set_contract
                    )
                    retained_scans[reference] = table
                    tables[reference] = table
                    if primary_inputs[reference]:
                        engine_inputs.append((table, receipt, descriptor.row_contract))
                    retained_parts[reference] = self._parquet_parts(
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

                    table = attach_parquet_scan(
                        backend, self.store.project_root, receipt, bindings=self.object_bindings
                    )
                    if primary_inputs[reference]:
                        engine_inputs.append((table, receipt, descriptor.row_contract))
                    entity = normalize_target_entity(
                        source_step.binding.owner.semantic_registry,
                        descriptor.population_authority.entity_ref,
                    )
                    retained_parts = (
                        self._parquet_parts(
                            backend,
                            descriptor,
                            source_dataset,
                            input_dataset=next(
                                value
                                for value in artifact_inputs(source_dataset)
                                if value.state.artifact_ref.ref == reference
                            ),
                        )
                        if descriptor.row_contract.shape_id.family_id in ("metric", "lifecycle")
                        else {}
                    )
                    scans[reference] = CompiledArtifactScan(
                        table, entity, tuple(retained_parts.items())
                    )
                if not isinstance(source_dataset, LogicalDataset):
                    raise _error("implementation_registration", run_ref)
                if read_time is None:
                    raise _error("authority_resolution", run_ref)
                recipe = compile_dataset(
                    source_dataset,
                    tables,
                    scans=scans,
                    source_owner=source_step.binding.owner,
                    read_timezone=read_time.engine_timezone_name,
                    read_timezone_source=read_time.read_tz_resolution,
                    event_coverages=event_coverages,
                )
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
                    preparations=(*recipe.preparations, *pair_checks)
                    if recipe.preparations
                    else (),
                )
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
            sample_statements = {
                preparation.relation_name: backend.statement(
                    sample_statement(backend, preparation),
                    role="sampling_fence",
                    inputs=(backend.prepare(preparation.expression),),
                )
                for preparation in preparations
                if isinstance(preparation, CompiledSampleFence)
            }
            from marivo.analysis.materialization.parquet_scan import validate_parquet_relation

            for table, receipt, row in engine_inputs:
                validate_parquet_relation(backend, table, receipt, row, self._record_statement)
            for entity in entities:
                if (
                    isinstance(entity.source, TableSourceIR)
                    and entity.ref.path not in checked_schemas
                ):
                    self._validate_source_schema(backend, entity)
            for fence in fences:
                for name in (fence.reader_name, fence.relation_name):
                    reserve_preparation(name)
                self._event("source_statement")
                source_table = read_json_source(
                    _ReservedJsonReader(backend, fence.reader_name, self._record_statement),
                    fence.source,
                    source_params=fence.parameters,
                )
                fence_statement = backend.table_statement(fence.relation_name, source_table)
                self._record_statement("source_fence", fence_statement.sql)
                backend.submit(fence_statement)
                self.statistics.source_fences += 1
            for validation in preparations:
                if isinstance(validation, CompiledRelationFence):
                    reserve_preparation(validation.relation_name)
                    self._event("source_statement")
                    fence_statement = relation_statements[validation.relation_name]
                    self._record_statement("source_fence", fence_statement.sql)
                    backend.submit(fence_statement)
                    self.statistics.source_fences += 1
                    continue
                if isinstance(validation, CompiledSampleFence):
                    reserve_preparation(validation.relation_name)
                    self._event("sampling_reserved")
                    sampling.append(
                        execute_sample(
                            backend,
                            validation,
                            statement=sample_statements[validation.relation_name],
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
                self._record_statement("validation_batch", validation.statement.sql)
                validations.extend(execute_batch(backend, validation, run_ref=run_ref))
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

    def _write_output(
        self,
        dataset: LogicalDataset,
        batches: Iterable[pa.RecordBatch],
        run_ref: str,
        *,
        parts: tuple[PartWriteSpec, ...] = (),
        independent_parts: tuple[IndependentPartWrite, ...] = (),
        sampling: tuple[SamplingRealization, ...] = (),
        source_key_validation: bool,
        target: MaterializationTarget,
        object_bindings: tuple[ObjectBinding, ...],
    ) -> tuple[str, DatasetWriteResult[StorageReceipt]]:
        nonce = uuid4().hex
        artifact_ref = "artifact_" + nonce
        staging, final, _ = reserve_output(
            self.store,
            run_ref=run_ref,
            session_ref=self.session_ref,
            artifact_ref=artifact_ref,
            nonce=nonce,
        )
        self._event("output_reserved")
        storage = write_local_dataset(
            project_root=self.store.project_root,
            staging_path=staging,
            final_path=final,
            batches=batches,
            row_contract=dataset.row_contract,
            row_set_contract=dataset.row_set_contract,
            parts=parts,
            independent_parts=independent_parts,
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
                event=self._event,
            )
        return artifact_ref, storage

    def _validate_target(
        self,
        source: SourceStep | None,
        physical: PhysicalStageGraph,
        target: MaterializationTarget,
        object_bindings: tuple[ObjectBinding, ...],
    ) -> None:
        if not isinstance(target, (LocalTarget, ObjectTarget)):
            selection_error("one supported configured target", "unknown target")
        if any(type(value) is not int or value <= 0 for value in (target.policy.row_group_rows,)):
            selection_error("positive row-group size", "invalid writer settings")
        if isinstance(target, ObjectTarget):
            from marivo.analysis.materialization.object_storage import validate_target

            validate_target(object_access(object_bindings, target.object_store_ref))

    def _artifact_batches(
        self, descriptor: ArtifactDescriptor, object_bindings: tuple[ObjectBinding, ...]
    ) -> Iterator[pa.RecordBatch]:
        from marivo.analysis.materialization.reads import payload_batches

        return payload_batches(
            self.store.project_root,
            descriptor.storage_receipt,
            policy=_READ_POLICY,
            bindings=object_bindings,
            row=descriptor.row_contract,
            rows=descriptor.row_set_contract,
        )

    def _attribution_source_summary(
        self, backend: ExecutionAdapter, table: ir.Table, row: DatasetRowContract
    ) -> AttributionSourceSummary:
        """Reduce complete Attribution proof inside its engine; return only global facts."""
        from marivo.analysis.operators.attribution_contracts import AttributionSemantics

        semantics = row.family_semantics
        if not isinstance(semantics, AttributionSemantics):
            raise _error("output_validation")
        names = {field.field_id: field.name for field in row.schema.columns}

        sql = attribution_summary_sql(
            backend.compile(table), names, semantics.scope_field_ids, row.key_field_ids
        )
        self._record_statement("attribution.source_summary", sql)
        self._event("source_statement")
        result: object = backend.submit(
            backend.statement(
                sql, role="attribution.source_summary", inputs=(backend.prepare(table),)
            )
        ).fetchone()
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

    def _parquet_parts(
        self,
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
                bindings=self.object_bindings,
                verify_schema=True,
            )
            self._record_statement("engine_check.part_schema", backend.compile(table.limit(0)))
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
                    bindings=self.object_bindings,
                )
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
                if str(descriptor.row_contract.shape_id) == "lifecycle/history@v1":
                    from marivo.analysis.materialization.lifecycle_publication import (
                        validate_relation,
                    )

                    validate_relation(
                        backend,
                        table,
                        descriptor.row_contract,
                        part.role,
                        self._record_statement,
                    )
            self._record_statement("engine_check.part_count", backend.compile(table.count()))
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
                self._record_statement("engine_check.part_support", backend.compile(check))
                failures: object = backend.read_scalar(
                    backend.prepare(check, role="engine_check.part_support")
                )
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
        object_bindings: tuple[ObjectBinding, ...],
        *,
        input_dataset: MaterializedDataset | None = None,
    ) -> tuple[tuple[LocalPartInput, ...], tuple[Iterable[pa.RecordBatch], ...]]:
        from marivo.analysis.materialization.reads import part_schema, read_part_batches
        from marivo.analysis.materialization.retained import (
            checked_component_batches,
            component_schema,
            selected_parts,
        )

        selected = (
            *selected_parts(descriptor, dataset, input_dataset=input_dataset),
            *(
                part
                for part in descriptor.retained_parts
                if part.role == "population_sampling_state"
            ),
        )
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
            if part.contract_id
            in (
                "metric.sufficient_components",
                "delta.sufficient_components",
                "event_funnel.additive_components",
            )
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
            call: (
                RowCall
                | FunnelCompareSpec
                | FunnelAttributeSpec
                | CompareSpecV1
                | AttributeSpecV1
                | CorrelateSpecV1
                | ForecastSpecV1
                | CandidateSpecV1
                | DriverCandidateSpecV1
            )
            if isinstance(
                payload,
                (
                    ComparePayload,
                    FunnelComparePayload,
                    FunnelAttributePayload,
                    AttributePayload,
                    CorrelatePayload,
                    ForecastPayload,
                    CandidatePayload,
                    DriverCandidatePayload,
                ),
            ):
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
        request = LocalGraphRequest(boundaries, tuple(stages), physical.primary_output)
        self._event("local_execution_started")
        result = execute_local(request, streams)
        self.statistics.local_handoffs = result.handoffs
        self._event("local_execution_completed")
        return result

    def _resolve_outcome(
        self,
        run: RunRecord,
        error: MaterializationError,
        phase: str,
        object_bindings: tuple[ObjectBinding, ...],
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
        self, backend: ExecutionAdapter, entity: TargetEntityContract
    ) -> ibis.Schema:
        source = entity.source
        if not isinstance(source, TableSourceIR):
            raise _error("source_binding", self.last_run_ref)
        database = source.database
        namespace = database if isinstance(database, str) else (database[-1] if database else None)
        catalog = database[0] if isinstance(database, tuple) and len(database) == 2 else None
        self._event("source_statement")
        self.statistics.validation_queries += 1
        actual = backend.get_schema(
            source.table, database=namespace, catalog=catalog, record=self._record_statement
        )
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
            timestamp_match = (
                isinstance(declared, dt.Timestamp)
                and isinstance(physical_type, dt.Timestamp)
                and declared.timezone == physical_type.timezone
                and declared.scale is None
                and physical_type.scale == 6
            )
            if physical_type != declared and not decimal_match and not timestamp_match:
                raise MaterializationError(
                    expected="physical source types matching declarations after nullability normalization; generic decimal accepts precision up to 38, and an unspecified timestamp scale accepts microseconds with the same timezone",
                    received="the governed source schema differs from its declaration",
                    repair="Correct the declaration or physical schema before executing this Dataset.",
                    stage="output_validation",
                    run_ref=self.last_run_ref,
                )
        return actual

    def _batches(
        self, backend: ExecutionAdapter, expression: ir.Table, batch_rows: int
    ) -> Iterator[pa.RecordBatch]:
        self._event("source_statement")
        self.statistics.primary_queries += 1
        reader = backend.batches(
            expression, role="primary", chunk_size=batch_rows, record=self._record_statement
        )
        seen = False
        try:
            for batch in reader:
                self._event("transfer")
                seen = True
                self.statistics.transferred_rows += batch.num_rows
                self.statistics.transferred_bytes += batch.nbytes
                yield batch
            if not seen:
                yield pa.RecordBatch.from_arrays(
                    [pa.array([], type=field.type) for field in reader.schema], schema=reader.schema
                )
        finally:
            failed = sys.exc_info()[0] is not None
            try:
                reader.close()
            except BaseException:
                if not failed:
                    raise


def producer_contract_versions(operator_id: str) -> tuple[tuple[str, str], ...]:
    from marivo.analysis.observation.contracts import producer_contract

    return producer_contract(operator_id).versions
