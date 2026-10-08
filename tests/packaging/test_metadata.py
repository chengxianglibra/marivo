"""Packaging metadata contracts."""

from __future__ import annotations

from packaging.requirements import Requirement

from marivo._compat import tomllib
from marivo.datasource.backends import SUPPORTED_BACKEND_TYPES
from tests.support.json import checked, obj
from tests.support.paths import PROJECT_ROOT


def _optional_dependencies() -> dict[str, list[str]]:
    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    with pyproject_path.open("rb") as handle:
        pyproject = tomllib.load(handle)
    optional = obj(obj(obj(checked(pyproject))["project"])["optional-dependencies"])
    result: dict[str, list[str]] = {}
    for name, dependencies in optional.items():
        assert isinstance(dependencies, list)
        result[name] = []
        for dependency in dependencies:
            assert isinstance(dependency, str)
            result[name].append(dependency)
    return result


def test_datasource_backend_extras_track_supported_backends() -> None:
    optional_dependencies = _optional_dependencies()

    for backend_type in SUPPORTED_BACKEND_TYPES:
        assert backend_type in optional_dependencies
        assert optional_dependencies[backend_type] == [f"ibis-framework[{backend_type}]>=12.0.0"]

    assert optional_dependencies["all"] == [
        "ibis-framework[duckdb,sqlite,trino,mysql,postgres,clickhouse]>=12.0.0"
    ]


def test_dev_extra_avoids_native_mysql_dependency() -> None:
    optional_dependencies = _optional_dependencies()

    assert all("mysql" not in dependency for dependency in optional_dependencies["dev"])


def test_core_sqlglot_requirement_excludes_broken_cleanup_version() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        dependencies = tomllib.load(handle)["project"]["dependencies"]
    requirement = next(Requirement(text) for text in dependencies if text.startswith("sqlglot"))
    assert requirement.specifier.contains("30.8.0")
    assert not requirement.specifier.contains("30.21.0")
    assert str(requirement.specifier) == "==30.8.0"
