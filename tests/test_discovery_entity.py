"""Tests for entity discovery rules and build_entity_result."""

from __future__ import annotations

import pytest

from marivo.datasource.authoring import ref
from marivo.datasource.discovery import (
    DiscoveryIssue,
    DiscoverySignal,
    EntityDiscoveryResult,
)
from marivo.datasource.discovery_rules import build_entity_result, entity_rules
from marivo.datasource.metadata import (
    ColumnMetadata,
    PartitionMetadata,
    TableMetadata,
)
from marivo.datasource.scan import (
    ColumnProfile,
    ScanReport,
    ScanScope,
    table,
)
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES


def _profile(
    name: str,
    data_type: str = "VARCHAR",
    *,
    type_family: str = "unknown",
    distinct: int = 5,
    null_count: int = 0,
    non_null_count: int = 5,
) -> ColumnProfile:
    return ColumnProfile(
        name=name,
        data_type=data_type,
        nullable=True,
        comment=None,
        null_count=null_count,
        empty_count=0,
        distinct_count=distinct,
        top_values=(),
        sample_values=(),
        min_value=None,
        max_value=None,
        non_null_count=non_null_count,
        type_family=type_family,
    )


def _scan(rows: int = 5, truncated: bool = False) -> ScanReport:
    return ScanReport(
        partition_used=None,
        partition_resolution="none",
        rows_scanned=rows,
        columns_scanned=("a",),
        truncated=truncated,
        elapsed_seconds=0.1,
        warnings=(),
    )


def _metadata(
    *,
    primary_keys: tuple[str, ...] = (),
    unique=(),
    partitions=(),
    columns=(),
) -> TableMetadata:
    return TableMetadata(
        datasource="wh",
        table="orders",
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=columns,
        partitions=partitions,
        warnings=(),
        primary_keys=primary_keys,
        unique_constraints=unique,
    )


def test_entity_rules_declared_primary_key_and_sampled_unique() -> None:
    metadata = _metadata(
        primary_keys=("order_id",),
        columns=(
            ColumnMetadata(
                name="order_id", type="INTEGER", nullable=False, comment=None, ordinal_position=1
            ),
        ),
    )
    profiles = (
        _profile("order_id", "INTEGER", type_family="integer", distinct=5, non_null_count=5),
    )
    out = entity_rules(metadata, _scan(5), profiles, ScanScope())
    ids = [s.rule_id for s in out if isinstance(s, DiscoverySignal)]
    assert "entity_declared_primary_key" in ids
    assert "entity_sampled_unique_column" in ids
    assert "entity_no_primary_key_evidence" not in [
        i.rule_id for i in out if isinstance(i, DiscoveryIssue)
    ]


def test_entity_rules_no_pk_evidence_warning() -> None:
    metadata = _metadata()
    profiles = (_profile("region", "VARCHAR", type_family="string", distinct=3, non_null_count=5),)
    out = entity_rules(metadata, _scan(5), profiles, ScanScope())
    issues = [i for i in out if isinstance(i, DiscoveryIssue)]
    assert any(
        i.rule_id == "entity_no_primary_key_evidence" and i.severity == "warning" for i in issues
    )


def test_entity_rules_temporal_and_partition_signals() -> None:
    metadata = _metadata(
        partitions=(PartitionMetadata(name="dt", type="date"),),
        columns=(
            ColumnMetadata(
                name="created_at", type="TIMESTAMP", nullable=True, comment=None, ordinal_position=1
            ),
        ),
    )
    profiles = (_profile("created_at", "TIMESTAMP", type_family="timestamp"),)
    out = entity_rules(metadata, _scan(3), profiles, ScanScope())
    ids = [s.rule_id for s in out if isinstance(s, DiscoverySignal)]
    assert "entity_temporal_column_detected" in ids
    assert "entity_partition_column_detected" in ids


def test_entity_rules_many_columns_info() -> None:
    columns = tuple(
        ColumnMetadata(
            name=f"c{i}", type="INTEGER", nullable=True, comment=None, ordinal_position=i
        )
        for i in range(5)
    )
    out = entity_rules(_metadata(columns=columns), _scan(1), (), ScanScope(max_columns=2))
    issues = [i for i in out if isinstance(i, DiscoveryIssue)]
    assert any(i.rule_id == "entity_many_columns" and i.severity == "info" for i in issues)


def test_build_entity_result_wires_rules_and_evidence() -> None:
    metadata = _metadata(
        primary_keys=("order_id",),
        columns=(
            ColumnMetadata(
                name="order_id", type="INTEGER", nullable=False, comment=None, ordinal_position=1
            ),
        ),
    )
    profiles = (
        _profile("order_id", "INTEGER", type_family="integer", distinct=5, non_null_count=5),
    )
    result = build_entity_result(
        datasource=ref("warehouse"),
        source=table("orders"),
        table_metadata=metadata,
        scan=_scan(5, truncated=True),
        scope=ScanScope(),
        column_profiles=profiles,
    )
    assert isinstance(result, EntityDiscoveryResult)
    # Result-scope truncated issue.
    assert any(i.rule_id == "discovery_scan_truncated" for i in result.issues)
    # Flattened evidence carries declared-primary-key and sampled-unique signals.
    result_signal_ids = {s.rule_id for s in result.signals}
    assert "entity_declared_primary_key" in result_signal_ids
    assert "entity_sampled_unique_column" in result_signal_ids
    # Typed primary-key evidence populated.
    assert any(c.source == "declared_primary" for c in result.primary_key_evidence)
    assert any(c.source == "sampled_unique" for c in result.primary_key_evidence)
    # Flattened result has no candidate or judgment-target surface.
    assert not hasattr(result, "candidates")
    assert not hasattr(result, "judgment_targets")
    # Result-scope signals and issues do not overlap.
    result_ids = {i.rule_id for i in result.issues}
    assert result_ids.isdisjoint(result_signal_ids)


def test_entity_result_render_includes_schema_and_partition_columns() -> None:
    metadata = _metadata(
        partitions=(
            PartitionMetadata(name="dt", type="date"),
            PartitionMetadata(name="region", type="varchar"),
        ),
        columns=(
            ColumnMetadata(
                name="order_id", type="INTEGER", nullable=False, comment=None, ordinal_position=1
            ),
            ColumnMetadata(
                name="amount",
                type="DOUBLE",
                nullable=True,
                comment="Gross amount",
                ordinal_position=2,
            ),
            ColumnMetadata(
                name="dt", type="DATE", nullable=False, comment=None, ordinal_position=3
            ),
        ),
    )
    result = build_entity_result(
        datasource=ref("warehouse"),
        source=table("orders"),
        table_metadata=metadata,
        scan=_scan(5),
        scope=ScanScope(),
        column_profiles=(_profile("order_id", "INTEGER", type_family="integer"),),
    )

    rendered = result.render()

    assert "schema columns:" in rendered
    assert "order_id | INTEGER | N" in rendered
    assert "amount | DOUBLE | Y | Gross amount" in rendered
    assert "partition columns: dt, region" in rendered
    assert result.partition_columns == ("dt", "region")


def test_entity_result_full_render_includes_all_wide_table_schema_and_profiles() -> None:
    columns = tuple(
        ColumnMetadata(
            name=f"wide_col_{i:02d}",
            type="VARCHAR",
            nullable=True,
            comment=None,
            ordinal_position=i,
        )
        for i in range(1, 69)
    )
    profiles = tuple(_profile(column.name, "VARCHAR", type_family="string") for column in columns)
    result = build_entity_result(
        datasource=ref("warehouse"),
        source=table("wide_orders"),
        table_metadata=_metadata(columns=columns),
        scan=_scan(5),
        scope=ScanScope(max_columns=100),
        column_profiles=profiles,
    )

    rendered = result.render(max_output_bytes=None)

    assert "wide_col_01 | VARCHAR | Y" in rendered
    assert "wide_col_68 | VARCHAR | Y" in rendered
    assert "wide_col_01 type=VARCHAR" in rendered
    assert "wide_col_68 type=VARCHAR" in rendered
    assert "... 60 more" not in rendered


def test_entity_result_default_render_caps_final_text_bytes() -> None:
    columns = tuple(
        ColumnMetadata(
            name=f"verbose_col_{i:03d}",
            type="VARCHAR",
            nullable=True,
            comment="x" * 1000,
            ordinal_position=i,
        )
        for i in range(1, 90)
    )
    profiles = tuple(_profile(column.name, "VARCHAR", type_family="string") for column in columns)
    result = build_entity_result(
        datasource=ref("warehouse"),
        source=table("wide_orders"),
        table_metadata=_metadata(columns=columns),
        scan=_scan(5),
        scope=ScanScope(max_columns=100),
        column_profiles=profiles,
    )

    rendered = result.render()

    assert len(rendered.encode("utf-8")) <= _DEFAULT_MAX_OUTPUT_BYTES
    assert f"output truncated at {_DEFAULT_MAX_OUTPUT_BYTES} bytes" in rendered
    assert "max_output_bytes=None" in rendered
    assert not rendered.endswith("\n")


def test_entity_result_full_render_disables_final_text_cap() -> None:
    columns = tuple(
        ColumnMetadata(
            name=f"verbose_col_{i:03d}",
            type="VARCHAR",
            nullable=True,
            comment="x" * 1000,
            ordinal_position=i,
        )
        for i in range(1, 90)
    )
    profiles = tuple(_profile(column.name, "VARCHAR", type_family="string") for column in columns)
    result = build_entity_result(
        datasource=ref("warehouse"),
        source=table("wide_orders"),
        table_metadata=_metadata(columns=columns),
        scan=_scan(5),
        scope=ScanScope(max_columns=100),
        column_profiles=profiles,
    )

    rendered = result.render(max_output_bytes=None)

    assert len(rendered.encode("utf-8")) > _DEFAULT_MAX_OUTPUT_BYTES
    assert "output truncated" not in rendered
    assert "verbose_col_089 | VARCHAR | Y" in rendered


def test_entity_result_rejects_non_positive_output_cap() -> None:
    result = build_entity_result(
        datasource=ref("warehouse"),
        source=table("orders"),
        table_metadata=_metadata(),
        scan=_scan(5),
        scope=ScanScope(),
        column_profiles=(),
    )

    with pytest.raises(ValueError, match="minimum"):
        result.render(max_output_bytes=0)


def test_entity_result_show_uses_full_render_when_requested(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = build_entity_result(
        datasource=ref("warehouse"),
        source=table("orders"),
        table_metadata=_metadata(),
        scan=_scan(5),
        scope=ScanScope(),
        column_profiles=(),
    )

    assert result.show(max_output_bytes=None) is None

    captured = capsys.readouterr()
    assert captured.out == result.render(max_output_bytes=None) + "\n"
