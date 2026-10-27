"""Native arithmetic retains NULL validity, exact identity and durable carriers."""

import math
from decimal import Decimal, localcontext

import pyarrow as pa
import pytest

from marivo.analysis.materialization.errors import MaterializationError
from tests.shared_fixtures import DslCaseFactory, catalog_value


def test_local_integer_sum_uses_native_accumulator_and_survives_storage(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    frame = case.session.values(
        by=(catalog_value(case.catalog, "sales.customer.customer_id", kind="field"),),
        columns={"x": None},
        rows=((("A",), {"x": 2**63 - 1}), (("B",), {"x": 1})),
    ).compute()
    total = frame.reduce({"total": frame["x"].sum()}).persist()
    assert total.to_pandas()["total"].tolist() == [-(2**63)]
    assert pa.array(total.to_pandas()["total"]).type == pa.int64()
    assert case.session.persisted_frame(total.artifact_id).to_pandas()["total"].tolist() == [
        -(2**63)
    ]
    # The native Series add kernel itself checks overflow; no fallback is allowed.
    with pytest.raises(MaterializationError, match="overflow"):
        frame.select({"x": frame["x"] + 1}).compute()


def test_local_nonfinite_values_keep_validity_and_zero_division(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    frame = case.session.values(by=(), columns={"x": None}, rows=(((), {"x": 1e308}),)).compute()
    derived = frame.select({"inf": frame["x"] * 1e308, "missing": frame["x"] / 0}).compute()
    derived = derived.select(
        {
            "nan": derived["inf"] - derived["inf"],
            "inf": derived["inf"] + 1,
            "missing": derived["missing"],
        }
    ).persist()
    result = case.session.persisted_frame(derived.artifact_id).to_pandas()
    assert math.isinf(result["inf"].iloc[0])
    assert math.isnan(result["nan"].iloc[0])
    assert pa.array(result["nan"]).null_count == 0
    assert pa.array(result["missing"]).null_count == 1


def test_decimal_scalar_arithmetic_uses_native_context(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    value = Decimal("1.23456789")
    frame = case.session.values(by=(), columns={"x": None}, rows=(((), {"x": value}),)).compute()
    with localcontext() as context:
        context.prec = 6
        expected = value + 1
        result = frame.select({"x": frame["x"] + 1}).compute().to_pandas()
        assert result["x"].tolist() == [expected]
        assert context.prec == 6
