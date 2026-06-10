"""Session class and session-local summaries."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

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
    from marivo.analysis.frames.candidate import CandidateObjective, CandidateSet, CandidateStrategy
    from marivo.analysis.frames.delta import DeltaFrame
    from marivo.analysis.frames.exploration import ExplorationResult
    from marivo.analysis.frames.forecast import ForecastFrame
    from marivo.analysis.frames.hypothesis import HypothesisTestResult
    from marivo.analysis.frames.metric import MetricFrame
    from marivo.analysis.frames.quality import QualityReport
    from marivo.analysis.intents._shape import SemanticShape
    from marivo.analysis.intents._types import DiscoverSensitivity, SliceValue
    from marivo.analysis.intents.transform import NormalizeKind, TransformOp
    from marivo.analysis.policies import AlignmentPolicy, LagPolicy, PromotionPolicy, SamplingPolicy
    from marivo.analysis.refs import ArtifactRef, DimensionRef, MetricRef
    from marivo.analysis.windows.spec import GrainInput, TimeScopeInput
    from marivo.semantic.reader import SemanticProject

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
class FrameRecord:
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


class Session:
    __slots__ = (
        "_backend_cache",
        "_backend_factory",
        "_calendars",
        "_created_at",
        "_cwd",
        "_default_calendar",
        "_id",
        "_judgment_store",
        "_judgment_store_unavailable",
        "_known_calendars",
        "_known_datasources",
        "_layout",
        "_name",
        "_project_root",
        "_question",
        "_semantic_project",
        "_state",
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
        state: SessionState,
        created_at: datetime,
        updated_at: datetime,
        backend_factory: BackendFactory | None,
        layout: PersistenceLayout,
        semantic_project: SemanticProject,
        tz: tzinfo | None = None,
        default_calendar: str | None = None,
        known_calendars: set[str] | None = None,
        calendars: Any = None,
        known_datasources: set[str] | None = None,
        backend_cache: Any = None,
        judgment_store: JudgmentStore | None = None,
        judgment_store_unavailable: bool = False,
    ) -> None:
        self._id = id
        self._name = name
        self._question = question
        self._cwd = cwd
        self._project_root = project_root
        self._state = state
        self._created_at = created_at
        self._updated_at = updated_at
        self._backend_factory = backend_factory
        self._layout = layout
        self._semantic_project = semantic_project
        self._tz = tz if tz is not None else resolve_system_timezone().tz
        self._default_calendar = default_calendar
        self._known_calendars = known_calendars if known_calendars is not None else set()
        self._calendars = calendars
        self._known_datasources = known_datasources if known_datasources is not None else set()
        self._backend_cache = backend_cache
        self._judgment_store = judgment_store
        self._judgment_store_unavailable = judgment_store_unavailable
        if self._backend_cache is None:
            from marivo.analysis.executor.backend import BackendCache

            self._backend_cache = BackendCache(self._backend_factory)
        if self._calendars is None:
            from marivo.analysis.calendar.loader import CalendarCache

            self._calendars = CalendarCache(self._project_root)

    def __repr__(self) -> str:
        return f"Session(name={self._name!r}, id={self._id!r})"

    def __dir__(self) -> list[str]:
        return sorted(
            name
            for name in super().__dir__()
            if not (name.startswith("_") and not name.startswith("__"))
        )

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
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def tz(self) -> tzinfo:
        return self._tz

    @property
    def default_calendar(self) -> str | None:
        return self._default_calendar

    @property
    def state(self) -> SessionState:
        return self._state

    @state.setter
    def state(self, value: SessionState) -> None:
        if value not in ("active", "archived"):
            raise ValueError(f"Invalid session state: {value!r}")
        self._state = value

    @property
    def is_read_only(self) -> bool:
        """Whether this session can execute queries against datasources.

        Returns ``True`` when no backend factory is configured, meaning the
        session can read persisted artifacts but cannot run new analysis that
        touches a datasource.
        """
        return self._backend_factory is None

    def jobs(self) -> list[JobSummary]:
        """Return lightweight summaries for every recorded job, oldest first.

        Each entry is a :class:`JobSummary` (id, intent, status, timing, output
        frame ref). For the full record of a single job, use :meth:`job`.
        """
        summaries: list[JobSummary] = []
        for job_id in list_job_ids(self._layout):
            record = read_job_record(self._layout, job_id)
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
        return read_job_record(self._layout, job_id)

    def frames(self) -> list[FrameRecord]:
        """Return a :class:`FrameRecord` for each persisted frame in this session.

        Returns an empty list when no frames have been persisted yet.
        """
        if not self._layout.frames_dir.is_dir():
            return []
        refs: list[FrameRecord] = []
        for frame_dir in sorted(self._layout.frames_dir.iterdir()):
            meta_file = frame_dir / "meta.json"
            if meta_file.is_file():
                meta = json.loads(meta_file.read_text())
                refs.append(FrameRecord(ref=meta["ref"], kind=meta["kind"]))
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
        if not self._layout.frames_dir.is_dir():
            return []
        entries: list[FrameSummaryEntry] = []
        for frame_dir in sorted(self._layout.frames_dir.iterdir()):
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
        reopened lazily on next access via :meth:`_evidence_store`.
        """
        if self._judgment_store is not None:
            self._judgment_store.close()
            self._judgment_store = None
        if self._backend_cache is not None:
            self._backend_cache.close_all()

    def _evidence_store(self) -> JudgmentStore | None:
        """Return the lazily-opened JudgmentStore, or None if unavailable."""
        if self._judgment_store is not None:
            return self._judgment_store
        if self._judgment_store_unavailable:
            return None
        from marivo.analysis.errors import EvidenceStoreUnavailableError
        from marivo.analysis.evidence.store import open_judgment_store, run_startup_gc

        db_path = self._layout.session_dir / "judgment.db"
        try:
            store = open_judgment_store(db_path)
        except EvidenceStoreUnavailableError:
            self._judgment_store_unavailable = True
            return None
        run_startup_gc(store, self._layout.frames_dir)
        self._judgment_store = store
        return store

    def knowledge(self) -> SessionKnowledge:
        """Return a SessionKnowledge projection for this session."""
        from marivo.analysis.evidence.knowledge import build_session_knowledge

        db_path = self._layout.session_dir / "judgment.db"
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
    if session._layout.meta_file.is_file():
        state = read_session_meta(session._layout).get("state", state)
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
        objective: CandidateObjective | str,
        strategy: CandidateStrategy | None = None,
        value: str | None = None,
        threshold: float | None = None,
        sensitivity: DiscoverSensitivity = "balanced",
        limit: int | None = None,
        search_space: list[DimensionRef] | None = None,
        peer_scope: list[DimensionRef] | None = None,
    ) -> CandidateSet:
        from marivo.analysis.intents.discover import discover

        return discover(
            source,
            objective=objective,
            strategy=strategy,
            value=value,
            threshold=threshold,
            sensitivity=sensitivity,
            limit=limit,
            search_space=search_space,
            peer_scope=peer_scope,
            session=self._session,
        )

    def point_anomalies(
        self,
        source: MetricFrame,
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
        source: DeltaFrame,
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
        source: DeltaFrame,
        *,
        search_space: list[DimensionRef],
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
        source: MetricFrame | DeltaFrame,
        *,
        search_space: list[DimensionRef] | None = None,
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
        source: MetricFrame | DeltaFrame,
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
        source: MetricFrame,
        *,
        peer_scope: list[DimensionRef] | None = None,
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
        op: TransformOp,
        where: dict[DimensionRef, SliceValue] | None = None,
        predicate: Callable[..., Any] | None = None,
        drop_axes: list[DimensionRef] | None = None,
        by: str | None = None,
        limit: int | None = None,
        order: str | None = None,
        method: str = "ordinal",
        rank_column: str = "rank",
        mode: str | None = None,
        baseline: object | None = None,
        window: object | None = None,
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
        frame: MetricFrame,
        *,
        mode: NormalizeKind,
        baseline: object | None = None,
    ) -> MetricFrame:
        from marivo.analysis.intents.transform import transform

        return transform.normalize(
            frame,
            mode=mode,
            baseline=baseline,
            session=self._session,
        )

    def window(self, frame: object, *, window: object) -> MetricFrame | DeltaFrame:
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
            db_path=self._session._layout.session_dir / "judgment.db",
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
            db_path=self._session._layout.session_dir / "judgment.db",
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
            db_path=self._session._layout.session_dir / "judgment.db",
            session_id=self._session.id,
            proposition_id=proposition_id,
            latest_only=latest_only,
        )

    def proposition(self, proposition_id: str) -> Proposition:
        """Return the proposition with the given id for this session."""
        from marivo.analysis.evidence.audit import get_proposition

        return get_proposition(
            db_path=self._session._layout.session_dir / "judgment.db",
            proposition_id=proposition_id,
        )

    def latest_assessment(self, proposition_id: str) -> Assessment | None:
        """Return the most recent assessment for a proposition, or None.

        Returns ``None`` when the proposition has never been assessed.
        """
        from marivo.analysis.evidence.audit import get_latest_assessment

        return get_latest_assessment(
            db_path=self._session._layout.session_dir / "judgment.db",
            proposition_id=proposition_id,
        )

    def trace(self, proposition_id: str) -> EvidenceTrace:
        """Return the full evidence trace for a proposition.

        The trace links the proposition to its supporting findings and
        assessments for audit and explanation.
        """
        from marivo.analysis.evidence.audit import build_evidence_trace

        return build_evidence_trace(
            db_path=self._session._layout.session_dir / "judgment.db",
            proposition_id=proposition_id,
        )
