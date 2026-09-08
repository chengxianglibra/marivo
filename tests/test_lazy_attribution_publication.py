"""Independent Attribution publication, cold authority, and exact retained proof checks."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.evidence._dataset_codec import finding_identity
from marivo.analysis.evidence._dataset_reads import _validate
from marivo.analysis.materialization.attribution_publication import (
    build_attribution_publication,
    finding_registration,
    summarize_attribution_frame,
)
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    ArtifactRecord,
    canonical_json,
    decode_descriptor,
    descriptor_payload,
    encode_descriptor,
    evidence_for,
    schema_fingerprint,
)
from marivo.analysis.materialization.errors import IntegrityError, MaterializationError
from marivo.analysis.materialization.publication import make_descriptor, materialization_contract
from marivo.analysis.materialization.storage import (
    DatasetWriteResult,
    _matches_type,
    _RowValidator,
    _to_dataframe,
)
from marivo.refs import ref
from tests.lazy_materialization_fixtures import descriptor as base_descriptor
from tests.lazy_observation_fixtures import make_sources

REGION = ref.dimension("sales.customers.region")
CHANNEL = ref.dimension("sales.orders.channel")


def _value(
    mode: Literal["joint", "hierarchy"] = "joint",
) -> tuple[ArtifactDescriptor, pd.DataFrame]:
    sources = make_sources(session_id="session")
    axes = (REGION,) if mode == "joint" else (REGION, CHANNEL)
    metric = sources.observe(ref.metric("sales.revenue")).with_dimensions(*axes).aggregate()
    logical = metric.compare(metric).attribute(axes=axes, mode=mode)
    realized = d._make_schema(
        tuple(
            replace(
                field,
                _token=d._CORE_TOKEN,
                physical_type_state=d._resolved_type(
                    field.logical_type_id, ids=logical._registration.ids
                ),
            )
            for field in logical.schema.columns
        )
    )
    records: list[dict[str, object]] = []
    for width in (1,) if mode == "joint" else (1, 2):
        for index, (member, other, current, baseline, contribution) in enumerate(
            (
                ("a", False, 3.0, 0.0, 3.0),
                (None, False, 0.0, 2.0, -2.0),
                (None, True, 0.0, 0.0, 0.0),
            )
        ):
            record: dict[str, object] = {
                "region": member,
                "active_axis_mask": tuple(position < width for position in range(len(axes))),
                "other_mask": (other, *((False,) if len(axes) == 2 else ())),
                "current_value": current,
                "baseline_value": baseline,
                "overall_delta": 1.0,
                "contribution": contribution,
                "share_of_total_delta": contribution,
                "share_of_positive_pool": 1.0 if contribution > 0 else 0.0,
                "share_of_negative_pool": 1.0 if contribution < 0 else 0.0,
                "contribution_rank": index + 1,
                "status": "ok",
            }
            if mode == "hierarchy":
                record["channel"] = "web" if width == 2 else None
            records.append(record)
    rows = pd.DataFrame(records, columns=[field.name for field in realized.columns])
    primary = replace(
        base_descriptor().storage_receipt,
        schema_fingerprint=schema_fingerprint(realized),
        realized_row_count=len(rows),
    )
    value = make_descriptor(
        logical,
        materialization_contract(logical),
        DatasetWriteResult(primary, (), realized, len(rows)),
        (),
    )
    return value, rows


def _publish(
    value: ArtifactDescriptor, rows: pd.DataFrame
) -> tuple[ArtifactDescriptor, tuple[t.Finding, ...]]:
    return build_attribution_publication(
        value, rows, artifact_ref="artifact", session_ref="session"
    )


@pytest.mark.parametrize("mode", ["joint", "hierarchy"])
def test_complete_proof_findings_and_cold_descriptor_are_independent(
    mode: Literal["joint", "hierarchy"], monkeypatch: pytest.MonkeyPatch
) -> None:
    value, rows = _value(mode)
    published, findings = _publish(value, rows)
    assert len(findings) == len(rows)
    assert len({item.canonical_item_key for item in findings}) == len(rows)
    summary = published.attribution_evidence
    assert summary is not None and summary.complete
    assert summary.scope_count == 1 and summary.resolution_count == (1 if mode == "joint" else 2)
    assert summary.max_reconciliation_error == 0.0
    assert (
        sum(
            item.value.contribution
            for item in findings
            if isinstance(item.value, t.ContributionFindingValueV1)
            and all(item.value.active_axis_mask)
        )
        == 1.0
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("cold Attribution cannot consult source authority")

    monkeypatch.setattr("marivo.semantic.validator.Registry.__init__", forbidden)
    encoded = encode_descriptor(published)
    recovered = decode_descriptor(encoded)
    assert encode_descriptor(recovered) == encoded
    registration = finding_registration(recovered)
    assert registration is not None
    record = ArtifactRecord(
        "artifact",
        "session",
        "a" * 64,
        recovered,
        "2026-09-08T00:00:00+00:00",
        "run",
        evidence_for(recovered),
    )
    for item in findings:
        _validate(item, record, registration)


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("contribution", 4.0),
        ("overall_delta", 2.0),
        ("share_of_positive_pool", 0.5),
        ("share_of_negative_pool", 1.0),
        ("contribution_rank", 2),
        ("active_axis_mask", (False,)),
        ("other_mask", (True,)),
        ("current_value", float("inf")),
    ],
)
def test_publication_rejects_numeric_and_mask_contradictions(
    field: str, replacement: object
) -> None:
    value, rows = _value()
    rows[field] = pd.Series([replacement, *rows[field].iloc[1:].tolist()], dtype=object)
    with pytest.raises(IntegrityError):
        _publish(value, rows)


def test_each_hierarchy_scope_requires_every_authored_resolution() -> None:
    value, rows = _value("hierarchy")
    rows = rows.iloc[:3]
    value = replace(value, storage_receipt=replace(value.storage_receipt, realized_row_count=3))
    with pytest.raises(IntegrityError, match="incomplete Attribution resolution"):
        _publish(value, rows)


def test_selected_continuation_keeps_full_proof_and_emits_no_findings() -> None:
    value, rows = _value()
    published, _ = _publish(value, rows)
    selected = replace(
        published,
        definition_fingerprint="ds_" + "b" * 64,
        storage_receipt=replace(published.storage_receipt, realized_row_count=1),
        dataset_materialization_contract=replace(
            published.dataset_materialization_contract,
            producer_id="attribution.limit",
            finding_policy_id="zero_findings@v1",
        ),
    )
    continued, findings = _publish(selected, rows.iloc[:1])
    assert findings == ()
    proof = continued.attribution_evidence
    assert proof is not None and not proof.complete and proof.complete_row_count == 3
    assert published.attribution_evidence is not None
    assert proof.mapped_membership_digest == published.attribution_evidence.mapped_membership_digest
    assert proof.origin_definition_fingerprint == value.definition_fingerprint


def test_logical_row_suffix_uses_complete_intermediate_proof() -> None:
    value, rows = _value()
    summary = summarize_attribution_frame(rows, value.row_contract)
    selected = replace(value, storage_receipt=replace(value.storage_receipt, realized_row_count=1))
    result, findings = build_attribution_publication(
        selected,
        rows.iloc[:1],
        artifact_ref="artifact",
        session_ref="session",
        source_summary=summary,
        continuation=True,
        proof_definition_fingerprint=value.definition_fingerprint,
    )
    assert findings == ()
    assert result.attribution_evidence is not None
    assert result.attribution_evidence.complete_row_count == 3
    assert not result.attribution_evidence.complete


@pytest.mark.parametrize("change", ["method", "digest", "count", "resolution"])
def test_cold_evidence_corruption_fails_closed(change: str) -> None:
    value, rows = _value()
    published, _ = _publish(value, rows)
    payload = descriptor_payload(published)
    summary = payload["attribution_evidence"]
    assert isinstance(summary, dict)
    if change == "method":
        summary["method"] = "unknown@v1"
    elif change == "digest":
        summary["mapped_membership_digest"] = "bad"
    elif change == "count":
        summary["complete_row_count"] = 4
    else:
        summary["resolution_count"] = 0
    with pytest.raises(IntegrityError):
        decode_descriptor(canonical_json(payload))


def test_cold_finding_rejects_unregistered_active_prefix() -> None:
    value, rows = _value("hierarchy")
    published, findings = _publish(value, rows)
    registration = finding_registration(published)
    assert registration is not None
    item = findings[0]
    assert isinstance(item.value, t.ContributionFindingValueV1)
    changed = replace(
        item, value=replace(item.value, active_axis_mask=(False, True), other_mask=(False, False))
    )
    changed = replace(changed, canonical_item_key=registration.canonical_item_key(changed))
    changed = replace(changed, finding_id=finding_identity(changed))
    record = ArtifactRecord(
        "artifact",
        "session",
        "a" * 64,
        published,
        "2026-09-08T00:00:00+00:00",
        "run",
        evidence_for(published),
    )
    with pytest.raises(IntegrityError, match="mask or method"):
        _validate(changed, record, registration)


def test_mask_physical_type_and_values_preserve_exact_tuple_identity() -> None:
    value, rows = _value()
    assert _matches_type("bool_tuple:1", pa.list_(pa.bool_()))
    assert _matches_type("bool_tuple:1", pa.list_(pa.bool_(), 1))
    assert not _matches_type("bool_tuple:1", pa.list_(pa.bool_(), 2))
    assert not _matches_type("bool_tuple:1", pa.list_(pa.int64()))
    table = pa.Table.from_pandas(rows, preserve_index=False)
    restored = _to_dataframe(table, value.row_contract)
    assert restored["active_axis_mask"].tolist() == [(True,)] * 3
    bad = rows.iloc[:1].copy()
    bad["active_axis_mask"] = pd.Series([(True, False)], dtype=object)
    with pytest.raises(MaterializationError, match="fixed-length boolean"):
        _RowValidator(value.row_contract, value.row_set_contract).accept(
            pa.RecordBatch.from_pandas(bad, preserve_index=False)
        )


@pytest.mark.parametrize(
    "field,replacement",
    [("active_axis_mask", (False, True)), ("other_mask", (False, True)), ("channel", "web")],
)
def test_storage_checks_each_later_batch_against_its_hierarchy_mask_contract(
    field: str, replacement: object
) -> None:
    value, rows = _value("hierarchy")
    validator = _RowValidator(value.row_contract, value.row_set_contract)
    validator.accept(pa.RecordBatch.from_pandas(rows.iloc[:1], preserve_index=False))
    malformed = rows.iloc[1:2].copy()
    malformed[field] = pd.Series([replacement], index=malformed.index, dtype=object)
    with pytest.raises(
        MaterializationError, match=r"invalid partition mask|invalid Attribution axis"
    ):
        validator.accept(pa.RecordBatch.from_pandas(malformed, preserve_index=False))


def test_finding_cap_uses_complete_deterministic_global_magnitude_order() -> None:
    value, rows = _value()
    count = 1002
    expanded = pd.concat([rows.iloc[:1]] * count, ignore_index=True)
    expanded["region"] = [f"member-{index:04}" for index in range(count)]
    expanded["current_value"] = 1.0
    expanded["baseline_value"] = 0.0
    expanded["contribution"] = 1.0
    expanded["overall_delta"] = float(count)
    expanded["share_of_total_delta"] = 1.0 / count
    expanded["share_of_positive_pool"] = 1.0 / count
    expanded["share_of_negative_pool"] = pd.Series([None] * count, dtype=object)
    expanded["contribution_rank"] = list(range(1, count + 1))
    value = replace(value, storage_receipt=replace(value.storage_receipt, realized_row_count=count))
    first, findings = _publish(value, expanded)
    second, repeated = _publish(value, expanded)
    assert len(findings) == 1000
    assert findings == repeated
    assert first.attribution_evidence == second.attribution_evidence
    assert first.attribution_evidence is not None
    assert first.attribution_evidence.eligible_finding_count == count
    assert first.attribution_evidence.finding_truncated
    assert findings[0].coordinates[0].value == "member-0000"
    assert findings[-1].coordinates[0].value == "member-0999"


def test_delta_retained_parts_validate_exact_side_state_and_presence() -> None:
    from marivo.analysis.materialization.retained import checked_component_batches, component_schema
    from marivo.analysis.observation.fold_contracts import fold_state_columns
    from marivo.analysis.operators.attribution_contracts import (
        delta_part_authorities,
        delta_presence_name,
        delta_state_name,
    )

    sources = make_sources()
    metric = sources.observe(ref.metric("sales.revenue")).with_dimensions(REGION).aggregate()
    row = metric.compare(metric).row_contract
    role, authority = delta_part_authorities(row)[0]
    side = "current"
    fields = [pa.field("region", pa.string())]
    values: list[list[object]] = [["a"]]
    for name, kind, _ in fold_state_columns(authority):
        physical = (
            pa.int64()
            if kind == "integer"
            else pa.bool_()
            if kind == "boolean"
            else pa.timestamp("us")
            if kind == "timestamp"
            else pa.float64()
        )
        fields.append(pa.field(delta_state_name(side, name), physical))
        values.append(
            [1 if kind in ("integer", "timestamp") else True if kind == "boolean" else 1.0]
        )
    fields.append(pa.field(delta_presence_name(side), pa.bool_()))
    values.append([True])
    schema = pa.schema(fields)
    batch = pa.RecordBatch.from_arrays(
        [pa.array(items, type=field.type) for items, field in zip(values, fields, strict=True)],
        schema=schema,
    )
    assert tuple(checked_component_batches((batch,), row, role)) == (batch,)
    assert component_schema(row, role, schema) == ("region",)
    with pytest.raises(IntegrityError, match="part fields differ"):
        component_schema(row, role, pa.schema(fields[:-1]))
    false_presence = batch.set_column(len(fields) - 1, fields[-1], pa.array([False]))
    with pytest.raises(IntegrityError, match="side component support"):
        tuple(checked_component_batches((false_presence,), row, role))
    required = next(
        index
        for index, (_, _, nullable) in enumerate(fold_state_columns(authority), 1)
        if not nullable
    )
    missing = batch.set_column(required, fields[required], pa.nulls(1, type=fields[required].type))
    with pytest.raises(IntegrityError, match="side component support"):
        tuple(checked_component_batches((missing,), row, role))


def test_attribution_findings_publish_and_roll_back_in_the_existing_store(tmp_path: Path) -> None:
    from marivo.analysis.evidence._dataset_reads import audit_findings
    from marivo.analysis.evidence._dataset_reads import findings as read_findings
    from marivo.analysis.materialization.contracts import RunDatasetInput
    from marivo.analysis.materialization.store import SessionStore

    value, rows = _value()
    published, findings = _publish(value, rows)
    store = SessionStore(tmp_path)
    store.create_session("attribution", session_ref="session")
    request = RunDatasetInput(
        value.definition_fingerprint,
        value.row_contract.shape_id,
        value.row_contract_fingerprint,
        value.row_set_contract_fingerprint,
        ("delta.attribute",),
        ("metric:sales.revenue",),
    )
    store.admit("session", "a" * 64, request, run_ref="run")

    def fail(point: str) -> None:
        if point == "insert_findings":
            raise RuntimeError("injected Attribution publication rollback")

    with pytest.raises(RuntimeError, match="rollback"):
        store.publish("run", "artifact", published, findings=findings, event=fail)
    with store._read() as conn:
        assert conn.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM findings").fetchone()[0] == 0
    store.publish("run", "artifact", published, findings=findings)
    cold = SessionStore(tmp_path).lookup("session", "a" * 64)
    assert cold is not None and cold.evidence.finding_count == 3
    with store._read() as conn:
        selected = read_findings(conn, cold)
        assert len(selected.items) == 3
        audit_findings(conn, cold)


def test_entity_publication_accepts_only_bounded_source_proof_and_no_identity_rows() -> None:
    from marivo.analysis.materialization.attribution_publication import AttributionSourceSummary

    sources = make_sources(session_id="session")
    metric = sources.observe(ref.metric("sales.revenue"))
    logical = metric.compare(metric).attribute(axes=(REGION,), mode="joint")
    realized = d._make_schema(
        tuple(
            replace(
                field,
                _token=d._CORE_TOKEN,
                physical_type_state=d._resolved_type(
                    field.logical_type_id, ids=logical._registration.ids
                ),
            )
            for field in logical.schema.columns
        )
    )
    primary = replace(
        base_descriptor().storage_receipt,
        schema_fingerprint=schema_fingerprint(realized),
        realized_row_count=3,
    )
    value = make_descriptor(
        logical, materialization_contract(logical), DatasetWriteResult(primary, (), realized, 3), ()
    )
    summary = AttributionSourceSummary(3, 3, (("ok", 3), ("zero_total_delta", 0)), 0.0, "a" * 64)
    published, findings = build_attribution_publication(
        value, None, artifact_ref="artifact", session_ref="session", source_summary=summary
    )
    assert findings == () and finding_registration(published) is None
    assert (
        decode_descriptor(encode_descriptor(published)).attribution_evidence
        == published.attribution_evidence
    )
    with pytest.raises(IntegrityError, match="without identity rows"):
        build_attribution_publication(
            value,
            pd.DataFrame(),
            artifact_ref="artifact",
            session_ref="session",
            source_summary=summary,
        )
    malformed = replace(summary, resolution_count=2)
    corrupted, _ = build_attribution_publication(
        value, None, artifact_ref="artifact", session_ref="session", source_summary=malformed
    )
    with pytest.raises(IntegrityError, match="Evidence counts"):
        decode_descriptor(encode_descriptor(corrupted))


@pytest.mark.parametrize("missing", [True, False])
def test_cold_attribution_requires_exact_side_fold_and_partition_authority(missing: bool) -> None:
    from marivo.analysis.observation.fold_contracts import decode_fold_authority

    value, rows = _value()
    published, _ = _publish(value, rows)
    assert published.attribution_fold_authority is not None
    if missing:
        corrupted = replace(published, attribution_fold_authority=None)
    else:
        current, baseline = published.attribution_fold_authority
        authority = decode_fold_authority(current)
        metric = authority.metrics[0].model_copy(update={"axis_partitions": ()})
        current = authority.model_copy(update={"metrics": (metric,)}).to_json()
        corrupted = replace(published, attribution_fold_authority=(current, baseline))
    with pytest.raises(MaterializationError):
        decode_descriptor(encode_descriptor(corrupted))


def test_source_proof_publication_streams_rows_without_regrouping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value, rows = _value()
    summary = summarize_attribution_frame(rows, value.row_contract)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("complete source output must not be collected into Python groups")

    monkeypatch.setattr(
        "marivo.analysis.materialization.attribution_publication._summarize_rows", forbidden
    )
    batches = pa.Table.from_pandas(rows, preserve_index=False).to_batches(max_chunksize=1)
    published, findings = build_attribution_publication(
        value, iter(batches), artifact_ref="artifact", session_ref="session", source_summary=summary
    )
    assert len(findings) == 3
    assert (
        published.attribution_evidence is not None
        and published.attribution_evidence.scope_count == 1
    )
    with pytest.raises(IntegrityError, match="complete validated source proof"):
        build_attribution_publication(
            value,
            iter(batches[:1]),
            artifact_ref="artifact",
            session_ref="session",
            source_summary=summary,
        )


def test_engine_unordered_contract_read_enforces_exact_hierarchy_key_order() -> None:
    import ibis

    from marivo.analysis.materialization.engine import ordered_relation

    value, rows = _value("hierarchy")
    assert value.row_set_contract.ordering.kind == "unordered"
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table(
            "rows", pa.Table.from_pandas(rows.iloc[::-1], preserve_index=False)
        )
        native = backend.to_pyarrow(
            ordered_relation(table, value.row_contract, value.row_set_contract)
        )
        validator = _RowValidator(value.row_contract, value.row_set_contract)
        for batch in native.to_batches(max_chunksize=1):
            validator.accept(batch)
        validator.finish()
        assert validator.count == len(rows)
        assert (
            native.column("active_axis_mask").to_pylist()
            == [[True, False]] * 3 + [[True, True]] * 3
        )
    finally:
        backend.disconnect()


@pytest.mark.parametrize("sign", [1, -1])
def test_finding_magnitude_order_preserves_decimal_digits_above_ambient_precision(
    sign: int,
) -> None:
    from decimal import Decimal

    from marivo.analysis.materialization.attribution_publication import _candidate_compare

    value, rows = _value()
    _, findings = _publish(value, rows)
    first, second = findings[:2]
    assert isinstance(first.value, t.ContributionFindingValueV1)
    assert isinstance(second.value, t.ContributionFindingValueV1)
    lower = Decimal("10000000000000000000000000000001")
    upper = Decimal("10000000000000000000000000000002")
    if sign < 0:
        lower, upper = lower.copy_negate(), upper.copy_negate()
    first = replace(first, value=replace(first.value, contribution=lower))
    second = replace(second, value=replace(second.value, contribution=upper))
    assert _candidate_compare(first, second) > 0
    assert _candidate_compare(second, first) < 0
