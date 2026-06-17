"""Lock the agent-advertised Session surface (dir, help, evidence namespace)."""

from __future__ import annotations

from typing import Any, cast

import marivo.analysis as mv
from marivo.introspection.surface import render as surface_render


def _mv_json_data(symbol: str | None = None) -> dict[str, Any]:
    """Return the JSON descriptor dict for an analysis symbol using internal render."""
    from marivo.analysis.help import _surface

    return cast("dict[str, Any]", surface_render(_surface(), symbol, "json"))


EXPECTED_SESSION_IDENTITY_FIELDS = (
    "id",
    "name",
    "question",
    "created_at",
    "updated_at",
    "default_calendar",
    "tz",
    "report_tz",
    "report_tz_name",
    "cwd",
    "project_root",
    "catalog",
)


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
        "decompose",
        "discover",
        "transform",
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
        "from_pandas",
        "explore_ibis",
        "promote_metric_frame",
        "promote_delta_frame",
        "promote_attribution_frame",
    ):
        assert advertised in names, f"missing advertised member: {advertised}"
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


def test_help_session_lists_object_methods():
    data = _mv_json_data("session")
    assert isinstance(data, dict)
    assert data["kind"] == "topic"
    expected_method_names = {
        "observe",
        "compare",
        "decompose",
        "correlate",
        "forecast",
        "assess_quality",
        "hypothesis_test",
        "discover",
        "transform",
        "evidence",
        "knowledge",
        "from_pandas",
        "explore_ibis",
        "promote_metric_frame",
        "promote_delta_frame",
        "promote_attribution_frame",
        "jobs",
        "recent_jobs",
        "job",
        "is_read_only",
        "close",
    }
    method_names = [m["name"] for m in data["content"]["methods"]]
    assert len(method_names) == len(set(method_names))
    assert set(method_names) == expected_method_names
    assert "validate" not in method_names
    assert "run_followup" not in method_names
    assert data["constraints"]
    assert data["content"]["constraints"]
    assert "Constraints:" in mv.help_text("session")


def test_session_namespaces_are_typed_helpers_only(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)

    assert not callable(session.discover)
    assert callable(session.discover.point_anomalies)
    assert callable(session.discover.driver_axes)

    assert not callable(session.transform)
    assert callable(session.transform.topk)
    assert callable(session.transform.slice)


def test_help_session_lists_identity_fields():
    data = _mv_json_data("session")
    assert isinstance(data, dict)
    content = data["content"]
    identity_fields = [field["name"] for field in content["identity_fields"]]
    assert len(identity_fields) == len(set(identity_fields))
    assert tuple(identity_fields) == EXPECTED_SESSION_IDENTITY_FIELDS

    text = mv.help_text("session")
    assert "Identity fields:" in text
    identity_section = text.split("Identity fields:\n", 1)[1].split("\n\nLifecycle:", 1)[0]
    text_fields = tuple(line.split(None, 1)[0] for line in identity_section.splitlines())
    assert text_fields == EXPECTED_SESSION_IDENTITY_FIELDS
