"""correlate wired through commit_result."""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.frames.metric import MetricFrame
from tests.shared_fixtures import make_metric_frame


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _metric(session, df: pd.DataFrame, *, metric_id: str) -> MetricFrame:
    return make_metric_frame(
        df,
        metric_id=metric_id,
        axes={},
        measure={"name": metric_id.rsplit(".", 1)[-1]},
        semantic_kind="time_series",
        semantic_model="sales",
        session=session,
    )


def test_correlate_populates_surface1_and_correlation_finding() -> None:
    session = session_attach.get_or_create(name="correlate_evidence")
    revenue = _metric(
        session,
        pd.DataFrame(
            {"bucket": ["2026-01-01", "2026-01-02", "2026-01-03"], "value": [1.0, 2.0, 3.0]}
        ),
        metric_id="sales.revenue",
    )
    orders = _metric(
        session,
        pd.DataFrame(
            {"bucket": ["2026-01-01", "2026-01-02", "2026-01-03"], "value": [2.0, 4.0, 6.0]}
        ),
        metric_id="sales.orders",
    )

    result = session.correlate(revenue, orders, method="pearson")

    assert result.meta.artifact_id is not None
    assert result.meta.ref == result.meta.artifact_id
    assert result.meta.evidence_status == "complete"

    with sqlite3.connect(session._layout.session_dir / "judgment.db") as conn:
        artifact_rows = conn.execute(
            "SELECT step_type, artifact_type, evidence_status FROM artifacts WHERE artifact_id=?",
            (result.meta.artifact_id,),
        ).fetchall()
        finding_types = conn.execute(
            "SELECT finding_type FROM findings WHERE artifact_id=?",
            (result.meta.artifact_id,),
        ).fetchall()
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    assert artifact_rows == [("correlate", "association_result", "complete")]
    assert finding_types == [("correlation_result",)]
    assert "propositions" not in tables
    assert result.evidence_digest is not None
    association = result.evidence_digest.items[0]
    assert association.kind == "association"
    assert association.epistemic_kind == "estimated"
    assert "causal_effect_not_estimated" in {
        boundary.kind for boundary in result.evidence_digest.boundaries
    }


def test_correlate_single_lag_evidence_writes_lag_zero() -> None:
    """The single-lag (default) path must write an evidence lag of 0, never
    not_computed. Without this pin a refactor could silently revert single-mode
    selected_lag_offset to None while range-mode tests stay green."""
    session = session_attach.get_or_create(name="correlate_evidence_single")
    a = _metric(
        session,
        pd.DataFrame({"value": [1.0, 2.0, 3.0]}),
        metric_id="sales.a",
    )
    b = _metric(
        session,
        pd.DataFrame({"value": [2.0, 4.0, 6.0]}),
        metric_id="sales.b",
    )

    result = session.correlate(a, b, method="pearson")

    assert result.meta.selection_rule == "single_lag"
    assert result.meta.selected_lag_offset == 0
    assert result.evidence_digest is not None
    association = result.evidence_digest.items[0]
    assert association.lag == 0.0
    assert association.coefficient == pytest.approx(1.0)


def test_correlate_range_evidence_matches_selected_best_lag() -> None:
    """With lag_range, the evidence coefficient/lag must match the selected best
    lag (max abs correlation, closest on tie), NOT the first row of the table."""
    session = session_attach.get_or_create(name="correlate_evidence_range")
    a = _metric(
        session,
        pd.DataFrame({"value": [1.0, 3.0, 2.0, 5.0, 4.0, 6.0]}),
        metric_id="sales.a",
    )
    # b is a shifted-by-2 copy of a; only lag 2 recovers a perfect correlation.
    b = _metric(
        session,
        pd.DataFrame({"value": [0.0, 0.0, 1.0, 3.0, 2.0, 5.0]}),
        metric_id="sales.b",
    )

    result = session.correlate(a, b, lag_range=range(-3, 4))

    assert result.meta.best_lag == 2
    assert result.meta.correlation == pytest.approx(1.0)
    assert result.evidence_digest is not None
    association = result.evidence_digest.items[0]
    assert association.kind == "association"
    assert association.lag == 2.0
    assert association.coefficient == pytest.approx(1.0)
    assert association.sample_size == result.meta.aligned_row_count


def test_correlate_range_evidence_summary_renders_selected_lag() -> None:
    """The rendered evidence summary must state the selected lag, never
    not_computed, and must agree with meta.best_lag."""
    session = session_attach.get_or_create(name="correlate_evidence_summary")
    a = _metric(
        session,
        pd.DataFrame({"value": [1.0, 3.0, 2.0, 5.0, 4.0, 6.0]}),
        metric_id="sales.a",
    )
    b = _metric(
        session,
        pd.DataFrame({"value": [0.0, 0.0, 1.0, 3.0, 2.0, 5.0]}),
        metric_id="sales.b",
    )

    result = session.correlate(a, b, lag_range=range(-3, 4))

    assert result.evidence_digest is not None
    rendered = result.evidence_digest.render()
    assert f"lag={result.meta.best_lag}" in rendered
    assert "lag=not_computed" not in rendered
