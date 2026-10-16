"""Lock the agent-advertised Run-first Session surface."""

from __future__ import annotations

import marivo.analysis as mv


def _session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create(name="surface_probe")


def test_dir_advertises_intents_and_hides_plumbing(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    names = set(dir(session))
    for advertised in (
        "observe",
        "members",
        "source_bindings",
        "runs",
        "get_run",
        "artifact",
        "graph",
        "catalog",
    ):
        assert advertised in names, f"missing advertised member: {advertised}"
    for hidden in (
        "population",
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
