"""Static datasource declaration, credentials, and optional live diagnostics."""

from __future__ import annotations

import ast
import os
import stat
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from marivo._kernel.compat import tomllib
from marivo._kernel.config import DATASOURCES_DIR
from marivo.cli.doctor.environment import LayerPathInspection
from marivo.cli.doctor.report import DoctorCheck, DoctorSection, DoctorStatus
from marivo.datasource.errors import DatasourceFieldInvalidError
from marivo.datasource.spi import (
    DEFAULT_DATASOURCE_DESCRIPTION,
    DEFAULT_DATASOURCE_NAME,
    ENGINE_PROFILES,
    SUPPORTED_BACKEND_TYPES,
    ClickHouseSpec,
    DatasourceIR,
    DatasourceSourceLocation,
    DatasourceSpec,
    DuckDBSecret,
    DuckDBSpec,
    EnvRef,
    MySQLSpec,
    PostgresSpec,
    TrinoSpec,
    datasource_ir_from_spec,
    default_datasource,
    env,
    http_bearer_secret,
    http_headers_secret,
    s3_secret,
    test_no_persist,
)


@dataclass(frozen=True)
class _StaticDatasourceLoadResult:
    datasources: tuple[DatasourceIR, ...]
    diagnostics: tuple[DoctorCheck, ...]


def _candidate_datasource_files(
    root: Path,
    only: str | None,
    layer_inspection: LayerPathInspection,
) -> tuple[Path, ...]:
    datasource_roots = [root / DATASOURCES_DIR]
    if layer_inspection.ok:
        datasource_roots.extend(
            models_root / "datasources" for models_root in layer_inspection.roots
        )
    files: list[Path] = []
    for datasource_root in datasource_roots:
        if not datasource_root.exists() or not datasource_root.is_dir():
            continue
        files.extend(
            child
            for child in sorted(datasource_root.iterdir())
            if child.is_file() and child.suffix == ".py" and not child.name.startswith(".")
        )
    return tuple(files)


def _datasource_parse_check(
    filepath: Path,
    summary: str,
    *,
    line: int | None = None,
    status: DoctorStatus = "warning",
) -> DoctorCheck:
    details: dict[str, object] = {"path": str(filepath)}
    if line is not None:
        details["line"] = line
    return DoctorCheck(
        id=f"datasource.parse_error.{filepath.stem}",
        label=f"{filepath.name} datasource declaration",
        status=status,
        summary=summary,
        details=details,
    )


def _backend_call_name(call: ast.Call) -> str | None:
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    if not isinstance(func.value, ast.Name) or func.value.id != "md":
        return None
    if func.attr not in SUPPORTED_BACKEND_TYPES:
        return None
    return func.attr


def _static_literal(value: ast.AST) -> object:
    if isinstance(value, ast.Call):
        constructors: dict[str, Callable[..., EnvRef | DuckDBSecret]] = {
            "env": env,
            "s3_secret": s3_secret,
            "http_bearer_secret": http_bearer_secret,
            "http_headers_secret": http_headers_secret,
        }
        func = value.func
        if (
            not isinstance(func, ast.Attribute)
            or not isinstance(func.value, ast.Name)
            or func.value.id != "md"
            or func.attr not in constructors
        ):
            raise ValueError("Only md.env and secret constructors are supported")
        names = [keyword.arg for keyword in value.keywords]
        if None in names or len(names) != len(set(names)):
            raise ValueError("Expanded or duplicate keyword arguments are not supported")
        return constructors[func.attr](
            *[_static_literal(arg) for arg in value.args],
            **{
                keyword.arg: _static_literal(keyword.value)
                for keyword in value.keywords
                if keyword.arg is not None
            },
        )
    if isinstance(value, (ast.List, ast.Tuple)):
        return [_static_literal(item) for item in value.elts]
    if isinstance(value, ast.Dict):
        if any(key is None for key in value.keys):
            raise ValueError("Expanded mappings are not supported")
        return {
            ast.literal_eval(key): _static_literal(item)
            for key, item in zip(value.keys, value.values, strict=True)
            if key is not None
        }
    return ast.literal_eval(value)


def _static_datasource_from_call(
    call: ast.Call,
    *,
    backend_type: str,
    filepath: Path,
) -> tuple[DatasourceIR | None, DoctorCheck | None]:
    if call.args:
        return None, _datasource_parse_check(
            filepath,
            f"unsupported positional arguments in static datasource declaration for {backend_type}",
            line=call.lineno,
        )

    kwargs: dict[str, object] = {}
    seen: set[str] = set()
    for keyword in call.keywords:
        if keyword.arg is None or keyword.arg in seen:
            return None, _datasource_parse_check(
                filepath,
                f"unsupported expanded or duplicate keyword arguments for {backend_type}",
                line=call.lineno,
            )
        seen.add(keyword.arg)
        if keyword.arg == "ai_context":
            continue
        try:
            kwargs[keyword.arg] = _static_literal(keyword.value)
        except Exception:
            return None, _datasource_parse_check(
                filepath,
                f"keyword {keyword.arg!r} must be a literal value for static datasource inspection",
                line=getattr(keyword.value, "lineno", call.lineno),
            )

    constructors: dict[str, Callable[..., DatasourceSpec]] = {
        "duckdb": DuckDBSpec,
        "trino": TrinoSpec,
        "mysql": MySQLSpec,
        "postgres": PostgresSpec,
        "clickhouse": ClickHouseSpec,
    }
    try:
        spec = constructors[backend_type](**kwargs)
    except (TypeError, ValueError, DatasourceFieldInvalidError) as error:
        return None, _datasource_parse_check(
            filepath,
            f"Invalid datasource declaration: {error}. Use user/password/auth=md.env('NAME') or secrets=[md.s3_secret(...), md.http_bearer_secret(...), md.http_headers_secret(...)].",
            line=call.lineno,
        )
    return datasource_ir_from_spec(
        spec, location=DatasourceSourceLocation(file=str(filepath), line=call.lineno)
    ), None


def _static_datasources_from_file(filepath: Path) -> _StaticDatasourceLoadResult:
    try:
        source = filepath.read_text(encoding="utf-8")
    except Exception as exc:
        return _StaticDatasourceLoadResult(
            datasources=(),
            diagnostics=(
                _datasource_parse_check(
                    filepath,
                    f"could not read datasource file: {type(exc).__name__}: {exc}",
                    status="fail",
                ),
            ),
        )
    try:
        module = ast.parse(source, filename=str(filepath))
    except SyntaxError as exc:
        return _StaticDatasourceLoadResult(
            datasources=(),
            diagnostics=(
                _datasource_parse_check(
                    filepath,
                    f"could not statically parse datasource file: {exc.msg}",
                    line=exc.lineno,
                    status="fail",
                ),
            ),
        )

    datasources: list[DatasourceIR] = []
    diagnostics: list[DoctorCheck] = []
    for statement in module.body:
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            continue
        call = statement.value
        backend_type = _backend_call_name(call)
        if backend_type is None:
            continue
        datasource, diagnostic = _static_datasource_from_call(
            call, backend_type=backend_type, filepath=filepath
        )
        if diagnostic is not None:
            diagnostics.append(diagnostic)
            continue
        assert datasource is not None
        datasources.append(datasource)

    if datasources or diagnostics:
        return _StaticDatasourceLoadResult(
            datasources=tuple(datasources), diagnostics=tuple(diagnostics)
        )
    return _StaticDatasourceLoadResult(
        datasources=(),
        diagnostics=(
            _datasource_parse_check(
                filepath,
                "no supported top-level md.<backend>(...) datasource declaration found for static inspection",
            ),
        ),
    )


def _load_project_datasources(
    root: Path,
    only: str | None,
    layer_inspection: LayerPathInspection,
) -> _StaticDatasourceLoadResult:
    datasources = [default_datasource()] if only in (None, DEFAULT_DATASOURCE_NAME) else []
    diagnostics: list[DoctorCheck] = []
    for filepath in _candidate_datasource_files(root, only, layer_inspection):
        result = _static_datasources_from_file(filepath)
        if only is None:
            datasources.extend(result.datasources)
            diagnostics.extend(result.diagnostics)
            continue
        matching = tuple(ds for ds in result.datasources if ds.name == only)
        if not matching:
            continue
        datasources.extend(matching)
        diagnostics.extend(result.diagnostics)

    unique_datasources: list[DatasourceIR] = []
    seen: dict[str, DatasourceIR] = {}
    for datasource in datasources:
        existing = seen.get(datasource.name)
        if existing is not None:
            diagnostics.append(
                DoctorCheck(
                    id=f"datasource.{datasource.name}.duplicate",
                    label=f"{datasource.name} datasource",
                    status="fail",
                    summary=(
                        f"Duplicate datasource name: {datasource.name!r}. "
                        f"First declaration: {existing.location.file}. "
                        f"Conflicting declaration: {datasource.location.file}."
                    ),
                    details={
                        "datasource": datasource.name,
                        "first": existing.location.file,
                        "second": datasource.location.file,
                    },
                )
            )
            continue
        seen[datasource.name] = datasource
        unique_datasources.append(datasource)
    return _StaticDatasourceLoadResult(
        datasources=tuple(unique_datasources),
        diagnostics=tuple(diagnostics),
    )


def _backend_extra_check(datasource: DatasourceIR) -> DoctorCheck:
    backend_type = datasource.backend_type
    if backend_type not in SUPPORTED_BACKEND_TYPES:
        return DoctorCheck(
            id=f"datasource.{datasource.name}",
            label=f"{datasource.name} datasource",
            status="fail",
            summary=f"unsupported backend type {backend_type!r}",
            details={"datasource": datasource.name, "backend_type": backend_type},
        )
    missing: list[str] = []
    for requirement in ENGINE_PROFILES[backend_type].required_modules:
        try:
            requirement.load()
        except Exception:
            missing.append(requirement.name)
    if missing:
        return DoctorCheck(
            id=f"datasource.{datasource.name}",
            label=f"{datasource.name} datasource",
            status="warning",
            summary=f"missing backend extra for {backend_type}",
            details={
                "datasource": datasource.name,
                "backend_type": backend_type,
                "missing": missing,
            },
            fix=(f'{sys.executable} -m pip install "marivo[{backend_type}]"',),
        )
    return DoctorCheck(
        id=f"datasource.{datasource.name}",
        label=f"{datasource.name} datasource",
        status="ok",
        summary=(
            DEFAULT_DATASOURCE_DESCRIPTION
            if datasource.name == DEFAULT_DATASOURCE_NAME
            else f"{backend_type} datasource configured"
        ),
        details={"datasource": datasource.name, "backend_type": backend_type},
    )


def datasource_section(
    root: Path,
    only: str | None,
    layer_inspection: LayerPathInspection,
) -> tuple[DoctorSection, tuple[DatasourceIR, ...]]:
    result = _load_project_datasources(root, only, layer_inspection)
    datasources = result.datasources
    if only is not None:
        datasources = tuple(ds for ds in datasources if ds.name == only)
    checks: list[DoctorCheck] = list(result.diagnostics)
    if only is not None and not datasources:
        checks.append(
            DoctorCheck(
                id=f"datasource.{only}.missing",
                label=f"{only} datasource",
                status="fail",
                summary=f"datasource {only!r} is not configured",
                fix=(f"marivo doctor --project-root {root}",),
            )
        )
    for datasource in sorted(datasources, key=lambda item: item.name):
        checks.append(_backend_extra_check(datasource))
    if not checks:
        checks.append(
            DoctorCheck(
                id="datasource.none",
                label="Datasource declarations",
                status="skipped",
                summary="no datasource declarations found",
            )
        )
    return DoctorSection(id="datasources", label="Datasources", checks=tuple(checks)), datasources


def _secret_cache_values(path: Path) -> tuple[dict[str, str], DoctorCheck | None]:
    if not path.exists():
        return {}, DoctorCheck(
            id="secret.cache_permissions",
            label="Secret cache permissions",
            status="skipped",
            summary=f"{path} does not exist",
        )
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        return {}, DoctorCheck(
            id="secret.cache_permissions",
            label="Secret cache permissions",
            status="fail",
            summary=f"{path} has insecure permissions {oct(mode)}",
            details={"path": str(path), "mode": oct(mode)},
            fix=("chmod 600 ~/.marivo/secrets.toml",),
        )
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except Exception as exc:
        return {}, DoctorCheck(
            id="secret.cache_permissions",
            label="Secret cache permissions",
            status="fail",
            summary=f"could not read {path}: {type(exc).__name__}: {exc}",
            details={"path": str(path)},
        )
    return {
        str(key): value for key, value in raw.items() if isinstance(value, str) and value
    }, DoctorCheck(
        id="secret.cache_permissions",
        label="Secret cache permissions",
        status="ok",
        summary=f"{path} is readable with safe permissions",
        details={"path": str(path)},
    )


def secrets_section(datasources: Sequence[DatasourceIR], *, project_root: Path) -> DoctorSection:
    cache_path = Path.home() / ".marivo" / "secrets.toml"
    cache, cache_check = _secret_cache_values(cache_path)
    checks: list[DoctorCheck] = []
    if cache_check is not None:
        checks.append(cache_check)
    for datasource in sorted(datasources, key=lambda item: item.name):
        for field_name, env_var in sorted(datasource.env_refs.items()):
            check_id = f"secret.env.{datasource.name}.{field_name}.{env_var}"
            env_value = os.environ.get(env_var)
            cache_value = cache.get(env_var)
            if env_value:
                checks.append(
                    DoctorCheck(
                        id=check_id,
                        label=env_var,
                        status="ok",
                        summary=f"{env_var} is set in the environment",
                        details={
                            "datasource": datasource.name,
                            "field": field_name,
                            "provider": "env",
                        },
                    )
                )
            elif cache_value:
                checks.append(
                    DoctorCheck(
                        id=check_id,
                        label=env_var,
                        status="ok",
                        summary=f"{env_var} is present in ~/.marivo/secrets.toml",
                        details={
                            "datasource": datasource.name,
                            "field": field_name,
                            "provider": "cache",
                        },
                    )
                )
            else:
                checks.append(
                    DoctorCheck(
                        id=check_id,
                        label=env_var,
                        status="fail",
                        summary=f"{env_var} is not set and not cached",
                        details={
                            "datasource": datasource.name,
                            "field": field_name,
                            "env_var": env_var,
                        },
                        fix=(
                            f'export {env_var}="secret_value"',
                            f"marivo doctor --project-root {project_root} --datasource {datasource.name} --connect",
                        ),
                    )
                )
    if not checks:
        checks.append(
            DoctorCheck(
                id="secret.none",
                label="Datasource secrets",
                status="skipped",
                summary="no datasource secret references found",
            )
        )
    return DoctorSection(id="secrets", label="Secrets", checks=tuple(checks))


def connect_section(datasources: Sequence[DatasourceIR], *, project_root: Path) -> DoctorSection:
    checks: list[DoctorCheck] = []
    for datasource in sorted(datasources, key=lambda item: item.name):
        result = test_no_persist(
            datasource.name,
            project_root=project_root,
        )
        latency = "n/a" if result.latency_ms is None else f"{result.latency_ms}ms"
        if result.ok:
            checks.append(
                DoctorCheck(
                    id=f"connect.{datasource.name}",
                    label=f"{datasource.name} live connection",
                    status="ok",
                    summary=f"live connection ok in {latency}",
                    details={"datasource": datasource.name, "latency_ms": result.latency_ms},
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    id=f"connect.{datasource.name}",
                    label=f"{datasource.name} live connection",
                    status="fail",
                    summary=(
                        result.repair.action
                        if result.repair is not None
                        else "live connection failed"
                    ),
                    details={"datasource": datasource.name, "latency_ms": result.latency_ms},
                    fix=(
                        f"marivo doctor --project-root {project_root} --datasource {datasource.name} --connect",
                    ),
                )
            )
    if not checks:
        checks.append(
            DoctorCheck(
                id="connect.none",
                label="Live datasource connection",
                status="skipped",
                summary="no datasource declarations found",
            )
        )
    return DoctorSection(id="connect", label="Live connectivity", checks=tuple(checks))
