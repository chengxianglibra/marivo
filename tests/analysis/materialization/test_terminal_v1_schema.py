"""The sole Store12 protocol closes every field and refuses historical v1 bytes."""

from dataclasses import fields
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
from marivo.analysis.materialization import terminal_protocol as protocol
from marivo.analysis.materialization.contracts import (
    FileEntry,
    LocalReceipt,
    canonical_json,
    parse_json,
)
from marivo.analysis.materialization.errors import IntegrityError

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
    return ArtifactManifest("store", "artifact", "session", "run", data, metadata)


def test_historical_output_envelope_is_rejected_without_rewrite() -> None:
    before = FROZEN
    with pytest.raises(IntegrityError, match="expected Frame output v1"):
        protocol.decode(FROZEN, ArtifactManifest)
    assert before == FROZEN


def test_one_explicit_closed_type_and_field_inventory() -> None:
    assert set(protocol._FIELDS) == {record.__name__ for record in protocol._RECORDS}
    for record in protocol._RECORDS:
        assert protocol._FIELDS[record.__name__] == tuple(field.name for field in fields(record))
    assert "DataProjection" not in protocol._FIELDS
    assert "CanonicalOrder" not in protocol._FIELDS
    assert protocol._FIELDS["InvocationInput"] == ("result_id",)
    assert protocol._FIELDS["ArtifactManifest"] == (
        "store_id",
        "artifact_id",
        "session_id",
        "run_id",
        "data",
        "metadata",
        "semantics_version",
    )
    saved = manifest()
    assert protocol.decode(protocol.encode(saved), ArtifactManifest) == saved
    assert saved.result_id == saved.metadata.result_id


@pytest.mark.parametrize(
    "change", ["extra", "missing", "wrong_type", "semantics", "record", "format"]
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
    invocation = protocol.encode(protocol.InvocationInput("result"))
    assert '"result_id":"result"' in invocation and '"kind"' not in invocation
    with pytest.raises(IntegrityError):
        protocol.decode(
            invocation.replace('"result_id":"result"', '"kind":"query"'), protocol.InvocationInput
        )
