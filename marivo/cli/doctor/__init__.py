"""Read-only Marivo setup diagnostics for the CLI."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from marivo._kernel.environment import environment_identity
from marivo.cli.doctor.analysis import state_section
from marivo.cli.doctor.datasources import connect_section, datasource_section, secrets_section
from marivo.cli.doctor.environment import (
    installation_section,
    project_section,
    resolve_project_root,
    skills_section,
)
from marivo.cli.doctor.render import render_fix_snap as render_fix_snap
from marivo.cli.doctor.render import render_text as render_text
from marivo.cli.doctor.report import DoctorCheck as DoctorCheck
from marivo.cli.doctor.report import DoctorReport as DoctorReport
from marivo.cli.doctor.report import DoctorSection as DoctorSection
from marivo.cli.doctor.report import status_from_checks as status_from_checks
from marivo.cli.doctor.semantic import semantic_section


@dataclass(frozen=True)
class DoctorOptions:
    project_root: str | Path | None = None
    format: Literal["text", "json"] = "text"
    fix_snap: bool = False
    semantic: bool = False
    connect: bool = False
    datasource: str | None = None


def base_report(
    sections: Sequence[DoctorSection], *, project_root: str | None = None
) -> DoctorReport:
    identity = environment_identity()
    return DoctorReport(
        status=status_from_checks(sections),
        project_root=project_root,
        python_executable=identity["python_executable"],
        marivo_version=identity["marivo_version"],
        marivo_package_path=identity["package_path"],
        sections=tuple(sections),
    )


def exit_code(report: DoctorReport) -> int:
    return 1 if report.status == "fail" else 0


def run_doctor(options: DoctorOptions | None = None) -> DoctorReport:
    opts = options or DoctorOptions()
    root = resolve_project_root(opts.project_root)
    project, layer_inspection = project_section(root)
    sections: list[DoctorSection] = [
        installation_section(),
        project,
        skills_section(root),
    ]
    datasource_checks, datasources = datasource_section(root, opts.datasource, layer_inspection)
    sections.append(datasource_checks)
    sections.append(secrets_section(datasources, project_root=root))
    if opts.semantic:
        sections.append(semantic_section(root))
    if opts.connect:
        sections.append(connect_section(datasources, project_root=root))
    sections.append(state_section(root))
    return base_report(sections, project_root=str(root))
