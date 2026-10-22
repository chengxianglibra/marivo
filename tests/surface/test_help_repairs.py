"""Public Help repairs must lead to the same independently queryable page."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo._help.model import MarivoHelpTargetError
from marivo._help.render import render_help_text
from marivo.analysis.core.frame_method_facts import (
    FrameMethodSupport,
)
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.errors import (
    AnalysisError,
    ArtifactNotFoundError,
    SessionNotFoundError,
)
from marivo.analysis.materialization.errors import StorageAccessError
from marivo.cli import main


def _cli_facts(text: str) -> str:
    return re.sub(
        r"marivo\.help\(([\"'])([a-z_][a-z0-9_.]*)\1\)",
        lambda match: "marivo help " + match.group(2),
        text,
    ).replace("marivo.help()", "marivo help")


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("analysis.discovery_snapshot", "datasource.discovery_snapshot"),
        ("analysis.semantic_catalog", "semantic.semantic_catalog"),
        ("semantic.raw_sql_result", "datasource.raw_sql_result"),
        ("semantic.analysis_frame.output_names", "analysis.analysis_frame.output_names"),
        ("readiness_report.to_dict", "semantic.readiness_report.to_dict"),
        ("datasource_summary", "datasource.datasource_summary"),
        ("semantic_catalog.fields", "semantic.semantic_catalog.fields"),
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
    session = mv.session.create(name="help-error-roundtrip")
    with pytest.raises(ArtifactNotFoundError) as captured:
        session.persisted_frame("missing")
    text, _, _ = render_help_text(type(captured.value))
    target = text.splitlines()[0].removeprefix("Target: ")
    assert render_help_text(target)[0] == text
    # Concrete repair and received input stay on the failure, not static Help.
    assert captured.value.received == "missing"
    assert "missing" not in text


def test_attribute_fact_read_repairs_are_exact_and_unrelated_errors_stay_at_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.create("attribute-fact-repair")
    pending = session.values(by=(), columns={"q": None}, rows=(((), {"q": 1}),))
    with pytest.raises(AnalysisError) as captured:
        _ = pending.method_facts
    assert captured.value.repair.help_target.display == "analysis.analysis_frame.method_facts"
    assert "Call compute() or persist()" in str(captured.value)
    assert "Kind: attribute" in render_help_text(captured.value.repair.help_target.display)[0]
    support = FrameMethodSupport("root", "rows", 1, 1, 1, 1)
    with pytest.raises(AnalysisError) as captured:
        support.show(n=-1)
    assert captured.value.repair.help_target.display == "analysis.frame_method_facts.show"
    assert "Kind: callable" in render_help_text(captured.value.repair.help_target.display)[0]
    unrelated = CoreRuleError(
        expected="a valid value", received="invalid", repair="Repair it.", location="frame.show"
    )
    assert unrelated.repair.help_target.display == "analysis"


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
    assert (
        repaired.out.rstrip()
        == _cli_facts(render_help_text("datasource.discovery_snapshot")[0]).rstrip()
    )


def test_cli_can_open_the_error_type_page(capsys: pytest.CaptureFixture[str]) -> None:
    text, _, _ = render_help_text(ArtifactNotFoundError)
    target = text.splitlines()[0].removeprefix("Target: ")
    main(["help", target])
    result = capsys.readouterr()
    assert result.err == ""
    assert result.out.rstrip() == _cli_facts(text).rstrip()


def test_cli_routes_can_be_copied_without_python_help(capsys: pytest.CaptureFixture[str]) -> None:
    main(["help", "analysis.frame.compare"])
    page = capsys.readouterr().out
    targets = re.findall(r"marivo help ([a-z_][a-z0-9_.]*)", page)
    assert "analysis.analysis_frame.compare" in targets
    assert "marivo.help(" not in page
    for target in targets:
        main(["help", target])
        assert capsys.readouterr().out.startswith(f"Target: {target}\n")


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("semantic.readiness_report.show", "analysis.runtime.values.show"),
        ("datasource.datasource_list.render", "analysis.runtime.values.render"),
        ("semantic.semantic_catalog.show", "analysis.runtime.values.show"),
        ("semantic.metric.show", "semantic.metric"),
        ("analysis.time_scope.month", "analysis.time_scope"),
        ("analysis.analysis_frame.comput", "analysis.analysis_frame.compute"),
    ],
)
def test_member_repair_preserves_receiver_before_lexical_similarity(
    requested: str, expected: str
) -> None:
    with pytest.raises(MarivoHelpTargetError) as captured:
        render_help_text(requested)
    assert captured.value.candidates[0] == expected
    for candidate in captured.value.candidates:
        assert render_help_text(candidate)[0].startswith(f"Target: {candidate}\n")
