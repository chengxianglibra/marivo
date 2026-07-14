"""Lock the agent-advertised Session surface (dir, help, evidence namespace)."""

from __future__ import annotations

import marivo.analysis as mv


def _session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create(name="surface_probe", use_datasources=False)


def test_evidence_namespace_exposes_audit_iterators(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)

    for name in (
        "findings",
        "propositions",
        "assessments",
        "proposition",
        "latest_assessment",
        "trace",
    ):
        assert callable(getattr(session.evidence, name))


def test_dir_advertises_intents_and_hides_plumbing(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    names = set(dir(session))
    for advertised in (
        "observe",
        "compare",
        "attribute",
        "discover",
        "correlate",
        "forecast",
        "assess_quality",
        "hypothesis_test",
        "evidence",
        "knowledge",
        "jobs",
        "recent_jobs",
        "close",
        "is_read_only",
        "catalog",
    ):
        assert advertised in names, f"missing advertised member: {advertised}"
    for removed in (
        "from_pandas",
        "explore_ibis",
        "promote_metric_frame",
        "promote_delta_frame",
        "promote_attribution_frame",
    ):
        assert removed not in names
    for hidden in (
        "layout",
        "semantic_project",
        "backend_factory",
        "backend_cache",
        "connection_runtime",
        "calendars",
        "known_calendars",
        "known_datasources",
        "judgment_store",
        "judgment_store_unavailable",
        "evidence_store",
        "findings",
        "propositions",
        "assessments",
        "_layout",
        "_connection_runtime",
        "_catalog",
        "_calendars",
        "_known_calendars",
        "_known_datasources",
        "_judgment_store",
        "_judgment_store_unavailable",
        "_evidence_store",
    ):
        assert hidden not in names, f"plumbing leaked into dir(): {hidden}"
    assert "validate" not in names
    assert "run_followup" not in names


def test_internal_fields_not_publicly_accessible(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)

    # Underscore-prefixed storage is reachable for internal code
    assert session._layout is not None
    assert callable(session._evidence_store)


def test_session_no_longer_exposes_transform_namespace(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)

    assert not hasattr(session, "transform")


def test_session_namespaces_are_typed_helpers_only(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)

    assert not callable(session.discover)
    assert callable(session.discover.point_anomalies)
    assert callable(session.discover.driver_axes)
