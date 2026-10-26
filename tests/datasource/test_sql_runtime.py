"""Public SQL submission ownership and native-driver witnesses."""

from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo import _execution_log
from marivo.datasource.adapters import CompiledRead, PhysicalRequirement, SourceSession
from marivo.datasource.ir import TableSourceIR
from marivo.datasource.secrets import LocalPlaintextCache
from marivo.semantic.reader import SemanticProject
from tests.datasource.driver_audit import native_audit
from tests.datasource.source_cases import ROWS, source_case, source_params
from tests.support.execution_logs import execution_records


def test_scoped_http_provider_sql_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _execution_log.scope(tmp_path), native_audit(monkeypatch, "duckdb") as audit:
        with source_case("duckdb", "http-json-auth", tmp_path, monkeypatch) as case:
            bound = case.session.bind(
                case.source, source_identity="sql_ownership.http_source", source_params=case.params
            )
            qualified = case.session.qualify(
                bound, PhysicalRequirement("sql_ownership.http_source", 1, frozenset({"scan"}))
            )
            read = case.session.compile(
                qualified,
                bound.relation,
                purpose="sql_ownership.http_source",
                expected_schema=bound.relation.schema().to_pyarrow(),
            )
            rows = len(case.session.execute(read))
            assert rows == len(ROWS)
            assert any(
                item.category == "provider" and item.purpose == "datasource.http_credentials"
                for item in audit.submissions
            )
            assert all("synthetic-source_profile" not in item.sql for item in audit.submissions)
            assert "synthetic-source_profile" not in str(execution_records(tmp_path))
            assert any(
                r.get("purpose") == "datasource.http_credentials"
                for r in execution_records(tmp_path)
            )
            environment = case.environment
        audit.save(
            "sql-native-duckdb-http-auth",
            environment,
            {"scoped_credentials": True, "credentials_absent_from_sql": True},
        )


@pytest.mark.parametrize(
    "backend", source_params(("duckdb", "postgres", "mysql", "trino", "clickhouse"))
)
def test_public_sql_owner_audit(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        LocalPlaintextCache,
        "default",
        classmethod(lambda cls: LocalPlaintextCache(tmp_path / "secrets.toml")),
    )
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    with native_audit(monkeypatch, backend) as audit:
        with source_case(backend, profile, tmp_path, monkeypatch) as case:
            assert isinstance(case.source, TableSourceIR)
            arguments = {
                **case.session.datasource.fields,
                **{key + "_env": value for key, value in case.session.datasource.env_refs.items()},
            }
            if "user" in arguments:
                monkeypatch.setenv("MARIVO_SQL_OWNER_READER", str(arguments.pop("user")))
                arguments["user_env"] = "MARIVO_SQL_OWNER_READER"
            semantic_project_factory(
                {
                    "datasources/warehouse.py": "import marivo.datasource as md\n"
                    + f"md.{backend}(name='warehouse',"
                    + ",".join(f"{key}={value!r}" for key, value in arguments.items())
                    + ")\n",
                    "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='R9',default=True)\n",
                    "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                    + f"ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['tenant','id','revision'])\n",
                }
            )
            reference = ms.ref.datasource("warehouse")
            connection = md.load().test(reference, timeout_seconds=5)
            if not connection.ok:
                connection.show()
            assert connection.ok
            inspection = md.inspect(
                reference, md.table(case.source.table, database=case.source.database)
            )
            assert {column.name for column in inspection.schema} == set(ROWS[0])
            governed: list[tuple[str, str]] = []
            source_execute = SourceSession.execute

            def observe(source: SourceSession, read: CompiledRead) -> pd.DataFrame:
                result = source_execute(source, read)
                governed.append((read.purpose, read.sql))
                return result

            monkeypatch.setattr(SourceSession, "execute", observe)
            session = mv.session.get_or_create("sql_ownership-sql-owner", timezone="UTC")
            if backend == "duckdb":
                facts = session.catalog.get("entity:sales.facts").value
                result = session.observe({"rows": facts.count()}).compute()
                assert result.to_pandas()["rows"].tolist() == [len(ROWS)]
            else:
                # Remote Query qualification belongs to later acceptance; retain
                # the SQL-owner witness through its governed datasource owner.
                bound = case.session.bind(case.source, source_identity="sql_ownership.facts")
                qualified = case.session.qualify(
                    bound, PhysicalRequirement("sql_ownership.facts", 1, frozenset({"scan"}))
                )
                read = case.session.compile(
                    qualified,
                    bound.relation,
                    purpose="sql_ownership.facts",
                    expected_schema=bound.relation.schema().to_pyarrow(),
                )
                assert len(case.session.execute(read)) == len(ROWS)
            assert session._runtime.store.resources(session._runtime.session_ref) == ()
            statement = "SELECT 7 AS marker"
            terminal = md.raw_sql(
                reference,
                statement,
                reason="R9.5 native terminal submission audit",
                timeout_seconds=5,
                project_root=tmp_path,
            )
            assert terminal.rows == ({"marker": 7},)
            assert terminal.sql == statement and terminal.returned_row_count == 1
            categories = {item.category for item in audit.submissions}
            assert {
                "provider",
                "raw_sql_terminal",
                "store",
                "ibis_metadata_preparation",
            } <= categories
            terminal_reads = [
                item for item in audit.submissions if item.category == "raw_sql_terminal"
            ]
            assert len(terminal_reads) == 1 and terminal_reads[0].sql == statement
            assert all(item.purpose for item in audit.submissions if item.category == "provider")
            logged = [r for r in execution_records(tmp_path) if r["event"] == "query.submitted"]
            assert governed
            assert Counter(governed) == Counter(
                (str(r["purpose"]), str(r["sql"]))
                for r in logged
                if str(r.get("purpose", "")).startswith(("analysis.", "query.", "sql_ownership."))
            )
            completed = [r for r in execution_records(tmp_path) if r["event"] == "query.completed"]
            assert {r["query_id"] for r in logged} == {r["query_id"] for r in completed}
            environment = {**case.environment, "profile": profile}
        audit.save(
            "sql-native-" + backend,
            environment,
            {
                "public_probe": True,
                "public_metadata": True,
                "governed_row_count": len(ROWS),
                "terminal_original_text": statement,
                "store_connection_identity": True,
                "resources_released": True,
                "business_methods_replayed": False,
            },
        )
