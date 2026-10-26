"""Current period Help and terminal disclosure have one executable entry shape."""

from datetime import timedelta
from pathlib import Path

import pytest

import marivo
import marivo.analysis as mv
from marivo._help import help as help_coordinator
from marivo._help.render import render_help_text
from tests.analysis.numeric.test_period_alignment import period
from tests.shared_fixtures import DslCaseFactory, terminal_runs


@pytest.mark.parametrize(
    "target, expected",
    (
        ("analysis.analysis_frame.compare", "absolute baseline"),
        ("analysis.analysis_frame.align", "complete"),
        ("analysis.ordinal", "complete"),
        ("analysis.offset", "iso week-year"),
        ("analysis.calendar_axis.shift", "sparse or empty"),
        ("analysis.analysis_frame.reduce", "current-value"),
        ("analysis.analysis_frame.to_pandas", "complete"),
    ),
)
def test_native_help_discloses_alignment_constraints(
    target: str,
    expected: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert marivo.help is help_coordinator
    help_coordinator(target)
    assert expected in capsys.readouterr().out.lower()


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
    comparison.show()
    assert "pending" in capsys.readouterr().out
    selected = comparison.select({"value": comparison["reference.absolute.value"]})
    defined = selected.where(~selected["value"].is_null())
    assert defined.reduce({"count": mv.row_count()}).compute().to_pandas()["count"].tolist() == [12]
    saved = comparison.persist()
    artifact = saved.artifact
    saved.show(n=1)
    card = capsys.readouterr().out
    assert "to_pandas" in card and "compare(" not in card
    data = saved.to_pandas()
    axes = data.attrs["axes"]
    assert axes[0]["role"] == "calendar"
    assert data.attrs["comparisons"][0]["alias"] == "reference"
    assert len(data.attrs["frozen_coordinates"]) == 3
    artifact.show()
    assert "session.persisted_frame(" in capsys.readouterr().out
    assert not hasattr(artifact, "contract") and not hasattr(artifact, "execute")


def test_offset_shift_and_named_compare_help_examples_are_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import marivo.analysis as mv
    from marivo._help.render import render_help_text

    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("period-help", timezone="UTC")
    axis = session.time_scope(start="2026-01-01", end="2026-02-01").by(mv.grain("month"))
    frame = session.values(by=(), columns={"value": "CNY"}, rows=(((), {"value": 12}),))
    previous = session.values(by=(), columns={"value": "CNY"}, rows=(((), {"value": 8}),))
    text = render_help_text("analysis.offset")[0]
    assert [line for line in text.splitlines() if line.startswith("Signature:")] == [
        "Signature: mv.offset(*, years: 'int')",
        "Signature: mv.offset(*, periods: 'int')",
    ]
    assert "_Omitted" not in text and "<omitted>" not in text
    assert text == render_help_text(mv.offset)[0]
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
            assert render_help_text(axis.shift)[0] == render_help_text(mv.CalendarAxis.shift)[0]
            result.show()
            assert "Direct offset years=-1" in capsys.readouterr().out
        elif target == "analysis_frame.compare":
            assert "Example inputs: frame, previous" in page
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
    assert "ValueError:" in render_help_text("analysis.calendar_axis.shift")[0]
    with pytest.raises(ValueError, match="offset"):
        axis.shift(mv.ordinal())
    for name in ("period_offset", "previous_year"):
        assert not hasattr(mv, name)
    assert terminal_runs(session) == ()


def test_compare_help_discloses_accepted_exact_duration_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis as mv
    from marivo._help.render import render_help_text

    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("duration-help", timezone="UTC")
    current = session.values(
        by=(), columns={"elapsed": "s"}, rows=(((), {"elapsed": timedelta(microseconds=38)}),)
    )
    baseline = session.values(
        by=(), columns={"elapsed": "s"}, rows=(((), {"elapsed": timedelta(microseconds=19)}),)
    )
    text = render_help_text("analysis.analysis_frame.compare")[0]
    assert text == render_help_text(mv.AnalysisFrame.compare)[0]
    assert text == render_help_text(current.compare)[0]
    assert "numeric or matching-tick Duration" in text
    assert "Duration keeps ticks" in text
    assert "relative is dimensionless" in text
    result = current.compare({"reference": mv.baseline(baseline)}).compute()
    data = result.to_pandas()
    assert data["reference.baseline.elapsed"].tolist() == [timedelta(microseconds=19)]
    assert data["reference.absolute.elapsed"].tolist() == [timedelta(microseconds=19)]
    assert data["reference.relative.elapsed"].tolist() == [1.0]
    assert data.attrs["units"][-1] == ("reference.relative.elapsed", None)
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
    text = render_help_text("analysis.baseline")[0]
    assert text == render_help_text(mv.baseline)[0]
    assert "ComparisonBaseline" in text and "without data reads" in text
    assert "analysis.baseline" in render_help_text("analysis.inputs.comparison")[0]
    descriptor = prepare().by_canonical_id("baseline")
    assert isinstance(descriptor, CallableInput)
    namespace: dict[str, object] = {"mv": mv, "frame": frame}
    exec(descriptor.example.code, namespace)
    assert isinstance(namespace["result"], mv.ComparisonBaseline)
    assert namespace["result"].frame is frame
    type_help = render_help_text("analysis.comparison_baseline")[0]
    assert type_help == render_help_text(mv.ComparisonBaseline)[0]
    for field in ("frame", "on", "match"):
        field_help = render_help_text(f"analysis.comparison_baseline.{field}")[0]
        assert "Kind: attribute" in field_help and "read-only" in field_help
    assert "Does not show Frame rows" in render_help_text("analysis.comparison_baseline.show")[0]
    capsys.readouterr()
    main(["help", "analysis.baseline"])
    cli_text = capsys.readouterr().out
    assert "Target: analysis.baseline" in cli_text
    signature = next(line for line in text.splitlines() if line.startswith("Signature:"))
    assert signature in cli_text
    assert "analysis.comparison_baseline" in cli_text
