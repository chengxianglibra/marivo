#!/usr/bin/env python3
"""Walk retained marivo-skills examples and validate them."""

from __future__ import annotations

import argparse
import os
import py_compile
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

SKILL_DIRS = (
    "marivo-skills/marivo-semantic",
    "marivo-skills/marivo-analysis",
)
EXAMPLE_TIMEOUT_SECONDS = 30
SKILL_MD_MAX_LINES = 600
_EXPECTED_PREFIX = "Expected output:"
_TEMPLATE_MARKER = "# marivo-example: template"
_TEMPLATE_REQUIRED_SNIPPETS = (
    "marivo.semantic",
    "marivo.analysis",
    "ms.find_project()",
    "project.load()",
    "project.list_metrics()",
    "mv.session.get_or_create(",
    "timezone=",
    "default_calendar=",
    "session.observe(",
    "mv.MetricRef(",
)
_TEMPLATE_FORBIDDEN_SNIPPETS = (
    "_fixtures",
    "ensure_loaded(",
    "mv.session.active(",
)


@dataclass
class Failure:
    file: Path
    reason: str
    detail: str = ""


def _iter_skill_dirs(root: Path) -> list[Path]:
    return [root / skill_dir for skill_dir in SKILL_DIRS if (root / skill_dir).is_dir()]


def _iter_example_files(examples_dir: Path) -> list[Path]:
    return sorted(
        p
        for p in examples_dir.iterdir()
        if p.is_file() and p.suffix == ".py" and not p.name.startswith("_")
    )


def _execute_example(example: Path) -> tuple[int, str, str]:
    """Run an example as a subprocess; return (returncode, stdout, stderr)."""
    env = os.environ.copy()
    root = str(Path.cwd())
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = root if not existing else f"{root}{os.pathsep}{existing}"
    proc = subprocess.run(
        [sys.executable, example.name],
        cwd=example.parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=EXAMPLE_TIMEOUT_SECONDS,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _partial_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _expected_keywords(example: Path) -> list[str]:
    """Extract non-blank lines from the `Expected output:` block of a pitfall file.

    Looks inside the file's leading triple-quoted docstring for a line containing
    `Expected output:`. Returns each subsequent non-empty line (stripped) until
    the docstring ends or a blank line ends the block.
    """
    text = example.read_text()
    if not text.startswith('"""'):
        return []
    end = text.find('"""', 3)
    if end < 0:
        return []
    doc = text[3:end]
    in_block = False
    keywords: list[str] = []
    for raw in doc.splitlines():
        line = raw.strip()
        if _EXPECTED_PREFIX in line:
            in_block = True
            continue
        if in_block:
            if not line:
                break
            keywords.append(line)
    return keywords


def _is_template_example(example: Path) -> bool:
    return _TEMPLATE_MARKER in example.read_text()


def _check_template_example(example: Path) -> Failure | None:
    text = example.read_text()
    try:
        with TemporaryDirectory(prefix="marivo-example-pyc-") as cache_dir:
            py_compile.compile(
                str(example),
                cfile=str(Path(cache_dir) / f"{example.stem}.pyc"),
                doraise=True,
            )
    except py_compile.PyCompileError as exc:
        return Failure(example, "invalid template", f"syntax error: {exc.msg}")

    missing = [snippet for snippet in _TEMPLATE_REQUIRED_SNIPPETS if snippet not in text]
    forbidden = [snippet for snippet in _TEMPLATE_FORBIDDEN_SNIPPETS if snippet in text]
    if missing or forbidden:
        detail_parts: list[str] = []
        if missing:
            detail_parts.append("missing required snippets: " + ", ".join(repr(s) for s in missing))
        if forbidden:
            detail_parts.append(
                "forbidden snippets present: " + ", ".join(repr(s) for s in forbidden)
            )
        return Failure(example, "invalid template", "; ".join(detail_parts))
    return None


def _check_example(example: Path) -> Failure | None:
    if _is_template_example(example):
        return _check_template_example(example)

    try:
        rc, stdout, stderr = _execute_example(example)
    except subprocess.TimeoutExpired as exc:
        return Failure(
            example,
            "timeout",
            f"exceeded {EXAMPLE_TIMEOUT_SECONDS}s; "
            f"partial stdout: {_partial_output(exc.stdout).strip()}; "
            f"partial stderr: {_partial_output(exc.stderr).strip()}",
        )
    if rc != 0:
        return Failure(example, "non-zero exit", f"exit={rc}\nstderr: {stderr.strip()}")
    if example.name.startswith("99_pitfall_"):
        keywords = _expected_keywords(example)
        if not keywords:
            return Failure(
                example,
                "missing Expected output block",
                "99_pitfall_*.py files must declare an Expected output: block "
                "inside their leading docstring.",
            )
        missing = [keyword for keyword in keywords if keyword not in stdout]
        if missing:
            missing_detail = "; ".join(repr(keyword) for keyword in missing)
            return Failure(
                example,
                "missing pitfall keyword",
                f"stdout did not contain: {missing_detail}",
            )
    if not stdout.strip():
        return Failure(example, "empty stdout", "example produced no stdout")
    return None


def _check_skill_md(skill_dir: Path) -> Failure | None:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return Failure(skill_md, "missing SKILL.md", "")
    lines = skill_md.read_text().splitlines()
    if len(lines) > SKILL_MD_MAX_LINES:
        return Failure(
            skill_md,
            "SKILL.md exceeds cap",
            f"{len(lines)} lines > {SKILL_MD_MAX_LINES}; split content into "
            "references/*.md and link from SKILL.md.",
        )
    return None


def _print_failure(failure: Failure) -> None:
    print(
        f"[examples-check] FAILED: {failure.file}\n"
        f"  Reason: {failure.reason}\n"
        f"  Detail: {failure.detail}\n"
        "  Fix:\n"
        f"    1. Run: cd {failure.file.parent} && {sys.executable} {failure.file.name}\n"
        "    2. Update the example to match the current SDK, or roll back the SDK change\n"
        "    3. If SKILL.md references this template, sync the See-also / decision tree",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    failures: list[Failure] = []
    for skill_dir in _iter_skill_dirs(args.root):
        md_failure = _check_skill_md(skill_dir)
        if md_failure is not None:
            failures.append(md_failure)
        examples_dir = skill_dir / "references" / "examples"
        if not examples_dir.is_dir():
            failures.append(Failure(examples_dir, "missing examples dir", ""))
            continue
        for example in _iter_example_files(examples_dir):
            failure = _check_example(example)
            if failure is not None:
                failures.append(failure)

    for failure in failures:
        _print_failure(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
