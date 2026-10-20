"""Session-owned explicit source construction and persisted Frame recovery."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from datetime import date, datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING

from marivo._temporal import InstantAnchor, TimeScope, _SessionTimeScope
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
from marivo.refs import EntityKind, Ref, SemanticKind
from marivo.semantic.catalog import SemanticCatalog

if TYPE_CHECKING:
    from ibis.common.deferred import Deferred

    from marivo._grouping import Grouping
    from marivo.analysis.dsl.frame import AnalysisFrame
    from marivo.analysis.dsl.frame_axis import FrameAxis
    from marivo.analysis.dsl.population import Population, PopulationPredicate
    from marivo.analysis.dsl.source_capture import ValueRow
    from marivo.analysis.dsl.temporal import CalendarAxis
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.semantic.expressions import Entity


class Session:
    """One named Session for explicit source inputs and immutable analysis data.

    Acquire with ``mv.session.get_or_create(name)`` or ``mv.session.resume(identity)``.
    Source construction resolves authored semantics without reading business rows.
    Persisted Frame recovery reads exact commit metadata without loading semantics.
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
        return self._runtime.local.record()

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

    def members(
        self,
        entity: Ref[EntityKind],
        *,
        at: InstantAnchor | None = None,
        where: Deferred | PopulationPredicate | None = None,
    ) -> Population:
        """Capture a source identity Population without reading schema or rows.

        Args:
            entity: Declared Entity Ref.
            at: Exact version selection instant.
            where: An optional same-root semantic field predicate.

        Returns:
            A lazy Population retaining this Session's ownership.

        Example:
            ``customers = session.members(ms.ref.entity('sales.customer'))``.

        Constraints:
            Source identities include only existing rows. Literal requested
            identities use mv.identities; compute keys with session.frame(population).
        """
        from marivo._definition.relations import PopulationProgram, SourceIdentities
        from marivo._definition.types import AxisBinding, Owner
        from marivo.analysis.core.model import reject
        from marivo.analysis.dsl.population import (
            Population,
            PopulationPredicate,
            _owner,
            condition_populations,
        )
        from marivo.semantic._deferred_semantics import consume_predicate
        from marivo.semantic.expressions import Entity, _Resolver

        if not isinstance(entity, Ref) or entity.kind is not SemanticKind.ENTITY:
            repair = (
                f"Pass ms.ref.entity({entity.ref.ref.path!r})."
                if isinstance(entity, Entity)
                else "Pass ms.ref.entity(path) for the intended Entity."
            )
            reject(
                "an EntityRef lookup key",
                f"{entity.kind.value} Ref" if isinstance(entity, Ref) else type(entity).__name__,
                repair,
                "session.members.entity",
            )
        resolved = _Resolver(self.catalog._state).entity(entity.path)
        predicate = (
            consume_predicate(where)
            if where is not None and not isinstance(where, PopulationPredicate)
            else None
        )
        if predicate is not None and any(root.ref != resolved.ref.ref for root in predicate.roots):
            reject(
                "a predicate on the selected Entity",
                str(tuple(root.ref.key for root in predicate.roots)),
                "Use a field condition from the identity Entity.",
                "session.members",
            )
        if at is not None and not isinstance(at, InstantAnchor):
            reject(
                "an exact InstantAnchor",
                type(at).__name__,
                "Construct at with session.instant(...).",
                "session.members",
            )
        axis = AxisBinding(resolved.ref, None, (), "identity", at)
        if isinstance(where, PopulationPredicate) and any(
            _owner(domain) not in (None, Owner(self.id, self._runtime.store.store_id))
            for domain in condition_populations(where._condition)
        ):
            reject(
                "a predicate domain owned by this Session",
                "another Session owner",
                "Rebuild the predicate domain in this Session.",
                "session.members",
            )
        node = SourceIdentities(
            resolved.ref,
            at,
            where._condition
            if isinstance(where, PopulationPredicate)
            else predicate.node
            if predicate is not None
            else None,
        )
        return Population(
            PopulationProgram(node, Owner(self.id, self._runtime.store.store_id), (axis,)),
            where._frame_inputs if isinstance(where, PopulationPredicate) else (),
        )

    def source_bindings(self, bindings: SourceBindingMap) -> AbstractContextManager[None]:
        """Capture source parameters while constructing logical sources.

        Args: bindings: Exact Entity refs mapped to declared non-secret parameters.
        Returns: A restoring authoring-scope context manager.
        Example: ``with session.source_bindings(values): frame = session.observe({'revenue': revenue})``.
        Constraints: Evaluation never rereads ambient bindings; values are not persisted.
        """
        return self._sources().binding_scopes.scope(bindings)

    def instant(
        self, value: str | date | datetime, *, timezone: str | None = None
    ) -> InstantAnchor:
        """Freeze a precise instant using this Session's saved timezone by default.

        Args:
            value: ISO date/timestamp, date, aware datetime, or existing anchor.
            timezone: Explicit parsing timezone; None uses report_tz_name.
        Returns: An immutable UTC InstantAnchor.
        Example: ``anchor = session.instant('2026-01-01')``.
        Constraints: Rejects naive datetimes and ambiguous/nonexistent local clocks; no source reads.
        """
        from marivo.analysis.dsl.temporal import exact_instant

        return exact_instant(value, self.report_tz_name if timezone is None else timezone)

    def time_scope(
        self,
        *,
        start: str | date | datetime,
        end: str | date | datetime,
        timezone: str | None = None,
    ) -> _SessionTimeScope:
        """Freeze a nonempty half-open observation scope and its calendar timezone.

        Args:
            start: Inclusive explicit boundary.
            end: Exclusive explicit boundary.
            timezone: Parsing/calendar timezone; None uses the saved report timezone.
        Returns: The shared TimeScope with exact InstantAnchor boundaries.
        Example: ``scope = session.time_scope(start='2026-01-01', end='2026-02-01')``.
        Constraints: No source/schema reads; DST ambiguity and naive datetime inputs reject.
        """
        from marivo.analysis.dsl.temporal import exact_scope

        return exact_scope(start, end, self.report_tz_name if timezone is None else timezone)

    def observe(
        self,
        metrics: Mapping[str, Deferred],
        *,
        during: TimeScope | CalendarAxis | None = None,
        by: tuple[Deferred | Grouping, ...] | Population = (),
        where: Deferred | PopulationPredicate | None = None,
        members: Population | None = None,
    ) -> AnalysisFrame:
        """Capture named Metric expressions as a lazy AnalysisFrame.

        Args:
            metrics: Nonempty ordered mapping of names to resolved Metric expressions.
            during: TimeScope filters only; CalendarAxis also adds frozen calendar buckets.
            by: Ordered semantic fields or Grouping rules, or a complete Population output domain. Empty ()
                evaluates overall Metrics per time bucket, or one scalar without a calendar.
            where: Field condition appended to each contribution occurrence, or an explicit
                PopulationPredicate restricting contributions. For member_count, conditions
                are single-valued on the explicit members without reselecting them.
            members: One complete Entity identity Population restricting contributions
                independently of grouping; member_count counts this explicit domain.

        Returns:
            An immutable pending AnalysisFrame owned by this Session.

        Example:
            ``frame = session.observe({'revenue': revenue, 'aov': aov}).compute()``.

        Constraints: Capture reads no source schema or business rows and creates no Run. Only
        compute/persist evaluate; persist publishes the completed result. Artifact objects and
        IDs are not Metric expressions. Value names are single nonempty segments without dots,
        backslashes, controls or reserved presence names.
        """
        from ibis.common.deferred import Deferred

        from marivo.analysis.core.model import reject
        from marivo.analysis.dsl import frame_sources

        if not isinstance(metrics, Mapping) or not metrics:
            reject(
                "a nonempty named mapping of Metric expressions",
                type(metrics).__name__,
                "Pass session.observe({'revenue': revenue}).",
                "session.observe",
            )
        for metric in metrics.values():
            if not isinstance(metric, Deferred):
                reject(
                    "a Metric expression for each output",
                    type(metric).__name__,
                    "Resolve a Metric expression; use persisted_frame(id) to read saved data.",
                    "session.observe",
                )
        return frame_sources.observe(
            self._runtime.local,
            self.catalog._state,
            self._runtime.connection_config,
            metrics,
            during=during,
            by=by,
            where=where,
            members=members,
            timezone=self.report_tz_name,
            calendar_snapshots=self._sources().period_calendar_snapshots,
        )

    def frame(self, population: Population) -> AnalysisFrame:
        """Capture the complete key domain of a general Population as a Frame.

        Args:
            population: A literal, source-backed or explicitly bridged Population.

        Returns:
            A pending AnalysisFrame with complete axes and no named values.

        Example:
            ``keys = session.frame(cohort).compute()``.

        Constraints: Supports multiple axes and zero-axis empty/singleton domains.
        Construction reads no source rows or schema and creates no Run; the Population must be
        unowned or belong to this exact Store and Session.
        """
        from marivo.analysis.core.model import reject
        from marivo.analysis.dsl import frame_sources
        from marivo.analysis.dsl.population import Population, _owner

        if not isinstance(population, Population):
            reject(
                "a Population key domain",
                type(population).__name__,
                "Construct a Population with members, identities, coordinates or population_from.",
                "session.frame",
            )
        owner = _owner(population._definition)
        if owner not in (None, self._runtime.local.owner):
            reject(
                "a Population from this Store and Session",
                repr(owner),
                "Rebuild the Population in this Session.",
                "session.frame",
            )
        return frame_sources.frame(
            self._runtime.local,
            self.catalog._state,
            self._runtime.connection_config,
            population,
        )

    def read(self, population: Population, fields: Mapping[str, Deferred]) -> AnalysisFrame:
        """Capture named scalar attributes for one complete Entity identity domain.

        Args:
            population: One Entity identity axis.
            fields: Nonempty named Fields.

        Returns:
            A pending AnalysisFrame preserving every requested complete identity.

        Example:
            ``attributes = session.read(customers, {'region': region}).compute()``.

        Constraints: Checks Session ownership, scalar paths and captured time anchors; missing
        attribute mappings fail at evaluation. Source construction does not read schema or
        business rows. Value names follow the Frame name rules.
        """
        from marivo.analysis.dsl import frame_sources

        return frame_sources.read(
            self._runtime.local,
            self.catalog._state,
            self._runtime.connection_config,
            population,
            fields,
        )

    def values(
        self,
        *,
        by: tuple[Deferred | Grouping, ...],
        columns: Mapping[str, str | None],
        rows: tuple[ValueRow, ...],
    ) -> AnalysisFrame:
        """Capture finite literal rows with explicit complete axes and value units.

        Args:
            by: Ordered Field/identity axes; () creates a zero-axis Frame.
            columns: Ordered single-segment value names mapped to units or None.
            rows: Complete key tuples paired with exact named literal values.

        Returns:
            A pending AnalysisFrame over the captured literal rows.

        Example:
            ``budget = session.values(by=(), columns={'budget': 'CNY'}, rows=(((), {'budget':
            100}),)).compute()``.

        Constraints: No DataFrame import, null literal values, duplicate complete keys or
        hand-written calendars. Zero-axis capture needs no Catalog or source configuration;
        capture never reads source rows or creates a Run.
        """
        from marivo.analysis.core.model import reject
        from marivo.analysis.dsl import frame_sources

        if not isinstance(by, tuple):
            reject(
                "an ordered tuple of complete axes",
                type(by).__name__,
                "Pass by=() or by=(field,) with resolved semantic axes.",
                "session.values",
            )
        if not by:
            return frame_sources.values(self._runtime.local, columns=columns, rows=rows)
        return frame_sources.coordinate_values(
            self._runtime.local,
            self.catalog._state,
            self._runtime.connection_config,
            by=by,
            columns=columns,
            rows=rows,
        )

    def persisted_frame(self, artifact_id: str) -> AnalysisFrame:
        """Recover the Frame bound to one exact committed Artifact ID.

        Args:
            artifact_id: Exact nonempty Artifact ID string from this Session.

        Returns:
            A stored AnalysisFrame with its complete saved structure.

        Example:
            ``saved = session.persisted_frame('artifact_0123456789abcdef')``.

        Constraints: Checks commit metadata without source, Catalog or payload reads. Data
        reads validate consumed files. Old Stores are refused unchanged; missing or foreign
        IDs fail. For audit metadata, use saved.artifact.
        """
        from marivo.analysis.core.model import reject
        from marivo.analysis.dsl.frame import AnalysisFrame

        if (
            type(artifact_id) is not str
            or not artifact_id
            or any(c in artifact_id for c in "\n\r\t")
        ):
            reject(
                "an exact nonempty Artifact ID string",
                type(artifact_id).__name__,
                "Pass the exact committed output ID, not an Artifact object or Ref.",
                "session.persisted_frame",
            )
        backing = self._runtime.local.open_frame(artifact_id)
        return AnalysisFrame._from_stored(
            self._runtime.local,
            backing.data,
            artifact_id=backing.artifact_id,
            metadata=backing.metadata,
        )

    def population_from(
        self,
        frame: AnalysisFrame,
        *,
        on: tuple[tuple[FrameAxis, Deferred | Entity], ...],
    ) -> Population:
        """Bind selected complete Frame axes to explicit semantic coordinates.

        Args:
            frame: An AnalysisFrame owned by this exact Store and Session.
            on: Required tuple of current FrameAxis and resolved Field/Entity pairs. An empty
                tuple projects the empty joint key.

        Returns:
            A lazy Population retaining the explicit Frame data dependency.

        Example:
            ``parents = session.population_from(chosen, on=((chosen.axes[0], region),))``.

        Constraints: Checks Frame type and ownership before resolving the receiving Catalog.
        Construction reads no data rows or source schema. Select only ordinary field or
        complete identity axes; targets declare new source bindings. Fixed input choices stay
        fixed; pending choices evaluate anew.
        """
        from marivo.analysis.core.model import reject
        from marivo.analysis.dsl.frame import AnalysisFrame
        from marivo.analysis.dsl.frame_population import population_from

        if type(frame) is not AnalysisFrame:
            reject(
                "an AnalysisFrame from the receiving Store and Session",
                type(frame).__name__,
                "Pass a Frame returned by this Session's constructors or operators.",
                "session.population_from",
            )
        if frame._local.owner != self._runtime.local.owner:
            reject(
                "an AnalysisFrame from the receiving Store and Session",
                repr(frame._local.owner),
                "Build the Population in the selected Frame's owning Session.",
                "session.population_from",
            )
        return population_from(self._runtime.local, self.catalog._state, frame, on=on)

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
        """Read bounded same-Session Run-to-Artifact production topology.

        Args:
            artifact_ref: Optional exact committed Artifact from this Session.
            direction: Ancestors includes the producer; descendants returns only the terminal Artifact. Without a focus, use ancestors.
            max_nodes: Node bound within [1, 500]; omitted neighbors become boundaries.
        Returns: The typed committed SessionGraph.
        Example: ``session.graph(artifact_ref=ref).show()``.
        Constraints: Graph reads never execute or infer origin lineage. Terminal Artifact summaries use committed metadata without reading Parquet or semantic graphs.
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
