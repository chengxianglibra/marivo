"""The sole Store26 protocol closes every field and refuses historical v1 bytes."""

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
from marivo.analysis.core.frame_metadata import EvaluationRecord, FrameResultMetadata
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
        EvaluationRecord(datetime(2020, 1, 1, tzinfo=timezone.utc), (), ("literal",)),
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
        "marivo.frame_output/v12",
        "marivo.frame_output/v13",
    ],
)
def test_historical_output_envelope_is_rejected_without_rewrite(historical_format: str) -> None:
    historical = parse_json(FROZEN)
    assert isinstance(historical, dict)
    historical["format"] = historical_format
    with pytest.raises(IntegrityError, match="expected Frame output v15"):
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
    assert saved.semantics_version == "frame.v15"
    assert protocol.FORMAT == "marivo.frame_output/v15"


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
            EvaluationRecord(datetime(2026, 10, 29, tzinfo=timezone.utc), (), ()),
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


def interpretation_manifest() -> ArtifactManifest:
    from marivo.analysis.core.data_schema import DataAlignment
    from marivo.analysis.core.frame_interpretation import (
        FrameAlignmentAxis,
        FrameAlignmentSide,
        FrameAxisGroup,
        FrameDerivedValueInput,
        FrameNamedValueInput,
        FramePairStatisticContext,
        FrameShareContext,
    )
    from marivo.analysis.core.lineage import SemanticObjectIdentity

    value = manifest()
    origin = SemanticObjectIdentity("metric", "sales.revenue", "captured-revenue")
    share = FrameShareContext(
        FrameNamedValueInput("previous.revenue", origin), (), (FrameAxisGroup("axis"),)
    )
    pair = FramePairStatisticContext(
        "pearson",
        FrameDerivedValueInput((origin,)),
        FrameNamedValueInput("previous.orders", None),
        (),
    )
    values = tuple(
        DataColumn(name, name, ScalarDataType("float64"), True)
        for name in ("share", "copy", "coefficient")
    )
    presence = tuple(
        DataColumn(name, name, ScalarDataType("boolean"), False)
        for name in ("left_present", "right_present")
    )
    coordinates = (FrameAlignmentAxis("axis", "self"),)
    schema = replace(
        value.data.schema,
        values=values,
        presence=presence,
        value_paths=tuple((v.name,) for v in values),
        presence_paths=tuple((v.name,) for v in presence),
        value_contexts=(("share", share), ("copy", share), ("coefficient", pair)),
        alignments=(
            DataAlignment(
                "alignment",
                FrameAlignmentSide(("share", "copy"), ("left_present",), coordinates),
                FrameAlignmentSide(("coefficient",), ("right_present",), coordinates),
            ),
        ),
    )
    return replace(
        value,
        data=replace(value.data, schema=schema),
        metadata=replace(value.metadata, lineage=empty_lineage(schema)),
    )


def test_share_pair_inputs_and_alignment_are_closed_interned_manifest_facts() -> None:
    from marivo.analysis.core.frame_interpretation import (
        FrameDerivedValueInput,
        FrameNamedValueInput,
        FramePairStatisticContext,
        FrameShareContext,
    )

    original = interpretation_manifest()
    wire = parse_json(protocol.encode(original))
    assert isinstance(wire, dict)
    assert len(wire["contexts"]) == 2
    restored = protocol.decode(canonical_json(wire), ArtifactManifest)
    assert restored == original
    contexts = dict(restored.data.schema.value_contexts)
    share, pair = contexts["share"], contexts["coefficient"]
    assert share is contexts["copy"]
    assert isinstance(share, FrameShareContext)
    assert share.numerator == FrameNamedValueInput("previous.revenue", share.numerator.subject)
    assert isinstance(pair, FramePairStatisticContext)
    assert isinstance(pair.x, FrameDerivedValueInput)
    assert pair.x.origins[0].path == "sales.revenue"
    assert pair.y == FrameNamedValueInput("previous.orders", None)
    assert "previous.revenue" not in {v.name for v in restored.data.schema.values}
    assert restored.data.schema.alignments[0].left.values == ("share", "copy")


def test_nested_attribute_carriers_encode_new_contexts_without_authorizing_targets() -> None:
    from marivo.analysis.core.frame_interpretation import FrameLevelTarget

    for name, context in interpretation_manifest().data.schema.value_contexts:
        target = FrameLevelTarget(name, context)
        assert protocol._decode(protocol._encode(target)) == target


@pytest.mark.parametrize(
    "damage", ("unknown_kind", "extra", "input_kind", "mutable_origins", "statistic")
)
def test_corrupt_share_and_pair_context_records_are_rejected(damage: str) -> None:
    from copy import deepcopy

    wire = parse_json(protocol.encode(interpretation_manifest()))
    assert isinstance(wire, dict)
    contexts = wire["contexts"]
    assert isinstance(contexts, list)
    share, pair = deepcopy(contexts)
    assert isinstance(share, dict) and isinstance(pair, dict)
    share_fields, pair_fields = share["fields"], pair["fields"]
    assert isinstance(share_fields, dict) and isinstance(pair_fields, dict)
    if damage == "unknown_kind":
        share_fields["kind"] = "normalization"
    elif damage == "extra":
        share_fields["denominator"] = "sum"
    elif damage == "input_kind":
        numerator = share_fields["numerator"]
        assert isinstance(numerator, dict)
        numerator["record"] = "ArbitraryInput"
    elif damage == "mutable_origins":
        x = pair_fields["x"]
        assert isinstance(x, dict) and isinstance(x["fields"], dict)
        x["fields"]["origins"] = {"sales.revenue": "captured-revenue"}
    else:
        pair_fields["statistic"] = "sum"
    wire["contexts"] = [share, pair]
    with pytest.raises(IntegrityError):
        protocol.decode(canonical_json(wire), ArtifactManifest)


@pytest.mark.parametrize("damage", ("value", "presence", "axis", "endpoint", "empty_presence"))
def test_dangling_alignment_references_cannot_enter_a_restored_schema(damage: str) -> None:
    wire = parse_json(protocol.encode(interpretation_manifest()))
    assert isinstance(wire, dict)
    value = wire["value"]
    assert isinstance(value, dict) and isinstance(value["fields"], dict)
    data = value["fields"]["data"]
    assert isinstance(data, dict) and isinstance(data["fields"], dict)
    schema = data["fields"]["schema"]
    assert isinstance(schema, dict) and isinstance(schema["fields"], dict)
    alignments = schema["fields"]["alignments"]
    assert isinstance(alignments, list)
    alignment = alignments[0]
    assert isinstance(alignment, dict) and isinstance(alignment["fields"], dict)
    left = alignment["fields"]["left"]
    assert isinstance(left, dict) and isinstance(left["fields"], dict)
    side = left["fields"]
    if damage == "value":
        side["values"] = ["missing"]
    elif damage in ("presence", "empty_presence"):
        side["presence"] = ["missing"] if damage == "presence" else []
    else:
        coordinates = side["coordinates"]
        assert isinstance(coordinates, list)
        coordinate = coordinates[0]
        assert isinstance(coordinate, dict) and isinstance(coordinate["fields"], dict)
        coordinate["fields"]["axis_token" if damage == "axis" else "endpoint"] = (
            "missing" if damage == "axis" else "left"
        )
    with pytest.raises(IntegrityError):
        protocol.decode(canonical_json(wire), ArtifactManifest)


@pytest.mark.parametrize("rows", (0, None, 12))
def test_source_reads_are_embedded_closed_manifest_metadata(rows: int | None) -> None:
    from marivo.analysis.core.frame_source_reads import (
        FrameSourceRead,
        FrameSourceReadPeriod,
        FrameSourceReadScope,
    )

    instant = datetime(2026, 1, 1, tzinfo=timezone.utc)
    reads = (
        FrameSourceRead("read_first", instant, (FrameSourceReadScope("sales.orders", None),), rows),
        FrameSourceRead(
            "read_second",
            instant,
            (
                FrameSourceReadScope(
                    "sales.orders", FrameSourceReadPeriod(None, instant, "Asia/Shanghai")
                ),
                FrameSourceReadScope("sales.lines", None),
            ),
            rows,
        ),
    )
    value = manifest()
    value = replace(value, metadata=replace(value.metadata, source_reads=reads))
    assert protocol.decode(protocol.encode(value), ArtifactManifest) == value
    assert protocol._FIELDS["StoredFrameMetadata"] == (
        "result_id",
        "evaluated_at",
        "inputs",
        "operations",
        "source_reads",
        "lineage",
    )
    assert {"OutputFinding", "OutputEvidence", "OutputCoordinate"}.isdisjoint(protocol._FIELDS)


@pytest.mark.parametrize(
    "damage", ("list", "count_bool", "scope", "period", "naive", "conflicting_id", "duplicate_id")
)
def test_corrupt_nested_source_reads_cannot_become_empty(damage: str) -> None:
    from copy import deepcopy

    from marivo.analysis.core.frame_source_reads import FrameSourceRead, FrameSourceReadScope

    instant = datetime(2026, 1, 1, tzinfo=timezone.utc)
    read = FrameSourceRead("read_first", instant, (FrameSourceReadScope("sales.orders", None),), 3)
    value = manifest()
    value = replace(value, metadata=replace(value.metadata, source_reads=(read,)))
    wire = parse_json(protocol.encode(value))
    assert isinstance(wire, dict)
    root = wire["value"]
    assert isinstance(root, dict)
    fields = root["fields"]
    assert isinstance(fields, dict)
    metadata = fields["metadata"]
    assert isinstance(metadata, dict)
    metadata_fields = metadata["fields"]
    assert isinstance(metadata_fields, dict)
    reads = metadata_fields["source_reads"]
    assert isinstance(reads, list)
    current = reads[0]
    assert isinstance(current, dict)
    read_fields = current["fields"]
    assert isinstance(read_fields, dict)
    if damage == "list":
        metadata_fields["source_reads"] = None
    elif damage == "count_bool":
        read_fields["returned_rows"] = True
    elif damage == "scope":
        read_fields["scopes"] = ["sales.orders"]
    elif damage == "period":
        read_fields["scopes"] = [
            {
                "record": "FrameSourceReadScope",
                "fields": {"source": "sales.orders", "period": "2026-01-01"},
            }
        ]
    elif damage == "naive":
        read_fields["completed_at"] = {"scalar": "datetime", "value": "2026-01-01T00:00:00"}
    else:
        duplicate = deepcopy(current)
        duplicate_fields = duplicate["fields"]
        assert isinstance(duplicate_fields, dict)
        if damage == "conflicting_id":
            duplicate_fields["returned_rows"] = 4
        reads.append(duplicate)
    with pytest.raises(IntegrityError):
        protocol.decode(canonical_json(wire), ArtifactManifest)


def test_current_store_contains_no_record_collections(tmp_path: Path) -> None:
    from marivo.analysis.materialization.store import SessionStore

    store = SessionStore(tmp_path)
    with store._read() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 26
    assert names == {
        "sessions",
        "analysis_action_runs",
        "analysis_action_run_terminals",
        "dataset_artifacts",
        "action_resource_journal",
    }
