"""Public API tests for marivo.analysis_py.profiles registry."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.analysis_py as mv
from marivo.analysis_py.errors import ProfileMissingError


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MARIVO_HOME", str(home))
    return home


def test_set_returns_summary(fake_home: Path) -> None:
    summary = mv.profiles.set("wh", backend_type="duckdb", path=":memory:")
    assert summary.name == "wh"
    assert summary.backend_type == "duckdb"


def test_list_returns_sorted_summaries(fake_home: Path) -> None:
    mv.profiles.set("b", backend_type="duckdb", path=":memory:")
    mv.profiles.set("a", backend_type="duckdb", path=":memory:")
    names = [p.name for p in mv.profiles.list()]
    assert names == ["a", "b"]


def test_describe_redacts_secrets(fake_home: Path) -> None:
    mv.profiles.set(
        "wh",
        backend_type="trino",
        host="trino.example",
        port=8080,
        catalog="hive",
        password_env="WAREHOUSE_PWD",
    )
    desc = mv.profiles.describe("wh")
    assert desc.literal_fields == {"host": "trino.example", "port": 8080, "catalog": "hive"}
    assert desc.env_refs == {"password": "WAREHOUSE_PWD"}


def test_describe_missing_raises_with_hint(fake_home: Path) -> None:
    with pytest.raises(ProfileMissingError) as exc_info:
        mv.profiles.describe("nope")
    rendered = str(exc_info.value)
    assert "mv.profiles.set" in rendered
    assert "'nope'" in rendered


def test_remove_returns_bool(fake_home: Path) -> None:
    mv.profiles.set("wh", backend_type="duckdb", path=":memory:")
    assert mv.profiles.remove("wh") is True
    assert mv.profiles.remove("wh") is False
