"""Public Help repairs must lead to the same independently queryable page."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo._help.model import MarivoHelpTargetError
from marivo._help.render import render_help_text
from marivo.analysis.errors import (
    AnalysisError,
    ArtifactNotFoundError,
    SessionNotFoundError,
)
from marivo.analysis.materialization.errors import StorageAccessError
from marivo.cli import main


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("analysis.discovery_snapshot", "datasource.discovery_snapshot"),
        ("analysis.semantic_catalog", "semantic.semantic_catalog"),
        ("semantic.raw_sql_result", "datasource.raw_sql_result"),
        ("semantic.query.output_names", "analysis.query.output_names"),
    ],
)
def test_cross_surface_suggestions_are_complete_resolvable_targets(
    requested: str, expected: str
) -> None:
    with pytest.raises(MarivoHelpTargetError) as captured:
        render_help_text(requested)
    candidates = captured.value.candidates
    assert candidates[0] == expected
    for candidate in candidates:
        text, _, _ = render_help_text(candidate)
        assert text.splitlines()[0] == f"Target: {candidate}"


@pytest.mark.parametrize(
    "error_type", [AnalysisError, ArtifactNotFoundError, SessionNotFoundError, StorageAccessError]
)
def test_private_error_classes_reuse_the_registered_error_contract(
    error_type: type[AnalysisError],
) -> None:
    text, _, _ = render_help_text(error_type)
    target = text.splitlines()[0].removeprefix("Target: ")
    assert target == "analysis.analysis_error"
    assert "Kind: error" in text
    assert render_help_text(target)[0] == text
    assert render_help_text(AnalysisError)[0] == text


def test_actual_failure_type_advertises_a_queryable_static_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(name="help-error-roundtrip")
    with pytest.raises(ArtifactNotFoundError) as captured:
        session.artifact("missing")
    text, _, _ = render_help_text(type(captured.value))
    target = text.splitlines()[0].removeprefix("Target: ")
    assert render_help_text(target)[0] == text
    # Concrete repair and received input stay on the failure, not static Help.
    assert captured.value.received == "missing"
    assert "missing" not in text


def test_cli_cross_surface_repair_can_be_followed(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as captured:
        main(["help", "analysis.discovery_snapshot"])
    assert captured.value.code == 2
    failure = capsys.readouterr()
    assert failure.out == ""
    assert "Candidates: datasource.discovery_snapshot" in failure.err
    main(["help", "datasource.discovery_snapshot"])
    repaired = capsys.readouterr()
    assert repaired.err == ""
    assert repaired.out.rstrip() == render_help_text("datasource.discovery_snapshot")[0].rstrip()


def test_cli_can_open_the_error_type_page(capsys: pytest.CaptureFixture[str]) -> None:
    text, _, _ = render_help_text(ArtifactNotFoundError)
    target = text.splitlines()[0].removeprefix("Target: ")
    main(["help", target])
    result = capsys.readouterr()
    assert result.err == ""
    assert result.out.rstrip() == text.rstrip()
