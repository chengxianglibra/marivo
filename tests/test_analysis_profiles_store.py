"""Storage-layer tests for project-level datasources."""

from __future__ import annotations

from pathlib import Path

import pytest

from marivo.analysis.datasources import store as datasource_store
from marivo.analysis.errors import (
    DatasourceFieldInvalidError,
    DatasourceSecretInPlaintextError,
)


@pytest.fixture(autouse=True)
def _chdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_datasource_dir_uses_project_marivo(tmp_path: Path) -> None:
    assert datasource_store.datasource_dir(tmp_path) == tmp_path / ".marivo" / "datasource"


def test_load_all_empty_when_no_file() -> None:
    assert datasource_store.load_all() == {}


def test_save_roundtrip() -> None:
    datasource_store.save_one(
        name="warehouse",
        backend_type="trino",
        fields={
            "host": "trino.example",
            "port": 8080,
            "user_env": "TRINO_USER",
            "catalog": "hive",
            "password_env": "TRINO_PASSWORD",
        },
    )
    datasources = datasource_store.load_all()
    assert set(datasources) == {"warehouse"}
    assert datasources["warehouse"].backend_type == "trino"
    assert datasources["warehouse"].fields["host"] == "trino.example"
    assert datasources["warehouse"].env_refs["user"] == "TRINO_USER"
    assert datasources["warehouse"].env_refs["password"] == "TRINO_PASSWORD"
    assert datasource_store.datasource_path("warehouse").is_file()


def test_save_overwrites_same_name() -> None:
    datasource_store.save_one(name="wh", backend_type="duckdb", fields={"path": ":memory:"})
    datasource_store.save_one(name="wh", backend_type="duckdb", fields={"path": "/tmp/foo.ddb"})
    assert datasource_store.load_one("wh") is not None
    assert datasource_store.load_one("wh").fields["path"] == "/tmp/foo.ddb"  # type: ignore[union-attr]


def test_save_rejects_plaintext_sensitive_field() -> None:
    with pytest.raises(DatasourceSecretInPlaintextError) as exc_info:
        datasource_store.save_one(
            name="wh",
            backend_type="trino",
            fields={"host": "h", "catalog": "c", "password": "literal-secret"},
        )
    assert exc_info.value.details["field"] == "password"
    assert "password_env" in str(exc_info.value)


def test_save_rejects_plaintext_user() -> None:
    with pytest.raises(DatasourceSecretInPlaintextError) as exc_info:
        datasource_store.save_one(
            name="wh",
            backend_type="trino",
            fields={"host": "h", "catalog": "c", "user": "analytics"},
        )
    assert exc_info.value.details["field"] == "user"


def test_save_rejects_empty_backend_type() -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        datasource_store.save_one(name="wh", backend_type="", fields={"path": ":memory:"})
    assert exc_info.value.details["field"] == "backend_type"


def test_save_allows_json_object_fields() -> None:
    datasource_store.save_one(
        name="wh",
        backend_type="trino",
        fields={
            "host": "h",
            "catalog": "c",
            "session_properties": {"query_max_run_time": "5m"},
        },
    )
    datasource = datasource_store.load_one("wh")
    assert datasource is not None
    assert datasource.fields["session_properties"] == {"query_max_run_time": "5m"}


def test_save_rejects_non_json_object_value() -> None:
    with pytest.raises(DatasourceFieldInvalidError):
        datasource_store.save_one(
            name="wh",
            backend_type="trino",
            fields={"host": "h", "catalog": "c", "extras": {"nested": object()}},
        )


def test_save_rejects_env_ref_non_string() -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        datasource_store.save_one(
            name="wh",
            backend_type="trino",
            fields={"host": "h", "catalog": "c", "password_env": ""},
        )
    assert exc_info.value.details["field"] == "password_env"


@pytest.mark.parametrize("name", ["foo/bar", "foo\\bar", " foo", "foo bar", "../foo"])
def test_save_rejects_path_unsafe_name(name: str) -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        datasource_store.save_one(name=name, backend_type="duckdb", fields={"path": ":memory:"})
    assert exc_info.value.details["field"] == "<name>"
    assert not (Path.cwd() / ".marivo" / "datasource" / "foo").exists()


def test_delete_one_returns_true_when_removed() -> None:
    datasource_store.save_one(name="wh", backend_type="duckdb", fields={"path": ":memory:"})
    assert datasource_store.delete_one("wh") is True
    assert datasource_store.load_one("wh") is None


def test_delete_one_idempotent() -> None:
    assert datasource_store.delete_one("missing") is False


def test_list_names_sorted() -> None:
    datasource_store.save_one(name="b", backend_type="duckdb", fields={"path": ":memory:"})
    datasource_store.save_one(name="a", backend_type="duckdb", fields={"path": ":memory:"})
    assert datasource_store.list_names() == ["a", "b"]
