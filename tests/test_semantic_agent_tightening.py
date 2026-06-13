"""Regression tests for semantic agent authoring guidance."""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

import marivo.semantic as ms

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


def _extract_call_snippets(text: str, call_name: str) -> tuple[str, ...]:
    snippets: list[str] = []
    start = 0
    while True:
        call_start = text.find(call_name, start)
        if call_start == -1:
            return tuple(snippets)

        open_paren = text.find("(", call_start + len(call_name))
        if open_paren == -1:
            return tuple(snippets)

        depth = 0
        for index in range(open_paren, len(text)):
            char = text[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    snippets.append(text[call_start : index + 1])
                    start = index + 1
                    break
        else:
            return tuple(snippets)


def _top_level_call_keyword_names(snippet: str) -> tuple[str, ...]:
    parsed = ast.parse(snippet)
    expr = parsed.body[0]
    assert isinstance(expr, ast.Expr)
    assert isinstance(expr.value, ast.Call)
    return tuple(keyword.arg for keyword in expr.value.keywords if keyword.arg is not None)


_EXAMPLE_PARAMS = [
    pytest.param(example, id=f"{example.parent.parent.parent.name}/{example.name}")
    for skill_dir in _load_run_skill_examples()._iter_skill_dirs(REPO_ROOT)
    for example in _load_run_skill_examples()._iter_example_files(
        skill_dir / "references" / "examples"
    )
]


def test_semantic_skill_points_to_standard_metadata_api() -> None:
    skill = _read("marivo-skills/marivo-semantic/SKILL.md")
    workflow = _read("marivo-skills/marivo-semantic/references/workflow.md")
    datasource = _read("marivo-skills/marivo-semantic/references/datasource.md")
    evidence = _read("marivo-skills/marivo-semantic/references/evidence-and-ledger.md")

    assert "md.inspect_table" in datasource
    assert "md.inspect_columns" in datasource
    assert "prepare_entity" in workflow
    assert "ScanScope" in workflow
    assert "bind_datasource_access" not in workflow
    assert "project.assess_authoring(" not in workflow
    assert "AuthoringQuestion" in evidence
    assert "authoring_abandoned" in workflow


def test_semantic_skill_uses_assess_authoring_not_next_checks() -> None:
    public_paths = [
        "marivo-skills/marivo-semantic/SKILL.md",
        "marivo-skills/marivo-semantic/references/workflow.md",
        "marivo-skills/marivo-semantic/references/evidence-and-ledger.md",
        "marivo-skills/marivo-semantic/references/closeout.md",
        "marivo-skills/marivo-semantic/references/pitfalls.md",
        "marivo-skills/marivo-semantic/references/examples/02_source_evidence_to_check.py",
        "marivo-skills/marivo-semantic/references/examples/03_closeout_readiness_richness.py",
    ]
    combined = "\n".join(_read(path) for path in public_paths)
    closeout = "\n".join(
        _read(path)
        for path in [
            "marivo-skills/marivo-semantic/references/closeout.md",
            "marivo-skills/marivo-semantic/references/examples/03_closeout_readiness_richness.py",
        ]
    )

    assert "project.assess_authoring(" not in combined
    assert "ms.AuthoringSourceInput(" not in combined
    assert "next_checks" not in combined
    assert "needs_evidence" not in combined
    assert "project.check_authoring_inputs(" not in combined
    assert "check_authoring_inputs" not in combined
    assert "project.readiness(" in closeout


def test_superseded_specs_point_to_authoring_pipeline_design() -> None:
    superseded_paths = [
        "docs/specs/semantic/skill-semantic-layer-authoring-design.md",
        "docs/specs/semantic/agent-semantic-layer-authoring-design.md",
    ]
    for path in superseded_paths:
        spec = _read(path)
        assert "docs/specs/semantic/stepwise-authoring-design.md" in spec
        assert "superseded" in spec.lower()
        assert "NextCheck" not in spec
        assert "next_checks" not in spec
        assert "needs_evidence" not in spec
        assert "project.check_authoring_inputs(" not in spec


def test_semantic_ai_context_help_describes_handoff_not_check_input() -> None:
    evidence = _read("marivo-skills/marivo-semantic/references/evidence-and-ledger.md")
    from marivo.introspection.surface import render as surface_render

    help_mod = __import__(ms.help.__module__, fromlist=["_surface"])
    # AiContext folds into a family in the top-level index; its summary is
    # reached via describe (single-symbol render), not the directory listing.
    ai_context = cast("dict[str, Any]", surface_render(help_mod._surface(), "AiContext", "json"))
    ai_context_summary = cast("str", ai_context["summary"])

    assert "agent-facing" in ai_context_summary.lower() or "handoff" in ai_context_summary.lower()


def test_semantic_skill_documents_trino_datasource_and_inspection() -> None:
    skill = _read("marivo-skills/marivo-semantic/SKILL.md")
    workflow = _read("marivo-skills/marivo-semantic/references/workflow.md")
    datasource = _read("marivo-skills/marivo-semantic/references/datasource.md")
    authoring = _read("marivo-skills/marivo-semantic/references/authoring-patterns.md")
    pitfalls = _read("marivo-skills/marivo-semantic/references/pitfalls.md")

    combined = "\n".join((skill, workflow, datasource, authoring, pitfalls))
    assert 'backend_type="trino"' in combined
    assert "client_tags" in combined
    assert "user_env" in combined
    assert 'source=ms.table("orders", database="sales_mart")' in combined
    assert 'source=ms.file("/data/orders/*.parquet", format="parquet")' in combined
    assert 'database="sales_mart"' in combined
    assert "backend.list_databases(catalog=" in combined
    assert "backend.list_tables(database=" in combined
    assert "backend.list_schemas()" not in combined
    assert 'backend.get_schema("orders", database="sales_mart")' in datasource
    assert "schema` is optional" in datasource
    assert "VARCHAR" in combined and 'cast("timestamp").cast("date")' in combined
    assert "catalog.schema.table" not in combined
    assert 'backend.table("orders", database="sales_mart")' not in combined
    assert "FDN" not in combined
    assert "md.list()" in combined
    assert "md.all()" not in combined


def test_semantic_skill_prefers_native_datasource_backends() -> None:
    skill = _read("marivo-skills/marivo-semantic/SKILL.md")
    workflow = _read("marivo-skills/marivo-semantic/references/workflow.md")
    datasource = _read("marivo-skills/marivo-semantic/references/datasource.md")
    pitfalls = _read("marivo-skills/marivo-semantic/references/pitfalls.md")

    combined = "\n".join((skill, workflow, datasource, pitfalls))
    assert "Choose the native backend first" in datasource
    assert "can federate to another engine" in datasource
    assert "Federated backend chosen by habit" in pitfalls
    assert 'backend_type="clickhouse"' in combined
    assert 'backend_type="mysql"' in combined
    assert 'backend_type="duckdb"' in combined
    assert "Hive or Iceberg lakehouse table" in datasource
    assert "JSON files" in datasource
    assert 'ms.file("/data/orders.json", format="json")' not in combined


def test_design_spec_marks_remaining_phases_implemented() -> None:
    spec = _read("docs/specs/semantic/agent-semantic-layer-authoring-design.md")

    assert "| Table metadata/comments | `md.inspect_source(...)` | same |" in spec
    assert "### Phase 4: Metadata API\n\nImplemented:" in spec
    assert "### Phase 5: Agent Automation Tightening\n\nImplemented:" in spec


def test_semantic_skill_examples_cover_new_workflow_cases() -> None:
    examples_dir = REPO_ROOT / "marivo-skills" / "marivo-semantic" / "references" / "examples"
    expected = {
        "01_single_domain_file.py",
        "02_source_evidence_to_check.py",
        "03_closeout_readiness_richness.py",
    }
    names = {path.name for path in examples_dir.glob("*.py")}
    assert expected == names

    single = _read("marivo-skills/marivo-semantic/references/examples/01_single_domain_file.py")
    evidence = _read(
        "marivo-skills/marivo-semantic/references/examples/02_source_evidence_to_check.py"
    )
    closeout_ref = _read("marivo-skills/marivo-semantic/references/closeout.md")
    preview_ref = _read("marivo-skills/marivo-semantic/references/preview.md")
    closeout = _read(
        "marivo-skills/marivo-semantic/references/examples/03_closeout_readiness_richness.py"
    )

    assert "partition time dimension" in single
    assert 'data_type="string"' in single
    assert 'date_format="%Y%m%d"' in single
    assert 'date_format="HH"' not in single
    assert "return table.dt" in single
    assert "return table.dt.cast" not in single
    assert 'required_prefix="log_date"' in single
    assert "project.prepare_entity(" in evidence
    assert "md.ScanScope()" in evidence
    assert "bind_datasource_access" not in evidence
    assert "ms.AuthoringSourceInput(" not in evidence
    assert "project.assess_authoring(" not in evidence
    assert "project.verify_object(" in evidence
    assert "project.readiness(" in closeout
    assert "bind_datasource_access" not in closeout
    for text in (closeout_ref, preview_ref, closeout):
        assert "require_preview" not in text
        assert "require_evidence_ledger" not in text
        assert "strict_enrichment" not in text
        assert "project.readiness(backend_factory" not in text
    assert "project.readiness(" in closeout_ref
    assert "project.readiness(" in closeout
    assert "project.collect_raw_preview(" not in closeout
    assert "project.richness(" in closeout


def test_semantic_docs_and_skills_use_verification_mode() -> None:
    paths = [
        "docs/specs/semantic/python-semantic-layer.md",
        "docs/specs/semantic/agent-semantic-layer-authoring-design.md",
        "marivo-skills/marivo-semantic/SKILL.md",
        "marivo-skills/marivo-semantic/references/authoring-patterns.md",
        "marivo-skills/marivo-semantic/references/evidence-and-ledger.md",
        "marivo-skills/marivo-semantic/references/closeout.md",
        "marivo-skills/marivo-semantic/references/pitfalls.md",
        "marivo-skills/marivo-semantic/references/examples/03_closeout_readiness_richness.py",
    ]
    combined = "\n".join(_read(path) for path in paths)

    assert "verification_mode" in combined
    assert "declared_status" not in combined


def test_agent_semantic_authoring_spec_uses_current_readiness_closeout_contract() -> None:
    spec = _read("docs/specs/semantic/agent-semantic-layer-authoring-design.md")
    stale_phrases = (
        "source SQL parity is drifted",
        "metric is `unverified` in strict readiness",
        "project.readiness(...) is specified below but does not exist yet",
    )

    for phrase in stale_phrases:
        assert phrase not in spec


def test_semantic_skill_documents_partition_friendly_time_fields() -> None:
    authoring = _read("marivo-skills/marivo-semantic/references/authoring-patterns.md")
    pitfalls = _read("marivo-skills/marivo-semantic/references/pitfalls.md")

    assert 'data_type="string"' in authoring
    assert 'date_format="%Y%m%d"' in authoring
    assert 'date_format="HH"' not in authoring
    assert 'required_prefix="log_date"' in authoring
    assert "return table.dt" in authoring
    assert "return table.dt.cast" not in authoring
    assert "predicate pushdown" in authoring
    assert "Complex event-time expressions are still valid" in authoring
    assert "partition dimension default" in pitfalls


def test_skills_document_uniform_help_contract() -> None:
    semantic_skill = _read("marivo-skills/marivo-semantic/SKILL.md")
    analysis_skill = _read("marivo-skills/marivo-analysis/SKILL.md")

    combined = "\n".join((semantic_skill, analysis_skill))
    # New contract: mv.help() is canonical; no format= in examples
    assert "mv.help(" in combined
    assert "ms.help(" in combined
    assert "format='json'" not in combined
    assert 'format="json"' not in combined


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


def test_stepwise_authoring_help_lists_new_symbols_only() -> None:
    import marivo.datasource as md
    from marivo.introspection.surface import render as surface_render
    from marivo.semantic.help import _surface as semantic_surface

    semantic_data = surface_render(semantic_surface(), None, "json")
    datasource_data = md.help(format="json", print=False)

    for name in ("prepare_entity", "prepare_metric", "VerifyResult", "DomainBrief"):
        assert name in str(semantic_data), f"semantic help missing {name}"
    for name in ("ScanScope", "inspect_columns", "probe_join_keys"):
        assert name in str(datasource_data), f"datasource help missing {name}"
    for removed in ("assess_authoring", "AuthoringSourceInput", "inspect_authored_object"):
        assert removed not in str(semantic_data), (
            f"semantic help still has removed symbol {removed}"
        )


def test_semantic_skill_md_caps_respected() -> None:
    run_skill_examples = _load_run_skill_examples()
    failures = [
        run_skill_examples._check_skill_md(skill_dir)
        for skill_dir in run_skill_examples._iter_skill_dirs(REPO_ROOT)
    ]
    failures = [f for f in failures if f is not None]
    assert not failures, [f"{f.reason}: {f.detail}" for f in failures]


def test_superseded_semantic_docs_point_to_stepwise_design() -> None:
    docs = {
        "docs/specs/semantic/authoring-pipeline-design.md": "superseded",
        "docs/specs/semantic/agent-semantic-layer-authoring-design.md": "superseded",
        "docs/specs/semantic/skill-semantic-layer-authoring-design.md": "superseded",
    }
    for path, marker in docs.items():
        text = (REPO_ROOT / path).read_text(encoding="utf-8").lower()
        assert marker in text, f"{path} missing '{marker}' marker"
        assert "stepwise-authoring-design.md" in text, (
            f"{path} missing stepwise-authoring-design.md reference"
        )


def test_semantic_skill_uses_stepwise_ladder_contract() -> None:
    paths = [
        "marivo-skills/marivo-semantic/SKILL.md",
        "marivo-skills/marivo-semantic/references/workflow.md",
        "marivo-skills/marivo-semantic/references/datasource.md",
        "marivo-skills/marivo-semantic/references/evidence-and-ledger.md",
        "marivo-skills/marivo-semantic/references/closeout.md",
        "marivo-skills/marivo-semantic/references/pitfalls.md",
    ]
    combined = "\n".join(Path(path).read_text(encoding="utf-8") for path in paths)

    for required in (
        "prepare_entity",
        "prepare_metric",
        "verify_object",
        "ScanScope",
        "authoring_abandoned",
    ):
        assert required in combined, f"skill references missing {required}"
    for removed in (
        "assess_authoring",
        "AuthoringSourceInput",
        "inspect_authored_object",
        "bind_datasource_access",
        "project.inspect_table",
        "project.inspect_columns",
        "backend_factory",
    ):
        assert removed not in combined, f"skill references still contain removed symbol {removed}"
