"""Native pending field Top returns bounded rows on each supported provider."""

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.core.frame_data import StoredFrameBacking
from marivo.analysis.materialization.errors import MaterializationError
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.json import Json, checked
from tests.support.source_trace import capture_source

CASES = [
    ("duckdb", "table"),
    ("postgres", "table"),
    ("mysql", "innodb-table"),
    ("trino", "iceberg"),
    ("clickhouse", "mergetree"),
]


def _mysql_materializes_attributes(plan: Json) -> bool:
    if isinstance(plan, list):
        return any(_mysql_materializes_attributes(item) for item in plan)
    if not isinstance(plan, dict):
        return False
    used = plan.get("used_columns")
    return (
        isinstance(used, list)
        and "__classify_value_0" in used
        and isinstance(plan.get("materialized_from_subquery"), dict)
    ) or any(_mysql_materializes_attributes(item) for item in plan.values())


def _data() -> SourceData:
    rows: list[dict[str, object]] = [
        {
            "id": identity,
            "tenant": tenant,
            "category": "x",
            "amount": 1,
            "big": score,
            "weight": 1,
            "day": date(2026, 7, 1),
        }
        for tenant, identity, score in (
            ("b", 1, 2**53 + 1),
            ("a", 2, None),
            ("a", 1, 2**53 + 1),
            ("b", 2, 2**53),
        )
    ]
    return SourceData(
        "id BIGINT, tenant VARCHAR(10), category VARCHAR(10), amount BIGINT, big BIGINT, weight BIGINT, day DATE",
        ",".join(
            f"({row['id']},'{row['tenant']}','x',1,"
            + ("NULL" if row["big"] is None else str(row["big"]))
            + ",1,DATE '2026-07-01')"
            for row in rows
        ),
        "id Int64, tenant String, category String, amount Int64, big Nullable(Int64), weight Int64, day Date",
        rows,
    )


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_native_read_top_retains_order_bounded_transport_and_fixed_boundary(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, _data()) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.create("native-read-top", timezone="UTC")
        members = session.members(session.catalog.entities.get("sales.facts"))
        score = session.catalog.fields.get("sales.facts.big")
        pending = session.read(members, {"score": score})
        raw = pending.compute().to_pandas()
        assert raw.iloc[:, 0].tolist() == [("a", 1), ("a", 2), ("b", 1), ("b", 2)]
        assert raw[("value", "score")].tolist() == [2**53 + 1, pd.NA, 2**53 + 1, 2**53]
        assert str(raw[("value", "score")].dtype) == "int64[pyarrow]"
        trace = capture_source(monkeypatch, measure_cost=True)
        for descending, expected in (
            (True, [("a", 1), ("b", 1), ("b", 2), ("a", 2)]),
            (False, [("b", 2), ("a", 1), ("b", 1), ("a", 2)]),
        ):
            result = pending.top(4, by=pending["score"], descending=descending).compute()
            rows = result.to_pandas()
            assert rows.iloc[:, 0].tolist() == expected
            assert pd.isna(rows[("value", "score")].iloc[-1])
            assert str(rows[("value", "score")].dtype) == "int64[pyarrow]"
        top = pending.top(1, by=pending["score"]).compute()
        assert top.to_pandas().iloc[0].tolist() == [("a", 1), 2**53 + 1]
        assert pending.top(0, by=pending["score"]).compute().to_pandas().empty
        assert trace.returned_rows == [4, 4, 1, 1]
        assert trace.uploaded_key_rows == []
        assert all("ORDER BY" in sql for sql in trace.native_sql)
        if backend == "mysql":
            from tests.datasource.environment import mysql_analysis as mysql

            # This unindexed fixture must keep the scalar attributes behind a
            # materialization boundary, rather than rescan them per identity.
            with mysql.connection() as connection, connection.cursor() as cursor:
                cursor.execute("EXPLAIN FORMAT=JSON " + trace.native_sql[2])
                plan = cursor.fetchone()
            assert plan is not None
            assert _mysql_materializes_attributes(checked(json.loads(plan[0])))
        trace.save(
            "frame-top-" + backend,
            case.environment,
            {"rows": 4, "top": [4, 4, 1, 0], "score": "nullable int64"},
            {
                "descending_keys": [["a", 1], ["b", 1], ["b", 2], ["a", 2]],
                "ascending_keys": [["b", 2], ["a", 1], ["b", 1], ["a", 2]],
                "returned_rows": [4, 4, 1, 1],
                "uploads": [],
            },
            top,
            (case.session,),
        )
        stored = top.persist()
        assert isinstance(stored._backing, StoredFrameBacking)
        reopened = session.persisted_frame(stored._backing.artifact_id)
        trace.returned_rows.clear()
        trace.native_sql.clear()
        for fixed in (top, stored, reopened):
            assert fixed.top(1, by=fixed["score"]).compute().to_pandas().iloc[0].tolist() == [
                ("a", 1),
                2**53 + 1,
            ]
            assert fixed.top(0, by=fixed["score"]).compute().to_pandas().empty
        assert trace.returned_rows == [] and trace.uploaded_key_rows == []


@pytest.mark.parametrize("backend,profile", source_params([("mysql", "innodb-table")]))
@pytest.mark.parametrize("n", (0, 1))
@pytest.mark.parametrize("invalid", ("duplicate", "null_identity"))
def test_mysql_read_top_preserves_full_input_key_errors(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
    n: int,
    invalid: str,
) -> None:
    source = _data()
    extra = (
        "(2,'a','x',1,1,1,DATE '2026-07-01')"
        if invalid == "duplicate"
        else "(0,NULL,'x',1,0,1,DATE '2026-07-01')"
    )
    monkeypatch.chdir(tmp_path)
    with source_case(
        backend, profile, tmp_path, monkeypatch, replace(source, values=source.values + "," + extra)
    ) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.create("mysql-read-top-integrity", timezone="UTC")
        members = session.members(session.catalog.entities.get("sales.facts"))
        pending = session.read(members, {"score": session.catalog.fields.get("sales.facts.big")})
        trace = capture_source(monkeypatch)
        with pytest.raises(MaterializationError) as failure:
            pending.top(n, by=pending["score"]).compute()
        assert failure.value.expected is not None
        if invalid == "duplicate":
            assert failure.value.stage == "stage_output"
            assert "unique stage key" in failure.value.expected
        else:
            assert "identity" in failure.value.expected
            assert "non-null" in failure.value.expected
        assert trace.returned_rows == [1] and trace.uploaded_key_rows == []
