"""Current period Help and terminal disclosure have one executable entry shape."""

from datetime import timedelta
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo._help.render import render_help_text
from tests.analysis.numeric.test_period_alignment import period
from tests.shared_fixtures import DslCaseFactory, frozen_coordinates, terminal_runs


def test_named_comparison_and_terminal_rows_disclose_current_protocol(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import marivo.analysis as mv

    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, 3)
    _, baseline = period(case.session, 7, 3)
    comparison = current.compare(
        {
            "reference": mv.baseline(
                baseline,
                match=mv.ordinal(),
                on=tuple(zip(current.axes, baseline.axes, strict=True)),
            )
        }
    )
    selected = comparison.select({"value": comparison["reference.absolute.value"]})
    defined = selected.where(~selected["value"].is_null())
    assert defined.reduce({"count": mv.row_count()}).compute().to_pandas()["count"].tolist() == [12]
    saved = comparison.persist()
    artifact = saved
    assert saved.to_pandas().attrs == {}
    assert saved.schema.axes[0].role == "calendar"
    assert saved.schema.comparisons[0].alias == "reference"
    assert len(frozen_coordinates(saved)) == 3
    artifact.show()
    audit = capsys.readouterr().out
    assert saved.artifact_id in audit
    assert not hasattr(artifact, "contract") and not hasattr(artifact, "execute")


def test_offset_shift_and_named_compare_help_examples_are_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis as mv
    from marivo._help.render import render_help_text

    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("period-help", timezone="UTC")
    axis = session.time_scope(start="2026-01-01", end="2026-02-01").by(mv.grain("month"))
    frame = session.values(by=(), columns={"value": "CNY"}, rows=(((), {"value": 12}),))
    previous = session.values(by=(), columns={"value": "CNY"}, rows=(((), {"value": 8}),))
    for target in ("offset", "calendar_axis.shift", "analysis_frame.compare"):
        page = render_help_text("analysis." + target)[0]
        code = page.split("Example:\n", 1)[1].split("\nExpected:", 1)[0].split("\nSee:", 1)[0]
        namespace: dict[str, object] = {
            "axis": axis,
            "frame": frame,
            "previous": previous,
        }
        exec(compile(code, "<period-help-example>", "exec"), namespace)
        result = namespace["result"]
        assert len(page) < 5000
        if target == "calendar_axis.shift":
            assert isinstance(result, mv.CalendarAxis)
        elif target == "analysis_frame.compare":
            assert isinstance(result, mv.AnalysisFrame)
            assert result.output_names == (
                "value",
                "reference.baseline.value",
                "reference.absolute.value",
                "reference.relative.value",
            )
            assert result.presence_names == ("current.present", "reference.present")
            assert result.compute().to_pandas()["reference.relative.value"].tolist() == [0.5]
    assert "analysis.calendar_axis.shift" in render_help_text("analysis.calendar_axis")[0]
    with pytest.raises(ValueError):
        axis.shift(mv.ordinal())
    for name in ("period_offset", "previous_year"):
        assert not hasattr(mv, name)
    assert terminal_runs(session) == ()


def test_compare_help_discloses_accepted_exact_duration_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis as mv

    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("duration-help", timezone="UTC")
    current = session.values(
        by=(), columns={"elapsed": "s"}, rows=(((), {"elapsed": timedelta(microseconds=38)}),)
    )
    baseline = session.values(
        by=(), columns={"elapsed": "s"}, rows=(((), {"elapsed": timedelta(microseconds=19)}),)
    )
    result = current.compare({"reference": mv.baseline(baseline)}).compute()
    data = result.to_pandas()
    assert data["reference.baseline.elapsed"].tolist() == [timedelta(microseconds=19)]
    assert data["reference.absolute.elapsed"].tolist() == [timedelta(microseconds=19)]
    assert data["reference.relative.elapsed"].tolist() == [1.0]
    assert result.schema.values["reference.relative.elapsed"].unit is None
    assert result.presence_names == ("current.present", "reference.present")
    assert terminal_runs(session) == ()


def test_baseline_help_routes_fields_and_example_without_execution(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo.analysis._capabilities.dataset_model import CallableInput
    from marivo.analysis._capabilities.dataset_registry import prepare
    from marivo.cli import main

    session = analysis_dsl_case_factory("j2").session
    frame = session.values(by=(), columns={"value": None}, rows=(((), {"value": 3}),))
    assert "analysis.baseline" in render_help_text("analysis.inputs.comparison")[0]
    descriptor = prepare().by_canonical_id("baseline")
    assert isinstance(descriptor, CallableInput)
    namespace: dict[str, object] = {"mv": mv, "frame": frame}
    exec(descriptor.example.code, namespace)
    assert isinstance(namespace["result"], mv.ComparisonBaseline)
    assert namespace["result"].frame is frame
    capsys.readouterr()
    main(["help", "analysis.baseline"])
    cli_text = capsys.readouterr().out
    assert "analysis.comparison_baseline" in cli_text
