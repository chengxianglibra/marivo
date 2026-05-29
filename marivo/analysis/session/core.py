"""Session class and session-local summaries."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any, Literal, cast

from marivo.analysis.session.persistence import (
    PersistenceLayout,
    list_job_ids,
    read_job_record,
    read_session_meta,
)
from marivo.analysis.timezone import resolve_system_timezone

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
    judgment_store: Any = None
    judgment_store_unavailable: bool = False

    def __post_init__(self) -> None:
        if self.backend_cache is None:
            from marivo.analysis.executor.backend import BackendCache

            self.backend_cache = BackendCache(self.backend_factory)
        if self.calendars is None:
            from marivo.analysis.calendar.loader import CalendarCache

            self.calendars = CalendarCache(self.project_root)

    @property
    def is_read_only(self) -> bool:
        return self.backend_factory is None

    def jobs(self) -> list[JobSummary]:
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
        if limit <= 0:
            return []
        return self.jobs()[-limit:]

    def job(self, job_id: str) -> dict[str, Any]:
        return read_job_record(self.layout, job_id)

    def frames(self) -> list[FrameRef]:
        if not self.layout.frames_dir.is_dir():
            return []
        refs: list[FrameRef] = []
        for frame_dir in sorted(self.layout.frames_dir.iterdir()):
            meta_file = frame_dir / "meta.json"
            if meta_file.is_file():
                meta = json.loads(meta_file.read_text())
                refs.append(FrameRef(ref=meta["ref"], kind=meta["kind"]))
        return refs

    def close(self) -> None:
        if self.judgment_store is not None:
            self.judgment_store.close()
            self.judgment_store = None
        if self.backend_cache is not None:
            self.backend_cache.close_all()

    def evidence_store(self) -> Any:
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

    def knowledge(self) -> Any:
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

    def run_followup(self, action: Any) -> Any:
        """Dispatch a FollowupAction to the appropriate operator."""
        from marivo.analysis.evidence.types import TriggeredByFollowup
        from marivo.analysis.followups import FollowupAction
        from marivo.analysis.session._load import load_frame

        if not isinstance(action, FollowupAction):
            raise TypeError(f"run_followup expected FollowupAction, got {type(action).__name__}")

        source_ref = action.input_refs[0] if action.input_refs else ""
        triggered_by = TriggeredByFollowup(
            action_id=action.action_id,
            source_artifact_id=source_ref,
            via="run_followup",
        )
        op = action.operator
        result: Any

        if op == "assess_quality":
            from marivo.analysis.intents.assess_quality import assess_quality

            source_frame = load_frame(source_ref, session=self)
            result = assess_quality(
                source_frame,
                session=self,
                _triggered_by=triggered_by,
            )
            self._record_followup_result(action=action, result=result)
            return result

        if op == "decompose":
            from marivo.analysis.intents.decompose import decompose
            from marivo.analysis.refs import DimensionRef

            source_frame = load_frame(source_ref, session=self)
            axis_param = action.params["axis"]
            axis = (
                axis_param
                if isinstance(axis_param, DimensionRef)
                else DimensionRef(id=str(axis_param))
            )
            result = decompose(
                cast("Any", source_frame),
                axis=axis,
                session=self,
                _triggered_by=triggered_by,
            )
            self._record_followup_result(action=action, result=result)
            return result

        if op == "discover":
            from marivo.analysis.intents._types import DiscoverSensitivity
            from marivo.analysis.intents.discover import discover

            source_frame = load_frame(source_ref, session=self)
            result = discover(
                cast("Any", source_frame),
                objective=cast("Any", action.params["objective"]),
                strategy=cast("Any", action.params.get("strategy")),
                value=cast("Any", action.params.get("value")),
                threshold=cast("Any", action.params.get("threshold")),
                sensitivity=cast(
                    "DiscoverSensitivity",
                    str(action.params.get("sensitivity", "balanced")),
                ),
                limit=cast("Any", action.params.get("limit")),
                search_space=cast("Any", action.params.get("search_space")),
                peer_scope=cast("Any", action.params.get("peer_scope")),
                session=self,
                _triggered_by=triggered_by,
            )
            self._record_followup_result(action=action, result=result)
            return result

        if op == "forecast":
            from marivo.analysis.intents.forecast import forecast

            source_frame = load_frame(source_ref, session=self)
            horizon_param = action.params.get("horizon")
            horizon = (
                horizon_param
                if isinstance(horizon_param, int)
                else 7
                if horizon_param in (None, "default")
                else int(str(horizon_param))
            )
            result = forecast(
                cast("Any", source_frame),
                horizon=horizon,
                session=self,
                _triggered_by=triggered_by,
            )
            self._record_followup_result(action=action, result=result)
            return result

        if op == "transform":
            from marivo.analysis.intents.transform import transform

            source_frame = load_frame(source_ref, session=self)
            result = transform(
                source_frame,
                op=cast("Any", action.params["op"]),
                session=self,
                _triggered_by=triggered_by,
                **{key: value for key, value in action.params.items() if key != "op"},
            )
            self._record_followup_result(action=action, result=result)
            return result

        if op is None and action.kind == "adjust_policy":
            self._mark_followup_executed(
                action_id=action.action_id,
                executed_step_id="retry_no_op",
            )
            return None

        if op == "compare":
            raise NotImplementedError(
                "run_followup(compare) requires both legs; agent must dispatch "
                "with explicit current + baseline frames"
            )
        if op == "observe":
            raise NotImplementedError(
                "run_followup(observe) requires explicit MetricRef; agent must dispatch"
            )

        raise NotImplementedError(f"run_followup is not wired for operator={op!r}")

    def findings(
        self,
        *,
        artifact: str | None = None,
        finding_type: str | None = None,
        subject: Any = None,
    ) -> Any:
        """Return Surface 3 findings for this session."""
        from marivo.analysis.evidence.audit import query_findings

        return query_findings(
            db_path=self.layout.session_dir / "judgment.db",
            session_id=self.id,
            artifact_id=artifact,
            finding_type=finding_type,
            subject=subject,
        )

    def propositions(
        self,
        *,
        type: str | None = None,
        subject: Any = None,
        status: str | None = None,
    ) -> Any:
        """Return Surface 3 propositions for this session."""
        from marivo.analysis.evidence.audit import query_propositions

        return query_propositions(
            db_path=self.layout.session_dir / "judgment.db",
            session_id=self.id,
            proposition_type=type,
            subject=subject,
            status=status,
        )

    def assessments(
        self,
        *,
        proposition_id: str | None = None,
        latest_only: bool = True,
    ) -> Any:
        """Return Surface 3 assessments for this session."""
        from marivo.analysis.evidence.audit import query_assessments

        return query_assessments(
            db_path=self.layout.session_dir / "judgment.db",
            session_id=self.id,
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
        metric: Any,
        *,
        window: Any = None,
        dimensions: list[Any] | None = None,
        where: dict[str, Any] | None = None,
    ) -> Any:
        from marivo.analysis.intents.observe import observe

        return observe(
            metric,
            window=window,
            dimensions=dimensions,
            where=where,
            session=self,
        )

    def compare(
        self,
        current: Any,
        baseline: Any,
        *,
        alignment: Any = None,
    ) -> Any:
        from marivo.analysis.intents.compare import compare

        return compare(current, baseline, alignment=alignment, session=self)

    def decompose(
        self,
        frame: Any,
        *,
        axis: Any,
        measure_column: str = "delta",
    ) -> Any:
        from marivo.analysis.intents.decompose import decompose

        return decompose(frame, axis=axis, measure_column=measure_column, session=self)

    def correlate(
        self,
        a: Any,
        b: Any,
        *,
        measure_a: str | None = None,
        measure_b: str | None = None,
        alignment: Any = None,
        lag_policy: Any = None,
        method: str = "pearson",
    ) -> Any:
        from marivo.analysis.intents.correlate import correlate

        return correlate(
            a,
            b,
            measure_a=measure_a,
            measure_b=measure_b,
            alignment=alignment,
            lag_policy=lag_policy,
            method=cast("Any", method),
            session=self,
        )

    def forecast(
        self,
        history: Any,
        *,
        horizon: int,
        model: str = "seasonal_naive",
        seasonality_period: int | None = None,
        interval_level: float = 0.95,
        measure_column: str | None = None,
    ) -> Any:
        from marivo.analysis.intents.forecast import forecast

        return forecast(
            history,
            horizon=horizon,
            model=cast("Any", model),
            seasonality_period=seasonality_period,
            interval_level=interval_level,
            measure_column=measure_column,
            session=self,
        )

    def assess_quality(self, frame: Any) -> Any:
        from marivo.analysis.intents.assess_quality import assess_quality

        return assess_quality(frame, session=self)

    def hypothesis_test(
        self,
        a: Any,
        b: Any,
        *,
        hypothesis: str = "mean_changed",
        value_a: str | None = None,
        value_b: str | None = None,
        alignment: Any = None,
        sampling: Any = None,
        alpha: float = 0.05,
    ) -> Any:
        from marivo.analysis.intents.test import hypothesis_test

        return hypothesis_test(
            a,
            b,
            hypothesis=cast("Any", hypothesis),
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
    ) -> Any:
        from marivo.analysis.escape_hatch import from_pandas

        return from_pandas(df, session=self, description=description, sources=sources)

    def explore_ibis(
        self,
        query_builder: Callable[[Any], Any],
        *,
        datasource: str,
        description: str | None = None,
        sources: list[Any] | None = None,
    ) -> Any:
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
        source: Any,
        *,
        policy: Any = None,
        metric: Any = None,
        semantic_kind: str | None = None,
        measure_column: str | None = None,
        axes: dict[str, Any] | None = None,
        time_axis: str | Any | None = None,
        semantic_model: str | None = None,
        window: Any = None,
        where: dict[str, Any] | None = None,
    ) -> Any:
        from marivo.analysis.escape_hatch import promote_metric_frame

        return promote_metric_frame(
            source,
            policy=policy,
            session=self,
            metric=metric,
            semantic_kind=cast("Any", semantic_kind),
            measure_column=measure_column,
            axes=axes,
            time_axis=time_axis,
            semantic_model=semantic_model,
            window=window,
            where=where,
        )

    def promote_delta_frame(
        self,
        source: Any,
        *,
        policy: Any = None,
        current: Any = None,
        baseline: Any = None,
        metric: Any = None,
        semantic_kind: str | None = None,
        semantic_model: str | None = None,
        delta_column: str | None = None,
        current_column: str | None = None,
        baseline_column: str | None = None,
        alignment: Any = None,
    ) -> Any:
        from marivo.analysis.escape_hatch import promote_delta_frame

        return promote_delta_frame(
            source,
            policy=policy,
            session=self,
            current=current,
            baseline=baseline,
            metric=metric,
            semantic_kind=cast("Any", semantic_kind),
            semantic_model=semantic_model,
            delta_column=delta_column,
            current_column=current_column,
            baseline_column=baseline_column,
            alignment=alignment,
        )

    def promote_attribution_frame(
        self,
        source: Any,
        *,
        policy: Any = None,
        source_delta: Any = None,
        driver_field: str | None = None,
        contribution_column: str | None = None,
        value_column: str | None = None,
        method: str = "promotion",
        method_params: dict[str, Any] | None = None,
    ) -> Any:
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

    def _mark_followup_executed(self, *, action_id: str, executed_step_id: str) -> None:
        """Mark a followup action as executed in the judgment store."""
        store = self.evidence_store()
        if store is None:
            return
        with store.transaction() as tx:
            tx.execute(
                "UPDATE followups SET executed_step_id=? WHERE followup_id=?",
                (executed_step_id, action_id),
            )

    def _record_followup_result(self, *, action: Any, result: Any) -> None:
        artifact_id = getattr(getattr(result, "meta", None), "artifact_id", None)
        executed_step_id = artifact_id if isinstance(artifact_id, str) else ""
        self._mark_followup_executed(
            action_id=action.action_id,
            executed_step_id=executed_step_id,
        )
        if not executed_step_id:
            return
        store = self.evidence_store()
        if store is None:
            return
        triggered_payload = json.dumps(
            {
                "action_id": action.action_id,
                "source_artifact_id": action.input_refs[0] if action.input_refs else "",
                "via": "run_followup",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        with store.transaction() as tx:
            tx.execute(
                "UPDATE artifacts SET triggered_by_followup=? WHERE artifact_id=? "
                "AND triggered_by_followup IS NULL",
                (triggered_payload, executed_step_id),
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
    ) -> Any:
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
    ) -> Any:
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
    ) -> Any:
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
    ) -> Any:
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
    ) -> Any:
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
    ) -> Any:
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
    ) -> Any:
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
    ) -> Any:
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

    def filter(self, frame: object, *, predicate: Callable[[Any], Any]) -> Any:
        from marivo.analysis.intents.transform import transform

        return transform.filter(frame, predicate=predicate, session=self._session)

    def slice(self, frame: object, *, where: dict[Any, Any]) -> Any:
        from marivo.analysis.intents.transform import transform

        return transform.slice(frame, where=where, session=self._session)

    def rollup(self, frame: object, *, drop_axes: list[Any]) -> Any:
        from marivo.analysis.intents.transform import transform

        return transform.rollup(frame, drop_axes=drop_axes, session=self._session)

    def topk(
        self,
        frame: object,
        *,
        by: str,
        limit: int,
        order: str | None = None,
    ) -> Any:
        from marivo.analysis.intents.transform import transform

        return transform.topk(
            frame,
            by=by,
            limit=limit,
            order=cast("Any", order),
            session=self._session,
        )

    def bottomk(self, frame: object, *, by: str, limit: int) -> Any:
        from marivo.analysis.intents.transform import transform

        return transform.bottomk(frame, by=by, limit=limit, session=self._session)

    def rank(
        self,
        frame: object,
        *,
        by: str,
        method: str = "ordinal",
        rank_column: str = "rank",
    ) -> Any:
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
    ) -> Any:
        from marivo.analysis.intents.transform import transform

        return transform.normalize(
            frame,
            mode=cast("Any", mode),
            baseline=baseline,
            session=self._session,
        )

    def window(self, frame: object, *, window: Any) -> Any:
        from marivo.analysis.intents.transform import transform

        return transform.window(frame, window=window, session=self._session)


@dataclass(frozen=True)
class EvidenceNamespace:
    """Session-scoped Surface 3 evidence object lookups."""

    _session: Session

    def proposition(self, proposition_id: str) -> Any:
        from marivo.analysis.evidence.audit import get_proposition

        return get_proposition(
            db_path=self._session.layout.session_dir / "judgment.db",
            proposition_id=proposition_id,
        )

    def latest_assessment(self, proposition_id: str) -> Any:
        from marivo.analysis.evidence.audit import get_latest_assessment

        return get_latest_assessment(
            db_path=self._session.layout.session_dir / "judgment.db",
            proposition_id=proposition_id,
        )

    def trace(self, proposition_id: str) -> Any:
        from marivo.analysis.evidence.audit import build_evidence_trace

        return build_evidence_trace(
            db_path=self._session.layout.session_dir / "judgment.db",
            proposition_id=proposition_id,
        )
