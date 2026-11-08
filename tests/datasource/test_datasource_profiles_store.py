"""Storage-layer tests for project-level datasources."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.datasource as md
from marivo.datasource import store as datasource_store
from marivo.datasource.authoring import (
    DuckDBSpec,
    TrinoSpec,
)
from marivo.datasource.errors import DatasourceFieldInvalidError


@pytest.fixture(autouse=True)
def _chdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_save_roundtrip() -> None:
    datasource_store.save_one(
        TrinoSpec(
            name="warehouse",
            host="trino.example",
            port=8080,
            user=md.env("TRINO_USER"),
            catalog="hive",
            auth=md.env("TRINO_AUTH"),
        )
    )
    datasources = datasource_store.load_all()
    assert set(datasources) == {"default", "warehouse"}
    assert datasources["warehouse"].backend_type == "trino"
    assert datasources["warehouse"].fields["host"] == "trino.example"
    assert datasources["warehouse"].env_refs["user"] == "TRINO_USER"
    assert datasources["warehouse"].env_refs["auth"] == "TRINO_AUTH"
    assert datasource_store.datasource_path("warehouse").is_file()


def test_duckdb_multiple_http_header_env_refs_round_trip() -> None:
    datasource_store.save_one(
        DuckDBSpec(
            name="change_focus",
            secrets=[
                md.http_headers_secret(
                    scope="http://change-focus.example/api/",
                    headers={
                        "x-secretid": md.env("CHANGE_FOCUS_SECRET_ID"),
                        "x-signature": md.env("CHANGE_FOCUS_SIGNATURE"),
                    },
                )
            ],
        )
    )

    restored = datasource_store.load_one("change_focus")

    assert restored is not None
    assert restored.fields["secrets"][0]["scope"] == "http://change-focus.example/api/"
    assert restored.env_refs == {
        "http@http://change-focus.example/api/.header:x-secretid": "CHANGE_FOCUS_SECRET_ID",
        "http@http://change-focus.example/api/.header:x-signature": "CHANGE_FOCUS_SIGNATURE",
    }
    persisted = datasource_store.datasource_path("change_focus").read_text()
    assert "md.http_headers_secret(" in persisted
    assert "CHANGE_FOCUS_SECRET_ID" in persisted
    assert "CHANGE_FOCUS_SIGNATURE" in persisted


def test_save_overwrites_same_name() -> None:
    datasource_store.save_one(DuckDBSpec(name="wh", path=":memory:"))
    datasource_store.save_one(DuckDBSpec(name="wh", path="/tmp/foo.ddb"))
    assert datasource_store.load_one("wh") is not None
    assert datasource_store.load_one("wh").fields["path"] == "/tmp/foo.ddb"  # type: ignore[union-attr]


def test_save_rejects_plaintext_sensitive_field() -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        datasource_store.save_one(
            TrinoSpec(
                name="wh",
                host="h",
                catalog="c",
                user=md.env("TRINO_USER"),
                extra={"password": "literal-secret"},
            )
        )
    assert exc_info.value.received == "str"


def test_save_rejects_plaintext_user() -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        datasource_store.save_one(
            TrinoSpec(
                name="wh",
                host="h",
                catalog="c",
                user=md.env("TRINO_USER"),
                extra={"user": "analytics"},
            )
        )
    assert exc_info.value.received == "str"


def test_save_rejects_plaintext_auth() -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        datasource_store.save_one(
            TrinoSpec(
                name="wh",
                host="h",
                catalog="c",
                user=md.env("TRINO_USER"),
                extra={"auth": "literal-token"},
            )
        )
    assert exc_info.value.received == "str"


def test_save_allows_json_object_fields() -> None:
    datasource_store.save_one(
        TrinoSpec(
            name="wh",
            host="h",
            catalog="c",
            user=md.env("TRINO_USER"),
            session_properties={"query_max_run_time": "5m"},
        )
    )
    datasource = datasource_store.load_one("wh")
    assert datasource is not None
    assert datasource.fields["session_properties"] == {"query_max_run_time": "5m"}


def test_save_rejects_env_ref_non_string() -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        datasource_store.save_one(
            TrinoSpec(
                name="wh",
                host="h",
                catalog="c",
                user=md.env("TRINO_USER"),
                auth="",  # type: ignore[arg-type]  # Invalid credential.
            )
        )
    assert exc_info.value.location is not None
    assert exc_info.value.location.endswith("field 'auth'")


@pytest.mark.parametrize("name", ["foo/bar", "foo\\bar", " foo", "foo bar", "../foo"])
def test_save_rejects_path_unsafe_name(name: str) -> None:
    with pytest.raises(DatasourceFieldInvalidError) as exc_info:
        datasource_store.save_one(DuckDBSpec(name=name, path=":memory:"))
    assert exc_info.value.location == "datasource name"


def test_delete_one_returns_true_when_removed() -> None:
    datasource_store.save_one(DuckDBSpec(name="wh", path=":memory:"))
    assert datasource_store.delete_one("wh") is True
    assert datasource_store.load_one("wh") is None


def test_delete_one_idempotent() -> None:
    assert datasource_store.delete_one("missing") is False


def test_list_names_sorted() -> None:
    datasource_store.save_one(DuckDBSpec(name="b", path=":memory:"))
    datasource_store.save_one(DuckDBSpec(name="a", path=":memory:"))
    assert datasource_store.list_names() == ["a", "b", "default"]


def test_save_rejects_non_json_object_value() -> None:
    with pytest.raises(DatasourceFieldInvalidError):
        datasource_store.save_one(
            TrinoSpec(
                name="wh",
                host="h",
                catalog="c",
                user=md.env("TRINO_USER"),
                extra={"nested": object()},  # type: ignore[dict-item]  # Invalid JSON value.
            )
        )
