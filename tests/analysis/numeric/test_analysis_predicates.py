"""Strict selection with independent row and consumption expectations."""

from decimal import Decimal

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import (
    DslCase,
    DslCaseFactory,
    analysis_dsl_rows,
    export_dsl_parquet_models,
)
from tests.support.paths import PROJECT_ROOT


def _amounts(case: DslCase) -> mv.AnalysisFrame:
    return case.session.observe(
        {"value": case.session.catalog.metrics.get("sales.revenue")},
        during=case.session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=case.session.members(ms.ref.entity("sales.customer")),
    )


def test_numeric_string_category_persists_and_exports(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(
            f'UPDATE "{case.names.customer}" SET region = ? WHERE "{case.names.customer_id}" = ?',
            ["123", "A"],
        )
    members = case.session.members(ms.ref.entity("sales.customer"))
    category = case.session.read(
        members, {"value": case.session.catalog.fields.get("sales.customer.region")}
    )
    selected = category.where(category["value"] == "123")
    saved = selected.persist()
    frame = saved.to_pandas()
    assert frame.iloc[:, 0].tolist() == ["A"]
    assert frame[("value", "value")].tolist() == ["123"]
    restored = case.session.persisted_frame(saved.artifact.id)
    assert restored.to_pandas().equals(frame)
    assert frame.loc[frame[("value", "value")] == "123"].equals(frame)
    assert selected.compute().to_pandas().equals(frame)


def test_public_multi_input_source_and_exported_pandas(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    revenue = session.catalog.metrics.get("sales.revenue")
    region = session.catalog.fields.get("sales.customer.region")
    buyer = session.catalog.relationships.get(f"sales.{case.names.buyer}")
    amount = session.observe(
        {"amount": revenue, "twice": revenue + revenue},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=members,
        where=region.via((buyer,)) == "east",
    )
    predicate = (amount["amount"] > 0) & (amount["amount"] < amount["twice"])
    facts = analysis_dsl_rows("j2")
    east = {member for member, region in facts.customers if region == "east"}
    totals: dict[str, float] = {}
    for _, member, _, _, occurred, value in facts.orders:
        if member is not None and str(occurred).startswith("2026-08"):
            totals[member] = totals.get(member, 0) + value
    expected = {member for member, total in totals.items() if total > 0 and member in east}
    assert set(amount.where(predicate).compute().to_pandas().iloc[:, 0]) == expected
    frame = amount.compute().to_pandas()
    matched = frame.loc[
        (frame[("value", "amount")] > 0) & (frame[("value", "amount")] < frame[("value", "twice")])
    ]
    assert set(matched.iloc[:, 0]) == expected


def test_nullable_selection_keeps_positive_values_and_offline_export(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import os
    import subprocess
    import sys

    import duckdb

    case = analysis_dsl_case_factory("j2")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace(
            'return amount_value.sum(time=time_value, empty="zero")',
            "return amount_value.sum(time=time_value)",
            1,
        )
    )
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(
            f'DELETE FROM "{case.names.order}" WHERE "{case.names.customer_id}" = ?', ["D"]
        )
    values = _amounts(case)
    original = values.persist()
    frame = original.to_pandas()
    assert frame[("value", "value")].isna().sum() == 1
    selected = values.where(~values["value"].is_null())
    positive = selected.where(selected["value"] > 0).persist()
    assert positive.to_pandas().iloc[:, 0].tolist() == ["A", "B"]
    assert positive.to_pandas()[("value", "value")].tolist() == [60, 120]
    assert values.where(values["value"] > 0).compute().to_pandas().equals(positive.to_pandas())
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    script = """
import sys
import duckdb
import marivo.analysis as mv
import marivo.semantic as ms
def unavailable(*args, **kwargs):
    raise AssertionError("terminal selection cannot reopen sources or Semantic")
duckdb.connect = unavailable
ms.load = unavailable
session = mv.session.resume(sys.argv[1], by="id")
values, saved = [session.persisted_frame(ref) for ref in sys.argv[2:]]
frame = values.to_pandas()
selected = frame.loc[frame[("value", "value")] > 0].reset_index(drop=True)
assert selected.equals(saved.to_pandas())
assert len(session.runs().items) == 2
"""
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                case.session.id,
                original.artifact.id,
                positive.artifact.id,
            ],
            cwd=case.root,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        offline.rename(case.database_path)
    assert completed.returncode == 0, completed.stderr


def test_public_decimal_field_comparison(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(
            f'ALTER TABLE "{case.names.order}" ALTER "{case.names.amount}" TYPE DECIMAL(18,2)'
        )
    values = _amounts(case)
    facts = analysis_dsl_rows("j2")
    totals: dict[str, float] = {}
    for _, member, _, _, occurred, amount in facts.orders:
        if member is not None and str(occurred).startswith("2026-08"):
            totals[member] = totals.get(member, 0) + amount
    matched = values.where(values["value"] == values["value"]).compute()
    assert set(matched.to_pandas().iloc[:, 0]) == set(totals)
    threshold = Decimal("0.00")
    saved = values.where(values["value"] > threshold).persist()
    frame = saved.to_pandas()
    assert set(frame.iloc[:, 0]) == {member for member, total in totals.items() if total > 0}
    assert all(isinstance(value, Decimal) for value in frame[("value", "value")])
    restored = case.session.persisted_frame(saved.artifact.id)
    assert restored.to_pandas().equals(frame)
    assert frame.loc[frame[("value", "value")] > threshold].equals(frame)


def test_public_duration_field_comparison(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    export_dsl_parquet_models(case, case.root)
    path = case.root / "source_files/order.parquet"
    table = pq.read_table(path)
    pq.write_table(
        table.set_column(
            table.schema.get_field_index("amount"),
            "amount",
            table["amount"].cast(pa.duration("ns")),
        ),
        path,
    )
    values = _amounts(case)
    expected = {
        member
        for _, member, _, _, occurred, _ in analysis_dsl_rows("j2").orders
        if member is not None and str(occurred).startswith("2026-08")
    }
    result = values.where(values["value"] == values["value"]).persist()
    frame = result.to_pandas()
    assert set(frame.iloc[:, 0]) == expected
    import pandas as pd

    dtype = frame[("value", "value")].dtype
    assert isinstance(dtype, pd.ArrowDtype) and dtype.pyarrow_dtype == pa.duration("ns")
    assert case.session.persisted_frame(result.artifact.id).to_pandas().equals(frame)
    runs_before = case.session.runs().items
    business_calls: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> None:
        business_calls.append("compile_or_execute")
        raise AssertionError("incompatible Duration comparison must fail during preparation")

    monkeypatch.setattr(SourceSession, "compile", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    with pytest.raises(CoreRuleError) as caught:
        result.where(result["value"] > 0).compute()
    error = caught.value
    assert error.expected == "matching value kinds"
    assert error.received == "duration, numeric"
    assert error.location == "Column"
    assert error.repair is not None
    assert error.repair.kind == "retry"
    assert error.repair.action == "Compare compatible values."
    assert error.run_ref is None
    assert case.session.runs().items == runs_before
    assert business_calls == []
    assert case.session.persisted_frame(result.artifact.id).to_pandas().equals(frame)


def test_foreign_predicate_inputs_reject_before_execution(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    values = case.session.read(
        members, {"value": case.session.catalog.fields.get("sales.customer.region")}
    )
    fixed = values.compute()
    other_session = mv.session.get_or_create("foreign-r63", report_timezone="UTC")
    other = other_session.read(
        other_session.members(ms.ref.entity("sales.customer")),
        {"value": other_session.catalog.fields.get("sales.customer.region")},
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("construction cannot start a Run")

    from marivo.datasource.adapters import SourceSession

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    with pytest.raises(AnalysisError, match="exact Store/Session/Frame input"):
        values.where(other["value"] == "east")
    with pytest.raises(AnalysisError, match="exact Store/Session/Frame input"):
        values.where(fixed["value"] == "east")
    with pytest.raises(AnalysisError):
        case.session.observe({"bad": fixed})  # type: ignore[dict-item]  # Fixed data is not a source Metric.
    with pytest.raises(AnalysisError, match="a Population from this Store and Session"):
        case.session.frame(other_session.members(ms.ref.entity("sales.customer")))


def test_closed_policy_and_predicate_inputs(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    query = _amounts(case)
    # Ordinary numeric thresholds no longer carry the retired positive-count
    # policy: zero, negative values and exact large integers are valid.
    for threshold, expected in ((0, 4), (-1, 4), (2**63, 0)):
        selected = query.where(query["value"] >= threshold).compute().to_pandas()
        assert len(selected) == expected
    for value in (float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(AnalysisError):
            _ = query["value"] >= value
    with pytest.raises(TypeError):
        mv.Column()  # type: ignore[call-arg]  # Deliberately bypass the required Query acquisition path.
    with pytest.raises(AnalysisError):
        query.where(True)  # type: ignore[arg-type]  # Deliberately invalid predicate type.
    for name in ("all_instances", "at_least", "SubjectBinding", "all_of"):
        assert not hasattr(mv, name)
