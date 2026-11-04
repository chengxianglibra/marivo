"""Scalar paths reuse identical prefixes without losing roles or missing matches."""

from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import sqlglot
from sqlglot import expressions as sge

import marivo.analysis as mv
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.analysis.temporal.test_calendar_native_keys import _maria
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import SourceTrace


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("mysql", "mariadb"),
            ("trino", "iceberg"),
            ("clickhouse", "mergetree"),
        ]
    ),
)
@pytest.mark.parametrize("order", ["short_first", "long_first", "fork", "other_role"])
def test_scalar_relationship_prefixes_keep_exact_matches(
    backend: str,
    profile: str,
    order: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
    source_trace: SourceTrace,
) -> None:
    _maria(profile, monkeypatch)
    records = {
        "facts": (
            "id BIGINT, tenant BIGINT, buyer BIGINT, seller BIGINT, amount BIGINT",
            [
                (1, 1, 1, 2, 10),
                (2, 1, 2, 1, 20),
                (3, 2, 1, 1, 30),
                (4, 1, None, 2, 40),
                (5, 1, 99, 2, 50),
                (6, 1, 3, 2, 60),
            ],
        ),
        "customers": (
            "id BIGINT, tenant BIGINT, region VARCHAR(10), country BIGINT, other BIGINT",
            [
                (1, 1, "North", 1, 2),
                (2, 1, "South", 2, 1),
                (1, 2, "West", 3, 1),
                (3, 1, None, 99, None),
            ],
        ),
        "countries": (
            "id BIGINT, name VARCHAR(10)",
            [(1, "A"), (2, "B"), (3, "C")],
        ),
    }
    tables: dict[str, str] = {}
    monkeypatch.chdir(tmp_path)
    with ExitStack() as stack:
        if backend == "duckdb":
            database = tmp_path / "paths.duckdb"
            with duckdb.connect(str(database)) as admin:
                for name, (columns, rows) in records.items():
                    admin.execute(f"CREATE TABLE {name} ({columns})")
                    admin.executemany(
                        f"INSERT INTO {name} VALUES ({','.join('?' for _ in rows[0])})", rows
                    )
                    tables[name] = f"md.table({name!r})"
            datasource = f"import marivo.datasource as md\nmd.duckdb(name='warehouse',path={str(database)!r},wall_clock_timezone='UTC')\n"
            customer_table = "customers"
        else:
            for name, (columns, rows) in records.items():
                names = [item.split()[0] for item in columns.split(",")]
                data = SourceData(
                    columns,
                    ",".join(
                        "("
                        + ",".join("NULL" if value is None else repr(value) for value in row)
                        + ")"
                        for row in rows
                    ),
                    ",".join(
                        item.strip()
                        .replace("BIGINT", "Nullable(Int64)")
                        .replace("VARCHAR(10)", "Nullable(String)")
                        for item in columns.split(",")
                    ),
                    [dict(zip(names, row, strict=True)) for row in rows],
                )
                case = stack.enter_context(
                    source_case(backend, profile, tmp_path, monkeypatch, data)
                )
                assert isinstance(case.source, TableSourceIR)
                tables[name] = f"md.table({case.source.table!r},database={case.source.database!r})"
                if name == "customers":
                    customer_table = case.source.table
            datasource = _datasource_model(backend, case, monkeypatch)
        model = "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        for name, (columns, _) in records.items():
            key = "['tenant','id']" if name == "customers" else "['id']"
            model += f"{name}=ms.entity(name={name!r},datasource=ms.ref.datasource('warehouse'),source={tables[name]},primary_key={key})\n"
            for item in columns.split(","):
                column = item.split()[0]
                constructor = "measure_column" if column == "amount" else "dimension_column"
                extra = ",additivity=ms.additive_all()" if column == "amount" else ""
                model += f"{name}_{column}=ms.{constructor}(name={column!r},entity={name},column={column!r}{extra})\n"
        model += """@ms.dimension(entity=customers)
def region_label(rows):
    return rows.region.fill_null('Unknown')
buyer=ms.relationship(name='buyer',from_entity=facts,to_entity=customers,keys=[ms.join_on(facts_tenant,customers_tenant),ms.join_on(facts_buyer,customers_id)])
seller=ms.relationship(name='seller',from_entity=facts,to_entity=customers,keys=[ms.join_on(facts_tenant,customers_tenant),ms.join_on(facts_seller,customers_id)])
home=ms.relationship(name='home',from_entity=customers,to_entity=countries,keys=[ms.join_on(customers_country,countries_id)])
other=ms.relationship(name='other',from_entity=customers,to_entity=countries,keys=[ms.join_on(customers_other,countries_id)])
"""
        semantic_project_factory(
            {
                "datasources/warehouse.py": datasource,
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Test',default=True)\n",
                "sales/model.py": model,
            }
        )
        session = mv.session.get_or_create("prefixes", timezone="UTC")

        def field(name: str):
            return catalog_value(session.catalog, "sales." + name, kind="field")

        buyer = session.catalog.get("relationship:sales.buyer")
        home = session.catalog.get("relationship:sales.home")
        short = field("customers.region_label").via((buyer,))
        long = field("countries.name").via((buyer, home))
        axes = (short, long)
        expected = [
            ("North", "A", 10),
            ("South", "B", 20),
            ("West", "C", 30),
            (None, None, 90),
            ("Unknown", None, 60),
        ]
        if order == "long_first":
            axes = (long, short)
            expected = [(right, left, total) for left, right, total in expected]
        elif order == "fork":
            other = session.catalog.get("relationship:sales.other")
            axes = (long, field("countries.name").via((buyer, other)))
            expected = [("A", "B", 10), ("B", "A", 20), ("C", "A", 30), (None, None, 150)]
        elif order == "other_role":
            seller = session.catalog.get("relationship:sales.seller")
            axes = (long, field("customers.region").via((seller,)))
            expected = [
                ("A", "South", 10),
                ("B", "North", 20),
                ("C", "West", 30),
                (None, "South", 150),
            ]
        frame = session.observe({"total": field("facts.amount").sum()}, by=axes).compute()
        actual = frame.to_pandas(
            axis_names={axis.token: f"axis{i}" for i, axis in enumerate(frame.axes)}
        )
        actual = actual.astype(object).where(pd.notna(actual), None)
        assert sorted(actual.itertuples(index=False, name=None), key=repr) == sorted(
            expected, key=repr
        )
        ast = sqlglot.parse_one(source_trace.native_sql[-1], read=backend)
        assert sum(item.name == customer_table for item in ast.find_all(sge.Table)) == (
            2 if order == "other_role" else 1
        )
