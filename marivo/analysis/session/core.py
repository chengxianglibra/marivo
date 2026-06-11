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
    from marivo.analysis.frames.candidate import CandidateSet
    from marivo.analysis.frames.delta import DeltaFrame
    from marivo.analysis.frames.exploration import ExplorationResult
    from marivo.analysis.frames.forecast import ForecastFrame
    from marivo.analysis.frames.hypothesis import HypothesisTestResult
    from marivo.analysis.frames.metric import MetricFrame
    from marivo.analysis.frames.quality import QualityReport
    from marivo.analysis.intents._shape import SemanticShape
    from marivo.analysis.intents._types import SliceValue
    from marivo.analysis.intents.transform import NormalizeKind
    from marivo.analysis.policies import AlignmentPolicy, PromotionPolicy, SamplingPolicy
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
        """Materialize a metric into a typed MetricFrame.

        When to use: starting point for any metric analysis workflow.

        Resolves ``metric`` against the active semantic project, applies the
        optional ``timescope`` / ``grain`` / ``dimensions`` / ``where`` filters, executes against
        the session's backend, and persists the result as a MetricFrame on disk.

        Args:
            metric: Wrap the registered metric id with ``mv.MetricRef("<domain>.<metric>")``.
                Bare strings are rejected.
            timescope: Half-open time range ``{"start": ..., "end": ...}`` — start is
                inclusive, end is exclusive.  For date-only strings, ``end="2026-08-01"``
                means data from August 1 is **not** included.
            grain: Optional time bucket grain. When present, observe returns a time
                series or panel depending on ``dimensions``.
            dimensions: Segment axes. In v1 all dimensions must resolve to the same
                entity as ``metric``.
            where: Pre-aggregation row filter. Keys are ``mv.DimensionRef(...)`` for
                the filtered dimension; values are either a scalar (``==``), a list
                (``in``), or ``{"op": "<op>", "value": ...}`` where op is one of
                ``==, !=, in, >, >=, <, <=, between``.
            time_dimension: Pick the entity time axis as
                ``mv.DimensionRef("<time_dimension>")`` when an entity declares multiple
                ``@ms.time_dimension`` columns. Omit when the entity has a single (or
                default) time dimension.
            expect_shape: Optional guard. If set, observe predicts the output shape
                from ``grain``/``dimensions`` and raises ``SemanticKindMismatchError``
                before any backend work when the prediction differs.

        Raises:
            MetricNotFoundError: The metric id is unknown or not ``<domain>.<metric>``.
            SemanticKindMismatchError: ``metric`` is not a ``MetricRef``, ``time_dimension``
                is not a ``DimensionRef``, or a ``where`` key is not a ``DimensionRef``.
            ObservePlanningError: Planning failed (e.g. cross-datasource plan, missing
                path, ambiguous dimension). Check ``details["code"]`` for the specific
                error code.

        Example:
            >>> frame = session.observe(
            ...     mv.MetricRef("sales.revenue"),
            ...     timescope={"start": "2026-07-01", "end": "2026-10-01"},
            ...     grain="day",
            ...     dimensions=[mv.DimensionRef("country")],
            ... )
            >>> frame.summary()
        """
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
        """Compute the typed delta between two MetricFrames (current minus baseline).

        When to use: quantify change between two periods; produces a DeltaFrame for decompose or discover.

        The two frames must share ``metric_id`` and ``semantic_kind``. ``segmented``
        frames must share segment columns; ``panel`` frames must share grain.

        Args:
            current: Current-period MetricFrame.
            baseline: Baseline-period MetricFrame.
            alignment: Defaults to ``AlignmentPolicy(kind="window_bucket")``. For
                ``segmented`` frames, only ``window_bucket`` is supported in v1.

        Raises:
            SemanticKindMismatchError: Different ``metric_id``, ``semantic_kind``, or
                ``current``/``baseline`` is not a MetricFrame.
            SegmentDimensionMismatchError: ``segmented`` frames disagree on segment columns.
            PanelGrainMismatchError: ``panel`` frames disagree on time grain.
            AlignmentPolicyNotApplicableError: Alignment kind incompatible with the frame shape.
            CrossSessionFrameError: A frame belongs to a different session.

        Example:
            >>> cur  = session.observe(mv.MetricRef("sales.revenue"), timescope={"start": "2026-07-01", "end": "2026-10-01"})
            >>> base = session.observe(mv.MetricRef("sales.revenue"), timescope={"start": "2025-07-01", "end": "2025-10-01"})
            >>> delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))
        """
        from marivo.analysis.intents.compare import compare

        return compare(current, baseline, alignment=alignment, session=self)

    def decompose(
        self,
        frame: DeltaFrame,
        *,
        axis: DimensionRef,
    ) -> AttributionFrame:
        """Attribute a DeltaFrame's movement across a chosen segment axis.

        When to use: attribute a delta to dimension segments (why did revenue drop?).

        For ``panel`` deltas, ``axis`` must be one of the frame's segment dimensions.
        For ``time_series`` deltas, ``axis`` is the bucket-start column.

        Args:
            frame: A DeltaFrame produced by ``session.compare``.
            axis: The segment column to attribute over, wrapped in ``mv.DimensionRef``.
                Dotted ids such as ``"model.field"`` resolve to the persisted
                DeltaFrame column ``"field"`` when present.

        Raises:
            SemanticKindMismatchError: ``frame`` is not a DeltaFrame, or ``axis`` is not a DimensionRef.
            AxisNotInPanelDimensionsError: ``axis`` is not a segment column of the panel.
            CrossSessionFrameError: ``frame`` belongs to a different session.

        Example:
            >>> delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))
            >>> attribution = session.decompose(delta, axis=mv.DimensionRef("country"))
            >>> attribution.summary()
        """
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
        method: Literal["pearson"] = "pearson",
    ) -> AssociationResult:
        """Measure the association between two MetricFrames over aligned buckets.

        When to use: measure statistical association between two metrics over aligned time buckets.

        v1 only supports Pearson correlation under ``window_bucket`` alignment with
        zero-lag behavior. Both frames must belong to the active session.

        Args:
            a: First MetricFrame.
            b: Second MetricFrame.
            measure_a: Numeric column on ``a``. Defaults to the frame's measure column.
            measure_b: Numeric column on ``b``. Defaults to the frame's measure column.
            alignment: Defaults to ``AlignmentPolicy(kind="window_bucket")``.
            method: Only ``"pearson"`` in v1.

        Raises:
            SemanticKindMismatchError: Inputs are not MetricFrames, or alignment
                kinds are unsupported.
            AlignmentFailedError: Frames cannot be aligned (e.g. no overlapping buckets).
            CrossSessionFrameError: A frame belongs to a different session.

        Example:
            >>> result = session.correlate(
            ...     a, b,
            ...     alignment=mv.AlignmentPolicy(kind="window_bucket"),
            ... )
            >>> result.summary()
        """
        from marivo.analysis.intents.correlate import correlate

        return correlate(
            a,
            b,
            measure_a=measure_a,
            measure_b=measure_b,
            alignment=alignment,
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
            measure_column: Numeric column to forecast. Defaults to the frame's measure column.

        Raises:
            ForecastShapeUnsupportedError: ``history`` is not a time_series / panel MetricFrame,
                or its grain is not in {day, week, month, quarter}.
            ForecastPolicyError: ``horizon`` or ``interval_level`` is out of range.
            ForecastInsufficientHistoryError: Not enough rows for the chosen model.
            ForecastInputQualityError: ``history`` contains NaN values in ``value``.
            CrossSessionFrameError: ``history`` belongs to a different session.

        Example:
            >>> history = session.observe(
            ...     mv.MetricRef("sales.revenue"),
            ...     timescope={"start": "2026-01-01", "end": "2026-04-01"}, grain="day",
            ... )
            >>> forecast = session.forecast(history, horizon=30)
            >>> forecast.summary()
        """
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
        """Run quality checks over a MetricFrame and return a structured report.

        When to use: check data quality (nulls, outliers, coverage) before analysis.

        v1 accepts only MetricFrames. Reports for DeltaFrame / CandidateSet /
        ForecastFrame / AttributionFrame are planned for later releases. The
        returned QualityReport carries per-check rows, blocking issues, and a list
        of recommended follow-up intents.

        Args:
            frame: A MetricFrame to inspect.

        Raises:
            QualityShapeUnsupportedError: ``frame`` is not a MetricFrame.
            CrossSessionFrameError: ``frame`` belongs to a different session.

        Example:
            >>> report = session.assess_quality(frame)
            >>> for issue in report.blocking_issues:
            ...     print(issue)
        """
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
            value_a: Numeric column on ``a``. Defaults to the frame's measure column.
            value_b: Numeric column on ``b``. Defaults to the frame's measure column.
            alignment: Defaults to ``AlignmentPolicy(kind="window_bucket")``.
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
            >>> result = session.hypothesis_test(cur, base)
            >>> result.summary()
        """
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
        """Import a pandas DataFrame into the session as an ExplorationResult.

        Use this when you have data from an external source (CSV, API response,
        manual construction) that you want to bring into the Marivo analysis
        pipeline. The returned ExplorationResult is an untyped scratch frame;
        promote it with ``promote_metric_frame`` or similar before passing to
        typed intents like ``compare`` or ``decompose``.

        Args:
            df: Source DataFrame. A defensive copy is made internally.
            description: Human-readable note stored in frame metadata.
            sources: Optional lineage references to upstream artifacts that
                produced this data.

        Returns:
            An ExplorationResult persisted to the session's frame store.

        Raises:
            SessionNotWritableError: If the resolved session is read-only.

        Example:
            >>> result = session.from_pandas(my_df, description="daily sales extract")
            >>> mf = session.promote_metric_frame(result, metric=metric_ref, ...)
        """
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
        """Run an ibis query against a datasource and return an ExplorationResult.

        Use this when you need to query a registered datasource with custom ibis
        logic that goes beyond what the semantic model exposes. The query is
        executed immediately and the result is persisted as an untyped scratch
        frame. Promote the result before passing to typed intents.

        Args:
            query_builder: A callable that receives an ibis backend connection
                and returns an ibis expression (table or column expression).
                The callable runs in its own closure scope — you must
                ``import ibis`` (or ``from ibis import _``) in your module
                before using ibis top-level names like ``ibis.desc()`` or
                ``_`` inside the callable.
            datasource: Name of the datasource registered in the session's
                backend cache.
            description: Human-readable note stored in frame metadata.
            sources: Optional lineage references to upstream artifacts.

        Returns:
            An ExplorationResult containing the query result, persisted to disk.

        Raises:
            TypeError: If ``query_builder`` does not return a valid ibis expression.
            NameError: If the callable references ibis names not in scope
                (e.g. forgot ``import ibis``).
            SessionNotWritableError: If the resolved session is read-only.

        Example:
            >>> import ibis
            >>> result = session.explore_ibis(
            ...     lambda con: con.table("orders").order_by(ibis.desc("amount")),
            ...     datasource="warehouse",
            ... )
        """
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
        """Upgrade an ExplorationResult or DataFrame into a typed MetricFrame.

        Use this when you have raw tabular data that represents a metric
        observation and you need to feed it into typed intents (``compare``,
        ``decompose``, etc.) that require a MetricFrame.

        No metadata is auto-inferred. ``semantic_kind``, ``measure_column``, and
        ``semantic_model`` must be supplied explicitly. ``metric`` and
        ``time_axis`` may instead fall back to ``policy.semantic_anchors``.
        ``axes``, ``window``, and ``where`` are only read from explicit arguments.

        Args:
            source: An ExplorationResult or raw pandas DataFrame to promote.
            policy: Supplies ``semantic_anchors`` fallback values and extra
                ``required_fields`` checks. Promotion always fails closed on
                missing metadata.
            metric: Reference to the semantic metric this frame measures.
                Falls back to ``policy.semantic_anchors.metric``.
            semantic_kind: One of "scalar", "time_series", "segmented", "panel".
            measure_column: Column name holding the numeric measure values.
            axes: Mapping of column name to DimensionRef for dimension axes.
            time_axis: Column name or DimensionRef for the time dimension.
                Falls back to ``policy.semantic_anchors.time_axis``.
            semantic_model: Name of the semantic model this metric belongs to.
            window: Absolute time window specification.
            where: Filter predicates applied to the observation.

        Returns:
            A MetricFrame persisted to the session's frame store.

        Raises:
            PromotionFailedError: If required fields are missing, columns are
                invalid, or the session's semantic project is ready with metrics
                defined and the metric id is not in the catalog.
            SessionNotWritableError: If the resolved session is read-only.

        Example:
            >>> mf = session.promote_metric_frame(
            ...     exploration_result,
            ...     metric=MetricRef("revenue"),
            ...     semantic_kind="time_series",
            ...     measure_column="total_revenue",
            ...     semantic_model="sales",
            ...     time_axis="order_date",
            ... )
        """
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
        """Upgrade an ExplorationResult or DataFrame into a typed DeltaFrame.

        Use this when you have pre-computed difference data between two metric
        observations and need a typed frame for downstream intents like
        ``decompose`` (attribution analysis).

        Required parameters: current, baseline, delta_column, current_column,
        baseline_column. ``current`` and ``baseline`` may instead fall back to
        ``policy.semantic_anchors``. Additionally metric, semantic_kind, and
        semantic_model are required but inherited from the referenced current
        MetricFrame when not supplied. No metadata is auto-inferred.

        Args:
            source: An ExplorationResult or raw DataFrame containing delta data.
            policy: Supplies ``semantic_anchors`` fallback values. Promotion
                always fails closed on missing metadata.
            current: ArtifactRef pointing to the "current" MetricFrame.
                Falls back to ``policy.semantic_anchors.current``.
            baseline: ArtifactRef pointing to the "baseline" MetricFrame.
                Falls back to ``policy.semantic_anchors.baseline``.
            metric: Override metric reference (must match source frames if given).
            semantic_kind: Override semantic kind (must match source frames).
            semantic_model: Override semantic model name (must match source frames).
            delta_column: Column name holding the numeric delta values.
            current_column: Column with current-period raw values. Required for
                the delta formula consistency check.
            baseline_column: Column with baseline-period raw values. Required for
                the delta formula consistency check.
            alignment: How current and baseline periods are aligned.
                Defaults to window_bucket alignment.

        Returns:
            A DeltaFrame persisted to the session's frame store.

        Raises:
            PromotionFailedError: If required fields are missing, columns are
                invalid, current/baseline metadata is inconsistent, or the
                session's semantic project is ready with metrics defined and the
                metric id is not in the catalog.
            SessionNotWritableError: If the resolved session is read-only.

        Example:
            >>> df = session.promote_delta_frame(
            ...     delta_exploration,
            ...     current=ArtifactRef(current_mf.ref),
            ...     baseline=ArtifactRef(baseline_mf.ref),
            ...     delta_column="revenue_change",
            ... )
        """
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
        """Upgrade an ExplorationResult or DataFrame into a typed AttributionFrame.

        Use this when you have pre-computed attribution/decomposition data that
        explains which drivers contributed to a delta, and you need a typed frame
        that the analysis pipeline can consume as evidence.

        Required parameters: source_delta, driver_field, contribution_column.
        ``source_delta`` may instead fall back to ``policy.semantic_anchors``.
        metric, semantic_kind, and semantic_model are inherited from the source
        DeltaFrame. No metadata is auto-inferred.

        Args:
            source: An ExplorationResult or raw DataFrame with attribution rows.
            policy: Supplies ``semantic_anchors`` fallback values. Promotion
                always fails closed on missing metadata.
            source_delta: ArtifactRef pointing to the DeltaFrame being explained.
                Falls back to ``policy.semantic_anchors.source_delta``.
            driver_field: Column name identifying the dimension driver
                (e.g., "region", "product_category").
            contribution_column: Numeric column holding each driver's contribution
                to the total delta. Must not contain NaN values.
            value_column: Optional column with the absolute value per driver.
            method: Attribution method label. Defaults to "promotion".
            method_params: Additional parameters for the attribution method.

        Returns:
            An AttributionFrame persisted to the session's frame store.

        Raises:
            PromotionFailedError: If required fields are missing, columns are
                invalid, or contribution_column contains null values.
            SessionNotWritableError: If the resolved session is read-only.

        Example:
            >>> af = session.promote_attribution_frame(
            ...     attribution_data,
            ...     source_delta=ArtifactRef(delta_frame.ref),
            ...     driver_field="region",
            ...     contribution_column="contribution",
            ... )
        """
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

    def point_anomalies(
        self,
        source: MetricFrame,
        *,
        value: str | None = None,
        threshold: float | None = None,
    ) -> CandidateSet:
        """Find time-series points with unusual values.

        Source must be a MetricFrame with time_series or panel shape.
        ``threshold`` is an absolute z-score cutoff (|z| >= threshold); default 3.0.
        Lower values flag more candidates.
        """
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
        """Find period-shift candidates from a DeltaFrame.

        Requires at least four time buckets in a time-series delta, or at least
        one panel series with four time buckets.
        ``threshold`` is an absolute z-score cutoff on rolling window means
        (|z| >= threshold); default 2.0.
        """
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
        """Find dimensions that explain a delta.

        Source must be a DeltaFrame. ``search_space`` is required and lists
        the candidate dimensions to evaluate for explanatory power.
        """
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
        """Find dimension slices with notable values.

        Accepts a MetricFrame or DeltaFrame. Optionally narrow the search
        with ``search_space``; otherwise all available dimensions are probed.
        ``threshold`` is an absolute z-score for MetricFrame (|z| >= threshold)
        or absolute delta value for DeltaFrame; default 2.0.
        """
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
        """Find time windows with notable behavior.

        Source must have time_series or panel shape. Returns windows where
        the metric exhibits significant trends, level shifts, or volatility.
        ``threshold`` is an absolute z-score cutoff (|z| >= threshold); default 2.0.
        """
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
        """Find segments that are outliers compared to their peers.

        Source must be a MetricFrame with segmented or panel shape.
        ``peer_scope`` defines the grouping for peer comparison; defaults to
        all non-time axes.
        ``threshold`` is a robust z-score cutoff using MAD
        (|robust_z| >= threshold); default 3.0.
        """
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

    def filter(self, frame: object, *, predicate: Callable[[Any], Any]) -> MetricFrame | DeltaFrame:
        """Filter rows using a predicate function.

        The predicate receives the underlying DataFrame and must return a
        boolean Series of the same length.
        """
        from marivo.analysis.intents.transform import transform

        return transform.filter(frame, predicate=predicate, session=self._session)

    def slice(self, frame: object, *, where: dict[DimensionRef, Any]) -> MetricFrame | DeltaFrame:
        """Filter rows by exact axis values.

        ``where`` maps ``mv.DimensionRef(...)`` axes to the value(s) to keep.
        Unlike ``filter``, operates on raw axis values without a callable.
        """
        from marivo.analysis.intents.transform import transform

        return transform.slice(frame, where=where, session=self._session)

    def rollup(self, frame: object, *, drop_axes: list[DimensionRef]) -> MetricFrame | DeltaFrame:
        """Aggregate to coarser segments by dropping axes.

        Removes the listed ``mv.DimensionRef(...)`` dimensions and re-aggregates
        measures over the remaining axes.
        """
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
        """Keep the top N rows ranked by a measure column.

        ``order`` defaults to ``"decrease"`` (largest first). Use
        ``"increase"`` to select the smallest values instead.
        """
        from marivo.analysis.intents.transform import transform

        return transform.topk(
            frame,
            by=by,
            limit=limit,
            order=cast("Any", order),
            session=self._session,
        )

    def bottomk(self, frame: object, *, by: str, limit: int) -> MetricFrame | DeltaFrame:
        """Keep the bottom N rows ranked by a measure column.

        Equivalent to ``topk(..., order="increase")``. Returns the rows with
        the smallest values in the ``by`` column.
        """
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
        """Add a rank column ordered by a measure.

        ``method`` controls tie-breaking: ``"ordinal"``, ``"dense"``,
        ``"min"``, or ``"max"``. The new column is named ``rank_column``.
        """
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
        """Convert measure values to a normalized form (MetricFrame only).

        Supported modes: ``"index"``, ``"share"``, ``"pct_change"``,
        ``"per_unit"``, ``"z_score"``. ``baseline`` sets the reference point
        when required by the mode.
        """
        from marivo.analysis.intents.transform import transform

        return transform.normalize(
            frame,
            mode=mode,
            baseline=baseline,
            session=self._session,
        )

    def window(self, frame: object, *, window: object) -> MetricFrame | DeltaFrame:
        """Restrict a frame to a time window.

        ``window`` is an ``AbsoluteWindow`` or compatible specification that
        defines the start/end bounds. The returned frame contains only rows
        within those bounds, preserving the original frame kind.
        """
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
