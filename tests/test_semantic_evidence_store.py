from __future__ import annotations

from dataclasses import replace

import pytest

from marivo.semantic.evidence import (
    AssessmentIssue,
    AuthoringEvidenceInput,
    ColumnEvidence,
    ColumnProfile,
    MetadataOnlyPolicy,
    SourceEvidencePack,
    TableSource,
)
from marivo.semantic.evidence_store import (
    EvidenceStore,
    content_fingerprint,
    structural_fingerprint,
)


def _pack(tmp_path, *, table="orders") -> SourceEvidencePack:
    source = TableSource(table=table, database="sales_mart")
    fp = structural_fingerprint(
        datasource="warehouse",
        source=source,
        schema=(("id", "BIGINT"), ("amount", "DOUBLE")),
        table_comment="orders fact",
        column_comments=(("id", "pk"),),
    )
    ev = EvidenceStore(tmp_path).make_source_ref(
        datasource="warehouse",
        source=source,
        structural_fp=fp,
        collected_at="2026-06-06T00:00:00+00:00",
    )
    return SourceEvidencePack(
        datasource="warehouse",
        source=source,
        schema=(("id", "BIGINT"), ("amount", "DOUBLE")),
        table_comment="orders fact",
        column_comments=(("id", "pk"),),
        nullable=(("id", False), ("amount", True)),
        partition_hints=("dt",),
        key_hints=(),
        column_profiles=(
            ColumnProfile(column="amount", data_type="DOUBLE", nullable=True, comment=None),
        ),
        metadata_warnings=(),
        evidence_refs=(ev,),
        sample_policy=MetadataOnlyPolicy(),
        redaction_status="redacted",
        truncated=False,
    )


def _column_evidence_for_store(
    store: EvidenceStore,
    *,
    table: str = "orders",
    collected_at: str = "2026-06-06T00:00:00+00:00",
) -> tuple[ColumnEvidence, str]:
    source = TableSource(table=table, database="sales_mart")
    fp = structural_fingerprint(
        datasource="warehouse",
        source=source,
        schema=(("id", "BIGINT"), ("amount", "DOUBLE")),
        table_comment="orders fact",
        column_comments=(("amount", "order amount"),),
    )
    ref = store.make_column_ref(
        datasource="warehouse",
        source=source,
        column="amount",
        structural_fp=fp,
        collected_at=collected_at,
    )
    return (
        ColumnEvidence(
            datasource="warehouse",
            source=source,
            column="amount",
            profile=ColumnProfile(
                column="amount",
                data_type="DOUBLE",
                nullable=True,
                comment="order amount",
                null_count=0,
            ),
            issues=(),
            evidence_refs=(ref.id,),
        ),
        fp,
    )


def test_structural_fingerprint_is_stable_and_order_independent():
    a = structural_fingerprint(
        datasource="w",
        source=TableSource(table="orders"),
        schema=(("a", "INT"), ("b", "INT")),
        table_comment="c",
        column_comments=(("a", "x"), ("b", "y")),
    )
    b = structural_fingerprint(
        datasource="w",
        source=TableSource(table="orders"),
        schema=(("b", "INT"), ("a", "INT")),
        table_comment="c",
        column_comments=(("b", "y"), ("a", "x")),
    )
    assert a == b and a.startswith("sha256:")


def test_write_then_get_source_pack(tmp_path):
    store = EvidenceStore(tmp_path)
    pack = _pack(tmp_path)
    evidence_id = pack.evidence_refs[0].id
    store.write_source_pack(pack)
    loaded = store.read_pack(evidence_id)
    assert isinstance(loaded, SourceEvidencePack)
    assert loaded.schema == pack.schema
    assert loaded.partition_hints == ("dt",)
    assert loaded.column_profiles[0].column == "amount"


def test_write_source_pack_rejects_mismatched_primary_ref(tmp_path):
    store = EvidenceStore(tmp_path)
    pack = _pack(tmp_path)
    mismatched_ref = replace(pack.evidence_refs[0], datasource="warehouse_archive")
    mismatched_pack = replace(pack, evidence_refs=(mismatched_ref,))

    with pytest.raises(ValueError, match="source evidence ref datasource"):
        store.write_source_pack(mismatched_pack)


def test_write_source_pack_rejects_mismatched_secondary_ref(tmp_path):
    store = EvidenceStore(tmp_path)
    pack = _pack(tmp_path)
    mismatched_ref = replace(pack.evidence_refs[0], datasource="warehouse_archive")
    mismatched_pack = replace(pack, evidence_refs=(pack.evidence_refs[0], mismatched_ref))

    with pytest.raises(ValueError, match="source evidence ref datasource"):
        store.write_source_pack(mismatched_pack)


def test_write_source_pack_rejects_stale_ref_for_previous_payload(tmp_path):
    store = EvidenceStore(tmp_path)
    source = TableSource(table="orders", database="sales_mart")
    stale_fp = structural_fingerprint(
        datasource="warehouse",
        source=source,
        schema=(("id", "BIGINT"), ("amount", "DOUBLE")),
        table_comment="orders fact",
        column_comments=(("id", "pk"),),
    )
    stale_ref = store.make_source_ref(
        datasource="warehouse",
        source=source,
        structural_fp=stale_fp,
        collected_at="2026-06-06T00:00:00+00:00",
    )
    pack = replace(
        _pack(tmp_path),
        table_comment="orders fact updated",
        evidence_refs=(stale_ref,),
    )

    with pytest.raises(ValueError, match="source evidence ref structural_fingerprint"):
        store.write_source_pack(pack)


def test_write_then_get_column_evidence_preserves_issues(tmp_path):
    store = EvidenceStore(tmp_path)
    source = TableSource(table="orders", database="sales_mart")
    fp = structural_fingerprint(
        datasource="warehouse",
        source=source,
        schema=(("id", "BIGINT"), ("amount", "DOUBLE")),
        table_comment="orders fact",
        column_comments=(("amount", "order amount"),),
    )
    ref = store.make_column_ref(
        datasource="warehouse",
        source=source,
        column="amount",
        structural_fp=fp,
        collected_at="2026-06-06T00:00:00+00:00",
    )
    issue = AssessmentIssue(
        kind="missing_evidence",
        severity="warning",
        refs=("sales.revenue",),
        message="Need metric provenance",
        rule_id="semantic.metric.provenance",
        evidence_refs=(ref.id,),
        next_checks=("parity_check",),
    )
    evidence = ColumnEvidence(
        datasource="warehouse",
        source=source,
        column="amount",
        profile=ColumnProfile(
            column="amount",
            data_type="DOUBLE",
            nullable=True,
            comment="order amount",
            null_count=0,
        ),
        issues=(issue,),
        evidence_refs=(ref.id,),
    )
    evidence_id = evidence.evidence_refs[0]
    store.write_column_evidence(evidence)
    loaded = store.read_pack(evidence_id)
    assert isinstance(loaded, ColumnEvidence)
    assert loaded.column == "amount"
    assert loaded.profile == evidence.profile
    assert loaded.evidence_refs == evidence.evidence_refs
    assert loaded.issues == evidence.issues


def test_list_by_source_returns_only_matching_source(tmp_path):
    store = EvidenceStore(tmp_path)
    store.write_source_pack(_pack(tmp_path, table="orders"))
    store.write_source_pack(_pack(tmp_path, table="users"))
    refs = store.list_evidence(
        datasource="warehouse",
        source=TableSource(table="orders", database="sales_mart"),
    )
    assert len(refs) == 1
    assert refs[0].source is not None and refs[0].source.table == "orders"


def test_list_by_source_includes_matching_column_evidence_ref(tmp_path):
    store = EvidenceStore(tmp_path)
    column_evidence, _ = _column_evidence_for_store(store, table="orders")
    store.write_column_evidence(column_evidence)
    other_column_evidence, _ = _column_evidence_for_store(store, table="users")
    store.write_column_evidence(other_column_evidence)
    refs = store.list_evidence(
        datasource="warehouse",
        source=TableSource(table="orders", database="sales_mart"),
    )
    assert [ref.id for ref in refs] == [column_evidence.evidence_refs[0]]
    assert refs[0].kind == "schema"
    assert refs[0].source is not None and refs[0].source.table == "orders"


def test_list_by_source_preserves_column_ref_metadata(tmp_path):
    store = EvidenceStore(tmp_path)
    column_evidence, fp = _column_evidence_for_store(
        store,
        collected_at="2026-06-06T12:34:56+00:00",
    )
    store.write_column_evidence(column_evidence)

    refs = store.list_evidence(
        datasource="warehouse",
        source=TableSource(table="orders", database="sales_mart"),
    )

    assert len(refs) == 1
    assert refs[0].id == column_evidence.evidence_refs[0]
    assert refs[0].structural_fingerprint == fp
    assert refs[0].collected_at == "2026-06-06T12:34:56+00:00"


def test_write_column_evidence_rejects_unknown_ref_id(tmp_path):
    store = EvidenceStore(tmp_path)
    source = TableSource(table="orders", database="sales_mart")
    evidence = ColumnEvidence(
        datasource="warehouse",
        source=source,
        column="amount",
        profile=ColumnProfile(column="amount", data_type="DOUBLE", nullable=True, comment=None),
        issues=(),
        evidence_refs=("col:1234567890abcdef",),
    )

    with pytest.raises(ValueError, match="unknown column evidence ref id"):
        store.write_column_evidence(evidence)


def test_write_column_evidence_rejects_mismatched_cached_ref(tmp_path):
    store = EvidenceStore(tmp_path)
    source = TableSource(table="orders", database="sales_mart")
    other_source = TableSource(table="users", database="sales_mart")
    fp = structural_fingerprint(
        datasource="warehouse",
        source=other_source,
        schema=(("id", "BIGINT"),),
        table_comment="users dimension",
        column_comments=(("id", "pk"),),
    )
    ref = store.make_column_ref(
        datasource="warehouse",
        source=other_source,
        column="amount",
        structural_fp=fp,
        collected_at="2026-06-06T00:00:00+00:00",
    )
    evidence = ColumnEvidence(
        datasource="warehouse",
        source=source,
        column="amount",
        profile=ColumnProfile(
            column="amount",
            data_type="DOUBLE",
            nullable=True,
            comment="order amount",
        ),
        issues=(),
        evidence_refs=(ref.id,),
    )

    with pytest.raises(ValueError, match="column evidence ref source"):
        store.write_column_evidence(evidence)


def test_write_column_evidence_rejects_profile_column_mismatch(tmp_path):
    store = EvidenceStore(tmp_path)
    source = TableSource(table="orders", database="sales_mart")
    fp = structural_fingerprint(
        datasource="warehouse",
        source=source,
        schema=(("id", "BIGINT"), ("amount", "DOUBLE"), ("status", "VARCHAR")),
        table_comment="orders fact",
        column_comments=(("amount", "order amount"),),
    )
    ref = store.make_column_ref(
        datasource="warehouse",
        source=source,
        column="amount",
        structural_fp=fp,
        collected_at="2026-06-06T00:00:00+00:00",
    )
    evidence = ColumnEvidence(
        datasource="warehouse",
        source=source,
        column="amount",
        profile=ColumnProfile(
            column="status",
            data_type="VARCHAR",
            nullable=True,
            comment="order status",
        ),
        issues=(),
        evidence_refs=(ref.id,),
    )

    with pytest.raises(ValueError, match="column evidence profile column"):
        store.write_column_evidence(evidence)


def test_fresh_store_lists_source_keyed_refs_with_column_metadata(tmp_path):
    store = EvidenceStore(tmp_path)
    pack = _pack(tmp_path)
    column_evidence, fp = _column_evidence_for_store(
        store,
        collected_at="2026-06-06T12:34:56+00:00",
    )
    store.write_source_pack(pack)
    store.write_column_evidence(column_evidence)

    refs = EvidenceStore(tmp_path).list_evidence(
        datasource="warehouse",
        source=TableSource(table="orders", database="sales_mart"),
    )

    refs_by_id = {ref.id: ref for ref in refs}
    column_ref = refs_by_id[column_evidence.evidence_refs[0]]
    assert refs_by_id[pack.evidence_refs[0].id].kind == "catalog_metadata"
    assert column_ref.kind == "schema"
    assert column_ref.structural_fingerprint == fp
    assert column_ref.collected_at == "2026-06-06T12:34:56+00:00"


def test_record_and_list_authoring_evidence_by_subject(tmp_path):
    store = EvidenceStore(tmp_path)
    ref = store.write_authoring_evidence(
        AuthoringEvidenceInput(
            kind="source_sql",
            subject_refs=("sales.revenue",),
            content="select sum(amount) from orders",
            source_dialect="trino",
        )
    )
    assert ref.kind == "source_sql"
    assert ref.content_fingerprint == content_fingerprint("select sum(amount) from orders")
    refs = store.list_evidence(subject_refs=("sales.revenue",))
    assert [r.id for r in refs] == [ref.id]
    assert store.list_evidence(subject_refs=("sales.other",)) == ()


def test_fresh_store_lists_authoring_evidence_by_subject(tmp_path):
    store = EvidenceStore(tmp_path)
    ref = store.write_authoring_evidence(
        AuthoringEvidenceInput(
            kind="knowledge_document",
            subject_refs=("sales.revenue",),
            content="Revenue excludes tax.",
            source_document="metric-spec.md",
        )
    )

    refs = EvidenceStore(tmp_path).list_evidence(subject_refs=("sales.revenue",))

    assert [r.id for r in refs] == [ref.id]
    assert refs[0].content_fingerprint == content_fingerprint("Revenue excludes tax.")


def test_read_pack_returns_none_for_unknown_id(tmp_path):
    assert EvidenceStore(tmp_path).read_pack("src:1234567890abcdef") is None


def test_read_pack_rejects_path_traversal_ids(tmp_path):
    store = EvidenceStore(tmp_path)
    outside = tmp_path / "escape.json"
    outside.write_text("{}", encoding="utf-8")

    assert store.read_pack("../escape") is None
    assert store.read_pack("src:1234567890abcdef/../../escape") is None


def test_read_authoring_rejects_path_traversal_ids(tmp_path):
    store = EvidenceStore(tmp_path)
    outside = tmp_path / "escape.json"
    outside.write_text("{}", encoding="utf-8")

    assert store.read_authoring("../escape") is None
    assert store.read_authoring("doc:1234567890abcdef/../../escape") is None
