"""Closed immutable adapter receipts and metadata-only v3 recovery."""

from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.materialization import contracts as c
from marivo.analysis.materialization.errors import IntegrityError
from tests.lazy_materialization_fixtures import descriptor


def _engine() -> c.EngineReceipt:
    return c.EngineReceipt(
        "warehouse",
        "a" * 64,
        "sessions/s/artifacts/a/primary/payload.duckdb",
        "b" * 64,
        "c" * 64,
        2,
        4096,
    )


def _object() -> c.ObjectReceipt:
    return c.ObjectReceipt(
        "object",
        "marivo/v3/s/a/primary/manifest.json",
        "exact-version",
        "d" * 64,
        "c" * 64,
        2,
        4096,
    )


@pytest.mark.parametrize(
    "receipt", [_engine(), replace(_engine(), realized_byte_count=None), _object()]
)
def test_closed_receipt_round_trip(receipt: c.StorageReceipt) -> None:
    payload = c.receipt_payload(receipt)
    recovered = c.decode_receipt(c.parse_json(c.canonical_json(payload)))
    assert recovered == receipt
    assert recovered.identity_digest == receipt.identity_digest
    assert "datasource" not in repr(receipt)


@pytest.mark.parametrize("receipt", [_engine(), _object()])
def test_descriptor_round_trip_with_all_required_parts(receipt: c.StorageReceipt) -> None:
    original = descriptor(metric=True)
    updated = replace(
        original,
        storage_receipt=replace(receipt, schema_fingerprint=original.realized_schema_fingerprint),
        retained_parts=tuple(
            replace(
                part,
                storage_receipt=replace(
                    receipt, schema_fingerprint=part.storage_receipt.schema_fingerprint
                ),
            )
            for part in original.retained_parts
        ),
    )
    encoded = c.encode_descriptor(updated)
    assert c.encode_descriptor(c.decode_descriptor(encoded)) == encoded


@pytest.mark.parametrize(
    "field,value",
    [
        ("immutable_relation_protocol", "ordinary_table"),
        ("qualified_relation_ref", "../foreign.duckdb"),
        ("qualified_relation_ref", "/absolute.duckdb"),
        ("relation_version_or_snapshot_token", "mutable"),
        ("realized_row_count", True),
    ],
)
def test_engine_receipt_rejects_unregistered_or_mutable_authority(
    field: str, value: object
) -> None:
    payload = c.receipt_payload(_engine())
    payload[field] = value
    with pytest.raises(IntegrityError):
        c.decode_receipt(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", "csv"),
        ("parquet_contract_version", 2),
        ("file_count", 0),
        ("object_version_or_manifest_hash", "null"),
        ("manifest_hash", "latest"),
        ("immutable_prefix_or_manifest_ref", "../other/manifest.json"),
        ("realized_byte_count", {"kind": "unavailable"}),
    ],
)
def test_object_receipt_rejects_unpinned_or_unknown_protocol(field: str, value: object) -> None:
    payload = c.receipt_payload(_object())
    payload[field] = value
    with pytest.raises(IntegrityError):
        c.decode_receipt(payload)


@pytest.mark.parametrize("receipt", [_engine(), _object()])
def test_receipt_codec_does_not_open_storage(
    receipt: c.StorageReceipt, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("metadata-only receipt accessed storage")

    monkeypatch.setattr(Path, "open", forbidden)
    assert c.decode_receipt(c.receipt_payload(receipt)) == receipt
