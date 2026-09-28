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

    # Every registered provider statement must appear here verbatim. Add the
    # (provider, statement_id) -> sha256(template) pair when registering one.
    pinned: dict[tuple[str, str], str] = {}
    observed = {
        (provider, statement_id): hashlib.sha256(statement.template.encode("utf-8")).hexdigest()
        for provider, owned in provider_statement_catalog().items()
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


def test_http_credentials_field_is_none_except_owner() -> None:
    for backend, profile in ENGINE_PROFILES.items():
        assert profile.http_credentials is None, f"{backend} must not own credentials yet"


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
