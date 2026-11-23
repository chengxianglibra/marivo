"""Semantic load and readiness diagnostics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from marivo.cli.doctor.report import DoctorCheck, DoctorSection, DoctorStatus, count_summary
from marivo.semantic import check as semantic_check


def _json_safe_detail(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe_detail(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe_detail(item) for item in value]
    return str(value)


def _issue_count(value: object, severity: str) -> int:
    """Count structured issue severity without promoting malformed diagnostics."""
    if not isinstance(value, list):
        return 0
    return sum(
        1 for issue in value if isinstance(issue, dict) and issue.get("severity") == severity
    )


def _domain_readiness(readiness_by_domain: Mapping[str, object], fix_cmd: str) -> DoctorSection:
    if not readiness_by_domain:
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
        warning_count = _issue_count(domain_warnings, "warning")
        advisory_count = _issue_count(domain_warnings, "advisory")

        if domain_status == "blocked":
            doctor_status: DoctorStatus = "fail"
        elif domain_status == "ready_with_warnings":
            doctor_status = "warning"
        else:
            doctor_status = "ok"

        summary_parts: list[str] = [domain_status]
        if blocker_count:
            summary_parts.append(count_summary(blocker_count, "blocker"))
        if warning_count:
            summary_parts.append(count_summary(warning_count, "warning"))
        if advisory_count:
            summary_parts.append(count_summary(advisory_count, "advisory"))
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


def _project_readiness(payload: Mapping[str, object], fix_cmd: str) -> DoctorSection:
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
    readiness_warning_count = _issue_count(readiness_warnings, "warning")
    readiness_advisory_count = _issue_count(readiness_warnings, "advisory")
    doctor_status: DoctorStatus
    if error_count or blocker_count or status in {"blocked", "errored"}:
        doctor_status = "fail"
    elif warning_count or readiness_warning_count or status == "ready_with_warnings":
        doctor_status = "warning"
    else:
        doctor_status = "ok"
    summary_parts = []
    if error_count:
        summary_parts.append(count_summary(error_count, "load error"))
    if warning_count:
        summary_parts.append(count_summary(warning_count, "load warning"))
    if blocker_count:
        summary_parts.append(count_summary(blocker_count, "readiness blocker"))
    if readiness_warning_count:
        summary_parts.append(count_summary(readiness_warning_count, "readiness warning"))
    if readiness_advisory_count:
        summary_parts.append(count_summary(readiness_advisory_count, "readiness advisory"))
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


def semantic_section(root: Path) -> DoctorSection:
    fix_cmd = f"marivo doctor --project-root {root} --semantic --format json"
    try:
        payload = semantic_check.run_check(workspace_dir=root, readiness=True)
        readiness_by_domain = payload.get("readiness_by_domain")
        if isinstance(readiness_by_domain, dict):
            return _domain_readiness(readiness_by_domain, fix_cmd)
        return _project_readiness(payload, fix_cmd)
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
