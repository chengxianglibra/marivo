"""Installation, manifest, and agent skill diagnostics."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from marivo._kernel.compat import tomllib
from marivo._kernel.config import (
    AGENTS_SKILLS_DIR,
    AUTHORED_DIR,
    CLAUDE_SKILLS_DIR,
    CODEX_SKILLS_DIR,
    DATASOURCES_DIR,
    PACKAGED_SKILLS,
    PROJECT_MANIFEST,
    SEMANTIC_DIR,
    load_project_config,
)
from marivo._kernel.environment import environment_identity
from marivo.cli.doctor.report import DoctorCheck, DoctorSection, DoctorStatus


@dataclass(frozen=True)
class LayerPathInspection:
    roots: tuple[Path, ...]
    checks: tuple[DoctorCheck, ...]

    @property
    def ok(self) -> bool:
        return not any(check.status == "fail" for check in self.checks)


def resolve_project_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value).resolve()
    env = os.environ.get("MARIVO_PROJECT_ROOT")
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve()


def installation_section() -> DoctorSection:
    identity = environment_identity()
    executable = identity["python_executable"]
    version = identity["marivo_version"]
    package_path = identity["package_path"]
    checks = (
        DoctorCheck(
            id="installation.python",
            label="Python executable",
            status="ok",
            summary=executable,
            details={"python_executable": executable},
        ),
        DoctorCheck(
            id="installation.marivo",
            label="Marivo package",
            status="ok",
            summary=f"{version} at {package_path}",
            details={"version": version, "package_path": package_path},
        ),
    )
    return DoctorSection(id="installation", label="Installation", checks=checks)


def _inspect_layer_paths(root: Path, roots: Sequence[Path]) -> LayerPathInspection:
    if not roots:
        return LayerPathInspection(roots=(), checks=())

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
    return LayerPathInspection(roots=tuple(valid_roots), checks=tuple(checks))


def project_section(root: Path) -> tuple[DoctorSection, LayerPathInspection]:
    checks: list[DoctorCheck] = []
    manifest = root / PROJECT_MANIFEST
    if not root.exists():
        layer_inspection = LayerPathInspection(roots=(), checks=())
        return (
            DoctorSection(
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
            ),
            layer_inspection,
        )
    try:
        config = load_project_config(root)
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        checks.append(
            DoctorCheck(
                id="project.marivo_toml",
                label="marivo.toml",
                status="fail",
                summary=f"marivo.toml is invalid: {exc}",
                details={"path": str(manifest)},
                fix=("Fix marivo.toml, then run marivo doctor again",),
            )
        )
        layer_inspection = LayerPathInspection(roots=(), checks=())
    else:
        layer_inspection = _inspect_layer_paths(root, config.semantic_layer_paths)
        details: dict[str, object] = {
            "path": str(manifest),
            "name": config.name,
            "semantic_layer_paths": [str(path) for path in config.semantic_layer_paths],
            "telemetry_enabled": config.telemetry_enabled,
        }
        if manifest.is_file():
            checks.append(
                DoctorCheck(
                    id="project.marivo_toml",
                    label="marivo.toml",
                    status="ok",
                    summary=f"project {config.name!r}",
                    details=details,
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    id="project.marivo_toml",
                    label="marivo.toml",
                    status="info",
                    summary=(
                        f"{manifest} is missing; using defaults "
                        f"name={config.name!r}, semantic.layer_paths=[], telemetry.enabled='on'"
                    ),
                    details=details,
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
        elif check_id == "project.models":
            status = "info"
            summary = f"{path} is missing; local model declarations are empty"
        elif check_id == "project.datasources":
            status = "info"
            summary = f"{path} is missing; no local datasources are declared"
        else:
            status = "info"
            summary = f"{path} is missing; semantic modeling is pending"
        checks.append(
            DoctorCheck(
                id=check_id,
                label=label,
                status=status,
                summary=summary,
                details={"path": str(path)},
            )
        )
    return DoctorSection(id="project", label="Project", checks=tuple(checks)), layer_inspection


def skills_section(root: Path) -> DoctorSection:
    checks: list[DoctorCheck] = []
    for agent_label, agent_dir in (
        ("Agents", AGENTS_SKILLS_DIR),
        ("Claude", CLAUDE_SKILLS_DIR),
        ("Codex", CODEX_SKILLS_DIR),
    ):
        for skill_name in PACKAGED_SKILLS:
            path = root / agent_dir / skill_name
            check_id = f"skills.{agent_label.lower()}.{skill_name}"
            details = {"path": str(path)}
            if not path.exists() and not path.is_symlink():
                checks.append(
                    DoctorCheck(
                        id=check_id,
                        label=f"{agent_label} {skill_name}",
                        status="skipped",
                        summary="skill is not installed",
                        details=details,
                        fix=(f"marivo init --project-root {root}",),
                    )
                )
                continue
            try:
                resolved = path.resolve(strict=True)
            except OSError as exc:
                checks.append(
                    DoctorCheck(
                        id=check_id,
                        label=f"{agent_label} {skill_name}",
                        status="fail",
                        summary=f"skill path is broken: {exc}",
                        details=details,
                        fix=(f"marivo init --project-root {root}",),
                    )
                )
                continue
            if not resolved.is_dir() or not (resolved / "SKILL.md").is_file():
                checks.append(
                    DoctorCheck(
                        id=check_id,
                        label=f"{agent_label} {skill_name}",
                        status="fail",
                        summary="skill path does not contain SKILL.md",
                        details={**details, "resolved_path": str(resolved)},
                        fix=(f"marivo init --project-root {root}",),
                    )
                )
                continue
            checks.append(
                DoctorCheck(
                    id=check_id,
                    label=f"{agent_label} {skill_name}",
                    status="ok",
                    summary="skill is available",
                    details={**details, "resolved_path": str(resolved)},
                )
            )
    return DoctorSection(id="skills", label="Agent skills", checks=tuple(checks))
