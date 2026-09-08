"""Real Delta extractor and Store transaction checks over exact retained contracts."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, localcontext
from pathlib import Path

import pyarrow as pa
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.evidence import _dataset_reads as reads
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.materialization.comparison_publication import build_delta_publication
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    RunDatasetInput,
    decode_descriptor,
    descriptor_payload,
    encode_descriptor,
    schema_fingerprint,
)
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.publication import make_descriptor, materialization_contract
from marivo.analysis.materialization.storage import DatasetWriteResult
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.observation.contracts import make_ids
from marivo.analysis.operators.contracts import DeltaSemantics
from marivo.refs import ref
from tests.lazy_materialization_fixtures import descriptor
from tests.lazy_observation_fixtures import make_sources


def _delta(
    count: int, *, entity: bool = False, temporal: bool = False
) -> tuple[ArtifactDescriptor, tuple[pa.RecordBatch, ...]]:
    source = make_sources(session_id="session")
    metric = source.observe(
        ref.metric("sales.revenue"), time_scope=time_scope(start="2026-02-01", end="2026-03-01")
    )
    if not entity:
        metric = metric.with_dimensions(ref.dimension("sales.customers.region"))
        if temporal:
            metric = metric.with_time_axis(
                ref.time_dimension("sales.orders.order_time"), grain=grain("day")
            )
        metric = metric.aggregate()
    result = metric.compare(metric)
    realized = d._make_schema(
        tuple(
            replace(
                column,
                _token=d._CORE_TOKEN,
                physical_type_state=d._resolved_type(
                    column.logical_type_id, ids=result._registration.ids
                ),
            )
            for column in result.schema.columns
        )
    )
    base = descriptor()
    receipt = replace(
        base.storage_receipt,
        schema_fingerprint=schema_fingerprint(realized),
        realized_row_count=count,
    )
    retained = make_descriptor(
        result,
        materialization_contract(result),
        DatasetWriteResult(receipt, (), realized, count),
        (("dataset.final_row_key_unique", 0),),
    )
    rows: list[dict[str, object]] = []
    for index in range(count):
        row: dict[str, object] = {
            "current_value": float(index + 2),
            "baseline_value": 1.0,
            "delta": float(index + 1),
            "relative_delta": float(index + 1),
            "coordinate_presence": "matched",
            "calculation_status": "ok",
            "relative_delta_status": "ok",
        }
        if temporal:
            row.update(
                comparison_ordinal=0, current_time=date(2026, 2, 1), baseline_time=date(2026, 2, 1)
            )
        for column in realized.columns:
            if column.role_id == "dimension":
                row[column.name] = f"region-{index:04}"
            elif column.identity.kind == "entity_identity":
                row[column.name] = index + 1
        rows.append({column.name: row[column.name] for column in realized.columns})
    return retained, tuple(pa.Table.from_pylist(rows).to_batches(max_chunksize=64))


def _prepared(
    count: int, *, entity: bool = False
) -> tuple[ArtifactDescriptor, tuple[t.Finding, ...]]:
    value, batches = _delta(count, entity=entity)
    return build_delta_publication(
        value, iter(batches), artifact_ref="artifact", session_ref="session"
    )


def _store(tmp_path: Path, value: ArtifactDescriptor) -> SessionStore:
    store = SessionStore(tmp_path)
    store.create_session("Delta publication", session_ref="session")
    store.admit(
        "session",
        "e" * 64,
        RunDatasetInput(
            value.definition_fingerprint,
            value.row_contract.shape_id,
            value.row_contract_fingerprint,
            value.row_set_contract_fingerprint,
            ("metric.compare",),
            ("metric:sales.revenue",),
        ),
        run_ref="run",
    )
    return store


def test_production_extractor_caps_1001_rows_and_cold_reads_exact_order(tmp_path: Path) -> None:
    value, items = _prepared(1001)
    evidence = value.delta_evidence
    assert evidence is not None
    assert evidence.eligible_finding_count == 1001
    assert evidence.emitted_finding_count == 1000 and evidence.finding_truncated
    assert len(items) == 1000
    assert isinstance(items[0].value, t.DeltaFindingValueV1)
    assert items[0].value.delta == 1001.0
    assert isinstance(items[-1].value, t.DeltaFindingValueV1)
    assert items[-1].value.delta == 2.0
    store = _store(tmp_path, value)
    committed = store.publish("run", "artifact", value, findings=items)
    recovered = SessionStore(tmp_path).artifact("artifact")
    assert recovered is not None and recovered.evidence == committed.evidence
    assert decode_descriptor(encode_descriptor(value)) == value
    with store._read() as conn:
        page = reads.findings(conn, recovered, limit=100)
        assert tuple(item.finding_id for item in page.items) == tuple(
            item.finding_id for item in items[:100]
        )
        assert all(
            item.committed_at == datetime.fromisoformat(recovered.committed_at)
            for item in page.items
        )
        reads.audit_findings(conn, recovered)


def test_selected_finding_page_does_not_decode_corrupt_unselected_body(tmp_path: Path) -> None:
    value, items = _prepared(3)
    store = _store(tmp_path, value)
    record = store.publish("run", "artifact", value, findings=items)
    with store._write() as conn:
        conn.execute(
            "UPDATE findings SET finding_body_payload='corrupt' WHERE finding_ref=?",
            (items[-1].finding_id,),
        )
    with store._read() as conn:
        assert len(reads.findings(conn, record, limit=2).items) == 2
        with pytest.raises(IntegrityError):
            reads.finding(conn, record, items[-1].finding_id)
        with pytest.raises(IntegrityError):
            reads.audit_findings(conn, record)


@pytest.mark.parametrize(
    "point", ["insert_artifact", "insert_evidence", "insert_findings", "insert_terminal"]
)
def test_nonzero_findings_share_one_atomic_publication(tmp_path: Path, point: str) -> None:
    value, items = _prepared(2)
    store = _store(tmp_path, value)

    def fail(event: str) -> None:
        if event == point:
            raise RuntimeError("injected publication failure")

    with pytest.raises(RuntimeError):
        store.publish("run", "artifact", value, findings=items, event=fail)
    with store._read() as conn:
        for table in (
            "dataset_artifacts",
            "dataset_evidence",
            "findings",
            "analysis_action_run_terminals",
        ):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_entity_delta_exposes_only_bounded_aggregate_evidence() -> None:
    value, items = _prepared(3, entity=True)
    assert not items
    assert value.delta_evidence is not None
    assert value.delta_evidence.eligible_finding_count == 0
    assert value.delta_evidence.coordinate_presence_counts == (
        ("matched", 3),
        ("current_only", 0),
        ("baseline_only", 0),
    )
    evidence = descriptor_payload(value)["delta_evidence"]
    assert isinstance(evidence, dict)
    assert set(evidence) == {
        "schema",
        "coordinate_presence_counts",
        "calculation_status_counts",
        "relative_status_counts",
        "matched_count",
        "unpaired_count",
        "numeric_promotion_id",
        "approximate",
        "eligible_finding_count",
        "emitted_finding_count",
        "finding_truncated",
        "finding_set_digest",
    }


def test_corrupt_operand_order_and_incomplete_evidence_fail_cold_decode() -> None:
    value, _ = _prepared(2)
    with pytest.raises(IntegrityError):
        decode_descriptor(
            encode_descriptor(
                replace(value, comparison_inputs=tuple(reversed(value.comparison_inputs)))
            )
        )
    assert value.delta_evidence is not None
    with pytest.raises(IntegrityError):
        decode_descriptor(
            encode_descriptor(
                replace(value, delta_evidence=replace(value.delta_evidence, matched_count=3))
            )
        )


def test_complete_stream_required_before_publication_evidence_is_valid() -> None:
    value, batches = _delta(100)
    with pytest.raises(IntegrityError, match="incomplete final Delta"):
        build_delta_publication(
            value, iter(batches[:1]), artifact_ref="artifact", session_ref="session"
        )


def test_time_findings_keep_paired_times_without_changing_canonical_row_key(tmp_path: Path) -> None:
    value, batches = _delta(2, temporal=True)
    value, items = build_delta_publication(
        value, iter(batches), artifact_ref="artifact", session_ref="session"
    )
    names = {field.field_id: field.name for field in value.realized_schema.columns}
    assert tuple(names[item.field_id] for item in items[0].coordinates)[-3:] == (
        "comparison_ordinal",
        "current_time",
        "baseline_time",
    )
    assert "2026-02-01" not in items[0].canonical_item_key
    store = _store(tmp_path, value)
    record = store.publish("run", "artifact", value, findings=items)
    with store._read() as conn:
        assert len(reads.findings(conn, record).items[0].coordinates) == 4
        reads.audit_findings(conn, record)


@pytest.mark.parametrize(
    "changes",
    [
        {
            "coordinate_presence": "current_only",
            "calculation_status": "missing_side",
            "delta": None,
            "relative_delta": None,
            "relative_delta_status": "delta_unavailable",
        },
        {
            "calculation_status": "null_input",
            "delta": None,
            "relative_delta": None,
            "relative_delta_status": "delta_unavailable",
        },
        {"relative_delta": None, "relative_delta_status": "baseline_zero"},
        {"relative_delta": None, "relative_delta_status": "delta_unavailable"},
        {"current_value": float("inf"), "delta": float("inf")},
        {"delta": float("nan")},
        {"relative_delta": -1.0},
    ],
)
def test_quality_rejects_arithmetic_and_status_contradictions(changes: dict[str, object]) -> None:
    value, batches = _delta(1)
    semantics = value.row_contract.family_semantics
    assert isinstance(semantics, DeltaSemantics)
    value = replace(
        value,
        row_contract=replace(
            value.row_contract,
            _token=d._CORE_TOKEN,
            family_semantics=replace(semantics, _token=d._CORE_TOKEN, exact_empty_zero=False),
        ),
    )
    row = batches[0].to_pylist()[0]
    row.update(changes)
    rows = pa.Table.from_pylist([row]).to_batches()
    with pytest.raises(IntegrityError):
        build_delta_publication(value, rows, artifact_ref="artifact", session_ref="session")


def test_equal_magnitude_findings_follow_typed_numeric_row_key_order() -> None:
    value, batches = _delta(2, temporal=True)
    rows = batches[0].to_pylist()
    dimension_name = next(
        field.name for field in value.realized_schema.columns if field.role_id == "dimension"
    )
    for row, ordinal in zip(rows, (10, 2), strict=True):
        row.update(current_value=2.0, delta=1.0, relative_delta=1.0, comparison_ordinal=ordinal)
        row[dimension_name] = "one-region"
    _, items = build_delta_publication(
        value,
        pa.Table.from_pylist(rows).to_batches(),
        artifact_ref="artifact",
        session_ref="session",
    )
    ordinal_field = next(
        field.field_id
        for field in value.realized_schema.columns
        if field.name == "comparison_ordinal"
    )
    assert tuple(
        next(
            coordinate.value
            for coordinate in item.coordinates
            if coordinate.field_id == ordinal_field
        )
        for item in items
    ) == (2, 10)


def test_operand_population_authority_cannot_be_forged_independently_of_basis() -> None:
    value, _ = _prepared(2)
    current, baseline = value.comparison_inputs
    forged = replace(
        baseline,
        population_authority=replace(baseline.population_authority, entity_ref="sales.other"),
    )
    with pytest.raises(IntegrityError):
        decode_descriptor(encode_descriptor(replace(value, comparison_inputs=(current, forged))))


def _decimal_publication(
    deltas: tuple[Decimal, ...],
) -> tuple[ArtifactDescriptor, tuple[t.Finding, ...]]:
    value, _ = _delta(len(deltas))
    semantics = value.row_contract.family_semantics
    assert isinstance(semantics, DeltaSemantics)
    ids = make_ids(())

    def decimal_schema(schema: d.DatasetSchema) -> d.DatasetSchema:
        return d._make_schema(
            tuple(
                replace(
                    column,
                    _token=d._CORE_TOKEN,
                    logical_type_id="decimal",
                    physical_type_state=d._resolved_type("decimal", ids=ids)
                    if isinstance(column.physical_type_state, d._ResolvedPhysicalType)
                    else d._deferred_type("decimal", ids=ids),
                )
                if column.role_id == "comparison_value"
                else column
                for column in schema.columns
            )
        )

    realized = decimal_schema(value.realized_schema)
    value = replace(
        value,
        row_contract=replace(
            value.row_contract,
            _token=d._CORE_TOKEN,
            schema=decimal_schema(value.row_contract.schema),
            family_semantics=replace(semantics, _token=d._CORE_TOKEN, numeric_type="decimal"),
        ),
        realized_schema=realized,
        storage_receipt=replace(
            value.storage_receipt, schema_fingerprint=schema_fingerprint(realized)
        ),
    )
    dimension = next(column.name for column in realized.columns if column.role_id == "dimension")
    records: list[dict[str, object]] = []
    for index, delta in enumerate(deltas):
        record: dict[str, object] = {
            dimension: f"region-{index:04}",
            "coordinate_presence": "matched",
            "current_value": delta,
            "baseline_value": Decimal(0),
            "delta": delta,
            "relative_delta": None,
            "calculation_status": "ok",
            "relative_delta_status": "baseline_zero",
        }
        records.append({column.name: record[column.name] for column in realized.columns})
    return build_delta_publication(
        value,
        pa.Table.from_pylist(records).to_batches(max_chunksize=64),
        artifact_ref="artifact",
        session_ref="session",
    )


@pytest.mark.parametrize(
    "deltas,expected",
    [
        (
            ("-10000000000000000000000000000000000001", "-10000000000000000000000000000000000002"),
            (1, 0),
        ),
        (
            ("10000000000000000000000000000000000001", "-10000000000000000000000000000000000002"),
            (1, 0),
        ),
        (
            ("-10000000000000000000000000000000000001", "10000000000000000000000000000000000001"),
            (0, 1),
        ),
    ],
)
def test_decimal_finding_magnitude_preserves_all_digits_and_signed_ties(
    deltas: tuple[str, str],
    expected: tuple[int, int],
) -> None:
    with localcontext() as context:
        context.prec = 28
        _, items = _decimal_publication(tuple(Decimal(value) for value in deltas))
    assert tuple(item.coordinates[0].value for item in items) == tuple(
        f"region-{index:04}" for index in expected
    )
    assert tuple(
        item.value.delta for item in items if isinstance(item.value, t.DeltaFindingValueV1)
    ) == tuple(Decimal(deltas[index]) for index in expected)


def test_decimal_finding_cap_selects_exact_largest_negative_magnitudes() -> None:
    deltas = tuple(Decimal(str(-(10**37 + index + 1))) for index in range(1001))
    with localcontext() as context:
        context.prec = 28
        value, items = _decimal_publication(deltas)
    assert value.delta_evidence is not None and value.delta_evidence.finding_truncated
    assert value.delta_evidence.eligible_finding_count == 1001
    assert tuple(item.coordinates[0].value for item in items) == tuple(
        f"region-{index:04}" for index in range(1000, 0, -1)
    )
