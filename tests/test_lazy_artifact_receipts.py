"""Closed local receipts and metadata-only recovery."""

from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.materialization import contracts as c
from marivo.analysis.materialization.errors import IntegrityError
from tests.lazy_materialization_fixtures import descriptor


def _local() -> c.LocalReceipt:
    entries = (c.FileEntry("data.parquet", 4096, "a" * 64),)
    return c.LocalReceipt(
        "sessions/s/artifacts/a/primary",
        entries,
        c.manifest_digest(entries),
        "a" * 64,
        "c" * 64,
        2,
        4096,
    )


def test_closed_receipt_round_trip() -> None:
    receipt = _local()
    payload = c.receipt_payload(receipt)
    recovered = c.decode_receipt(c.parse_json(c.canonical_json(payload)))
    assert recovered == receipt
    assert recovered.identity_digest == receipt.identity_digest
    assert "datasource" not in repr(receipt)


def test_descriptor_round_trip_with_all_required_parts() -> None:
    receipt = _local()
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
        ("format", "csv"),
        ("project_relative_path", "../foreign"),
        ("project_relative_path", "/absolute"),
        ("bytes_hash", "mutable"),
        ("realized_row_count", True),
    ],
)
def test_local_receipt_rejects_unregistered_or_mutable_authority(field: str, value: object) -> None:
    payload = c.receipt_payload(_local())
    payload[field] = value
    with pytest.raises(IntegrityError):
        c.decode_receipt(payload)


def test_receipt_codec_does_not_open_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = _local()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("metadata-only receipt accessed storage")

    monkeypatch.setattr(Path, "open", forbidden)
    assert c.decode_receipt(c.receipt_payload(receipt)) == receipt
