"""Call mv.help() for bounded agent help over the Marivo analysis runtime."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from marivo.analysis.session._layout import PersistenceLayout, read_job_record
from marivo.analysis.timezone import resolve_system_timezone
from marivo.render import Card, RenderableResult

if TYPE_CHECKING:
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
    from marivo.analysis.frames.candidate import CandidateSet, CandidateStrategy
    from marivo.analysis.frames.delta import DeltaFrame
    from marivo.analysis.frames.forecast import ForecastFrame
    from marivo.analysis.frames.hypothesis import HypothesisTestResult
    from marivo.analysis.frames.metric import MetricFrame
    from marivo.analysis.frames.quality import QualityReport
    from marivo.analysis.intents._attribution_mode import AttributionMode
    from marivo.analysis.intents._shape import SemanticShape
    from marivo.analysis.intents._types import SliceValue
    from marivo.analysis.policies import AlignmentPolicy, SamplingPolicy
    from marivo.analysis.semantic_inputs import DimensionInput, MetricInput
    from marivo.analysis.session._store import SessionStore
    from marivo.analysis.windows.spec import GrainInput, TimeScopeInput
    from marivo.semantic.catalog import SemanticCatalog

SemanticKind = Literal["scalar", "time_series", "segmented", "panel"]


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


class Session:
    """Call mv.help(Session) for its public consumption contract."""

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
        judgment_store: JudgmentStore | None = None,
        judgment_store_unavailable: bool = False,
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

        summaries: list[JobSummary] = []
        for row in self._store.list_jobs(self.id):
            job_id = row["job_id"]
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
        from marivo.analysis.errors import JobNotFoundError

        row = self._store.get_job(self.id, job_id)
        if row is None:
            raise JobNotFoundError(
                message=f"no job '{job_id}' in session {self.id!r}",
                context={"session_id": self.id, "job_id": job_id},
            )
        return read_job_record(self._layout, job_id)

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

    def frame_summaries(self) -> list[FrameSummaryEntry]:
        """Return rich metadata for each persisted frame in this session.

        Unlike :meth:`frames` which returns lightweight (ref, kind) pairs,
        this method includes metric_id, semantic_kind, and other fields
        needed for semantic lookup across script boundaries.
        """
        entries: list[FrameSummaryEntry] = []
        for row in self._store.list_artifacts(self.id):
            meta_path = row["meta_path"]
            abs_meta = self._project_root / meta_path
            if abs_meta.is_file():
                meta = json.loads(abs_meta.read_text())
                entries.append(
                    FrameSummaryEntry(
                        ref=meta["ref"],
                        kind=meta["kind"],
                        metric_id=meta.get("metric_id"),
                        semantic_kind=meta.get("semantic_kind"),
                        semantic_model=meta.get("semantic_model"),
                        created_at=meta.get("created_at"),
                        analysis_purpose=meta.get("analysis_purpose"),
                        row_count=meta.get("row_count"),
                        content_hash=meta.get("content_hash"),
                    )
                )
        entries.sort(key=lambda e: (e.created_at or "", e.ref))
        return entries

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

    def _evidence_store(self) -> JudgmentStore | None:
        """Return the lazily-opened JudgmentStore, or None if unavailable."""
        if self._judgment_store is not None:
            return self._judgment_store
        if self._judgment_store_unavailable:
            return None
        from marivo.analysis.errors import EvidenceStoreUnavailableError
        from marivo.analysis.evidence.store import open_judgment_store

        db_path = self._layout.session_dir / "judgment.db"
        try:
            store = open_judgment_store(db_path)
        except EvidenceStoreUnavailableError:
            self._judgment_store_unavailable = True
            return None
        self._judgment_store = store
        return store

    def knowledge(self) -> SessionKnowledge:
        """Return an immutable SessionKnowledge snapshot for this session.

        When to use: synthesis at key decision points, closeout recaps, and
        cross-script session recovery. The snapshot projects judgment.db into
        bounded typed sections:

        - ``knowledge.observations()`` -- observation digests for every
          observe commit, oldest first.
        - ``knowledge.facts(kind=...)`` -- established facts from compare,
          attribute, hypothesis_test, forecast, and correlate.
        - ``knowledge.open_items()`` / ``knowledge.blocked_followups()`` /
          ``knowledge.next_steps()`` -- unresolved work.

        Returns:
            SessionKnowledge snapshot. Read-only; does not create a step or
            enter lineage.

        Example:
            >>> knowledge = session.knowledge()
            >>> knowledge.observations()[-1].digest
            >>> knowledge.facts(kind="change")

        Constraints:
            Check ``knowledge.evidence_completeness`` before consuming the
            lists; ``"unavailable"`` means unknown, not empty.
        """
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

    def observe(
        self,
        metric: MetricInput | list[MetricInput] | tuple[MetricInput, ...],
        *,
        time_scope: TimeScopeInput = None,
        grain: GrainInput = None,
        dimensions: list[DimensionInput] | None = None,
        slice_by: dict[DimensionInput, SliceValue] | None = None,
        time_dimension: DimensionInput | None = None,
        expect_shape: SemanticShape | None = None,
        analysis_purpose: str | None = None,
    ) -> MetricFrame:
        """Materialize a metric into a typed MetricFrame.

        When to use: starting point for any metric analysis workflow.

        Resolves ``metric`` against the active semantic catalog, applies the
        optional ``time_scope`` / ``grain`` / ``dimensions`` / ``slice_by`` filters, executes against
        the session's backend, and persists the result as a MetricFrame on disk.

        ``to_pandas()`` value column naming: a simple single-metric frame uses
        ``"value"``; a derived single-metric frame and all multi-metric frames
        use one column per metric (the metric short name, or a qualified metric
        id when names collide). Read ``frame.value_columns`` for the exact
        exported name(s) before merging or renaming across frames.

        Args:
            metric: Catalog metric object or ``SemanticRef``, or a non-empty
                sequence of them for a multi-metric observation over one shared
                scope. Bare strings are rejected. Sequences accept simple
                metrics and non-cumulative derived metrics (ratio /
                weighted_average / linear); cumulative and folded metrics must
                be observed individually.
            time_scope: Half-open time range ``{"start": ..., "end": ...}`` — start is
                inclusive, end is exclusive.  For date-only strings, ``end="2026-08-01"``
                means data from August 1 is **not** included.
            grain: Optional time bucket grain. When present, observe returns a time
                series or panel depending on ``dimensions``.
            dimensions: Segment axes. Omit, pass ``None``, or pass ``[]`` for no
                segment axes. In v1 all non-empty dimension lists must resolve to
                the same entity as ``metric``.
            slice_by: Pre-aggregation row filter. Keys are catalog dimension objects/refs for
                the filtered dimension; values are either a scalar (``==``), a
                list/tuple/set (``in``), or ``{"op": "<op>", "value": ...}`` where op is one of
                ``==, !=, in, >, >=, <, <=, between``.
            time_dimension: Pick the entity time axis as
                a catalog time-dimension object/ref when an entity declares multiple
                ``@ms.time_dimension`` columns. Omit when the entity has a single (or
                default) time dimension.
            expect_shape: Optional guard. If set, observe predicts the output shape
                from ``grain``/``dimensions`` and raises ``SemanticKindMismatchError``
                before any backend work when the prediction differs.

        Raises:
            MetricNotFoundError: The metric id is unknown or not ``<domain>.<metric>``.
            SemanticKindMismatchError: ``metric`` is not a catalog metric object/ref,
                ``time_dimension`` is not a catalog dimension object/ref, or a ``slice_by``
                key is not a catalog dimension object/ref.
            ObservePlanningError: Planning failed (e.g. cross-datasource plan, missing
                path, ambiguous dimension). Check ``details["code"]`` for the specific
                error code.

        Example:
            >>> catalog = session.catalog
            >>> revenue = catalog.get("metric.sales.revenue")
            >>> country = catalog.get("dimension.sales.orders.country").ref
            >>> frame = session.observe(
            ...     revenue,
            ...     time_scope={"start": "2026-07-01", "end": "2026-10-01"},
            ...     grain="day",
            ...     dimensions=[country],
            ...     analysis_purpose="确认三季度按国家收入走势",
            ... )
            >>> frame.show()
            >>> # Filter to a subset before aggregation with slice_by:
            >>> us_frame = session.observe(
            ...     revenue,
            ...     time_scope={"start": "2026-07-01", "end": "2026-10-01"},
            ...     grain="day",
            ...     slice_by={country: "US"},
            ... )
            >>> us_frame.show()
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.observe import observe

        with _track_session_operation(
            self,
            "marivo.analysis.observe",
            family="core",
            intent="observe",
            attributes={"marivo.analysis.dimension_count": len(dimensions or [])},
        ):
            validate_capability_inputs("observe", metric=metric, time_scope=time_scope)
            return observe(
                metric,
                time_scope=time_scope,
                grain=grain,
                dimensions=dimensions,
                slice_by=slice_by,
                time_dimension=time_dimension,
                expect_shape=expect_shape,
                analysis_purpose=analysis_purpose,
                session=self,
            )

    def compare(
        self,
        current: MetricFrame,
        baseline: MetricFrame,
        *,
        alignment: AlignmentPolicy | None = None,
        analysis_purpose: str | None = None,
    ) -> DeltaFrame:
        """Compute the typed delta between two MetricFrames (current minus baseline).

        When to use: quantify change between two periods; produces a DeltaFrame for attribute or discover.

        The two frames must share ``metric_id`` and ``semantic_kind``. ``segmented``
        frames must share segment columns; ``panel`` frames must share grain.

        Args:
            current: Current-period MetricFrame.
            baseline: Baseline-period MetricFrame.
            alignment: Defaults to ``mv.window_bucket()``. For
                ``segmented`` frames, only ``window_bucket`` is supported in v1.

        Raises:
            SemanticKindMismatchError: Different ``metric_id``, ``semantic_kind``, or
                ``current``/``baseline`` is not a MetricFrame.
            SegmentDimensionMismatchError: ``segmented`` frames disagree on segment columns.
            PanelGrainMismatchError: ``panel`` frames disagree on time grain.
            AlignmentPolicyNotApplicableError: Alignment kind incompatible with the frame shape.
            CrossSessionFrameError: A frame belongs to a different session.

        Example:
            >>> revenue = session.catalog.get("metric.sales.revenue")
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
        from marivo.analysis.intents.compare import compare

        semantic_kind = getattr(current.meta, "semantic_kind", None)
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
            validate_capability_inputs("compare", a=current, b=baseline, alignment=alignment)
            return compare(
                current,
                baseline,
                alignment=alignment,
                analysis_purpose=analysis_purpose,
                session=self,
            )

    def attribute(
        self,
        frame: DeltaFrame,
        *,
        axes: list[DimensionInput],
        mode: AttributionMode | None = None,
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
        Additive deltas support axis-sum attribution. Semi-additive deltas
        support non-time axes but reject their persisted status time axis.
        Component-aware ratio and weighted-average deltas use mix attribution.
        Tier-1 means over a measure are observed with exact sum and non-null
        count components, then use weighted mix attribution. Other non-additive
        metrics, non-additive linear compositions, and deltas missing persisted
        additivity metadata fail closed. Re-observe and compare old artifacts
        before retrying attribution.
        Plain non-linear sampled folds such as percentile, min, max, first, or
        last retain their earlier guard unless they are part of a persisted
        component-aware ratio or weighted-average delta.

        Args:
            frame: A DeltaFrame produced by ``session.compare``.
            axes: One or more catalog dimension refs/objects to attribute over.
            mode: Required for multiple axes. ``"joint"`` returns one row per
                axis combination; ``"hierarchy"`` returns ordered prefix rows.
                Omit for a single axis.

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
            >>> country = session.catalog.get("dimension.sales.orders.country").ref
            >>> channel = session.catalog.get("dimension.sales.orders.channel").ref
            >>> attribution = session.attribute(
            ...     delta,
            ...     axes=[country, channel],
            ...     mode="joint",
            ...     analysis_purpose="按国家归因收入变化",
            ... )
        """
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
            validate_capability_inputs("attribute", frame=frame, axes=axes)
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
        the result carries one row per lag and ``meta.best_lag`` marks the
        strongest. Default is lag 0 only. Both frames must belong to the active session.

        Args:
            a: First MetricFrame.
            b: Second MetricFrame.
            measure_a: Numeric column on ``a``. Defaults to the frame's measure column.
            measure_b: Numeric column on ``b``. Defaults to the frame's measure column.
            alignment: Defaults to ``mv.window_bucket()``.
            method: ``"pearson"``, ``"spearman"``, or ``"kendall"``.
            lag_range: Lags to explore (e.g. ``range(0, 5)``). Defaults to lag 0.

        Raises:
            SemanticKindMismatchError: Inputs are not MetricFrames, or alignment
                kinds are unsupported.
            AlignmentFailedError: Frames cannot be aligned (e.g. no overlapping buckets).
            CrossSessionFrameError: A frame belongs to a different session.

        Example:
            >>> result = session.correlate(
            ...     a, b,
            ...     alignment=mv.window_bucket(),
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
            ...     session.catalog.get("metric.sales.revenue"),
            ...     time_scope={"start": "2026-01-01", "end": "2026-04-01"}, grain="day",
            ... )
            >>> forecast = session.forecast(
            ...     history,
            ...     horizon=30,
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
        """Run quality checks over a MetricFrame and return a structured report.

        When to use: check data quality (nulls, outliers, coverage) before analysis.

        v1 accepts only MetricFrames. Reports for DeltaFrame / CandidateSet /
        ForecastFrame / AttributionFrame are planned for later releases. The
        returned QualityReport carries per-check rows, blocking issues, and a list
        of mechanical affordances for next intents.

        Args:
            frame: A MetricFrame to inspect.

        Raises:
            QualityShapeUnsupportedError: ``frame`` is not a MetricFrame.
            CrossSessionFrameError: ``frame`` belongs to a different session.

        Example:
            >>> report = session.assess_quality(
            ...     frame,
            ...     analysis_purpose="检查收入观察结果是否可用于归因",
            ... )
            >>> for issue in report.blocking_issues:
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
            validate_capability_inputs("assess_quality", target=frame)
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
            value_a: Numeric column on ``a``. Defaults to the frame's measure column.
            value_b: Numeric column on ``b``. Defaults to the frame's measure column.
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
            validate_capability_inputs("hypothesis_test", a=a, b=b, alignment=alignment)
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
        search_space: list[DimensionInput],
        value: str | None = None,
        limit: int | None = 50,
        analysis_purpose: str | None = None,
    ) -> CandidateSet:
        """Find dimensions that explain a delta.

        Source must be a DeltaFrame. ``search_space`` is required and lists
        the candidate dimensions to evaluate for explanatory power. ``limit``
        bounds the candidate count (top by |score|, default 50; ``None`` for
        unbounded); truncation is recorded in ``params``.
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
            validate_capability_inputs(
                "discover.driver_axes", source=source, search_space=search_space
            )
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
        search_space: list[DimensionInput] | None = None,
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
        peer_scope: list[DimensionInput] | None = None,
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
