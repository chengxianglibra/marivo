"""Regression tests for semantic agent authoring guidance."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text()


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


def test_active_semantic_authoring_guidance_omits_prepare_stage() -> None:
    current_flow = "help -> discover -> settle/grill -> author -> verify"
    active_paths = [
        "agent-guide.md",
        "docs/specs/semantic/authoring-workflow.md",
        "docs/specs/semantic/overview.md",
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


def test_semantic_skill_routes_to_authoring_help_topics() -> None:
    text = _read("marivo/skills/marivo-semantic/SKILL.md")
    assert 'md.help("authoring")' in text or "md.help('authoring')" in text
    assert 'ms.help("authoring")' in text or "ms.help('authoring')" in text
    # still workflow-only, no parameter tables, no prepare_
    assert "prepare_" not in text


def test_semantic_skill_datasource_reference_distinguishes_table_and_file_sources() -> None:
    text = _read("marivo/skills/marivo-semantic/references/datasource.md")

    for required in (
        "md.duckdb(",
        "md.table(",
        "md.parquet(",
        "md.csv(",
        "md.json(",
        "internal table or view",
        "DuckDB file source",
        "not a datasource declaration",
    ):
        assert required in text, f"datasource reference missing {required!r}"

    for forbidden in ("md.duckdb.parquet", "md.duckdb.csv", "md.duckdb.json"):
        assert forbidden not in text


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
            "md.table",
            "md.parquet",
            "md.csv",
            "md.json",
            "ms.help",
            "ms.verify_object",
            "file source",
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
