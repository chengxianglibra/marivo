"""Nullable float comparisons preserve zero denominators and outer missing sides."""

from decimal import Decimal

import pandas as pd
import pytest

from marivo.analysis.errors import AnalysisError
from marivo.analysis.methods.numeric_state import nullable_arithmetic
from marivo.semantic.errors import SemanticExpressionError
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.parametrize("decimal", (False, True))
def test_mixed_numeric_nullable_arithmetic_preserves_object_masks(decimal: bool) -> None:
    left = pd.Series([Decimal("10.0") if decimal else 10.0, 0.0, pd.NA], dtype=object)
    right = pd.Series([2.0, 0.0, 1.0], dtype=object)
    result = nullable_arithmetic("divide", left, right)
    assert result.iloc[0] == 5.0
    assert result.iloc[1] is pd.NA and result.iloc[2] is pd.NA
    assert result.dtype == object
    with pytest.raises(SemanticExpressionError):
        nullable_arithmetic("divide", left, right, zero_denominator="error")


def test_public_float_compare_keeps_absolute_and_outer_presence(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    category = session.catalog.fields.get("sales.order.channel")
    current = session.values(
        by=(category,),
        columns={"sales": "CNY"},
        rows=((("A",), {"sales": 10.0}), (("B",), {"sales": 0.0}), (("C",), {"sales": 5.0})),
    )
    baseline = session.values(
        by=(category,),
        columns={"sales": "CNY"},
        rows=((("A",), {"sales": 2.0}), (("B",), {"sales": 0.0}), (("D",), {"sales": 7.0})),
    )
    artifact = current.compare(baseline, how="outer").execute()
    frame = session.artifact(artifact.id).to_pandas()
    assert frame[("value", "sales.absolute")].iloc[:2].tolist() == [8.0, 0.0]
    assert frame[("value", "sales.relative")].iloc[0] == 4.0
    assert frame[("value", "sales.relative")].iloc[1:].isna().all()
    assert frame[("value", "sales.absolute")].iloc[2:].isna().all()
    assert frame[("presence", ("current",))].tolist() == [True, True, True, False]
    assert frame[("presence", ("baseline",))].tolist() == [True, True, False, True]
    before = session.graph().artifacts
    with pytest.raises(AnalysisError):
        current.compare(baseline, how="outer", zero_denominator="error").execute()
    assert session.graph().artifacts == before
