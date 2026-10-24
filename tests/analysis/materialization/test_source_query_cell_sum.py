"""Native cell sums retain missing masks and checks while accepting source precision."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.materialization.errors import MaterializationError
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import capture_source

CASES = [("duckdb", "table"), ("trino", "iceberg"), ("clickhouse", "mergetree")]


def _data(boundary: str = "ordinary") -> SourceData:
    amounts = [1e16, 1.0, -1e16, None, None, 0.0]
    decimals = [Decimal("0.10"), Decimal("0.20"), None, None, None, Decimal("0.00")]
    if boundary == "nan":
        amounts[0] = float("nan")
    elif boundary == "infinity":
        amounts[0] = float("inf")
    elif boundary == "float_overflow":
        amounts[:3] = [1e308, 1e308, 0.0]
    elif boundary == "decimal_overflow":
        decimals[:2] = [Decimal("5000000000000000.00")] * 2
    rows: list[dict[str, object]] = [
        {
            "id": index + 1,
            "tenant": f"t{index + 1}",
            "category": "A",
            "amount": amount,
            "big": decimal,
            "weight": 1,
            "day": date(2026, 7, 1),
        }
        for index, (amount, decimal) in enumerate(zip(amounts, decimals, strict=True))
    ]
    values = ",".join(
        f"({row['id']},'{row['tenant']}','A',"
        + (
            "NULL"
            if row["amount"] is None
            else "CAST('NaN' AS DOUBLE)"
            if isinstance(row["amount"], float) and pd.isna(row["amount"])
            else "CAST('Infinity' AS DOUBLE)"
            if row["amount"] == float("inf")
            else str(row["amount"])
        )
        + f",{row['big'] if row['big'] is not None else 'NULL'},1,DATE '2026-07-01')"
        for row in rows
    )
    return SourceData(
        "id BIGINT NOT NULL, tenant VARCHAR(16) NOT NULL, category VARCHAR(16), amount DOUBLE, big DECIMAL(18,2), weight BIGINT NOT NULL, day DATE",
        values,
        "id Int64, tenant String, category Nullable(String), amount Nullable(Float64), big Nullable(Decimal(18,2)), weight Int64, day Date",
        rows,
    )


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_native_cell_sum_keeps_each_na_mask_and_native_value(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, _data()) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.create("native-cell-sum", timezone="UTC")
        source = session.read(
            session.members(session.catalog.entities.get("sales.facts")),
            {
                "amount": session.catalog.fields.get("sales.facts.amount"),
                "decimal": session.catalog.fields.get("sales.facts.big"),
            },
        )
        table = case.session.bind(case.source, source_identity="cell-sum-oracle").relation
        native = case.session._backend.execute(table.aggregate(value=table.amount.sum()))
        assert isinstance(native, pd.DataFrame)
        # Compare the native sum against an independently submitted SQL value.
        direct_sql = (
            f"SELECT SUM(amount) FROM ({ibis.to_sql(table, dialect=backend)}) AS input_rows"
        )
        result = case.session._backend.raw_sql(direct_sql)
        direct = result.result_rows[0] if backend == "clickhouse" else result.fetchone()
        assert direct is not None
        assert native.value.iloc[0] == direct[0]
        source_trace = capture_source(monkeypatch)
        source = source.compute()
        trace = capture_source(monkeypatch)
        for selected, is_empty in (
            (source, False),
            (source.where(source["amount"].is_null()), False),
            (source.where(source["amount"] > float("1e100")), True),
        ):
            query = selected.reduce(
                {"total": selected["amount"].sum(), "decimal": selected["decimal"].sum()}
            )
            frame = query.compute().to_pandas()
            if selected is source:
                assert frame[("value", "total")].iloc[0] == 1.0
                assert frame[("value", "decimal")].iloc[0] == Decimal("0.30")
            else:
                for name in ("total", "decimal"):
                    value = frame[("value", name)].iloc[0]
                    assert value == 0 if is_empty else pd.isna(value)
        assert trace.returned_rows == []
        assert trace.uploaded_key_rows == []
        source_trace.save(
            f"query-cell-sum-{backend}",
            case.environment,
            {"rows": 6},
            {"operation": "native sum, all-NA and empty"},
            None,
            (case.session,),
        )


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("boundary", ("nan", "infinity", "float_overflow", "decimal_overflow"))
def test_local_cell_sum_retains_numeric_errors_before_top_zero(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
    boundary: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, _data(boundary)) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.create("checked-cell-sum", timezone="UTC")
        source = session.read(
            session.members(session.catalog.entities.get("sales.facts")),
            {
                "value": session.catalog.fields.get(
                    "sales.facts.big" if boundary == "decimal_overflow" else "sales.facts.amount"
                )
            },
        )
        total = source.reduce({"total": source["value"].sum()})
        query = total.top(0, by=total["total"])
        trace = capture_source(monkeypatch)
        if boundary == "decimal_overflow":
            assert total.compute().to_pandas()["value", "total"].tolist() == [
                Decimal("10000000000000000.00")
            ]
            assert query.compute().to_pandas().empty
        else:
            # This Frame statistic executes locally after its source read.
            # Its finite-state policy is independent of native source SUM.
            with pytest.raises(MaterializationError, match="finite current-row statistic"):
                query.compute()
        assert trace.uploaded_key_rows == []
