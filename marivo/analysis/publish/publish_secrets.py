"""Scan a staged report package for obvious secrets before publishing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_AWS_KEY_RE = re.compile(r"AKIA[0-9A-Z]{16}")
_SECRET_KV_RE = re.compile(
    r"(?i)(password|passwd|secret|api[_-]?key|token|access[_-]?key|aws_secret_access_key)"
    r"\s*[=:]\s*[\"'][^\"']{4,}[\"']"
)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")

_TEXT_SUFFIXES: frozenset[str] = frozenset(
    {".json", ".py", ".html", ".md", ".txt", ".toml", ".csv", ".yaml", ".yml"}
)


@dataclass(frozen=True)
class SecretScanIssue:
    """A single secret-like finding in a packaged file."""

    rel_path: str
    lineno: int
    check: str
    message: str


def _scan_text(rel_path: str, text: str) -> list[SecretScanIssue]:
    issues: list[SecretScanIssue] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _AWS_KEY_RE.search(line):
            issues.append(
                SecretScanIssue(rel_path, lineno, "aws_key", "possible AWS access key id")
            )
        if _SECRET_KV_RE.search(line):
            issues.append(
                SecretScanIssue(
                    rel_path, lineno, "secret_kv", "possible hardcoded secret assignment"
                )
            )
        if _PRIVATE_KEY_RE.search(line):
            issues.append(
                SecretScanIssue(rel_path, lineno, "private_key", "possible private key block")
            )
    return issues


def scan_package_for_secrets(package_dir: str | Path) -> tuple[SecretScanIssue, ...]:
    """Scan packaged text files and return any secret-like findings.

    Only known text suffixes are scanned; binary/unknown files (e.g. parquet
    frame snapshots, images) are skipped. Files that cannot be decoded as UTF-8
    are skipped rather than treated as findings.
    """
    package_root = Path(package_dir)
    issues: list[SecretScanIssue] = []
    for path in sorted(package_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        rel = path.relative_to(package_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        issues.extend(_scan_text(rel, text))
    return tuple(issues)
