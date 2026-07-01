"""Packaging metadata contracts."""

from __future__ import annotations

import tomllib
from pathlib import Path

from marivo.datasource.backends import SUPPORTED_BACKEND_TYPES


def _optional_dependencies() -> dict[str, list[str]]:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject_path.open("rb") as handle:
        pyproject = tomllib.load(handle)
    return pyproject["project"]["optional-dependencies"]


def _dependencies() -> list[str]:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject_path.open("rb") as handle:
        pyproject = tomllib.load(handle)
    return pyproject["project"]["dependencies"]


def test_datasource_backend_extras_track_supported_backends() -> None:
    optional_dependencies = _optional_dependencies()

    for backend_type in SUPPORTED_BACKEND_TYPES:
        assert backend_type in optional_dependencies
        assert optional_dependencies[backend_type] == [f"ibis-framework[{backend_type}]>=12.0.0"]

    assert optional_dependencies["all"] == [
        "ibis-framework[duckdb,trino,mysql,postgres,clickhouse]>=12.0.0"
    ]


def test_s3_publish_client_is_default_dependency() -> None:
    assert "boto3>=1.34" in _dependencies()
