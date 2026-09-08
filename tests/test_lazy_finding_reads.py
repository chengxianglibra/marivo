"""Selected v3 Store reads with test-owned Findings, never production extraction."""

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.errors import FindingNotFoundError
from marivo.analysis.evidence import _dataset_reads as reads
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.evidence._dataset_codec import (
    decode_finding_body,
    encode_finding_body,
    finding_identity,
    finding_set_digest,
)
from marivo.analysis.materialization.contracts import (
    ArtifactRecord,
    EvidenceRecord,
    canonical_json,
    digest,
    encode_descriptor,
)
from marivo.analysis.materialization.errors import IntegrityError, MaterializationError
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.observation.contracts import make_ids
from marivo.analysis.refs import ArtifactRef
from tests.lazy_dataset_fixtures import make_row_contracts
from tests.lazy_materialization_fixtures import descriptor

_NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)
_SUBJECT = t.MetricFindingSubjectV1(metric=d._catalog_identity("metric:sales.revenue"))


def _item(artifact_ref: str, ordinal: int) -> t.Finding:
    result = t.Finding(
        finding_id="pending",
        artifact_ref=ArtifactRef(ref=artifact_ref),
        session_id="owner",
        finding_type="delta",
        epistemic_kind="algebraic",
        subject=_SUBJECT,
        coordinates=(),
        canonical_item_key=canonical_json(["row", ordinal]),
        value=t.DeltaFindingValueV1(
            coordinate_presence="matched",
            current_value=ordinal + 2,
            baseline_value=1,
            delta=ordinal + 1,
            relative_delta=t.DefinedFindingRatioV1(value=float(ordinal + 1)),
        ),
        derivation=t.FindingDerivationV1(
            producer_id="test.delta",
            extractor_contract_id="test_delta_finding",
            extractor_contract_version="1",
            source_artifact_refs=(),
            source_fields=(),
        ),
        committed_at=_NOW,
    )
    return replace(result, finding_id=finding_identity(result))


def _item_key(finding: t.Finding) -> str:
    assert isinstance(finding.value, t.DeltaFindingValueV1)
    return canonical_json(["row", finding.value.current_value - 2])


def _seed(
    store: SessionStore,
    *,
    artifact_ref: str = "artifact",
    count: int = 3,
) -> tuple[ArtifactRecord, reads.FindingRegistration, tuple[t.Finding, ...]]:
    # This seam tests Store selection and Finding codecs. The existing test family
    # supplies metadata; no production observation is published with Findings.
    row, row_set = make_row_contracts()
    base = descriptor()
    contract = replace(
        base.dataset_materialization_contract,
        producer_id="test.delta",
        shape_id=row.shape_id,
        finding_extractor_id="test_delta_finding",
        finding_policy_id="test_findings@v1",
    )
    metadata = replace(
        base,
        row_contract=row,
        row_set_contract=row_set,
        realized_schema=row.schema,
        dataset_materialization_contract=contract,
    )
    items = tuple(_item(artifact_ref, ordinal) for ordinal in range(count))
    evidence = EvidenceRecord(
        evidence_digest="a" * 64,
        finding_count=count,
        finding_set_digest=finding_set_digest(items),
        extractor_contract_versions=("test_delta_finding@v1",),
        quality_summary_digest="b" * 64,
        typed_issue_digest="c" * 64,
    )
    record = ArtifactRecord(
        artifact_ref,
        "owner",
        digest(artifact_ref),
        metadata,
        _NOW.isoformat(),
        "test_run",
        evidence,
    )
    with store._write() as conn:
        conn.execute(
            "INSERT INTO dataset_artifacts VALUES(?,?,?,?,?)",
            (
                artifact_ref,
                record.session_ref,
                record.execution_key_digest,
                encode_descriptor(metadata),
                record.committed_at,
            ),
        )
        conn.execute(
            "INSERT INTO dataset_evidence VALUES(?,?,?,?,?)",
            (
                artifact_ref,
                evidence.evidence_digest,
                count,
                evidence.finding_set_digest,
                canonical_json(evidence.extractor_contract_versions),
            ),
        )
        conn.executemany(
            "INSERT INTO findings VALUES(?,?,?,?,?)",
            [
                (
                    item.finding_id,
                    artifact_ref,
                    ordinal,
                    finding_identity(item),
                    encode_finding_body(item),
                )
                for ordinal, item in enumerate(items)
            ],
        )
    registration = reads.FindingRegistration(
        producer_id="test.delta",
        extractor_contract_id="test_delta_finding",
        extractor_contract_version="1",
        shape_id=row.shape_id,
        finding_type="delta",
        subject=_SUBJECT,
        coordinates=(),
        source_artifact_refs=(),
        source_fields=(),
        canonical_item_key=_item_key,
    )
    return record, registration, items


def _store(tmp_path: Path) -> SessionStore:
    store = SessionStore(tmp_path)
    store.create_session("Finding owner", session_ref="owner")
    return store


def test_real_store_paging_exact_ownership_and_full_digest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record, registration, items = _seed(store)
    other, other_registration, _ = _seed(store, artifact_ref="other", count=1)
    with store._read() as conn:
        first = reads.findings(conn, record, limit=2, registration=registration)
        assert first.items == items[:2]
        assert first.has_more and first.next_cursor is not None
        final = reads.findings(
            conn, record, limit=2, cursor=first.next_cursor, registration=registration
        )
        assert final.items == items[2:]
        assert not final.has_more and final.next_cursor is None
        assert (
            reads.finding(conn, record, items[1].finding_id, registration=registration) == items[1]
        )
        reads.audit_findings(conn, record, registration=registration)
        with pytest.raises(FindingNotFoundError):
            reads.finding(conn, other, items[0].finding_id, registration=other_registration)
        with pytest.raises(MaterializationError):
            reads.findings(conn, other, cursor=first.next_cursor, registration=other_registration)
    assert record.descriptor.row_contract.shape_id.family_id == "test"
    assert reads.evidence_digest(record).finding_count == 3


def test_corrupt_lookahead_and_unselected_body_are_not_decoded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record, registration, items = _seed(store)
    with store._write() as conn:
        conn.execute(
            "UPDATE findings SET finding_body_payload='corrupt' WHERE finding_ref=?",
            (items[2].finding_id,),
        )
    with store._read() as conn:
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        page = reads.findings(conn, record, limit=2, registration=registration)
        assert page.items == items[:2]
        selects = [sql for sql in statements if sql.startswith("SELECT")]
        assert len(selects) == 3
        assert "finding_body_payload" not in selects[0]
        assert items[2].finding_id not in " ".join(selects[1:])
        assert (
            reads.finding(conn, record, items[0].finding_id, registration=registration) == items[0]
        )
        with pytest.raises(IntegrityError):
            reads.findings(conn, record, cursor=page.next_cursor, registration=registration)
        with pytest.raises(IntegrityError):
            reads.audit_findings(conn, record, registration=registration)


def test_page_uses_one_snapshot_during_concurrent_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    record, registration, items = _seed(store)
    original = decode_finding_body
    decoded = 0

    def race(
        payload: str,
        *,
        finding_id: str,
        artifact_ref: str,
        session_id: str,
        committed_at: datetime,
    ) -> t.Finding:
        nonlocal decoded
        decoded += 1
        if decoded == 1:
            with store._write() as writer:
                writer.execute(
                    "UPDATE findings SET finding_body_payload='corrupt' WHERE finding_ref=?",
                    (items[1].finding_id,),
                )
        return original(
            payload,
            finding_id=finding_id,
            artifact_ref=artifact_ref,
            session_id=session_id,
            committed_at=committed_at,
        )

    monkeypatch.setattr(reads, "decode_finding_body", race)
    with store._read() as conn:
        assert reads.findings(conn, record, registration=registration).items == items
    with store._read() as conn, pytest.raises(IntegrityError):
        reads.finding(conn, record, items[1].finding_id, registration=registration)


@pytest.mark.parametrize("corruption", ["count", "digest", "ordinal", "identity", "ownership"])
def test_full_audit_checks_count_digest_ordinals_identity_and_body_authority(
    tmp_path: Path,
    corruption: str,
) -> None:
    store = _store(tmp_path)
    record, registration, items = _seed(store)
    if corruption == "count":
        record = replace(record, evidence=replace(record.evidence, finding_count=4))
    elif corruption == "digest":
        record = replace(record, evidence=replace(record.evidence, finding_set_digest="d" * 64))
    else:
        with store._write() as conn:
            if corruption == "ordinal":
                conn.execute(
                    "UPDATE findings SET finding_ordinal=8 WHERE finding_ref=?",
                    (items[2].finding_id,),
                )
            elif corruption == "identity":
                conn.execute(
                    "UPDATE findings SET finding_identity_digest='forged' WHERE finding_ref=?",
                    (items[2].finding_id,),
                )
            else:
                bad = replace(
                    items[2],
                    derivation=replace(
                        items[2].derivation, source_artifact_refs=(ArtifactRef(ref="foreign"),)
                    ),
                )
                conn.execute(
                    "UPDATE findings SET finding_body_payload=? WHERE finding_ref=?",
                    (encode_finding_body(bad), items[2].finding_id),
                )
    with store._read() as conn, pytest.raises(IntegrityError):
        reads.audit_findings(conn, record, registration=registration)


def test_zero_production_contract_rejects_selected_or_audited_unexpected_rows(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    record, _, _ = _seed(store)
    zero = replace(
        record, descriptor=descriptor(), evidence=replace(record.evidence, finding_count=0)
    )
    with store._read() as conn:
        with pytest.raises(IntegrityError):
            reads.findings(conn, zero)
        with pytest.raises(IntegrityError):
            reads.audit_findings(conn, zero)


@pytest.mark.parametrize("limit", [0, 101, True])
def test_invalid_page_limit_fails_before_query(tmp_path: Path, limit: int) -> None:
    store = _store(tmp_path)
    record, registration, _ = _seed(store, count=0)
    with store._read() as conn:
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        with pytest.raises(MaterializationError):
            reads.findings(conn, record, limit=limit, registration=registration)
        assert statements == []


def test_empty_set_100_limit_and_readonly_reads_preserve_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record, registration, _ = _seed(store, count=0)
    with store._read() as conn:
        before = tuple(conn.execute("SELECT * FROM sessions"))
        assert reads.findings(conn, record, limit=100, registration=registration).items == ()
        reads.audit_findings(conn, record, registration=registration)
        assert tuple(conn.execute("SELECT * FROM sessions")) == before
        assert not conn.execute("PRAGMA query_only").fetchone()[0]
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM findings")


def _dimension(*, nullable: bool = True) -> d.DatasetField:
    ids = make_ids(())
    return d._make_field(
        field_id=d._make_field_id("dimension.region"),
        name="region",
        role_id="dimension",
        identity=d._catalog_identity("dimension:sales.region"),
        derivation_identity="test.region",
        logical_type_id="string",
        physical_type_state=d._resolved_type("string", ids=ids),
        nullable=nullable,
        ids=ids,
    )


@pytest.mark.parametrize(
    "scalar,active,other,valid",
    [
        (None, True, False, True),
        (None, False, False, True),
        (None, True, True, True),
        ("member", True, False, True),
        ("member", False, False, False),
        ("member", True, True, False),
        (7, True, False, False),
    ],
)
def test_registered_dimension_null_states_are_derived_from_contribution_masks(
    scalar: t.Scalar,
    active: bool,
    other: bool,
    valid: bool,
) -> None:
    field = _dimension()
    rule = reads.CoordinateRule(field, "dimension", 0)
    coordinate = t.FindingCoordinateV1(
        field_id=field.field_id, identity=field.identity, value=scalar
    )
    base = _item("artifact", 0)
    value = t.ContributionFindingValueV1(
        method="additive_difference@v1",
        active_axis_mask=(active,),
        other_mask=(other,),
        contribution_kind="metric",
        current_value=2,
        baseline_value=1,
        overall_delta=1,
        contribution=1,
        share_of_total_delta=t.DefinedFindingRatioV1(value=1.0),
        share_of_positive_pool=t.DefinedFindingRatioV1(value=1.0),
        share_of_negative_pool=t.UndefinedFindingShareV1(reason="empty_negative_pool"),
        contribution_rank=1,
        status="ok",
    )
    item = replace(base, finding_type="contribution", coordinates=(coordinate,), value=value)
    if valid:
        reads._coordinate(coordinate, rule, item)
    else:
        with pytest.raises(IntegrityError):
            reads._coordinate(coordinate, rule, item)


def test_registration_rejects_identity_fields_and_non_dimension_nulls() -> None:
    row, _ = make_row_contracts("entity")
    with pytest.raises(IntegrityError):
        reads.CoordinateRule(row.schema.columns[0], "step")
    field = _dimension()
    rule = reads.CoordinateRule(field, "time")
    coordinate = t.FindingCoordinateV1(field_id=field.field_id, identity=field.identity, value=None)
    with pytest.raises(IntegrityError):
        reads._coordinate(coordinate, rule, _item("artifact", 0))


@pytest.mark.parametrize("limit", [1, 20, 100])
def test_complete_page_traversal_preserves_canonical_ordinals(tmp_path: Path, limit: int) -> None:
    store = _store(tmp_path)
    record, registration, expected = _seed(store, count=105)
    result: list[t.Finding] = []
    cursor: str | None = None
    with store._read() as conn:
        while True:
            page = reads.findings(
                conn, record, limit=limit, cursor=cursor, registration=registration
            )
            result.extend(page.items)
            if not page.has_more:
                break
            assert page.next_cursor is not None and page.next_cursor != cursor
            cursor = page.next_cursor
    assert tuple(result) == expected
    assert len({finding.finding_id for finding in result}) == len(expected)


def test_bad_cursor_discards_raw_exception_context(tmp_path: Path) -> None:
    import base64

    store = _store(tmp_path)
    record, registration, _ = _seed(store)
    canary = "private-cursor-canary"
    cursor = base64.urlsafe_b64encode(('"' + canary).encode()).decode().rstrip("=")
    with store._read() as conn, pytest.raises(MaterializationError) as caught:
        reads.findings(conn, record, cursor=cursor, registration=registration)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert canary not in str(caught.value)
    assert cursor not in str(caught.value)


def test_full_audit_honors_shared_deadline_before_empty_set(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record, registration, _ = _seed(store, count=0)

    def expired() -> None:
        raise TimeoutError("inspection deadline")

    with store._read() as conn, pytest.raises(TimeoutError):
        reads.audit_findings(conn, record, registration=registration, check=expired)


def _replace_findings(
    store: SessionStore,
    record: ArtifactRecord,
    items: tuple[t.Finding, ...],
) -> ArtifactRecord:
    record = replace(
        record,
        evidence=replace(
            record.evidence,
            finding_count=len(items),
            finding_set_digest=finding_set_digest(items),
        ),
    )
    with store._write() as conn:
        conn.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (
                encode_descriptor(record.descriptor),
                record.artifact_ref,
            ),
        )
        conn.execute(
            "UPDATE dataset_evidence SET finding_count=?,finding_set_digest=? WHERE artifact_ref=?",
            (
                len(items),
                record.evidence.finding_set_digest,
                record.artifact_ref,
            ),
        )
        conn.execute("DELETE FROM findings WHERE artifact_ref=?", (record.artifact_ref,))
        conn.executemany(
            "INSERT INTO findings VALUES(?,?,?,?,?)",
            [
                (
                    item.finding_id,
                    record.artifact_ref,
                    ordinal,
                    finding_identity(item),
                    encode_finding_body(item),
                )
                for ordinal, item in enumerate(items)
            ],
        )
    return record


def test_selected_store_coordinate_read_requires_exact_field_null_and_key_contract(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    record, registration, initial = _seed(store, count=1)
    field = _dimension()
    schema = d._make_schema((*record.descriptor.realized_schema.columns, field))
    row = d._make_row_contract(
        schema_version=1,
        shape_id=record.descriptor.row_contract.shape_id,
        schema=schema,
        coordinate_field_ids=(field.field_id,),
        key_field_ids=(field.field_id,),
        family_semantics=d._complete_from_schema(),
    )
    row_set = d._make_row_set_contract(
        schema_version=1,
        cardinality=d._keyed_cardinality(d._unknown_row_bound()),
        ordering=d._unordered_ordering(),
    )
    record = replace(
        record,
        descriptor=replace(
            record.descriptor,
            row_contract=row,
            row_set_contract=row_set,
            realized_schema=schema,
        ),
    )

    def key(item: t.Finding) -> str:
        return canonical_json(["region", item.coordinates[0].value])

    item = replace(
        initial[0],
        coordinates=(
            t.FindingCoordinateV1(
                field_id=field.field_id,
                identity=field.identity,
                value=None,
            ),
        ),
        canonical_item_key=canonical_json(["region", None]),
    )
    item = replace(item, finding_id=finding_identity(item))
    registration = replace(
        registration,
        coordinates=(reads.CoordinateRule(field, "dimension"),),
        canonical_item_key=key,
    )
    record = _replace_findings(store, record, (item,))
    with store._read() as conn:
        assert reads.finding(conn, record, item.finding_id, registration=registration) == item
        reads.audit_findings(conn, record, registration=registration)
    wrong = replace(item, coordinates=(replace(item.coordinates[0], value="changed"),))
    with store._write() as conn:
        conn.execute(
            "UPDATE findings SET finding_body_payload=? WHERE finding_ref=?",
            (
                encode_finding_body(wrong),
                item.finding_id,
            ),
        )
    with store._read() as conn, pytest.raises(IntegrityError):
        reads.finding(conn, record, item.finding_id, registration=registration)


def test_full_finding_set_streams_beyond_per_record_metadata_byte_bound(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record, registration, initial = _seed(store, count=300)
    suffix = "x" * 3500

    def key(item: t.Finding) -> str:
        assert isinstance(item.value, t.DeltaFindingValueV1)
        return canonical_json(["row", item.value.current_value - 2, suffix])

    items = tuple(replace(item, canonical_item_key=key(item)) for item in initial)
    items = tuple(replace(item, finding_id=finding_identity(item)) for item in items)
    assert sum(len(encode_finding_body(item)) for item in items) > 1_048_576
    record = _replace_findings(store, record, items)
    registration = replace(registration, canonical_item_key=key)
    checks = 0

    def checked() -> None:
        nonlocal checks
        checks += 1

    with store._read() as conn:
        reads.audit_findings(conn, record, registration=registration, check=checked)
    assert checks == len(items) + 1


def test_deep_bounded_cursor_returns_safe_selection_error_without_store_reads(
    tmp_path: Path,
) -> None:
    import base64

    store = _store(tmp_path)
    record, registration, _ = _seed(store, count=0)
    payload = b"[" * 1400 + b"0" + b"]" * 1400
    cursor = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    assert len(cursor) <= 4096
    with store._read() as conn:
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        with pytest.raises(MaterializationError) as caught:
            reads.findings(conn, record, cursor=cursor, registration=registration)
    assert statements == []
    assert caught.value.received == "invalid Finding selection"
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert cursor not in str(caught.value)
