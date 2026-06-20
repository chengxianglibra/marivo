"""Project-level datasource file storage."""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any, cast

from marivo.config import DATASOURCES_DIR
from marivo.datasource.authoring import DatasourceSpec
from marivo.datasource.errors import DatasourceMissingError
from marivo.datasource.ir import AiContextIR, DatasourceIR
from marivo.datasource.loader import load_datasources
from marivo.datasource.secrets import conventional_env_var
from marivo.project import resolve_project_root


def datasource_dir(project_root: Path | None = None) -> Path:
    root = project_root or resolve_project_root()
    return root / DATASOURCES_DIR


def datasource_path(name: str, project_root: Path | None = None) -> Path:
    return datasource_dir(project_root) / f"{name}.py"


def _literal(value: Any) -> str:
    return repr(value)


def _ai_context_literal(context: AiContextIR) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if context.business_definition is not None:
        data["business_definition"] = context.business_definition
    if context.guardrails:
        data["guardrails"] = list(context.guardrails)
    if context.synonyms:
        data["synonyms"] = list(context.synonyms)
    if context.examples:
        data["examples"] = list(context.examples)
    if context.instructions is not None:
        data["instructions"] = context.instructions
    if context.owner_notes is not None:
        data["owner_notes"] = context.owner_notes
    return data


_SPEC_CLASS_BY_BACKEND: dict[str, str] = {
    "clickhouse": "_ClickHouseSpec",
    "duckdb": "_DuckDBSpec",
    "mysql": "_MySQLSpec",
    "postgres": "_PostgresSpec",
    "trino": "_TrinoSpec",
}

_CONVENIENCE_FUNC_BY_BACKEND: dict[str, str] = {
    "clickhouse": "clickhouse",
    "duckdb": "duckdb",
    "mysql": "mysql",
    "postgres": "postgres",
    "trino": "trino",
}


def _write_datasource_file(
    *,
    spec: DatasourceSpec,
    project_root: Path | None = None,
) -> Path:
    path = datasource_path(spec.name, project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    func_name = _CONVENIENCE_FUNC_BY_BACKEND[spec.backend_type]
    # Separate declared fields from extra fields.
    declared_names = {
        f.name for f in dataclass_fields(spec) if f.name not in ("fields", "env_refs")
    }
    declared_kwargs: dict[str, Any] = {}
    extra_kwargs: dict[str, Any] = {}
    for key, value in spec.fields.items():
        if key in declared_names:
            declared_kwargs[key] = value
        else:
            extra_kwargs[key] = value
    kwargs: dict[str, Any] = {"name": spec.name, **declared_kwargs}
    # Only write explicit *_env overrides; conventional names are implied.
    for stem, env_var in spec.env_refs.items():
        if env_var == conventional_env_var(spec.name, stem):
            continue
        kwargs[f"{stem}_env"] = env_var
    ai_context = _ai_context_literal(cast("AiContextIR", spec.ai_context))
    if ai_context:
        kwargs["ai_context"] = ai_context
    if extra_kwargs:
        kwargs["extra"] = extra_kwargs
    lines = ["import marivo.datasource as md", "", f"md.{func_name}("]
    for key, value in kwargs.items():
        lines.append(f"    {key}={_literal(value)},")
    lines.append(")")
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
