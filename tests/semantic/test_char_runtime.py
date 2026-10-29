"""Native fixed-width character metadata, validation and grouped observation."""

from collections.abc import Callable
from functools import partial
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.datasource as md
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import SourceData, source_case
from tests.shared_fixtures import catalog_value

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize(
    "backend,profile,port",
    [
        ("postgres", "table", None),
        ("mysql", "innodb-table", 23306),
        ("mysql", "innodb-table", 23307),
        ("trino", "non-iceberg", None),
    ],
    ids=["postgres", "mysql", "mariadb", "trino"],
)
def test_char_entity_preview_and_analysis(
    backend: str,
    profile: str,
    port: int | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    if port == 23307:
        import os

        if os.environ.get("MARIVO_MARIADB_ANALYSIS_TEST") != "1":
            pytest.skip("Enable MARIVO_MARIADB_ANALYSIS_TEST for MariaDB")
        from tests.datasource.environment import mysql_analysis as mysql

        monkeypatch.setattr(mysql, "connection", partial(mysql.connection, port=port))
        monkeypatch.setattr(mysql, "setup", partial(mysql.setup, port=port))
        monkeypatch.setattr(mysql, "PORT", port)
        monkeypatch.setenv("MARIVO_MYSQL_ANALYSIS_TEST", "1")
    data = SourceData(
        "id CHAR(8) NOT NULL, category CHAR(8), amount BIGINT, ignored CHAR(1)",
        "('one','A',1,'x'),('two','A ',2,'x'),('three','',4,'x'),('four',NULL,8,'x'),('five','猫',16,'x')",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        arguments = {
            **case.session.datasource.fields,
            **{key + "_env": value for key, value in case.session.datasource.env_refs.items()},
        }
        monkeypatch.setenv("MARIVO_CHAR_READER", str(arguments.pop("user")))
        arguments["user_env"] = "MARIVO_CHAR_READER"
        arguments_text = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
        source = f"md.table({case.source.table!r}, database={case.source.database!r})"
        semantic_project_factory(
            {
                "datasources/warehouse.py": f'import marivo.datasource as md\nmd.{backend}(name="warehouse", {arguments_text})\n',
                "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales",owner="Data",default=True)\n',
                "sales/model.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f'facts=ms.entity(name="facts",datasource=ms.ref.datasource("warehouse"),source={source},primary_key=["id"])\n'
                + 'category=ms.dimension_column(name="category",entity=facts,column="category")\n'
                + 'amount=ms.measure_column(name="amount",entity=facts,column="amount",additivity=ms.additive_all())\n'
                + f'@ms.entity(datasource=ms.ref.datasource("warehouse"),source={source},primary_key=["id"])\n'
                + 'def projected(rows):\n    return rows.select("id", "category", "amount")\n'
                + 'projected_category=ms.dimension_column(name="category",entity=projected,column="category")\n'
                + 'projected_amount=ms.measure_column(name="amount",entity=projected,column="amount",additivity=ms.additive_all())\n',
            }
        )
        session = mv.session.get_or_create("char-runtime", timezone="UTC")
        catalog = session.catalog
        entities = [catalog.get("entity:sales.facts"), catalog.get("entity:sales.projected")]
        report = catalog.validate(entities)
        assert report.status == "current", report.to_dict()
        # Physical metadata retains the width, while schema validation accepts string fields.
        from marivo.datasource.engines.base import MetadataInspectRequest

        bound = case.session.bind(case.source, source_identity="char-metadata")
        metadata = case.session.provider.metadata.inspect_table(
            MetadataInspectRequest(
                datasource=case.session.datasource.name,
                backend=case.session._backend,
                table=case.source.table,
                database=case.source.database,
                table_expr=bound.relation,
                include_partitions=False,
                datasource_ir=case.session.datasource,
            )
        )
        assert {c.name: c.type.lower() for c in metadata.columns}["category"] in {
            "char(8)",
            "character(8)",
        }
        # Read values are backend-native: PostgreSQL and Trino retain padding;
        # MySQL/MariaDB's default reader strips it. Both A spellings form one group.
        label = "A" if backend == "mysql" else "A       "
        empty = "" if backend == "mysql" else "        "
        cat = "猫" if backend == "mysql" else "猫       "
        for entity, path in zip(entities, ["facts", "projected"], strict=True):
            preview = catalog.preview(
                entity, scope=md.unpruned(max_rows=10, timeout_seconds=30), limit=10
            )
            assert {row["id"].rstrip(): row["category"] for row in preview.rows} == {
                "one": label,
                "two": label,
                "three": empty,
                "four": None,
                "five": cat,
            }
            category = catalog_value(catalog, f"sales.{path}.category", kind="field")
            amount = catalog_value(catalog, f"sales.{path}.amount", kind="field")
            rows = session.observe({"total": amount.sum()}, by=(category,)).compute().to_pandas()
            actual = {
                None if pd.isna(key) else key: value
                for key, value in rows.itertuples(index=False, name=None)
            }
            assert actual == {label: 3, empty: 4, None: 8, cat: 16}
