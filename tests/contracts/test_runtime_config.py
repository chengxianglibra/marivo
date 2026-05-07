from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.local.toml_runtime_config import TomlRuntimeConfig
from app.contracts.errors import ValidationError


def _make_config(tmp_path: Path) -> TomlRuntimeConfig:
    config_path = tmp_path / "marivo.toml"
    config_path.write_text(
        '[profile]\nmode = "local"\n\n[datasource]\ntype = "duckdb"\n'
    )
    return TomlRuntimeConfig(config_path)


toml_config_factories = [
    ("TomlRuntimeConfig", _make_config),
]


@pytest.mark.parametrize("name,factory", toml_config_factories)
def test_get_existing_key(name, factory, tmp_path):
    config = factory(tmp_path)
    assert config.get("profile.mode") == "local"


@pytest.mark.parametrize("name,factory", toml_config_factories)
def test_get_missing_key_returns_none(name, factory, tmp_path):
    config = factory(tmp_path)
    assert config.get("nonexistent.key") is None


@pytest.mark.parametrize("name,factory", toml_config_factories)
def test_get_datasource_type(name, factory, tmp_path):
    config = factory(tmp_path)
    assert config.get("datasource.type") == "duckdb"


def test_missing_config_file_returns_none(tmp_path):
    config = TomlRuntimeConfig(tmp_path / "nonexistent.toml")
    assert config.get("any.key") is None


def test_invalid_toml_raises_validation_error(tmp_path):
    bad_path = tmp_path / "bad.toml"
    bad_path.write_text("this is [ not valid toml {{{")
    config = TomlRuntimeConfig(bad_path)
    with pytest.raises(ValidationError):
        config.get("any.key")
