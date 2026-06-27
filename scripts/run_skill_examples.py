#!/usr/bin/env python3
"""Walk retained marivo/skills examples and validate them."""

from __future__ import annotations

import argparse
import ast
import io
import os
import py_compile
import runpy
import subprocess
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

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
    'catalog.list(kind="metric").ids()',
    "mv.session.get_or_create(",
    "default_calendar=",
    "session.observe(",
    "session.catalog.get(",
)
_TEMPLATE_FORBIDDEN_SNIPPETS = (
    "_fixtures",
    "ensure_loaded(",
    "mv.session.active(",
)
_SEMANTIC_EXAMPLE_NAMES = ("01_datasource.py", "02_semantic_model.py")
_SEMANTIC_DATASOURCE_REQUIRED_CALLS = (
    "md.help",
    "md.test",
    "md.discover_entity",
    "md.discover_dimensions",
    "md.discover_time_dimensions",
    "md.discover_measures",
    "md.discover_dimension_values",
)
_SEMANTIC_MODEL_REQUIRED_CALLS = (
    "ms.measure_column",
    "ms.aggregate",
    "ms.relationship",
    "ms.metric",
    "ms.ratio",
    "ms.weighted_average",
    "ms.linear",
    "ms.verify_object",
    "ms.readiness",
)
_SEMANTIC_MODEL_REQUIRED_FEATURES = ("@ms.metric(root_entity=orders)",)
_SEMANTIC_EXAMPLE_FORBIDDEN_REFERENCES = (
    "md.inspect_columns",
    "md.inspect_table",
    "md.probe_join_keys",
    "project.assess_authoring(",
    "ms.AuthoringSourceInput(",
)
_SEMANTIC_EXAMPLE_FORBIDDEN_NAMES = ("judgment_targets",)


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


def _execute_example_in_process(example: Path) -> tuple[int, str, str]:
    """Run an example in the current process via runpy.

    Much faster than subprocess because it avoids per-example Python
    startup and marivo import overhead (~1.6s per example).
    """
    old_cwd = Path.cwd()
    example_dir = str(example.parent)
    repo_root = str(old_cwd)
    old_path = list(sys.path)

    # When running via subprocess from example.parent, Python adds CWD
    # to sys.path[0] and the helper sets PYTHONPATH to repo root. runpy.run_path
    # does neither, so inject both paths for equivalent import resolution.
    sys.path.insert(0, repo_root)
    sys.path.insert(0, example_dir)
    os.chdir(example.parent)

    try:
        import marivo.analysis.session as session_attach

        session_attach._reset_process_state()
        stdout_buf = io.StringIO()
        old_stdout = sys.stdout
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
        sys.stdout = old_stdout
    finally:
        os.chdir(old_cwd)
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


def _attribute_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _attribute_name(node.value)
        if owner is None:
            return None
        return f"{owner}.{node.attr}"
    return None


@dataclass(frozen=True)
class _SemanticExampleSource:
    calls: frozenset[str]
    attributes: frozenset[str]
    names: frozenset[str]
    metric_decorator_has_root_entity_orders: bool


def _embedded_source_trees(tree: ast.Module, *, filename: str) -> list[ast.Module]:
    trees: list[ast.Module] = []
    for node in ast.walk(tree):
        value: ast.AST | None = None
        if isinstance(node, ast.Assign | ast.AnnAssign):
            value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        try:
            trees.append(ast.parse(value.value, filename=filename))
        except SyntaxError:
            continue
    return trees


def _semantic_example_source(example: Path) -> _SemanticExampleSource | Failure:
    try:
        tree = ast.parse(example.read_text(), filename=str(example))
    except SyntaxError as exc:
        return Failure(example, "semantic example content", f"syntax error: {exc.msg}")

    trees = [tree, *_embedded_source_trees(tree, filename=str(example))]
    calls: set[str] = set()
    attributes: set[str] = set()
    names: set[str] = set()
    metric_decorator_has_root_entity_orders = False

    for source_tree in trees:
        for node in ast.walk(source_tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            if isinstance(node, ast.Attribute):
                attr_name = _attribute_name(node)
                if attr_name is not None:
                    attributes.add(attr_name)
            if isinstance(node, ast.Call):
                call_name = _attribute_name(node.func)
                if call_name is not None:
                    calls.add(call_name)

        for node in ast.walk(source_tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if _attribute_name(decorator.func) != "ms.metric":
                    continue
                for keyword in decorator.keywords:
                    if (
                        keyword.arg == "root_entity"
                        and isinstance(keyword.value, ast.Name)
                        and keyword.value.id == "orders"
                    ):
                        metric_decorator_has_root_entity_orders = True

    return _SemanticExampleSource(
        calls=frozenset(calls),
        attributes=frozenset(attributes),
        names=frozenset(names),
        metric_decorator_has_root_entity_orders=metric_decorator_has_root_entity_orders,
    )


def _check_semantic_example_contract(examples_dir: Path, examples: list[Path]) -> list[Failure]:
    failures: list[Failure] = []
    expected = set(_SEMANTIC_EXAMPLE_NAMES)
    actual = {example.name for example in examples}
    if actual != expected:
        failures.append(
            Failure(
                examples_dir,
                "semantic example contract",
                "expected exactly "
                + ", ".join(_SEMANTIC_EXAMPLE_NAMES)
                + "; found "
                + ", ".join(sorted(actual)),
            )
        )

    required_by_name = {
        "01_datasource.py": _SEMANTIC_DATASOURCE_REQUIRED_CALLS,
        "02_semantic_model.py": _SEMANTIC_MODEL_REQUIRED_CALLS,
    }
    for example in examples:
        source = _semantic_example_source(example)
        if isinstance(source, Failure):
            failures.append(source)
            continue
        missing = [
            call for call in required_by_name.get(example.name, ()) if call not in source.calls
        ]
        if (
            example.name == "02_semantic_model.py"
            and not source.metric_decorator_has_root_entity_orders
        ):
            missing.extend(_SEMANTIC_MODEL_REQUIRED_FEATURES)
        forbidden = [
            reference
            for reference in _SEMANTIC_EXAMPLE_FORBIDDEN_REFERENCES
            if reference.removesuffix("(") in source.attributes
        ]
        forbidden.extend(name for name in _SEMANTIC_EXAMPLE_FORBIDDEN_NAMES if name in source.names)
        if missing or forbidden:
            detail_parts: list[str] = []
            if missing:
                detail_parts.append(
                    "missing required calls: " + ", ".join(repr(s) for s in missing)
                )
            if forbidden:
                detail_parts.append(
                    "forbidden executable references present: "
                    + ", ".join(repr(s) for s in forbidden)
                )
            failures.append(Failure(example, "semantic example content", "; ".join(detail_parts)))
    return failures


def _check_example(example: Path, *, in_process: bool = False) -> Failure | None:
    if _is_template_example(example):
        return _check_template_example(example)

    try:
        if in_process:
            rc, stdout, stderr = _execute_example_in_process(example)
        else:
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

    failures: list[Failure] = []
    for skill_dir in _iter_skill_dirs(args.root):
        md_failure = _check_skill_md(skill_dir)
        if md_failure is not None:
            failures.append(md_failure)
        examples_dir = skill_dir / "references" / "examples"
        if not examples_dir.is_dir():
            failures.append(Failure(examples_dir, "missing examples dir", ""))
            continue
        examples = _iter_example_files(examples_dir)
        if skill_dir.name == "marivo-semantic":
            failures.extend(_check_semantic_example_contract(examples_dir, examples))
        for example in examples:
            failure = _check_example(example, in_process=args.in_process)
            if failure is not None:
                failures.append(failure)

    for failure in failures:
        _print_failure(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
