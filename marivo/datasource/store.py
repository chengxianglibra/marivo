"""Project-level datasource file storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from marivo.datasource.authoring import DatasourceSpec
from marivo.datasource.errors import DatasourceMissingError
from marivo.datasource.ir import DatasourceIR
from marivo.datasource.loader import load_datasources
from marivo.project import resolve_project_root


def datasource_dir(project_root: Path | None = None) -> Path:
    root = project_root or resolve_project_root()
    return root / "marivo" / "datasources"


def datasource_path(name: str, project_root: Path | None = None) -> Path:
    return datasource_dir(project_root) / f"{name}.py"


def _literal(value: Any) -> str:
    return repr(value)


def _write_datasource_file(
    *,
    spec: DatasourceSpec,
    project_root: Path | None = None,
) -> Path:
    path = datasource_path(spec.name, project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    kwargs = {"name": spec.name, "backend_type": spec.backend_type, **dict(spec.fields)}
    kwargs.update({f"{key}_env": value for key, value in spec.env_refs.items()})
    lines = ["import marivo.datasource as md", "", "datasource = md.DatasourceSpec("]
    if spec.description is not None:
        kwargs["description"] = spec.description
    for key, value in kwargs.items():
        lines.append(f"    {key}={_literal(value)},")
    lines.append(")")
    lines.append("md.datasource(datasource)")
    path.write_text("\n".join(lines) + "\n")
    return path


def load_all(project_root: Path | None = None) -> dict[str, DatasourceIR]:
    result = load_datasources(datasource_dir(project_root))
    if result.errors:
        raise result.errors[0]
    return {datasource.name: datasource for datasource in result.datasources}


def load_one(name: str, project_root: Path | None = None) -> DatasourceIR | None:
    return load_all(project_root).get(name)


def save_one(spec: DatasourceSpec, project_root: Path | None = None) -> DatasourceIR:
    _write_datasource_file(
        spec=spec,
        project_root=project_root,
    )
    datasource = load_one(spec.name, project_root)
    if datasource is None:
        raise DatasourceMissingError(
            message=f"datasource {spec.name!r} was not written",
            details={"datasource": spec.name, "available": list_names(project_root)},
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
