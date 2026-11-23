"""Immutable diagnostic checks, sections, and report schema."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

DoctorStatus = Literal["ok", "info", "warning", "fail", "skipped"]
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
        if any(check.status == "info" for check in self.checks):
            return "info"
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


def status_from_checks(sections: Sequence[DoctorSection]) -> ReportStatus:
    if any(section.status == "fail" for section in sections):
        return "fail"
    if any(section.status == "warning" for section in sections):
        return "warning"
    return "ok"


def count_summary(count: int, singular: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {singular}{suffix}"
