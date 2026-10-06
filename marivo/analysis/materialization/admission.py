"""Private first-route execution from pure definition to committed Dataset authority."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pyarrow as pa

from marivo._temporal import PeriodCalendarSnapshotV1
from marivo.analysis.core.time_authority import ReportTimeAuthority
from marivo.analysis.evidence._dataset_types import (
    ArtifactRevalidation,
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
    ObservationOwner,
    ObservationSourceContext,
)
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session import _lazy_graph, _lazy_runtime_reads
from marivo.analysis.session._lazy_read_model import (
    GraphDirection,
    RunLifecycle,
    RunPage,
    SessionGraph,
)
from marivo.analysis.session._lazy_read_model import (
    RunRecord as ReadRunRecord,
)
from marivo.analysis.session._lazy_sources import make_source_owner
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.validator import Registry

if TYPE_CHECKING:
    from marivo.analysis.compiler.graph_plan import RouteChoice
    from marivo.analysis.core.graph import Node
    from marivo.analysis.materialization.execution_key import SourceKeyBinding
    from marivo.analysis.materialization.graph_execution import PreparedGraph
    from marivo.analysis.materialization.graph_publication import SourceFactory
    from marivo.analysis.materialization.graph_store import GraphArtifact

_PREVIEW_MAX_OUTPUT_BYTES = 8192
_READ_POLICY = ReadPolicy()
_LOCAL_STORAGE_POLICY = StoragePolicy()


@dataclass(slots=True)
class ExecutionStatistics:
    """Ephemeral per-action diagnostics, never a publication authority."""

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
    ) -> None:
        session_record = store.session(session_ref)
        if session_record is None:
            raise _error("authority_resolution")
        self.report_time = ReportTimeAuthority(
            timezone=session_record.report_timezone_name,
            resolution=session_record.report_timezone_resolution,
        )
        self.store = store
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
                    )
            # A competing creator won this name. Its guard must be acquired only
            # after the unused candidate guard has been released.
        runtime = cls(
            store,
            record.session_ref,
            event=event,
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
    ) -> DatasetRuntime:
        if not MaterializationLayout(project_root).store_db.is_file():
            raise _error("authority_resolution")
        return cls(
            SessionStore.open_existing(project_root),
            session_ref,
            event=event,
        )

    def sources(
        self,
        *,
        semantic_registry: Registry,
        sidecar: CompiledExpressionSidecar,
        catalog: SemanticCatalog | None = None,
        period_calendar_snapshots: tuple[PeriodCalendarSnapshotV1, ...] = (),
    ) -> ObservationOwner:
        owner = make_source_owner(
            semantic_registry=semantic_registry,
            sidecar=sidecar,
            session_id=self.session_ref,
            store_id=self.store.store_id,
            catalog=catalog,
            period_calendar_snapshots=period_calendar_snapshots,
            report_time=self.report_time,
        )
        self._source_context.current = owner
        return owner

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
        from marivo.analysis.materialization.inspection import revalidate

        return revalidate(self.store, reference)

    def _execute_graph(
        self,
        root: Node,
        routes: tuple[RouteChoice, ...],
        *,
        source_bindings: tuple[SourceKeyBinding, ...] = (),
        source_factory: SourceFactory | None = None,
        source_schemas: tuple[pa.Schema, ...] = (),
    ) -> GraphArtifact:
        """Execute the private v7 graph through this existing Runtime owner."""
        from marivo.analysis.materialization.graph_publication import execute

        return execute(
            self,
            root,
            routes,
            source_bindings=source_bindings,
            source_factory=source_factory,
            source_schemas=source_schemas,
        )

    def _prepare_graph(self, root: Node, routes: tuple[RouteChoice, ...]) -> PreparedGraph:
        """Prepare the private graph schedule under this Session's identity."""
        from marivo.analysis.materialization.graph_execution import prepare_graph

        return prepare_graph(root, session_ref=self.session_ref, routes=routes)
