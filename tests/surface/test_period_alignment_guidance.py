"""Independent disclosure checks for aligned coordinates and native continuation guidance."""

import pytest

import marivo
import marivo.analysis as mv
import marivo.semantic as ms
from marivo._help import help as help_coordinator
from marivo.analysis.errors import AnalysisError
from tests.analysis.numeric.test_period_alignment import period
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.parametrize(
    "target, expected",
    (
        ("analysis.dsl.NumericComparison.compare", "1..N"),
        ("analysis.dsl.NumericComparison.ratio", "aligned"),
        ("analysis.dsl.one_to_one", "without a time grid"),
        ("analysis.dsl.NumericComparison.forecast", "original endpoint"),
        ("analysis.dsl.NumericComparison.runs", "original endpoint"),
        ("analysis.dsl.NumericComparison.correlate", "explicit lag_range"),
        ("analysis.ordinal", "1..N"),
        ("analysis.outer", "unobserved"),
    ),
)
def test_native_help_discloses_alignment_constraints(
    target: str, expected: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert marivo.help is help_coordinator
    help_coordinator(target)
    assert expected in capsys.readouterr().out


def test_aligned_contract_and_selected_rows_teach_original_endpoint_repair(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    _, current = period(members, 8, 3)
    _, baseline = period(members, 7, 3)
    comparison = current.compare(baseline, align=mv.exact(time=mv.ordinal()))
    for result in (comparison, comparison.execute()):
        result.contract().show()
        assert "aligned_time=ordinal keys 1..N" in capsys.readouterr().out
        for view, expected_count in ((result.absolute, 12), (result.relative, 0)):
            selected = view.where(view.value.is_defined())
            for receiver in (view, selected):
                receiver.contract().show()
                guidance = capsys.readouterr().out
                assert "original endpoints before comparison" in guidance
                calls = tuple(action.call for action in receiver.contract().actions)
                assert not any("forecast(" in call or "runs(" in call for call in calls)
                with pytest.raises(AnalysisError, match="aligned") as failure:
                    receiver.forecast(horizon=mv.periods(1))
                assert "before comparing" in str(failure.value)
            count = selected.aggregate(mv.count()).execute()
            assert count.to_pandas().value.tolist() == [expected_count]
