"""Phase 2 core operator surface regression tests."""

from __future__ import annotations

import marivo.analysis as mv
import marivo.analysis.intents as intents
import marivo.analysis.session as session_attach


def test_intents_export_hypothesis_test_not_test_alias() -> None:
    assert "hypothesis_test" in intents.__all__
    assert "test" not in intents.__all__
    assert callable(intents.hypothesis_test)
    assert not hasattr(intents, "test")


def test_session_default_surface_has_no_test_alias(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    session = mv.session.get_or_create(name="phase2")

    assert callable(session.hypothesis_test)
    assert not hasattr(session, "test")


def test_session_default_surface_excludes_scratch_and_promotion(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    session = mv.session.get_or_create(name="phase2")
    names = set(dir(session))

    for removed in (
        "from_pandas",
        "explore_ibis",
        "promote_metric_frame",
        "promote_delta_frame",
        "promote_attribution_frame",
    ):
        assert removed not in names
        assert not hasattr(session, removed)


def test_top_level_analysis_no_longer_exports_scratch_or_promotion_types() -> None:
    for removed in ("ExplorationResult", "PromotionPolicy", "PromotionSemanticAnchors"):
        assert removed not in mv.__all__
        assert not hasattr(mv, removed)


def test_help_default_operator_surface_is_phase2_core() -> None:
    text = mv.help_text()

    for expected in (
        "observe",
        "compare",
        "attribute",
        "discover",
        "correlate",
        "hypothesis_test",
        "forecast",
        "assess_quality",
    ):
        assert expected in text

    for removed in (
        "measure",
        "compare_frames",
        "correlate_frames",
        "forecast_frame(",
        "explain",
        "scan(",
        "session.test(",
        "assess(",
        "decompose",
        "promote_metric_frame",
        "promote_delta_frame",
        "promote_attribution_frame",
        "from_pandas",
        "explore_ibis",
    ):
        assert removed not in text


def test_delta_affordance_uses_attribute_not_decompose(tmp_path, monkeypatch) -> None:
    from datetime import UTC, datetime

    import pandas as pd

    from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
    from marivo.analysis.lineage import Lineage

    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    session = mv.session.get_or_create(name="phase2")
    delta = DeltaFrame(
        _df=pd.DataFrame({"region": ["US"], "delta": [1.0]}),
        meta=DeltaFrameMeta(
            kind="delta_frame",
            ref="frame_delta",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_compare",
            created_at=datetime(2026, 6, 1, tzinfo=UTC),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            metric_id="sales.revenue",
            source_current_ref="frame_current",
            source_baseline_ref="frame_baseline",
            alignment={"kind": "window_bucket"},
            semantic_kind="segmented",
            semantic_model="sales",
        ),
    )

    capability_ids = {item.capability_id for item in delta.contract().affordances}
    assert "attribute" in capability_ids
    assert "decompose" not in capability_ids
