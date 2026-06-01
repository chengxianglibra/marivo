"""Regression tests for semantic agent authoring guidance."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_run_skill_examples() -> ModuleType:
    name = "_marivo_run_skill_examples"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "scripts" / "run_skill_examples.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text()


_EXAMPLE_PARAMS = [
    pytest.param(example, id=f"{example.parent.parent.parent.name}/{example.name}")
    for skill_dir in _load_run_skill_examples()._iter_skill_dirs(REPO_ROOT)
    for example in _load_run_skill_examples()._iter_example_files(
        skill_dir / "references" / "examples"
    )
]


def test_semantic_skill_points_to_standard_metadata_api() -> None:
    skill = _read("marivo-skills/marivo-semantic/SKILL.md")
    workflow = _read("marivo-skills/marivo-semantic/references/authoring-workflow.md")
    evidence = _read("marivo-skills/marivo-semantic/references/evidence.md")

    assert "mv.datasources.inspect_table(...)" in skill
    assert "mv.datasources.inspect_table(" in workflow
    assert "Table metadata evidence" in evidence
    assert "table.schema()` returns types but not comments" in skill
    assert "target preview APIs until they exist" not in skill


def test_semantic_skill_documents_trino_datasource_and_inspection() -> None:
    skill = _read("marivo-skills/marivo-semantic/SKILL.md")
    workflow = _read("marivo-skills/marivo-semantic/references/authoring-workflow.md")
    datasource = _read("marivo-skills/marivo-semantic/references/datasource.md")

    combined = "\n".join((skill, workflow, datasource))
    assert 'backend_type="trino"' in combined
    assert "client_tags" in combined
    assert "user_env" in combined
    assert 'table="orders", database="sales_mart"' in combined
    assert 'backend.table("orders", database="sales_mart")' in combined
    assert 'database="sales_mart"' in combined
    assert "backend.list_tables(database=" in combined
    assert "backend.list_schemas()" in combined
    assert "schema` is optional" in datasource
    assert "catalog.schema.table" not in combined
    assert "does not accept `database=`" not in combined
    assert "do not pass `database=`" not in combined
    assert "FDN" not in combined
    assert "mv.datasources.all()" in combined
    assert "mv.datasources.list()" not in combined


def test_design_spec_marks_remaining_phases_implemented() -> None:
    spec = _read("docs/specs/semantic/agent-semantic-layer-authoring-design.md")

    assert "| Table metadata/comments | `mv.datasources.inspect_table(...)` | same |" in spec
    assert "### Phase 4: Metadata API\n\nImplemented:" in spec
    assert "### Phase 5: Agent Automation Tightening\n\nImplemented:" in spec


def test_semantic_skill_examples_cover_phase5_cases() -> None:
    examples_dir = REPO_ROOT / "marivo-skills" / "marivo-semantic" / "references" / "examples"
    expected = {
        "05_inspect_table_metadata.py",
        "06_readiness_requires_preview.py",
        "07_readiness_unverified_metric.py",
        "08_readiness_parity_drift.py",
        "09_ambiguous_time_axis_prompt.py",
    }

    assert expected.issubset({path.name for path in examples_dir.glob("*.py")})


@pytest.mark.parametrize("example", _EXAMPLE_PARAMS)
def test_semantic_skill_example_executes(example: Path) -> None:
    run_skill_examples = _load_run_skill_examples()
    old_cwd = Path.cwd()
    os.chdir(REPO_ROOT)
    try:
        failure = run_skill_examples._check_example(example, in_process=True)
    finally:
        os.chdir(old_cwd)

    assert failure is None, f"{failure.reason}: {failure.detail}" if failure else None


def test_semantic_skill_md_caps_respected() -> None:
    run_skill_examples = _load_run_skill_examples()
    failures = [
        run_skill_examples._check_skill_md(skill_dir)
        for skill_dir in run_skill_examples._iter_skill_dirs(REPO_ROOT)
    ]
    failures = [f for f in failures if f is not None]
    assert not failures, [f"{f.reason}: {f.detail}" for f in failures]
