"""Tests for datasource help after discovery public-surface cleanup."""

from __future__ import annotations

import marivo.datasource as md


def test_datasource_help_lists_discovery_family_and_scope_helpers() -> None:
    text = md.help_text()
    assert "md.discover_entity" in text
    assert "md.discover_dimensions" in text
    assert "md.discover_time_dimensions" in text
    assert "md.discover_measures" in text
    assert "md.discover_relationship" in text
    assert "md.discover_dimension_values" in text
    assert "md.raw_sql" in text
    assert "md.latest_partition" in text
    assert "md.partition" in text
    assert "md.unpruned" in text


def test_datasource_help_omits_removed_inspection_primitives() -> None:
    text = md.help_text()
    for removed in (
        "md.inspect_table",
        "md.inspect_source",
        "md.inspect_columns",
        "md.probe_join_keys",
        "ColumnInspection",
        "JoinKeyProbe",
    ):
        assert removed not in text


def test_datasource_help_detail_for_discover_measures_teaches_evidence_boundary() -> None:
    text = md.help_text("discover_measures")
    assert "DatasourceRef" in text
    assert "MeasureDiscoveryResult" in text
    assert "does not choose authoritative units" in text


def test_datasource_describe_covers_discovery_symbols() -> None:
    for symbol, expected in (
        ("discover_entity", "EntityDiscoveryResult"),
        ("discover_dimensions", "DimensionDiscoveryResult"),
        ("discover_time_dimensions", "TimeDimensionDiscoveryResult"),
        ("discover_measures", "MeasureDiscoveryResult"),
        ("discover_relationship", "RelationshipDiscoveryResult"),
        ("discover_dimension_values", "DimensionValueDiscoveryResult"),
        ("raw_sql", "RawSqlResult"),
        ("latest_partition", "ScanScope"),
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
        "inspect_table",
        "inspect_source",
        "inspect_columns",
        "probe_join_keys",
        "ColumnInspection",
        "JoinKeyProbe",
    ):
        assert forbidden not in text
