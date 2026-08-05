from __future__ import annotations

import importlib
from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.errors import AttributionMaterializationError
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents._replay import (
    ObserveReplay,
    recover_alignment_policy,
    recover_observe_replay,
)
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.runtime_metric import replay_payload
from marivo.analysis.session._runtime import persist_job_record
from marivo.refs import ref as ref_factory
from tests.shared_fixtures import make_test_delta_contract, make_test_metric_contract


@pytest.fixture(autouse=True)
def _session_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield
    session_attach._reset_process_state()


def _now() -> datetime:
    return datetime(2026, 7, 1, 8, 0, 0, tzinfo=UTC)


def _metric_frame(session: mv.Session, *, params: dict[str, object]) -> MetricFrame:
    df = pd.DataFrame({"value": [10.0]})
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_dimension": "sales.orders.created_at",
        },
        "region": {
            "role": "dimension",
            "column": "region",
            "ref": "sales.orders.region",
        },
    }
    return MetricFrame(
        _df=df,
        meta=MetricFrameMeta(
            kind="metric_frame",
            ref="frame_current",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe_current",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(
                steps=[
                    LineageStep(
                        intent="observe",
                        job_ref="job_observe_current",
                        inputs=[],
                        params_digest="sha256:observe",
                        params=params,
                    )
                ]
            ),
            metric_id="sales.revenue",
            **make_test_metric_contract(
                df,
                metric_id="sales.revenue",
                axes=axes,
                where={"sales.orders.region": "US"},
            ),
            axes=axes,
            measure={"name": "revenue"},
            window={
                "start": "2026-07-01T00:00:00+00:00",
                "end": "2026-08-01T00:00:00+00:00",
                "grain": "day",
                "time_dimension": "sales.orders.created_at",
            },
            where={"sales.orders.region": "US"},
            semantic_kind="time_series",
            semantic_model="sales",
        ),
    )


def _delta_frame(session: mv.Session, *, alignment: dict[str, object]) -> DeltaFrame:
    return DeltaFrame(
        _df=pd.DataFrame({"delta": [2.0]}),
        meta=DeltaFrameMeta(
            **make_test_delta_contract("sales.revenue"),
            kind="delta_frame",
            ref="frame_delta",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_compare",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            metric_id="sales.revenue",
            source_current_ref="frame_current",
            source_baseline_ref="frame_baseline",
            alignment=alignment,
            semantic_kind="scalar",
            semantic_model="sales",
        ),
    )


def test_recover_observe_replay_reads_lineage_params() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _metric_frame(
        session,
        params={
            "replay_expression": replay_payload(ref_factory.metric("sales.revenue")),
            "timescope": {
                "original": {"start": "2026-07-01", "end": "2026-08-01"},
                "resolved": {
                    "start": "2026-07-01T00:00:00+00:00",
                    "end": "2026-08-01T00:00:00+00:00",
                    "grain": "day",
                    "time_dimension": "sales.orders.created_at",
                },
                "report_tz": "UTC",
            },
        },
    )

    replay = recover_observe_replay(frame, session=session)

    assert replay.metric == ref_factory.metric("sales.revenue")
    assert replay.time_scope == {"start": "2026-07-01", "end": "2026-08-01"}
    assert replay.grain == "day"
    assert replay.time_dimension == ref_factory.time_dimension("sales.orders.created_at")
    assert replay.dimensions == (ref_factory.dimension("sales.orders.region"),)
    assert replay.slice_by == {ref_factory.dimension("sales.orders.region"): "US"}


def test_recover_observe_replay_requires_observe_params() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _metric_frame(session, params={})

    with pytest.raises(AttributionMaterializationError) as exc_info:
        recover_observe_replay(frame, session=session)

    assert exc_info.value._context["recoverability_status"] == "observe_params_missing"
    assert exc_info.value._context["source_ref"] == "frame_current"


def test_recover_alignment_policy_filters_enriched_compare_metadata() -> None:
    session = mv.session.get_or_create(name="demo")
    delta = _delta_frame(
        session,
        alignment={
            "kind": "window_bucket",
            "mode": "calendar_bucket",
            "strict_lengths": True,
            "axes": {"time": {"role": "time", "column": "bucket_start"}},
            "coverage": {"rows": 2},
            "baseline_bucket_column": "bucket_start_b",
        },
    )

    policy = recover_alignment_policy(delta)

    assert isinstance(policy, AlignmentPolicy)
    assert policy.kind == "window_bucket"
    assert policy.mode == "calendar_bucket"
    assert policy.strict_lengths is True


def test_recover_alignment_policy_reports_invalid_policy_fields() -> None:
    session = mv.session.get_or_create(name="demo")
    delta = _delta_frame(session, alignment={"kind": "dow_aligned"})

    with pytest.raises(AttributionMaterializationError) as exc_info:
        recover_alignment_policy(delta)

    assert exc_info.value._context["recoverability_status"] == "alignment_policy_invalid"
    assert exc_info.value._context["delta_ref"] == "frame_delta"


_OBSERVE_PARAMS: dict[str, object] = {
    "replay_expression": replay_payload(ref_factory.metric("sales.revenue")),
    "timescope": {
        "original": {"start": "2026-07-01", "end": "2026-08-01"},
        "resolved": {
            "start": "2026-07-01T00:00:00+00:00",
            "end": "2026-08-01T00:00:00+00:00",
            "grain": "day",
            "time_dimension": "sales.orders.created_at",
        },
        "report_tz": "UTC",
    },
}


def _metric_frame_no_lineage(session: mv.Session) -> MetricFrame:
    """Build a MetricFrame with empty lineage but a produced_by_job ref."""
    df = pd.DataFrame({"value": [10.0]})
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_dimension": "sales.orders.created_at",
        },
        "region": {
            "role": "dimension",
            "column": "region",
            "ref": "sales.orders.region",
        },
    }
    return MetricFrame(
        _df=df,
        meta=MetricFrameMeta(
            kind="metric_frame",
            ref="frame_current",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe_current",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            metric_id="sales.revenue",
            **make_test_metric_contract(
                df,
                metric_id="sales.revenue",
                axes=axes,
                where={"sales.orders.region": "US"},
            ),
            axes=axes,
            measure={"name": "revenue"},
            window={
                "start": "2026-07-01T00:00:00+00:00",
                "end": "2026-08-01T00:00:00+00:00",
                "grain": "day",
                "time_dimension": "sales.orders.created_at",
            },
            where={"sales.orders.region": "US"},
            semantic_kind="time_series",
            semantic_model="sales",
        ),
    )


def test_recover_observe_replay_falls_back_to_job_record() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _metric_frame_no_lineage(session)

    persist_job_record(
        session,
        {
            "id": "job_observe_current",
            "session_id": session.id,
            "intent": "observe",
            **job_semantics_from_frames(frame),
            "params": dict(_OBSERVE_PARAMS),
            "input_frame_refs": [],
            "output_frame_ref": "frame_current",
            "started_at": _now().isoformat(),
            "finished_at": _now().isoformat(),
            "duration_ms": 0,
            "status": "succeeded",
            "error": None,
            "semantic_project_root": str(session.project_root),
            "queries": [],
        },
    )

    replay = recover_observe_replay(frame, session=session)

    assert replay.metric == ref_factory.metric("sales.revenue")
    assert replay.time_scope == {"start": "2026-07-01", "end": "2026-08-01"}
    assert replay.grain == "day"
    assert replay.time_dimension == ref_factory.time_dimension("sales.orders.created_at")
    assert replay.dimensions == (ref_factory.dimension("sales.orders.region"),)
    assert replay.slice_by == {ref_factory.dimension("sales.orders.region"): "US"}


def test_observe_replay_with_dimensions_dedups_and_skips_time_dimension() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _metric_frame(session, params=dict(_OBSERVE_PARAMS))

    replay = recover_observe_replay(frame, session=session)

    result = replay.with_dimensions(
        [
            ref_factory.dimension("sales.orders.region"),
            ref_factory.dimension("sales.orders.platform"),
            ref_factory.time_dimension("sales.orders.created_at"),
        ]
    )

    assert result.dimensions == (
        ref_factory.dimension("sales.orders.region"),
        ref_factory.dimension("sales.orders.platform"),
    )


def test_replay_uses_exact_dependency_digest_not_whole_catalog_fingerprint(
    monkeypatch,
) -> None:
    session = mv.session.get_or_create(name="digest_replay")
    replay = ObserveReplay(
        metric=ref_factory.metric("sales.revenue"),
        time_scope=None,
        grain=None,
        dimensions=(),
        slice_by={},
        time_dimension=None,
        dependency_digest="sha256:unchanged",
        catalog_definition_fingerprint="sha256:old-catalog",
    )
    result = SimpleNamespace(meta=SimpleNamespace(execution_stats=None))
    monkeypatch.setattr(
        "marivo.analysis.intents._runtime_metric_lowering.lower_metric_inputs",
        lambda *_args, **_kwargs: SimpleNamespace(
            dependency_digest=SimpleNamespace(digest="sha256:unchanged")
        ),
    )
    observe_module = importlib.import_module("marivo.analysis.intents.observe")
    monkeypatch.setattr(observe_module, "observe", lambda *_args, **_kwargs: result)

    assert replay.call_observe(session) is result


def test_replay_stops_before_execution_when_dependency_digest_changes(
    monkeypatch,
) -> None:
    session = mv.session.get_or_create(name="digest_drift")
    replay = ObserveReplay(
        metric=ref_factory.metric("sales.revenue"),
        time_scope=None,
        grain=None,
        dimensions=(),
        slice_by={},
        time_dimension=None,
        dependency_digest="sha256:recorded",
        catalog_definition_fingerprint="sha256:recorded-catalog",
    )
    monkeypatch.setattr(
        "marivo.analysis.intents._runtime_metric_lowering.lower_metric_inputs",
        lambda *_args, **_kwargs: SimpleNamespace(
            dependency_digest=SimpleNamespace(digest="sha256:changed")
        ),
    )
    executed = False

    def fail_if_executed(*_args, **_kwargs):
        nonlocal executed
        executed = True
        raise AssertionError("observe must not execute after dependency drift")

    observe_module = importlib.import_module("marivo.analysis.intents.observe")
    monkeypatch.setattr(observe_module, "observe", fail_if_executed)

    with pytest.raises(AttributionMaterializationError) as exc_info:
        replay.call_observe(session)
    assert exc_info.value._context["recoverability_status"] == ("semantic_dependency_changed")
    assert executed is False
