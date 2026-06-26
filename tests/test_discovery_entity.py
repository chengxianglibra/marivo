"""Tests for entity discovery rules and build_entity_result."""

from __future__ import annotations

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


def test_build_entity_result_wires_rules_and_targets() -> None:
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
        candidate_profiles=profiles,
    )
    assert isinstance(result, EntityDiscoveryResult)
    # Result-scope truncated issue.
    assert any(i.rule_id == "discovery_scan_truncated" for i in result.issues)
    # Candidate carries declared-primary-key and sampled-unique signals.
    cand = result.candidates[0]
    cand_signal_ids = {s.rule_id for s in cand.signals}
    assert "entity_declared_primary_key" in cand_signal_ids
    assert "entity_sampled_unique_column" in cand_signal_ids
    # Typed primary-key candidates populated.
    assert any(c.source == "declared_primary" for c in cand.primary_key_candidates)
    assert any(c.source == "sampled_unique" for c in cand.primary_key_candidates)
    # Judgment targets are the entity template.
    paths = {t.field_path for t in result.judgment_targets}
    assert "entity.primary_key" in paths
    # Result-scope and candidate-scope do not overlap.
    result_ids = {i.rule_id for i in result.issues}
    assert result_ids.isdisjoint(cand_signal_ids)
