"""Runtime family gate tests: registry ``accepted_inputs`` is the single source.

Tests cover two layers:

1. **Property-style family matrix** — for each invokable descriptor with
   accepted_inputs, assert that registered families pass the private gate and
   every other public family raises ``AnalysisError`` with the correct
   ``location`` and ``repair.help_target``.

2. **Public-entry integration** — for each Session operator, concrete
   discover/transform method, and governed boundary, pass a wrong public
   family and assert the failure originates at the shared gate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis._capabilities.validation import (
    classify_input_family,
    validate_capability_inputs,
)
from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.policies import AlignmentPolicy, SamplingPolicy
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic.catalog import SemanticKind
from tests.ref_helpers import make_ref
from tests.shared_fixtures import make_metric_frame, make_test_delta_contract

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield
    session_attach._reset_process_state()


def _metric_frame(session, *, semantic_kind="time_series"):
    return make_metric_frame(
        pd.DataFrame({"bucket": ["a", "b", "c", "d"], "value": [10.0, 20.0, 30.0, 40.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "field": "bucket", "grain": "day"}},
        measure={"name": "value"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        session=session,
    )


def _delta_frame(session, *, semantic_kind="time_series"):
    meta = DeltaFrameMeta(
        **make_test_delta_contract("sales.revenue"),
        kind="delta_frame",
        ref="frame_delta",
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job="job_test",
        created_at=datetime.now(UTC),
        row_count=4,
        byte_size=0,
        lineage=Lineage(
            steps=[LineageStep(intent="compare", job_ref="j", inputs=[], params_digest="t")]
        ),
        metric_id="sales.revenue",
        source_current_ref="frame_a",
        source_baseline_ref="frame_b",
        alignment={"kind": "window_bucket"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
    )
    return DeltaFrame(
        _df=pd.DataFrame({"bucket": ["a", "b", "c", "d"], "delta": [1.0, 2.0, 3.0, 4.0]}),
        meta=meta,
    )


# ---------------------------------------------------------------------------
# 1. classify_input_family
# ---------------------------------------------------------------------------


def test_classify_metric_frame():
    session = session_attach.get_or_create(name="cls")
    mf = _metric_frame(session)
    assert classify_input_family(mf) == "MetricFrame"


def test_classify_delta_frame():
    session = session_attach.get_or_create(name="cls")
    df = _delta_frame(session)
    assert classify_input_family(df) == "DeltaFrame"


def test_classify_alignment_policy():
    assert classify_input_family(AlignmentPolicy(kind="window_bucket")) == "AlignmentPolicy"


def test_classify_sampling_policy():
    assert classify_input_family(SamplingPolicy()) == "SamplingPolicy"


def test_classify_metric_semantic_ref():
    ref = make_ref("sales.revenue", SemanticKind.METRIC)
    assert classify_input_family(ref) == "MetricSemantic"


def test_classify_dimension_semantic_ref():
    ref = make_ref("sales.orders.region", SemanticKind.DIMENSION)
    assert classify_input_family(ref) == "DimensionSemantic"


def test_classify_time_dimension_semantic_ref():
    ref = make_ref("sales.orders.order_date", SemanticKind.TIME_DIMENSION)
    assert classify_input_family(ref) == "TimeDimensionSemantic"


# ---------------------------------------------------------------------------
# 2. validate_capability_inputs: registered families pass, others raise
# ---------------------------------------------------------------------------


def test_gate_compare_accepts_metric_frames():
    session = session_attach.get_or_create(name="mtx")
    a = _metric_frame(session)
    b = _metric_frame(session)
    validate_capability_inputs("compare", a=a, b=b, alignment=AlignmentPolicy(kind="window_bucket"))


def test_gate_compare_rejects_delta_frame_for_a():
    session = session_attach.get_or_create(name="mtx")
    mf = _metric_frame(session)
    df = _delta_frame(session)
    with pytest.raises(AnalysisError) as exc:
        validate_capability_inputs(
            "compare", a=df, b=mf, alignment=AlignmentPolicy(kind="window_bucket")
        )
    assert exc.value.location == "compare.a"
    assert exc.value.repair is not None
    assert exc.value.repair.help_target == LiveHelpTarget(
        surface="analysis", canonical_id="compare"
    )


def test_gate_compare_rejects_delta_frame_for_b():
    session = session_attach.get_or_create(name="mtx")
    mf = _metric_frame(session)
    df = _delta_frame(session)
    with pytest.raises(AnalysisError) as exc:
        validate_capability_inputs(
            "compare", a=mf, b=df, alignment=AlignmentPolicy(kind="window_bucket")
        )
    assert exc.value.location == "compare.b"


def test_gate_compare_rejects_wrong_alignment():
    session = session_attach.get_or_create(name="mtx")
    a = _metric_frame(session)
    b = _metric_frame(session)
    with pytest.raises(AnalysisError) as exc:
        validate_capability_inputs("compare", a=a, b=b, alignment="window_bucket")
    assert exc.value.location == "compare.alignment"
    assert exc.value.repair is not None
    assert exc.value.repair.help_target == LiveHelpTarget(
        surface="analysis", canonical_id="compare"
    )


def test_gate_attribute_accepts_delta_frame():
    session = session_attach.get_or_create(name="mtx")
    df = _delta_frame(session)
    axes = [make_ref("sales.orders.region", SemanticKind.DIMENSION)]
    validate_capability_inputs("attribute", frame=df, axes=axes)


def test_gate_attribute_rejects_metric_frame():
    session = session_attach.get_or_create(name="mtx")
    mf = _metric_frame(session)
    axes = [make_ref("sales.orders.region", SemanticKind.DIMENSION)]
    with pytest.raises(AnalysisError) as exc:
        validate_capability_inputs("attribute", frame=mf, axes=axes)
    assert exc.value.location == "attribute.frame"
    assert exc.value.repair is not None
    assert exc.value.repair.help_target == LiveHelpTarget(
        surface="analysis", canonical_id="attribute"
    )


def test_gate_forecast_accepts_metric_frame():
    session = session_attach.get_or_create(name="mtx")
    mf = _metric_frame(session)
    validate_capability_inputs("forecast", history=mf)


def test_gate_forecast_rejects_delta_frame():
    session = session_attach.get_or_create(name="mtx")
    df = _delta_frame(session)
    with pytest.raises(AnalysisError) as exc:
        validate_capability_inputs("forecast", history=df)
    assert exc.value.location == "forecast.history"


def test_gate_assess_quality_accepts_metric_frame():
    session = session_attach.get_or_create(name="mtx")
    mf = _metric_frame(session)
    validate_capability_inputs("assess_quality", target=mf)


def test_gate_assess_quality_rejects_delta_frame():
    session = session_attach.get_or_create(name="mtx")
    df = _delta_frame(session)
    with pytest.raises(AnalysisError) as exc:
        validate_capability_inputs("assess_quality", target=df)
    assert exc.value.location == "assess_quality.target"


def test_gate_correlate_rejects_delta_frame():
    session = session_attach.get_or_create(name="mtx")
    mf = _metric_frame(session)
    df = _delta_frame(session)
    with pytest.raises(AnalysisError) as exc:
        validate_capability_inputs(
            "correlate", a=df, b=mf, alignment=AlignmentPolicy(kind="window_bucket")
        )
    assert exc.value.location == "correlate.a"


def test_gate_hypothesis_test_rejects_delta_frame():
    session = session_attach.get_or_create(name="mtx")
    mf = _metric_frame(session)
    df = _delta_frame(session)
    with pytest.raises(AnalysisError) as exc:
        validate_capability_inputs(
            "hypothesis_test", a=mf, b=df, alignment=AlignmentPolicy(kind="window_bucket")
        )
    assert exc.value.location == "hypothesis_test.b"


def test_gate_discover_point_anomalies_accepts_metric_frame():
    session = session_attach.get_or_create(name="mtx")
    mf = _metric_frame(session)
    validate_capability_inputs("discover.point_anomalies", source=mf)


def test_gate_discover_point_anomalies_rejects_delta_frame():
    session = session_attach.get_or_create(name="mtx")
    df = _delta_frame(session)
    with pytest.raises(AnalysisError) as exc:
        validate_capability_inputs("discover.point_anomalies", source=df)
    assert exc.value.location == "discover.point_anomalies.source"


def test_gate_discover_period_shifts_accepts_delta_frame():
    session = session_attach.get_or_create(name="mtx")
    df = _delta_frame(session)
    validate_capability_inputs("discover.period_shifts", source=df)


def test_gate_discover_period_shifts_rejects_metric_frame():
    session = session_attach.get_or_create(name="mtx")
    mf = _metric_frame(session)
    with pytest.raises(AnalysisError) as exc:
        validate_capability_inputs("discover.period_shifts", source=mf)
    assert exc.value.location == "discover.period_shifts.source"


def test_gate_transform_filter_accepts_metric_frame():
    session = session_attach.get_or_create(name="mtx")
    mf = _metric_frame(session)
    validate_capability_inputs("transform.filter", receiver=mf)


def test_gate_transform_filter_accepts_delta_frame():
    session = session_attach.get_or_create(name="mtx")
    df = _delta_frame(session)
    validate_capability_inputs("transform.filter", receiver=df)


def test_gate_transform_normalize_rejects_delta_frame():
    session = session_attach.get_or_create(name="mtx")
    df = _delta_frame(session)
    with pytest.raises(AnalysisError) as exc:
        validate_capability_inputs("transform.normalize", receiver=df)
    assert exc.value.location == "transform.normalize.receiver"


def test_gate_metric_frame_metric_accepts_metric_frame():
    session = session_attach.get_or_create(name="mtx")
    mf = _metric_frame(session)
    validate_capability_inputs("MetricFrame.metric", receiver=mf)


def test_gate_metric_frame_metric_rejects_delta_frame():
    session = session_attach.get_or_create(name="mtx")
    df = _delta_frame(session)
    with pytest.raises(AnalysisError) as exc:
        validate_capability_inputs("MetricFrame.metric", receiver=df)
    assert exc.value.location == "MetricFrame.metric.receiver"


def test_gate_delta_frame_components_rejects_metric_frame():
    session = session_attach.get_or_create(name="mtx")
    mf = _metric_frame(session)
    with pytest.raises(AnalysisError) as exc:
        validate_capability_inputs("DeltaFrame.components", receiver=mf)
    assert exc.value.location == "DeltaFrame.components.receiver"


# ---------------------------------------------------------------------------
# 3. Public-entry integration: wrong family raises at the gate
# ---------------------------------------------------------------------------


def test_session_compare_rejects_delta_at_gate():
    session = session_attach.get_or_create(name="int")
    mf = _metric_frame(session)
    df = _delta_frame(session)
    with pytest.raises(AnalysisError) as exc:
        session.compare(mf, df)  # type: ignore[arg-type]
    assert exc.value.location == "compare.b"
    assert exc.value.repair is not None
    assert exc.value.repair.help_target == LiveHelpTarget(
        surface="analysis", canonical_id="compare"
    )


def test_session_attribute_rejects_metric_frame_at_gate():
    session = session_attach.get_or_create(name="int")
    mf = _metric_frame(session)
    with pytest.raises(AnalysisError) as exc:
        session.attribute(mf, axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)])  # type: ignore[arg-type]
    assert exc.value.location == "attribute.frame"


def test_session_forecast_rejects_delta_at_gate():
    session = session_attach.get_or_create(name="int")
    df = _delta_frame(session)
    with pytest.raises(AnalysisError) as exc:
        session.forecast(df, horizon=3)  # type: ignore[arg-type]
    assert exc.value.location == "forecast.history"


def test_session_assess_quality_rejects_delta_at_gate():
    session = session_attach.get_or_create(name="int")
    df = _delta_frame(session)
    with pytest.raises(AnalysisError) as exc:
        session.assess_quality(df)  # type: ignore[arg-type]
    assert exc.value.location == "assess_quality.target"


def test_session_correlate_rejects_delta_at_gate():
    session = session_attach.get_or_create(name="int")
    mf = _metric_frame(session)
    df = _delta_frame(session)
    with pytest.raises(AnalysisError) as exc:
        session.correlate(mf, df)  # type: ignore[arg-type]
    assert exc.value.location == "correlate.b"


def test_session_hypothesis_test_rejects_delta_at_gate():
    session = session_attach.get_or_create(name="int")
    mf = _metric_frame(session)
    df = _delta_frame(session)
    with pytest.raises(AnalysisError) as exc:
        session.hypothesis_test(mf, df)  # type: ignore[arg-type]
    assert exc.value.location == "hypothesis_test.b"


def test_discover_point_anomalies_rejects_delta_at_gate():
    session = session_attach.get_or_create(name="int")
    df = _delta_frame(session)
    with pytest.raises(AnalysisError) as exc:
        session.discover.point_anomalies(df)  # type: ignore[arg-type]
    assert exc.value.location == "discover.point_anomalies.source"


def test_discover_period_shifts_rejects_metric_at_gate():
    session = session_attach.get_or_create(name="int")
    mf = _metric_frame(session)
    with pytest.raises(AnalysisError) as exc:
        session.discover.period_shifts(mf)  # type: ignore[arg-type]
    assert exc.value.location == "discover.period_shifts.source"


def test_transform_normalize_rejects_delta_at_gate():
    session = session_attach.get_or_create(name="int")
    df = _delta_frame(session)
    with pytest.raises(AnalysisError) as exc:
        validate_capability_inputs("transform.normalize", receiver=df)
    assert exc.value.location == "transform.normalize.receiver"


def test_metric_frame_metric_rejects_delta_receiver_at_gate():
    session = session_attach.get_or_create(name="int")
    df = _delta_frame(session)
    with pytest.raises(AnalysisError) as exc:
        validate_capability_inputs("MetricFrame.metric", receiver=df)
    assert exc.value.location == "MetricFrame.metric.receiver"


# ---------------------------------------------------------------------------
# 4. Spy test: registered capability id reaches validate_capability_inputs
# ---------------------------------------------------------------------------


def test_compare_calls_gate_with_compare_id():
    session = session_attach.get_or_create(name="spy")
    mf = _metric_frame(session)
    df = _delta_frame(session)
    with patch("marivo.analysis._capabilities.validation.validate_capability_inputs") as mock_gate:
        mock_gate.side_effect = AnalysisError(
            message="gate spy",
            location="compare.b",
            repair=AnalysisRepair(
                kind="retry",
                action="spy",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
            ),
        )
        with pytest.raises(AnalysisError):
            session.compare(mf, df)
        mock_gate.assert_called_once()
        call_kwargs = mock_gate.call_args
        assert call_kwargs.args[0] == "compare"


def test_attribute_calls_gate_with_attribute_id():
    session = session_attach.get_or_create(name="spy")
    mf = _metric_frame(session)
    with patch("marivo.analysis._capabilities.validation.validate_capability_inputs") as mock_gate:
        mock_gate.side_effect = AnalysisError(
            message="gate spy",
            location="attribute.frame",
            repair=AnalysisRepair(
                kind="retry",
                action="spy",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="attribute"),
            ),
        )
        with pytest.raises(AnalysisError):
            session.attribute(
                mf,
                axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
            )
        mock_gate.assert_called_once()
        assert mock_gate.call_args.args[0] == "attribute"
