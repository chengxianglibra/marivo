"""Actual remote source values feed independent Frame endpoint and key operations."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.query_key_fusion import _outer_join_value
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import terminal_runs
from tests.support.source_trace import capture_source

CASES = [("trino", "iceberg"), ("clickhouse", "mergetree")]


def _data() -> SourceData:
    rows: list[dict[str, object]] = [
        {
            "id": index,
            "tenant": side,
            "category": category,
            "amount": value,
            "big": 1,
            "weight": 1,
            "day": date(2026, 7, 1),
        }
        for index, (side, category, value) in enumerate(
            (
                ("left", "A", None),
                ("right", "A", 0),
                ("left", "B", 5),
                ("right", "C", None),
                ("right", "", 1),
                ("left", None, 2),
                ("right", None, 3),
            ),
            1,
        )
    ]
    return SourceData(
        "id BIGINT NOT NULL, tenant VARCHAR(16) NOT NULL, category VARCHAR(16), amount BIGINT, big BIGINT, weight BIGINT, day DATE",
        ",".join(
            f"({row['id']},'{row['tenant']}',"
            + ("NULL" if row["category"] is None else f"'{row['category']}'")
            + ","
            + ("NULL" if row["amount"] is None else str(row["amount"]))
            + ",1,1,DATE '2026-07-01')"
            for row in rows
        ),
        "id Int64, tenant String, category Nullable(String), amount Nullable(Int64), big Int64, weight Int64, day Date",
        rows,
    )


def test_clickhouse_outer_input_declares_nullable_structure_even_for_literals() -> None:
    table = ibis.table({"key": ibis.dtype("string").copy(nullable=False)}, name="endpoint")
    sql = ibis.to_sql(
        table.select(
            key=_outer_join_value(table.key, "clickhouse"),
            present=_outer_join_value(ibis.literal(True), "clickhouse"),
        ),
        dialect="clickhouse",
    ).upper()
    assert "NULLABLE(STRING)" in sql
    assert "NULLABLE(BOOL)" in sql


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("operation", ("align", "compare"))
def test_remote_source_frames_keep_independent_endpoint_presence(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
    operation: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, _data()) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote-relations", timezone="UTC")
        category = session.catalog.fields.get("sales.facts.category")
        tenant = session.catalog.fields.get("sales.facts.tenant")
        amount = session.catalog.fields.get("sales.facts.amount")
        left = session.observe(
            {"v": amount.sum().where(tenant == "left")}, by=(category,)
        ).compute()
        right = session.observe(
            {"v": amount.sum().where(tenant == "right")}, by=(category,)
        ).compute()
        left_rows, right_rows = left.to_pandas(), right.to_pandas()
        on = ((left.axes[0], right.axes[0]),)
        query = (
            left.align(right, on=on)
            if operation == "align"
            else left.compare({"base": right}, on={"base": on})
        )
        trace = capture_source(monkeypatch)
        rows = query.compute().to_pandas()

        def keys(table: pd.DataFrame) -> list[str | None]:
            return [None if pd.isna(key) else key for key in table.iloc[:, 0]]

        left_keys, right_keys = keys(left_rows), keys(right_rows)
        assert keys(rows) == left_keys + [key for key in right_keys if key not in left_keys]
        assert set(keys(rows)) == {"A", "B", "C", "", None}
        current_presence = "left_present" if operation == "align" else "current.present"
        baseline_presence = "right_present" if operation == "align" else "base.present"
        current_value = "left.v" if operation == "align" else "v"
        baseline_value = "right.v" if operation == "align" else "base.baseline.v"
        assert rows["presence", current_presence].tolist() == [
            key in left_keys for key in keys(rows)
        ]
        assert rows["presence", baseline_presence].tolist() == [
            key in right_keys for key in keys(rows)
        ]
        for index, key in enumerate(keys(rows)):
            if key == "A":
                assert pd.isna(rows["value", current_value].iloc[index])
                assert rows["value", baseline_value].iloc[index] == 0
            elif key is None:
                assert rows["value", current_value].iloc[index] == 2
                assert rows["value", baseline_value].iloc[index] == 3
                if operation == "compare":
                    assert rows["value", "base.absolute.v"].iloc[index] == -1
        aligned = left.align(right, on=on)
        empty = aligned.top(0, by=aligned["left.v"]).compute()
        assert empty.to_pandas().empty
        assert empty.output_names == ("left.v", "right.v")
        assert empty.presence_names == ("left_present", "right_present")
        assert trace.native_sql == [] and trace.uploaded_key_rows == []
        assert session.observe({"n": amount.count()}).compute().to_pandas()[
            "value", "n"
        ].tolist() == [5]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_remote_shared_consumers_keep_separate_group_images_and_parent_checks(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, _data()) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote-shared-groups", timezone="UTC")
        category = session.catalog.fields.get("sales.facts.category")
        amount = session.catalog.fields.get("sales.facts.amount")
        producer = session.observe({"n": amount.count()}, by=(category,))
        chosen = producer.where(producer["n"] >= 1)
        selected = session.population_from(chosen, on=((chosen.axes[0], category),))
        condition = mv.in_population(selected)
        query = session.observe(
            {
                "a": amount.sum().where(condition).where(category == "A"),
                "c": amount.sum().where(~condition).where(category == "C"),
                "null_key": amount.sum().where(condition).where(category.isnull()),
            },
            by=(category,),
        )
        trace = capture_source(monkeypatch)
        rows = query.compute().to_pandas()
        assert len(rows) == 3
        for index, key in enumerate(rows.iloc[:, 0]):
            if pd.isna(key):
                assert rows["value", "null_key"].iloc[index] == 5
            elif key == "A":
                assert rows["value", "a"].iloc[index] == 0
            else:
                assert key == "C"
                value = rows["value", "c"].iloc[index]
                assert pd.isna(value)
        # Each consumer retains its source scope. Absent groups have sum zero;
        # present all-NULL groups retain their unknown value.
        missing = 0
        expected_values = {
            "A": (0, missing, 0),
            "C": (missing, None, missing),
            None: (missing, missing, 5),
        }
        actual_keys = [None if pd.isna(key) else key for key in rows.iloc[:, 0]]
        assert set(actual_keys) == {"A", "C", None}
        # Native handoff determines pandas NULL sentinels per provider; compare
        # the semantic NULL positions and exact values independent of carrier.
        for index, key in enumerate(actual_keys):
            for column, expected in zip(("a", "c", "null_key"), expected_values[key], strict=True):
                value = rows["value", column].iloc[index]
                if expected is None:
                    assert pd.isna(value)
                else:
                    assert not pd.isna(value) and value == expected, (
                        f"member={key!r}, column={column!r}, value={value!r}, expected={expected!r}"
                    )
        parent = session.observe({"n": amount.sum(nulls="error")}, by=(category,))
        empty_frame = parent.top(0, by=parent["n"])
        checked = session.population_from(empty_frame, on=((empty_frame.axes[0], category),))
        failed = session.observe(
            {
                "a": amount.count().where(mv.in_population(checked)),
                "b": amount.count().where(~mv.in_population(checked)),
            },
            by=(category,),
        )
        with pytest.raises(MaterializationError, match="non-NA"):
            failed.compute()
        assert trace.uploaded_key_rows


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_remote_multiple_shared_parameters_are_replaced_atomically(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, _data()) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote-parameter-block", timezone="UTC")
        category = session.catalog.fields.get("sales.facts.category")
        amount = session.catalog.fields.get("sales.facts.amount")
        producer = session.observe({"n": amount.count()}, by=(category,))
        first = producer.where(producer["n"] >= 1)
        second = producer.where(producer["n"] <= 1)
        left = session.population_from(first, on=((first.axes[0], category),))
        right = session.population_from(second, on=((second.axes[0], category),))
        query = session.observe(
            {"n": amount.count().where(mv.in_population(left) & ~mv.in_population(right))}
        )
        rows = query.compute().to_pandas()
        assert rows["value", "n"].tolist() == [2]
        assert terminal_runs(session) == ()
