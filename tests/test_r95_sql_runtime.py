"""Small public SQL witnesses; existing R9 business evidence is not replayed."""

from collections.abc import Callable
from pathlib import Path

import ibis
import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.adapters import PhysicalRequirement
from marivo.datasource.ir import TableSourceIR
from marivo.datasource.secrets import LocalPlaintextCache
from marivo.semantic.reader import SemanticProject
from tests.r9_source_cases import ROWS, source_case
from tests.r95_driver_audit import native_audit

pytestmark = pytest.mark.runtime


def test_clickhouse_owned_control_sql_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.multisource_environment import clickhouse_analysis as ch

    with native_audit(monkeypatch, "clickhouse") as audit:
        with source_case("clickhouse", "mergetree", tmp_path, monkeypatch) as case:
            source = case.session
            source._prepare_interrupt()
            bound = source.bind(case.source, source_identity="r95.owned_control")
            expression = bound.relation
            for _ in range(18):
                expression = expression.cross_join(bound.relation.view()).select(expression)
            expression = expression.mutate(noise=ibis.random())
            qualified = source.qualify(
                bound,
                PhysicalRequirement("r95.owned_control", 1, frozenset({"scan", "join", "project"})),
            )
            read = source.compile(
                qualified,
                expression,
                purpose="r95.owned_control",
                expected_schema=expression.schema().to_pyarrow(),
            )
            stream = source.batches(read, chunk_size=1)
            try:
                assert next(iter(stream)).num_rows == 1
                assert source._clickhouse_active is not None
                identity = source._clickhouse_active[1]
                with ch.connection(admin=True) as observer:
                    active = observer.query(
                        "SELECT query FROM system.processes WHERE query_id={id:String}",
                        parameters={"id": identity},
                    ).result_rows
                assert len(active) == 1 and str(active[0][0]).startswith(read.sql)
                source._request_interrupt()
            finally:
                stream.close()
            assert len(source.cancel_submissions) == 1
            control = source.cancel_submissions[0]
            assert control.state == "succeeded"
            native = [
                item
                for item in audit.submissions
                if item.category == "provider"
                and item.owner == "clickhouse.analysis.cancel_owned_query"
            ]
            assert len(native) == 1
            assert native[0].boundary == "clickhouse_connect.query"
            assert (
                native[0].sql
                == control.sql
                == ("KILL QUERY WHERE query_id={id:String} AND user={user:String} SYNC")
            )
            assert native[0].purpose == "analysis.cancel_owned_query"
            assert native[0].parameter_names == ("id", "user")
            assert native[0].state == "succeeded"
            assert source._clickhouse_cancel_failure is None
            with ch.connection(admin=True) as observer:
                assert observer.query(
                    "SELECT count() FROM system.processes WHERE query_id={id:String}",
                    parameters={"id": identity},
                ).first_row == (0,)
            environment = case.environment
        assert source._cancel_control_released and source._cancel_pool is None
        audit.save(
            "sql-native-clickhouse-owned-control",
            environment,
            {
                "source_issued_query_observed_active": True,
                "native_control_statement": control.sql,
                "bound_parameter_names": ["id", "user"],
                "owned_query_absent_after_cancel": True,
                "reader_and_control_closed": True,
                "business_graph_replayed": False,
            },
        )


def test_scoped_http_provider_sql_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with native_audit(monkeypatch, "duckdb") as audit:
        with source_case("duckdb", "http-json-auth", tmp_path, monkeypatch) as case:
            bound = case.session.bind(
                case.source, source_identity="r95.http_source", source_params=case.params
            )
            qualified = case.session.qualify(
                bound, PhysicalRequirement("r95.http_source", 1, frozenset({"scan"}))
            )
            read = case.session.compile(
                qualified,
                bound.relation,
                purpose="r95.http_source",
                expected_schema=bound.relation.schema().to_pyarrow(),
            )
            stream = case.session.batches(read, chunk_size=32)
            try:
                rows = sum(batch.num_rows for batch in stream)
            finally:
                stream.close()
            assert rows == len(ROWS)
            assert any(
                item.category == "provider" and item.purpose == "datasource.http_credentials"
                for item in audit.submissions
            )
            assert all("synthetic-r92" not in item.sql for item in audit.submissions)
            environment = case.environment
        audit.save(
            "sql-native-duckdb-http-auth",
            environment,
            {"scoped_credentials": True, "credentials_absent_from_sql": True},
        )


@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
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
                monkeypatch.setenv("MARIVO_R95_READER", str(arguments.pop("user")))
                arguments["user_env"] = "MARIVO_R95_READER"
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
            connection = md.test(reference, timeout_seconds=5)
            if not connection.ok:
                connection.show()
            assert connection.ok
            inspection = md.inspect(
                reference, md.table(case.source.table, database=case.source.database)
            )
            assert {column.name for column in inspection.schema} == set(ROWS[0])
            session = mv.session.get_or_create("r95-sql-owner", report_timezone="UTC")
            result = session.members(ms.ref.entity("sales.facts")).execute()
            assert len(result.to_pandas()) == len(ROWS)
            assert session._runtime.store.resources(session._runtime.session_ref) == ()
            statement = "SELECT 7 AS marker"
            terminal = md.raw_sql(
                reference,
                statement,
                reason="R9.5 native terminal submission audit",
                limit=1,
                timeout_seconds=5,
                project_root=tmp_path,
            )
            assert terminal.rows == ({"marker": 7},)
            assert terminal.sql == statement and not terminal.is_truncated
            categories = {item.category for item in audit.submissions}
            assert {
                "provider",
                "governed_ibis",
                "raw_sql_terminal",
                "store",
                "ibis_metadata_preparation",
            } <= categories
            terminal_reads = [
                item for item in audit.submissions if item.category == "raw_sql_terminal"
            ]
            assert len(terminal_reads) == 1 and terminal_reads[0].sql == statement
            assert all(item.purpose for item in audit.submissions if item.category == "provider")
            environment = {**case.environment, "profile": profile}
        audit.save(
            "sql-native-" + backend,
            environment,
            {
                "public_probe": True,
                "public_metadata": True,
                "public_members": len(ROWS),
                "terminal_original_text": statement,
                "store_connection_identity": True,
                "resources_released": True,
                "business_methods_replayed": False,
            },
        )
