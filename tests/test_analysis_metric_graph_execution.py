from __future__ import annotations

import pandas as pd
import pytest

from marivo.analysis.intents._metric_evaluators import (
    MetricEvaluationError,
    RatioEvaluationV1,
    align_metric_children_v1,
    evaluate_linear_v1,
)


def test_ratio_e0_union_alignment_distinguishes_absent_null_and_zero() -> None:
    numerator = pd.DataFrame({"segment": ["a", "b", "c"], "value": [10.0, None, 4.0]})
    denominator = pd.DataFrame({"segment": ["a", "b", "d"], "value": [2.0, 0.0, 8.0]})

    result = RatioEvaluationV1().evaluate(
        numerator,
        denominator,
        zero_division="null",
    )

    assert result.frame["segment"].tolist() == ["a", "b", "c", "d"]
    assert result.frame.loc[0, "value"] == 5.0
    assert result.frame.loc[1:, "value"].isna().all()
    assert result.quality.roles["numerator"].absent_rows == 1
    assert result.quality.roles["numerator"].present_null_rows == 1
    assert result.quality.roles["denominator"].absent_rows == 1
    assert result.quality.roles["denominator"].present_zero_rows == 1
    assert result.quality.zero_division_rows == 1
    assert result.quality.affected_result_rows == 3


def test_ratio_error_policy_counts_only_present_zero() -> None:
    numerator = pd.DataFrame({"key": [1], "value": [1.0]})
    denominator = pd.DataFrame({"key": [1], "value": [0.0]})

    with pytest.raises(ZeroDivisionError, match="1 aligned row"):
        RatioEvaluationV1().evaluate(numerator, denominator, zero_division="error")


def test_linear_e0_keeps_asymmetric_key_with_null_result() -> None:
    left = pd.DataFrame({"key": [1, 2], "value": [10.0, 20.0]})
    right = pd.DataFrame({"key": [1, 3], "value": [1.0, 3.0]})

    result = evaluate_linear_v1((("term0", 1.0, left), ("term1", -1.0, right)))

    assert result.frame["key"].tolist() == [1, 2, 3]
    assert result.frame.loc[0, "value"] == 9.0
    assert result.frame.loc[1:, "value"].isna().all()
    assert result.quality.affected_result_rows == 2


def test_alignment_rejects_key_schema_order_mismatch() -> None:
    left = pd.DataFrame({"a": [1], "b": [2], "value": [3.0]})
    right = pd.DataFrame({"b": [2], "a": [1], "value": [4.0]})

    with pytest.raises(MetricEvaluationError, match="key schema"):
        align_metric_children_v1((("left", left), ("right", right)))


def test_scalar_alignment_requires_one_row_per_child() -> None:
    left = pd.DataFrame({"value": [1.0, 2.0]})
    right = pd.DataFrame({"value": [3.0]})

    with pytest.raises(MetricEvaluationError, match="exactly one row"):
        align_metric_children_v1((("left", left), ("right", right)))
