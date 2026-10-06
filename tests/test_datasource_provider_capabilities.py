"""Contract tests for the provider capability channel and statement registry."""

from __future__ import annotations

from typing import Any

import pytest

from marivo.datasource import adapters
from marivo.datasource.capabilities import (
    ProviderStatement,
    execute_provider_statement,
    json_http_headers,
    provider_statement,
    provider_statement_catalog,
    provider_statement_log,
    register_provider_statements,
    render_provider_statement,
    url_is_in_http_scope,
)
from marivo.datasource.engines import ENGINE_PROFILES
from marivo.datasource.engines.base import EngineProfile, MetadataInspectRequest
from marivo.datasource.errors import DatasourceSourceCapabilityError
from marivo.datasource.metadata import TableMetadata


class _RecordingBackend:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self.queries: list[str] = []
        self.rows = rows or []
        self.closed = 0
        self._marivo_certified_authoring = False

    def raw_sql(self, sql: str) -> Any:
        self.queries.append(sql)
        return _RecordingCursor(self.rows)


class _RecordingCursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.description = [("value", "int")]
        self._rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows

    def close(self) -> None:
        return None


class _FailingBackend:
    def raw_sql(self, sql: str) -> Any:
        raise RuntimeError("connectivity lost")


@pytest.fixture(scope="module", autouse=True)
def loaded_providers() -> None:
    for backend in ENGINE_PROFILES:
        assert ENGINE_PROFILES[backend].name == backend


def test_registered_statements_are_pinned_by_snapshot() -> None:
    """The channel SQL text is frozen; edits must update this snapshot deliberately."""
    import hashlib

    # Load every engine module so each provider's statements are registered.
    assert all(name in ENGINE_PROFILES for name in ENGINE_PROFILES)

    # Every registered provider statement must appear here verbatim. Add the
    # (provider, statement_id) -> sha256(template) pair when registering one.
    pinned: dict[tuple[str, str], str] = {
        (
            "clickhouse",
            "clickhouse.analysis.cancel_owned_query",
        ): "8407fff8e636b6bf444a67ebf40ba37a02277f63c7d8f53239d65815cd7e9d9b",
        (
            "mysql",
            "mysql.analysis.cancel_owned_query",
        ): "02548904a68f6735059c88cdd252b225475ee367d812cbaa460d05653bc4f7bc",
        (
            "mysql",
            "mysql.authoring.install_select_deadline",
        ): "52da100c9a351a428ffae1adb7616ae4fca2ca3bd6de25dc7a2c1118f788f090",
        (
            "mysql",
            "mysql.authoring.read_select_deadline",
        ): "9abc2d9f258053bc5bca1f82f73cbada2fb81fc577fb2f4af2dc07f376a8fd6c",
        (
            "clickhouse",
            "clickhouse.columns.fallback",
        ): "709ba652e01cc50d9b33c7b2fdb18b76d9dc98677da78aa35c26beda0a5af2d2",
        (
            "clickhouse",
            "clickhouse.columns.full",
        ): "b4e949ba4a69aa4a1f4f2c51055a4c8b61d4b4131acc7d98cc2353fb918111e8",
        (
            "clickhouse",
            "clickhouse.parts.profile",
        ): "e5aab4e0725f4b78e7eb71fba1fe6c590488382f55035b0e04f81711c796b00d",
        (
            "clickhouse",
            "clickhouse.parts_columns.active",
        ): "e74cf9b89ff3dd0a70aac498607e43f3f8420cd9989281413aca3261d5217c62",
        (
            "clickhouse",
            "clickhouse.tables.comment",
        ): "9877df57bc1fbe4e2f8fbfd7fee6c7ccfae0097b52f34e67c13bc1372c19505f",
        (
            "clickhouse",
            "clickhouse.tables.create_query",
        ): "b78e969a0d64619fa25f9e76b924aa0736cd947db317a9c8a3a409dce5728dd0",
        (
            "clickhouse",
            "clickhouse.tables.full",
        ): "794067abc2b33a294ccfbd5a6353b708b606532ef21fa4081632866fd8cb7a87",
        (
            "clickhouse",
            "clickhouse.tables.local_partition_key",
        ): "13bfcb87e33ae3e3b96954dd3dbc846a554a6d5789040bcaf86aca4d0c9eacc1",
        (
            "duckdb",
            "duckdb.constraints",
        ): "b3c719890b8aedcf09a746953e758fc288df2c0446d1364fadc0afd88452a36c",
        (
            "duckdb",
            "duckdb.http_secret_bearer",
        ): "fd508a63cb457ea45d964ff9db5bf9958c70a9ae9639b376616972f5f7447f7d",
        (
            "duckdb",
            "duckdb.http_secret_headers",
        ): "b1f8b0fbc9dcafd9a51b3876bd3409d122c7e78f54b9e3c121c856db281c7f0e",
        (
            "duckdb",
            "duckdb.namespace.current",
        ): "a4c4ac1c8ed42516de7b86109f2dfe3cbf7aadbc008851126b988472d2565952",
        (
            "duckdb",
            "duckdb.tables.columns",
        ): "514968e568aa525319ce845f27fefffc10e058ee75d1952afeba7d9821fb2556",
        (
            "duckdb",
            "duckdb.tables.comment",
        ): "79b7013cbb781b43ee9ee3575d0403edb53afdb5a6f720297ad787649695ab10",
        (
            "duckdb",
            "duckdb.tables.comment_size",
        ): "6ace28909262bc84b6d3506cd785579386c90bce78da7bdf0021fc21bfef6801",
        (
            "duckdb",
            "duckdb.views.database_qualified",
        ): "32cd1c9a2426b7ce2d7652a0611527958f1e8151e8ba039f4f5091d81a7d9e25",
        (
            "duckdb",
            "duckdb.views.schema_qualified",
        ): "9caeecc09a5e820921a3a5f205cb74323e9d4310a572c8900482f58cf57f7a3a",
        (
            "mysql",
            "mysql.columns.show",
        ): "3a76e6a0680dff91eeff9d76183b8dbd2dfe69e6110fb2e102aef91a79a63d02",
        (
            "mysql",
            "mysql.indexes.primary",
        ): "da11ca0ddd4c745bcda25c28d924fad4e29afe64cb95612ced6c55562739940d",
        (
            "mysql",
            "mysql.partitions",
        ): "31145d6f5be99a01ce1da201c7fede2010fa929e36f4fb9d36298a3aa8b01d88",
        (
            "mysql",
            "mysql.partitions_schema",
        ): "e4a6809bbad2846ea2fde8526d5ea7f0ba2a7acdddefb96bb74878075d9c87f4",
        (
            "mysql",
            "mysql.tables.comment",
        ): "427c866e5ddfd460d887502114c64ea9bc195cfd9d677fb162acd56d88a9e614",
        (
            "mysql",
            "mysql.tables.comment_schema",
        ): "c73404974f6cf66f7c0ecfb28d26227ca11c6a65ffdf1793af7b0a2ec58ccbec",
        (
            "mysql",
            "mysql.tables.type",
        ): "2be0ddb94cc4ba9bc8b60c48dbfd3b0186a8d744d74ccb98a3f529fc17d8f30c",
        (
            "mysql",
            "mysql.tables.type_schema",
        ): "cd10d82f99f2d0a8447c1c5a58f1843c7f2ddafff1d28ede812aedb3c6a6af4e",
        (
            "mysql",
            "mysql.views.definition",
        ): "9a7445e45bcbc934aca0d1d0b8ac9cf82ca8168acab95076ea50038f5b2f2441",
        (
            "mysql",
            "mysql.views.definition_schema",
        ): "6b75885449628a723cfe4e55ab0aa79b1ed3103ff0bd29723238e45276e62b89",
        (
            "postgres",
            "postgres.columns",
        ): "b46809c22735331fac831c188a7bfbcaac045d1dca5b990fd0583df78b2cea41",
        (
            "postgres",
            "postgres.comment.columns",
        ): "01cb342262878bffa099722fbcd42f6f70aad18816639b56932d6be8b303dbba",
        (
            "postgres",
            "postgres.comment.table",
        ): "0fb55a21ee6179110212e635d7308f08ff81878248c21f678802b20e309e7e9f",
        (
            "postgres",
            "postgres.constraints",
        ): "5716da4614b72abcf0907079598b70865c8726d6a079df631bf02a1f929044ca",
        (
            "postgres",
            "postgres.partition.key",
        ): "6165beff79425c6b55acb72dd8ac67437bb5851b8ceca646ba0a6b3abb946f5b",
        (
            "postgres",
            "postgres.profile.physical",
        ): "73b4d0a8df2e9dda12710b0ac20a528d9580c8ce02e1a95963ffa4929610460e",
        (
            "postgres",
            "postgres.tables.kind",
        ): "6a5af7a08b1cd022e3bdcbf5c5ea9003856fb3f5c6ba10039f6c9dab7fff6d27",
        (
            "sqlite",
            "sqlite.pragma.index_info",
        ): "3b25054b5a4a4f89898806b4b020ae56b2c4e56ef04f342a67684dacbc74c05a",
        (
            "sqlite",
            "sqlite.pragma.index_list",
        ): "d3039ed95522bc4c649226792c8aff4deceab6039ef7d03cade6560f60e0eb4d",
        (
            "sqlite",
            "sqlite.pragma.table_info",
        ): "237a2faf8e4bebb8b55c0848614c2245fd2f0f63deb8b5286e95e6004260c688",
        (
            "sqlite",
            "sqlite.schema.kind",
        ): "1ea7edcf28dc690b6068d106c60ec5ef7fed215ee7078b2486114ed7c6bcb337",
        (
            "trino",
            "trino.columns",
        ): "56b9066cd5eac76477c018228712611fe546ce4e4f87e8c519992f737e088959",
        (
            "trino",
            "trino.constraints",
        ): "7623737c191544843de582fdc57aeb7898cd44d4d70c0cbe1bcf15e1355703a1",
        (
            "trino",
            "trino.show_columns",
        ): "937612f4874b9d5e60adfa307d5897032a4f6a108a4398d65b4c6875f645b94e",
        (
            "trino",
            "trino.show_create",
        ): "1cc53fe114df6a9b665f7f979aa76a6fe51c15ec38a9964b154d40033c2ec993",
        (
            "trino",
            "trino.show_stats",
        ): "d4e79ef93a3af896fc2ed7f6e0512ec90a4e68a7fbd2638b1c173b5d140561f4",
        (
            "trino",
            "trino.tables.type",
        ): "6c30c26bcfcaa4e53f0467c8b7e6376167d6a6ca97ac800386d1ec1bdb158d1d",
        (
            "trino",
            "trino.views.definition",
        ): "35e034663ab1deb5121af91dd3f55eae847d78830513fd2e5756ee8549a25268",
    }
    observed = {
        (provider, statement_id): hashlib.sha256(statement.template.encode("utf-8")).hexdigest()
        for provider, owned in provider_statement_catalog().items()
        # The local "probe" provider registers throwaway test-only statements.
        if provider != "probe"
        for statement_id, statement in sorted(owned.items())
    }
    assert observed == pinned


def test_register_rejects_duplicate_and_mismatched_ids() -> None:
    statement = ProviderStatement(
        statement_id="probe.demo_select",
        template="SELECT {column}",
        literal_slots=frozenset({"column"}),
    )
    register_provider_statements("probe", {"demo_select": statement})
    with pytest.raises(ValueError, match="duplicate provider statement"):
        register_provider_statements("probe", {"demo_select": statement})
    with pytest.raises(ValueError, match="declares id"):
        register_provider_statements(
            "probe",
            {"mismatched": ProviderStatement(statement_id="probe.other", template="SELECT 1")},
        )


def test_statement_without_slot_in_template_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing the 'column' slot"):
        ProviderStatement(
            statement_id="probe.no_slot",
            template="SELECT 1",
            literal_slots=frozenset({"column"}),
        )


def test_provider_statement_rejects_unregistered_ids() -> None:
    with pytest.raises(DatasourceSourceCapabilityError, match="closed to unregistered SQL"):
        provider_statement("duckdb", "duckdb.never_registered")


def test_every_production_statement_has_exact_authorized_purposes() -> None:
    for backend in ENGINE_PROFILES:
        for statement in provider_statement_catalog()[backend].values():
            if statement.statement_id == "mysql.analysis.cancel_owned_query":
                expected = {"analysis.cancel_owned_query", "datasource.authoring.deadline"}
            elif statement.statement_id == "clickhouse.analysis.cancel_owned_query":
                expected = {"analysis.cancel_owned_query"}
            elif statement.statement_id.startswith("mysql.authoring."):
                expected = {"semantic.certified_preview.deadline"}
            elif statement.statement_id.startswith("duckdb.http_secret_"):
                expected = {"datasource.http_credentials"}
            else:
                expected = {f"datasource.metadata.{backend}"}
            assert statement.allowed_purposes == frozenset(expected)
            recorder = _RecordingBackend()
            with pytest.raises(DatasourceSourceCapabilityError, match="purpose"):
                execute_provider_statement(
                    recorder,
                    ENGINE_PROFILES[backend],
                    statement.statement_id,
                    purpose="analysis.business_read",
                )
            assert recorder.queries == []
            assert provider_statement_log(recorder) == ()


def test_empty_purpose_registration_grants_no_submission_authority() -> None:
    statement = ProviderStatement(statement_id="probe.no_purpose", template="SELECT 1")
    register_provider_statements("probe", {"no_purpose": statement})
    recorder = _RecordingBackend()
    with pytest.raises(DatasourceSourceCapabilityError, match="purpose"):
        execute_provider_statement(
            recorder, _probe_profile(), statement.statement_id, purpose="test.channel"
        )
    assert recorder.queries == []


def test_render_quotes_literals_and_identifiers_strictly() -> None:
    profile = adapters.provider_for("duckdb")
    statement = ProviderStatement(
        statement_id="probe.render_check",
        template="SELECT {value} FROM {table} WHERE {scope}",
        literal_slots=frozenset({"value", "scope"}),
        identifier_slots=frozenset({"table"}),
    )
    sql = render_provider_statement(
        statement,
        profile,
        values={"value": "o'brien", "scope": "it's"},
        identifiers={"table": ("main", 'order"line')},
    )
    assert sql == "SELECT 'o''brien' FROM \"main\".\"order\"\"line\" WHERE 'it''s'"


def test_render_rejects_slot_mismatch() -> None:
    profile = adapters.provider_for("duckdb")
    statement = ProviderStatement(
        statement_id="probe.slot_check",
        template="SELECT {value}",
        literal_slots=frozenset({"value"}),
    )
    with pytest.raises(ValueError, match="unexpected=\\['extra'\\]"):
        render_provider_statement(statement, profile, values={"value": "1", "extra": "2"})
    with pytest.raises(ValueError, match="missing=\\['value'\\]"):
        render_provider_statement(statement, profile, values={})


def _probe_profile() -> EngineProfile:
    from marivo.datasource.engines.base import (
        AuthoringCapabilities,
        EngineMetadataIntrospection,
        EngineProfile,
        identity_read_only_kwargs,
        identity_str,
    )

    def inspect_table(request: MetadataInspectRequest) -> TableMetadata:
        raise AssertionError("Probe statements do not inspect table metadata")

    return EngineProfile(
        name="probe",
        aliases=(),
        authoring_func="",
        required_modules=(),
        connect=lambda name, kwargs: None,
        apply_read_only_kwargs=identity_read_only_kwargs,
        identifier_quote='"',
        table_name_parts=lambda request: (request.source.table,),
        inspect_partition_values=None,
        metadata=EngineMetadataIntrospection(inspect_table=inspect_table),
        authoring_capabilities=AuthoringCapabilities(
            partition_predicate_supported=False,
            transformed_partition_supported=False,
            timeout_enforced=False,
            byte_estimate_supported=False,
        ),
        translate_strptime_format=identity_str,
        datetime_decode_policy="local_naive_label",
        quantile=None,
        percentile_uses_approx_quantile=False,
        authoring_timeout=None,
    )


def test_execute_records_submission_and_closes_cursor() -> None:
    profile = _probe_profile()
    statement = ProviderStatement(
        statement_id="probe.execute_check",
        template="SELECT {value}",
        literal_slots=frozenset({"value"}),
        allowed_purposes=frozenset({"test.channel"}),
    )
    register_provider_statements("probe", {"execute_check": statement})
    backend = _RecordingBackend(rows=[(1,)])
    rows = execute_provider_statement(
        backend, profile, "probe.execute_check", values={"value": 1}, purpose="test.channel"
    )
    assert rows == ({"value": 1},)
    assert backend.queries == ["SELECT '1'"]
    log = provider_statement_log(backend)
    assert len(log) == 1
    assert log[0].provider == "probe"
    assert log[0].statement_id == "probe.execute_check"
    assert log[0].purpose == "test.channel"
    assert log[0].state == "succeeded"


def test_execute_failure_marks_submission_failed_and_reraises() -> None:
    profile = _probe_profile()
    statement = ProviderStatement(
        statement_id="probe.fail_check",
        template="SELECT {value}",
        literal_slots=frozenset({"value"}),
        allowed_purposes=frozenset({"test.channel"}),
    )
    register_provider_statements("probe", {"fail_check": statement})
    backend = _FailingBackend()
    with pytest.raises(RuntimeError, match="connectivity lost"):
        execute_provider_statement(
            backend, profile, "probe.fail_check", values={"value": 1}, purpose="test.channel"
        )
    log = provider_statement_log(backend)
    assert log[0].state == "failed"


def test_http_credentials_field_is_owned_only_by_duckdb() -> None:
    for backend, profile in ENGINE_PROFILES.items():
        if backend == "duckdb":
            assert profile.http_credentials is not None
        else:
            assert profile.http_credentials is None, f"{backend} must not own credentials"


def test_url_is_in_http_scope_boundaries() -> None:
    scope = "https://api.example.com/v1/"
    assert url_is_in_http_scope("https://api.example.com/v1/orders", scope)
    assert url_is_in_http_scope("https://api.example.com/v1/", scope)
    assert url_is_in_http_scope("https://API.example.com/v1/orders", scope)
    assert not url_is_in_http_scope("https://api.example.com/v1x/orders", scope)
    assert not url_is_in_http_scope("https://api.example.com/v2/orders", scope)
    assert not url_is_in_http_scope("https://evil.example.com/v1/orders", scope)
    assert not url_is_in_http_scope("http://api.example.com/v1/orders", scope)
    assert not url_is_in_http_scope("https://api.example.com.evil.com/v1/orders", scope)


def test_json_http_headers_returns_empty_without_credentials() -> None:
    backend = _RecordingBackend()
    assert json_http_headers(backend, "https://api.example.com/v1/orders") == {}


@pytest.mark.parametrize("value", [True, 0, -1, 4294967296, "50", "50; KILL QUERY 1"])
def test_mysql_certification_deadline_rejects_untrusted_integer(value: object) -> None:
    profile = ENGINE_PROFILES["mysql"]
    statement = provider_statement("mysql", "mysql.authoring.install_select_deadline")
    with pytest.raises(ValueError, match="requires timeout_ms"):
        render_provider_statement(statement, profile, values={"timeout_ms": value})


@pytest.mark.parametrize("value", [1, 30000, 4294967295])
def test_mysql_certification_deadline_renders_only_bounded_integer(value: int) -> None:
    profile = ENGINE_PROFILES["mysql"]
    statement = provider_statement("mysql", "mysql.authoring.install_select_deadline")
    assert (
        render_provider_statement(statement, profile, values={"timeout_ms": value})
        == f"SET SESSION max_execution_time = {value}"
    )


@pytest.mark.parametrize(
    "statement_id",
    ["mysql.authoring.install_select_deadline", "mysql.authoring.read_select_deadline"],
)
def test_mysql_certification_statements_reject_other_purposes(statement_id: str) -> None:
    backend = _RecordingBackend()
    with pytest.raises(DatasourceSourceCapabilityError, match="purpose"):
        execute_provider_statement(
            backend, ENGINE_PROFILES["mysql"], statement_id, purpose="analysis.execute"
        )
    assert backend.queries == []
    assert provider_statement_log(backend) == ()


@pytest.mark.parametrize("fault", ["denied", "mismatch", "empty", "correct"])
def test_mysql_certification_guard_requires_confirmed_server_limit(
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    from collections.abc import Mapping

    from ibis.backends import BaseBackend

    from marivo.datasource.engines import mysql

    backend = _RecordingBackend()
    monkeypatch.setattr(backend, "_marivo_certified_authoring", True, raising=False)
    calls: list[str] = []

    def execute(
        backend: BaseBackend,
        profile: EngineProfile,
        statement_id: str,
        *,
        values: Mapping[str, object] = {},
        purpose: str,
    ) -> tuple[dict[str, object], ...]:
        calls.append(statement_id)
        assert purpose == "semantic.certified_preview.deadline"
        if statement_id.endswith("install_select_deadline"):
            assert values == {"timeout_ms": 30000}
            if fault == "denied":
                raise PermissionError("session setting denied")
            return ()
        return (
            ()
            if fault == "empty"
            else ({"@@session.max_execution_time": 0 if fault == "mismatch" else 30000},)
        )

    monkeypatch.setattr(mysql, "execute_provider_statement", execute)
    entered = False
    if fault == "correct":
        with mysql.certification_timeout(backend, 30):
            entered = True
        assert backend._marivo_certified_authoring is False
    else:
        with (
            pytest.raises(DatasourceSourceCapabilityError),
            mysql.certification_timeout(backend, 30),
        ):
            entered = True
    assert entered is (fault == "correct")
    assert len(calls) == (1 if fault == "denied" else 2)


@pytest.mark.parametrize("value", [True, 0, -1, 18446744073709551616, "123", "123; SELECT 1"])
def test_mysql_cancel_rejects_untrusted_connection_identity(value: object) -> None:
    statement = provider_statement("mysql", "mysql.analysis.cancel_owned_query")
    with pytest.raises(ValueError, match="requires thread_id"):
        render_provider_statement(statement, ENGINE_PROFILES["mysql"], values={"thread_id": value})


def test_mysql_cancel_has_a_closed_purpose_and_numeric_target() -> None:
    statement = provider_statement("mysql", "mysql.analysis.cancel_owned_query")
    assert (
        render_provider_statement(statement, ENGINE_PROFILES["mysql"], values={"thread_id": 123})
        == "KILL QUERY 123"
    )
    backend = _RecordingBackend()
    with pytest.raises(DatasourceSourceCapabilityError, match="purpose"):
        execute_provider_statement(
            backend,
            ENGINE_PROFILES["mysql"],
            statement.statement_id,
            values={"thread_id": 123},
            purpose="semantic.certified_preview.deadline",
        )
    assert backend.queries == []
    assert provider_statement_log(backend) == ()


def test_clickhouse_cancel_keeps_bound_identity_and_closed_purpose() -> None:
    profile = ENGINE_PROFILES["clickhouse"]
    statement = provider_statement("clickhouse", "clickhouse.analysis.cancel_owned_query")
    assert statement.parameterized
    assert render_provider_statement(statement, profile) == (
        "KILL QUERY WHERE query_id={id:String} AND user={user:String} SYNC"
    )
    backend = _RecordingBackend()
    with pytest.raises(DatasourceSourceCapabilityError, match="purpose"):
        execute_provider_statement(
            backend,
            profile,
            statement.statement_id,
            parameters={"id": "owned-native-id", "user": "reader"},
            purpose="datasource.metadata.clickhouse",
        )
    assert backend.queries == []
    assert provider_statement_log(backend) == ()


@pytest.mark.parametrize(
    "parameters", [{}, {"id": "x"}, {"id": "", "user": "reader"}, {"id": "x", "user": ""}]
)
def test_clickhouse_cancel_requires_both_nonempty_owned_identity_parameters(
    parameters: dict[str, str],
) -> None:
    backend = _RecordingBackend()
    with pytest.raises(ValueError, match="bound nonempty id and user"):
        execute_provider_statement(
            backend,
            ENGINE_PROFILES["clickhouse"],
            "clickhouse.analysis.cancel_owned_query",
            parameters=parameters,
            purpose="analysis.cancel_owned_query",
        )
    assert backend.queries == []
