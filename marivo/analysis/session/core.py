"""Typed analysis session runtime."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast, overload

from marivo.analysis.session._layout import PersistenceLayout
from marivo.analysis.timezone import resolve_system_timezone
from marivo.render import Card, RenderableResult


class _Unset:
    __slots__ = ()

    def __repr__(self) -> str:
        """Deterministic repr so live help never leaks a memory address."""
        return "<unset>"


_UNSET = _Unset()


T = TypeVar("T")


def _normalize_unset(value: T | _Unset) -> T | None:
    return None if isinstance(value, _Unset) else value


if TYPE_CHECKING:
    from marivo._temporal import Grain as TemporalGrain
    from marivo.analysis.event import (
        CompletenessDeclaration,
        EventMatchingPolicy,
        EventOccurrenceBounds,
        EventPattern,
        EventWatermarkReceipt,
        PatternStep,
    )
    from marivo.analysis.evidence import ArtifactRevalidation
    from marivo.analysis.evidence.store import EvidenceStore
    from marivo.analysis.frames.association import AssociationResult
    from marivo.analysis.frames.attribution import AttributionFrame
    from marivo.analysis.frames.base import BaseFrame
    from marivo.analysis.frames.candidate import (
        CandidateSet,
        OntologyMetricCandidate,
        PointAnomalyStrategy,
    )
    from marivo.analysis.frames.delta import DeltaFrame
    from marivo.analysis.frames.event import EventFrame
    from marivo.analysis.frames.forecast import ForecastFrame
    from marivo.analysis.frames.hypothesis import HypothesisTestResult
    from marivo.analysis.frames.lifecycle import LifecycleFrame
    from marivo.analysis.frames.metric import MetricFrame
    from marivo.analysis.frames.subject import SubjectSet
    from marivo.analysis.funnel import FunnelLossRate
    from marivo.analysis.intents._attribution_mode import AttributionMode
    from marivo.analysis.intents._shape import SemanticShape
    from marivo.analysis.lifecycle import FromInception
    from marivo.analysis.policies import AlignmentPolicy, SamplingPolicy
    from marivo.analysis.runtime_metric import RuntimeMetricExpr
    from marivo.analysis.session._read_model import (
        GraphDirection,
        RunPage,
        RunRecord,
        SessionGraph,
    )
    from marivo.analysis.session._store import SessionStore
    from marivo.analysis.slice_types import SliceValue
    from marivo.analysis.subject import SubjectSelection
    from marivo.analysis.windows.spec import TimeScope
    from marivo.ontology.catalog import OntologyCatalog
    from marivo.refs import (
        DimensionKind,
        EntityKind,
        EventKind,
        MetricKind,
        Ref,
        StateModelKind,
        TimeDimensionKind,
    )
    from marivo.semantic.catalog import SemanticCatalog, _SemanticInput
    from marivo.semantic.errors import SemanticError


def _track_session_operation(
    session: object,
    event_name: str,
    *,
    family: str,
    intent: str,
    attributes: dict[str, str | int | float | bool] | None = None,
) -> Any:
    from marivo.telemetry import track_operation

    return track_operation(
        event_name,
        family=family,
        intent=intent,
        session=session,
        attributes=attributes,
    )


@contextmanager
def _track_materializing_operation(
    session: Session,
    event_name: str,
    *,
    capability_id: str,
    family: str,
    intent: str,
    arguments: Mapping[str, object],
    analysis_purpose: str | None,
    attributes: dict[str, str | int | float | bool] | None = None,
) -> Iterator[Any]:
    """Combine telemetry with one private persisted Run admission."""
    from marivo.analysis.errors import CrossSessionFrameError
    from marivo.analysis.frames.base import BaseFrame
    from marivo.analysis.session._runs import admit_run, collect_input_artifact_refs

    def validate_frame_ownership(value: object) -> None:
        if isinstance(value, BaseFrame):
            if value.meta.session_id != session.id:
                raise CrossSessionFrameError(
                    message=(
                        f"frame belongs to session {value.meta.session_id!r}, not {session.id!r}"
                    )
                )
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                validate_frame_ownership(key)
                validate_frame_ownership(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                validate_frame_ownership(item)

    validate_frame_ownership(arguments)
    input_artifact_refs = tuple(
        ref
        for ref in collect_input_artifact_refs(arguments)
        if session._store.get_artifact(session.id, ref) is not None
    )

    with (
        _track_session_operation(
            session,
            event_name,
            family=family,
            intent=intent,
            attributes=attributes,
        ) as operation,
        admit_run(
            session,
            capability_id=capability_id,
            analysis_purpose=analysis_purpose,
            arguments=arguments,
            input_artifact_refs=input_artifact_refs,
        ),
    ):
        yield operation


class Session(RenderableResult):
    """Call marivo.help(Session) for its public consumption contract."""

    __slots__ = (
        "_catalog",
        "_connection_runtime",
        "_created_at",
        "_cwd",
        "_id",
        "_judgment_store",
        "_judgment_store_unavailable",
        "_layout",
        "_name",
        "_ontology_catalog",
        "_ontology_issues",
        "_ontology_state",
        "_project_root",
        "_question",
        "_report_tz_name",
        "_report_tz_resolution",
        "_report_tz_warning",
        "_store",
        "_tz",
        "_updated_at",
    )

    def __init__(
        self,
        id: str,
        name: str,
        question: str | None,
        cwd: Path,
        project_root: Path,
        created_at: datetime,
        updated_at: datetime,
        connection_runtime: Any,
        layout: PersistenceLayout,
        semantic_catalog: SemanticCatalog,
        store: SessionStore,
        report_tz: tzinfo | None = None,
        report_tz_name: str | None = None,
        report_tz_resolution: str | None = None,
        report_tz_warning: str | None = None,
        judgment_store: EvidenceStore | None = None,
        judgment_store_unavailable: bool = False,
        ontology_state: Literal["absent", "ready", "unavailable"] = "absent",
        ontology_catalog: OntologyCatalog | None = None,
        ontology_issues: tuple[SemanticError, ...] = (),
    ) -> None:
        self._id = id
        self._name = name
        self._question = question
        self._cwd = cwd
        self._project_root = project_root
        self._created_at = created_at
        self._updated_at = updated_at
        self._connection_runtime = connection_runtime
        self._layout = layout
        self._catalog = semantic_catalog
        self._store = store
        if report_tz is not None:
            self._tz = report_tz
            self._report_tz_name = report_tz_name if report_tz_name is not None else str(report_tz)
            self._report_tz_resolution = (
                report_tz_resolution if report_tz_resolution is not None else "iana"
            )
            self._report_tz_warning = report_tz_warning
        else:
            resolved_report_tz = resolve_system_timezone()
            self._tz = resolved_report_tz.tz
            self._report_tz_name = (
                report_tz_name if report_tz_name is not None else resolved_report_tz.name
            )
            self._report_tz_resolution = (
                report_tz_resolution
                if report_tz_resolution is not None
                else resolved_report_tz.resolution
            )
            self._report_tz_warning = (
                report_tz_warning if report_tz_warning is not None else resolved_report_tz.warning
            )
        self._judgment_store = judgment_store
        self._judgment_store_unavailable = judgment_store_unavailable
        self._ontology_state = ontology_state
        self._ontology_catalog = ontology_catalog
        self._ontology_issues = ontology_issues

    def _repr_identity(self) -> str:
        return f"Session id={self._id} name={self._name}"

    def _card(self) -> Card:
        from marivo.analysis._capabilities.registry import REGISTRY
        from marivo.analysis.session._runtime_reads import SessionRuntimeReads

        mode = "read_only" if self.is_read_only else "writable"
        recap = SessionRuntimeReads(self).recap()
        properties, methods = REGISTRY.public_object_members("Session")
        intrinsic_methods = tuple(method for method in methods if method in {"show"})
        registered_calls = tuple(
            call
            for call in REGISTRY.public_member_calls("Session")
            if call not in {".render()", ".show()"} and not call.startswith(".graph(")
        )
        graph_calls = [
            ".graph(artifact_ref='<ref>', direction='ancestors')",
            ".graph(artifact_ref='<ref>', direction='descendants')",
        ]
        if recap.overall_graph_available:
            graph_calls.insert(0, ".graph()")
        card = Card(
            identity=self._repr_identity(),
            available=(
                *(f".{property_name}" for property_name in properties),
                *(f".{method_name}()" for method_name in intrinsic_methods),
                *registered_calls,
                *graph_calls,
            ),
        ).status(mode)
        card.field("question", self._question or "none")
        card.field("ontology", self._ontology_state)
        card.field("report_timezone", self._report_tz_name)
        card.field("created_at", self._created_at.isoformat())
        card.field("updated_at", self._updated_at.isoformat())
        card.field(
            "artifacts",
            f"total={recap.artifact_count} heads={recap.head_artifact_count} "
            f"evidence_complete={recap.evidence_complete_count} "
            f"partial={recap.evidence_partial_count} "
            f"unavailable={recap.evidence_unavailable_count}",
        )
        card.field(
            "runs",
            f"succeeded={recap.succeeded_run_count} failed={recap.failed_run_count} "
            f"incomplete={recap.incomplete_run_count}",
        )
        card.listing("attention", recap.attention_run_ids or ("none",))
        card.listing("heads", recap.head_artifact_refs or ("none",))
        if not recap.overall_graph_available:
            card.field("overall graph", "too large; use Run paging or a focused graph call")
        card.field("current authority", "not checked; call session.revalidate('<ref>')")
        card.field("source freshness", "not checked by Session reads")
        return card

    # -- Public identity properties (read-only) --

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def question(self) -> str | None:
        return self._question

    @property
    def cwd(self) -> Path:
        return self._cwd

    @property
    def project_root(self) -> Path:
        return self._project_root

    @property
    def catalog(self) -> SemanticCatalog:
        """Return the session semantic catalog."""
        return self._catalog

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def tz(self) -> tzinfo:
        return self._tz

    @property
    def report_tz(self) -> tzinfo:
        return self._tz

    @property
    def report_tz_name(self) -> str:
        return self._report_tz_name

    @property
    def report_tz_resolution(self) -> str:
        return self._report_tz_resolution

    @property
    def report_tz_warning(self) -> str | None:
        return self._report_tz_warning

    @property
    def is_read_only(self) -> bool:
        """Whether this session can execute queries against datasources.

        Returns ``True`` when no datasource resolution path is configured,
        meaning the session can read persisted artifacts but cannot run new
        analysis that touches a datasource.
        """
        service = getattr(self._connection_runtime, "service", None)
        if service is None:
            return False
        has_overrides = bool(getattr(service, "_backend_overrides", {}))
        has_factory = getattr(service, "_backend_factory", None) is not None
        uses_datasources = bool(getattr(service, "_use_datasources", False))
        return not (has_overrides or has_factory or uses_datasources)

    def source_bindings(
        self,
        bindings: Mapping[
            Ref[EntityKind],
            Mapping[
                str,
                str | int | float | bool | Sequence[str | int | float | bool],
            ],
        ],
        /,
    ) -> AbstractContextManager[None]:
        """Bind parameterized JSON sources for one analysis execution scope.

        Args:
            bindings: Exact entity refs mapped to their required source
                parameters. A value is a scalar (``str | int | float | bool``) or
                a flat, non-empty list of scalars; list values are URL-encoded as
                repeated query keys or serialized as a JSON array in a POST body.

        Returns:
            A context manager that installs bindings only for its dynamic scope.

        Example:
            >>> with session.source_bindings({
            ...     ms.ref.entity("monitoring.samples"): {"start": 1, "end": 2},
            ...     ms.ref.entity("monitoring.apps"): {"app": ["app-1", "app-2"]},
            ... }):
            ...     frame = session.observe(ms.ref.metric("monitoring.value"))

        Constraints:
            Keys must be current ``Ref[entity]`` values using parameterized
            ``md.json(...)`` sources. Missing and extra values fail before
            execution. Nested or empty lists are rejected.
        """
        from marivo.analysis.session._source_bindings import source_binding_scope

        return source_binding_scope(self._connection_runtime, self._catalog, bindings)

    def runs(
        self,
        *,
        status: Literal["incomplete", "succeeded", "failed"] | None = None,
        capability_id: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> RunPage:
        """Return one bounded newest-first page of immutable Run records.

        Args:
            status: Optional exact lifecycle filter.
            capability_id: Optional exact current capability id.
            limit: Maximum records to retain, from 1 through 100.
            cursor: Opaque continuation returned by the previous page.

        Returns:
            A bounded immutable :class:`RunPage`.

        Example:
            >>> page = session.runs(status="failed", limit=5)
            >>> failed = page.items[0] if page.items else None

        Constraints:
            Reads only exact current-schema state and never resumes or retries a Run.
        """
        from marivo.analysis.session._runtime_reads import SessionRuntimeReads

        return SessionRuntimeReads(self).runs(
            status=status,
            capability_id=capability_id,
            limit=limit,
            cursor=cursor,
        )

    def get_run(self, run_id: str) -> RunRecord:
        """Return one exact immutable Run record.

        Args:
            run_id: Exact Run identity from ``runs()`` or ``graph()``.

        Returns:
            One :class:`IncompleteRun`, :class:`SucceededRun`, or :class:`FailedRun`.

        Example:
            >>> run = session.get_run("run_01")
            >>> run.show()

        Constraints:
            Unknown ids fail structurally; no implicit latest Run is selected.
        """
        from marivo.analysis.session._runtime_reads import SessionRuntimeReads

        return SessionRuntimeReads(self).get_run(run_id)

    def artifact(self, ref: str) -> BaseFrame:
        """Load one exact committed Artifact owned by this Session.

        Args:
            ref: Exact Artifact ref from a Run, graph, or Session recap.

        Returns:
            The concrete immutable :class:`BaseFrame` subtype for that Artifact.

        Example:
            >>> artifact = session.artifact("artifact_01")
            >>> artifact.show()

        Constraints:
            The ref must belong to this exact current-schema Session.
        """
        from marivo.analysis.session._runtime_reads import SessionRuntimeReads

        return SessionRuntimeReads(self).artifact(ref)

    def revalidate(self, ref: str) -> ArtifactRevalidation:
        """Revalidate one exact Artifact against current authority and Evidence.

        Args:
            ref: Exact Artifact ref owned by this Session.

        Returns:
            An ephemeral immutable :class:`ArtifactRevalidation` result.

        Example:
            >>> result = session.revalidate("artifact_01")
            >>> result.show()

        Constraints:
            Revalidation is read-only and does not establish datasource freshness or
            business validity.
        """
        from marivo.analysis.session._runtime_reads import SessionRuntimeReads

        return SessionRuntimeReads(self).revalidate(ref)

    def graph(
        self,
        *,
        artifact_ref: str | None = None,
        direction: GraphDirection = "ancestors",
        max_nodes: int = 100,
    ) -> SessionGraph:
        """Project a bounded factual Run/Artifact graph for this Session.

        Args:
            artifact_ref: Optional exact focus Artifact; omit for the overall graph.
            direction: ``"ancestors"`` or, with a focus, ``"descendants"``.
            max_nodes: Maximum retained Run and Artifact nodes, from 1 through 500.

        Returns:
            An immutable :class:`SessionGraph` with explicit truncation boundaries.

        Example:
            >>> graph = session.graph(
            ...     artifact_ref="artifact_01", direction="ancestors", max_nodes=100
            ... )

        Constraints:
            Graphs contain persisted runtime facts only; they do not read Findings,
            infer semantic authority, or check datasource freshness.
        """
        from marivo.analysis.session._runtime_reads import SessionRuntimeReads

        return SessionRuntimeReads(self).graph(
            artifact_ref=artifact_ref,
            direction=direction,
            max_nodes=max_nodes,
        )

    def close(self) -> None:
        """Release session resources: the evidence store and cached backends.

        Safe to call more than once. After closing, the evidence store is
        reopened lazily on next access via :meth:`_evidence_store`.
        """
        if self._judgment_store is not None:
            self._judgment_store.close()
            self._judgment_store = None
        if self._connection_runtime is not None:
            self._connection_runtime.close_all()

    def _evidence_store(self) -> EvidenceStore | None:
        """Return the lazily opened EvidenceStore, or None for commit isolation."""
        if self._judgment_store is not None:
            return self._judgment_store
        if self._judgment_store_unavailable:
            return None
        from marivo.analysis.errors import EvidenceStoreUnavailableError
        from marivo.analysis.evidence.store import open_evidence_store

        db_path = self._layout.session_dir / "judgment.db"
        try:
            store = open_evidence_store(db_path)
        except EvidenceStoreUnavailableError:
            self._judgment_store_unavailable = True
            return None
        self._judgment_store = store
        return store

    @property
    def discover(self) -> SessionDiscoverNamespace:
        """Return session-bound candidate discovery helpers."""
        return SessionDiscoverNamespace(self)

    @property
    def events(self) -> SessionEvents:
        """Return the typed Event Journey materialization and reducer namespace."""
        return SessionEvents(self)

    @property
    def lifecycle(self) -> SessionLifecycle:
        """Return replay-based Lifecycle materialization and reducer operators."""
        return SessionLifecycle(self)

    def select_subjects(
        self,
        artifact: EventFrame | LifecycleFrame,
        *,
        selection: SubjectSelection,
        analysis_purpose: str | None = None,
    ) -> SubjectSet:
        """Select a persisted typed SubjectSet from a journey or replay history.

        Args:
            artifact: Exact ``EventFrame[journey]`` or ``LifecycleFrame[history]``
                produced in this session.
            selection: Closed typed selection matching the source artifact —
                ``mv.dropped_before(step=...)`` for a journey, or
                ``mv.in_state(state=..., as_of=...)`` for replay history.
            analysis_purpose: Optional business purpose retained in lineage.

        Returns:
            A persisted ``SubjectSet`` containing only governed identity tuples.

        Example:
            >>> dropouts = session.select_subjects(
            ...     journeys,
            ...     selection=mv.dropped_before(step=payment_step),
            ... )
            >>> paid = session.select_subjects(
            ...     history,
            ...     selection=mv.in_state(paid_state, as_of="2026-07-15T00:00:00Z"),
            ... )

        Constraints:
            Journeys accept only first-per-subject matching and an exact
            non-initial PatternStep retained by the source pattern. Replay
            history accepts only an exact retained ``ModelStateHandle`` and an
            ``as_of`` inside the closed source replay window.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.subjects import select_subjects

        validate_capability_inputs(
            "select_subjects",
            session=self,
            artifact=artifact,
            selection=selection,
        )
        with _track_materializing_operation(
            self,
            "marivo.analysis.select_subjects",
            capability_id="select_subjects",
            family="subjects",
            intent="select_subjects",
            arguments={"artifact": artifact, "selection": selection},
            analysis_purpose=analysis_purpose,
        ):
            return select_subjects(
                artifact,
                selection=selection,
                analysis_purpose=analysis_purpose,
                session=self,
            )

    @overload
    def observe(
        self,
        metrics: OntologyMetricCandidate,
        *,
        analysis_purpose: str | None = None,
    ) -> MetricFrame: ...

    @overload
    def observe(
        self,
        metrics: (
            _SemanticInput[MetricKind]
            | RuntimeMetricExpr
            | list[_SemanticInput[MetricKind] | RuntimeMetricExpr]
            | tuple[_SemanticInput[MetricKind] | RuntimeMetricExpr, ...]
        ),
        *,
        time_scope: TimeScope | None = None,
        grain: TemporalGrain | None = None,
        dimensions: list[_SemanticInput[DimensionKind | TimeDimensionKind]] | None = None,
        slice_by: Mapping[
            _SemanticInput[DimensionKind | TimeDimensionKind],
            SliceValue,
        ]
        | None = None,
        time_dimension: _SemanticInput[TimeDimensionKind] | None = None,
        expect_shape: SemanticShape | None = None,
        cohort: SubjectSet | None = None,
        analysis_purpose: str | None = None,
    ) -> MetricFrame: ...

    def observe(
        self,
        metrics: (
            _SemanticInput[MetricKind]
            | RuntimeMetricExpr
            | OntologyMetricCandidate
            | list[_SemanticInput[MetricKind] | RuntimeMetricExpr]
            | tuple[_SemanticInput[MetricKind] | RuntimeMetricExpr, ...]
        ),
        *,
        time_scope: TimeScope | _Unset | None = _UNSET,
        grain: TemporalGrain | _Unset | None = _UNSET,
        dimensions: list[_SemanticInput[DimensionKind | TimeDimensionKind]]
        | _Unset
        | None = _UNSET,
        slice_by: Mapping[_SemanticInput[DimensionKind | TimeDimensionKind], SliceValue]
        | _Unset
        | None = _UNSET,
        time_dimension: _SemanticInput[TimeDimensionKind] | _Unset | None = _UNSET,
        expect_shape: SemanticShape | _Unset | None = _UNSET,
        cohort: SubjectSet | _Unset | None = _UNSET,
        analysis_purpose: str | None = None,
    ) -> MetricFrame:
        """Materialize one or more metric roots into a typed MetricFrame.

        When to use: starting point for any metric analysis workflow.

        Resolves an exact current-catalog metric entry/ref or a closed recursive
        value from ``mv.runtime_metric``, applies the shared observation scope,
        executes one bounded expression graph, and persists canonical refs in
        the resulting MetricFrame.

        ``to_pandas()`` exports one value column per ordered root. Read
        ``frame.value_columns`` before merging or renaming frames. Runtime metric
        constructors require an explicit non-empty ``label`` for every expression;
        labels are stable public value-column handles but remain presentation-only
        metadata rather than catalog authority or value identity.

        Args:
            metrics: Exact current-catalog metric entry/ref,
                ``RuntimeMetricExpr``, or a non-empty list/tuple of either over
                one shared scope. Bare strings and stale or cross-catalog
                entries are rejected. Catalog and runtime roots may be
                recursively composed, including nested catalog-derived metrics.
                Temporal roots in one sequence must resolve to the same exact
                time-dimension ref.
            time_scope: One ``mv.time_scope(...)`` value (or exact catalog period scope).
                The interval is half-open: start is inclusive and end is exclusive.
            grain: Optional unified ``mv.grain(...)`` or certified semantic grain. When
                present, observe returns a time series or panel depending on ``dimensions``.
                It must be no finer than the selected time dimension's declared
                granularity.
            dimensions: Exact current-catalog dimension/time-dimension entries
                or refs used as segment axes. Omit, pass ``None``, or pass
                ``[]`` for no segment axes.
            slice_by: Pre-aggregation global row filter. Keys are exact dimension
                refs; values are either a scalar (``==``), a
                list/tuple/set (``in``), or ``{"op": "<op>", "value": ...}`` where op is one of
                ``==, !=, in, >, >=, <, <=, between``.
            time_dimension: Exact current-catalog time-dimension entry/ref
                selecting the time axis when an entity declares multiple time
                dimensions. When the default axis is day-granular, an hourly
                observation requires an hour-granular time dimension passed
                explicitly here; author and verify one first if none exists.
            expect_shape: Optional guard. If set, observe predicts the output shape
                from ``grain``/``dimensions`` and raises ``SemanticKindMismatchError``
                before any backend work when the prediction differs.
            cohort: Optional ready ``SubjectSet``. Membership is applied to every
                metric leaf before aggregation through the governed subject path.

        Raises:
            MetricNotFoundError: A catalog metric ref is unknown.
            SemanticKindMismatchError: A semantic input is not the required exact
                ref subclass, roots do not share one shape/model/source domain,
                or an expression exceeds the fixed graph contract.
            TemporalSuitabilityError: A temporal request has no usable shared
                time axis or requests an incompatible encoding/grain.
            ObservePlanningError: Planning failed (e.g. cross-datasource plan, missing
                path, ambiguous dimension). Check ``details["code"]`` for the specific
                error code.

        Example:
            >>> catalog = session.catalog
            >>> revenue = catalog.metrics.get("sales.revenue")
            >>> country = catalog.dimensions.get("sales.orders.country")
            >>> channel = catalog.dimensions.get("sales.orders.channel")
            >>> frame = session.observe(
            ...     revenue,
            ...     time_scope=mv.time_scope(start="2026-07-01", end="2026-10-01"),
            ...     grain=mv.grain("day"),
            ...     dimensions=[country],
            ...     analysis_purpose="确认三季度按国家收入走势",
            ... )
            >>> frame.show()
            >>> order_count = catalog.metrics.get("sales.order_count")
            >>> report = session.observe(metrics=[revenue, order_count])
            >>> report.show()
            >>> # Filter to a subset before aggregation with slice_by:
            >>> us_online_frame = session.observe(
            ...     revenue,
            ...     time_scope=mv.time_scope(start="2026-07-01", end="2026-10-01"),
            ...     grain=mv.grain("day"),
            ...     slice_by={country: "US", channel: "online"},
            ... )
            >>> us_online_frame.show()
            >>> # Derived ratio division uses zero_division="null":
            >>> # a present zero denominator/weight yields null (never +/-inf) and is
            >>> # counted in frame.meta.quality_summary.zero_denominator_rows.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.errors import (
            CandidateNotObservableError,
            CandidateScopeOverrideForbiddenError,
        )
        from marivo.analysis.frames.candidate import OntologyMetricCandidate
        from marivo.analysis.intents.observe import observe

        if isinstance(metrics, (list, tuple)) and any(
            isinstance(item, OntologyMetricCandidate) for item in metrics
        ):
            raise CandidateNotObservableError(
                message="OntologyMetricCandidate cannot be observed in a collection",
                expected="one selected candidate passed directly to session.observe(candidate)",
                received="candidate collection or mixed Metric/candidate input",
            )

        if isinstance(metrics, OntologyMetricCandidate):
            overrides = tuple(
                name
                for name, value in (
                    ("time_scope", time_scope),
                    ("grain", grain),
                    ("dimensions", dimensions),
                    ("slice_by", slice_by),
                    ("time_dimension", time_dimension),
                    ("expect_shape", expect_shape),
                    ("cohort", cohort),
                )
                if value is not _UNSET
            )
            if overrides:
                raise CandidateScopeOverrideForbiddenError(
                    message="candidate observation cannot override inherited scope",
                    expected="only analysis_purpose= is accepted with OntologyMetricCandidate",
                    received=", ".join(overrides),
                )
            from marivo.analysis.intents.observe_candidate import observe_candidate

            validate_capability_inputs("observe", metrics=metrics)
            with _track_materializing_operation(
                self,
                "marivo.analysis.observe",
                capability_id="observe",
                family="core",
                intent="observe",
                arguments={"metrics": metrics},
                analysis_purpose=analysis_purpose,
                attributes={"marivo.analysis.candidate_observation": True},
            ):
                return observe_candidate(
                    metrics,
                    analysis_purpose=analysis_purpose,
                    session=self,
                )

        normalized_time_scope = _normalize_unset(time_scope)
        normalized_grain = _normalize_unset(grain)
        normalized_dimensions = _normalize_unset(dimensions)
        normalized_slice_by = _normalize_unset(slice_by)
        normalized_time_dimension = _normalize_unset(time_dimension)
        normalized_expect_shape = _normalize_unset(expect_shape)
        normalized_cohort = _normalize_unset(cohort)

        validate_capability_inputs(
            "observe",
            session=self,
            time_scope=normalized_time_scope,
            cohort=normalized_cohort,
        )
        with _track_materializing_operation(
            self,
            "marivo.analysis.observe",
            capability_id="observe",
            family="core",
            intent="observe",
            arguments={
                "metrics": metrics,
                "time_scope": normalized_time_scope,
                "grain": normalized_grain,
                "dimensions": normalized_dimensions,
                "slice_by": normalized_slice_by,
                "time_dimension": normalized_time_dimension,
                "expect_shape": normalized_expect_shape,
                "cohort": normalized_cohort,
            },
            analysis_purpose=analysis_purpose,
            attributes={"marivo.analysis.dimension_count": len(normalized_dimensions or [])},
        ) as telemetry_operation:
            result = observe(
                metrics,
                time_scope=normalized_time_scope,
                grain=normalized_grain,
                dimensions=normalized_dimensions,
                slice_by=normalized_slice_by,
                time_dimension=normalized_time_dimension,
                expect_shape=normalized_expect_shape,
                cohort=normalized_cohort,
                analysis_purpose=analysis_purpose,
                session=self,
            )
            graph = result.meta.expression_graph
            if graph is not None:
                node_kind_counts: dict[str, int] = {}
                zero_policies: set[str] = set()
                for record in graph.nodes:
                    node_kind_counts[record.node.kind] = (
                        node_kind_counts.get(record.node.kind, 0) + 1
                    )
                    zero_policy = getattr(record.node, "zero_division", None)
                    if isinstance(zero_policy, str):
                        zero_policies.add(zero_policy)
                graph_attributes: dict[str, str | int | float | bool] = {
                    "marivo.analysis.metric_graph.root_count": len(graph.roots),
                    "marivo.analysis.metric_graph.node_count": len(graph.nodes),
                    "marivo.analysis.metric_graph.pre_cse_occurrence_count": len(graph.occurrences),
                    "marivo.analysis.metric_graph.max_depth": max(
                        (occurrence.path.count(".") + 1 for occurrence in graph.occurrences),
                        default=0,
                    ),
                    "marivo.analysis.metric_graph.node_kinds": ",".join(
                        f"{kind}:{node_kind_counts[kind]}" for kind in sorted(node_kind_counts)
                    ),
                    "marivo.analysis.metric_graph.reused_occurrences": max(
                        0, len(graph.occurrences) - len(graph.nodes)
                    ),
                    "marivo.analysis.metric_graph.zero_policies": ",".join(sorted(zero_policies)),
                    "marivo.analysis.semantic_shape": result.meta.semantic_kind,
                }
                execution_stats = result.meta.execution_stats
                if execution_stats is not None:
                    downstream_blockers = set(execution_stats.downstream_blockers)
                    cumulative = result.meta.cumulative
                    if isinstance(cumulative, dict):
                        compare_blocker = cumulative.get("compare_blocker")
                        if isinstance(compare_blocker, str) and compare_blocker:
                            downstream_blockers.add(compare_blocker)
                    graph_attributes.update(
                        {
                            "marivo.analysis.metric_graph.root_origins": ",".join(
                                execution_stats.root_origins
                            ),
                            "marivo.analysis.metric_graph.cache_hit": execution_stats.cache_hit,
                            "marivo.analysis.metric_graph.artifact_deduplicated": (
                                execution_stats.artifact_deduplicated
                            ),
                            "marivo.analysis.metric_graph.cse_used": (
                                execution_stats.cse_reused_occurrences > 0
                            ),
                            "marivo.analysis.metric_graph.replay_used": (
                                execution_stats.replay_used
                            ),
                            "marivo.analysis.metric_graph.physical_execution_count": (
                                execution_stats.physical_execution_count
                            ),
                            "marivo.analysis.downstream_blockers": (
                                ",".join(sorted(downstream_blockers)) or "none"
                            ),
                        }
                    )
                if telemetry_operation is not None:
                    telemetry_operation.attributes.update(graph_attributes)
                else:
                    from marivo.telemetry import _add_operation_attributes

                    _add_operation_attributes(graph_attributes)
            return result

    def compare(
        self,
        current: MetricFrame | EventFrame,
        baseline: MetricFrame | EventFrame,
        *,
        alignment: AlignmentPolicy | None = None,
        analysis_purpose: str | None = None,
    ) -> DeltaFrame:
        """Compute the typed delta between two MetricFrames (current minus baseline).

        When to use: quantify change between two periods; produces a DeltaFrame for attribute or discover.

        The two frames must share persisted comparable value semantics and
        ``semantic_kind``. Equivalent catalog and runtime expressions may have
        different metric identities. Segmented frames must share exact requested
        dimension refs; time-bearing frames must share time-dimension identity,
        grain, and report timezone.

        Args:
            current: Current-period MetricFrame or EventFrame[funnel].
            baseline: Baseline-period MetricFrame or EventFrame[funnel].
            alignment: Defaults to ``mv.window_bucket()``. For day-grain
                time-series or panel frames selected by exact temporal-occurrence
                scopes, ``mv.occurrence_progress(anchor=..., unmatched=...)``
                pairs effective local-day ordinals; ``mv.working_day_progress(
                schedule=..., unmatched=...)`` pairs working-day ordinals under
                one certified schedule. Segmented frames continue to support only
                ``window_bucket`` in v1.

        Guidance:
            Funnel comparison has one mechanically determined alignment:
            persisted PatternStep identity plus the exact axis-value tuple. It
            accepts no alignment argument, never aligns by position, zero-fills
            additive counts for one-sided tuples, and leaves absent-side rates
            null. Any coverage-censored aligned population is rejected.
            The result keeps the dimension columns alongside the protocol
            columns ``current``/``baseline``/``delta``/``pct_change``/
            ``pct_change_status``/``presence_status``, so a semantic dimension
            named like one of those is rejected with a semantic-authoring
            repair instead of a raw duplicate-column error.

        Raises:
            SemanticKindMismatchError: Different value semantics or
                ``semantic_kind``, ``current``/``baseline`` is not a MetricFrame,
                or a dimension column collides with a result protocol column.
            SegmentDimensionMismatchError: ``segmented`` frames disagree on segment columns.
            PanelGrainMismatchError: ``panel`` frames disagree on time grain.
            AlignmentPolicyNotApplicableError: Alignment kind incompatible with the frame shape.
            CrossSessionFrameError: A frame belongs to a different session.

        Example:
            >>> revenue = session.catalog.require(ms.ref.metric("sales.revenue")).ref
            >>> cur = session.observe(
            ...     revenue,
            ...     time_scope=mv.time_scope(start="2026-07-01", end="2026-10-01"),
            ... )
            >>> base = session.observe(
            ...     revenue,
            ...     time_scope=mv.time_scope(start="2025-07-01", end="2025-10-01"),
            ... )
            >>> delta = session.compare(
            ...     cur,
            ...     base,
            ...     alignment=mv.window_bucket(),
            ...     analysis_purpose="量化三季度收入同比变化",
            ... )
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.frames.event import EventFrame

        if type(current) is EventFrame or type(baseline) is EventFrame:
            from marivo.analysis.intents.funnel_compare import (
                compare_funnels,
                validate_funnel_compare_admission,
            )

            validate_funnel_compare_admission(
                current,
                baseline,
                alignment=alignment,
                session=self,
            )
            validate_capability_inputs(
                "compare", current=current, baseline=baseline, alignment=alignment
            )
            with _track_materializing_operation(
                self,
                "marivo.analysis.compare.funnel",
                capability_id="compare",
                family="events",
                intent="compare",
                arguments={
                    "current": current,
                    "baseline": baseline,
                    "alignment": alignment,
                },
                analysis_purpose=analysis_purpose,
                attributes={"marivo.analysis.semantic_kind": "funnel"},
            ):
                return compare_funnels(
                    current,
                    baseline,
                    alignment=alignment,
                    analysis_purpose=analysis_purpose,
                    session=self,
                )

        from marivo.analysis.intents.compare import compare

        current_metric = cast("MetricFrame", current)
        baseline_metric = cast("MetricFrame", baseline)

        semantic_kind = getattr(current_metric.meta, "semantic_kind", None)
        attrs: dict[str, str | int | float | bool] | None = (
            {"marivo.analysis.semantic_kind": semantic_kind}
            if isinstance(semantic_kind, str)
            else None
        )
        validate_capability_inputs(
            "compare",
            current=current_metric,
            baseline=baseline_metric,
            alignment=alignment,
        )
        with _track_materializing_operation(
            self,
            "marivo.analysis.compare",
            capability_id="compare",
            family="core",
            intent="compare",
            arguments={
                "current": current_metric,
                "baseline": baseline_metric,
                "alignment": alignment,
            },
            analysis_purpose=analysis_purpose,
            attributes=attrs,
        ):
            return compare(
                current_metric,
                baseline_metric,
                alignment=alignment,
                analysis_purpose=analysis_purpose,
                session=self,
            )

    def attribute(
        self,
        frame: DeltaFrame,
        *,
        axes: list[_SemanticInput[DimensionKind | TimeDimensionKind]],
        mode: AttributionMode | None = None,
        top_k: int | None = None,
        target: FunnelLossRate | None = None,
        analysis_purpose: str | None = None,
    ) -> AttributionFrame:
        """Attribute a DeltaFrame's movement over explicit deterministic axes.

        When to use: after observe -> compare, compute deterministic
        contribution rows for explicit axes selected by the caller. If a
        requested axis is missing from the input DeltaFrame, Marivo attempts to
        replay the source observe/compare lineage with the extra axis and fails
        closed when replay is not recoverable.
        For a current cumulative delta, business dimensions replay cumulative
        endpoint levels. Exactly the cumulative ``over`` time dimension uses
        the additive base-flow bridge and emits a distinct temporal row
        contract with exact intervals, source side, effect kind, and per-parent
        reconciliation. Mixing the ``over`` axis with business dimensions is
        blocked; derived component time bridges and cumulative count-distinct
        bases are also blocked by the persisted route map.
        For multiple axes on a metric delta, omitting ``mode`` defaults to
        ``"joint"`` and returns one additive row per complete axis combination.
        Choose ``mode="hierarchy"`` for prefix-level drill-down rows; hierarchy
        rows repeat parent totals, so only the deepest level is additive.
        A single-axis result preserves the concrete dimension column name, so
        its pandas rows can join directly to the source DeltaFrame on that
        dimension. Generic ``driver`` and ``path`` columns are reserved for
        multi-axis hierarchy rows.
        The concrete name must not collide with attribution result, value, or
        panel bucket columns; such a collision fails closed with a
        structured semantic-authoring repair instead of producing duplicate or
        ambiguous columns. Evidence protocol fields are mapped explicitly and
        do not reserve user dimension names.
        Additive deltas support axis-sum attribution. Semi-additive deltas
        support non-time axes but reject their persisted status time axis.
        Component-aware ratio and weighted-mean deltas use mix attribution.
        Tier-1 means over a measure are observed with exact sum and non-null
        count components, then use weighted mix attribution. Other non-additive
        metrics, non-additive linear compositions, and deltas missing persisted
        additivity metadata fail closed. Re-observe and compare old artifacts
        before retrying attribution.
        Plain non-linear sampled folds such as percentile, min, max, first, or
        last retain their earlier guard unless they are part of a persisted
        component-aware ratio or weighted-mean delta.
        Every contribution row exposes ``share_of_total_delta`` plus neutral
        positive- and negative-contribution pool shares. Marivo does not label
        either pool as improvement or degradation because metric desirability
        is not part of the persisted metric contract. New and churned component
        segments receive exact one-sided contributions. The result metadata and
        ``show()`` card expose total, contribution, one-sided, unattributed, and
        residual reconciliation facts; attribution fails closed if a deepest
        partition does not reconcile within numeric tolerance.

        Args:
            frame: A DeltaFrame produced by ``session.compare``.
            axes: One or more exact current-catalog dimension/time-dimension
                entries or refs to attribute over.
            mode: For metric deltas, defaults to ``"joint"`` when multiple axes
                are supplied. ``"hierarchy"`` returns ordered prefix rows;
                typed resolution evidence states whether they are rollup-safe.
                Omit for a single axis. Funnel deltas still require an explicit
                ``"joint"`` or ``"hierarchy"`` mode for multiple axes.
            top_k: Optional positive number of named members retained per parent.
                Remaining members become one governed Other player selected once
                over the complete current-plus-baseline comparison scope.
            target: Required only for ``DeltaFrame[funnel]``; pass one exact
                ``mv.funnel_loss_rate(step=...)`` target.
            analysis_purpose: Optional durable label explaining why this
                attribution was produced.

        Guidance:
            Funnel attribution accepts only an ungrouped DeltaFrame[funnel],
            introduces governed driver axes over persisted journey membership,
            and never rematches Events. Ratio-mix emits additive ``loss`` and
            ``denominator_mix`` components with exact reconciliation. These are
            arithmetic contributions to an observed change, not causal claims.

        Returns:
            An AttributionFrame with dimension, reconciled contribution, and
            share columns.

        Raises:
            SemanticKindMismatchError: ``frame`` is not a DeltaFrame, axes are
                missing, contain duplicates, or use an invalid multi-axis mode.
            AttributionMaterializationError: A requested axis is missing from
                the DeltaFrame and replay is not recoverable.
            AttributionAdditivityError: Persisted metric additivity is missing
                or incompatible with the requested attribution axes.
            CrossSessionFrameError: A frame belongs to a different session.

        Example:
            >>> delta = session.compare(cur, base, alignment=mv.window_bucket())
            >>> country = session.catalog.dimensions.get("sales.orders.country")
            >>> channel = session.catalog.dimensions.get("sales.orders.channel")
            >>> attribution = session.attribute(
            ...     delta,
            ...     axes=[country, channel],
            ...     analysis_purpose="按国家归因收入变化",
            ... )
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.errors import (
            AnalysisRepair,
            FunnelAttributionUnsupportedError,
            SemanticKindMismatchError,
        )
        from marivo.analysis.intents._attribution_topk import validate_top_k
        from marivo.introspection.live.model import LiveHelpTarget

        validate_capability_inputs("attribute", session=self, frame=frame)
        validated_top_k = validate_top_k(top_k)
        if frame.meta.semantic_kind == "funnel":
            if validated_top_k is not None:
                raise SemanticKindMismatchError(
                    message="attribute top_k is not applicable to funnel attribution",
                    context={"argument": "top_k", "reason": "top_k_not_applicable"},
                )
            from marivo.analysis.intents.funnel_attribute import (
                attribute_funnel,
                validate_funnel_attribute_admission,
            )

            validate_funnel_attribute_admission(frame, session=self)

            with _track_materializing_operation(
                self,
                "marivo.analysis.attribute.funnel",
                capability_id="attribute",
                family="events",
                intent="attribute",
                arguments={
                    "frame": frame,
                    "axes": axes,
                    "mode": mode,
                    "target": target,
                },
                analysis_purpose=analysis_purpose,
                attributes={"marivo.analysis.axis_count": len(axes)},
            ):
                return attribute_funnel(
                    frame,
                    axes=axes,
                    mode=mode,
                    target=target,
                    analysis_purpose=analysis_purpose,
                    session=self,
                )
        if target is not None:
            raise FunnelAttributionUnsupportedError(
                message="a funnel target requires a DeltaFrame[funnel]",
                expected=(
                    "session.attribute(<DeltaFrame[funnel]>, target=mv.funnel_loss_rate(...))"
                ),
                received=f"DeltaFrame[{frame.meta.semantic_kind}]",
                location="session.attribute(target)",
                repair=AnalysisRepair(
                    kind="user_choice",
                    action=(
                        "Compare two compatible EventFrame[funnel] artifacts to produce "
                        "a DeltaFrame[funnel], or omit target for a Metric delta."
                    ),
                    help_target=LiveHelpTarget(
                        surface="analysis",
                        canonical_id="attribute",
                    ),
                ),
            )
        from marivo.analysis.intents.attribute import attribute

        semantic_kind = getattr(frame.meta, "semantic_kind", None)
        attrs: dict[str, str | int | float | bool] = {"marivo.analysis.axis_count": len(axes)}
        if isinstance(semantic_kind, str):
            attrs["marivo.analysis.semantic_kind"] = semantic_kind
        effective_mode = mode
        if effective_mode is None and len(axes) > 1:
            effective_mode = "joint"
        if effective_mode is not None:
            attrs["marivo.analysis.attribution_mode"] = effective_mode
        if validated_top_k is not None:
            attrs["marivo.analysis.top_k"] = validated_top_k
        with _track_materializing_operation(
            self,
            "marivo.analysis.attribute",
            capability_id="attribute",
            family="core",
            intent="attribute",
            arguments={
                "frame": frame,
                "axes": axes,
                "mode": effective_mode,
                "top_k": validated_top_k,
            },
            analysis_purpose=analysis_purpose,
            attributes=attrs,
        ):
            return attribute(
                frame,
                axes=axes,
                mode=effective_mode,
                top_k=validated_top_k,
                analysis_purpose=analysis_purpose,
                session=self,
            )

    def correlate(
        self,
        a: MetricFrame,
        b: MetricFrame,
        *,
        measure_a: str | None = None,
        measure_b: str | None = None,
        alignment: AlignmentPolicy | None = None,
        method: Literal["pearson", "spearman", "kendall"] = "pearson",
        lag_range: range | Sequence[int] | None = None,
        analysis_purpose: str | None = None,
    ) -> AssociationResult:
        """Measure the association between two MetricFrames over aligned buckets.

        When to use: measure statistical association between two metrics over aligned time buckets.

        Supports Pearson (linear), Spearman (monotonic rank), and Kendall (ordinal
        concordance) correlation under ``window_bucket`` alignment. ``lag_range``
        explores delayed associations: each lag pairs ``a[t]`` with ``b[t+lag]``;
        positive lag means ``a`` leads ``b`` and negative lag means ``b`` leads
        ``a``. Non-zero lag requires time-series or panel inputs; panel shifts stay
        within each dimension series, and null pairs are dropped after shifting.
        The result carries one row per lag and ``meta.best_lag`` (also exposed as
        ``meta.selected_lag_offset``) marks the single lag the summary/evidence
        represents. The selected lag is the one with the strongest absolute
        correlation, preferring the closest lag on ties
        (``meta.selection_rule == "max_abs_correlation_closest_lag"``). Default is
        lag 0 only (``meta.selection_rule == "single_lag"``). Both frames must
        belong to the active session.

        Alignment keys are taken exclusively from the time and dimension axes
        declared on both frames; common columns that are not declared as axes are
        never inferred as alignment keys. With no shared declared axes the frames
        are aligned positionally.

        Args:
            a: First MetricFrame.
            b: Second MetricFrame.
            measure_a: Public value column from ``a.value_columns``. Defaults to
                the frame's unique metric value column.
            measure_b: Public value column from ``b.value_columns``. Defaults to
                the frame's unique metric value column.
            alignment: Defaults to ``mv.window_bucket()``.
            method: ``"pearson"``, ``"spearman"``, or ``"kendall"``.
            lag_range: Signed lags to explore for time-series or panel inputs
                (e.g. ``range(-3, 4)``). Defaults to lag 0.

        Raises:
            SemanticKindMismatchError: Inputs are not MetricFrames, or alignment
                kinds are unsupported.
            AlignmentFailedError: Frames cannot be aligned (e.g. no overlapping buckets).
            CrossSessionFrameError: A frame belongs to a different session.

        Example:
            >>> # lag=k pairs a[t] with b[t+k]; positive means a leads b.
            >>> result = session.correlate(
            ...     a, b,
            ...     measure_a=a.value_columns[0],
            ...     measure_b=b.value_columns[0],
            ...     alignment=mv.window_bucket(),
            ...     lag_range=range(-3, 4),
            ...     analysis_purpose="验证收入和订单量是否同向变化",
            ... )
            >>> result.show()
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.correlate import correlate

        semantic_kind = getattr(a.meta, "semantic_kind", None)
        attrs: dict[str, str | int | float | bool] | None = (
            {"marivo.analysis.semantic_kind": semantic_kind}
            if isinstance(semantic_kind, str)
            else None
        )
        validate_capability_inputs("correlate", a=a, b=b, alignment=alignment)
        with _track_materializing_operation(
            self,
            "marivo.analysis.correlate",
            capability_id="correlate",
            family="core",
            intent="correlate",
            arguments={
                "a": a,
                "b": b,
                "measure_a": measure_a,
                "measure_b": measure_b,
                "alignment": alignment,
                "method": method,
                "lag_range": tuple(lag_range) if lag_range is not None else None,
            },
            analysis_purpose=analysis_purpose,
            attributes=attrs,
        ):
            return correlate(
                a,
                b,
                measure_a=measure_a,
                measure_b=measure_b,
                alignment=alignment,
                method=method,
                lag_range=lag_range,
                analysis_purpose=analysis_purpose,
                session=self,
            )

    def forecast(
        self,
        history: MetricFrame,
        *,
        horizon: int,
        model: Literal["naive", "seasonal_naive", "drift"] = "seasonal_naive",
        seasonality_period: int | None = None,
        interval_level: float = 0.95,
        measure_column: str | None = None,
        analysis_purpose: str | None = None,
    ) -> ForecastFrame:
        """Project a time_series or panel MetricFrame forward by ``horizon`` periods.

        When to use: project a time series forward; requires time_series or panel shape.

        Built-in day/week/month/quarter histories retain their fixed-calendar
        behavior. A history observed with a certified semantic calendar uses
        that exact period binding: periods must be complete, consecutive, and
        shared by every panel series, while ``horizon`` counts certified period
        ordinals and cannot exceed the snapshot's coverage. ``naive`` and
        ``drift`` operate on ordinal steps; semantic ``seasonal_naive`` requires
        an explicit ``seasonality_period > 1``. Forecast never treats a missing
        segment period as zero, guesses a future boundary, or substitutes the
        current snapshot for the history binding. Impute or re-observe before
        forecasting.

        Args:
            history: A ``time_series`` or ``panel`` MetricFrame.
            horizon: Number of exact periods to project. Must be >= 1.
            model: Forecast strategy. Semantic ``seasonal_naive`` needs an explicit
                ``seasonality_period``.
            seasonality_period: Seasonal ordinal distance. Built-in grains retain
                defaults (day=7, week=52, month=12, quarter=4); semantic grains do not.
            interval_level: Confidence level for prediction intervals. Must be in (0, 1).
            measure_column: Public value column from ``history.value_columns``.
                Defaults to the frame's unique metric value column.

        Raises:
            ForecastShapeUnsupportedError: ``history`` is not a time_series / panel MetricFrame,
                its binding is unsupported or unavailable, its certified periods are
                incomplete/non-consecutive, or the requested semantic horizon exceeds coverage.
            ForecastPolicyError: ``horizon`` or ``interval_level`` is out of range.
            ForecastInsufficientHistoryError: Not enough rows for the chosen model.
            ForecastInputQualityError: ``history`` contains NaN values or missing
                time buckets globally or within a panel series.
            CrossSessionFrameError: ``history`` belongs to a different session.

        Example:
            >>> history = session.observe(
            ...     session.catalog.require(ms.ref.metric("sales.revenue")),
            ...     time_scope=mv.time_scope(start="2026-01-01", end="2026-04-01"),
            ...     grain=mv.grain("day"),
            ... )
            >>> forecast = session.forecast(
            ...     history,
            ...     horizon=30,
            ...     measure_column=history.value_columns[0],
            ...     analysis_purpose="预测未来 30 天收入走势",
            ... )
            >>> forecast.show()

        Guidance:
            A semantic history carries one exact certified period binding. ``naive``
            and ``drift`` advance by period ordinal; ``seasonal_naive`` requires an
            explicit ``seasonality_period > 1``. Re-observe complete consecutive
            periods when a snapshot, key, boundary, or coverage check fails.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.forecast import forecast, validate_forecast_admission

        semantic_kind = getattr(history.meta, "semantic_kind", None)
        attrs: dict[str, str | int | float | bool] = {
            "marivo.analysis.horizon": horizon,
            "marivo.analysis.forecast_model": model,
        }
        if isinstance(semantic_kind, str):
            attrs["marivo.analysis.semantic_kind"] = semantic_kind
        validate_capability_inputs("forecast", history=history)
        validate_forecast_admission(
            history,
            horizon=horizon,
            model=model,
            seasonality_period=seasonality_period,
            interval_level=interval_level,
            session=self,
        )
        with _track_materializing_operation(
            self,
            "marivo.analysis.forecast",
            capability_id="forecast",
            family="core",
            intent="forecast",
            arguments={
                "history": history,
                "horizon": horizon,
                "model": model,
                "seasonality_period": seasonality_period,
                "interval_level": interval_level,
                "measure_column": measure_column,
            },
            analysis_purpose=analysis_purpose,
            attributes=attrs,
        ):
            return forecast(
                history,
                horizon=horizon,
                model=model,
                seasonality_period=seasonality_period,
                interval_level=interval_level,
                measure_column=measure_column,
                analysis_purpose=analysis_purpose,
                session=self,
            )

    def hypothesis_test(
        self,
        a: MetricFrame,
        b: MetricFrame,
        *,
        hypothesis: Literal["mean_changed"] = "mean_changed",
        value_a: str | None = None,
        value_b: str | None = None,
        alignment: AlignmentPolicy | None = None,
        sampling: SamplingPolicy | None = None,
        alpha: float = 0.05,
        analysis_purpose: str | None = None,
    ) -> HypothesisTestResult:
        """Run a paired hypothesis test over two compatible MetricFrames.

        When to use: statistically validate whether a metric changed between two periods.

        v1 only supports ``hypothesis="mean_changed"`` under ``window_bucket``
        alignment. Scalar MetricFrames are not testable. ``a`` and ``b`` must share
        ``semantic_kind`` and ``semantic_model``; ``sampling.pairing`` must match
        the frame shape (``segment_key`` for segmented, ``window_bucket`` for
        time_series / panel).

        Args:
            a: Current MetricFrame.
            b: Baseline MetricFrame.
            hypothesis: Only ``"mean_changed"`` in v1.
            value_a: Public value column from ``a.value_columns``. Defaults to
                the frame's unique metric value column.
            value_b: Public value column from ``b.value_columns``. Defaults to
                the frame's unique metric value column.
            alignment: Defaults to ``mv.window_bucket()``.
            sampling: Defaults to ``SamplingPolicy()`` (pairing inferred from shape).
            alpha: Significance level in (0, 0.5].

        Raises:
            SemanticKindMismatchError: Inputs are not MetricFrames, or differ in
                ``semantic_kind`` / ``semantic_model``.
            TestPolicyError: ``hypothesis`` / ``alpha`` / ``alignment.kind`` is unsupported.
            TestAlignmentError: Frames cannot be paired under the alignment.
            TestShapeNotTestableError: Frame shape is scalar or otherwise untestable.
            CrossSessionFrameError: A frame belongs to a different session.

        Example:
            >>> result = session.hypothesis_test(
            ...     cur,
            ...     base,
            ...     value_a=cur.value_columns[0],
            ...     value_b=base.value_columns[0],
            ...     analysis_purpose="验证收入变化是否统计显著",
            ... )
            >>> result.show()
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.hypothesis_test import hypothesis_test

        semantic_kind = getattr(a.meta, "semantic_kind", None)
        attrs: dict[str, str | int | float | bool] | None = (
            {"marivo.analysis.semantic_kind": semantic_kind}
            if isinstance(semantic_kind, str)
            else None
        )
        validate_capability_inputs(
            "hypothesis_test",
            a=a,
            b=b,
            alignment=alignment,
            sampling=sampling,
        )
        with _track_materializing_operation(
            self,
            "marivo.analysis.hypothesis_test",
            capability_id="hypothesis_test",
            family="core",
            intent="hypothesis_test",
            arguments={
                "a": a,
                "b": b,
                "hypothesis": hypothesis,
                "value_a": value_a,
                "value_b": value_b,
                "alignment": alignment,
                "sampling": sampling,
                "alpha": alpha,
            },
            analysis_purpose=analysis_purpose,
            attributes=attrs,
        ):
            return hypothesis_test(
                a,
                b,
                hypothesis=hypothesis,
                value_a=value_a,
                value_b=value_b,
                alignment=alignment,
                sampling=sampling,
                alpha=alpha,
                analysis_purpose=analysis_purpose,
                session=self,
            )


def ensure_session_can_execute(session: Session) -> None:
    """Raise ``NoBackendFactoryError`` when the session has no backend factory."""
    from marivo.analysis.errors import NoBackendFactoryError

    if session.is_read_only:
        raise NoBackendFactoryError(
            message=f"session '{session.name}' has no backend factory configured",
            context={"session_id": session.id, "session_name": session.name},
        )


@dataclass(frozen=True, repr=False)
class SessionEvents(RenderableResult):
    """Session-bound Event inspection, Journey materialization, and reducers."""

    _session: Session

    def _repr_identity(self) -> str:
        return f"SessionEvents session={self._session.id}"

    def _card(self) -> Card:
        from marivo.analysis._capabilities.registry import REGISTRY

        _properties, methods = REGISTRY.public_object_members("SessionEvents")
        intrinsic_methods = tuple(method for method in methods if method in {"show"})
        registered_calls = tuple(
            call
            for call in REGISTRY.public_member_calls("SessionEvents")
            if call not in {".render()", ".show()"}
        )
        return Card(
            identity=self._repr_identity(),
            available=(
                *(f".{method_name}()" for method_name in intrinsic_methods),
                *registered_calls,
            ),
        ).status("phase=event_reducers")

    def watermark(
        self,
        event: _SemanticInput[EventKind],
        *,
        through: str,
    ) -> EventWatermarkReceipt | None:
        """Return the authoritative observed completeness watermark for one Event.

        When to use: prefer an observed watermark over a caller declaration.

        Resolves one exact current-catalog Event's catalog facts, asks the
        session's backend completeness provider for that Event's datasource, and
        returns the provider's authoritative ``EventWatermarkReceipt`` (or
        ``None`` when no provider exists, or the provider has no authoritative
        watermark for this exact Event). ``lifecycle.replay`` and
        ``events.match`` consume this same receipt through their authoritative
        coverage resolution, so a non-``None`` receipt here is exactly what
        makes an observed coverage authoritative downstream.

        Args:
            event: Current-catalog ``EventEntry`` or exact ``Ref[event]``.
            through: Inclusive completeness bound the caller requires the Event
                to be complete through.

        Returns:
            The provider's authoritative ``EventWatermarkReceipt`` when one
            exists, otherwise ``None``.

        Raises:
            SemanticKindMismatchError: ``event`` is not one exact current-catalog
                Event entry or ref.

        Guidance:
            This is an observed fact from a backend completeness provider, not a
            caller assumption. It is strictly stronger than
            ``mv.declared_complete_through(...)``. A non-``None``
            ``EventWatermarkReceipt`` is exactly what ``lifecycle.replay`` and
            ``events.match`` consume through their authoritative coverage
            resolution. When ``None`` is returned, fall back to an explicit
            governed declaration only when you can supply a rationale.

        Example:
            >>> order_created = session.catalog.events.get("commerce.order_created")
            >>> watermark = session.events.watermark(
            ...     order_created,
            ...     through="2026-08-01T00:00:00Z",
            ... )
            >>> if watermark is None:
            ...     coverage = mv.declared_complete_through(
            ...         inputs=(order_created.ref,),
            ...         through="2026-08-01T00:00:00Z",
            ...         rationale="Reconciled through the follow-up bound.",
            ...     )
            ... else:
            ...     print(watermark.complete_through)
        """
        from marivo.analysis.intents.watermark import watermark

        with _track_session_operation(
            self._session,
            "marivo.analysis.events.watermark",
            family="events",
            intent="events.watermark",
        ):
            return watermark(event, through=through, session=self._session)

    def occurrence_bounds(
        self,
        event_or_model: _SemanticInput[EventKind | StateModelKind],
    ) -> EventOccurrenceBounds:
        """Return observed occurrence-time bounds for one Event or StateModel.

        When to use: inspect exact Event occurrence boundaries before choosing a
        replay or matching window.

        Args:
            event_or_model: Current-catalog ``EventEntry`` / ``Ref[event]`` or
                ``StateModelEntry`` / ``Ref[state_model]``. A StateModel
                automatically contributes its exact inception and transition
                Events.

        Returns:
            ``EventOccurrenceBounds`` with the exact Event refs and
            UTC-normalized earliest/latest occurrence instants. Both bounds are
            ``None`` when none of the target's Events has an occurrence. A
            StateModel with no Event triggers returns an empty ``event_refs``
            tuple and both bounds absent.

        Raises:
            SemanticKindMismatchError: ``event_or_model`` is not one exact
                current-catalog Event or StateModel entry/ref.

        Guidance:
            This performs bounded-result scalar aggregation over exact Event
            predicates. It never reports a Datasource-wide maximum and does not
            prove completeness. After choosing a candidate upper bound, use
            ``session.events.watermark(event, through=...)`` or the consuming
            operation's coverage result to establish completeness.

        Example:
            >>> lifecycle = session.catalog.state_models.get("commerce.order_lifecycle")
            >>> bounds = session.events.occurrence_bounds(lifecycle)
            >>> print(bounds.latest_occurrence_at)
        """
        from marivo.analysis.intents.event_occurrence_bounds import occurrence_bounds

        with _track_session_operation(
            self._session,
            "marivo.analysis.events.occurrence_bounds",
            family="events",
            intent="events.occurrence_bounds",
        ):
            return occurrence_bounds(event_or_model, session=self._session)

    def match(
        self,
        *,
        pattern: EventPattern,
        cohort_window: TimeScope,
        completion_through: str,
        matching: EventMatchingPolicy,
        completeness: tuple[CompletenessDeclaration, ...] = (),
        cohort: SubjectSet | None = None,
        analysis_purpose: str | None = None,
    ) -> EventFrame:
        """Match typed Event occurrences into dense subject journeys.

        ``cohort_window`` is half-open: only first-step occurrences in
        ``[start, end)`` establish journeys. ``completion_through`` is an
        inclusive follow-up bound. Missing steps are ``incomplete`` only when
        every pattern Event has authoritative or declared coverage; otherwise
        they are ``coverage_censored``.

        Args:
            pattern: Non-empty typed sequence built with ``mv.step`` and
                ``mv.sequence``. Every participant endpoint must be the same
                Entity.
            cohort_window: Half-open first-step cohort window.
            completion_through: Inclusive follow-up bound at or after the
                cohort window end.
            matching: ``mv.first_per_subject()`` or an explicit
                ``mv.every_start(completion_assignment=...)`` policy.
            completeness: Optional exact Event completeness declarations.
            cohort: Optional ready ``SubjectSet`` with the exact pattern subject.
            analysis_purpose: Optional business purpose retained in lineage.

        Returns:
            A persisted ``EventFrame[journey]`` with one dense row for every
            journey and pattern step.

        Guidance:
            ``completion_through`` requests the latest follow-up instant used
            for matching and coverage checks; it never proves that input data
            is complete through that instant. Prefer an observed backend
            watermark (obtain one with ``session.events.watermark(...)``). Use
            ``mv.declared_complete_through(...)`` only for an explicit governed
            assumption with a rationale.

        Example:
            >>> cart_created = session.catalog.events.get("commerce.cart_created")
            >>> payment_succeeded = session.catalog.events.get("commerce.payment_succeeded")
            >>> cart_user = ms.participant_role(event=cart_created.ref, name="user")
            >>> payment_buyer = ms.participant_role(event=payment_succeeded.ref, name="buyer")
            >>> pattern = mv.sequence(
            ...     mv.step(participant=cart_user, key="cart"),
            ...     mv.step(participant=payment_buyer, key="payment"),
            ... )
            >>> journeys = session.events.match(
            ...     pattern=pattern,
            ...     cohort_window=mv.time_scope(
            ...         start="2026-07-01T00:00:00Z",
            ...         end="2026-07-08T00:00:00Z",
            ...     ),
            ...     completion_through="2026-07-15T00:00:00Z",
            ...     matching=mv.first_per_subject(),
            ... )

        Constraints:
            Step subjects are inferred from cardinality-one participant
            endpoints and their Entity primary keys. Same-time occurrences
            from different EventRefs are rejected when their order would
            affect matching.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.events import match

        validate_capability_inputs(
            "events.match",
            session=self._session,
            pattern=pattern,
            cohort_window=cohort_window,
            matching=matching,
            completeness=completeness,
            cohort=cohort,
        )
        with _track_materializing_operation(
            self._session,
            "marivo.analysis.events.match",
            capability_id="events.match",
            family="events",
            intent="events.match",
            arguments={
                "pattern": pattern,
                "cohort_window": cohort_window,
                "completion_through": completion_through,
                "matching": matching,
                "completeness": completeness,
                "cohort": cohort,
            },
            analysis_purpose=analysis_purpose,
            attributes={
                "marivo.analysis.event_step_count": len(pattern.steps),
                "marivo.analysis.event_matching": matching.kind,
            },
        ):
            return match(
                pattern=pattern,
                cohort_window=cohort_window,
                completion_through=completion_through,
                matching=matching,
                completeness=completeness,
                cohort=cohort,
                analysis_purpose=analysis_purpose,
                session=self._session,
            )

    def funnel(
        self,
        journeys: EventFrame,
        *,
        axes: Sequence[_SemanticInput[DimensionKind]] = (),
        analysis_purpose: str | None = None,
    ) -> EventFrame:
        """Reduce first-per-subject journeys into a reconciled Event funnel.

        With ``axes=()`` this reads only the persisted journey artifact. Each
        declared Dimension axis is enriched at the first-step occurrence time
        through one governed, fanout-safe subject path.

        Args:
            journeys: Exact same-session ``EventFrame[journey]`` matched with
                ``mv.first_per_subject()``.
            axes: Current-catalog Dimension entries or exact Dimension refs.
            analysis_purpose: Optional business purpose retained in lineage.

        Returns:
            A persisted ``EventFrame[funnel]`` with exact additive counts,
            censoring-aware rates, and grouped reconciliation evidence.

        Example:
            >>> acquisition_channel = session.catalog.dimensions.get(
            ...     "commerce.orders.acquisition_channel"
            ... )
            >>> funnel = session.events.funnel(
            ...     journeys,
            ...     axes=[acquisition_channel],
            ...     analysis_purpose="Measure checkout conversion by entry channel.",
            ... )
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.event_reducers import funnel

        validate_capability_inputs(
            "events.funnel",
            session=self._session,
            journeys=journeys,
            axes=axes,
        )
        with _track_materializing_operation(
            self._session,
            "marivo.analysis.events.funnel",
            capability_id="events.funnel",
            family="events",
            intent="events.funnel",
            arguments={"journeys": journeys, "axes": axes},
            analysis_purpose=analysis_purpose,
        ):
            return funnel(
                journeys,
                axes=axes,
                analysis_purpose=analysis_purpose,
                session=self._session,
            )

    def time_to_event(
        self,
        journeys: EventFrame,
        *,
        start_step: PatternStep,
        end_step: PatternStep,
        axes: Sequence[object] = (),
        analysis_purpose: str | None = None,
    ) -> EventFrame:
        """Project persisted Event assignments into exact elapsed durations.

        The reducer never queries or rematches Event inputs. ``start_step`` and
        ``end_step`` must be the exact typed steps retained by the source
        pattern, and the start must precede the end. ``axes`` may carry
        governed subject Dimensions to group elapsed durations (each journey
        subject receives one deterministic cohort-entry axis tuple).

        Args:
            journeys: Exact same-session ``EventFrame[journey]``.
            start_step: Exact reached step from the persisted source pattern.
            end_step: Exact later step from the persisted source pattern.
            axes: Optional governed subject Dimensions retained per row.
            analysis_purpose: Optional business purpose retained in lineage.

        Returns:
            A persisted ``EventFrame[time_to_event]`` with one row per source
            journey that reached ``start_step``.

        Example:
            >>> start_step, end_step = journeys.meta.pattern.steps[:2]
            >>> elapsed = session.events.time_to_event(
            ...     journeys,
            ...     start_step=start_step,
            ...     end_step=end_step,
            ...     analysis_purpose="Measure checkout-to-payment elapsed time.",
            ... )
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.event_reducers import time_to_event

        validate_capability_inputs(
            "events.time_to_event",
            session=self._session,
            journeys=journeys,
            start_step=start_step,
            end_step=end_step,
            axes=axes,
        )
        with _track_materializing_operation(
            self._session,
            "marivo.analysis.events.time_to_event",
            capability_id="events.time_to_event",
            family="events",
            intent="events.time_to_event",
            arguments={
                "journeys": journeys,
                "start_step": start_step,
                "end_step": end_step,
                "axes": axes,
            },
            analysis_purpose=analysis_purpose,
        ):
            return time_to_event(
                journeys,
                start_step=start_step,
                end_step=end_step,
                axes=axes,
                analysis_purpose=analysis_purpose,
                session=self._session,
            )


@dataclass(frozen=True, repr=False)
class SessionLifecycle(RenderableResult):
    """Session-bound replay-based Lifecycle operators."""

    _session: Session

    def _repr_identity(self) -> str:
        return f"SessionLifecycle session={self._session.id}"

    def _card(self) -> Card:
        from marivo.analysis._capabilities.registry import REGISTRY

        _properties, methods = REGISTRY.public_object_members("SessionLifecycle")
        intrinsic_methods = tuple(method for method in methods if method in {"show"})
        registered_calls = tuple(
            call
            for call in REGISTRY.public_member_calls("SessionLifecycle")
            if call not in {".render()", ".show()"}
        )
        return Card(
            identity=self._repr_identity(),
            available=(
                *(f".{method_name}()" for method_name in intrinsic_methods),
                *registered_calls,
            ),
        ).status("phase=lifecycle_replay")

    def replay(
        self,
        model: _SemanticInput[StateModelKind],
        *,
        window: TimeScope,
        seed: FromInception,
        completeness: tuple[CompletenessDeclaration, ...] = (),
        cohort: SubjectSet | None = None,
        analysis_purpose: str | None = None,
    ) -> LifecycleFrame:
        """Replay one StateModel from its explicit inception seed.

        State is reconstructed from the first qualifying inception, which may
        precede ``window``; only the resulting intervals are clipped to the
        half-open ``[start, end)`` window. Each modeled Event is queried once
        however many triggers it serves, and Events outside the StateModel are
        never read.

        Args:
            model: Current-catalog ``StateModelEntry`` or exact
                ``Ref[state_model]`` declaring at least one inception trigger.
            window: Half-open timezone-aware replay output window.
            seed: ``mv.from_inception()``; replay has no default seed.
            completeness: Optional exact declarations covering only Events used
                by the current StateModel triggers.
            cohort: Optional ready ``SubjectSet`` over the model subject Entity.
            analysis_purpose: Optional business purpose retained in lineage.

        Returns:
            A persisted ``LifecycleFrame[history]`` with one row per clipped
            state interval, bound to a private fixed-contract violation trace.

        Guidance:
            Use ``session.events.occurrence_bounds(model)`` to inspect the exact
            modeled Event range before choosing ``window``. Violation handling
            is the fixed v1 replay contract rather than a
            policy slot: an occurrence that no modeled transition admits
            records a violation-trace row and leaves state unchanged, and
            modeled occurrences before inception are ignored rather than
            counted as violations. Completeness governs censoring, not
            correctness — without an authoritative watermark or a declaration
            covering ``window.end``, open intervals are ``coverage_censored``
            and subjects with no observed inception are censored instead of
            failing. Prefer an observed watermark (obtain one with
            ``session.events.watermark(...)``); use
            ``mv.declared_complete_through(...)`` only as an explicit governed
            assumption with a rationale.

        Example:
            >>> order_lifecycle = session.catalog.state_models.get(
            ...     "commerce.order_lifecycle"
            ... )
            >>> history = session.lifecycle.replay(
            ...     order_lifecycle,
            ...     window=mv.time_scope(
            ...         start="2026-07-01T00:00:00Z",
            ...         end="2026-08-01T00:00:00Z",
            ...     ),
            ...     seed=mv.from_inception(),
            ...     analysis_purpose="Read order state duration before the price change.",
            ... )

        Constraints:
            Subject identity comes from the StateModel subject Entity primary
            key. Same-time occurrences of different modeled Events are
            rejected when their order would change state or violation
            classification.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.lifecycle import replay, validate_replay_admission

        validate_capability_inputs(
            "lifecycle.replay",
            session=self._session,
            window=window,
            completeness=completeness,
            cohort=cohort,
        )
        validate_replay_admission(
            model,
            window=window,
            seed=seed,
            completeness=completeness,
            cohort=cohort,
            session=self._session,
        )
        with _track_materializing_operation(
            self._session,
            "marivo.analysis.lifecycle.replay",
            capability_id="lifecycle.replay",
            family="lifecycle",
            intent="lifecycle.replay",
            arguments={
                "model": model,
                "window": window,
                "seed": seed,
                "completeness": completeness,
                "cohort": cohort,
            },
            analysis_purpose=analysis_purpose,
        ):
            return replay(
                model,
                window=window,
                seed=seed,
                completeness=completeness,
                cohort=cohort,
                analysis_purpose=analysis_purpose,
                session=self._session,
            )

    def distribution(
        self,
        history: LifecycleFrame,
        *,
        at: Sequence[str],
        axes: Sequence[_SemanticInput[DimensionKind]] = (),
        analysis_purpose: str | None = None,
    ) -> LifecycleFrame:
        """Reduce replay history into dense point-in-time state distributions.

        Args:
            history: Exact same-session ``LifecycleFrame[history]``.
            at: Non-empty timezone-aware instants inside the replay window.
            axes: Current-catalog Dimension entries or exact Dimension refs
                reachable from the subject through one to-one path.
            analysis_purpose: Optional business purpose retained in lineage.

        Returns:
            A persisted ``LifecycleFrame[distribution]`` that is dense over
            modeled states and reconciles exactly to ungrouped counts.

        Example:
            >>> spread = session.lifecycle.distribution(
            ...     history,
            ...     at=("2026-07-15T00:00:00Z",),
            ... )

        Constraints:
            Reads only the committed history artifact; axis enrichment queries
            governed subject Dimensions and never rereads Events.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.lifecycle_reducers import distribution

        validate_capability_inputs(
            "lifecycle.distribution",
            session=self._session,
            history=history,
            axes=axes,
        )
        with _track_materializing_operation(
            self._session,
            "marivo.analysis.lifecycle.distribution",
            capability_id="lifecycle.distribution",
            family="lifecycle",
            intent="lifecycle.distribution",
            arguments={"history": history, "at": at, "axes": axes},
            analysis_purpose=analysis_purpose,
        ):
            return distribution(
                history,
                at=at,
                axes=axes,
                analysis_purpose=analysis_purpose,
                session=self._session,
            )

    def transitions(
        self,
        history: LifecycleFrame,
        *,
        analysis_purpose: str | None = None,
    ) -> LifecycleFrame:
        """Count dense modeled transitions from one committed replay history.

        Args:
            history: Exact same-session ``LifecycleFrame[history]``.
            analysis_purpose: Optional business purpose retained in lineage.

        Returns:
            A persisted ``LifecycleFrame[transitions]`` dense over the distinct
            modeled state pairs, including zero counts, in declared order.

        Example:
            >>> moves = session.lifecycle.transitions(history)

        Constraints:
            Reads only the committed history artifact. Illegal occurrences are
            not transitions; read them with ``session.lifecycle.violations``.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.lifecycle_reducers import transitions

        validate_capability_inputs("lifecycle.transitions", history=history)
        with _track_materializing_operation(
            self._session,
            "marivo.analysis.lifecycle.transitions",
            capability_id="lifecycle.transitions",
            family="lifecycle",
            intent="lifecycle.transitions",
            arguments={"history": history},
            analysis_purpose=analysis_purpose,
        ):
            return transitions(
                history,
                analysis_purpose=analysis_purpose,
                session=self._session,
            )

    def dwell(
        self,
        history: LifecycleFrame,
        *,
        analysis_purpose: str | None = None,
    ) -> LifecycleFrame:
        """Summarize completed and censored interval dwell by modeled state.

        Args:
            history: Exact same-session ``LifecycleFrame[history]``.
            analysis_purpose: Optional business purpose retained in lineage.

        Returns:
            A persisted ``LifecycleFrame[dwell]`` with one row per modeled
            state and separate completed, right-censored, and
            coverage-censored interval counts.

        Example:
            >>> durations = session.lifecycle.dwell(history)

        Constraints:
            Reads only the committed history artifact. Censored intervals are
            reported separately and are never treated as completed durations.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.lifecycle_reducers import dwell

        validate_capability_inputs("lifecycle.dwell", history=history)
        with _track_materializing_operation(
            self._session,
            "marivo.analysis.lifecycle.dwell",
            capability_id="lifecycle.dwell",
            family="lifecycle",
            intent="lifecycle.dwell",
            arguments={"history": history},
            analysis_purpose=analysis_purpose,
        ):
            return dwell(
                history,
                analysis_purpose=analysis_purpose,
                session=self._session,
            )

    def violations(
        self,
        history: LifecycleFrame,
        *,
        analysis_purpose: str | None = None,
    ) -> LifecycleFrame:
        """Expose the persisted fixed-contract replay violation trace.

        Args:
            history: Exact same-session ``LifecycleFrame[history]``.
            analysis_purpose: Optional business purpose retained in lineage.

        Returns:
            A persisted ``LifecycleFrame[violations]`` with one row per
            occurrence that no modeled transition admitted, carrying the state
            that was left unchanged.

        Guidance:
            These rows are model-versus-data disagreements, not a data-quality
            verdict. Read them as evidence that the StateModel is incomplete or
            that the source Events are out of contract, and resolve that
            business question before treating the replayed history as final.

        Example:
            >>> trace = session.lifecycle.violations(history)

        Constraints:
            Reads only the committed private trace bound to ``history``; it
            never replays or rereads Events.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.lifecycle_reducers import violations

        validate_capability_inputs("lifecycle.violations", history=history)
        with _track_materializing_operation(
            self._session,
            "marivo.analysis.lifecycle.violations",
            capability_id="lifecycle.violations",
            family="lifecycle",
            intent="lifecycle.violations",
            arguments={"history": history},
            analysis_purpose=analysis_purpose,
        ):
            return violations(
                history,
                analysis_purpose=analysis_purpose,
                session=self._session,
            )


@dataclass(frozen=True)
class SessionDiscoverNamespace:
    """Session-bound candidate discovery helpers."""

    _session: Session

    def semantic_hypotheses(
        self,
        source: MetricFrame | DeltaFrame,
        *,
        limit: int = 50,
    ) -> CandidateSet:
        """Discover bounded unscored Metric candidates through one ontology edge.

        Args:
            source: Persisted arity-one MetricFrame or Metric-derived DeltaFrame.
            limit: Maximum persisted candidates, from 1 through 200.

        Returns:
            CandidateSet[semantic_hypothesis] with stable item ids and diagnostics.

        Example:
            candidates = session.discover.semantic_hypotheses(frame, limit=50)
            candidates.show()
            candidate = candidates.select(item_id="candidate_<full sha256>")

        Constraints:
            Requires a ready Session ontology binding. It never executes candidates,
            scores them, or creates causal evidence.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.semantic_hypotheses import (
            semantic_hypotheses,
            validate_semantic_hypotheses_admission,
        )

        validate_capability_inputs(
            "discover.semantic_hypotheses",
            session=self._session,
            source=source,
        )
        validate_semantic_hypotheses_admission(
            source,
            limit=limit,
            session=self._session,
        )
        with _track_materializing_operation(
            self._session,
            "marivo.analysis.discover.semantic_hypotheses",
            capability_id="discover.semantic_hypotheses",
            family="discover",
            intent="semantic_hypotheses",
            arguments={"source": source, "limit": limit},
            analysis_purpose=None,
        ):
            return semantic_hypotheses(source, limit=limit, session=self._session)

    def point_anomalies(
        self,
        source: MetricFrame,
        *,
        value: str | None = None,
        threshold: float | None = None,
        # keep in sync with _DEFAULT_DISCOVER_LIMIT in marivo.analysis.intents.discover
        limit: int | None = 50,
        strategy: PointAnomalyStrategy | None = None,
        analysis_purpose: str | None = None,
    ) -> CandidateSet:
        """Find time-series points with unusual values.

        Source must be a MetricFrame with time_series or panel shape.
        ``threshold`` is an absolute z-score cutoff (|z| >= threshold); default 3.0.
        Lower values flag more candidates. ``limit`` bounds the candidate count
        (top by |z|, default 50; ``None`` for unbounded); truncation is
        recorded in ``params``. ``strategy`` selects the scoring kernel: the
        default ``zscore`` uses a global mean/std baseline; ``seasonal_robust_zscore``
        uses a median/MAD baseline stratified by day-of-week, which resists an
        anomaly contaminating the baseline and avoids flagging weekly seasonality.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.discover import discover, validate_discover_admission

        validate_capability_inputs("discover.point_anomalies", source=source)
        validate_discover_admission(
            source,
            objective="point_anomalies",
            strategy=strategy,
            threshold=threshold,
            limit=limit,
            session=self._session,
        )
        with _track_materializing_operation(
            self._session,
            "marivo.analysis.discover.point_anomalies",
            capability_id="discover.point_anomalies",
            family="discover",
            intent="point_anomalies",
            arguments={
                "source": source,
                "value": value,
                "threshold": threshold,
                "limit": limit,
                "strategy": strategy,
            },
            analysis_purpose=analysis_purpose,
        ):
            return discover.point_anomalies(
                source,
                value=value,
                threshold=threshold,
                limit=limit,
                strategy=strategy,
                analysis_purpose=analysis_purpose,
                session=self._session,
            )

    def period_shifts(
        self,
        source: DeltaFrame,
        *,
        value: str | None = None,
        threshold: float | None = None,
        limit: int | None = 50,
        analysis_purpose: str | None = None,
    ) -> CandidateSet:
        """Find period-shift candidates from a DeltaFrame.

        Requires at least four time buckets in a time-series delta, or at least
        one panel series with four time buckets.
        ``threshold`` is an absolute z-score cutoff on rolling window means
        (|z| >= threshold); default 2.0. ``limit`` bounds the candidate count
        (top by |z|, default 50; ``None`` for unbounded); truncation is
        recorded in ``params``.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.discover import discover, validate_discover_admission

        validate_capability_inputs("discover.period_shifts", source=source)
        validate_discover_admission(
            source,
            objective="period_shifts",
            threshold=threshold,
            limit=limit,
            session=self._session,
        )
        with _track_materializing_operation(
            self._session,
            "marivo.analysis.discover.period_shifts",
            capability_id="discover.period_shifts",
            family="discover",
            intent="period_shifts",
            arguments={
                "source": source,
                "value": value,
                "threshold": threshold,
                "limit": limit,
            },
            analysis_purpose=analysis_purpose,
        ):
            return discover.period_shifts(
                source,
                value=value,
                threshold=threshold,
                limit=limit,
                analysis_purpose=analysis_purpose,
                session=self._session,
            )

    def driver_axes(
        self,
        source: DeltaFrame,
        *,
        search_space: list[_SemanticInput[DimensionKind | TimeDimensionKind]],
        value: str | None = None,
        limit: int | None = 50,
        analysis_purpose: str | None = None,
    ) -> CandidateSet:
        """Find dimensions that explain a delta.

        Source must be a DeltaFrame. ``search_space`` is required and lists
        the candidate dimensions to evaluate for explanatory power. ``limit``
        bounds the candidate count (top by |score|, default 50; ``None`` for
        unbounded); truncation is recorded in ``params``.

        Example:
            >>> country = session.catalog.dimensions.get("sales.orders.country")
            >>> candidates = session.discover.driver_axes(delta, search_space=[country])
            >>> candidates.show()
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.discover import discover, validate_discover_admission

        validate_capability_inputs("discover.driver_axes", source=source)
        validate_discover_admission(
            source,
            objective="driver_axes",
            limit=limit,
            search_space=search_space,
            session=self._session,
        )
        with _track_materializing_operation(
            self._session,
            "marivo.analysis.discover.driver_axes",
            capability_id="discover.driver_axes",
            family="discover",
            intent="driver_axes",
            arguments={
                "source": source,
                "search_space": search_space,
                "value": value,
                "limit": limit,
            },
            analysis_purpose=analysis_purpose,
            attributes={"marivo.analysis.search_space_count": len(search_space)},
        ):
            return discover.driver_axes(
                source,
                search_space=search_space,
                value=value,
                limit=limit,
                analysis_purpose=analysis_purpose,
                session=self._session,
            )

    def interesting_slices(
        self,
        source: MetricFrame | DeltaFrame,
        *,
        search_space: list[_SemanticInput[DimensionKind | TimeDimensionKind]] | None = None,
        value: str | None = None,
        threshold: float | None = None,
        limit: int | None = 50,
        analysis_purpose: str | None = None,
    ) -> CandidateSet:
        """Find dimension slices with notable values.

        Accepts a MetricFrame or DeltaFrame. Optionally narrow the search
        with ``search_space``; otherwise all available dimensions are probed.
        ``threshold`` is an absolute z-score for MetricFrame (|z| >= threshold)
        or absolute delta value for DeltaFrame; default 2.0. ``limit`` bounds
        the candidate count (top by |score|, default 50; ``None`` for
        unbounded); truncation is recorded in ``params``.

        Example:
            >>> country = session.catalog.dimensions.get("sales.orders.country")
            >>> candidates = session.discover.interesting_slices(
            ...     frame, search_space=[country]
            ... )
            >>> candidates.show()
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.discover import discover, validate_discover_admission

        validate_capability_inputs("discover.interesting_slices", source=source)
        validate_discover_admission(
            source,
            objective="interesting_slices",
            threshold=threshold,
            limit=limit,
            search_space=search_space,
            session=self._session,
        )
        with _track_materializing_operation(
            self._session,
            "marivo.analysis.discover.interesting_slices",
            capability_id="discover.interesting_slices",
            family="discover",
            intent="interesting_slices",
            arguments={
                "source": source,
                "search_space": search_space,
                "value": value,
                "threshold": threshold,
                "limit": limit,
            },
            analysis_purpose=analysis_purpose,
            attributes={"marivo.analysis.search_space_count": len(search_space or [])},
        ):
            return discover.interesting_slices(
                source,
                search_space=search_space,
                value=value,
                threshold=threshold,
                limit=limit,
                analysis_purpose=analysis_purpose,
                session=self._session,
            )

    def interesting_windows(
        self,
        source: MetricFrame | DeltaFrame,
        *,
        value: str | None = None,
        threshold: float | None = None,
        limit: int | None = 50,
        analysis_purpose: str | None = None,
    ) -> CandidateSet:
        """Find time windows with notable behavior.

        Source must have time_series or panel shape. Returns windows where
        the metric exhibits significant trends, level shifts, or volatility.
        ``threshold`` is an absolute z-score cutoff (|z| >= threshold); default 2.0.
        ``limit`` bounds the candidate count (top by |score|, default 50;
        ``None`` for unbounded); truncation is recorded in ``params``.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.discover import discover, validate_discover_admission

        validate_capability_inputs("discover.interesting_windows", source=source)
        validate_discover_admission(
            source,
            objective="interesting_windows",
            threshold=threshold,
            limit=limit,
            session=self._session,
        )
        with _track_materializing_operation(
            self._session,
            "marivo.analysis.discover.interesting_windows",
            capability_id="discover.interesting_windows",
            family="discover",
            intent="interesting_windows",
            arguments={
                "source": source,
                "value": value,
                "threshold": threshold,
                "limit": limit,
            },
            analysis_purpose=analysis_purpose,
        ):
            return discover.interesting_windows(
                source,
                value=value,
                threshold=threshold,
                limit=limit,
                analysis_purpose=analysis_purpose,
                session=self._session,
            )

    def cross_sectional_outliers(
        self,
        source: MetricFrame,
        *,
        peer_scope: list[_SemanticInput[DimensionKind | TimeDimensionKind]] | None = None,
        value: str | None = None,
        threshold: float | None = None,
        limit: int | None = 50,
        analysis_purpose: str | None = None,
    ) -> CandidateSet:
        """Find segments that are outliers compared to their peers.

        Source must be a MetricFrame with segmented or panel shape.
        ``peer_scope`` defines the grouping for peer comparison; defaults to
        all non-time axes.
        ``threshold`` is a robust z-score cutoff using MAD
        (|robust_z| >= threshold); default 3.0. ``limit`` bounds the candidate
        count (top by |robust_z|, default 50; ``None`` for unbounded);
        truncation is recorded in ``params``.

        Example:
            >>> region = session.catalog.dimensions.get("sales.orders.region")
            >>> candidates = session.discover.cross_sectional_outliers(
            ...     frame, peer_scope=[region]
            ... )
            >>> candidates.show()
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.discover import discover, validate_discover_admission

        validate_capability_inputs("discover.cross_sectional_outliers", source=source)
        validate_discover_admission(
            source,
            objective="cross_sectional_outliers",
            threshold=threshold,
            limit=limit,
            peer_scope=peer_scope,
            session=self._session,
        )
        with _track_materializing_operation(
            self._session,
            "marivo.analysis.discover.cross_sectional_outliers",
            capability_id="discover.cross_sectional_outliers",
            family="discover",
            intent="cross_sectional_outliers",
            arguments={
                "source": source,
                "peer_scope": peer_scope,
                "value": value,
                "threshold": threshold,
                "limit": limit,
            },
            analysis_purpose=analysis_purpose,
            attributes={"marivo.analysis.peer_scope_count": len(peer_scope or [])},
        ):
            return discover.cross_sectional_outliers(
                source,
                peer_scope=peer_scope,
                value=value,
                threshold=threshold,
                limit=limit,
                analysis_purpose=analysis_purpose,
                session=self._session,
            )
