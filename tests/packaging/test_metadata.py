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
        if backend_type not in {"duckdb", "mysql"}:
            assert optional_dependencies[backend_type] == [
                f"ibis-framework[{backend_type}]>=12.0.0"
            ]

    assert optional_dependencies["duckdb"] == [
        "ibis-framework[duckdb]>=12.0.0",
        "duckdb==1.5.3",
    ]

    assert optional_dependencies["mysql"] == [
        "PyMySQL[rsa]>=1.1.2,<1.2",
        "pyarrow-hotfix>=0.4",
        "rich>=12.4.4",
    ]

    assert optional_dependencies["all"] == [
        "ibis-framework[duckdb,trino,postgres,clickhouse]>=12.0.0",
        "duckdb==1.5.3",
        "PyMySQL[rsa]>=1.1.2,<1.2",
    ]


def test_all_extras_avoid_native_mysql_dependency() -> None:
    optional_dependencies = _optional_dependencies()
    requirements = [
        Requirement(dependency)
        for dependencies in optional_dependencies.values()
        for dependency in dependencies
    ]

    assert all(
        requirement.name.lower() not in {"mysqlclient", "types-mysqlclient"}
        and "mysql" not in requirement.extras
        for requirement in requirements
    )


def test_core_sqlglot_requirement_excludes_broken_cleanup_version() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        dependencies = tomllib.load(handle)["project"]["dependencies"]
    requirement = next(Requirement(text) for text in dependencies if text.startswith("sqlglot"))
    assert requirement.specifier.contains("30.8.0")
    assert not requirement.specifier.contains("30.21.0")
    assert str(requirement.specifier) == "==30.8.0"
