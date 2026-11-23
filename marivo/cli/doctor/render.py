"""Terminal rendering of captured doctor diagnostics."""

from collections.abc import Mapping, Sequence

from marivo.cli.doctor.report import DoctorReport, DoctorSection, count_summary


def _section_summary(section: DoctorSection) -> str:
    failures = sum(1 for check in section.checks if check.status == "fail")
    warnings = sum(1 for check in section.checks if check.status == "warning")
    infos = sum(1 for check in section.checks if check.status == "info")
    skipped = sum(1 for check in section.checks if check.status == "skipped")
    if failures:
        return f"{failures} failure" if failures == 1 else f"{failures} failures"
    if warnings:
        return f"{warnings} warning" if warnings == 1 else f"{warnings} warnings"
    if infos:
        return count_summary(infos, "informational note")
    advisories = 0
    for check in section.checks:
        details = check.details
        if not isinstance(details, Mapping):
            continue
        readiness_details = details.get("readiness")
        containers = (details, readiness_details)
        for container in containers:
            if not isinstance(container, Mapping):
                continue
            issues = container.get("warnings")
            if isinstance(issues, Sequence) and not isinstance(issues, str | bytes | bytearray):
                advisories += sum(
                    1
                    for issue in issues
                    if isinstance(issue, Mapping) and issue.get("severity") == "advisory"
                )
    if advisories:
        return count_summary(advisories, "advisory")
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
