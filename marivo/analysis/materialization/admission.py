"""Private assembly of exact local authority and explicit source configuration."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from marivo._temporal import PeriodCalendarSnapshotV2
from marivo.analysis.core.time_authority import ReportTimeAuthority
from marivo.analysis.materialization.errors import (
    _execution_error as _error,
)
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.observation.contracts import ObservationOwner
from marivo.analysis.session._lazy_sources import make_source_owner
from marivo.datasource import credentials as cr
from marivo.datasource.runtime import DatasourceConnectionConfig, DatasourceConnectionService
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.validator import Registry


class DatasetRuntime:
    """Assemble local Store services separately from captured source configuration."""

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
        self.local = LocalFrameServices(store, session_ref)
        self.report_time = ReportTimeAuthority(
            timezone=session_record.report_timezone_name,
            resolution=session_record.report_timezone_resolution,
        )
        self._hook = event
        self.connection_config = DatasourceConnectionConfig(
            store.project_root, cr.current_resolver()
        )

    @property
    def store(self) -> SessionStore:
        return self.local.store

    @property
    def session_ref(self) -> str:
        return self.local.session_ref

    def connection_service(self) -> DatasourceConnectionService:
        """Create a source owner bound to this Runtime's captured host resolver."""
        cr.check_binding(self.connection_config.resolver)
        with cr.bind_resolver(self.connection_config.resolver):
            return DatasourceConnectionService(self.store.project_root)

    @classmethod
    def create(
        cls,
        project_root: Path,
        name: str,
        *,
        question: str | None = None,
        timezone: str | None = None,
        domains: tuple[str, ...] | None = None,
        event: Callable[[str], None] | None = None,
    ) -> DatasetRuntime:
        from marivo.analysis.timezone import resolve_system_timezone, resolve_timezone

        zone = resolve_system_timezone() if timezone is None else resolve_timezone(timezone)
        store = SessionStore(project_root)
        candidate_ref = "session_" + uuid4().hex
        with session_writer_guard(store.layout.lock_path(candidate_ref), session_ref=candidate_ref):
            record = store.create_session(
                name,
                session_ref=candidate_ref,
                question=question,
                report_timezone_name=zone.name,
                report_timezone_resolution=zone.kind,
                report_timezone_origin="system" if timezone is None else "explicit",
                domains=domains,
            )
        return cls(store, record.session_ref, event=event)

    @classmethod
    def open(
        cls,
        project_root: Path,
        session_ref: str,
        *,
        event: Callable[[str], None] | None = None,
    ) -> DatasetRuntime:
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
        period_calendar_snapshots: tuple[PeriodCalendarSnapshotV2, ...] = (),
    ) -> ObservationOwner:
        return make_source_owner(
            semantic_registry=semantic_registry,
            sidecar=sidecar,
            session_id=self.session_ref,
            store_id=self.store.store_id,
            catalog=catalog,
            period_calendar_snapshots=period_calendar_snapshots,
            report_time=self.report_time,
        )

    def _event(self, point: str) -> None:
        if self._hook is not None:
            self._hook(point)
