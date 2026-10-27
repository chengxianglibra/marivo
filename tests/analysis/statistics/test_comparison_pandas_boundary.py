"""Nullable float comparisons preserve zero denominators and outer missing sides."""

from decimal import Decimal

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.methods.numeric_state import nullable_arithmetic
from tests.shared_fixtures import DslCaseFactory, catalog_value, terminal_runs


@pytest.mark.parametrize("decimal", (False, True))
def test_mixed_numeric_nullable_arithmetic_preserves_object_masks(decimal: bool) -> None:
    left = pd.Series([Decimal("10.0") if decimal else 10.0, 0.0, pd.NA], dtype=object)
    right = pd.Series([2.0, 0.0, 1.0], dtype=object)
    if decimal:
        from marivo.semantic.errors import SemanticExpressionError

        # Native Python object arithmetic rejects Decimal/float mixing.
        with pytest.raises(SemanticExpressionError, match="unsupported operand"):
            nullable_arithmetic("divide", left, right)
        return
    result = nullable_arithmetic("divide", left, right)
    assert result.iloc[0] == 5.0
    assert result.iloc[1] is pd.NA and result.iloc[2] is pd.NA
    assert result.dtype == object


def test_public_float_compare_keeps_absolute_and_outer_presence(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    category = catalog_value(session.catalog, "sales.order.channel", kind="field")
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
    artifact = current.compare(
        {"reference": mv.baseline(baseline, on=((current.axes[0], baseline.axes[0]),))}
    ).persist()
    frame = session.persisted_frame(artifact.artifact_id).to_pandas()
    assert frame["reference.absolute.sales"].iloc[:2].tolist() == [8.0, 0.0]
    assert frame["reference.relative.sales"].iloc[0] == 4.0
    assert frame["reference.relative.sales"].iloc[1:].isna().all()
    assert frame["reference.absolute.sales"].iloc[2:].isna().all()
    assert frame["current.present"].tolist() == [True, True, True, False]
    assert frame["reference.present"].tolist() == [True, True, False, True]
    assert terminal_runs(session, status="succeeded")
