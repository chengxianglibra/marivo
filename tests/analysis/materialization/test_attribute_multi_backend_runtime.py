"""Real native Attribute reads retain exact values and original denominators."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from fractions import Fraction
from numbers import Integral
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo.analysis.core.attribute import AttributeInputError
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import capture_source

CASES = [
    ("postgres", "table"),
    ("mysql", "innodb-table"),
    ("trino", "iceberg"),
    ("clickhouse", "mergetree"),
]


def _columns(backend: str, amount_type: str) -> str:
    collation = " COLLATE utf8mb4_0900_bin" if backend == "mysql" else ""
    return (
        f"id BIGINT NOT NULL, tenant VARCHAR(10){collation} NOT NULL, "
        f"category VARCHAR(10){collation}, amount {amount_type}, "
        "big BIGINT, weight BIGINT, day DATE"
    )


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_native_attribute_exact_additive_joint_keys_and_read_audit(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    data = SourceData(
        _columns(backend, "DECIMAL(18,2)"),
        "(1,'east','A',1.25,9007199254740993,1,DATE '2026-07-01'),"
        "(2,'east','B',2.75,7,3,DATE '2026-07-01'),"
        "(1,'west','A',3.50,-2,1,DATE '2026-07-01'),"
        "(2,'west',NULL,0.50,3,1,DATE '2026-07-01')",
        "id Int64, tenant String, category Nullable(String), amount Decimal(18,2), big Int64, weight Int64, day Date",
        [
            {
                "id": identity,
                "tenant": tenant,
                "category": category,
                "amount": Decimal(amount),
                "big": big,
                "weight": weight,
                "day": date(2026, 7, 1),
            }
            for identity, tenant, category, amount, big, weight in (
                (1, "east", "A", "1.25", 2**53 + 1, 1),
                (2, "east", "B", "2.75", 7, 3),
                (1, "west", "A", "3.50", -2, 1),
                (2, "west", None, "0.50", 3, 1),
            )
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("native-attribute-exact", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        big = session.catalog.fields.get("sales.facts.big")
        tenant = session.catalog.fields.get("sales.facts.tenant")
        category = session.catalog.fields.get("sales.facts.category")
        parent = session.observe({"decimal": amount.sum(), "integer": big.sum()}).compute()
        trace = capture_source(monkeypatch)
        report = parent.attribute(by=(tenant, category)).compute()
        rows = report.to_pandas()
        keys = [
            (tenant, None if pd.isna(category) else category)
            for tenant, category in rows.iloc[:, :2].itertuples(index=False, name=None)
        ]
        # Repeated id and category values distinguish complete tuples from a
        # single-column identity or a Cartesian reconstruction of classifications.
        expected = {
            ("east", "A"): (Decimal("1.25"), 2**53 + 1),
            ("east", "B"): (Decimal("2.75"), 7),
            ("west", "A"): (Decimal("3.50"), -2),
            ("west", None): (Decimal("0.50"), 3),
        }
        assert len(keys) == 4 and set(keys) == set(expected)
        for name, index, total in (
            (
                "decimal",
                0,
                Decimal("8.00"),
            ),
            ("integer", 1, 2**53 + 9),
        ):
            contributions = [expected[key][index] for key in keys]
            values = rows[f"contribution.{name}"]
            assert values.tolist() == contributions
            assert rows[f"detail.value.{name}"].tolist() == contributions
            assert rows[f"total.{name}"].tolist() == [total] * 4
            assert rows[f"observed_total.{name}"].tolist() == [total] * 4
            assert all(isinstance(value, (Integral, Decimal)) for value in values)
            assert not pa.types.is_floating(pa.array(values).type)

        purposes = [
            submission.purpose for owner in trace.owners for submission in owner.submissions
        ]
        parent_reads = [purpose for purpose in purposes if "attribute-parent:" in purpose]
        assert parent_reads
        assert all(
            purpose.replace("attribute-parent:", "", 1) in purposes for purpose in parent_reads
        )
        assert trace.native_sql and trace.returned_rows
        submissions = tuple(trace.native_sql)
        saved = report.persist()
        facts = saved.artifact.evidence_digest().items
        role_reads = [
            dict(fact.values)
            for fact in facts
            if fact.kind == "read" and "attribute_read_role" in dict(fact.values)
        ]
        assert {fact["attribute_read_role"] for fact in role_reads} == {"parent", "fine"}
        assert all(fact["attribute_read_policy"] == "independent_reads/v1" for fact in role_reads)
        assert all(f"query.{fact['physical_stage']}" in purposes for fact in role_reads)
        closures = [
            dict(fact.values)
            for fact in facts
            if dict(fact.values).get("check") == "attribute_closure"
        ]
        assert {(fact["target"], fact["mode"], fact["parent_count"]) for fact in closures} == {
            ("decimal", "level", 1),
            ("integer", "level", 1),
        }
        assert all(
            fact["policy"] == "attribute_closure/v2" and fact["result"] == "passed"
            for fact in closures
        )
        assert tuple(trace.native_sql) == submissions


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_native_attribute_mean_and_ratio_use_complete_parent_denominators(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    data = SourceData(
        _columns(backend, "BIGINT"),
        "(1,'east','A',100,NULL,10,DATE '2026-07-01'),"
        "(2,'east','A',NULL,NULL,100,DATE '2026-07-01'),"
        "(3,'east','B',0,NULL,2,DATE '2026-07-01'),"
        "(4,'east','B',0,NULL,3,DATE '2026-07-01'),"
        "(5,'east','B',0,NULL,5,DATE '2026-07-01')",
        "id Int64, tenant String, category String, amount Nullable(Int64), big Nullable(Int64), weight Int64, day Date",
        [
            {
                "id": identity,
                "tenant": "east",
                "category": category,
                "amount": amount,
                "big": None,
                "weight": weight,
                "day": date(2026, 7, 1),
            }
            for identity, category, amount, weight in (
                (1, "A", 100, 10),
                (2, "A", None, 100),
                (3, "B", 0, 2),
                (4, "B", 0, 3),
                (5, "B", 0, 5),
            )
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("native-attribute-denominators", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        weight = session.catalog.fields.get("sales.facts.weight")
        category = session.catalog.fields.get("sales.facts.category")
        parent = session.observe(
            {"mean": amount.mean(), "ratio": amount.sum() / weight.sum()}
        ).compute()
        rows = parent.attribute(by=(category,)).compute().to_pandas()
        assert rows.iloc[:, 0].tolist() == ["A", "B"]
        # A has one valid value and B has three: the original mean is 25,
        # while averaging the two subgroup means would incorrectly return 50.
        assert rows["detail.value.mean"].tolist() == [100.0, 0.0]
        assert rows["component.value.denominator.mean"].tolist() == [1, 3]
        assert rows["contribution.mean"].tolist() == [25.0, 0.0]
        assert rows["total.mean"].tolist() == [25.0, 25.0]
        # The NULL amount still contributes its weight to the explicit ratio.
        assert rows["component.value.denominator.ratio"].tolist() == [110, 10]
        assert rows["detail.value.ratio"].tolist() == pytest.approx(
            [float(Fraction(100, 110)), 0.0]
        )
        assert rows["contribution.ratio"].tolist() == pytest.approx(
            [float(Fraction(100, 120)), 0.0]
        )
        assert rows["total.ratio"].tolist() == pytest.approx([float(Fraction(100, 120))] * 2)


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_native_attribute_distinguishes_known_empty_from_all_null(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    data = SourceData(
        _columns(backend, "BIGINT"),
        "(1,'east',NULL,NULL,NULL,1,DATE '2026-07-01')",
        "id Int64, tenant String, category Nullable(String), amount Nullable(Int64), big Nullable(Int64), weight Int64, day Date",
        [
            {
                "id": 1,
                "tenant": "east",
                "category": None,
                "amount": None,
                "big": None,
                "weight": 1,
                "day": date(2026, 7, 1),
            }
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("native-attribute-empty", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        weight = session.catalog.fields.get("sales.facts.weight")
        category = session.catalog.fields.get("sales.facts.category")
        empty_parent = session.observe({"q": amount.sum()}, where=weight > 1).compute()
        assert empty_parent.to_pandas()["q"].tolist() == [0]
        empty = empty_parent.attribute(by=(category,)).compute()
        assert empty.to_pandas().empty
        assert "contribution.q" in empty.output_names
        assert empty.method_facts

        unknown_parent = session.observe({"q": amount.sum()}).compute()
        assert unknown_parent.to_pandas()["q"].isna().all()
        with pytest.raises(AttributeInputError) as caught:
            unknown_parent.attribute(by=(category,)).compute()
        assert caught.value.reason == "undefined_input"
        assert caught.value.attribute_context is not None


@pytest.mark.parametrize("backend,profile", source_params([("mysql", "innodb-table")]))
def test_mysql_attribute_rejects_nondeterministic_collation_before_business_reads(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    data = SourceData(
        _columns(backend, "BIGINT").replace(
            "category VARCHAR(10) COLLATE utf8mb4_0900_bin",
            "category VARCHAR(10) COLLATE utf8mb4_0900_ai_ci",
        ),
        "(1,'east','A',100,NULL,1,DATE '2026-07-01')",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("mysql-attribute-collation", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        parent = session.observe({"q": amount.sum()}).compute()
        trace = capture_source(monkeypatch)
        with pytest.raises(AttributeInputError) as caught:
            parent.attribute(by=(category,)).compute()
        assert caught.value.reason == "unqualified_contribution_route"
        assert isinstance(caught.value.received, str)
        assert "collation" in caught.value.received
        # Native metadata qualification may run; no Attribute business query does.
        assert trace.native_sql == [] and trace.returned_rows == []
