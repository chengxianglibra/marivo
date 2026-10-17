"""Current period Help and terminal disclosure have one executable entry shape."""

import pytest

import marivo
from marivo._help import help as help_coordinator
from tests.analysis.numeric.test_period_alignment import period
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.parametrize(
    "target, expected",
    (
        ("analysis.Query.compare", "absolute baseline"),
        ("analysis.Query.align", "complete"),
        ("analysis.ordinal", "complete"),
        ("analysis.period_offset", "calendar"),
        ("analysis.previous_year", "year"),
        ("analysis.Query.rollup", "original"),
        ("analysis.Artifact.to_pandas", "complete"),
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


def test_paired_query_contract_and_terminal_rows_disclose_current_protocol(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import marivo.analysis as mv

    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, 3)
    _, baseline = period(case.session, 7, 3)
    comparison = current.compare(baseline, match=mv.ordinal())
    comparison.contract().show()
    assert "execute" in capsys.readouterr().out
    selected = comparison.select({"value": comparison["value.absolute"]})
    defined = selected.where(~selected["value"].is_null())
    assert defined.reduce({"count": mv.row_count()}).execute().to_pandas()[
        ("value", "count")
    ].tolist() == [12]
    artifact = comparison.execute()
    artifact.show(n=1)
    card = capsys.readouterr().out
    assert "to_pandas" in card and "compare(" not in card
    axes = artifact.to_pandas().attrs["marivo"]["axes"]
    assert {projection["path"] for projection in axes[0]["projections"]} == {
        ("current",),
        ("baseline",),
    }
    assert not hasattr(artifact, "contract") and not hasattr(artifact, "execute")
