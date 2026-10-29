"""Lock the public analysis Session surface without publication bookkeeping."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.analysis as mv


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> mv.Session:
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create(name="surface_probe")


def test_dir_advertises_intents_and_hides_plumbing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    names = set(dir(session))
    for advertised in (
        "observe",
        "members",
        "frame",
        "persisted_frame",
        "persisted_frames",
        "catalog",
    ):
        assert advertised in names, f"missing advertised member: {advertised}"
    for hidden in (
        "runs",
        "get_run",
        "artifacts",
        "artifact",
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


def test_run_types_and_help_are_removed() -> None:
    import marivo
    from marivo._help.model import MarivoHelpTargetError

    for name in ("FailedRun", "IncompleteRun", "SucceededRun", "RunPage"):
        assert not hasattr(mv, name)
    assert not hasattr(mv.session, "reconcile_run")
    for target in (
        "failed_run",
        "incomplete_run",
        "succeeded_run",
        "run_page",
        "session.runs",
        "session.get_run",
        "session.reconcile_run",
        "runtime.runs",
        "runtime.session_inspection",
    ):
        with pytest.raises(MarivoHelpTargetError):
            marivo.help("analysis." + target)
