"""Public Help repairs must lead to the same independently queryable page."""

from __future__ import annotations

import re
from decimal import Decimal
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
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.cli import main
from tests.shared_fixtures import DslCaseFactory, catalog_value


@pytest.fixture
def literal_frame(analysis_dsl_case_factory: DslCaseFactory) -> mv.AnalysisFrame:
    session = analysis_dsl_case_factory("j1").session
    region = catalog_value(session.catalog, "sales.customer.region", kind="field")
    return session.values(
        by=(region,),
        columns={"value": None},
        rows=((("east",), {"value": 1}), (("west",), {"value": 3})),
    ).compute()


def _assert_repair(error: AnalysisError, target: str) -> None:
    assert error.expected and error.received
    assert error.repair is not None
    assert error.repair.action
    assert error.repair.help_target.display == target
    assert render_help_text(target)[0].startswith(f"Target: {target}\n")


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
        ("semantic_catalog.search", "semantic.semantic_catalog.search"),
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
    session = mv.session.get_or_create("attribute-fact-repair")
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


def test_pending_export_repairs_with_the_returned_complete_frame(
    literal_frame: mv.AnalysisFrame,
) -> None:
    pending = literal_frame.select({"renamed": literal_frame["value"]})
    with pytest.raises(CoreRuleError) as captured:
        pending.to_pandas()
    _assert_repair(captured.value, "analysis.analysis_frame.to_pandas")
    assert captured.value.received == "expression"
    assert "compute() or persist()" in captured.value.repair.action
    assert pending.compute().to_pandas()["renamed"].tolist() == [1, 3]
    with pytest.raises(CoreRuleError):
        pending.to_pandas()


@pytest.mark.parametrize("consumer", ("reduce", "over"))
def test_stale_statistics_keep_the_actual_consumer_and_repair(
    literal_frame: mv.AnalysisFrame, consumer: str
) -> None:
    selected = literal_frame.where(literal_frame["value"] > 1)
    stale = literal_frame["value"].sum()
    with pytest.raises(CoreRuleError) as captured:
        if consumer == "reduce":
            selected.reduce({"total": stale})
        else:
            selected.over(stale)
    _assert_repair(captured.value, f"analysis.analysis_frame.{consumer}")
    assert captured.value.location == f"frame.{consumer}"
    assert captured.value.expected == "a statistic from this exact Frame input"
    assert "Reacquire the Column on this Frame" in captured.value.repair.action
    current = selected["value"].sum()
    repaired = (
        selected.reduce({"total": current})
        if consumer == "reduce"
        else selected.select({"total": selected.over(current)})
    )
    assert repaired.compute().to_pandas()["total"].tolist() == [3]


@pytest.mark.parametrize("consumer", ("where", "top", "select"))
def test_stale_columns_keep_the_receiving_frame_operation(
    literal_frame: mv.AnalysisFrame, consumer: str
) -> None:
    selected = literal_frame.select({"value": literal_frame["value"]})
    with pytest.raises(CoreRuleError) as captured:
        if consumer == "where":
            selected.where(literal_frame["value"] > 1)
        elif consumer == "top":
            selected.top(1, by=literal_frame["value"])
        else:
            selected.select({"value": literal_frame["value"]})
    _assert_repair(captured.value, f"analysis.analysis_frame.{consumer}")
    assert captured.value.expected == "a Column from this exact Store/Session/Frame input"
    assert captured.value.location == f"frame.{consumer}"
    if consumer == "where":
        repaired = selected.where(selected["value"] > 1)
    elif consumer == "top":
        repaired = selected.top(1, by=selected["value"])
    else:
        repaired = selected.select({"value": selected["value"]})
    expected = [1, 3] if consumer == "select" else [3]
    assert repaired.compute().to_pandas()["value"].tolist() == expected


def test_share_foreign_axis_repairs_on_the_columns_own_frame(
    literal_frame: mv.AnalysisFrame,
) -> None:
    selected = literal_frame.select({"value": literal_frame["value"]})
    with pytest.raises(CoreRuleError) as captured:
        selected["value"].share(along=literal_frame.axes)
    _assert_repair(captured.value, "analysis.column.share")
    assert captured.value.location == "Column.share"
    assert captured.value.expected == "an axis from this exact Frame input"
    shares = selected["value"].share(along=selected.axes)
    result = selected.select({"share": shares}).compute().to_pandas()
    assert result["share"].tolist() == [Decimal("0.25"), Decimal("0.75")]


@pytest.mark.parametrize("consumer", ("align", "compare"))
def test_shared_axis_mapping_repairs_keep_the_binary_consumer(
    literal_frame: mv.AnalysisFrame, consumer: str
) -> None:
    other = literal_frame.select({"value": literal_frame["value"]})
    with pytest.raises(CoreRuleError) as captured:
        if consumer == "align":
            literal_frame.align(other, on=())
        else:
            literal_frame.compare({"reference": mv.baseline(other, on=())})
    _assert_repair(captured.value, f"analysis.analysis_frame.{consumer}")
    assert captured.value.location == f"frame.{consumer}"
    assert captured.value.expected == "a complete explicit axis bijection"
    pairs = ((literal_frame.axes[0], other.axes[0]),)
    repaired = (
        literal_frame.align(other, on=pairs)
        if consumer == "align"
        else literal_frame.compare({"reference": mv.baseline(other, on=pairs)})
    )
    rows = repaired.compute().to_pandas()
    path = "left.value" if consumer == "align" else "value"
    assert rows[path].tolist() == [1, 3]


def test_real_show_failure_has_a_precise_owner_and_executable_repair(
    literal_frame: mv.AnalysisFrame, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(CoreRuleError) as captured:
        literal_frame.show(n=-1)
    _assert_repair(captured.value, "analysis.analysis_frame.show")
    assert captured.value.expected == "a nonnegative integer preview count"
    assert captured.value.received == "-1"
    literal_frame.show(n=0)
    assert "AnalysisFrame" in capsys.readouterr().out


def test_correlate_shared_pair_validation_keeps_the_frame_owner(
    literal_frame: mv.AnalysisFrame,
) -> None:
    labels = literal_frame.coord(literal_frame.axes[0])
    with pytest.raises(CoreRuleError) as captured:
        literal_frame.correlate(labels, literal_frame["value"], method="pearson")
    _assert_repair(captured.value, "analysis.analysis_frame.correlate")
    assert captured.value.expected == "a numeric Column (int64, float64 or Decimal)"
    result = (
        literal_frame.correlate(literal_frame["value"], literal_frame["value"], method="pearson")
        .compute()
        .to_pandas()
    )
    assert result["coefficient"].tolist() == pytest.approx([1])
    assert result["n_pairs"].tolist() == [2]


def test_reverse_operator_failure_routes_to_its_existing_operator_page(
    literal_frame: mv.AnalysisFrame,
) -> None:
    with pytest.raises(CoreRuleError) as captured:
        _ = "invalid" - literal_frame["value"]
    _assert_repair(captured.value, "analysis.column.__rsub__")
    assert captured.value.expected == "numeric Columns"
    repaired = literal_frame.select({"remaining": 10 - literal_frame["value"]})
    assert repaired.compute().to_pandas()["remaining"].tolist() == [9, 7]


@pytest.mark.parametrize("consumer", ("sum", "share"))
def test_column_statistic_validation_keeps_the_public_consumer(
    literal_frame: mv.AnalysisFrame, consumer: str
) -> None:
    labels = literal_frame.coord(literal_frame.axes[0])
    with pytest.raises(CoreRuleError) as captured:
        if consumer == "sum":
            labels.sum()
        else:
            labels.share(along=literal_frame.axes)
    _assert_repair(captured.value, f"analysis.column.{consumer}")
    assert captured.value.expected == "a numeric Column"
    expected: list[int | Decimal]
    if consumer == "sum":
        result = literal_frame.reduce({"result": literal_frame["value"].sum()})
        expected = [4]
    else:
        result = literal_frame.select(
            {"result": literal_frame["value"].share(along=literal_frame.axes)}
        )
        expected = [Decimal("0.25"), Decimal("0.75")]
    assert result.compute().to_pandas()["result"].tolist() == expected


def test_invalid_select_name_keeps_its_owner_through_shared_name_validation(
    literal_frame: mv.AnalysisFrame,
) -> None:
    with pytest.raises(CoreRuleError) as captured:
        literal_frame.select({"invalid.name": literal_frame["value"]})
    _assert_repair(captured.value, "analysis.analysis_frame.select")
    assert captured.value.received == "'invalid.name'"
    repaired = literal_frame.select({"renamed": literal_frame["value"]})
    assert repaired.compute().to_pandas()["renamed"].tolist() == [1, 3]


@pytest.mark.parametrize(
    ("consumer", "read_boundary"),
    (
        ("compute", "read_complete"),
        ("to_pandas", "read_data"),
        ("persist", "open_frame"),
        ("findings", "findings"),
        ("evidence_digest", "evidence_digest"),
        ("method_facts", "read_method_facts"),
        ("show", "preview"),
    ),
)
def test_frame_consumers_preserve_the_specialized_storage_failure(
    literal_frame: mv.AnalysisFrame,
    monkeypatch: pytest.MonkeyPatch,
    consumer: str,
    read_boundary: str,
) -> None:
    saved = literal_frame.persist()
    failure = StorageAccessError("unauthorized")

    def unavailable(*args: object, **kwargs: object) -> None:
        raise failure

    with monkeypatch.context() as fault:
        fault.setattr(LocalFrameServices, read_boundary, unavailable)
        with pytest.raises(StorageAccessError) as captured:
            if consumer == "method_facts":
                _ = getattr(saved, consumer)
            else:
                getattr(saved, consumer)()
    assert captured.value is failure
    assert captured.value.storage_status == "unauthorized"
    _assert_repair(captured.value, "analysis.session.persisted_frame")
    assert saved.to_pandas()["value"].tolist() == [1, 3]


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
    main(["help", "analysis.operators"])
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
