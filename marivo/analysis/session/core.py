"""Session-owned source construction and committed Dataset reads."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING

from marivo._temporal import TimeScope
from marivo.analysis.datasets.base import MaterializedDataset
from marivo.analysis.evidence._dataset_types import ArtifactRevalidation
from marivo.analysis.materialization.contracts import SessionRecord
from marivo.analysis.observation.contracts import EntityInput, MetricInput, TimeDimensionInput
from marivo.analysis.observation.metric import LogicalMetricDataset, PopulationInput
from marivo.analysis.observation.population import LogicalPopulationDataset
from marivo.analysis.observation.source_bindings import SourceBindingMap
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session._lazy_read_model import (
    GraphDirection,
    RunLifecycle,
    RunPage,
    RunRecord,
    SessionGraph,
)
from marivo.analysis.session._lazy_sources import LazyEvents, LazyLifecycle, LazySources
from marivo.semantic.catalog import SemanticCatalog

if TYPE_CHECKING:
    from marivo.analysis.materialization.admission import DatasetRuntime


class Session:
    """One named Session for logical sources and committed Dataset reads.

    Acquire with ``mv.session.get_or_create(name)`` or ``mv.session.resume(identity)``.
    Source methods load authored semantics without executing datasource queries.
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
    _sources_value: LazySources | None
    _catalog_value: SemanticCatalog | None

    def _sources(self) -> LazySources:
        if self._sources_value is None:
            from marivo.semantic.catalog import load

            catalog = load(workspace_dir=self.project_root)
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
    def events(self) -> LazyEvents:
        """Return Event source construction bound to this Session."""
        return self._sources().events

    @property
    def lifecycle(self) -> LazyLifecycle:
        """Return Lifecycle source construction bound to this Session."""
        return self._sources().lifecycle

    def population(
        self,
        entity: EntityInput,
        *,
        time_scope: TimeScope | None = None,
        time_dimension: TimeDimensionInput | None = None,
    ) -> LogicalPopulationDataset:
        """Construct governed Entity membership without executing a query.

        Args:
            entity: Exact Entity ref or loaded catalog entry.
            time_scope: Explicit membership-selection scope for versioned Entities.
            time_dimension: Exact membership time axis when required.
        Returns: A Logical Population Dataset.
        Example: ``population = session.population(customers)``.
        Constraints: Versioned Entities require their own finite membership scope.
        """
        return self._sources().population(
            entity, time_scope=time_scope, time_dimension=time_dimension
        )

    def observe(
        self,
        metrics: MetricInput | list[MetricInput] | tuple[MetricInput, ...],
        *,
        population: PopulationInput | None = None,
        time_scope: TimeScope | None = None,
        time_dimension: TimeDimensionInput | None = None,
    ) -> LogicalMetricDataset:
        """Construct ordered Metric observations without executing a query.

        Args:
            metrics: One Metric or an ordered collection of exact Metric inputs.
            population: Optional admitted identity-bearing Dataset.
            time_scope: Independent observation window.
            time_dimension: Exact observation time axis when required.
        Returns: A Logical Metric Dataset.
        Example: ``result = session.observe(revenue).aggregate().execute()``.
        Constraints: Membership scope does not implicitly select observation time.
        """
        return self._sources().observe(
            metrics, population=population, time_scope=time_scope, time_dimension=time_dimension
        )

    def source_bindings(self, bindings: SourceBindingMap) -> AbstractContextManager[None]:
        """Capture source parameters while constructing logical sources.

        Args: bindings: Exact Entity refs mapped to declared non-secret parameters.
        Returns: A restoring authoring-scope context manager.
        Example: ``with session.source_bindings(values): dataset = session.observe(metric)``.
        Constraints: execute() never rereads ambient bindings; values are not persisted.
        """
        return self._sources().source_bindings(bindings)

    def artifact(self, reference: str | ArtifactRef) -> MaterializedDataset:
        """Recover an exact committed Dataset without reading current sources.

        Args: reference: Exact ArtifactRef or reference string.
        Returns: The paired concrete Materialized Dataset.
        Example: ``saved = session.artifact(ref)``.
        Constraints: Recovery never executes the Artifact's origin graph.
        """
        return self._runtime.artifact(reference)

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

        Args: run_id: Exact Run identity.
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

    def revalidate(self, reference: str | ArtifactRef) -> ArtifactRevalidation:
        """Explicitly inspect Artifact, storage and Evidence integrity.

        Args: reference: Exact committed Artifact identity.
        Returns: Three independent integrity and authority assessments.
        Example: ``session.revalidate(ref).show()``.
        Constraints: This is not a source freshness or reuse verdict.
        """
        return self._runtime.revalidate(reference)

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
