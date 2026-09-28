"""Private first-route execution from pure definition to committed Dataset authority."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pandas as pd

from marivo._temporal import PeriodCalendarSnapshotV1
from marivo.analysis.datasets.base import LogicalDataset, MaterializedDataset
from marivo.analysis.domains.completeness import (
    EventCoverageProvider,
)
from marivo.analysis.domains.event import LogicalEventDataset, MaterializedEventDataset
from marivo.analysis.domains.lifecycle import (
    LogicalLifecycleDataset,
    MaterializedLifecycleDataset,
)
from marivo.analysis.evidence._dataset_types import (
    ArtifactDigest,
    ArtifactRevalidation,
    Finding,
    FindingPage,
)
from marivo.analysis.materialization.contracts import (
    ArtifactRecord,
)
from marivo.analysis.materialization.errors import (
    _execution_error as _error,
)
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.materialization.reconciliation import reconcile_session
from marivo.analysis.materialization.storage import (
    ReadPolicy,
    StoragePolicy,
)
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.submissions import Submission
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.observation.contracts import (
    ObservationSourceContext,
)
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
from marivo.analysis.operators.attribution import (
    LogicalAttributionDataset,
    MaterializedAttributionDataset,
)
from marivo.analysis.operators.candidate_dataset import (
    LogicalCandidateDataset,
    MaterializedCandidateDataset,
)
from marivo.analysis.operators.delta import LogicalDeltaDataset, MaterializedDeltaDataset
from marivo.analysis.operators.forecast_dataset import (
    LogicalForecastDataset,
    MaterializedForecastDataset,
)
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
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.validator import Registry

if TYPE_CHECKING:
    from marivo.analysis.compiler.graph_plan import RouteChoice
    from marivo.analysis.core.graph import Node
    from marivo.analysis.materialization.dsl_j1_artifact import J1Node
    from marivo.analysis.materialization.dsl_j1_runtime import J1SourceFactory
    from marivo.analysis.materialization.graph_execution import PreparedGraph
    from marivo.analysis.observation.dsl_j1_dataset import MaterializedJ1Dataset

_PREVIEW_MAX_OUTPUT_BYTES = 8192
_READ_POLICY = ReadPolicy()
_LOCAL_STORAGE_POLICY = StoragePolicy()


@dataclass(slots=True)
class ExecutionStatistics:
    """Ephemeral per-action diagnostics, never a publication authority."""

    j1_source_evaluations: int = 0
    j1_fixed_cache_hits: int = 0
    primary_queries: int = 0
    validation_queries: int = 0
    source_fences: int = 0
    transferred_rows: int = 0
    transferred_bytes: int = 0
    events: dict[str, int] = field(default_factory=dict)
    statements: list[tuple[str, str]] = field(default_factory=list)
    submissions: list[Submission] = field(default_factory=list)
    local_handoffs: tuple[tuple[int, int], ...] = ()


class DatasetRuntime:
    """Private assembly owner; construction, execution and reads have distinct boundaries."""

    def __init__(
        self,
        store: SessionStore,
        session_ref: str,
        *,
        event: Callable[[str], None] | None = None,
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
            from datetime import timezone

            from marivo.datasource.timezone import parse_timezone

            try:
                timezone_name, resolved_zone = parse_timezone(report_timezone)
            except (ValueError, KeyError):
                zoneinfo_from_name(report_timezone)
                raise
            timezone_resolution = "fixed_offset" if isinstance(resolved_zone, timezone) else "iana"
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
                        event_coverage_provider=event_coverage_provider,
                    )
            # A competing creator won this name. Its guard must be acquired only
            # after the unused candidate guard has been released.
        runtime = cls(
            store,
            record.session_ref,
            event=event,
            event_coverage_provider=event_coverage_provider,
        )
        with session_writer_guard(
            store.layout.lock_path(record.session_ref), session_ref=record.session_ref
        ):
            resolved = store.session_by_name(name)
            if resolved is None or resolved.session_ref != record.session_ref:
                raise _error("authority_resolution")
            if report_timezone is not None and resolved.report_timezone_name != timezone_name:
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
        event_coverage_provider: EventCoverageProvider | None = None,
    ) -> DatasetRuntime:
        if not MaterializationLayout(project_root).store_db.is_file():
            raise _error("authority_resolution")
        return cls(
            SessionStore.open_existing(project_root),
            session_ref,
            event=event,
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
        """Read existing v6 Session history without creating or activating a Session."""
        _lazy_runtime_reads.page_after(limit, cursor, operation="recent")
        return _lazy_history.recent(
            SessionStore.open_existing(project_root), limit=limit, cursor=cursor
        )

    @staticmethod
    def inspect(
        project_root: Path, name: str, *, run_limit: int = 5, run_cursor: str | None = None
    ) -> SessionInspection:
        """Read a named existing v6 Session and one bounded Run page."""
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

    def _observe_submission(self, receipt: Submission) -> None:
        self.statistics.submissions.append(receipt)
        self.statistics.statements.append((receipt.role, receipt.sql))
        self._event(receipt.domain + "_statement")
        if receipt.role == "primary":
            self.statistics.primary_queries += 1
        if receipt.role.startswith("engine_check.") or receipt.role in {
            "validation_batch",
        }:
            self.statistics.validation_queries += 1

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
        from marivo.analysis.materialization import dataset_presentation

        return dataset_presentation.revalidate(self, reference)

    def _recover(self, record: ArtifactRecord) -> MaterializedDataset:
        from marivo.analysis.materialization import dataset_presentation

        return dataset_presentation.recover(self, record)

    def _selected(self, dataset: MaterializedDataset) -> ArtifactRecord:
        from marivo.analysis.materialization import dataset_presentation

        return dataset_presentation.selected(self, dataset)

    def _prepare_graph(self, root: Node, routes: tuple[RouteChoice, ...]) -> PreparedGraph:
        """Prepare the private graph schedule under this Session's identity."""
        from marivo.analysis.materialization.graph_execution import prepare_graph

        return prepare_graph(root, session_ref=self.session_ref, routes=routes)

    def execute_j1(
        self,
        node: J1Node,
        *,
        source: J1SourceFactory | None = None,
        input_node: J1Node | None = None,
        input_artifact_ref: str | None = None,
        input_nodes: tuple[J1Node, J1Node] | None = None,
        input_artifact_refs: tuple[str, str] | None = None,
        source_route: Literal["automatic", "python", "source_numeric"] = "automatic",
        public_snapshot: str | None = None,
    ) -> MaterializedJ1Dataset:
        """Execute a private J1 node through this Session Runtime.

        Args:
            node: The same constructed J1 node may be evaluated again.
            source: Factory opening one admitted DuckDB/Ibis source context.
            input_node: Exact predecessor definition for local continuation.
            input_artifact_ref: Saved predecessor Artifact selected for local work.
            input_nodes: Ordered current and baseline definitions for private comparison.
            input_artifact_refs: Exact ordered Artifacts for private comparison.
            source_route: Private J4 source implementation choice for validation.
        Returns:
            The exact committed J1 Artifact as a Materialized Dataset.
        Example:
            ``result = runtime.execute_j1(observed, source=open_source)``.
        Constraints:
            This internal entry is not a public Analysis DSL method. Supply
            either a source factory or both fixed-input arguments.
        """
        from marivo.analysis.materialization.dsl_j1_runtime import execute_j1

        return execute_j1(
            self,
            node,
            source=source,
            input_node=input_node,
            input_artifact_ref=input_artifact_ref,
            input_nodes=input_nodes,
            input_artifact_refs=input_artifact_refs,
            source_route=source_route,
            public_snapshot=public_snapshot,
        )

    def show(self, dataset: MaterializedDataset, *, max_output_bytes: int | None = None) -> None:
        from marivo.analysis.materialization import dataset_presentation

        dataset_presentation.show(
            self, dataset, max_output_bytes=max_output_bytes, policy=_READ_POLICY
        )

    def to_pandas(self, dataset: MaterializedDataset) -> pd.DataFrame:
        from marivo.analysis.materialization import dataset_presentation

        return dataset_presentation.to_pandas(self, dataset, policy=_READ_POLICY)

    def evidence_digest(self, dataset: MaterializedDataset) -> ArtifactDigest:
        from marivo.analysis.materialization import dataset_presentation

        return dataset_presentation.evidence_digest(self, dataset)

    def findings(
        self, dataset: MaterializedDataset, *, limit: int, cursor: str | None
    ) -> FindingPage:
        from marivo.analysis.materialization import dataset_presentation

        return dataset_presentation.findings(self, dataset, limit=limit, cursor=cursor)

    def finding(self, dataset: MaterializedDataset, finding_id: str) -> Finding:
        from marivo.analysis.materialization import dataset_presentation

        return dataset_presentation.finding(self, dataset, finding_id)

    def _validate_reader_owner(self, dataset: MaterializedDataset) -> None:
        from marivo.analysis.materialization import dataset_presentation

        dataset_presentation.validate_reader_owner(self, dataset)

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
        from marivo.analysis.materialization import dataset_execution

        return dataset_execution.execute(self, dataset)
