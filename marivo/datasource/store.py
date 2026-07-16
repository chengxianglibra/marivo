"""Project-level datasource file storage."""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any, cast

from marivo.config import AUTHORED_DIR, DATASOURCES_DIR, load_semantic_layer_paths
from marivo.datasource.authoring import DatasourceSpec, _storage_name
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.errors import (
    DatasourceDuplicateError,
    DatasourceLoadError,
    DatasourceMissingError,
    repair,
)
from marivo.datasource.ir import AiContextIR, DatasourceIR
from marivo.datasource.loader import load_datasources
from marivo.datasource.secrets import conventional_env_var
from marivo.project import resolve_project_root


def datasource_dir(project_root: Path | None = None) -> Path:
    root = project_root or resolve_project_root()
    return root / DATASOURCES_DIR


def datasource_path(name: str, project_root: Path | None = None) -> Path:
    return datasource_dir(project_root) / f"{_storage_name(name)}.py"


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
) -> Path:
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
    # Only write explicit *_env overrides; conventional names are implied.
    for stem, env_var in spec.env_refs.items():
        if env_var == conventional_env_var(spec.name, stem):
            continue
        kwargs[f"{stem}_env"] = env_var
    ai_context_call = _ai_context_literal(cast("AiContextIR", spec.ai_context))
    if extra_kwargs:
        kwargs["extra"] = extra_kwargs
    lines = [
        "import marivo.datasource as md",
        "import marivo.semantic as ms",
        "",
        f"md.{func_name}(",
    ]
    for key, value in kwargs.items():
        lines.append(f"    {key}={_literal(value)},")
    if ai_context_call is not None:
        lines.append(f"    ai_context={ai_context_call},")
    lines.append(")")
    path.write_text("\n".join(lines) + "\n")
    return path


def load_all(project_root: Path | None = None) -> dict[str, DatasourceIR]:
    result = load_datasources(datasource_dir(project_root))
    if result.errors:
        raise result.errors[0]
    return {datasource.name: datasource for datasource in result.datasources}


def load_one(name: str, project_root: Path | None = None) -> DatasourceIR | None:
    return load_all(project_root).get(_storage_name(name))


def _layered_models_roots(project_root: Path | None = None) -> tuple[Path, ...]:
    root = project_root or resolve_project_root()
    local_models = root / AUTHORED_DIR
    try:
        external_roots = load_semantic_layer_paths(root)
    except ValueError as exc:
        raise DatasourceLoadError(
            message=str(exc),
            expected="a valid semantic layer configuration",
            received=str(exc),
            location=str(root / "marivo.toml"),
            repair=repair(
                kind="configure",
                canonical_id="load",
                action="Fix the semantic layer configuration and reload datasources.",
            ),
        ) from exc
    errors: list[str] = []
    seen_external: set[Path] = set()
    for external_root in external_roots:
        if external_root == local_models.resolve():
            errors.append(
                "Configured semantic layer models root duplicates the local "
                f"project models root: {external_root}"
            )
            continue
        if external_root in seen_external:
            errors.append(
                f"Configured semantic layer models root is listed more than once: {external_root}"
            )
            continue
        seen_external.add(external_root)
        if not external_root.exists():
            errors.append(f"Configured semantic layer models root does not exist: {external_root}")
            continue
        if not external_root.is_dir():
            errors.append(
                f"Configured semantic layer models root is not a directory: {external_root}"
            )
            continue
        if not (external_root / "datasources").is_dir():
            errors.append(
                "Configured semantic layer models root is missing datasources/: "
                f"{external_root / 'datasources'}"
            )
        if not (external_root / "semantic").is_dir():
            errors.append(
                "Configured semantic layer models root is missing semantic/: "
                f"{external_root / 'semantic'}"
            )
    if errors:
        reason = "; ".join(errors)
        raise DatasourceLoadError(
            message=reason,
            expected="valid distinct semantic layer model roots",
            received=reason,
            location=str(root / "marivo.toml"),
            repair=repair(
                kind="configure",
                canonical_id="load",
                action="Fix the configured semantic layer roots and reload datasources.",
            ),
        )
    return (local_models, *external_roots)


def load_all_layered(project_root: Path | None = None) -> dict[str, DatasourceIR]:
    datasources: dict[str, DatasourceIR] = {}
    for models_root in _layered_models_roots(project_root):
        result = load_datasources(models_root / "datasources")
        if result.errors:
            raise result.errors[0]
        for datasource in result.datasources:
            existing = datasources.get(datasource.name)
            if existing is not None:
                first = existing.location.file
                second = datasource.location.file
                raise DatasourceDuplicateError(
                    message=(
                        f"Duplicate datasource name: {datasource.name!r}. "
                        f"First declaration: {first}. Conflicting declaration: {second}."
                    ),
                    expected="a unique datasource name across semantic layers",
                    received=datasource.name,
                    location=second,
                    repair=repair(
                        kind="reauthor",
                        canonical_id="load",
                        action="Rename or remove one conflicting datasource declaration.",
                    ),
                )
            datasources[datasource.name] = datasource
    return datasources


def load_one_layered(name: str, project_root: Path | None = None) -> DatasourceIR | None:
    return load_all_layered(project_root).get(_storage_name(name))


def save_one(spec: DatasourceSpec, project_root: Path | None = None) -> DatasourceIR:
    _write_datasource_file(
        spec=spec,
        project_root=project_root,
    )
    datasource = load_one(spec.name, project_root)
    if datasource is None:
        raise DatasourceMissingError(
            message=f"datasource {spec.name!r} was not written",
            expected="a persisted datasource declaration",
            received=spec.name,
            location="models/datasources/",
            repair=repair(
                kind="register",
                canonical_id="register",
                action="Register the datasource again.",
                candidates=tuple(list_names(project_root)),
            ),
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


def list_names_layered(project_root: Path | None = None) -> list[str]:
    return sorted(load_all_layered(project_root).keys())
