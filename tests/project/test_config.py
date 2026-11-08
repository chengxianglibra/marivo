"""Tests for effective zero-initialization project configuration."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from marivo._compat import tomllib
from marivo.config import ProjectConfig, load_project_config


def test_missing_manifest_uses_defaults_without_writing(tmp_path: Path) -> None:
    config = load_project_config(tmp_path)

    assert config == ProjectConfig(name=tmp_path.name)
    assert config.semantic_layer_paths == ()
    assert config.telemetry_enabled is True
    assert config.telemetry_mode == "on"


def test_missing_fields_use_defaults_and_version_is_not_a_config_field(tmp_path: Path) -> None:
    (tmp_path / "marivo.toml").write_text(
        "[project]\nversion = 42\n",
        encoding="utf-8",
    )

    config = load_project_config(tmp_path)

    assert config == ProjectConfig(name=tmp_path.name)


def test_explicit_project_configuration_overrides_defaults(tmp_path: Path) -> None:
    absolute = (tmp_path / "absolute" / "models").resolve()
    (tmp_path / "marivo.toml").write_text(
        textwrap.dedent(
            f"""
            [project]
            name = "sales"

            [semantic]
            layer_paths = ["../shared/models", "{absolute}"]

            [telemetry]
            enabled = "off"
            """
        ),
        encoding="utf-8",
    )

    config = load_project_config(tmp_path)

    assert config.name == "sales"
    assert config.semantic_layer_paths == (
        (tmp_path / "../shared/models").resolve(),
        absolute,
    )
    assert config.telemetry_enabled is False
    assert config.telemetry_mode == "off"


def test_on_telemetry_mode_is_explicit(tmp_path: Path) -> None:
    (tmp_path / "marivo.toml").write_text('[telemetry]\nenabled = "on"\n', encoding="utf-8")
    config = load_project_config(tmp_path)
    assert config.telemetry_enabled is True
    assert config.telemetry_mode == "on"


@pytest.mark.parametrize(
    "manifest",
    [
        'project = "sales"\n',
        '[project]\nname = ""\n',
        'semantic = "models"\n',
        '[semantic]\nlayer_paths = "../shared/models"\n',
        'telemetry = "on"\n',
        "[telemetry]\nenabled = true\n",
        '[telemetry]\nenabled = "full"\n',
    ],
)
def test_explicit_invalid_configuration_fails_closed(tmp_path: Path, manifest: str) -> None:
    (tmp_path / "marivo.toml").write_text(manifest, encoding="utf-8")

    with pytest.raises(ValueError):
        load_project_config(tmp_path)


def test_invalid_toml_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "marivo.toml").write_text("invalid [[ toml", encoding="utf-8")

    with pytest.raises(tomllib.TOMLDecodeError):
        load_project_config(tmp_path)
