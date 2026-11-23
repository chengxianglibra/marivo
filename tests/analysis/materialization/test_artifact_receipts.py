"""Closed local receipts and metadata-only recovery."""

from pathlib import Path

import pytest

from marivo.analysis.materialization.contracts import decode_receipt, parse_json
from marivo.analysis.model.receipts import FileEntry, LocalReceipt, canonical_json, receipt_payload


def _local() -> LocalReceipt:
    entries = (FileEntry("data.parquet", 4096),)
    return LocalReceipt(
        "sessions/s/artifacts/a/primary",
        entries,
        2,
        4096,
    )


def test_closed_receipt_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    receipt = _local()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("metadata-only receipt accessed storage")

    monkeypatch.setattr(Path, "open", forbidden)
    payload = receipt_payload(receipt)
    recovered = decode_receipt(parse_json(canonical_json(payload)))
    assert recovered == receipt
    assert recovered.identity_digest == receipt.identity_digest
