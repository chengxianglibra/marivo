"""Closed local receipts and metadata-only recovery."""

from pathlib import Path

import pytest

from marivo.analysis.materialization import contracts as c
from marivo.analysis.materialization.errors import IntegrityError


def _local() -> c.LocalReceipt:
    entries = (c.FileEntry("data.parquet", 4096),)
    return c.LocalReceipt(
        "sessions/s/artifacts/a/primary",
        entries,
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


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", "csv"),
        ("project_relative_path", "../foreign"),
        ("project_relative_path", "/absolute"),
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
