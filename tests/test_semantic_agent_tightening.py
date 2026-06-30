"""Regression tests for semantic agent authoring guidance."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

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


def test_semantic_skill_is_workflow_only_after_layering_simplification() -> None:
    skill = _read("marivo/skills/marivo-semantic/SKILL.md")
    datasource = _read("marivo/skills/marivo-semantic/references/datasource.md")
    closeout = _read("marivo/skills/marivo-semantic/references/closeout.md")
    pitfalls = _read("marivo/skills/marivo-semantic/references/pitfalls.md")
    combined = "\n".join((skill, datasource, closeout, pitfalls))
    references = "\n".join((datasource, closeout, pitfalls))

    assert "help -> discover -> settle/grill -> author -> verify" in skill
    for required in (
        "Evidence before questions",
        "Wide discovery, narrow authoring",
        "One active batch",
        "One object then verify",
        "One grill decision",
        "Ref-only links",
        "No contract transcription",
        "## Process Flow",
        "digraph marivo_semantic",
        "Broad discovery pass",
        "The broad discovery pass includes `md.inspect_table(...)`",
        "`md.inspect_partitions(...)` when the table is partitioned",
        "choose active batch",
        "ms.verify_object(ref)",
    ):
        assert required in skill

    assert "`ms.help(...)` owns the static authoring contract" in skill
    assert "`md.discover_*` owns datasource evidence" in skill
    assert "This skill owns workflow and routing only" in skill
    assert "ms.readiness(" in closeout
    assert "md.help(" in datasource
    assert "md.test(" in datasource
    assert "md.inspect_table(" in datasource
    assert "md.inspect_partitions(" in datasource
    assert "discovery_column_limit_truncated" in datasource
    assert "broad domain- or table-group discovery pass" in datasource
    assert "choose active batch" not in references
    assert "A grill turn MUST ask exactly one unresolved semantic decision" not in references

    forbidden = (
        "judgment_targets",
        "md.inspect_columns",
        "md.probe_join_keys",
        "md.latest_partition",
        'ms.help("datetime")',
        'ms.help("timestamp")',
        'ms.help("strptime")',
        'ms.help("hour_prefix")',
        "backend.list_databases(",
        "backend.get_schema(",
        "strict_enrichment",
        "require_preview",
        "require_evidence_ledger",
    )
    for text in forbidden:
        assert text not in combined


def test_semantic_skill_deleted_reference_files_stay_deleted() -> None:
    deleted = {
        "marivo/skills/marivo-semantic/references/authoring-patterns.md",
        "marivo/skills/marivo-semantic/references/object-briefs.md",
        "marivo/skills/marivo-semantic/references/preview.md",
        "marivo/skills/marivo-semantic/references/evidence-and-ledger.md",
        "marivo/skills/marivo-semantic/references/workflow.md",
    }
    for path in deleted:
        assert not (REPO_ROOT / path).exists(), f"{path} should not be recreated"


def test_superseded_authoring_spec_points_to_stepwise_design() -> None:
    spec = _read("docs/specs/semantic/semantic-authoring-design-superseded.md")
    assert "docs/specs/semantic/stepwise-authoring-design.md" in spec
    assert "superseded" in spec.lower()
    # The merged superseded doc preserves historical pipeline terminology
    # (NextCheck, next_checks, needs_evidence, check_authoring_inputs) as
    # reference context. Verify the header directs agents away from them.
    assert "Status: superseded" in spec


def test_design_spec_marks_remaining_phases_implemented() -> None:
    spec = _read("docs/specs/semantic/semantic-authoring-design-superseded.md")

    assert "| Table metadata/comments | `md.inspect_source(...)` | same |" in spec
    assert "### Phase 4: Metadata API\n\nImplemented:" in spec
    assert "### Phase 5: Agent Automation Tightening\n\nImplemented:" in spec


def test_agent_semantic_authoring_spec_uses_current_readiness_closeout_contract() -> None:
    spec = _read("docs/specs/semantic/semantic-authoring-design-superseded.md")
    stale_phrases = (
        "source SQL parity is drifted",
        "metric is `unverified` in strict readiness",
        "project.readiness(...) is specified below but does not exist yet",
    )

    for phrase in stale_phrases:
        assert phrase not in spec


def test_skills_document_uniform_help_contract() -> None:
    semantic_skill = _read("marivo/skills/marivo-semantic/SKILL.md")
    analysis_skill = _read("marivo/skills/marivo-analysis/SKILL.md")

    combined = "\n".join((semantic_skill, analysis_skill))
    # New contract: mv.help() is canonical; no format= in examples
    assert "mv.help(" in combined
    assert "ms.help(" in combined
    assert "format='json'" not in combined
    assert 'format="json"' not in combined


def test_stepwise_authoring_help_lists_new_symbols_only() -> None:
    from marivo.datasource.help import _surface as datasource_surface
    from marivo.introspection.surface import render as surface_render
    from marivo.semantic.help import _surface as semantic_surface

    semantic_data = surface_render(semantic_surface(), None, "json")
    datasource_data = surface_render(datasource_surface(), None, "json")

    for name in ("VerifyResult", "domain", "entity", "metric"):
        assert name in str(semantic_data), f"semantic help missing {name}"
    for name in ("prepare_entity", "prepare_metric", "DomainBrief"):
        assert name not in str(semantic_data), f"semantic help still exposes {name}"
    for name in ("ScanScope", "discover_entity", "discover_measures", "raw_sql"):
        assert name in str(datasource_data), f"datasource help missing {name}"


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
        "docs/specs/semantic/semantic-authoring-design-superseded.md": "superseded",
    }
    for path, marker in docs.items():
        text = (REPO_ROOT / path).read_text(encoding="utf-8").lower()
        assert marker in text, f"{path} missing '{marker}' marker"
        assert "stepwise-authoring-design.md" in text, (
            f"{path} missing stepwise-authoring-design.md reference"
        )


def test_active_semantic_authoring_guidance_omits_prepare_stage() -> None:
    current_flow = "help -> discover -> settle/grill -> author -> verify"
    active_paths = [
        "agent-guide.md",
        "docs/specs/semantic/stepwise-authoring-design.md",
        "docs/specs/semantic/python-semantic-layer.md",
        "marivo/skills/marivo-semantic/SKILL.md",
    ]
    banned_patterns = [
        re.compile(r"prepare_\w+"),
        re.compile(r"prepare\s*[-/>\u2192]+\s*author", re.IGNORECASE),
        re.compile(r"prepare-before", re.IGNORECASE),
        re.compile(r"prepare/verify", re.IGNORECASE),
    ]

    for path in active_paths:
        text = _read(path)
        assert current_flow in text, f"{path} missing current flow"
        for pattern in banned_patterns:
            assert pattern.search(text) is None, (
                f"{path} still teaches removed prepare stage via {pattern.pattern!r}"
            )


def test_prepare_era_specs_are_marked_historical() -> None:
    current_flow = "help -> discover -> settle/grill -> author -> verify"
    historical_paths = [
        "docs/specs/semantic/semantic-authoring-design-superseded.md",
        "docs/superpowers/specs/2026-06-16-metric-unit-measure-propagation-design.md",
        "docs/superpowers/specs/2026-06-16-semantic-authoring-surface-redesign-design.md",
        "docs/superpowers/specs/2026-06-20-api-reference-organization-design.md",
        "docs/superpowers/specs/2026-06-21-datasource-semantic-agent-surface-fix-design.md",
        "docs/superpowers/specs/2026-06-21-semantic-column-authoring-design.md",
        "docs/superpowers/specs/2026-06-25-authoring-discover-design.md",
        "docs/superpowers/specs/2026-06-26-authoring-guidance-layering-design.md",
        "docs/superpowers/specs/2026-06-27-semantic-skill-layering-simplification-design.md",
    ]

    for path in historical_paths:
        text = _read(path)
        assert "Historical note" in text, f"{path} missing historical note"
        assert current_flow in text, f"{path} missing current flow pointer"


def test_semantic_design_docs_teach_discovery_first_contract() -> None:
    paths = [
        "docs/api/datasource.rst",
        "docs/specs/semantic/stepwise-authoring-design.md",
        "docs/specs/semantic/python-semantic-layer.md",
    ]
    combined = "\n".join(_read(path) for path in paths)

    for required in (
        "discover_entity",
        "discover_dimensions",
        "discover_time_dimensions",
        "discover_measures",
        "discover_relationship",
        "discover_dimension_values",
        "inspect_table",
        "inspect_partitions",
        "raw_sql",
        "partition",
        "unpruned",
        "DatasourceRef",
        "TableSource",
    ):
        assert required in combined, f"semantic docs missing {required!r}"

    forbidden = (
        "md.inspect_source",
        "md.inspect_columns",
        "md.probe_join_keys",
        "md.latest_partition",
        "project.assess_authoring(",
        "check_authoring_inputs",
    )
    for phrase in forbidden:
        assert phrase not in combined, f"semantic docs still contain {phrase!r}"


def test_site_docs_cover_discovery_first_semantic_authoring() -> None:
    en_paths = [
        "site/src/content/docs/en/latest/concepts/semantic-layer.mdx",
        "site/src/content/docs/en/latest/quick-start.mdx",
        "site/src/content/docs/en/latest/release-notes/0.2.1.mdx",
    ]
    zh_paths = [
        "site/src/content/docs/zh-cn/latest/concepts/semantic-layer.mdx",
        "site/src/content/docs/zh-cn/latest/quick-start.mdx",
        "site/src/content/docs/zh-cn/latest/release-notes/0.2.1.mdx",
    ]
    en = "\n".join(_read(path) for path in en_paths)
    zh = "\n".join(_read(path) for path in zh_paths)

    for text, label in ((en, "English site docs"), (zh, "Chinese site docs")):
        for required in (
            "md.discover_entity",
            "md.discover_dimensions",
            "md.discover_measures",
            "md.inspect_table",
            "md.inspect_partitions",
            "md.partition",
            "ms.help",
            "ms.verify_object",
        ):
            assert required in text, f"{label} missing {required}"
        for forbidden in (
            "md.inspect_source",
            "md.inspect_columns",
            "md.probe_join_keys",
            "md.latest_partition",
            "ColumnInspection",
            "JoinKeyProbe",
        ):
            assert forbidden not in text, f"{label} still contains {forbidden}"
