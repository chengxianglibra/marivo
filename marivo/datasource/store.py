"""Project-level datasource file storage."""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any, cast

from marivo._authoring.loading import _current_source_loading_file
from marivo.config import (
    AUTHORED_DIR,
    DATASOURCES_DIR,
    ProjectConfig,
    load_project_config,
)
from marivo.datasource.authoring import DatasourceSpec, _storage_name
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.errors import (
    DatasourceLoadError,
    repair,
)
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation
from marivo.datasource.loader import _load_models_datasources, _models_root_errors
from marivo.project import resolve_project_root


def datasource_dir(project_root: Path | None = None) -> Path:
    root = project_root or resolve_project_root()
    return root / DATASOURCES_DIR


def datasource_path(name: str, project_root: Path | None = None) -> Path:
    return datasource_dir(project_root) / f"{_storage_name(name)}.py"


def require_project_config(project_root: Path) -> ProjectConfig:
    """Load project configuration through the datasource error boundary."""
    try:
        return load_project_config(project_root)
    except ValueError as exc:
        raise DatasourceLoadError(
            message=f"project configuration is invalid: {exc}",
            expected="a valid explicit marivo.toml or no project manifest",
            received=str(exc),
            location=str(project_root / "marivo.toml"),
            repair=repair(
                kind="configure",
                canonical_id="load",
                action="Fix the explicit marivo.toml configuration and reload datasources.",
            ),
        ) from exc


def _literal(value: Any) -> str:
    return repr(value)


def _ai_context_literal(context: AiContextIR) -> str | None:
    """Generate a ms.ai_context(...) call string from an AiContextIR.

    Returns None if all fields are empty/None.
    """
    parts: list[str] = []
    if context.business_definition is not None:
        parts.append(f"business_definition={context.business_definition!r}")
    if context.guardrails:
        parts.append(f"guardrails={list(context.guardrails)!r}")
    if not parts:
        return None
    return f"ms.ai_context({', '.join(parts)})"


def _write_datasource_file(
    *,
    spec: DatasourceSpec,
    project_root: Path | None = None,
) -> DatasourceSourceLocation:
    path = datasource_path(spec.name, project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    func_name = require_profile_for_backend_type(spec.backend_type).authoring_func
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
    http_headers_env: dict[str, str] = {}
    for stem, env_var in spec.env_refs.items():
        if stem.startswith("http_header:"):
            http_headers_env[stem.removeprefix("http_header:")] = env_var
            continue
        kwargs[f"{stem}_env"] = env_var
    if http_headers_env:
        kwargs["http_headers_env"] = http_headers_env
    ai_context_call = _ai_context_literal(cast("AiContextIR", spec.ai_context))
    if extra_kwargs:
        kwargs["extra"] = extra_kwargs
    lines = [
        "import marivo.datasource as md",
        "import marivo.semantic as ms",
        "",
    ]
    constructor_line = len(lines) + 1
    lines.append(f"md.{func_name}(")
    for key, value in kwargs.items():
        lines.append(f"    {key}={_literal(value)},")
    if ai_context_call is not None:
        lines.append(f"    ai_context={ai_context_call},")
    lines.append(")")
    path.write_text("\n".join(lines) + "\n")
    return DatasourceSourceLocation(file=str(path.resolve()), line=constructor_line)


def load_all(project_root: Path | None = None) -> dict[str, DatasourceIR]:
    root = project_root or resolve_project_root()
    config = require_project_config(root)
    models_roots = (root / AUTHORED_DIR, *config.semantic_layer_paths)
    errors = _models_root_errors(models_roots)
    if errors:
        raise errors[0]
    result = _load_models_datasources(models_roots)
    if result.errors:
        raise result.errors[0]
    return {datasource.name: datasource for datasource in result.datasources}


def load_one(name: str, project_root: Path | None = None) -> DatasourceIR | None:
    return load_all(project_root).get(_storage_name(name))


def save_one(spec: DatasourceSpec, project_root: Path | None = None) -> DatasourceIR:
    loading_file = _current_source_loading_file()
    if loading_file is not None:
        constructor = require_profile_for_backend_type(spec.backend_type).authoring_func
        raise DatasourceLoadError(
            message=f"md.register() cannot persist datasource {spec.name!r} while loading model files.",
            expected="datasource constructors in declaration files; md.register() outside model loading",
            received=f"md.register() for datasource {spec.name!r} during model loading",
            location=str(loading_file),
            repair=repair(
                kind="reauthor",
                canonical_id="register",
                action=(
                    f"Remove the md.register(...) wrapper and call md.{constructor}(...) directly "
                    "in the datasource declaration file. Semantic files reference the datasource "
                    "through its ref. Run md.register() only in setup scripts outside model loading."
                ),
            ),
        )
    root = project_root or resolve_project_root()
    require_project_config(root)
    location = _write_datasource_file(
        spec=spec,
        project_root=root,
    )
    return spec.to_ir(location=location)


def delete_one(name: str, project_root: Path | None = None) -> bool:
    root = project_root or resolve_project_root()
    require_project_config(root)
    path = datasource_path(name, root)
    if not path.is_file():
        return False
    path.unlink()
    return True


def list_names(project_root: Path | None = None) -> list[str]:
    return sorted(load_all(project_root).keys())
