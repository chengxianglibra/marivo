"""Session class and session-local summaries."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from marivo.analysis.session._layout import PersistenceLayout, read_job_record
from marivo.analysis.timezone import resolve_system_timezone
from marivo.render import Card, RenderableResult

if TYPE_CHECKING:
    from marivo.analysis.derive import IbisQuerySpec, MetricColumns
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
    from marivo.analysis.frames.forecast import ForecastFrame
    from marivo.analysis.frames.hypothesis import HypothesisTestResult
    from marivo.analysis.frames.metric import MetricFrame
    from marivo.analysis.frames.quality import QualityReport
    from marivo.analysis.intents._shape import SemanticShape
    from marivo.analysis.intents._types import SliceValue
    from marivo.analysis.intents.attribute import AttributeMode
    from marivo.analysis.intents.transform import NormalizeKind
    from marivo.analysis.policies import AlignmentPolicy, SamplingPolicy
    from marivo.analysis.publish.publish_targets import PublishTarget
    from marivo.analysis.publish.report_models import (
        MarivoReportArtifact,
        ReportPackageValidationResult,
    )
    from marivo.analysis.publish.report_publish import PublishReportResult
    from marivo.analysis.semantic_inputs import DimensionInput, MetricInput
    from marivo.analysis.session._store import SessionStore
    from marivo.analysis.windows.spec import GrainInput, TimeScopeInput
    from marivo.semantic.catalog import SemanticCatalog

SemanticKind = Literal["scalar", "time_series", "segmented", "panel"]


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

    def _repr_identity(self) -> str:
        parts = f"FrameSummaryEntry ref={self.ref} kind={self.kind}"
        if self.metric_id:
            parts += f" metric={self.metric_id}"
        return parts

    def _card(self) -> Card:
        return Card(identity=self._repr_identity(), available=(".render()", ".show()")).status(
            f"metric={self.metric_id} created={self.created_at}"
        )


@dataclass(frozen=True)
class ReportRegistration:
    """Immutable result of persisting a report package under a session.

    Attributes:
        report_id: The report identifier (generated or user-supplied).
        package_dir: Absolute path to the on-disk report package directory.
        entrypoint: The primary entrypoint file name within the package.
        content_hash: Deterministic ``sha256:`` hash of the package contents.
    """

    report_id: str
    package_dir: Path
    entrypoint: str
    content_hash: str


class Session:
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
                details={"session_id": self.id, "job_id": job_id},
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

    # -- Report methods -----------------------------------------------------

    def save_report(
        self,
        artifact: MarivoReportArtifact,
        *,
        report_id: str | None = None,
        adapter: Literal["mcp", "package"] = "package",
    ) -> ReportRegistration:
        """Persist a report package under this session and register it in the store.

        Writes package files first, then registers metadata in the store so
        that on-disk state and store state are always consistent.

        Args:
            artifact: The report artifact to persist.
            report_id: Optional report identifier. When ``None``, a unique id
                is generated with the ``rpt_`` prefix.
            adapter: Materialization adapter to use:

                - ``"package"`` (default) writes only the canonical JSON
                  package via :func:`write_report_artifact`.
                - ``"mcp"`` writes the canonical package plus MCP adapter files
                  via :func:`materialize_mcp_adapter`.

        Returns:
            A :class:`ReportRegistration` with the report id, package dir,
            entrypoint, and content hash.

        Raises:
            ValueError: If *report_id* is supplied but not safe for use as a
                directory name.

        Example:
            >>> registration = session.save_report(artifact)
            >>> registration.report_id
            'rpt_abc123'
        """
        import secrets

        from marivo.analysis.publish.publish_hash import compute_package_hash
        from marivo.analysis.session._layout import report_dir

        resolved_id = report_id if report_id is not None else f"rpt_{secrets.token_hex(6)}"
        pkg_dir = report_dir(self._layout, resolved_id)

        # Materialize package files to disk.
        if adapter == "mcp":
            from marivo.analysis.publish.report_mcp_adapter import materialize_mcp_adapter

            updated = materialize_mcp_adapter(artifact, pkg_dir)
        elif adapter == "package":
            from marivo.analysis.publish.report_package import write_report_artifact

            write_report_artifact(artifact, pkg_dir)
            updated = artifact
        else:
            raise ValueError(f"unknown adapter: {adapter!r}")

        # Determine the primary entrypoint from the updated manifest.
        entrypoints = updated.manifest.entrypoints
        entrypoint = next(iter(entrypoints.values())) if entrypoints else ""

        # Compute package hash after files are written.
        content_hash = compute_package_hash(pkg_dir)

        # Register in the store after files are on disk.
        package_dir_relative = self._layout.relative_path(pkg_dir)
        self._store.record_report(
            session_id=self.id,
            report_id=resolved_id,
            package_dir=package_dir_relative,
            entrypoint=entrypoint,
            package_hash=content_hash,
        )

        return ReportRegistration(
            report_id=resolved_id,
            package_dir=pkg_dir,
            entrypoint=entrypoint,
            content_hash=content_hash,
        )

    def validate_report(self, report_id: str) -> ReportPackageValidationResult:
        """Resolve a registered report's package dir and validate it.

        Args:
            report_id: The report identifier previously used with
                :meth:`save_report`.

        Returns:
            A :class:`~marivo.analysis.publish.ReportPackageValidationResult`
            indicating whether the package is valid.

        Raises:
            ReportPublishError: If *report_id* is not registered in this
                session.

        Example:
            >>> result = session.validate_report("rpt_abc123")
            >>> result.ok
            True
        """
        from marivo.analysis.errors import ReportPublishError
        from marivo.analysis.publish.report_validation import validate_report_artifact

        row = self._store.get_report(self.id, report_id)
        if row is None:
            raise ReportPublishError(
                message=f"report {report_id!r} is not registered in session {self.name!r}",
                details={"report_id": report_id, "session_name": self.name},
            )

        package_dir = self._project_root / row["package_dir"]
        from marivo.analysis.publish.report_package import load_report_artifact

        artifact = load_report_artifact(package_dir)
        return validate_report_artifact(artifact)

    def publish_report(
        self,
        report_id: str,
        *,
        exported_by: str | None = None,
        exported_at: str | None = None,
        target: str | PublishTarget | None = None,
        project_root: str | Path | None = None,
    ) -> PublishReportResult:
        """Publish a registered session report and record the published URL.

        Resolves the package directory from the store, validates the package,
        then delegates to the existing publish logic.

        Args:
            report_id: The report identifier previously used with
                :meth:`save_report`.
            exported_by: Exporter name. Defaults to the current OS user.
            exported_at: ISO-8601 export timestamp. Defaults to now.
            target: Publish target (a path string for local filesystem, or a
                :class:`~marivo.analysis.publish.PublishTarget` instance).
            project_root: Project root for resolving publish config. Defaults
                to this session's project root.

        Returns:
            A :class:`~marivo.analysis.publish.PublishReportResult` with the
            published URI, content hash, exporter info, and file count.

        Raises:
            ReportPublishError: If *report_id* is not registered in this
                session.
            ReportPublishValidationError: If the package fails validation.

        Example:
            >>> result = session.publish_report("rpt_abc123", target="/published")
            >>> result.uri
            'file:///published/...'
        """
        from marivo.analysis.errors import ReportPublishError
        from marivo.analysis.publish.report_publish import publish_report_package

        row = self._store.get_report(self.id, report_id)
        if row is None:
            raise ReportPublishError(
                message=f"report {report_id!r} is not registered in session {self.name!r}",
                details={"report_id": report_id, "session_name": self.name},
            )

        package_dir = self._project_root / row["package_dir"]
        result = publish_report_package(
            package_dir,
            exported_by=exported_by,
            exported_at=exported_at,
            target=target,
            project_root=project_root or self._project_root,
        )

        # Record the published URL in the store.
        self._store.update_report_published_url(self.id, report_id, result.uri)

        return result

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
        metric: MetricInput,
        *,
        time_scope: TimeScopeInput = None,
        grain: GrainInput = None,
        dimensions: list[DimensionInput] | None = None,
        slice_by: dict[DimensionInput, SliceValue] | None = None,
        time_dimension: DimensionInput | None = None,
        expect_shape: SemanticShape | None = None,
    ) -> MetricFrame:
        """Materialize a metric into a typed MetricFrame.

        When to use: starting point for any metric analysis workflow.

        Resolves ``metric`` against the active semantic catalog, applies the
        optional ``time_scope`` / ``grain`` / ``dimensions`` / ``slice_by`` filters, executes against
        the session's backend, and persists the result as a MetricFrame on disk.

        Args:
            metric: Catalog metric object or ``SemanticRef`` from ``session.catalog``.
                Bare strings are rejected.
            time_scope: Half-open time range ``{"start": ..., "end": ...}`` — start is
                inclusive, end is exclusive.  For date-only strings, ``end="2026-08-01"``
                means data from August 1 is **not** included.
            grain: Optional time bucket grain. When present, observe returns a time
                series or panel depending on ``dimensions``.
            dimensions: Segment axes. Omit, pass ``None``, or pass ``[]`` for no
                segment axes. In v1 all non-empty dimension lists must resolve to
                the same entity as ``metric``.
            slice_by: Pre-aggregation row filter. Keys are catalog dimension objects/refs for
                the filtered dimension; values are either a scalar (``==``), a list
                (``in``), or ``{"op": "<op>", "value": ...}`` where op is one of
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
            ... )
            >>> frame.show()
        """
        from marivo.analysis.intents.observe import observe

        return observe(
            metric,
            time_scope=time_scope,
            grain=grain,
            dimensions=dimensions,
            slice_by=slice_by,
            time_dimension=time_dimension,
            expect_shape=expect_shape,
            session=self,
        )

    def derive_metric_frame(
        self,
        *,
        metric: MetricInput,
        query: IbisQuerySpec,
        columns: MetricColumns,
        time_scope: TimeScopeInput = None,
        grain: GrainInput = None,
        label: str | None = None,
    ) -> MetricFrame:
        """Run a governed Ibis query and attach MetricFrame metadata to the result.

        This is the only default escape hatch that can re-enter the canonical
        metric-frame workflow. Semantic refs identify the metric and axis
        bindings; query output columns are always plain strings.

        Unlike ``observe``, this method does not compute, filter, or aggregate
        data -- the ``build`` callback owns the query output. ``time_scope`` and
        ``grain`` annotate the frame's window metadata and are passed to ``build``
        via ``ctx`` for convenience (e.g., ``ctx.bucket()`` for time truncation).
        The query output is used verbatim. Unbound columns in the build output
        are retained as-is; project within build to limit the column set.

        ``time_scope`` defaults to ``None`` (no window) for frames without a time
        axis.
        """
        from marivo.analysis.derive import IbisQuerySpec, MetricColumns, derive_metric_frame

        if not isinstance(query, IbisQuerySpec):
            raise TypeError("derive_metric_frame query must be mv.ibis_query(...)")
        if not isinstance(columns, MetricColumns):
            raise TypeError("derive_metric_frame columns must be mv.metric_columns(...)")
        return derive_metric_frame(
            metric=metric,
            query=query,
            columns=columns,
            time_scope=time_scope,
            grain=grain,
            label=label,
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
            >>> cur  = session.observe(revenue, time_scope={"start": "2026-07-01", "end": "2026-10-01"})
            >>> base = session.observe(revenue, time_scope={"start": "2025-07-01", "end": "2025-10-01"})
            >>> delta = session.compare(cur, base, alignment=mv.window_bucket())
        """
        from marivo.analysis.intents.compare import compare

        return compare(current, baseline, alignment=alignment, session=self)

    def attribute(
        self,
        frame: DeltaFrame,
        *,
        axes: list[DimensionInput],
        mode: AttributeMode = "flat",
    ) -> AttributionFrame:
        """Attribute a DeltaFrame's movement over explicit deterministic axes.

        When to use: compute deterministic contribution rows for a compared metric.
        This operator does not explain business causes and does not choose axes
        for the agent. Pass explicit axes selected from the catalog or from a
        CandidateSet.
        Component-aware ratio and weighted-average deltas use mix attribution.
        Plain non-linear sampled folds such as percentile, min, max, first, or
        last cannot be summed by axis and remain unsupported unless they are
        part of a persisted component-aware derived metric delta.

        Args:
            frame: A DeltaFrame produced by ``session.compare``.
            axes: One or more catalog dimension refs/objects to attribute over.
            mode: ``"flat"`` for a single-axis attribution, ``"nested"`` or
                ``"recursive"`` for flattened hierarchy rows over multiple axes.

        Raises:
            SemanticKindMismatchError: ``frame`` is not a DeltaFrame, axes are
                missing, an axis is not present in the frame, or mode is unsupported.
            CrossSessionFrameError: A frame belongs to a different session.

        Example:
            >>> delta = session.compare(cur, base, alignment=mv.window_bucket())
            >>> country = session.catalog.get("dimension.sales.orders.country").ref
            >>> attribution = session.attribute(delta, axes=[country])
        """
        from marivo.analysis.intents.attribute import attribute

        return attribute(frame, axes=axes, mode=mode, session=self)

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
            alignment: Defaults to ``mv.window_bucket()``.
            method: Only ``"pearson"`` in v1.

        Raises:
            SemanticKindMismatchError: Inputs are not MetricFrames, or alignment
                kinds are unsupported.
            AlignmentFailedError: Frames cannot be aligned (e.g. no overlapping buckets).
            CrossSessionFrameError: A frame belongs to a different session.

        Example:
            >>> result = session.correlate(
            ...     a, b,
            ...     alignment=mv.window_bucket(),
            ... )
            >>> result.show()
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
            ...     session.catalog.get("metric.sales.revenue"),
            ...     time_scope={"start": "2026-01-01", "end": "2026-04-01"}, grain="day",
            ... )
            >>> forecast = session.forecast(history, horizon=30)
            >>> forecast.show()
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
        of mechanical affordances for next intents.

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
            >>> result = session.hypothesis_test(cur, base)
            >>> result.show()
        """
        from marivo.analysis.intents.hypothesis_test import hypothesis_test

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


def ensure_session_can_execute(session: Session) -> None:
    """Raise ``NoBackendFactoryError`` when the session has no backend factory."""
    from marivo.analysis.errors import NoBackendFactoryError

    if session.is_read_only:
        raise NoBackendFactoryError(
            message=f"session '{session.name}' has no backend factory configured",
            details={"session_name": session.name},
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
        search_space: list[DimensionInput],
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
        search_space: list[DimensionInput] | None = None,
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
        peer_scope: list[DimensionInput] | None = None,
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

    def slice(
        self, frame: object, *, slice_by: dict[DimensionInput, Any]
    ) -> MetricFrame | DeltaFrame:
        """Filter rows by exact axis values.

        ``slice_by`` maps catalog dimension refs/objects to the value(s) to keep.
        Unlike ``filter``, operates on raw axis values without a callable.
        """
        from marivo.analysis.intents.transform import transform

        return transform.slice(frame, slice_by=slice_by, session=self._session)

    def rollup(self, frame: object, *, drop_axes: list[DimensionInput]) -> MetricFrame | DeltaFrame:
        """Aggregate to coarser segments by dropping axes.

        Removes the listed catalog dimensions and re-aggregates
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
