"""Current period Help and terminal disclosure have one executable entry shape."""

import pytest

import marivo
from marivo._help import help as help_coordinator
from tests.analysis.numeric.test_period_alignment import period
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.parametrize(
    "target, expected",
    (
        ("analysis.analysis_frame.compare", "absolute baseline"),
        ("analysis.analysis_frame.align", "complete"),
        ("analysis.ordinal", "complete"),
        ("analysis.period_offset", "calendar"),
        ("analysis.previous_year", "year"),
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


def test_paired_frame_contract_and_terminal_rows_disclose_current_protocol(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import marivo.analysis as mv

    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, 3)
    _, baseline = period(case.session, 7, 3)
    comparison = current.compare(
        baseline, match=mv.ordinal(), on=tuple(zip(current.axes, baseline.axes, strict=True))
    )
    comparison.show()
    assert "pending" in capsys.readouterr().out
    selected = comparison.select({"value": comparison["absolute.value"]})
    defined = selected.where(~selected["value"].is_null())
    assert defined.reduce({"count": mv.row_count()}).compute().to_pandas()[
        ("value", "count")
    ].tolist() == [12]
    saved = comparison.persist()
    artifact = saved.artifact
    saved.show(n=1)
    card = capsys.readouterr().out
    assert "to_pandas" in card and "compare(" not in card
    data = saved.to_pandas()
    axes = data.attrs["axes"]
    assert axes[0]["role"] == "paired"
    assert {"left", "right", "mapping_name"} <= axes[0]["descriptor"].keys()
    assert len(data.attrs["frozen_coordinates"]) == 3
    artifact.show()
    assert "session.persisted_frame(" in capsys.readouterr().out
    assert not hasattr(artifact, "contract") and not hasattr(artifact, "execute")
