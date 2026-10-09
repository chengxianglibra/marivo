"""Session-owned source construction and committed Dataset reads."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, overload

from marivo._temporal import BeforeEndBoundary, TimeScope
from marivo.analysis.materialization.contracts import SessionRecord
from marivo.analysis.observation.contracts import ObservationOwner
from marivo.analysis.observation.source_bindings import SourceBindingMap
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session._lazy_read_model import (
    GraphDirection,
    RunLifecycle,
    RunPage,
    RunRecord,
    SessionGraph,
)
from marivo.refs import BusinessOrderKind, EntityKind, Ref
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.event import ParticipantRoleHandle

if TYPE_CHECKING:
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.public_dsl import (
        LogicalAnalysisDomain,
        LogicalAnchorDomain,
        LogicalFixedAnalysisDomain,
        LogicalJourneyResult,
        MaterializedAnalysisDomain,
        MaterializedJourneyResult,
        PublicMaterialized,
    )
    from marivo.analysis.session._history_lifecycle import HistoryLifecycle
    from marivo.analysis.session._journey_events import JourneyEvents


class Session:
    """One named Session for logical sources and committed Dataset reads.

    Acquire with ``mv.session.get_or_create(name)`` or ``mv.session.resume(identity)``.
    Source methods load authored semantics; members may perform schema-only R1 preflight.
    Retained reads do not load current semantics or replay an origin graph.
    """

    __slots__ = ("_catalog_value", "_runtime", "_sources_value")

    def __new__(cls) -> Session:
        raise TypeError("Use mv.session.get_or_create(name) or mv.session.resume(identity).")

    @classmethod
    def _from_runtime(cls, runtime: DatasetRuntime) -> Session:
        result = object.__new__(cls)
        result._runtime = runtime
        result._sources_value = None
        result._catalog_value = None
        return result

    _runtime: DatasetRuntime
    _sources_value: ObservationOwner | None
    _catalog_value: SemanticCatalog | None

    def _sources(self) -> ObservationOwner:
        if self._sources_value is None:
            from marivo.datasource import credentials as cr
            from marivo.semantic.catalog import load

            config = self._runtime.connection_config
            cr.check_binding(config.resolver)
            with cr.bind_resolver(config.resolver):
                catalog = load(workspace_dir=self.project_root, domains=self._record().domains)
            from marivo._temporal import PeriodCalendarSnapshotV1
            from marivo.refs import SemanticKind, _create_ref
            from marivo.semantic.catalog import PeriodCalendarEntry

            snapshots: list[PeriodCalendarSnapshotV1] = []
            for identity in sorted(catalog._state.registry.period_calendars):
                calendar = catalog.require(_create_ref(SemanticKind.PERIOD_CALENDAR, identity))
                assert isinstance(calendar, PeriodCalendarEntry)
                status, snapshot = calendar._snapshot_with_status()
                if status == "current" and snapshot is not None:
                    snapshots.append(snapshot)
            self._sources_value = self._runtime.sources(
                semantic_registry=catalog._state.registry,
                sidecar=catalog._state.sidecar,
                catalog=catalog,
                period_calendar_snapshots=tuple(snapshots),
            )
            self._catalog_value = catalog
        return self._sources_value

    def __dir__(self) -> list[str]:
        return sorted(name for name in type(self).__dict__ if not name.startswith("_"))

    def _record(self) -> SessionRecord:
        from marivo.analysis.materialization.contracts import invalid

        record = self._runtime.store.session(self.id)
        if record is None:
            raise invalid("Session is absent from its owning Store")
        return record

    @property
    def id(self) -> str:
        """Return the immutable owning Session identity."""
        return self._runtime.session_ref

    @property
    def domains(self) -> tuple[str, ...] | None:
        """Return the saved semantic domain scope; None selects all domains."""
        return self._record().domains

    @property
    def name(self) -> str:
        """Return the persisted Session name."""
        return self._record().name

    @property
    def question(self) -> str | None:
        """Return the current persisted investigation question."""
        return self._record().question

    @property
    def project_root(self) -> Path:
        """Return the owning project directory."""
        return self._runtime.store.project_root

    @property
    def created_at(self) -> datetime:
        """Return the persisted creation timestamp."""
        from marivo.analysis.materialization.contracts import parse_timestamp

        return parse_timestamp(self._record().created_at)

    @property
    def updated_at(self) -> datetime:
        """Return the persisted last-update timestamp."""
        from marivo.analysis.materialization.contracts import parse_timestamp

        return parse_timestamp(self._record().updated_at)

    @property
    def report_tz_name(self) -> str:
        """Return the persisted report timezone name."""
        return self._record().report_timezone_name

    @property
    def report_tz_resolution(self) -> str:
        """Return the persisted timezone resolution kind."""
        return self._record().report_timezone_resolution

    @property
    def report_tz(self) -> tzinfo:
        """Return the persisted report timezone."""
        from marivo.analysis.timezone import restore_timezone

        return restore_timezone(self.report_tz_name, self.report_tz_resolution)

    @property
    def catalog(self) -> SemanticCatalog:
        """Load and return this Session's authored semantic catalog without a query."""
        self._sources()
        assert self._catalog_value is not None
        return self._catalog_value

    @property
    def events(self) -> JourneyEvents:
        """Return Event source construction bound to this Session."""
        from marivo.analysis.session._journey_events import JourneyEvents

        return JourneyEvents(self._sources())

    @property
    def lifecycle(self) -> HistoryLifecycle:
        """Return canonical Lifecycle source construction bound to this Session."""
        from marivo.analysis.session._history_lifecycle import HistoryLifecycle

        return HistoryLifecycle(self._sources())

    @overload
    def anchors(
        self,
        source: ParticipantRoleHandle,
        *,
        population: LogicalAnalysisDomain,
        during: TimeScope,
        business_order: Ref[BusinessOrderKind] | None = None,
    ) -> LogicalAnchorDomain: ...

    @overload
    def anchors(
        self,
        source: LogicalJourneyResult | MaterializedJourneyResult,
        *,
        population: LogicalAnalysisDomain | LogicalFixedAnalysisDomain | MaterializedAnalysisDomain,
        during: TimeScope,
    ) -> LogicalAnchorDomain: ...

    def anchors(
        self,
        source: ParticipantRoleHandle | LogicalJourneyResult | MaterializedJourneyResult,
        *,
        population: LogicalAnalysisDomain | LogicalFixedAnalysisDomain | MaterializedAnalysisDomain,
        during: TimeScope,
        business_order: Ref[BusinessOrderKind] | None = None,
    ) -> LogicalAnchorDomain:
        """Bind exact Event occurrences or existing Journey starts as Anchors.

        Args:
            source: Exact Event participant or canonical Journey.
            population: Explicit matching Subject domain.
            during: Half-open selection of starts.
            business_order: Event-only captured order.

        Returns: A LogicalAnchorDomain preserving every selected Anchor instance.
        Example: ``anchors = session.anchors(buyer, population=members, during=window)``.
        Constraints: One Session; fixed Journey uses compatible fixed population without Semantic loading.
        """
        from marivo.analysis.core.domain_captures import fail
        from marivo.analysis.materialization.graph_anchors import bind
        from marivo.analysis.materialization.graph_relation import LiveBinding, Relation
        from marivo.analysis.public_dsl import (
            _TOKEN,
            LogicalAnalysisDomain,
            LogicalAnchorDomain,
            LogicalFixedAnalysisDomain,
            LogicalJourneyResult,
            MaterializedAnalysisDomain,
            MaterializedJourneyResult,
        )

        if not isinstance(
            population,
            (LogicalAnalysisDomain, LogicalFixedAnalysisDomain, MaterializedAnalysisDomain),
        ):
            fail("input_mode", "Anchor population must be an explicit AnalysisDomain")
        if population._runtime.session_ref != self._runtime.session_ref:
            fail("input_binding", "foreign Session population")
        input_source: ParticipantRoleHandle | Relation
        if isinstance(source, ParticipantRoleHandle):
            if not isinstance(population._node.binding, LiveBinding):
                fail("input_mode", "fixed population plus a live Event is mixed")
            owner = self._sources()
            input_source = source
        elif isinstance(source, (LogicalJourneyResult, MaterializedJourneyResult)):
            if source._runtime.session_ref != self._runtime.session_ref:
                fail("input_binding", "foreign Session Journey")
            owner, input_source = None, source._node
        else:
            fail("anchor_binding", "Anchor source must be an Event role or canonical Journey")
        node = bind(
            input_source,
            population._node,
            during=during,
            owner=owner,
            business_order=business_order,
        )
        return LogicalAnchorDomain(_TOKEN, node, self._runtime)

    def members(
        self, entity: Ref[EntityKind], *, at: datetime | BeforeEndBoundary | None = None
    ) -> LogicalAnalysisDomain:
        """Construct the complete governed Entity member domain at an exact version.

        Construction uses schema-only preflight, without business rows or Run.
        Identity retains every declared string or int64 key column.

        Args:
            entity: Exact declared Entity Ref.
            at: Explicit version instant or TimeScope.before_end; None for unversioned Entities.
        Returns: A logical AnalysisDomain bound to this Session.
        Example: ``customers = session.members(ms.ref.entity('sales.customer'))``.
        Constraints: execute() reads and saves the complete member key set; may scan
        the entire Entity source, no default row truncation. PK uniqueness,
        unversioned identity and show(n=...) do not bound size. Shared 600-second
        graph budget.
        """
        from marivo.analysis.materialization.graph_relation import Relation
        from marivo.analysis.public_dsl import new_members

        self._sources()
        assert self._catalog_value is not None
        state = self._catalog_value._state
        relation = Relation.members(
            self._runtime, state.registry, state.sidecar, self.report_tz_name, entity, at=at
        )
        return new_members(relation, self._runtime)

    def source_bindings(self, bindings: SourceBindingMap) -> AbstractContextManager[None]:
        """Capture source parameters while constructing logical sources.

        Args: bindings: Exact Entity refs mapped to declared non-secret parameters.
        Returns: A restoring authoring-scope context manager.
        Example: ``with session.source_bindings(values): dataset = session.members(entity).observe(metric)``.
        Constraints: execute() never rereads ambient bindings; values are not persisted.
        """
        return self._sources().binding_scopes.scope(bindings)

    def artifact(self, reference: str | ArtifactRef) -> PublicMaterialized:
        """Recover an exact committed Dataset without reading current sources.

        Args: reference: Exact ArtifactRef or reference string.
        Returns: The materialized relation variant selected by its checked Store 8 definition.
        Example: ``saved = session.artifact(ref)``.
        Constraints: Recovery uses Store 8, descriptor v3, continuation v4 and graph DAG v2 without reopening sources or reauditing committed result contents. Obsolete formats require source re-execution; existing files are preserved.
        """
        from marivo.analysis.materialization import graph_store
        from marivo.analysis.materialization.graph_dataset import GraphDataset
        from marivo.analysis.materialization.graph_relation import Relation
        from marivo.analysis.public_dsl import wrap_materialized
        from marivo.analysis.session._lazy_runtime_reads import missing_artifact

        with self._runtime.store._read() as connection:
            record = graph_store.artifact(self._runtime.store, connection, str(reference))
        if record is None:
            raise missing_artifact(str(reference))
        saved = GraphDataset(self._runtime, record)
        return wrap_materialized(Relation.restore(saved), self._runtime, saved)

    def runs(
        self, *, status: RunLifecycle | None = None, limit: int = 20, cursor: str | None = None
    ) -> RunPage:
        """Read a bounded page of this Session's Runs.

        Args:
            status: Optional exact lifecycle filter.
            limit: Page size from 1 through 100.
            cursor: Previous page's next_cursor with the same filter.
        Returns: A newest-first RunPage.
        Example: ``session.runs(limit=5).show()``.
        Constraints: This read performs no activation or reconciliation.
        """
        return self._runtime.runs(status=status, limit=limit, cursor=cursor)

    def get_run(self, run_id: str) -> RunRecord:
        """Read an exact same-Session Run.

        Args: run_id: Exact incomplete, failed or succeeded Run identity.
        Returns: The closed incomplete, failed or succeeded Run variant.
        Example: ``session.get_run(run_id).show()``.
        Constraints: Foreign Run identities are rejected.
        """
        return self._runtime.get_run(run_id)

    def graph(
        self,
        *,
        artifact_ref: str | ArtifactRef | None = None,
        direction: GraphDirection = "ancestors",
        max_nodes: int = 100,
    ) -> SessionGraph:
        """Read bounded committed Run and Artifact topology.

        Args:
            artifact_ref: Optional exact graph root.
            direction: Ancestors or descendants of the root.
            max_nodes: Positive bounded graph size.
        Returns: The typed committed SessionGraph.
        Example: ``session.graph(artifact_ref=ref).show()``.
        Constraints: Graph reads never execute or infer origin lineage.
        """
        return self._runtime.graph(
            artifact_ref=artifact_ref, direction=direction, max_nodes=max_nodes
        )

    def render(self, *, max_output_bytes: int | None = 8192) -> str:
        """Render a bounded metadata recap.

        Args: max_output_bytes: Additional UTF-8 output limit, or None.
        Returns: Deterministic terminal text.
        Example: ``text = session.render()``.
        Constraints: No current catalog or source reads occur.
        """
        from marivo.analysis.session._lazy_runtime_reads import recap

        return recap(self._runtime.store, self.id).render(max_output_bytes=max_output_bytes)

    def show(self, *, max_output_bytes: int | None = 8192) -> None:
        """Print a bounded Session recap.

        Args: max_output_bytes: Additional UTF-8 output limit, or None.
        Returns: None after printing.
        Example: ``session.show()``.
        Constraints: This metadata read does not execute or recover work.
        """
        print(self.render(max_output_bytes=max_output_bytes))

    def __repr__(self) -> str:
        return f"<Session id={self.id}; use .show()>"
