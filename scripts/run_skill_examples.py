#!/usr/bin/env python3
"""Walk retained marivo/skills examples and validate them."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import os
import py_compile
import runpy
import subprocess
import sys
import traceback
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType

SKILL_DIRS = (
    "marivo/skills/marivo-semantic",
    "marivo/skills/marivo-analysis",
)
EXAMPLE_TIMEOUT_SECONDS = 30
SKILL_MD_MAX_LINES = 600
_EXPECTED_PREFIX = "Expected output:"
_TEMPLATE_MARKER = "# marivo-example: template"
_TEMPLATE_REQUIRED_SNIPPETS = (
    "marivo.semantic",
    "marivo.analysis",
    "ms.load()",
    "catalog.metrics.ids()",
    "mv.session.get_or_create(",
    "default_calendar=",
    "session.observe(",
    "session.catalog.get(",
)
_TEMPLATE_FORBIDDEN_SNIPPETS = (
    "_fixtures",
    "_support",
    "ensure_loaded(",
    "mv.session.active(",
)
_PUBLIC_EXAMPLE_FORBIDDEN_SNIPPETS = (
    "tempfile",
    "os.chdir",
    "_fixtures",
    "_support",
    "ensure_loaded(",
    "CREATE TABLE",
    "INSERT INTO",
    "ibis.duckdb.connect",
    "md.duckdb(",
    "models/datasources",
)


@dataclass
class Failure:
    file: Path
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class _ExampleExecutionContext:
    cwd: Path
    env: dict[str, str]


@contextlib.contextmanager
def _null_example_context(example: Path) -> Iterator[_ExampleExecutionContext]:
    yield _ExampleExecutionContext(cwd=example.parent, env={})


def _iter_skill_dirs(root: Path) -> list[Path]:
    return [root / skill_dir for skill_dir in SKILL_DIRS if (root / skill_dir).is_dir()]


def _iter_example_files(examples_dir: Path) -> list[Path]:
    return sorted(
        p
        for p in examples_dir.iterdir()
        if p.is_file() and p.suffix == ".py" and not p.name.startswith("_")
    )


def _skill_name_for_example(example: Path) -> str | None:
    try:
        return example.parent.parent.parent.name
    except IndexError:
        return None


def _support_file_for_examples(examples_dir: Path) -> Path | None:
    skill_name = examples_dir.parent.parent.name
    support_name_by_skill = {
        "marivo-analysis": "analysis_project.py",
    }
    expected_name = support_name_by_skill.get(skill_name)
    if expected_name is not None:
        support_file = examples_dir / "_support" / expected_name
        if support_file.is_file():
            return support_file
    legacy_support_file = examples_dir / "_support" / "example_project.py"
    if legacy_support_file.is_file():
        return legacy_support_file
    return None


def _load_support_module(examples_dir: Path) -> ModuleType | None:
    support_file = _support_file_for_examples(examples_dir)
    if support_file is None:
        return None
    module_name = f"_marivo_example_support_{abs(hash(support_file))}"
    spec = importlib.util.spec_from_file_location(module_name, support_file)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _execution_context_for_example(example: Path) -> Iterator[_ExampleExecutionContext]:
    if _is_template_example(example):
        with _null_example_context(example) as context:
            yield context
        return

    skill_name = _skill_name_for_example(example)
    support_module = _load_support_module(example.parent)
    if support_module is None:
        with _null_example_context(example) as context:
            yield context
        return

    factory_name_by_skill = {
        "marivo-analysis": "analysis_examples_project",
    }
    factory_name = factory_name_by_skill.get(skill_name)
    if factory_name is None or not hasattr(support_module, factory_name):
        with _null_example_context(example) as context:
            yield context
        return

    factory = getattr(support_module, factory_name)
    with factory() as context:
        cwd = Path(context.root)
        env = getattr(context, "env", {})
        yield _ExampleExecutionContext(cwd=cwd, env=dict(env))


def _execute_example(
    example: Path,
    *,
    repo_root: Path | None = None,
    cwd: Path | None = None,
    env_overrides: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run an example as a subprocess; return (returncode, stdout, stderr)."""
    example = example.resolve()
    env = os.environ.copy()
    root = str((repo_root or Path.cwd()).resolve())
    example_dir = str(example.parent)
    existing = env.get("PYTHONPATH")
    path_parts = [root, example_dir]
    if existing:
        path_parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(path_parts)
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, str(example)],
        cwd=cwd or example.parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=EXAMPLE_TIMEOUT_SECONDS,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _execute_example_in_process(
    example: Path,
    *,
    repo_root: Path | None = None,
    cwd: Path | None = None,
    env_overrides: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run an example in the current process via runpy.

    Much faster than subprocess because it avoids per-example Python
    startup and marivo import overhead (~1.6s per example).
    """
    old_cwd = Path.cwd()
    old_env = os.environ.copy()
    old_stdout = sys.stdout
    example_dir = str(example.parent)
    root = str((repo_root or old_cwd).resolve())
    old_path = list(sys.path)
    run_cwd = cwd or example.parent

    # When running via subprocess from example.parent, Python adds CWD
    # to sys.path[0] and the helper sets PYTHONPATH to repo root. runpy.run_path
    # does neither, so inject both paths for equivalent import resolution.
    sys.path.insert(0, root)
    sys.path.insert(0, example_dir)
    if env_overrides:
        os.environ.update(env_overrides)
    os.chdir(run_cwd)

    try:
        import marivo.analysis.session as session_attach

        session_attach._reset_process_state()
        stdout_buf = io.StringIO()
        sys.stdout = stdout_buf
        try:
            runpy.run_path(str(example), run_name="__main__")
            rc = 0
            stderr = ""
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
            stderr = ""
        except Exception:
            rc = 1
            stderr = traceback.format_exc()
        stdout = stdout_buf.getvalue()
    finally:
        sys.stdout = old_stdout
        os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_env)
        sys.path[:] = old_path

    return rc, stdout, stderr


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


def _check_public_example_text(example: Path) -> Failure | None:
    if "_support" in example.parts or "_fixtures" in example.parts:
        return None
    text = example.read_text()
    forbidden = [snippet for snippet in _PUBLIC_EXAMPLE_FORBIDDEN_SNIPPETS if snippet in text]
    if forbidden:
        reason = "forbidden public example reference"
        if _TEMPLATE_MARKER in text:
            reason = "invalid template; forbidden public example reference"
        return Failure(
            example,
            reason,
            ", ".join(repr(snippet) for snippet in forbidden),
        )
    return None


def _check_example(
    example: Path,
    *,
    in_process: bool = False,
    repo_root: Path | None = None,
) -> Failure | None:
    example = example.resolve()
    resolved_repo_root = (repo_root or Path.cwd()).resolve()
    public_failure = _check_public_example_text(example)
    if public_failure is not None:
        return public_failure
    if _is_template_example(example):
        return _check_template_example(example)

    try:
        with _execution_context_for_example(example) as context:
            if in_process:
                if context.cwd == example.parent and not context.env:
                    rc, stdout, stderr = _execute_example_in_process(
                        example, repo_root=resolved_repo_root
                    )
                else:
                    rc, stdout, stderr = _execute_example_in_process(
                        example,
                        repo_root=resolved_repo_root,
                        cwd=context.cwd,
                        env_overrides=context.env,
                    )
            else:
                if context.cwd == example.parent and not context.env:
                    rc, stdout, stderr = _execute_example(example, repo_root=resolved_repo_root)
                else:
                    rc, stdout, stderr = _execute_example(
                        example,
                        repo_root=resolved_repo_root,
                        cwd=context.cwd,
                        env_overrides=context.env,
                    )
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
    parser.add_argument(
        "--in-process",
        action="store_true",
        dest="in_process",
        help="Run examples in-process instead of subprocesses. "
        "This is the default because each example reuses the same Python "
        "runtime and marivo imports.",
    )
    parser.add_argument(
        "--subprocess",
        action="store_false",
        dest="in_process",
        help="Run each example in a fresh Python subprocess. Slower, but useful "
        "when debugging process-global state leaks.",
    )
    parser.set_defaults(in_process=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()

    failures: list[Failure] = []
    for skill_dir in _iter_skill_dirs(root):
        md_failure = _check_skill_md(skill_dir)
        if md_failure is not None:
            failures.append(md_failure)
        examples_dir = skill_dir / "references" / "examples"
        if not examples_dir.is_dir():
            # marivo-semantic and marivo-analysis are single-file boundary
            # kernels with no packaged examples.  Only SKILL.md presence is
            # required; absent examples directories are valid.
            continue
        examples = _iter_example_files(examples_dir)
        for example in examples:
            failure = _check_example(example, in_process=args.in_process, repo_root=root)
            if failure is not None:
                failures.append(failure)

    for failure in failures:
        _print_failure(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
