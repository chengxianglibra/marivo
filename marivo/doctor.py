"""Read-only environment and setup diagnostics for the marivo CLI."""

from __future__ import annotations

import ast
import os
import sqlite3
import stat
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Literal

from marivo import __version__
from marivo.config import (
    AUTHORED_DIR,
    DATASOURCES_DIR,
    PROJECT_MANIFEST,
    SEMANTIC_DIR,
    load_semantic_layer_paths,
)
from marivo.datasource.authoring import SENSITIVE_FIELD_STEMS
from marivo.datasource.engines import ENGINE_PROFILES, SUPPORTED_BACKEND_TYPES
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation
from marivo.datasource.secrets import conventional_env_var

DoctorStatus = Literal["ok", "warning", "fail", "skipped"]
ReportStatus = Literal["ok", "warning", "fail"]


@dataclass(frozen=True)
class DoctorCheck:
    id: str
    label: str
    status: DoctorStatus
    summary: str
    details: Mapping[str, object] | None = None
    fix: Sequence[str] = ()

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "summary": self.summary,
        }
        if self.details is not None:
            payload["details"] = dict(self.details)
        if self.fix != ():
            payload["fix"] = list(self.fix)
        return payload


@dataclass(frozen=True)
class DoctorSection:
    id: str
    label: str
    checks: Sequence[DoctorCheck] = ()

    @property
    def status(self) -> DoctorStatus:
        if any(check.status == "fail" for check in self.checks):
            return "fail"
        if any(check.status == "warning" for check in self.checks):
            return "warning"
        if self.checks and all(check.status == "skipped" for check in self.checks):
            return "skipped"
        return "ok"

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass(frozen=True)
class DoctorReport:
    status: ReportStatus
    project_root: str | None
    python_executable: str
    marivo_version: str
    marivo_package_path: str
    sections: Sequence[DoctorSection] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "project_root": self.project_root,
            "python_executable": self.python_executable,
            "marivo": {
                "version": self.marivo_version,
                "package_path": self.marivo_package_path,
            },
            "sections": [section.to_dict() for section in self.sections],
        }


@dataclass(frozen=True)
class DoctorOptions:
    project_root: str | Path | None = None
    format: Literal["text", "json"] = "text"
    fix_snap: bool = False
    semantic: bool = False
    connect: bool = False
    datasource: str | None = None


_BACKEND_IMPORT_PROBES: dict[str, tuple[str, ...]] = {
    backend_type: profile.required_modules for backend_type, profile in ENGINE_PROFILES.items()
}


@dataclass(frozen=True)
class _StaticDatasourceLoadResult:
    datasources: tuple[DatasourceIR, ...]
    diagnostics: tuple[DoctorCheck, ...]


@dataclass(frozen=True)
class _LayerPathInspection:
    roots: tuple[Path, ...]
    checks: tuple[DoctorCheck, ...]

    @property
    def ok(self) -> bool:
        return not any(check.status == "fail" for check in self.checks)


def status_from_checks(sections: Sequence[DoctorSection]) -> ReportStatus:
    if any(section.status == "fail" for section in sections):
        return "fail"
    if any(section.status == "warning" for section in sections):
        return "warning"
    return "ok"


def _section_summary(section: DoctorSection) -> str:
    failures = sum(1 for check in section.checks if check.status == "fail")
    warnings = sum(1 for check in section.checks if check.status == "warning")
    skipped = sum(1 for check in section.checks if check.status == "skipped")
    if failures:
        return f"{failures} failure" if failures == 1 else f"{failures} failures"
    if warnings:
        return f"{warnings} warning" if warnings == 1 else f"{warnings} warnings"
    if skipped and skipped == len(section.checks):
        return "skipped"
    return "ok"


def _fix_commands(report: DoctorReport) -> list[str]:
    commands: list[str] = []
    seen: set[str] = set()
    for section in report.sections:
        for check in section.checks:
            for command in check.fix:
                if command not in seen:
                    seen.add(command)
                    commands.append(command)
    return commands


def _json_safe_detail(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe_detail(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe_detail(item) for item in value]
    return str(value)


def _count_summary(count: int, singular: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {singular}{suffix}"


def render_text(report: DoctorReport) -> str:
    lines = [
        f"Marivo doctor: {report.status}",
        f"Python: {report.python_executable}",
        f"Marivo: {report.marivo_version} ({report.marivo_package_path})",
    ]
    if report.project_root is not None:
        lines.append(f"Project: {report.project_root}")
    lines.append("")
    for section in report.sections:
        lines.append(f"[{section.id}] {section.status} {_section_summary(section)}")
        if section.status != "ok":
            non_skipped = [c for c in section.checks if c.status != "skipped"]
            if len(non_skipped) > 1:
                for check in non_skipped:
                    lines.append(f"  {check.label}: {check.status} - {check.summary}")
    commands = _fix_commands(report)
    if commands:
        lines.append("")
        lines.append("Fix:")
        lines.extend(f"  {command}" for command in commands)
    return "\n".join(lines)


def render_fix_snap(report: DoctorReport) -> str:
    lines = [
        f"Marivo doctor fix snapshot: {report.status}",
        f"Python: {report.python_executable}",
    ]
    if report.project_root is not None:
        lines.append(f"Project: {report.project_root}")
    commands = _fix_commands(report)
    if commands:
        lines.append("")
        lines.append("Fix:")
        lines.extend(f"  {command}" for command in commands)
    else:
        lines.append("")
        lines.append("No fix commands suggested.")
    return "\n".join(lines)


def _package_path() -> str:
    import marivo

    return str(marivo.__file__ or "")


def _resolve_project_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value).resolve()
    env = os.environ.get("MARIVO_PROJECT_ROOT")
    if env:
        return Path(env).resolve()
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / PROJECT_MANIFEST).is_file():
            return candidate
    return current


def _installation_section() -> DoctorSection:
    checks = (
        DoctorCheck(
            id="installation.python",
            label="Python executable",
            status="ok",
            summary=sys.executable,
            details={"python_executable": sys.executable},
        ),
        DoctorCheck(
            id="installation.marivo",
            label="Marivo package",
            status="ok",
            summary=f"{__version__} at {_package_path()}",
            details={"version": __version__, "package_path": _package_path()},
        ),
    )
    return DoctorSection(id="installation", label="Installation", checks=checks)


def _inspect_layer_paths(root: Path) -> _LayerPathInspection:
    manifest = root / PROJECT_MANIFEST
    if not manifest.is_file():
        return _LayerPathInspection(roots=(), checks=())
    try:
        roots = load_semantic_layer_paths(root)
    except ValueError as exc:
        return _LayerPathInspection(
            roots=(),
            checks=(
                DoctorCheck(
                    id="project.semantic.layer_paths",
                    label="[semantic].layer_paths",
                    status="fail",
                    summary=str(exc),
                    details={"path": str(manifest)},
                    fix=("Fix marivo.toml [semantic].layer_paths, then run marivo doctor again",),
                ),
            ),
        )
    if not roots:
        return _LayerPathInspection(roots=(), checks=())

    checks: list[DoctorCheck] = []
    valid_roots: list[Path] = []
    local_models = (root / AUTHORED_DIR).resolve()
    seen: set[Path] = set()
    for index, models_root in enumerate(roots):
        check_id = f"project.semantic.layer_paths.{index}"
        details = {"path": str(models_root)}
        if models_root == local_models:
            checks.append(
                DoctorCheck(
                    id=check_id,
                    label=f"layer path {index}",
                    status="fail",
                    summary=(
                        "configured semantic layer models root duplicates the local "
                        f"project models root: {models_root}"
                    ),
                    details=details,
                    fix=("Remove the local models/ path from [semantic].layer_paths.",),
                )
            )
            continue
        if models_root in seen:
            checks.append(
                DoctorCheck(
                    id=check_id,
                    label=f"layer path {index}",
                    status="fail",
                    summary=f"configured semantic layer models root is duplicated: {models_root}",
                    details=details,
                    fix=("Keep each [semantic].layer_paths entry unique.",),
                )
            )
            continue
        seen.add(models_root)
        if not models_root.exists():
            checks.append(
                DoctorCheck(
                    id=check_id,
                    label=f"layer path {index}",
                    status="fail",
                    summary=f"configured semantic layer models root does not exist: {models_root}",
                    details=details,
                    fix=("Point [semantic].layer_paths at existing models/ directories.",),
                )
            )
            continue
        if not models_root.is_dir():
            checks.append(
                DoctorCheck(
                    id=check_id,
                    label=f"layer path {index}",
                    status="fail",
                    summary=(
                        f"configured semantic layer models root is not a directory: {models_root}"
                    ),
                    details=details,
                    fix=("Point [semantic].layer_paths at models/ directories.",),
                )
            )
            continue
        missing: list[str] = []
        if not (models_root / "datasources").is_dir():
            missing.append("datasources/")
        if not (models_root / "semantic").is_dir():
            missing.append("semantic/")
        if missing:
            checks.append(
                DoctorCheck(
                    id=check_id,
                    label=f"layer path {index}",
                    status="fail",
                    summary=(
                        f"configured semantic layer models root {models_root} is missing "
                        f"{', '.join(missing)}"
                    ),
                    details=details,
                    fix=(
                        "Create datasources/ and semantic/ under the configured models root, "
                        "or remove this layer path.",
                    ),
                )
            )
            continue
        valid_roots.append(models_root)
        checks.append(
            DoctorCheck(
                id=check_id,
                label=f"layer path {index}",
                status="ok",
                summary=f"{models_root} contains datasources/ and semantic/",
                details=details,
            )
        )
    return _LayerPathInspection(roots=tuple(valid_roots), checks=tuple(checks))


def _project_section(root: Path) -> DoctorSection:
    checks: list[DoctorCheck] = []
    manifest = root / PROJECT_MANIFEST
    layer_inspection = _inspect_layer_paths(root)
    if not root.exists():
        return DoctorSection(
            id="project",
            label="Project",
            checks=(
                DoctorCheck(
                    id="project.root",
                    label="Project root",
                    status="fail",
                    summary=f"project root does not exist: {root}",
                    details={"project_root": str(root)},
                    fix=(f"mkdir -p {root}", f"marivo doctor --project-root {root}"),
                ),
            ),
        )
    if not manifest.is_file():
        checks.append(
            DoctorCheck(
                id="project.marivo_toml",
                label="marivo.toml",
                status="fail",
                summary=f"marivo.toml was not found in {root}",
                details={"path": str(manifest)},
                fix=(
                    "cd <project-with-marivo.toml>",
                    "export MARIVO_PROJECT_ROOT=<project-with-marivo.toml>",
                    f"marivo doctor --project-root {root}",
                    "marivo init",
                ),
            )
        )
    else:
        try:
            with manifest.open("rb") as handle:
                parsed = tomllib.load(handle)
            project_table = parsed.get("project")
            name = project_table.get("name") if isinstance(project_table, dict) else None
            if isinstance(name, str) and name:
                checks.append(
                    DoctorCheck(
                        id="project.marivo_toml",
                        label="marivo.toml",
                        status="ok",
                        summary=f"project {name!r}",
                        details={"path": str(manifest), "name": name},
                    )
                )
            else:
                checks.append(
                    DoctorCheck(
                        id="project.marivo_toml",
                        label="marivo.toml",
                        status="fail",
                        summary="marivo.toml is missing [project].name",
                        details={"path": str(manifest)},
                        fix=('Add [project] name = "<project-name>" to marivo.toml',),
                    )
                )
        except tomllib.TOMLDecodeError as exc:
            checks.append(
                DoctorCheck(
                    id="project.marivo_toml",
                    label="marivo.toml",
                    status="fail",
                    summary=f"marivo.toml is invalid TOML: {exc}",
                    details={"path": str(manifest)},
                    fix=("Fix marivo.toml syntax, then run marivo doctor again",),
                )
            )
    checks.extend(layer_inspection.checks)
    using_valid_external_layers = layer_inspection.ok and len(layer_inspection.roots) > 0
    for check_id, label, path in (
        ("project.models", "models/", root / AUTHORED_DIR),
        ("project.datasources", "models/datasources/", root / DATASOURCES_DIR),
        ("project.semantic", "models/semantic/", root / SEMANTIC_DIR),
    ):
        if path.is_dir():
            status: DoctorStatus = "ok"
            summary = f"{path} exists"
        elif using_valid_external_layers:
            status = "ok"
            summary = f"{path} is missing; using configured semantic layer paths"
        else:
            status = "warning"
            summary = f"{path} is missing"
        checks.append(
            DoctorCheck(
                id=check_id,
                label=label,
                status=status,
                summary=summary,
                details={"path": str(path)},
            )
        )
    return DoctorSection(id="project", label="Project", checks=tuple(checks))


def _candidate_datasource_files(root: Path, only: str | None) -> tuple[Path, ...]:
    datasource_roots = [root / DATASOURCES_DIR]
    layer_inspection = _inspect_layer_paths(root)
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
    if func.attr not in _BACKEND_IMPORT_PROBES:
        return None
    return func.attr


def _static_literal(value: ast.AST) -> object:
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
    for keyword in call.keywords:
        if keyword.arg is None:
            return None, _datasource_parse_check(
                filepath,
                f"unsupported **kwargs in static datasource declaration for {backend_type}",
                line=call.lineno,
            )
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

    name = kwargs.pop("name", None)
    if not isinstance(name, str) or not name:
        return None, _datasource_parse_check(
            filepath,
            "datasource name must be a non-empty literal string for static inspection",
            line=call.lineno,
        )

    extra = kwargs.pop("extra", None)
    if extra is not None:
        if not isinstance(extra, dict) or not all(isinstance(key, str) for key in extra):
            return None, _datasource_parse_check(
                filepath,
                "datasource extra must be a literal dict with string keys for static inspection",
                line=call.lineno,
            )
        kwargs.update(extra)

    fields: dict[str, object] = {}
    env_refs: dict[str, str] = {}
    for key, value in kwargs.items():
        if key.endswith("_env"):
            if value is None:
                continue
            if not isinstance(value, str) or not value:
                return None, _datasource_parse_check(
                    filepath,
                    f"datasource field {key!r} must be a non-empty env var name",
                    line=call.lineno,
                )
            env_refs[key[: -len("_env")]] = value
            continue
        fields[key] = value

    return (
        DatasourceIR(
            semantic_id=name,
            name=name,
            backend_type=backend_type,
            fields=fields,
            env_refs=env_refs,
            ai_context=AiContextIR(),
            python_symbol=name,
            location=DatasourceSourceLocation(file=str(filepath), line=call.lineno),
        ),
        None,
    )


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


def _load_project_datasources(root: Path, only: str | None) -> _StaticDatasourceLoadResult:
    datasources: list[DatasourceIR] = []
    layer_inspection = _inspect_layer_paths(root)
    diagnostics: list[DoctorCheck] = [
        check for check in layer_inspection.checks if check.status == "fail"
    ]
    for filepath in _candidate_datasource_files(root, only):
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
    for module_name in _BACKEND_IMPORT_PROBES[backend_type]:
        try:
            import_module(module_name)
        except Exception:
            missing.append(module_name)
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
        summary=f"{backend_type} datasource configured",
        details={"datasource": datasource.name, "backend_type": backend_type},
    )


def _datasource_section(
    root: Path, only: str | None
) -> tuple[DoctorSection, tuple[DatasourceIR, ...]]:
    result = _load_project_datasources(root, only)
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


def _secrets_section(datasources: Sequence[DatasourceIR], *, project_root: Path) -> DoctorSection:
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
        for stem in sorted(SENSITIVE_FIELD_STEMS):
            if stem in datasource.fields or stem in datasource.env_refs:
                continue
            conventional = conventional_env_var(datasource.name, stem)
            if os.environ.get(conventional) or cache.get(conventional):
                checks.append(
                    DoctorCheck(
                        id=f"secret.conventional.{conventional}",
                        label=conventional,
                        status="ok",
                        summary=f"{conventional} is available as a conventional fallback",
                        details={"datasource": datasource.name, "field": stem},
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


def _semantic_section(root: Path) -> DoctorSection:
    fix_cmd = f"marivo doctor --project-root {root} --semantic --format json"
    try:
        from marivo.semantic.check import run_check

        payload = run_check(workspace_dir=root, readiness=True, format="json")
        readiness_by_domain = payload.get("readiness_by_domain")

        if isinstance(readiness_by_domain, dict) and readiness_by_domain:
            checks: list[DoctorCheck] = []
            for domain_name in sorted(readiness_by_domain.keys()):
                domain_payload = readiness_by_domain[domain_name]
                if not isinstance(domain_payload, dict):
                    continue
                domain_status = str(domain_payload.get("status", "ready"))
                domain_blockers = _json_safe_detail(
                    domain_payload.get("blockers")
                    if isinstance(domain_payload.get("blockers"), list)
                    else []
                )
                domain_warnings = _json_safe_detail(
                    domain_payload.get("warnings")
                    if isinstance(domain_payload.get("warnings"), list)
                    else []
                )
                blocker_count = len(domain_blockers) if isinstance(domain_blockers, list) else 0
                warning_count = len(domain_warnings) if isinstance(domain_warnings, list) else 0

                if domain_status == "blocked":
                    doctor_status: DoctorStatus = "fail"
                elif domain_status == "ready_with_warnings":
                    doctor_status = "warning"
                else:
                    doctor_status = "ok"

                summary_parts: list[str] = [domain_status]
                if blocker_count:
                    summary_parts.append(_count_summary(blocker_count, "blocker"))
                if warning_count:
                    summary_parts.append(_count_summary(warning_count, "warning"))
                summary = ", ".join(summary_parts)

                domain_fix: Sequence[str] = ()
                if doctor_status in ("fail", "warning"):
                    domain_fix = (fix_cmd,)

                checks.append(
                    DoctorCheck(
                        id=f"semantic.readiness.{domain_name}",
                        label=domain_name,
                        status=doctor_status,
                        summary=summary,
                        details={
                            "status": domain_status,
                            "blockers": domain_blockers,
                            "warnings": domain_warnings,
                        },
                        fix=domain_fix,
                    )
                )
            return DoctorSection(id="semantic", label="Semantic readiness", checks=tuple(checks))

        if isinstance(readiness_by_domain, dict) and not readiness_by_domain:
            return DoctorSection(
                id="semantic",
                label="Semantic readiness",
                checks=(
                    DoctorCheck(
                        id="semantic.readiness",
                        label="Semantic readiness",
                        status="skipped",
                        summary="no semantic domains loaded",
                    ),
                ),
            )

        status = str(payload.get("status"))
        errors = payload.get("errors")
        warnings = payload.get("warnings")
        readiness = payload.get("readiness")
        error_details = _json_safe_detail(errors if isinstance(errors, list) else [])
        warning_details = _json_safe_detail(warnings if isinstance(warnings, list) else [])
        details: dict[str, object] = {
            "semantic_status": status,
            "errors": error_details,
            "warnings": warning_details,
        }
        readiness_blockers: object = []
        readiness_warnings: object = []
        if isinstance(readiness, dict):
            readiness_blockers = _json_safe_detail(
                readiness.get("blockers") if isinstance(readiness.get("blockers"), list) else []
            )
            readiness_warnings = _json_safe_detail(
                readiness.get("warnings") if isinstance(readiness.get("warnings"), list) else []
            )
            details["readiness"] = {
                "status": str(readiness.get("status")),
                "blockers": readiness_blockers,
                "warnings": readiness_warnings,
            }
        error_count = len(error_details) if isinstance(error_details, list) else 0
        warning_count = len(warning_details) if isinstance(warning_details, list) else 0
        blocker_count = len(readiness_blockers) if isinstance(readiness_blockers, list) else 0
        readiness_warning_count = (
            len(readiness_warnings) if isinstance(readiness_warnings, list) else 0
        )
        if error_count or blocker_count or status in {"blocked", "errored"}:
            doctor_status = "fail"
        elif warning_count or readiness_warning_count or status == "ready_with_warnings":
            doctor_status = "warning"
        else:
            doctor_status = "ok"
        summary_parts = []
        if error_count:
            summary_parts.append(_count_summary(error_count, "load error"))
        if warning_count:
            summary_parts.append(_count_summary(warning_count, "load warning"))
        if blocker_count:
            summary_parts.append(_count_summary(blocker_count, "readiness blocker"))
        if readiness_warning_count:
            summary_parts.append(_count_summary(readiness_warning_count, "readiness warning"))
        summary = f"semantic status is {status}"
        if summary_parts:
            summary = f"{summary} ({', '.join(summary_parts)})"
        fix: Sequence[str] = ()
        if doctor_status == "fail":
            fix = (fix_cmd,)
        return DoctorSection(
            id="semantic",
            label="Semantic readiness",
            checks=(
                DoctorCheck(
                    id="semantic.readiness",
                    label="Semantic readiness",
                    status=doctor_status,
                    summary=summary,
                    details=details,
                    fix=fix,
                ),
            ),
        )
    except Exception as exc:
        return DoctorSection(
            id="semantic",
            label="Semantic readiness",
            checks=(
                DoctorCheck(
                    id="semantic.readiness",
                    label="Semantic readiness",
                    status="fail",
                    summary=f"{type(exc).__name__}: {exc}",
                    fix=(fix_cmd,),
                ),
            ),
        )


def _connect_section(datasources: Sequence[DatasourceIR], *, project_root: Path) -> DoctorSection:
    checks: list[DoctorCheck] = []
    from marivo.datasource import manage as datasource_manage

    for datasource in sorted(datasources, key=lambda item: item.name):
        result = datasource_manage.test_no_persist(
            datasource.name,
            project_root=project_root,
            include_semantic_layers=True,
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


def _state_section(root: Path) -> DoctorSection:
    analysis_dir = root / ".marivo" / "analysis"
    db_path = analysis_dir / "session_store.db"
    checks: list[DoctorCheck] = []
    if not analysis_dir.exists():
        checks.append(
            DoctorCheck(
                id="state.analysis_dir",
                label=".marivo/analysis",
                status="skipped",
                summary=f"{analysis_dir} does not exist",
            )
        )
        return DoctorSection(id="state", label="Analysis state", checks=tuple(checks))
    checks.append(
        DoctorCheck(
            id="state.analysis_dir",
            label=".marivo/analysis",
            status="ok",
            summary=f"{analysis_dir} exists",
        )
    )
    if not db_path.exists():
        checks.append(
            DoctorCheck(
                id="state.session_store",
                label="session_store.db",
                status="skipped",
                summary=f"{db_path} does not exist",
            )
        )
        return DoctorSection(id="state", label="Analysis state", checks=tuple(checks))
    try:
        uri = f"file:{db_path}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            names = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        expected = {"sessions", "runtime_state", "artifacts", "jobs"}
        missing = sorted(expected - names)
        if missing:
            checks.append(
                DoctorCheck(
                    id="state.session_store",
                    label="session_store.db",
                    status="fail",
                    summary=f"session store is missing tables: {', '.join(missing)}",
                    details={"path": str(db_path), "missing_tables": missing},
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    id="state.session_store",
                    label="session_store.db",
                    status="ok",
                    summary="existing analysis store is readable",
                    details={"path": str(db_path)},
                )
            )
    except Exception as exc:
        checks.append(
            DoctorCheck(
                id="state.session_store",
                label="session_store.db",
                status="fail",
                summary=f"could not read session store: {type(exc).__name__}: {exc}",
                details={"path": str(db_path)},
            )
        )
    return DoctorSection(id="state", label="Analysis state", checks=tuple(checks))


def base_report(
    sections: Sequence[DoctorSection], *, project_root: str | None = None
) -> DoctorReport:
    return DoctorReport(
        status=status_from_checks(sections),
        project_root=project_root,
        python_executable=sys.executable,
        marivo_version=__version__,
        marivo_package_path=_package_path(),
        sections=tuple(sections),
    )


def exit_code(report: DoctorReport) -> int:
    return 1 if report.status == "fail" else 0


def run_doctor(options: DoctorOptions | None = None) -> DoctorReport:
    opts = options or DoctorOptions()
    root = _resolve_project_root(opts.project_root)
    sections: list[DoctorSection] = [_installation_section(), _project_section(root)]
    datasource_section, datasources = _datasource_section(root, opts.datasource)
    sections.append(datasource_section)
    sections.append(_secrets_section(datasources, project_root=root))
    if opts.semantic:
        sections.append(_semantic_section(root))
    if opts.connect:
        sections.append(_connect_section(datasources, project_root=root))
    sections.append(_state_section(root))
    return base_report(sections, project_root=str(root))
