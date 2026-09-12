"""Lock the agent-advertised Run-first Session surface."""

from __future__ import annotations

import marivo.analysis as mv


def _session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create(name="surface_probe")


def test_session_removes_the_legacy_evidence_namespace(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    assert not hasattr(session, "evidence")


def test_dir_advertises_intents_and_hides_plumbing(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    names = set(dir(session))
    for advertised in (
        "observe",
        "population",
        "events",
        "lifecycle",
        "source_bindings",
        "runs",
        "get_run",
        "artifact",
        "revalidate",
        "graph",
        "catalog",
    ):
        assert advertised in names, f"missing advertised member: {advertised}"
    for removed in (
        "evidence",
        "frame_summaries",
        "get_frame",
        "jobs",
        "recent_jobs",
        "job",
        "from_pandas",
        "explore_ibis",
        "promote_metric_frame",
        "promote_delta_frame",
        "promote_attribution_frame",
        "assess_quality",
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
    assert session._runtime is not None
    assert "_runtime" not in dir(session)


def test_session_no_longer_exposes_transform_namespace(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)

    assert not hasattr(session, "transform")


def test_session_namespaces_are_typed_helpers_only(authoring_evidence_project, monkeypatch):
    session = _session(authoring_evidence_project, monkeypatch)

    assert not callable(session.events)
    assert callable(session.events.match)
    assert not callable(session.lifecycle)
    assert callable(session.lifecycle.replay)
