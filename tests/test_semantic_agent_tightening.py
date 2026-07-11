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
    for name in (
        "PartitionScope",
        "UnprunedScope",
        "SourceInspection",
        "DiscoverySnapshot",
        "raw_sql",
    ):
        assert name in str(datasource_data), f"datasource help missing {name}"


def test_active_semantic_authoring_guidance_omits_prepare_stage() -> None:
    current_flow = (
        "help/browse -> inspect -> explicit scope -> sample once -> project evidence "
        "-> settle/grill -> author one Python object -> load typed object -> static verify "
        "-> scoped preview -> readiness -> analysis"
    )
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


def test_site_docs_cover_snapshot_first_semantic_authoring() -> None:
    en_paths = [
        "site/src/content/docs/en/latest/concepts/semantic-layer.mdx",
        "site/src/content/docs/en/latest/quick-start.mdx",
        "site/src/content/docs/en/latest/concepts/readiness.mdx",
        "site/src/content/docs/en/latest/concepts/analysis-workflow.mdx",
    ]
    zh_paths = [
        "site/src/content/docs/zh-cn/latest/concepts/semantic-layer.mdx",
        "site/src/content/docs/zh-cn/latest/quick-start.mdx",
        "site/src/content/docs/zh-cn/latest/concepts/readiness.mdx",
        "site/src/content/docs/zh-cn/latest/concepts/analysis-workflow.mdx",
    ]
    en = "\n".join(_read(path) for path in en_paths)
    zh = "\n".join(_read(path) for path in zh_paths)

    for text, label in ((en, "English site docs"), (zh, "Chinese site docs")):
        for required in (
            "md.inspect",
            "inspection.sample",
            "snapshot.entity",
            "snapshot.dimensions",
            "snapshot.measures",
            "md.partition",
            "md.unpruned",
            "md.table",
            "md.parquet",
            "md.csv",
            "md.json",
            "ms.help",
            "catalog.verify_object",
            "using=snapshot",
            "catalog.readiness",
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
            "md.inspect_table",
            "md.inspect_partitions",
            "md.discover_entity",
            "md.discover_dimensions",
            "md.discover_time_dimensions",
            "md.discover_measures",
            "md.discover_relationship",
            "md.discover_dimension_values",
        ):
            assert forbidden not in text, f"{label} still contains {forbidden}"


def test_latest_release_note_names_removed_surface_as_historical_family_only() -> None:
    for locale in ("en", "zh-cn"):
        text = _read(f"site/src/content/docs/{locale}/latest/release-notes/0.2.1.mdx")
        assert "historical discovery API family" in text
        assert "md.discover_" not in text


def test_latest_semantic_guides_have_one_snapshot_catalog_route() -> None:
    paths = (
        "site/src/content/docs/en/latest/concepts/semantic-layer.mdx",
        "site/src/content/docs/zh-cn/latest/concepts/semantic-layer.mdx",
    )
    for path in paths:
        text = _read(path)
        for required in (
            "snapshot.entity",
            "catalog.verify_object(obj)",
            "catalog.preview(obj, using=snapshot)",
            "catalog.readiness(refs=[obj])",
        ):
            assert required in text, f"{path} missing {required!r}"
        for stale in (
            "max_columns",
            "max_output_bytes",
            "ms.verify_object",
            "md.discover_",
            "ms.readiness()",
        ):
            assert stale not in text, f"{path} still contains {stale!r}"


def test_latest_readiness_guides_use_typed_catalog_objects() -> None:
    paths = (
        "site/src/content/docs/en/latest/concepts/readiness.mdx",
        "site/src/content/docs/zh-cn/latest/concepts/readiness.mdx",
    )
    for path in paths:
        text = _read(path)
        assert 'revenue = catalog.get("metric.sales.revenue")' in text
        assert "catalog.readiness(refs=[revenue])" in text
        assert "ms.readiness" not in text
        assert 'refs=["sales.revenue"]' not in text


def test_analysis_handoff_guidance_uses_typed_catalog_readiness_only() -> None:
    analysis_paths = (
        "site/src/content/docs/en/latest/concepts/analysis-workflow.mdx",
        "site/src/content/docs/zh-cn/latest/concepts/analysis-workflow.mdx",
    )
    for path in analysis_paths:
        text = _read(path)
        assert "catalog.readiness(refs=[revenue, region]).show()" in text
        assert "ms.readiness" not in text
        assert "readiness(refs=[revenue.ref, region.ref])" not in text

    canonical = _read("docs/specs/semantic/loading-validation-introspection.md")
    assert "catalog.readiness(refs=[obj])" in canonical
    assert "ms.readiness" not in canonical
    assert "catalog.readiness(refs=None)" not in canonical
