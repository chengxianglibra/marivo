"""Executable private receivers for native disclosure, never public Session dispatch.

Semantic loading and backend selection remain the eventual public assembly's job.
This receiver accepts the already prepared private source/runtime owners.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from marivo.analysis.datasets.base import MaterializedDataset
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.evidence._dataset_types import ArtifactRevalidation
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import SessionRecord
from marivo.analysis.materialization.reconciliation import reconcile_session
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session._lazy_read_model import (
    GraphDirection,
    RunLifecycle,
    RunPage,
    RunRecord,
    SessionGraph,
    SessionInspection,
    SessionSummaryPage,
)
from marivo.analysis.session._lazy_sources import LazySources
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry


def _invalid(expected: str, received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected=expected,
        received=received,
        repair="Inspect existing v3 Session/Run identities and use the owning guarded Runtime operation.",
        location="session.private_disclosure",
    )


@dataclass(frozen=True, slots=True, repr=False)
class PreparedSession(LazySources):
    """Private composition of the existing construction and retained-read receivers."""

    _runtime: DatasetRuntime

    @classmethod
    def from_runtime(
        cls, runtime: DatasetRuntime, registry: Registry, sidecar: CompiledExpressionSidecar
    ) -> PreparedSession:
        source = runtime.sources(semantic_registry=registry, sidecar=sidecar)
        return cls(source._owner, source._registry, runtime)

    @property
    def id(self) -> str:
        return self._runtime.session_ref

    @property
    def name(self) -> str:
        return self._record().name

    @property
    def question(self) -> str | None:
        return self._record().question

    def _record(self) -> SessionRecord:
        record = self._runtime.store.session(self.id)
        if record is None:
            raise _invalid("existing Session", "missing Session")
        return record

    def artifact(self, reference: str | ArtifactRef) -> MaterializedDataset:
        """Recover the exact committed Dataset using the existing Runtime owner."""
        return self._runtime.artifact(reference)

    def runs(
        self, *, status: RunLifecycle | None = None, limit: int = 20, cursor: str | None = None
    ) -> RunPage:
        """Read one bounded page without activation or reconciliation."""
        return self._runtime.runs(status=status, limit=limit, cursor=cursor)

    def get_run(self, run_id: str) -> RunRecord:
        """Read one exact same-Session Run."""
        return self._runtime.get_run(run_id)

    def graph(
        self,
        *,
        artifact_ref: str | ArtifactRef | None = None,
        direction: GraphDirection = "ancestors",
        max_nodes: int = 100,
    ) -> SessionGraph:
        """Read the bounded committed topology and consumed foreign boundaries."""
        return self._runtime.graph(
            artifact_ref=artifact_ref, direction=direction, max_nodes=max_nodes
        )

    def revalidate(self, reference: str | ArtifactRef) -> ArtifactRevalidation:
        """Inspect committed integrity and authority, without checking source freshness."""
        return self._runtime.revalidate(reference)

    def render(self, *, max_output_bytes: int | None = 8192) -> str:
        """Render the existing bounded v3 Session recap."""
        from marivo.analysis.session._lazy_runtime_reads import recap

        return recap(self._runtime.store, self.id).render(max_output_bytes=max_output_bytes)

    def show(self, *, max_output_bytes: int | None = 8192) -> None:
        """Print the existing bounded v3 Session recap."""
        from marivo.analysis.session._lazy_runtime_reads import recap

        recap(self._runtime.store, self.id).show(max_output_bytes=max_output_bytes)

    def __repr__(self) -> str:
        return f"<Session id={self.id}; use .show()>"


@dataclass(frozen=True, slots=True, repr=False)
class PreparedSessionNamespace:
    """Private explicit project/source binding for executable disclosure examples."""

    project_root: Path
    semantic_registry: Registry
    sidecar: CompiledExpressionSidecar

    def _wrap(self, runtime: DatasetRuntime) -> PreparedSession:
        return PreparedSession.from_runtime(runtime, self.semantic_registry, self.sidecar)

    def get_or_create(self, name: str, question: str | None = None) -> PreparedSession:
        """Create or recover the named private v3 Session through its existing owner."""
        runtime = DatasetRuntime.create(self.project_root, name)
        if question is not None:
            with session_writer_guard(
                runtime.store.layout.lock_path(runtime.session_ref), session_ref=runtime.session_ref
            ):
                runtime.store.activate(runtime.session_ref, question=question)
        return self._wrap(runtime)

    def current(self) -> PreparedSession | None:
        """Read the current existing v3 Session without creating state or recovering work."""
        from marivo.analysis.materialization.layout import MaterializationLayout

        if not MaterializationLayout(self.project_root).store_db.is_file():
            return None
        store = SessionStore.open_existing(self.project_root)
        record = store.current()
        return (
            None
            if record is None
            else self._wrap(DatasetRuntime.open(self.project_root, record.session_ref))
        )

    def resume(self, identity: str, *, by: Literal["name", "id"] | None = None) -> PreparedSession:
        """Resolve an existing identity and delegate guarded recovery to DatasetRuntime."""
        if by not in (None, "name", "id"):
            raise _invalid("by=None, name or id", "unsupported identity selector")
        store = SessionStore.open_existing(self.project_root)
        named = store.session_by_name(identity) if by != "id" else None
        keyed = store.session(identity) if by != "name" else None
        if named is not None and keyed is not None and named.session_ref != keyed.session_ref:
            raise _invalid("unambiguous identity or explicit by", "name/id collision")
        record = named or keyed
        if record is None:
            raise _invalid("existing Session identity", "unknown identity")
        return self._wrap(DatasetRuntime.create(self.project_root, record.name))

    def recent(self, *, limit: int = 20, cursor: str | None = None) -> SessionSummaryPage:
        """Read existing v3 Session history without activation."""
        return DatasetRuntime.recent(self.project_root, limit=limit, cursor=cursor)

    def inspect(
        self, name: str, *, run_limit: int = 5, run_cursor: str | None = None
    ) -> SessionInspection:
        """Read one existing Session and a bounded Run page without activation."""
        return DatasetRuntime.inspect(
            self.project_root, name, run_limit=run_limit, run_cursor=run_cursor
        )

    def abandon_run(self, *, session_id: str, run_id: str) -> None:
        """Delegate stopped-work recovery under the existing Session writer guard.

        Reconciliation, including every backend terminal/fencing proof, remains
        owned by the Runtime. Caller intent cannot override a committed success.
        """
        store = SessionStore.open_existing(self.project_root)
        with session_writer_guard(store.layout.lock_path(session_id), session_ref=session_id):
            reconcile_session(store, session_id, event=lambda point: None, run_ref=run_id)

    def __repr__(self) -> str:
        return "<private Session namespace; use .recent()>"
