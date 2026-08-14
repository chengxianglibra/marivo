"""Current-schema frame loading and destructive rejection checks."""

import json
from datetime import datetime

import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo._compat import UTC
from marivo.analysis.errors import (
    CrossSessionFrameError,
    FrameCacheCorruptedError,
    FrameMetaInvalidError,
    FrameRefNotFound,
)
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.session._layout import write_frame_to_disk
from tests.shared_fixtures import (
    connect_sales_orders,
    make_metric_frame,
    make_test_delta_contract,
    make_test_metric_meta_contract,
    sales_backends,
)


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _base_meta(session, *, kind, ref):
    return {
        "kind": kind,
        "ref": ref,
        "session_id": session.id,
        "project_root": str(session.project_root),
        "produced_by_job": "job_test",
        "created_at": datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        "row_count": 1,
        "byte_size": 0,
        "lineage": Lineage(
            steps=[
                LineageStep(
                    intent="test",
                    job_ref="job_test",
                    inputs=[],
                    params_digest="sha256:test",
                )
            ],
        ),
    }


def test_load_frame_round_trips_hypothesis_test_result():
    from marivo.analysis.frames.hypothesis import (
        HypothesisTestResult,
        HypothesisTestResultMeta,
    )
    from marivo.analysis.session._runtime import persist_frame

    session = session_attach.get_or_create(name="demo")
    frame = HypothesisTestResult(
        _df=pd.DataFrame({"p_value": [0.01], "rejected": [True]}),
        meta=HypothesisTestResultMeta(
            **_base_meta(session, kind="hypothesis_test_result", ref="frame_test"),
            source_refs=["frame_a", "frame_b"],
            metric_ids=["sales.revenue"],
            semantic_kinds=["time_series", "time_series"],
            semantic_models=["sales", "sales"],
            hypothesis="mean_changed",
            method="paired_t",
            alignment={"kind": "window_bucket"},
            sampling={"unit": "bucket"},
            alpha=0.05,
            result_shape="single",
            segment_dimensions=[],
            rejected_count=1,
            not_enough_data_count=0,
        ),
    )
    frame.meta = persist_frame(session, frame)

    loaded = session.get_frame("frame_test")

    assert isinstance(loaded, HypothesisTestResult)
    assert loaded.meta.kind == "hypothesis_test_result"
    assert loaded.meta.hypothesis == "mean_changed"
    assert loaded.to_pandas().iloc[0]["p_value"] == 0.01


def test_load_frame_round_trips_forecast_frame():
    from marivo.analysis.frames.forecast import ForecastFrame, ForecastFrameMeta
    from marivo.analysis.session._runtime import persist_frame

    session = session_attach.get_or_create(name="demo")
    frame = ForecastFrame(
        _df=pd.DataFrame({"time": ["2026-06-01"], "forecast": [12.0]}),
        meta=ForecastFrameMeta(
            **_base_meta(session, kind="forecast_frame", ref="frame_forecast"),
            source_refs=["frame_history"],
            metric_id="sales.revenue",
            semantic_model="sales",
            semantic_kind="time_series",
            measure={"field": "value"},
            axes={"time": {"field": "time", "grain": "day"}},
            history_window={"start": "2026-01-01", "end": "2026-05-31"},
            forecast_window={"start": "2026-06-01", "end": "2026-06-01"},
            horizon=1,
            horizon_unit="day",
            model="naive",
            seasonality_period=None,
            interval_level=0.95,
            interval_method="normal_residual",
            train_row_count_per_segment={"__all__": 30},
            segment_dimensions=[],
        ),
    )
    frame.meta = persist_frame(session, frame)

    loaded = session.get_frame("frame_forecast")

    assert isinstance(loaded, ForecastFrame)
    assert loaded.meta.kind == "forecast_frame"
    assert loaded.meta.horizon == 1
    assert loaded.to_pandas().iloc[0]["forecast"] == 12.0


def test_load_frame_round_trips_quality_report():
    from marivo.analysis.frames.quality import QualityReport, QualityReportMeta
    from marivo.analysis.session._runtime import persist_frame

    session = session_attach.get_or_create(name="demo")
    frame = QualityReport(
        _df=pd.DataFrame({"check": ["missing_values"], "status": ["ok"]}),
        meta=QualityReportMeta(
            **_base_meta(session, kind="quality_report", ref="frame_quality"),
            source_refs=["frame_metric"],
            report_shape="metric",
            target_kind="metric_frame",
            target_metric_id="sales.revenue",
            target_semantic_model="sales",
            target_semantic_kind="time_series",
            checks_run=["missing_values"],
            overall_status="ok",
            blocking_issue_count=0,
            warning_count=0,
        ),
    )
    frame.meta = persist_frame(session, frame)

    loaded = session.get_frame("frame_quality")

    assert isinstance(loaded, QualityReport)
    assert loaded.meta.kind == "quality_report"
    assert loaded.meta.overall_status == "ok"
    assert loaded.to_pandas().iloc[0]["check"] == "missing_values"


def test_loads_new_operator_frame_families(tmp_path, monkeypatch):
    from tests.shared_fixtures import seeded_time_series_metric_frame

    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    session = session_attach.get_or_create(name="demo")
    frame = seeded_time_series_metric_frame(session=session, n_buckets=10)

    outputs = [
        session.hypothesis_test(frame, frame),
        session.forecast(frame, horizon=2, model="naive"),
        session.assess_quality(frame),
    ]

    assert [session.get_frame(output.ref).meta.kind for output in outputs] == [
        "hypothesis_test_result",
        "forecast_frame",
        "quality_report",
    ]


def test_session_get_frame_accepts_ref_string():
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session,
    )
    loaded = session.get_frame(frame.ref)
    assert isinstance(loaded, MetricFrame)
    assert loaded.ref == frame.ref


def test_session_get_frame_ref_not_found():
    session = session_attach.get_or_create(name="demo")
    with pytest.raises(FrameRefNotFound):
        session.get_frame("frame_nonexistent")


# ---------------------------------------------------------------------------
# Store-backed frame loading tests
# ---------------------------------------------------------------------------


def test_resolve_frame_session_uses_persisted_project_root_for_connection_runtime(
    tmp_path, monkeypatch
):
    """Resolving a frame session from another cwd keeps datasource lookup project-scoped."""
    project_a = tmp_path / "project_a"
    project_b = tmp_path / "project_b"
    project_a.mkdir()
    project_b.mkdir()

    monkeypatch.chdir(project_a)
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session,
    )
    session_attach._reset_process_state()

    monkeypatch.chdir(project_b)
    from marivo.analysis.session._resolve import resolve_frame_session

    resolved = resolve_frame_session(frame.meta.session_id, frame.meta.project_root)

    assert resolved.project_root == project_a.resolve()
    assert resolved._connection_runtime.service.project_root == project_a.resolve()


def test_frame_file_without_artifacts_row_is_unreachable():
    """A frame file on disk without an artifacts store row cannot be loaded."""
    session = session_attach.get_or_create(name="demo")
    # Write a frame directly to disk without using persist_frame,
    # so no store row is created.
    from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
    from marivo.analysis.lineage import Lineage, LineageStep

    ref = "frame_orphan"
    meta = MetricFrameMeta(
        **make_test_metric_meta_contract("custom.metric"),
        kind="metric_frame",
        ref=ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=None,
        created_at=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        row_count=1,
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="test_orphan",
                    job_ref=None,
                    inputs=[],
                    params_digest="test",
                )
            ],
        ),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        window=None,
        where={},
        semantic_kind="scalar",
        semantic_model="custom",
    )
    frame = MetricFrame(_df=pd.DataFrame({"value": [1.0]}), meta=meta)
    # Write to disk only (no store registration).
    write_frame_to_disk(session._layout, frame)
    # Attempting to load it should raise FrameRefNotFound.
    with pytest.raises(FrameRefNotFound):
        session.get_frame(ref)


def test_registered_frame_with_missing_bytes_raises_corrupted_error():
    """A registered frame whose data.parquet is deleted raises FrameCacheCorruptedError."""
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session,
    )
    # make_metric_frame uses persist_frame, so the frame is registered.
    # Delete the data file to simulate corruption.
    data_path = session._layout.frames_dir / frame.ref / "data.parquet"
    data_path.unlink()
    with pytest.raises(FrameCacheCorruptedError):
        session.get_frame(frame.ref)


@pytest.mark.parametrize(
    "schema_version",
    [
        None,
        "analysis-artifact/v4",
        "analysis-artifact/v5",
        "analysis-artifact/v6",
        "analysis-artifact/v7",
    ],
)
def test_registered_frame_rejects_every_non_current_artifact_schema(schema_version):
    """Issue #55: the loader accepts only the current artifact schema (v8).

    v6/v7 are not migrated or dual-read — they return a typed error whose
    message names the unsupported version and whose context carries
    expected/got, so callers can recreate the analysis session.
    """
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session,
    )
    meta_path = session._layout.frames_dir / frame.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    if schema_version is None:
        payload.pop("artifact_schema_version")
    else:
        payload["artifact_schema_version"] = schema_version
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameMetaInvalidError, match="unsupported artifact schema") as exc_info:
        session.get_frame(frame.ref)
    # got/expected must be visible through public fields (not only private
    # context) so an agent can see what was read vs what is required.
    assert exc_info.value.received == (schema_version or "<missing>")
    assert exc_info.value.expected == "analysis-artifact/v8"
    assert "analysis-artifact/v8" in str(exc_info.value)
    # A cutover is expected: the repair must tell the agent to re-run analysis.
    assert exc_info.value.repair is not None
    assert "recreate" in exc_info.value.message
    assert "Re-run the analysis" in exc_info.value.repair.action
    assert "Repair:" in str(exc_info.value)


def test_current_artifact_schema_version_is_v8():
    """Issue #55: producers write analysis-artifact/v8 and the loader accepts only v8."""
    from marivo.analysis.frames.base import CURRENT_ARTIFACT_SCHEMA_VERSION

    assert CURRENT_ARTIFACT_SCHEMA_VERSION == "analysis-artifact/v8"

    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session,
    )
    assert frame.meta.artifact_schema_version == "analysis-artifact/v8"


def test_cross_session_frame_raises_cross_session_frame_error():
    """A frame registered to another session raises CrossSessionFrameError."""
    session_a = session_attach.get_or_create(name="session_a")
    frame = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session_a,
    )

    # Create a second session. Manually register the frame ref in session_b's
    # store so that the store lookup passes, but point the paths to session_a's
    # files. The meta.json will say session_id=session_a.id, triggering
    # CrossSessionFrameError.
    session_b = session_attach.get_or_create(name="session_b")
    session_b._store.record_artifact(
        session_id=session_b.id,
        artifact_id=frame.ref,
        kind=frame.meta.kind,
        path=session_a._layout.relative_path(
            session_a._layout.frames_dir / frame.ref / "data.parquet"
        ),
        meta_path=session_a._layout.relative_path(
            session_a._layout.frames_dir / frame.ref / "meta.json"
        ),
        content_hash=None,
        produced_by_job=None,
    )
    with pytest.raises(CrossSessionFrameError):
        session_b.get_frame(frame.ref)


def test_current_metric_state_error_message_carries_concrete_reason(tmp_path):
    """Issue #64: helper must surface the concrete reason in the public message.

    Previously ``_current_metric_state_error`` hard-coded a generic "corrupt
    current-schema metric state" message and dropped the per-call-site ``reason``
    into ``context`` only.  Agents reading the public message could not tell a
    genuine corruption from a concrete validation failure (replay params
    missing, fingerprint mismatch, ...).  The message must reflect ``reason``.
    """
    from marivo.analysis.intents.observe import observe
    from marivo.analysis.session._load import load_frame
    from marivo.semantic import SemanticKind
    from tests.conftest import bootstrap_sales_project
    from tests.ref_helpers import make_ref

    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    session = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    frame = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=session)

    # Tamper with one validated field so the current-schema metric state check
    # raises through _current_metric_state_error.
    meta_path = session._layout.frames_dir / frame.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    payload["expression_fingerprint"] = "mutated-wrong-fingerprint"
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        load_frame(frame.ref, session=session)

    message = exc_info.value.message
    # The concrete reason must be visible in the public message...
    assert "fingerprint does not match the canonical graph roots" in message
    # ...and it must not be papered over as generic corruption.
    assert "corrupt" not in message.lower()
    # Issue #65: the context-only raise must still yield a machine-readable
    # repair so an agent knows to re-run the producing intent.
    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "retry"
    assert exc_info.value.repair.help_target.canonical_id == "observe"
    assert "Re-run observe" in exc_info.value.repair.action
    assert "Repair:" in str(exc_info.value)


def test_expected_ref_mismatch_reports_expected_and_received(tmp_path):
    """Issue #64 follow-up: expected-ref mismatch must surface both values.

    The four expected-ref checks share one loop in
    ``_validate_current_metric_state``; tampering with any single field must
    produce a message carrying both the expected ref and the received
    (tampered) value, so an agent can tell a corrupted value from a changed
    naming rule.
    """
    from marivo.analysis.intents.observe import observe
    from marivo.analysis.session._load import load_frame
    from marivo.semantic import SemanticKind
    from tests.conftest import bootstrap_sales_project
    from tests.ref_helpers import make_ref

    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    session = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    frame = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=session)

    meta_path = session._layout.frames_dir / frame.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    # expression_graph_ref is one of the four expected-ref fields sharing the
    # same for-loop; tamper with it and assert both values reach the message.
    expected = payload["expression_graph_ref"]
    payload["expression_graph_ref"] = "tampered-wrong-ref"
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        load_frame(frame.ref, session=session)

    message = exc_info.value.message
    assert f"expected {expected!r}" in message
    assert "found 'tampered-wrong-ref'" in message


def test_cumulative_delta_missing_attribution_raises_missing_state(tmp_path):
    """Issue #65 review P2-2: a cumulative delta whose schema string is correct
    but whose ``cumulative_attribution`` key is absent must raise through the
    missing-state branch (repair names both observe and compare steps), not
    through a self-contradictory "written with X but expects X" message."""
    from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
    from marivo.analysis.session._runtime import persist_frame

    session = session_attach.get_or_create(name="demo")
    meta = DeltaFrameMeta(
        **make_test_delta_contract("sales.cum_gmv"),
        kind="delta_frame",
        ref="frame_cum_delta",
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job="job_cum_delta",
        created_at=datetime(2026, 7, 8, 10, 0, 0, tzinfo=UTC),
        row_count=1,
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="compare",
                    job_ref="job_cum_delta",
                    inputs=["frame_a", "frame_b"],
                    params_digest="sha256:compare",
                )
            ],
        ),
        metric_id="sales.cum_gmv",
        source_current_ref="frame_a",
        source_baseline_ref="frame_b",
        alignment={"kind": "window_bucket"},
        semantic_kind="segmented",
        semantic_model="sales",
        cumulative={"kind": "all_history", "over": "sales.orders.order_date"},
    )
    frame = DeltaFrame(_df=pd.DataFrame({"region": ["US"], "delta": [1.0]}), meta=meta)
    frame.meta = persist_frame(session, frame)

    # Strip the cumulative_attribution key: this is the exact persisted shape a
    # pre-1a13abf5 cumulative delta artifact has (schema string correct, key
    # missing). The sub-schema is carried in meta["artifact_schema"].
    meta_path = session._layout.frames_dir / frame.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    assert "cumulative_attribution" not in payload
    payload["artifact_schema"] = "cumulative-delta/v1"
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        session.get_frame(frame.ref)

    err = exc_info.value
    assert "cumulative_attribution" in err.message
    assert err.repair is not None
    assert err.repair.kind == "retry"
    # The repair must guide the agent through both the observe and the compare
    # step (cumulative delta rebuild needs both).
    assert "observe" in err.repair.action.lower()
    assert "compare" in err.repair.action.lower()
    # Location carried by the construction site (no _derive_fields fallback).
    assert err.location is not None
    assert "cumulative_attribution" in err.location
    assert "Repair:" in str(err)


def test_cumulative_delta_unsupported_schema_reports_expected_and_received(tmp_path):
    """Issue #65 review P2-1: the cumulative-delta unsupported-schema raise must
    carry ``expected``/``received`` as explicit kwargs so the diagnostic fact
    ("written with schema X, current requires cumulative-delta/v1") reaches the
    agent in str(e) — not as dead context keys it can never see."""
    from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
    from marivo.analysis.session._runtime import persist_frame

    session = session_attach.get_or_create(name="demo")
    meta = DeltaFrameMeta(
        **make_test_delta_contract("sales.cum_gmv"),
        kind="delta_frame",
        ref="frame_cum_delta_v0",
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job="job_cum_delta_v0",
        created_at=datetime(2026, 7, 8, 10, 0, 0, tzinfo=UTC),
        row_count=1,
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="compare",
                    job_ref="job_cum_delta_v0",
                    inputs=["frame_a", "frame_b"],
                    params_digest="sha256:compare",
                )
            ],
        ),
        metric_id="sales.cum_gmv",
        source_current_ref="frame_a",
        source_baseline_ref="frame_b",
        alignment={"kind": "window_bucket"},
        semantic_kind="segmented",
        semantic_model="sales",
        cumulative={"kind": "all_history", "over": "sales.orders.order_date"},
    )
    frame = DeltaFrame(_df=pd.DataFrame({"region": ["US"], "delta": [1.0]}), meta=meta)
    frame.meta = persist_frame(session, frame)

    meta_path = session._layout.frames_dir / frame.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    payload["artifact_schema"] = "cumulative-delta/v0"
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        session.get_frame(frame.ref)

    err = exc_info.value
    assert "unsupported cumulative delta artifact schema" in err.message
    assert err.expected == "cumulative-delta/v1"
    assert err.received == "cumulative-delta/v0"
    assert "Expected: cumulative-delta/v1" in str(err)
    assert "Received: cumulative-delta/v0" in str(err)
    assert err.repair is not None
    assert "Repair:" in str(err)
