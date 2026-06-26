"""Tests for relationship discovery rules and build_relationship_result."""

from __future__ import annotations

from marivo.datasource.discovery import (
    KeyTypeEvidence,
    RelationshipDiscoveryResult,
)
from marivo.datasource.discovery_rules import build_relationship_result, relationship_rules
from marivo.datasource.scan import JoinSide, ScanReport, table


def _scan(truncated: bool = False) -> ScanReport:
    return ScanReport(
        partition_used=None,
        partition_resolution="none",
        rows_scanned=10,
        columns_scanned=("k",),
        truncated=truncated,
        elapsed_seconds=0.1,
        warnings=(),
    )


def _from_side() -> JoinSide:
    return JoinSide(datasource="wh", source=table("orders"), columns=("customer_id",))


def _to_side() -> JoinSide:
    return JoinSide(datasource="wh", source=table("customers"), columns=("customer_id",))


def test_relationship_key_type_evidence_signal_and_match_rate() -> None:
    key_types = (
        KeyTypeEvidence(
            side="from", column="customer_id", type_family="integer", data_type="BIGINT"
        ),
        KeyTypeEvidence(side="to", column="customer_id", type_family="integer", data_type="BIGINT"),
    )
    out = relationship_rules(
        key_types, sampled_key_count=10, matched_key_count=8, max_rows_per_key=1
    )
    ids = [getattr(item, "rule_id", None) for item in out]
    assert "relationship_key_type_evidence" in ids
    assert "relationship_match_rate" in ids
    assert "relationship_key_type_mismatch_observed" not in ids
    assert "relationship_fanout_observed" not in ids


def test_relationship_key_type_mismatch_warning() -> None:
    key_types = (
        KeyTypeEvidence(
            side="from", column="customer_id", type_family="integer", data_type="BIGINT"
        ),
        KeyTypeEvidence(side="to", column="customer_id", type_family="string", data_type="VARCHAR"),
    )
    out = relationship_rules(
        key_types, sampled_key_count=5, matched_key_count=5, max_rows_per_key=1
    )
    issues = [i for i in out if hasattr(i, "severity")]
    assert any(
        getattr(i, "rule_id", None) == "relationship_key_type_mismatch_observed" for i in issues
    )


def test_relationship_no_matches_and_fanout_warnings() -> None:
    key_types = (
        KeyTypeEvidence(side="from", column="k", type_family="integer", data_type="BIGINT"),
        KeyTypeEvidence(side="to", column="k", type_family="integer", data_type="BIGINT"),
    )
    out = relationship_rules(
        key_types, sampled_key_count=5, matched_key_count=0, max_rows_per_key=3
    )
    issues = [i for i in out if hasattr(i, "severity")]
    ids = {getattr(i, "rule_id", None) for i in issues}
    assert "relationship_no_matches_sampled" in ids
    assert "relationship_fanout_observed" in ids


def test_build_relationship_result_wires_evidence_and_truncation() -> None:
    key_types = (
        KeyTypeEvidence(
            side="from", column="customer_id", type_family="integer", data_type="BIGINT"
        ),
        KeyTypeEvidence(side="to", column="customer_id", type_family="integer", data_type="BIGINT"),
    )
    result = build_relationship_result(
        from_side=_from_side(),
        to_side=_to_side(),
        key_type_evidence=key_types,
        sampled_key_count=10,
        matched_key_count=7,
        max_rows_per_key=2,
        avg_rows_per_key=1.4,
        cardinality_evidence="many_to_one",
        from_scan=_scan(truncated=True),
        to_scan=_scan(),
    )
    assert isinstance(result, RelationshipDiscoveryResult)
    evidence = result.evidence
    assert evidence.match_rate == 0.7
    assert evidence.cardinality_evidence == "many_to_one"
    evidence_issue_ids = {i.rule_id for i in evidence.issues}
    assert "relationship_fanout_observed" in evidence_issue_ids
    # Result-scope probe-truncated issue.
    assert any(i.rule_id == "relationship_probe_truncated" for i in result.issues)
    assert "relationship.keys" in {t.field_path for t in result.judgment_targets}
