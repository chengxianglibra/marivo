"""Lock the agent-advertised Session surface (dir, help, evidence namespace)."""

from __future__ import annotations

import marivo.analysis as mv

EXPECTED_SESSION_IDENTITY_FIELDS = (
    "id",
    "name",
    "question",
    "state",
    "created_at",
    "updated_at",
    "default_calendar",
    "tz",
    "cwd",
    "project_root",
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

    assert callable(session.findings)
    assert callable(session.propositions)
    assert callable(session.assessments)


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
        "from_pandas",
        "explore_ibis",
        "promote_metric_frame",
        "promote_delta_frame",
        "promote_attribution_frame",
    ):
        assert advertised in names, f"missing advertised member: {advertised}"
    for hidden in (
        "_HIDDEN_FROM_DIR",
        "layout",
        "semantic_project",
        "backend_factory",
        "backend_cache",
        "calendars",
        "known_calendars",
        "known_datasources",
        "judgment_store",
        "judgment_store_unavailable",
        "evidence_store",
        "findings",
        "propositions",
        "assessments",
    ):
        assert hidden not in names, f"plumbing leaked into dir(): {hidden}"
    assert "validate" not in names
    assert "run_followup" not in names


def test_hidden_members_are_still_callable(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    assert session.layout is not None
    assert callable(session.evidence_store)


def test_help_session_lists_object_methods():
    data = mv.help("session", format="json")
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
        "frames",
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


def test_help_session_lists_identity_fields():
    data = mv.help("session", format="json")
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
