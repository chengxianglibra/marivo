"""Typed analysis session runtime."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast, overload

from marivo.analysis._pages import (
    _BoundedPage,
    decode_keyset_cursor,
    encode_keyset_cursor,
)
from marivo.analysis.session._layout import PersistenceLayout, read_job_record
from marivo.analysis.timezone import resolve_system_timezone
from marivo.render import Card, RenderableResult


class _Unset:
    __slots__ = ()


_UNSET = _Unset()


def _normalize_unset[T](value: T | _Unset) -> T | None:
    return None if isinstance(value, _Unset) else value


if TYPE_CHECKING:
    from marivo.analysis.event import (
        CompletenessDeclaration,
        EventMatchingPolicy,
        EventPattern,
        PatternStep,
    )
    from marivo.analysis.evidence import (
        ArtifactDigest,
        ArtifactDigestPage,
        EvidenceDerivationTrace,
        Finding,
        FindingPage,
    )
    from marivo.analysis.evidence.store import EvidenceStore
    from marivo.analysis.frames.association import AssociationResult
    from marivo.analysis.frames.attribution import AttributionFrame
    from marivo.analysis.frames.base import BaseFrame
    from marivo.analysis.frames.candidate import (
        CandidateSet,
        CandidateStrategy,
        OntologyMetricCandidate,
    )
    from marivo.analysis.frames.delta import DeltaFrame
    from marivo.analysis.frames.event import EventFrame
    from marivo.analysis.frames.forecast import ForecastFrame
    from marivo.analysis.frames.hypothesis import HypothesisTestResult
    from marivo.analysis.frames.lifecycle import LifecycleFrame
    from marivo.analysis.frames.metric import MetricFrame
    from marivo.analysis.frames.quality import QualityReport
    from marivo.analysis.frames.subject import SubjectSet
    from marivo.analysis.funnel import FunnelLossRate
    from marivo.analysis.intents._attribution_mode import AttributionMode
    from marivo.analysis.intents._shape import SemanticShape
    from marivo.analysis.lifecycle import FromInception
    from marivo.analysis.policies import AlignmentPolicy, SamplingPolicy
    from marivo.analysis.runtime_metric import RuntimeMetricExpr
    from marivo.analysis.session._store import SessionStore
    from marivo.analysis.slice_types import SliceValue
    from marivo.analysis.subject import SubjectSelection
    from marivo.analysis.windows.spec import GrainInput, TimeScope, TimeScopeInput
    from marivo.ontology.catalog import OntologyCatalog
    from marivo.refs import DimensionKind, MetricKind, StateModelKind, TimeDimensionKind
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


@dataclass(frozen=True, repr=False)
class JobSummary(RenderableResult):
    id: str
    intent: str
    status: str
    started_at: str
    duration_ms: int
    output_frame_ref: str | None

    def _repr_identity(self) -> str:
        return f"JobSummary id={self.id} intent={self.intent} status={self.status}"

    def _card(self) -> Card:
        return Card(identity=self._repr_identity(), available=(".render()", ".show()")).status(
            f"duration={self.duration_ms}ms frame={self.output_frame_ref}"
        )


@dataclass(frozen=True, repr=False)
class FrameSummaryEntry(RenderableResult):
    ref: str
    kind: str
    metric_id: str | None
    semantic_kind: str | None
    semantic_model: str | None
    created_at: str | None
    row_count: int | None = None
    content_hash: str | None = None
    analysis_purpose: str | None = None
    evidence_status: str = "unavailable"

    @property
    def id(self) -> str:
        """Alias for the persisted frame ``ref``."""
        return self.ref

    def _repr_identity(self) -> str:
        parts = f"FrameSummaryEntry ref={self.ref} kind={self.kind}"
        if self.metric_id:
            parts += f" metric={self.metric_id}"
        return parts

    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".render()", ".show()")).status(
            f"metric={self.metric_id} created={self.created_at}"
        )
        if self.analysis_purpose:
            card.field("analysis_purpose", self.analysis_purpose)
        return card


class FrameSummaryPage(_BoundedPage[FrameSummaryEntry]):
    """Bounded newest-first page of persisted frame summaries."""


def _catalog_metric_path(meta: dict[str, object]) -> str | None:
    """Project one catalog metric display path from structured persisted identity."""
    identity = meta.get("metric_identity")
    if not isinstance(identity, dict):
        identities = meta.get("metric_identities")
        if isinstance(identities, list) and len(identities) == 1:
            identity = identities[0]
    if not isinstance(identity, dict) or identity.get("kind") != "catalog":
        return None
    payload = identity.get("metric_ref")
    if not isinstance(payload, dict) or payload.get("kind") != "metric":
        return None
    path = payload.get("path")
    return path if isinstance(path, str) and path else None


def _read_job_summaries(
    *, store: SessionStore, layout: PersistenceLayout, session_id: str
) -> list[JobSummary]:
    """Read persisted job summaries without requiring a live session."""
    summaries: list[JobSummary] = []
    for row in store.list_jobs(session_id):
        record = read_job_record(layout, row["job_id"])
        summaries.append(
            JobSummary(
                id=record["id"],
                intent=record["intent"],
                status=record["status"],
                started_at=record["started_at"],
                duration_ms=record["duration_ms"],
                output_frame_ref=record.get("output_frame_ref"),
            )
        )
    summaries.sort(key=lambda item: (item.started_at, item.id))
    return summaries


def _read_frame_summary_page(
    *,
    store: SessionStore,
    project_root: Path,
    session_id: str,
    kind: str | None,
    evidence_status: str | None,
    limit: int,
    cursor: str | None,
) -> FrameSummaryPage:
    """Read one persisted frame-summary page without a live session."""
    if not 1 <= limit <= 100:
        raise ValueError("frame_summaries limit must be within [1, 100]")
    after: tuple[str, str] | None = None
    if cursor is not None:
        committed_at, identity = decode_keyset_cursor(cursor)
        if not isinstance(committed_at, str):
            raise ValueError("frame_summaries cursor has an invalid sort key")
        after = (committed_at, identity)
    rows = store.page_artifacts(
        session_id,
        kind=kind,
        evidence_status=evidence_status,
        limit=limit,
        after=after,
    )
    has_more = len(rows) > limit
    entries: list[FrameSummaryEntry] = []
    for row in rows[:limit]:
        meta_path = row["meta_path"]
        abs_meta = project_root / meta_path
        try:
            meta = json.loads(abs_meta.read_text()) if abs_meta.is_file() else {}
        except (OSError, json.JSONDecodeError):
            meta = {}
        metric_id = _catalog_metric_path(meta)
        entries.append(
            FrameSummaryEntry(
                ref=meta.get("ref", row["artifact_id"]),
                kind=meta.get("kind", row["kind"]),
                metric_id=metric_id,
                semantic_kind=meta.get("semantic_kind"),
                semantic_model=metric_id.split(".", 1)[0] if metric_id else None,
                created_at=meta.get("created_at", row["created_at"]),
                evidence_status=row["evidence_status"],
                analysis_purpose=meta.get("analysis_purpose"),
                row_count=meta.get("row_count"),
                content_hash=meta.get("content_hash", row["content_hash"]),
            )
        )
    next_cursor = None
    if has_more:
        last_row = rows[limit - 1]
        next_cursor = encode_keyset_cursor(last_row["created_at"], last_row["artifact_id"])
    return FrameSummaryPage(
        items=tuple(entries),
        limit=limit,
        has_more=has_more,
        next_cursor=next_cursor,
    )


class Session(RenderableResult):
    """Call marivo.help(Session) for its public consumption contract."""

    __slots__ = (
        "_calendars",
        "_catalog",
        "_connection_runtime",
        "_created_at",
        "_cwd",
        "_default_calendar",
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
        default_calendar: str | None = None,
        calendars: Any = None,
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
        self._default_calendar = default_calendar
        self._calendars = calendars
        self._judgment_store = judgment_store
        self._judgment_store_unavailable = judgment_store_unavailable
        self._ontology_state = ontology_state
        self._ontology_catalog = ontology_catalog
        self._ontology_issues = ontology_issues
        if self._calendars is None:
            from marivo.analysis.calendar.loader import CalendarCache

            self._calendars = CalendarCache(self._project_root)

    def _repr_identity(self) -> str:
        return f"Session id={self._id} name={self._name}"

    def _card(self) -> Card:
        from marivo.analysis._capabilities.registry import REGISTRY

        mode = "read_only" if self.is_read_only else "writable"
        properties, methods = REGISTRY.public_object_members("Session")
        intrinsic_methods = tuple(method for method in methods if method in {"render", "show"})
        registered_calls = tuple(
            call
            for call in REGISTRY.public_member_calls("Session")
            if call not in {".render()", ".show()"}
        )
        card = Card(
            identity=self._repr_identity(),
            available=(
                *(f".{property_name}" for property_name in properties),
                *(f".{method_name}()" for method_name in intrinsic_methods),
                *registered_calls,
            ),
        ).status(mode)
        card.field("question", self._question or "none")
        card.field("ontology", self._ontology_state)
        card.field("report_timezone", self._report_tz_name)
        card.field("created_at", self._created_at.isoformat())
        card.field("updated_at", self._updated_at.isoformat())
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
    def default_calendar(self) -> str | None:
        return self._default_calendar

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

    def jobs(self) -> list[JobSummary]:
        """Return lightweight summaries for every recorded job, oldest first.

        Each entry is a :class:`JobSummary` (id, intent, status, timing, output
        frame ref). For the full record of a single job, use :meth:`job`.
        """

        return _read_job_summaries(store=self._store, layout=self._layout, session_id=self.id)

    def recent_jobs(self, limit: int = 5) -> list[JobSummary]:
        """Return the most recent ``limit`` job summaries, oldest first.

        A non-positive ``limit`` returns an empty list.
        """
        if limit <= 0:
            return []
        return self.jobs()[-limit:]

    def job(self, job_id: str) -> dict[str, Any]:
        """Return the full record for a single job as a dict.

        Unlike :meth:`jobs`, which returns lightweight :class:`JobSummary`
        objects, this returns the complete persisted record including fields
        such as ``params``. Raises if no job with ``job_id`` exists.
        """
        from marivo.analysis.errors import JobNotFoundError, SchemaVersionMismatchError

        row = self._store.get_job(self.id, job_id)
        if row is None:
            raise JobNotFoundError(
                message=f"no job '{job_id}' in session {self.id!r}",
                context={"session_id": self.id, "job_id": job_id},
            )
        record = read_job_record(self._layout, job_id)
        if record.get("schema") != "marivo.analysis_job/v2":
            raise SchemaVersionMismatchError(
                message="unsupported_persisted_schema: job record is not marivo.analysis_job/v2",
                context={
                    "job_id": job_id,
                    "received_schema": record.get("schema"),
                    "expected_schema": "marivo.analysis_job/v2",
                    "repair": "Start a new analysis session and regenerate the artifact.",
                },
            )
        return record

    def get_frame(self, ref: str) -> BaseFrame:
        """Load a persisted frame by ref or artifact_id.

        Reconstructs a live frame object from the on-disk parquet and
        meta.json.  The returned frame is fully functional and can be
        passed to any intent (compare, attribute, etc.).

        Args:
            ref: The frame ref string.  After observe() or compare()
                returns, ``frame.ref`` equals the deterministic
                artifact_id, so ``session.get_frame(prev_frame.ref)``
                works across script boundaries.

        Raises:
            FrameRefNotFound: No frame with this ref exists in this session.
            CrossSessionFrameError: The frame belongs to a different session.
            FrameCacheCorruptedError: The frame data is on disk but unreadable.
        """
        from marivo.analysis.session._load import load_frame

        return load_frame(ref, session=self)

    def frame_summaries(
        self,
        *,
        kind: str | None = None,
        evidence_status: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> FrameSummaryPage:
        """Return one bounded newest-first page of analysis-result metadata.

        With no ``kind`` filter, linked component and coverage sidecars are
        omitted. Pass their exact kind to inspect those internal frames.

        Example:
            page = session.frame_summaries(limit=20)
            next_page = session.frame_summaries(limit=20, cursor=page.next_cursor)
        """
        return _read_frame_summary_page(
            store=self._store,
            project_root=self._project_root,
            session_id=self.id,
            kind=kind,
            evidence_status=evidence_status,
            limit=limit,
            cursor=cursor,
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
    def evidence(self) -> EvidenceNamespace:
        """Return Surface 3 evidence lookup helpers."""
        return EvidenceNamespace(self)

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
            artifact=artifact,
            selection=selection,
        )
        with _track_session_operation(
            self,
            "marivo.analysis.select_subjects",
            family="subjects",
            intent="select_subjects",
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
        time_scope: TimeScopeInput = None,
        grain: GrainInput = None,
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
        time_scope: TimeScopeInput | _Unset = _UNSET,
        grain: GrainInput | _Unset = _UNSET,
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
            time_scope: Half-open time range ``{"start": ..., "end": ...}`` — start is
                inclusive, end is exclusive.  For date-only strings, ``end="2026-08-01"``
                means data from August 1 is **not** included.
            grain: Optional time bucket grain. When present, observe returns a time
                series or panel depending on ``dimensions``.
            dimensions: Exact current-catalog dimension/time-dimension entries
                or refs used as segment axes. Omit, pass ``None``, or pass
                ``[]`` for no segment axes.
            slice_by: Pre-aggregation global row filter. Keys are exact dimension
                refs; values are either a scalar (``==``), a
                list/tuple/set (``in``), or ``{"op": "<op>", "value": ...}`` where op is one of
                ``==, !=, in, >, >=, <, <=, between``.
            time_dimension: Exact current-catalog time-dimension entry/ref
                selecting the time axis when an entity declares multiple time
                dimensions.
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
            ...     time_scope={"start": "2026-07-01", "end": "2026-10-01"},
            ...     grain="day",
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
            ...     time_scope={"start": "2026-07-01", "end": "2026-10-01"},
            ...     grain="day",
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

            with _track_session_operation(
                self,
                "marivo.analysis.observe",
                family="core",
                intent="observe",
                attributes={"marivo.analysis.candidate_observation": True},
            ):
                validate_capability_inputs("observe", metrics=metrics)
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

        with _track_session_operation(
            self,
            "marivo.analysis.observe",
            family="core",
            intent="observe",
            attributes={"marivo.analysis.dimension_count": len(normalized_dimensions or [])},
        ) as telemetry_operation:
            validate_capability_inputs(
                "observe", time_scope=normalized_time_scope, cohort=normalized_cohort
            )
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
            alignment: Defaults to ``mv.window_bucket()``. For
                ``segmented`` frames, only ``window_bucket`` is supported in v1.

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
            >>> cur = session.observe(revenue, time_scope={"start": "2026-07-01", "end": "2026-10-01"})
            >>> base = session.observe(revenue, time_scope={"start": "2025-07-01", "end": "2025-10-01"})
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
            from marivo.analysis.intents.funnel_compare import compare_funnels

            with _track_session_operation(
                self,
                "marivo.analysis.compare.funnel",
                family="events",
                intent="compare",
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
        with _track_session_operation(
            self,
            "marivo.analysis.compare",
            family="core",
            intent="compare",
            attributes=attrs,
        ):
            validate_capability_inputs(
                "compare",
                current=current_metric,
                baseline=baseline_metric,
                alignment=alignment,
            )
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
        target: FunnelLossRate | None = None,
        analysis_purpose: str | None = None,
    ) -> AttributionFrame:
        """Attribute a DeltaFrame's movement over explicit deterministic axes.

        When to use: after observe -> compare, compute deterministic
        contribution rows for explicit axes selected by the caller. If a
        requested axis is missing from the input DeltaFrame, Marivo attempts to
        replay the source observe/compare lineage with the extra axis and fails
        closed when replay is not recoverable.
        For multiple axes, choose ``mode="joint"`` for one row per complete
        axis combination, or ``mode="hierarchy"`` for prefix-level drill-down
        rows. Joint rows are additive; hierarchy rows repeat parent totals, so
        only the deepest level is additive.
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
            mode: Required for multiple axes. ``"joint"`` returns one row per
                axis combination; ``"hierarchy"`` returns ordered prefix rows.
                Omit for a single axis.
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
            ...     mode="joint",
            ...     analysis_purpose="按国家归因收入变化",
            ... )
        """
        from marivo.analysis.errors import (
            AnalysisRepair,
            FunnelAttributionUnsupportedError,
        )
        from marivo.introspection.live.model import LiveHelpTarget

        if frame.meta.semantic_kind == "funnel":
            from marivo.analysis.intents.funnel_attribute import attribute_funnel

            with _track_session_operation(
                self,
                "marivo.analysis.attribute.funnel",
                family="events",
                intent="attribute",
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
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.attribute import attribute

        semantic_kind = getattr(frame.meta, "semantic_kind", None)
        attrs: dict[str, str | int | float | bool] = {"marivo.analysis.axis_count": len(axes)}
        if isinstance(semantic_kind, str):
            attrs["marivo.analysis.semantic_kind"] = semantic_kind
        if mode is not None:
            attrs["marivo.analysis.attribution_mode"] = mode
        with _track_session_operation(
            self,
            "marivo.analysis.attribute",
            family="core",
            intent="attribute",
            attributes=attrs,
        ):
            validate_capability_inputs("attribute", frame=frame)
            return attribute(
                frame,
                axes=axes,
                mode=mode,
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
        The result carries one row per lag and ``meta.best_lag`` marks the strongest.
        Default is lag 0 only. Both frames must belong to the active session.

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
        with _track_session_operation(
            self,
            "marivo.analysis.correlate",
            family="core",
            intent="correlate",
            attributes=attrs,
        ):
            validate_capability_inputs("correlate", a=a, b=b, alignment=alignment)
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
        """Project a time_series or panel MetricFrame forward by ``horizon`` buckets.

        When to use: project a time series forward; requires time_series or panel shape.

        v1 requires continuous time buckets and no NaN values. Impute or re-observe
        before forecasting. ``seasonal_naive`` needs at least
        ``seasonality_period + 1`` training rows per series.

        Args:
            history: A ``time_series`` or ``panel`` MetricFrame.
            horizon: Number of buckets to project. Must be >= 1.
            model: Forecast strategy. ``seasonal_naive`` defaults to the grain-typical period.
            seasonality_period: Override for the seasonality period. Defaults by grain
                (day=7, week=52, month=12, quarter=4).
            interval_level: Confidence level for prediction intervals. Must be in (0, 1).
            measure_column: Public value column from ``history.value_columns``.
                Defaults to the frame's unique metric value column.

        Raises:
            ForecastShapeUnsupportedError: ``history`` is not a time_series / panel MetricFrame,
                or its grain is not in {day, week, month, quarter}.
            ForecastPolicyError: ``horizon`` or ``interval_level`` is out of range.
            ForecastInsufficientHistoryError: Not enough rows for the chosen model.
            ForecastInputQualityError: ``history`` contains NaN values in ``value``.
            CrossSessionFrameError: ``history`` belongs to a different session.

        Example:
            >>> history = session.observe(
            ...     session.catalog.require(ms.ref.metric("sales.revenue")),
            ...     time_scope={"start": "2026-01-01", "end": "2026-04-01"}, grain="day",
            ... )
            >>> forecast = session.forecast(
            ...     history,
            ...     horizon=30,
            ...     measure_column=history.value_columns[0],
            ...     analysis_purpose="预测未来 30 天收入走势",
            ... )
            >>> forecast.show()
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.forecast import forecast

        semantic_kind = getattr(history.meta, "semantic_kind", None)
        attrs: dict[str, str | int | float | bool] = {
            "marivo.analysis.horizon": horizon,
            "marivo.analysis.forecast_model": model,
        }
        if isinstance(semantic_kind, str):
            attrs["marivo.analysis.semantic_kind"] = semantic_kind
        with _track_session_operation(
            self,
            "marivo.analysis.forecast",
            family="core",
            intent="forecast",
            attributes=attrs,
        ):
            validate_capability_inputs("forecast", history=history)
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

    def assess_quality(
        self, frame: BaseFrame, *, analysis_purpose: str | None = None
    ) -> QualityReport:
        """Run quality checks over a MetricFrame or EventFrame and return a report.

        When to use: check data quality and coverage before downstream analysis.

        EventFrame[journey] checks include row identity, participant resolution,
        ordering determinism, per-input completeness, declarations, and censoring.
        Other derived frame families remain unsupported.

        Args:
            frame: A MetricFrame or EventFrame[journey] to inspect.

        Raises:
            QualityShapeUnsupportedError: ``frame`` is not a supported frame.
            CrossSessionFrameError: ``frame`` belongs to a different session.

        Example:
            >>> report = session.assess_quality(
            ...     frame,
            ...     analysis_purpose="检查收入观察结果是否可用于归因",
            ... )
            >>> for issue in report.contract().issues:
            ...     print(issue)
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.assess_quality import assess_quality

        with _track_session_operation(
            self,
            "marivo.analysis.assess_quality",
            family="core",
            intent="assess_quality",
        ):
            validate_capability_inputs("assess_quality", frame=frame)
            return assess_quality(frame, analysis_purpose=analysis_purpose, session=self)

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
        with _track_session_operation(
            self,
            "marivo.analysis.hypothesis_test",
            family="core",
            intent="hypothesis_test",
            attributes=attrs,
        ):
            validate_capability_inputs(
                "hypothesis_test",
                a=a,
                b=b,
                alignment=alignment,
                sampling=sampling,
            )
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
            context={"session_name": session.name},
        )


# Deprecated: kept for backward compatibility with intent modules that import
# ensure_session_writable. Will be removed once those modules are migrated to
# ensure_session_can_execute (Task 5).
ensure_session_writable = ensure_session_can_execute


@dataclass(frozen=True, repr=False)
class SessionEvents(RenderableResult):
    """Session-bound Event Journey materialization and reducer operators."""

    _session: Session

    def _repr_identity(self) -> str:
        return f"SessionEvents session={self._session.id}"

    def _card(self) -> Card:
        from marivo.analysis._capabilities.registry import REGISTRY

        _properties, methods = REGISTRY.public_object_members("SessionEvents")
        intrinsic_methods = tuple(method for method in methods if method in {"render", "show"})
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
            watermark. Use ``mv.declared_complete_through(...)`` only for an
            explicit governed assumption with a rationale.

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
            ...     cohort_window=mv.TimeScope(
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
            pattern=pattern,
            cohort_window=cohort_window,
            matching=matching,
            completeness=completeness,
            cohort=cohort,
        )
        with _track_session_operation(
            self._session,
            "marivo.analysis.events.match",
            family="events",
            intent="events.match",
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
            journeys=journeys,
            axes=axes,
        )
        with _track_session_operation(
            self._session,
            "marivo.analysis.events.funnel",
            family="events",
            intent="events.funnel",
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
        analysis_purpose: str | None = None,
    ) -> EventFrame:
        """Project persisted Event assignments into exact elapsed durations.

        The reducer never queries or rematches Event inputs. ``start_step`` and
        ``end_step`` must be the exact typed steps retained by the source
        pattern, and the start must precede the end.

        Args:
            journeys: Exact same-session ``EventFrame[journey]``.
            start_step: Exact reached step from the persisted source pattern.
            end_step: Exact later step from the persisted source pattern.
            analysis_purpose: Optional business purpose retained in lineage.

        Returns:
            A persisted ``EventFrame[time_to_event]`` with one row per source
            journey that reached ``start_step``.

        Example:
            >>> elapsed = session.events.time_to_event(
            ...     journeys,
            ...     start_step=checkout_step,
            ...     end_step=payment_step,
            ...     analysis_purpose="Measure checkout-to-payment elapsed time.",
            ... )
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.event_reducers import time_to_event

        validate_capability_inputs(
            "events.time_to_event",
            journeys=journeys,
            start_step=start_step,
            end_step=end_step,
        )
        with _track_session_operation(
            self._session,
            "marivo.analysis.events.time_to_event",
            family="events",
            intent="events.time_to_event",
        ):
            return time_to_event(
                journeys,
                start_step=start_step,
                end_step=end_step,
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
        intrinsic_methods = tuple(method for method in methods if method in {"render", "show"})
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
            Violation handling is the fixed v1 replay contract rather than a
            policy slot: an occurrence that no modeled transition admits
            records a violation-trace row and leaves state unchanged, and
            modeled occurrences before inception are ignored rather than
            counted as violations. Completeness governs censoring, not
            correctness — without an authoritative watermark or a declaration
            covering ``window.end``, open intervals are ``coverage_censored``
            and subjects with no observed inception are censored instead of
            failing. Prefer an observed watermark; use
            ``mv.declared_complete_through(...)`` only as an explicit governed
            assumption with a rationale.

        Example:
            >>> order_lifecycle = session.catalog.state_models.get(
            ...     "commerce.order_lifecycle"
            ... )
            >>> history = session.lifecycle.replay(
            ...     order_lifecycle,
            ...     window=mv.TimeScope(
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
        from marivo.analysis.intents.lifecycle import replay

        validate_capability_inputs(
            "lifecycle.replay",
            window=window,
            completeness=completeness,
            cohort=cohort,
        )
        with _track_session_operation(
            self._session,
            "marivo.analysis.lifecycle.replay",
            family="lifecycle",
            intent="lifecycle.replay",
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
            history=history,
            axes=axes,
        )
        with _track_session_operation(
            self._session,
            "marivo.analysis.lifecycle.distribution",
            family="lifecycle",
            intent="lifecycle.distribution",
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
        with _track_session_operation(
            self._session,
            "marivo.analysis.lifecycle.transitions",
            family="lifecycle",
            intent="lifecycle.transitions",
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
        with _track_session_operation(
            self._session,
            "marivo.analysis.lifecycle.dwell",
            family="lifecycle",
            intent="lifecycle.dwell",
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
        with _track_session_operation(
            self._session,
            "marivo.analysis.lifecycle.violations",
            family="lifecycle",
            intent="lifecycle.violations",
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
        from marivo.analysis.intents.semantic_hypotheses import semantic_hypotheses

        with _track_session_operation(
            self._session,
            "marivo.analysis.discover.semantic_hypotheses",
            family="discover",
            intent="semantic_hypotheses",
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
        strategy: CandidateStrategy | None = None,
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
        from marivo.analysis.intents.discover import discover

        with _track_session_operation(
            self._session,
            "marivo.analysis.discover.point_anomalies",
            family="discover",
            intent="point_anomalies",
        ):
            validate_capability_inputs("discover.point_anomalies", source=source)
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
        from marivo.analysis.intents.discover import discover

        with _track_session_operation(
            self._session,
            "marivo.analysis.discover.period_shifts",
            family="discover",
            intent="period_shifts",
        ):
            validate_capability_inputs("discover.period_shifts", source=source)
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
        from marivo.analysis.intents.discover import discover

        with _track_session_operation(
            self._session,
            "marivo.analysis.discover.driver_axes",
            family="discover",
            intent="driver_axes",
            attributes={"marivo.analysis.search_space_count": len(search_space)},
        ):
            validate_capability_inputs("discover.driver_axes", source=source)
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
        from marivo.analysis.intents.discover import discover

        with _track_session_operation(
            self._session,
            "marivo.analysis.discover.interesting_slices",
            family="discover",
            intent="interesting_slices",
            attributes={"marivo.analysis.search_space_count": len(search_space or [])},
        ):
            validate_capability_inputs("discover.interesting_slices", source=source)
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
        from marivo.analysis.intents.discover import discover

        with _track_session_operation(
            self._session,
            "marivo.analysis.discover.interesting_windows",
            family="discover",
            intent="interesting_windows",
        ):
            validate_capability_inputs("discover.interesting_windows", source=source)
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
        from marivo.analysis.intents.discover import discover

        with _track_session_operation(
            self._session,
            "marivo.analysis.discover.cross_sectional_outliers",
            family="discover",
            intent="cross_sectional_outliers",
            attributes={"marivo.analysis.peer_scope_count": len(peer_scope or [])},
        ):
            validate_capability_inputs("discover.cross_sectional_outliers", source=source)
            return discover.cross_sectional_outliers(
                source,
                peer_scope=peer_scope,
                value=value,
                threshold=threshold,
                limit=limit,
                analysis_purpose=analysis_purpose,
                session=self._session,
            )


@dataclass(frozen=True)
class EvidenceNamespace:
    """Session-scoped Surface 3 evidence object lookups."""

    _session: Session

    def findings(
        self,
        *,
        kind: str | None = None,
        artifact_ref: str | None = None,
        subject: Any = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> FindingPage:
        """Return one bounded newest-first page of canonical findings.

        Example:
            page = session.evidence.findings(artifact_ref=artifact.ref, limit=50)
            for finding in page.items:
                print(finding.finding_type)
        """
        from marivo.analysis.evidence.audit import query_findings

        return query_findings(
            store=self._require_store(),
            session_id=self._session.id,
            kind=kind,
            artifact_ref=artifact_ref,
            subject=subject,
            limit=limit,
            cursor=cursor,
        )

    def digests(
        self,
        *,
        operator: str | None = None,
        subject: Any = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> ArtifactDigestPage:
        """Return one bounded newest-first page of persisted digest snapshots.

        Example:
            page = session.evidence.digests(operator="compare", limit=10)
            print(page.has_more, page.next_cursor)
            next_page = session.evidence.digests(limit=10, cursor=page.next_cursor)
        """
        from marivo.analysis.evidence.audit import query_digests

        return query_digests(
            store=self._require_store(),
            session_id=self._session.id,
            operator=operator,
            subject=subject,
            limit=limit,
            cursor=cursor,
        )

    def digest(self, artifact_ref: str) -> ArtifactDigest:
        """Return the exact persisted digest for one artifact.

        Example:
            digest = session.evidence.digest(artifact.ref)
            digest.show()
        """
        from marivo.analysis.evidence.audit import get_digest

        return get_digest(store=self._require_store(), artifact_ref=artifact_ref)

    def finding(self, finding_id: str) -> Finding:
        """Return one canonical typed finding by identity.

        Example:
            finding = session.evidence.finding(finding_id)
            print(finding.value)
        """
        from marivo.analysis.evidence.audit import get_finding

        return get_finding(store=self._require_store(), finding_id=finding_id)

    def trace(self, finding_id: str) -> EvidenceDerivationTrace:
        """Trace one finding to its source fields and retained digest items.

        Example:
            trace = session.evidence.trace(finding_id)
            print(trace.derivation.rule_id, trace.source_fields)
        """
        from marivo.analysis.evidence.audit import build_evidence_trace

        return build_evidence_trace(store=self._require_store(), finding_id=finding_id)

    def _require_store(self) -> EvidenceStore:
        from marivo.analysis.errors import EvidenceStoreUnavailableError

        store = self._session._evidence_store()
        if store is None:
            raise EvidenceStoreUnavailableError(
                message="evidence store is unavailable for this session",
                context={"session_id": self._session.id},
            )
        return store
