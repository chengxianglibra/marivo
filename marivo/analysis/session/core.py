"""Session class and session-local summaries."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

from marivo.analysis.session.persistence import (
    PersistenceLayout,
    list_job_ids,
    read_job_record,
    read_session_meta,
)
from marivo.analysis.timezone import resolve_system_timezone

if TYPE_CHECKING:
    import pandas as pd

    from marivo.analysis.evidence import (
        Assessment,
        EvidenceTrace,
        Finding,
        Proposition,
        SessionKnowledge,
    )
    from marivo.analysis.evidence.store import JudgmentStore
    from marivo.analysis.frames.association import AssociationResult
    from marivo.analysis.frames.attribution import AttributionFrame
    from marivo.analysis.frames.base import BaseFrame
    from marivo.analysis.frames.candidate import CandidateSet
    from marivo.analysis.frames.delta import DeltaFrame
    from marivo.analysis.frames.exploration import ExplorationResult
    from marivo.analysis.frames.forecast import ForecastFrame
    from marivo.analysis.frames.hypothesis import HypothesisTestResult
    from marivo.analysis.frames.metric import MetricFrame
    from marivo.analysis.frames.quality import QualityReport
    from marivo.analysis.intents._shape import SemanticShape
    from marivo.analysis.intents._types import SliceValue
    from marivo.analysis.policies import AlignmentPolicy, LagPolicy, PromotionPolicy, SamplingPolicy
    from marivo.analysis.refs import ArtifactRef, DimensionRef, MetricRef
    from marivo.analysis.windows.spec import GrainInput, TimeScopeInput

SemanticKind = Literal["scalar", "time_series", "segmented", "panel"]

SessionState = Literal["active", "archived"]
BackendFactory = Callable[[str], Any]


@dataclass(frozen=True)
class JobSummary:
    id: str
    intent: str
    status: str
    started_at: str
    duration_ms: int
    output_frame_ref: str | None


@dataclass(frozen=True)
class FrameRef:
    ref: str
    kind: str


@dataclass(frozen=True)
class FrameSummaryEntry:
    ref: str
    kind: str
    metric_id: str | None
    semantic_kind: str | None
    semantic_model: str | None
    created_at: str | None


@dataclass
class Session:
    id: str
    name: str
    question: str | None
    cwd: Path
    project_root: Path
    state: SessionState
    created_at: datetime
    updated_at: datetime
    backend_factory: BackendFactory | None
    layout: PersistenceLayout
    semantic_project: Any  # SemanticProject from marivo.semantic
    tz: tzinfo = field(default_factory=lambda: resolve_system_timezone().tz)
    default_calendar: str | None = None
    known_calendars: set[str] = field(default_factory=set)
    calendars: Any = None
    known_datasources: set[str] = field(default_factory=set)
    backend_cache: Any = None
    judgment_store: JudgmentStore | None = None
    judgment_store_unavailable: bool = False

    _HIDDEN_FROM_DIR: ClassVar[frozenset[str]] = frozenset(
        {
            "_HIDDEN_FROM_DIR",
            "layout",
            "semantic_project",
            "backend_factory",
            "backend_cache",
            "calendars",
            "known_calendars",
            "known_datasources",
            "judgment_store",
            "judgment_store_unavailable",
            "evidence_store",
            "findings",
            "propositions",
            "assessments",
        }
    )

    def __dir__(self) -> list[str]:
        return sorted(name for name in super().__dir__() if name not in self._HIDDEN_FROM_DIR)

    def __post_init__(self) -> None:
        if self.backend_cache is None:
            from marivo.analysis.executor.backend import BackendCache

            self.backend_cache = BackendCache(self.backend_factory)
        if self.calendars is None:
            from marivo.analysis.calendar.loader import CalendarCache

            self.calendars = CalendarCache(self.project_root)

    @property
    def is_read_only(self) -> bool:
        """Whether this session can execute queries against datasources.

        Returns ``True`` when no backend factory is configured, meaning the
        session can read persisted artifacts but cannot run new analysis that
        touches a datasource.
        """
        return self.backend_factory is None

    def jobs(self) -> list[JobSummary]:
        """Return lightweight summaries for every recorded job, oldest first.

        Each entry is a :class:`JobSummary` (id, intent, status, timing, output
        frame ref). For the full record of a single job, use :meth:`job`.
        """
        summaries: list[JobSummary] = []
        for job_id in list_job_ids(self.layout):
            record = read_job_record(self.layout, job_id)
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
        return read_job_record(self.layout, job_id)

    def frames(self) -> list[FrameRef]:
        """Return a :class:`FrameRef` for each persisted frame in this session.

        Returns an empty list when no frames have been persisted yet.
        """
        if not self.layout.frames_dir.is_dir():
            return []
        refs: list[FrameRef] = []
        for frame_dir in sorted(self.layout.frames_dir.iterdir()):
            meta_file = frame_dir / "meta.json"
            if meta_file.is_file():
                meta = json.loads(meta_file.read_text())
                refs.append(FrameRef(ref=meta["ref"], kind=meta["kind"]))
        return refs

    def get_frame(self, ref: str) -> BaseFrame:
        """Load a persisted frame by ref or artifact_id.

        Reconstructs a live frame object from the on-disk parquet and
        meta.json.  The returned frame is fully functional and can be
        passed to any intent (compare, decompose, etc.).

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

    def frame_summaries(self) -> list[FrameSummaryEntry]:
        """Return rich metadata for each persisted frame in this session.

        Unlike :meth:`frames` which returns lightweight (ref, kind) pairs,
        this method includes metric_id, semantic_kind, and other fields
        needed for semantic lookup across script boundaries.
        """
        if not self.layout.frames_dir.is_dir():
            return []
        entries: list[FrameSummaryEntry] = []
        for frame_dir in sorted(self.layout.frames_dir.iterdir()):
            meta_file = frame_dir / "meta.json"
            if meta_file.is_file():
                meta = json.loads(meta_file.read_text())
                entries.append(
                    FrameSummaryEntry(
                        ref=meta["ref"],
                        kind=meta["kind"],
                        metric_id=meta.get("metric_id"),
                        semantic_kind=meta.get("semantic_kind"),
                        semantic_model=meta.get("semantic_model"),
                        created_at=meta.get("created_at"),
                    )
                )
        return entries

    def close(self) -> None:
        """Release session resources: the evidence store and cached backends.

        Safe to call more than once. After closing, the evidence store is
        reopened lazily on next access via :meth:`evidence_store`.
        """
        if self.judgment_store is not None:
            self.judgment_store.close()
            self.judgment_store = None
        if self.backend_cache is not None:
            self.backend_cache.close_all()

    def evidence_store(self) -> JudgmentStore | None:
        """Return the lazily-opened JudgmentStore, or None if unavailable."""
        if self.judgment_store is not None:
            return self.judgment_store
        if self.judgment_store_unavailable:
            return None
        from marivo.analysis.errors import EvidenceStoreUnavailableError
        from marivo.analysis.evidence.store import open_judgment_store, run_startup_gc

        db_path = self.layout.session_dir / "judgment.db"
        try:
            store = open_judgment_store(db_path)
        except EvidenceStoreUnavailableError:
            self.judgment_store_unavailable = True
            return None
        run_startup_gc(store, self.layout.frames_dir)
        self.judgment_store = store
        return store

    def knowledge(self) -> SessionKnowledge:
        """Return a SessionKnowledge projection for this session."""
        from marivo.analysis.evidence.knowledge import build_session_knowledge

        db_path = self.layout.session_dir / "judgment.db"
        if not db_path.exists():
            from datetime import UTC
            from datetime import datetime as _dt

            from marivo.analysis.evidence.knowledge import SessionKnowledge

            now = _dt.now(UTC)
            return SessionKnowledge(
                session_id=self.id,
                snapshot_id=f"snap_{self.id}_{int(now.timestamp() * 1_000_000)}",
                snapshot_at=now,
                evidence_completeness="unavailable",
            )
        return build_session_knowledge(db_path=db_path, session_id=self.id)

    def findings(
        self,
        *,
        artifact_id: str | None = None,
        finding_type: str | None = None,
        subject: Any = None,
    ) -> Iterator[Finding]:
        """Return Surface 3 findings for this session.

        Prefer ``session.evidence.findings(...)``. This top-level alias is kept
        for backward compatibility.
        """
        return self.evidence.findings(
            artifact_id=artifact_id,
            finding_type=finding_type,
            subject=subject,
        )

    def propositions(
        self,
        *,
        proposition_type: str | None = None,
        subject: Any = None,
        status: str | None = None,
    ) -> Iterator[Proposition]:
        """Return Surface 3 propositions for this session.

        Prefer ``session.evidence.propositions(...)``. This top-level alias is
        kept for backward compatibility.
        """
        return self.evidence.propositions(
            proposition_type=proposition_type,
            subject=subject,
            status=status,
        )

    def assessments(
        self,
        *,
        proposition_id: str | None = None,
        latest_only: bool = True,
    ) -> Iterator[Assessment]:
        """Return Surface 3 assessments for this session.

        Prefer ``session.evidence.assessments(...)``. This top-level alias is
        kept for backward compatibility.
        """
        return self.evidence.assessments(
            proposition_id=proposition_id,
            latest_only=latest_only,
        )

    @property
    def evidence(self) -> EvidenceNamespace:
        """Return Surface 3 evidence lookup helpers."""
        return EvidenceNamespace(self)

    @property
    def discover(self) -> SessionDiscoverNamespace:
        """Return session-bound candidate discovery helpers."""
        return SessionDiscoverNamespace(self)

    @property
    def transform(self) -> SessionTransformNamespace:
        """Return session-bound transform helpers."""
        return SessionTransformNamespace(self)

    def observe(
        self,
        metric: MetricRef,
        *,
        timescope: TimeScopeInput = None,
        grain: GrainInput = None,
        dimensions: list[DimensionRef] | None = None,
        where: dict[DimensionRef, SliceValue] | None = None,
        time_dimension: DimensionRef | None = None,
        expect_shape: SemanticShape | None = None,
    ) -> MetricFrame:
        from marivo.analysis.intents.observe import observe

        return observe(
            metric,
            timescope=timescope,
            grain=grain,
            dimensions=dimensions,
            where=where,
            time_dimension=time_dimension,
            expect_shape=expect_shape,
            session=self,
        )

    def compare(
        self,
        current: MetricFrame,
        baseline: MetricFrame,
        *,
        alignment: AlignmentPolicy | None = None,
    ) -> DeltaFrame:
        from marivo.analysis.intents.compare import compare

        return compare(current, baseline, alignment=alignment, session=self)

    def decompose(
        self,
        frame: DeltaFrame,
        *,
        axis: DimensionRef,
    ) -> AttributionFrame:
        from marivo.analysis.intents.decompose import decompose

        return decompose(frame, axis=axis, session=self)

    def correlate(
        self,
        a: MetricFrame,
        b: MetricFrame,
        *,
        measure_a: str | None = None,
        measure_b: str | None = None,
        alignment: AlignmentPolicy | None = None,
        lag_policy: LagPolicy | None = None,
        method: Literal["pearson"] = "pearson",
    ) -> AssociationResult:
        from marivo.analysis.intents.correlate import correlate

        return correlate(
            a,
            b,
            measure_a=measure_a,
            measure_b=measure_b,
            alignment=alignment,
            lag_policy=lag_policy,
            method=method,
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
    ) -> ForecastFrame:
        from marivo.analysis.intents.forecast import forecast

        return forecast(
            history,
            horizon=horizon,
            model=model,
            seasonality_period=seasonality_period,
            interval_level=interval_level,
            measure_column=measure_column,
            session=self,
        )

    def assess_quality(self, frame: BaseFrame) -> QualityReport:
        from marivo.analysis.intents.assess_quality import assess_quality

        return assess_quality(frame, session=self)

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
    ) -> HypothesisTestResult:
        from marivo.analysis.intents.test import hypothesis_test

        return hypothesis_test(
            a,
            b,
            hypothesis=hypothesis,
            value_a=value_a,
            value_b=value_b,
            alignment=alignment,
            sampling=sampling,
            alpha=alpha,
            session=self,
        )

    def from_pandas(
        self,
        df: Any,
        *,
        description: str | None = None,
        sources: list[Any] | None = None,
    ) -> ExplorationResult:
        from marivo.analysis.escape_hatch import from_pandas

        return from_pandas(df, session=self, description=description, sources=sources)

    def explore_ibis(
        self,
        query_builder: Callable[[Any], Any],
        *,
        datasource: str,
        description: str | None = None,
        sources: list[Any] | None = None,
    ) -> ExplorationResult:
        from marivo.analysis.escape_hatch import explore_ibis

        return explore_ibis(
            query_builder,
            datasource=datasource,
            session=self,
            description=description,
            sources=sources,
        )

    def promote_metric_frame(
        self,
        source: ExplorationResult | pd.DataFrame,
        *,
        policy: PromotionPolicy | None = None,
        metric: MetricRef | None = None,
        semantic_kind: SemanticKind | None = None,
        measure_column: str | None = None,
        axes: dict[str, DimensionRef] | None = None,
        time_axis: str | DimensionRef | None = None,
        semantic_model: str | None = None,
        window: object | None = None,
        where: dict[str, Any] | None = None,
    ) -> MetricFrame:
        from marivo.analysis.escape_hatch import promote_metric_frame

        return promote_metric_frame(
            source,
            policy=policy,
            session=self,
            metric=metric,
            semantic_kind=semantic_kind,
            measure_column=measure_column,
            axes=axes,
            time_axis=time_axis,
            semantic_model=semantic_model,
            window=window,
            where=where,
        )

    def promote_delta_frame(
        self,
        source: ExplorationResult | pd.DataFrame,
        *,
        policy: PromotionPolicy | None = None,
        current: ArtifactRef | None = None,
        baseline: ArtifactRef | None = None,
        metric: MetricRef | None = None,
        semantic_kind: SemanticKind | None = None,
        semantic_model: str | None = None,
        delta_column: str | None = None,
        current_column: str | None = None,
        baseline_column: str | None = None,
        alignment: AlignmentPolicy | None = None,
    ) -> DeltaFrame:
        from marivo.analysis.escape_hatch import promote_delta_frame

        return promote_delta_frame(
            source,
            policy=policy,
            session=self,
            current=current,
            baseline=baseline,
            metric=metric,
            semantic_kind=semantic_kind,
            semantic_model=semantic_model,
            delta_column=delta_column,
            current_column=current_column,
            baseline_column=baseline_column,
            alignment=alignment,
        )

    def promote_attribution_frame(
        self,
        source: ExplorationResult | pd.DataFrame,
        *,
        policy: PromotionPolicy | None = None,
        source_delta: ArtifactRef | None = None,
        driver_field: str | None = None,
        contribution_column: str | None = None,
        value_column: str | None = None,
        method: str = "promotion",
        method_params: dict[str, Any] | None = None,
    ) -> AttributionFrame:
        from marivo.analysis.escape_hatch import promote_attribution_frame

        return promote_attribution_frame(
            source,
            policy=policy,
            session=self,
            source_delta=source_delta,
            driver_field=driver_field,
            contribution_column=contribution_column,
            value_column=value_column,
            method=method,
            method_params=method_params,
        )


def ensure_session_writable(session: Session) -> None:
    from marivo.analysis.errors import SessionStateError

    state = session.state
    if session.layout.meta_file.is_file():
        state = read_session_meta(session.layout).get("state", state)
    if state == "archived":
        session.state = "archived"
        raise SessionStateError(message=f"session '{session.name}' is archived")


@dataclass(frozen=True)
class SessionDiscoverNamespace:
    """Session-bound candidate discovery helpers."""

    _session: Session

    def __call__(
        self,
        source: object,
        *,
        objective: Any,
        strategy: Any = None,
        value: str | None = None,
        threshold: float | None = None,
        sensitivity: str = "balanced",
        limit: int | None = None,
        search_space: list[Any] | None = None,
        peer_scope: list[Any] | None = None,
    ) -> CandidateSet:
        from marivo.analysis.intents.discover import discover

        return discover(
            source,
            objective=objective,
            strategy=strategy,
            value=value,
            threshold=threshold,
            sensitivity=cast("Any", sensitivity),
            limit=limit,
            search_space=search_space,
            peer_scope=peer_scope,
            session=self._session,
        )

    def point_anomalies(
        self,
        source: Any,
        *,
        value: str | None = None,
        threshold: float | None = None,
    ) -> CandidateSet:
        from marivo.analysis.intents.discover import discover

        return discover.point_anomalies(
            source,
            value=value,
            threshold=threshold,
            session=self._session,
        )

    def period_shifts(
        self,
        source: Any,
        *,
        value: str | None = None,
        threshold: float | None = None,
    ) -> CandidateSet:
        from marivo.analysis.intents.discover import discover

        return discover.period_shifts(
            source,
            value=value,
            threshold=threshold,
            session=self._session,
        )

    def driver_axes(
        self,
        source: Any,
        *,
        search_space: list[Any],
        value: str | None = None,
        limit: int | None = None,
    ) -> CandidateSet:
        from marivo.analysis.intents.discover import discover

        return discover.driver_axes(
            source,
            search_space=search_space,
            value=value,
            limit=limit,
            session=self._session,
        )

    def interesting_slices(
        self,
        source: Any,
        *,
        search_space: list[Any] | None = None,
        value: str | None = None,
        threshold: float | None = None,
        limit: int | None = None,
    ) -> CandidateSet:
        from marivo.analysis.intents.discover import discover

        return discover.interesting_slices(
            source,
            search_space=search_space,
            value=value,
            threshold=threshold,
            limit=limit,
            session=self._session,
        )

    def interesting_windows(
        self,
        source: Any,
        *,
        value: str | None = None,
        threshold: float | None = None,
    ) -> CandidateSet:
        from marivo.analysis.intents.discover import discover

        return discover.interesting_windows(
            source,
            value=value,
            threshold=threshold,
            session=self._session,
        )

    def cross_sectional_outliers(
        self,
        source: Any,
        *,
        peer_scope: list[Any] | None = None,
        value: str | None = None,
        threshold: float | None = None,
    ) -> CandidateSet:
        from marivo.analysis.intents.discover import discover

        return discover.cross_sectional_outliers(
            source,
            peer_scope=peer_scope,
            value=value,
            threshold=threshold,
            session=self._session,
        )


@dataclass(frozen=True)
class SessionTransformNamespace:
    """Session-bound family-preserving transform helpers."""

    _session: Session

    def __call__(
        self,
        frame: object,
        *,
        op: Any,
        where: Any = None,
        predicate: Any = None,
        drop_axes: Any = None,
        by: Any = None,
        limit: int | None = None,
        order: str | None = None,
        method: str = "ordinal",
        rank_column: str = "rank",
        mode: str | None = None,
        baseline: Any = None,
        window: Any = None,
    ) -> MetricFrame | DeltaFrame:
        from marivo.analysis.intents.transform import transform

        return transform(
            frame,
            op=op,
            where=where,
            predicate=predicate,
            drop_axes=drop_axes,
            by=by,
            limit=limit,
            order=order,
            method=method,
            rank_column=rank_column,
            mode=mode,
            baseline=baseline,
            window=window,
            session=self._session,
        )

    def filter(self, frame: object, *, predicate: Callable[[Any], Any]) -> MetricFrame | DeltaFrame:
        from marivo.analysis.intents.transform import transform

        return transform.filter(frame, predicate=predicate, session=self._session)

    def slice(self, frame: object, *, where: dict[DimensionRef, Any]) -> MetricFrame | DeltaFrame:
        from marivo.analysis.intents.transform import transform

        return transform.slice(frame, where=where, session=self._session)

    def rollup(self, frame: object, *, drop_axes: list[DimensionRef]) -> MetricFrame | DeltaFrame:
        from marivo.analysis.intents.transform import transform

        return transform.rollup(frame, drop_axes=drop_axes, session=self._session)

    def topk(
        self,
        frame: object,
        *,
        by: str,
        limit: int,
        order: str | None = None,
    ) -> MetricFrame | DeltaFrame:
        from marivo.analysis.intents.transform import transform

        return transform.topk(
            frame,
            by=by,
            limit=limit,
            order=cast("Any", order),
            session=self._session,
        )

    def bottomk(self, frame: object, *, by: str, limit: int) -> MetricFrame | DeltaFrame:
        from marivo.analysis.intents.transform import transform

        return transform.bottomk(frame, by=by, limit=limit, session=self._session)

    def rank(
        self,
        frame: object,
        *,
        by: str,
        method: str = "ordinal",
        rank_column: str = "rank",
    ) -> MetricFrame | DeltaFrame:
        from marivo.analysis.intents.transform import transform

        return transform.rank(
            frame,
            by=by,
            method=cast("Any", method),
            rank_column=rank_column,
            session=self._session,
        )

    def normalize(
        self,
        frame: Any,
        *,
        mode: str,
        baseline: Any = None,
    ) -> MetricFrame:
        from marivo.analysis.intents.transform import transform

        return transform.normalize(
            frame,
            mode=cast("Any", mode),
            baseline=baseline,
            session=self._session,
        )

    def window(self, frame: object, *, window: Any) -> MetricFrame | DeltaFrame:
        from marivo.analysis.intents.transform import transform

        return transform.window(frame, window=window, session=self._session)


@dataclass(frozen=True)
class EvidenceNamespace:
    """Session-scoped Surface 3 evidence object lookups."""

    _session: Session

    def findings(
        self,
        *,
        artifact_id: str | None = None,
        finding_type: str | None = None,
        subject: Any = None,
    ) -> Iterator[Finding]:
        """Return Surface 3 findings for this session."""
        from marivo.analysis.evidence.audit import query_findings

        return query_findings(
            db_path=self._session.layout.session_dir / "judgment.db",
            session_id=self._session.id,
            artifact_id=artifact_id,
            finding_type=finding_type,
            subject=subject,
        )

    def propositions(
        self,
        *,
        proposition_type: str | None = None,
        subject: Any = None,
        status: str | None = None,
    ) -> Iterator[Proposition]:
        """Return Surface 3 propositions for this session."""
        from marivo.analysis.evidence.audit import query_propositions

        return query_propositions(
            db_path=self._session.layout.session_dir / "judgment.db",
            session_id=self._session.id,
            proposition_type=proposition_type,
            subject=subject,
            status=status,
        )

    def assessments(
        self,
        *,
        proposition_id: str | None = None,
        latest_only: bool = True,
    ) -> Iterator[Assessment]:
        """Return Surface 3 assessments for this session."""
        from marivo.analysis.evidence.audit import query_assessments

        return query_assessments(
            db_path=self._session.layout.session_dir / "judgment.db",
            session_id=self._session.id,
            proposition_id=proposition_id,
            latest_only=latest_only,
        )

    def proposition(self, proposition_id: str) -> Proposition:
        """Return the proposition with the given id for this session."""
        from marivo.analysis.evidence.audit import get_proposition

        return get_proposition(
            db_path=self._session.layout.session_dir / "judgment.db",
            proposition_id=proposition_id,
        )

    def latest_assessment(self, proposition_id: str) -> Assessment | None:
        """Return the most recent assessment for a proposition, or None.

        Returns ``None`` when the proposition has never been assessed.
        """
        from marivo.analysis.evidence.audit import get_latest_assessment

        return get_latest_assessment(
            db_path=self._session.layout.session_dir / "judgment.db",
            proposition_id=proposition_id,
        )

    def trace(self, proposition_id: str) -> EvidenceTrace:
        """Return the full evidence trace for a proposition.

        The trace links the proposition to its supporting findings and
        assessments for audit and explanation.
        """
        from marivo.analysis.evidence.audit import build_evidence_trace

        return build_evidence_trace(
            db_path=self._session.layout.session_dir / "judgment.db",
            proposition_id=proposition_id,
        )
