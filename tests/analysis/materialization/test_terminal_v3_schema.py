"""The sole Store23 protocol closes every field and refuses historical v1 bytes."""

from dataclasses import fields, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from marivo.analysis.core.artifact_data import (
    ArtifactData,
    ArtifactManifest,
    DataAxis,
    DataColumn,
    DataSchema,
    ParquetHandle,
    ScalarDataType,
)
from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
from marivo.analysis.core.lineage import empty as empty_lineage
from marivo.analysis.materialization import terminal_protocol as protocol
from marivo.analysis.materialization.contracts import (
    FileEntry,
    LocalReceipt,
    canonical_json,
    parse_json,
)
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.terminal_store import describe

FROZEN = Path(__file__).with_name("terminal_v1_frozen.json").read_text().strip()


def manifest() -> ArtifactManifest:
    key = DataColumn("key", "customer", ScalarDataType("int64"), False)
    schema = DataSchema((DataAxis("axis", (key,), name="customer", kind="member"),), ())
    data = ArtifactData(
        schema,
        ParquetHandle(
            LocalReceipt("artifact/primary", (FileEntry("data.parquet", 1),), 1, 1), "a" * 64
        ),
    )
    metadata = FrameResultMetadata(
        "result",
        FrameAudit(
            (), EvaluationRecord(datetime(2020, 1, 1, tzinfo=timezone.utc), (), ("literal",))
        ),
    )
    return ArtifactManifest(
        "store",
        "artifact",
        "session",
        "run",
        data,
        describe(replace(metadata, lineage=empty_lineage(schema))),
    )


@pytest.mark.parametrize(
    "historical_format",
    [
        "marivo.frame_output/v1",
        "marivo.frame_output/v2",
        "marivo.frame_output/v3",
        "marivo.frame_output/v4",
        "marivo.frame_output/v5",
        "marivo.frame_output/v6",
        "marivo.frame_output/v7",
        "marivo.frame_output/v8",
        "marivo.frame_output/v9",
        "marivo.frame_output/v10",
        "marivo.frame_output/v11",
    ],
)
def test_historical_output_envelope_is_rejected_without_rewrite(historical_format: str) -> None:
    historical = parse_json(FROZEN)
    assert isinstance(historical, dict)
    historical["format"] = historical_format
    with pytest.raises(IntegrityError, match="expected Frame output v12"):
        protocol.decode(canonical_json(historical), ArtifactManifest)
    assert Path(__file__).with_name("terminal_v1_frozen.json").read_text().strip() == FROZEN


def test_one_explicit_closed_type_and_field_inventory() -> None:
    assert set(protocol._FIELDS) == {record.__name__ for record in protocol._RECORDS}
    for record in protocol._RECORDS:
        assert protocol._FIELDS[record.__name__] == tuple(field.name for field in fields(record))
    assert "DataProjection" not in protocol._FIELDS
    assert "CanonicalOrder" not in protocol._FIELDS
    assert protocol._FIELDS["InvocationInput"] == ("result_id",)
    assert protocol._FIELDS["EndpointPresence"] == ("path", "inherited_paths")
    assert protocol._FIELDS["DataComparison"] == (
        "alias",
        "values",
        "binding",
        "current_presence",
        "baseline_presence",
        "enclosing_presence",
        "namespace",
        "contexts",
        "current_fixed_axes",
        "baseline_fixed_axes",
        "named",
    )
    assert {"BoundTimeGrid", "CalendarCoordinate", "Metric", "CivilShiftTarget"}.isdisjoint(
        protocol._FIELDS
    )
    assert protocol._FIELDS["ArtifactManifest"] == (
        "store_id",
        "artifact_id",
        "session_id",
        "run_id",
        "data",
        "metadata",
        "method_facts",
        "description",
        "semantics_version",
    )
    saved = manifest()
    assert protocol.decode(protocol.encode(saved), ArtifactManifest) == saved
    assert saved.result_id == saved.metadata.result_id
    assert saved.semantics_version == "frame.v12"
    assert protocol.FORMAT == "marivo.frame_output/v12"


@pytest.mark.parametrize(
    "change",
    [
        "extra",
        "missing",
        "wrong_type",
        "semantics",
        "record",
        "format",
        "missing_description",
        "description_type",
    ],
)
def test_sole_manifest_refuses_unknown_or_wrong_fields(change: str) -> None:
    wire = parse_json(protocol.encode(manifest()))
    assert isinstance(wire, dict)
    record = wire["value"]
    assert isinstance(record, dict)
    body = record["fields"]
    assert isinstance(body, dict)
    if change == "extra":
        body["legacy"] = None
    elif change == "missing":
        del body["metadata"]
    elif change == "wrong_type":
        body["store_id"] = True
    elif change == "semantics":
        body["semantics_version"] = "frame.v2"
    elif change == "record":
        record["record"] = "Query"
    elif change == "missing_description":
        del body["description"]
    elif change == "description_type":
        body["description"] = 42
    else:
        wire["format"] = "marivo.terminal_output/v1"
    with pytest.raises(IntegrityError):
        protocol.decode(canonical_json(wire), ArtifactManifest)


def test_nested_exact_fields_are_required_and_old_query_marker_is_absent() -> None:
    saved = manifest()
    payload = protocol.encode(saved)
    assert '"name":"customer"' in payload
    assert '"at":null' in payload
    assert '"value_paths":[]' in payload
    assert '"comparisons":[]' in payload
    with pytest.raises(IntegrityError):
        protocol.decode(
            payload.replace('"comparisons":[]', '"legacy_comparisons":[]'), ArtifactManifest
        )
    invocation = protocol.encode(protocol.InvocationInput("result"))
    assert '"result_id":"result"' in invocation and '"kind"' not in invocation
    with pytest.raises(IntegrityError):
        protocol.decode(
            invocation.replace('"result_id":"result"', '"kind":"query"'), protocol.InvocationInput
        )


def test_saved_context_pool_is_shared_and_does_not_grow_with_rows(tmp_path: Path) -> None:
    import pandas as pd

    from marivo.analysis.core.frame_data import CompletedFrameData, CompletedFrameResult
    from marivo.analysis.dsl.frame import AnalysisFrame
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.terminal_store import manifest as read_manifest

    local = DatasetRuntime.create(tmp_path, "context-pool", timezone="UTC").local
    counts = []
    for size in (2, 200):
        schema = DataSchema(
            (DataAxis("id", (DataColumn("id", "id", ScalarDataType("int64"), False),), name="id"),),
            (DataColumn("amount", "amount", ScalarDataType("int64"), False, "USD"),),
            value_paths=(("amount",),),
        )
        metadata = FrameResultMetadata(
            "pool",
            FrameAudit((), EvaluationRecord(datetime(2026, 10, 29, tzinfo=timezone.utc), (), ())),
        )
        source = AnalysisFrame._from_memory(
            local,
            CompletedFrameResult(
                CompletedFrameData(
                    schema, pd.DataFrame({"id": range(size), "amount": range(size)})
                ),
                metadata,
            ),
        )
        result = source.select(
            {
                "total": source.over(source["amount"].sum()),
                "again": source.over(source["amount"].sum()),
            }
        ).persist()
        with local.store._read() as connection:
            saved = read_manifest(local.store, connection, result.artifact_id)
        assert saved is not None
        wire = parse_json(protocol.encode(saved))
        assert isinstance(wire, dict)
        pool = wire["contexts"]
        assert isinstance(pool, list)
        counts.append(len(pool))
        assert saved.metadata.lineage is not None
        assert saved.metadata.lineage.input_id == result.schema.input_id
        restored = protocol.decode(protocol.encode(saved), ArtifactManifest)
        contexts = restored.data.schema.value_contexts
        assert contexts[0][1] is contexts[1][1]
    assert counts == [1, 1]
