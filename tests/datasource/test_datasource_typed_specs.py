"""Contract tests for datasource convenience functions and public spec classes."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import marivo.datasource as md
import marivo.datasource.catalog as catalog_module
import marivo.semantic as ms
from marivo.datasource import store as datasource_store
from marivo.datasource.authoring import (
    ClickHouseSpec,
    DatasourceSpec,
    DuckDBSpec,
    MySQLSpec,
    PostgresSpec,
    TrinoSpec,
    _ir_from_spec,
    validate_datasource_name,
)
from marivo.datasource.errors import (
    DatasourceFieldInvalidError,
)
from marivo.datasource.ir import DatasourceIR, DatasourceSourceLocation
from marivo.refs import ref as ref_factory
from tests.support.refs import as_ref
from tests.support.result_protocol import assert_conforms


def _ir(
    spec: DuckDBSpec | TrinoSpec | MySQLSpec | PostgresSpec | ClickHouseSpec,
) -> DatasourceIR:
    return _ir_from_spec(
        spec,
        location=DatasourceSourceLocation(file="<test>", line=1),
    )


# -- Public spec class tests (validation, serialization, IR mapping) --


def test_duckdb_spec_defaults_to_memory_path() -> None:
    spec = DuckDBSpec(name="local")
    ir = _ir(spec)

    assert spec.backend_type == "duckdb"
    assert ir.backend_type == "duckdb"
    assert ir.fields == {"path": ":memory:", "read_only": False}
    assert ir.env_refs == {}


def test_duckdb_http_auth_is_scoped_and_environment_backed() -> None:
    bearer = _ir(
        DuckDBSpec(
            name="hawkeye",
            secrets=[
                md.http_bearer_secret(
                    scope="http://hawkeye.example/report/api/", token=md.env("HAWKEYE_TOKEN")
                )
            ],
        )
    )
    custom = _ir(
        DuckDBSpec(
            name="custom",
            secrets=[
                md.http_headers_secret(
                    scope="https://api.example/v1/",
                    headers={
                        "x-secretid": md.env("CHANGE_FOCUS_SECRET_ID"),
                        "x-signature": md.env("CHANGE_FOCUS_SIGNATURE"),
                    },
                )
            ],
        )
    )

    assert bearer.fields == {
        "path": ":memory:",
        "read_only": False,
        "secrets": [{"type": "http", "scope": "http://hawkeye.example/report/api/"}],
    }
    assert bearer.env_refs == {"http@http://hawkeye.example/report/api/.token": "HAWKEYE_TOKEN"}
    assert custom.env_refs == {
        "http@https://api.example/v1/.header:x-secretid": "CHANGE_FOCUS_SECRET_ID",
        "http@https://api.example/v1/.header:x-signature": "CHANGE_FOCUS_SIGNATURE",
    }


@pytest.mark.parametrize(
    "name",
    [
        "prod-mysql",
        "Warehouse",
        "1warehouse",
    ],
)
def test_datasource_name_grammar_rejects_invalid_shapes(
    name: str,
) -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        validate_datasource_name(name)

    assert exc_info.value.expected == "[a-z][a-z0-9_]*"


def test_trino_spec_maps_declared_fields_and_named_secret_env_refs() -> None:
    spec = TrinoSpec(
        name="warehouse",
        host="trino.example",
        catalog="hive",
        port=8443,
        wall_clock_timezone="Asia/Shanghai",
        client_tags=("agent", "semantic-authoring"),
        session_properties={"query_max_run_time": "5m"},
        user=md.env("TRINO_USER"),
        auth=md.env("TRINO_AUTH"),
    )
    ir = _ir(spec)

    assert spec.backend_type == "trino"
    assert ir.fields == {
        "host": "trino.example",
        "catalog": "hive",
        "port": 8443,
        "client_tags": ["agent", "semantic-authoring"],
        "session_properties": {"query_max_run_time": "5m"},
    }
    assert ir.env_refs == {"user": "TRINO_USER", "auth": "TRINO_AUTH"}
    assert ir.wall_clock_timezone == "Asia/Shanghai"


def test_trino_user_is_required_by_spec_and_helper() -> None:
    assert inspect.signature(TrinoSpec).parameters["user"].default is inspect.Parameter.empty
    assert inspect.signature(md.trino).parameters["user"].default is inspect.Parameter.empty

    with pytest.raises(TypeError):
        TrinoSpec(  # type: ignore[call-arg]
            name="warehouse",
            host="trino.example",
            catalog="hive",
        )

    with pytest.raises(TypeError):
        md.trino(  # type: ignore[call-arg]
            name="warehouse",
            host="trino.example",
            catalog="hive",
        )

    for invalid_user in ("", None):
        with pytest.raises(DatasourceFieldInvalidError) as exc_info:
            TrinoSpec(
                name="warehouse",
                host="trino.example",
                catalog="hive",
                user=invalid_user,  # type: ignore[arg-type]
            )

        assert exc_info.value.expected == "an EnvRef"
        assert exc_info.value.location is not None
        assert exc_info.value.location.endswith("field 'user'")
        assert exc_info.value.repair is not None
        assert exc_info.value.repair.help_target.canonical_id == "env"

    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        md.trino(
            name="warehouse",
            host="trino.example",
            catalog="hive",
            user=None,  # type: ignore[arg-type]
        )

    assert exc_info.value.repair is not None
    assert exc_info.value.repair.help_target.canonical_id == "env"


def test_mysql_postgres_and_clickhouse_required_shapes() -> None:
    assert _ir(MySQLSpec(name="mysql_wh", host="mysql.example", database="mart")).fields == {
        "host": "mysql.example",
        "database": "mart",
    }
    assert _ir(PostgresSpec(name="pg_wh", host="pg.example", database="mart")).fields == {
        "host": "pg.example",
        "database": "mart",
    }
    assert _ir(ClickHouseSpec(name="ch_wh", host="ch.example", secure=True)).fields == {
        "host": "ch.example",
        "secure": True,
    }


def test_empty_required_string_raises_teaching_error() -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        TrinoSpec(name="warehouse", host="", catalog="hive", user=md.env("TRINO_USER"))

    assert exc_info.value.location == "models/datasources/ entry 'warehouse' field 'host'"


def test_extra_merges_json_safe_passthrough_fields() -> None:
    spec = ClickHouseSpec(
        name="ch_wh",
        host="ch.example",
        extra={"compression": "lz4", "connect_timeout": 10},
    )

    assert _ir(spec).fields == {
        "host": "ch.example",
        "compression": "lz4",
        "connect_timeout": 10,
    }


def test_extra_rejects_plaintext_sensitive_stems() -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        TrinoSpec(
            name="warehouse",
            host="trino.example",
            catalog="hive",
            user=md.env("TRINO_USER"),
            extra={"password": "literal-secret"},
        )

    assert exc_info.value.received == "str"


def test_extra_rejects_non_json_values() -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        TrinoSpec(
            name="warehouse",
            host="trino.example",
            catalog="hive",
            user=md.env("TRINO_USER"),
            extra={"custom_option": object()},  # type: ignore[dict-item]  # Invalid JSON value.
        )

    assert exc_info.value.location is not None
    assert exc_info.value.location.endswith("field 'custom_option'")


def test_datasource_helper_returns_public_spec_and_ref() -> None:
    spec = md.duckdb(name="warehouse", path="warehouse.duckdb")

    assert isinstance(spec, md.DuckDBSpec)
    reference = as_ref(spec.ref)
    assert reference == ref_factory.datasource("warehouse")
    assert reference is not None
    assert reference.path == "warehouse"
    assert reference.name == "warehouse"


def test_spec_ai_context_maps_to_ir() -> None:
    spec = DuckDBSpec(
        name="warehouse",
        ai_context=ms.ai_context(
            business_definition="Local analytical warehouse.",
            guardrails=["Do not use for production freshness checks."],
        ),
    )

    ir = _ir(spec)

    assert ir.ai_context.business_definition == "Local analytical warehouse."
    assert ir.ai_context.guardrails == ("Do not use for production freshness checks.",)


# -- Public convenience function signatures --


def test_trino_helper_signature_has_current_fields() -> None:
    signature = inspect.signature(md.trino)

    assert "host" in signature.parameters
    assert "catalog" in signature.parameters
    assert "ai_context" in signature.parameters


def test_duckdb_helper_signature_has_current_fields() -> None:
    signature = inspect.signature(md.duckdb)

    assert "name" in signature.parameters
    assert "path" in signature.parameters
    assert "ai_context" in signature.parameters


# -- Store persistence tests --


def test_store_writes_convenience_function_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    md.register(
        TrinoSpec(
            name="warehouse",
            host="trino.example",
            catalog="hive",
            user=md.env("MARIVO_WAREHOUSE_USER"),
            auth=md.env("TRINO_AUTH"),
        )
    )

    datasource_file = tmp_path / "models" / "datasources" / "warehouse.py"
    text = datasource_file.read_text(encoding="utf-8")
    assert "md.trino(" in text
    assert "user=md.env('MARIVO_WAREHOUSE_USER')" in text or (
        'user=md.env("MARIVO_WAREHOUSE_USER")' in text
    )
    assert "auth=md.env('TRINO_AUTH')" in text or 'auth=md.env("TRINO_AUTH")' in text
    assert md.load().describe("warehouse").env_refs == {
        "user": "MARIVO_WAREHOUSE_USER",
        "auth": "TRINO_AUTH",
    }


def test_store_persists_ai_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    md.register(
        DuckDBSpec(
            name="warehouse",
            path=":memory:",
            ai_context=ms.ai_context(
                business_definition="Local analytical warehouse.",
                guardrails=["Use for tests only."],
            ),
        )
    )

    datasource_file = tmp_path / "models" / "datasources" / "warehouse.py"
    text = datasource_file.read_text(encoding="utf-8")
    assert "ai_context=" in text
    assert "business_definition" in text
    assert md.load().describe("warehouse").literal_fields == {
        "path": ":memory:",
        "read_only": False,
    }


def test_catalog_list_returns_displayable_datasource_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    md.register(DuckDBSpec(name="warehouse", path=":memory:"))

    result = md.load().list()

    assert isinstance(result, md.DatasourceList)
    assert_conforms(result)
    assert len(result) == 2
    assert result.items[1].name == "warehouse"
    assert result[0].backend_type == "duckdb"
    assert [item.name for item in result] == ["default", "warehouse"]
    assert result.show() is None  # type: ignore[func-returns-value]  # Verify the runtime return.


def test_catalog_show_renders_full_datasource_model_without_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRINO_USER", "warehouse-user")
    monkeypatch.setenv("TRINO_AUTH", "super-secret-token")
    md.register(
        TrinoSpec(
            name="warehouse",
            host="trino.example",
            catalog="hive",
            user=md.env("TRINO_USER"),
            auth=md.env("TRINO_AUTH"),
            ai_context=ms.ai_context(
                business_definition="Curated warehouse tables.",
                guardrails=["Use partition filters."],
            ),
        )
    )

    catalog = md.load()
    assert catalog.show() is None  # type: ignore[func-returns-value]  # Verify the runtime return.


def test_catalog_show_uses_one_current_declaration_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    initial = {name: _ir(DuckDBSpec(name=name)) for name in ("zeta", "alpha")}
    changed = {"current": _ir(DuckDBSpec(name="current"))}

    def load_all(project_root: Path) -> dict[str, DatasourceIR]:
        calls.append(project_root)
        return initial if len(calls) == 1 else changed

    monkeypatch.setattr(datasource_store, "load_all", load_all)
    catalog = catalog_module.DatasourceCatalog(workspace_dir=tmp_path)

    assert catalog.show() is None  # type: ignore[func-returns-value]  # Verify the runtime return.
    assert calls == [tmp_path]

    assert catalog.show() is None  # type: ignore[func-returns-value]  # Verify the runtime return.
    assert calls == [tmp_path, tmp_path]


# -- Public spec field visibility --


def test_declared_spec_fields_are_visible_to_dataclasses_help() -> None:
    trino_field_names = {field.name for field in fields(TrinoSpec)}

    assert {"name", "host", "catalog", "port", "user", "auth", "extra"} <= trino_field_names


@pytest.mark.parametrize(
    "spec",
    [
        DuckDBSpec(name="local"),
        TrinoSpec(
            name="warehouse",
            host="trino.example",
            catalog="hive",
            user=md.env("TRINO_USER"),
            port=8443,
            schema="sales",
            http_scheme="https",
        ),
        MySQLSpec(name="mysql_wh", host="mysql.example", database="sales", port=3307),
        PostgresSpec(
            name="pg_wh",
            host="postgres.example",
            database="sales",
            port=5433,
            schema="mart",
        ),
        ClickHouseSpec(
            name="clickhouse_wh",
            host="clickhouse.example",
            port=9440,
            database="sales",
            secure=True,
        ),
    ],
)
def test_datasource_specs_render_only_agent_relevant_state(
    spec: DuckDBSpec | TrinoSpec | MySQLSpec | PostgresSpec | ClickHouseSpec,
) -> None:
    assert_conforms(spec)


def test_datasource_spec_render_summarizes_hidden_configuration_and_credentials() -> None:
    spec = TrinoSpec(
        name="warehouse",
        host="trino.example",
        catalog="hive",
        user=md.env("WAREHOUSE_USER"),
        auth=md.env("WAREHOUSE_AUTH"),
        session_properties={f"property_{index}": "x" * 100 for index in range(100)},
        extra={"custom_options": {f"option_{index}": "y" * 100 for index in range(100)}},
    )

    rendered = spec.render()

    assert len(rendered.encode("utf-8")) < 1024
    assert len(repr(spec)) < 200
    assert spec.env_refs == {"user": "WAREHOUSE_USER", "auth": "WAREHOUSE_AUTH"}


@pytest.mark.parametrize(
    "spec",
    [
        md.duckdb(name="local", wall_clock_timezone="Etc/GMT-8"),
        md.postgres(name="pg", host="pg.example", database="app", wall_clock_timezone="+08:00"),
        md.mysql(name="my", host="mysql.example", database="app", wall_clock_timezone="UTC+08:00"),
        md.trino(
            name="tr",
            host="trino.example",
            catalog="hive",
            user=md.env("TRINO_USER"),
            wall_clock_timezone="UTC+08:00:00",
        ),
        md.clickhouse(name="ch", host="ch.example", wall_clock_timezone="UTC+08:00"),
    ],
)
def test_wall_clock_timezone_is_normalized_separate_from_connection_fields(
    spec: DatasourceSpec, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource import store
    from marivo.datasource.authoring_store import datasource_spec_fingerprint

    declaration = _ir(spec)
    assert spec.wall_clock_timezone == "UTC+08:00"
    assert declaration.wall_clock_timezone == "UTC+08:00"
    changed = replace(declaration, wall_clock_timezone="UTC")
    assert datasource_spec_fingerprint(declaration) == datasource_spec_fingerprint(changed)

    store.save_one(spec, project_root=tmp_path)
    loaded = store.load_one(spec.name, project_root=tmp_path)
    assert loaded is not None
    assert loaded.wall_clock_timezone == "UTC+08:00"
    assert loaded.fields == declaration.fields
    assert loaded.env_refs == declaration.env_refs
    default = store.load_one("default", project_root=tmp_path)
    assert default is not None
    assert default.wall_clock_timezone is None
    description = md.load(workspace_dir=tmp_path).describe(spec.name)
    assert description.wall_clock_timezone == "UTC+08:00"

    from marivo.datasource import backends
    from marivo.datasource.engines import require_profile_for_backend_type

    profile = require_profile_for_backend_type(spec.backend_type)
    captured: dict[str, object] = {}

    def connect(name: str, kwargs: Mapping[str, object]) -> SimpleNamespace:
        assert name == spec.name
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setenv("TRINO_USER", "reader")
    monkeypatch.setattr(
        backends,
        "require_profile_for_backend_type",
        lambda _kind: replace(profile, connect=connect),
    )
    backends.build_backend(declaration)
    if spec.backend_type == "trino":
        assert captured["timezone"] == "UTC"


@pytest.mark.parametrize(
    "value", ["", " Asia/Shanghai", "Asia/Shanghai ", "localtime", "Not/AZone"]
)
def test_wall_clock_timezone_invalid_values_have_datasource_repair(value: str) -> None:
    with pytest.raises(DatasourceFieldInvalidError) as caught:
        md.duckdb(name="warehouse", wall_clock_timezone=value)
    assert caught.value.received == repr(value)
    assert caught.value.effect_observed is not None
    assert caught.value.effect_observed.query_executed is False
    assert caught.value.repair is not None
    assert caught.value.repair.help_target.canonical_id == "duckdb"


def test_datasource_ir_normalizes_direct_declarations_and_rejects_invalid_timezone() -> None:
    definition = _ir(md.duckdb(name="warehouse"))
    assert replace(definition, wall_clock_timezone="Etc/UTC").wall_clock_timezone == "UTC"
    assert replace(definition, wall_clock_timezone="Etc/GMT-8").wall_clock_timezone == "UTC+08:00"
    with pytest.raises(DatasourceFieldInvalidError):
        replace(definition, wall_clock_timezone="Not/AZone")


@pytest.mark.parametrize("headers", [{}, {"bad header": md.env("KEY")}, {"X-Key": "literal"}])
def test_duckdb_headers_reject_invalid_inputs(headers: object) -> None:
    with pytest.raises(DatasourceFieldInvalidError):
        md.http_headers_secret(scope="https://api.example/", headers=headers)  # type: ignore[arg-type]  # Invalid headers exercise validation.


def test_missing_required_field_raises_native_type_error() -> None:
    with pytest.raises(TypeError):
        TrinoSpec(name="warehouse", host="trino.example")  # type: ignore[call-arg]


def test_unknown_field_raises_native_type_error() -> None:
    with pytest.raises(TypeError):
        TrinoSpec(  # type: ignore[call-arg]
            name="warehouse",
            host="trino.example",
            catalog="hive",
            user=md.env("TRINO_USER"),
            prot=8080,
        )


def test_trino_timezone_is_removed_from_spec_and_constructor() -> None:
    for constructor in (md.trino, TrinoSpec):
        assert "timezone" not in inspect.signature(constructor).parameters
        with pytest.raises(TypeError):
            constructor(
                name="warehouse",
                host="trino.example",
                catalog="hive",
                user=md.env("TRINO_USER"),
                timezone="UTC",  # type: ignore[call-arg]  # Check the removed keyword.
            )
    with pytest.raises(DatasourceFieldInvalidError):
        md.trino(
            name="warehouse",
            host="trino.example",
            catalog="hive",
            user=md.env("TRINO_USER"),
            extra={"timezone": "Asia/Shanghai"},
        )
    with pytest.raises(DatasourceFieldInvalidError):
        md.duckdb(name="warehouse", extra={"wall_clock_timezone": "UTC"})
