"""Project-level datasource file storage."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from marivo.analysis_py.errors import DatasourceMissingError
from marivo.analysis_py.session.active import resolve_project_root
from marivo.datasource_py.authoring import _split_fields, validate_datasource_name
from marivo.datasource_py.ir import DatasourceIR
from marivo.datasource_py.loader import load_datasources


def datasource_dir(project_root: Path | None = None) -> Path:
    root = project_root or resolve_project_root()
    return root / ".marivo" / "datasource"


def datasource_path(name: str, project_root: Path | None = None) -> Path:
    return datasource_dir(project_root) / f"{name}.py"


def _literal(value: Any) -> str:
    return repr(value)


def _write_datasource_file(
    *,
    name: str,
    backend_type: str,
    fields: Mapping[str, Any],
    project_root: Path | None = None,
) -> Path:
    path = datasource_path(name, project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    kwargs = {"name": name, "backend_type": backend_type, **dict(fields)}
    lines = ["import marivo.datasource_py as md", "", "md.datasource("]
    for key, value in kwargs.items():
        lines.append(f"    {key}={_literal(value)},")
    lines.append(")")
    path.write_text("\n".join(lines) + "\n")
    return path


def _validate_datasource_config(
    *,
    name: str,
    backend_type: str,
    fields: Mapping[str, Any],
) -> None:
    validate_datasource_name(name)
    if not isinstance(backend_type, str) or not backend_type:
        from marivo.datasource_py.errors import DatasourceFieldInvalidError

        raise DatasourceFieldInvalidError(
            message=f"datasource {name!r} missing required backend_type",
            details={
                "datasource": name,
                "field": "backend_type",
                "reason": "backend_type is required and must be a non-empty string",
            },
        )
    _split_fields(name, fields)


def load_all(project_root: Path | None = None) -> dict[str, DatasourceIR]:
    result = load_datasources(datasource_dir(project_root))
    if result.errors:
        raise result.errors[0]
    return {datasource.name: datasource for datasource in result.datasources}


def load_one(name: str, project_root: Path | None = None) -> DatasourceIR | None:
    return load_all(project_root).get(name)


def save_one(
    name: str,
    backend_type: str,
    fields: Mapping[str, Any],
    project_root: Path | None = None,
) -> DatasourceIR:
    _validate_datasource_config(name=name, backend_type=backend_type, fields=fields)
    _write_datasource_file(
        name=name,
        backend_type=backend_type,
        fields=fields,
        project_root=project_root,
    )
    datasource = load_one(name, project_root)
    if datasource is None:
        raise DatasourceMissingError(
            message=f"datasource {name!r} was not written",
            details={"datasource": name, "available": list_names(project_root)},
        )
    return datasource


def delete_one(name: str, project_root: Path | None = None) -> bool:
    path = datasource_path(name, project_root)
    if not path.is_file():
        return False
    path.unlink()
    return True


def list_names(project_root: Path | None = None) -> list[str]:
    return sorted(load_all(project_root).keys())
