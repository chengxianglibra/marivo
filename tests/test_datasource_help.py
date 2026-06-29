"""Tests for datasource help after discovery public-surface cleanup."""

from __future__ import annotations

from pathlib import Path

import marivo.datasource as md


def test_datasource_help_lists_discovery_family_and_scope_helpers() -> None:
    text = md.help_text()
    assert "md.discover_entity" in text
    assert "md.discover_dimensions" in text
    assert "md.discover_time_dimensions" in text
    assert "md.discover_measures" in text
    assert "md.discover_relationship" in text
    assert "md.discover_dimension_values" in text
    assert "md.inspect_table" in text
    assert "md.inspect_partitions" in text
    assert "md.raw_sql" in text
    assert "md.partition" in text
    assert "md.unpruned" in text


def test_datasource_help_omits_removed_low_level_primitives() -> None:
    text = md.help_text()
    for removed in (
        "md.inspect_source",
        "md.inspect_columns",
        "md.probe_join_keys",
        "ColumnInspection",
        "JoinKeyProbe",
        "md.latest_partition",
    ):
        assert removed not in text


def test_datasource_help_detail_for_discover_measures_teaches_evidence_boundary() -> None:
    text = md.help_text("discover_measures")
    assert "DatasourceRef" in text
    assert "DatasourceResult" in text
    assert "call `.show()` to inspect bounded evidence" in text
    assert "does not choose authoritative units" in text
    assert ".columns" not in text
    assert ".profile" not in text
    assert ".issues" not in text


def test_datasource_help_detail_for_discover_entity_names_schema_and_partitions() -> None:
    text = md.help_text("discover_entity")

    assert "schema columns" in text
    assert "partition columns" in text


def test_datasource_help_detail_for_raw_sql_names_metadata_diagnostics() -> None:
    text = md.help_text("raw_sql")

    assert "SHOW" in text
    assert "DESCRIBE" in text
    assert "EXPLAIN" in text


def test_datasource_help_detail_for_connect_teaches_context_manager() -> None:
    text = md.help_text("connect")

    assert "DatasourceConnection" in text
    assert "with md.connect" in text
    assert "disconnect" in text


def test_datasource_describe_covers_discovery_symbols() -> None:
    for symbol, expected in (
        ("discover_entity", "DatasourceResult"),
        ("discover_dimensions", "DatasourceResult"),
        ("discover_time_dimensions", "DatasourceResult"),
        ("discover_measures", "DatasourceResult"),
        ("discover_relationship", "DatasourceResult"),
        ("discover_dimension_values", "DatasourceResult"),
        ("inspect_table", "DatasourceResult"),
        ("inspect_partitions", "DatasourceResult"),
        ("raw_sql", "DatasourceResult"),
        ("partition", "ScanScope"),
        ("unpruned", "ScanScope"),
        ("JoinSide", "DatasourceRef"),
        ("TableSource", "table"),
    ):
        text = md.help_text(symbol)
        assert expected in text, f"md.help_text({symbol!r}) missing {expected!r}"


def test_datasource_top_level_help_has_no_legacy_aliases() -> None:
    text = md.help_text()
    for forbidden in (
        "inspect_source",
        "inspect_columns",
        "probe_join_keys",
        "ColumnInspection",
        "JoinKeyProbe",
        "latest_partition",
        "RawSqlResult",
    ):
        assert forbidden not in text


def test_datasource_api_docs_list_public_datasource_result() -> None:
    text = Path("docs/api/datasource.rst").read_text(encoding="utf-8")

    assert "DatasourceResult" in text
    assert "inspect_table" in text
    assert "inspect_partitions" in text
    assert "latest_partition" not in text
    assert "DiscoveryResult" not in text
    assert "DatasourceConnection" in text
    for removed in (
        "EntityDiscoveryResult",
        "DimensionDiscoveryResult",
        "TimeDimensionDiscoveryResult",
        "MeasureDiscoveryResult",
        "RelationshipDiscoveryResult",
        "DimensionValueDiscoveryResult",
        "ColumnDiscovery",
        "TimeColumnDiscovery",
        "PrimaryKeyCandidate",
        "FormatCandidate",
    ):
        assert removed not in text
