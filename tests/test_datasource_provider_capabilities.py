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
from marivo.datasource.errors import DatasourceSourceCapabilityError


class _RecordingBackend:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self.queries: list[str] = []
        self.rows = rows or []
        self.closed = 0

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


def test_registered_statements_are_pinned_by_snapshot() -> None:
    """The channel SQL text is frozen; edits must update this snapshot deliberately."""
    import hashlib

    # Load every engine module so each provider's statements are registered.
    assert all(name in ENGINE_PROFILES for name in ENGINE_PROFILES)

    # Every registered provider statement must appear here verbatim. Add the
    # (provider, statement_id) -> sha256(template) pair when registering one.
    pinned: dict[tuple[str, str], str] = {
        ("duckdb", "duckdb.constraints"): (
            "aed05b4e5cde9aad979d79838f6473690bf7b2291e3ae6e8f385a5c2d7f7b372"
        ),
        ("duckdb", "duckdb.http_secret_bearer"): (
            "fd508a63cb457ea45d964ff9db5bf9958c70a9ae9639b376616972f5f7447f7d"
        ),
        ("duckdb", "duckdb.http_secret_headers"): (
            "b1f8b0fbc9dcafd9a51b3876bd3409d122c7e78f54b9e3c121c856db281c7f0e"
        ),
        ("duckdb", "duckdb.namespace.current"): (
            "a4c4ac1c8ed42516de7b86109f2dfe3cbf7aadbc008851126b988472d2565952"
        ),
        ("duckdb", "duckdb.tables.columns"): (
            "1100b01df364d116844953075a4929f8be3821e2e5957cacac33fe40166695be"
        ),
        ("duckdb", "duckdb.tables.comment"): (
            "2feee340737744c45a8b9654e0b5c7773e554adb01867507bc8bceb9ecac2553"
        ),
        ("duckdb", "duckdb.tables.comment_size"): (
            "58ee012b4e4b04cd950bc4260a81e54cafaddb8ec8616b4edfb0154247eda6f3"
        ),
        ("duckdb", "duckdb.views.database_qualified"): (
            "32cd1c9a2426b7ce2d7652a0611527958f1e8151e8ba039f4f5091d81a7d9e25"
        ),
        ("duckdb", "duckdb.views.schema_qualified"): (
            "9caeecc09a5e820921a3a5f205cb74323e9d4310a572c8900482f58cf57f7a3a"
        ),
        ("sqlite", "sqlite.pragma.index_info"): (
            "3b25054b5a4a4f89898806b4b020ae56b2c4e56ef04f342a67684dacbc74c05a"
        ),
        ("sqlite", "sqlite.pragma.index_list"): (
            "10dec4aefa9f8f9cd08da9ef3a371bcc9cec203cbfee5aa1cfd22af6a963c916"
        ),
        ("sqlite", "sqlite.pragma.table_info"): (
            "237a2faf8e4bebb8b55c0848614c2245fd2f0f63deb8b5286e95e6004260c688"
        ),
        ("sqlite", "sqlite.schema.kind"): (
            "1ea7edcf28dc690b6068d106c60ec5ef7fed215ee7078b2486114ed7c6bcb337"
        ),
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


def _probe_profile():
    from marivo.datasource.engines.base import (
        AuthoringCapabilities,
        EngineMetadataIntrospection,
        EngineProfile,
        identity_read_only_kwargs,
        identity_str,
    )

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
        metadata=EngineMetadataIntrospection(inspect_table=lambda request: None),
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
