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
    assert "md.json" in text


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


def test_datasource_help_detail_for_json_source_builder() -> None:
    text = md.help_text("json")

    assert "JsonSourceIR" in text
    assert "format" in text
    assert "http(s):// URL" in text
    assert "columns" not in text


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


def test_help_lists_authoring_topic() -> None:
    text = md.help_text()
    assert "authoring" in text


def test_authoring_topic_renders_datasource_stages_and_handoff() -> None:
    text = md.help_text("authoring")
    # import shape
    assert "import marivo.datasource as md" in text
    # stage APIs named in spec §md.help("authoring")
    for needle in (
        "md.help(",
        "md.register(",
        "md.test(",
        "md.inspect_table(",
        "md.inspect_partitions(",
        "md.discover_entity",
        "md.discover_dimensions",
        "md.discover_time_dimensions",
        "md.discover_measures",
        "md.discover_relationship",
        "md.discover_dimension_values",
        "md.raw_sql(",
        'ms.help("authoring")',
    ):
        assert needle in text, f"authoring topic missing {needle!r}"
    # *_env secret rule + no internal secret classes
    assert "_env" in text
    assert "SecretStore" not in text
    assert "LocalPlaintextCache" not in text
    # budget
    assert text.count("\n") <= 80
    # no banned words
    assert "recommend" not in text.lower()
    assert "prepare_" not in text


def test_authoring_topic_distinguishes_duckdb_datasource_from_sources() -> None:
    text = md.help_text("authoring")

    for needle in (
        "md.duckdb(name=",
        'md.table("orders")',
        'md.parquet("data/orders/*.parquet")',
        'md.csv("data/orders/*.csv")',
        'md.json("data/events/*.json"',
        "internal table or view",
        "DuckDB file source",
        "not a datasource declaration",
    ):
        assert needle in text, f"authoring topic missing {needle!r}"

    for forbidden in (
        "md.duckdb.parquet",
        "md.duckdb.csv",
        "md.duckdb.json",
    ):
        assert forbidden not in text


def test_clickhouse_help_example_shows_register_test_inspect() -> None:
    text = md.help_text("clickhouse")
    assert "md.clickhouse(" in text
    assert "user_env=" in text and "password_env=" in text
    assert "md.register(spec)" in text
    assert "md.test(spec.ref)" in text
    assert "md.inspect_table(" in text
    # no plaintext secrets
    assert "password=" not in text.replace("password_env=", "")


def test_backend_help_examples_show_register_test_chain() -> None:
    """Each backend constructor help shows the register/test/inspect tail."""
    for backend in ("duckdb", "trino", "mysql", "postgres", "clickhouse"):
        text = md.help_text(backend)
        assert f"md.{backend}(" in text, f"{backend} help missing md.{backend}("
        assert "md.register(spec)" in text, f"{backend} help missing md.register(spec)"
        assert "md.test(spec.ref)" in text, f"{backend} help missing md.test(spec.ref)"
        # no plaintext secrets in any backend example
        assert "password=" not in text.replace("password_env=", ""), (
            f"{backend} help contains plaintext password= secret"
        )
        assert "user=" not in text.replace("user_env=", ""), (
            f"{backend} help contains plaintext user= secret"
        )


def test_trino_help_example_does_not_use_catalog_as_table_database() -> None:
    text = md.help_text("trino")

    assert 'md.table("orders", database="hive")' not in text
    assert 'schema="analytics"' in text
    assert 'md.inspect_table(spec.ref, md.table("orders")).show()' in text


def test_duckdb_help_examples_show_internal_table_and_file_source() -> None:
    text = md.help_text("duckdb")

    assert 'md.inspect_table(spec.ref, md.table("orders")).show()' in text
    assert 'md.inspect_table(spec.ref, md.parquet("data/orders/*.parquet")).show()' in text
    assert "internal table or view" in text
    assert "DuckDB file source" in text


def test_source_builder_help_distinguishes_sources_from_datasources() -> None:
    expectations = {
        "table": ("internal table or view", "not a datasource declaration"),
        "parquet": ("DuckDB file source", "not a datasource declaration"),
        "csv": ("DuckDB file source", "not a datasource declaration"),
        "json": ("DuckDB file source", "not a datasource declaration"),
    }
    for symbol, needles in expectations.items():
        text = md.help_text(symbol)
        for needle in needles:
            assert needle in text, f"md.help_text({symbol!r}) missing {needle!r}"


def test_datasource_api_docs_list_public_datasource_result() -> None:
    text = Path("docs/api/datasource.rst").read_text(encoding="utf-8")

    assert "DatasourceResult" in text
    assert "inspect_table" in text
    assert "inspect_partitions" in text
    assert "latest_partition" not in text
    assert "DiscoveryResult" not in text
    assert "DatasourceConnection" in text
    assert "Datasource vs source" in text
    assert "md.duckdb(...)" in text
    assert "md.table(...)" in text
    assert "md.parquet(...)" in text
    assert "md.csv(...)" in text
    assert "md.json(...)" in text
    assert "internal tables/views" in text
    assert "DuckDB file sources" in text
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


def test_ai_context_topic_points_to_ms_constructor() -> None:
    text = md.help_text("ai_context")
    assert "ms.ai_context(" in text
    for field in (
        "business_definition",
        "guardrails",
        "synonyms",
        "examples",
        "instructions",
        "owner_notes",
    ):
        assert field in text
    # invalid shapes named
    assert "summary=" in text
    assert "glossary=" in text
    # canonical contract pointer
    assert 'ms.help("ai_context")' in text or "ms.help('ai_context')" in text
    # must not imply md.ai_context exists
    assert "md.ai_context(" not in text
