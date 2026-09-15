"""Offline rejection tests for the opt-in environment smoke receipt."""

from io import BytesIO
from pathlib import Path

import pytest

from tests.multisource_environment import qualify
from tests.multisource_environment.smoke import verify_clickhouse_settings


def test_effective_settings_match_the_exact_qualification_scope() -> None:
    verify_clickhouse_settings(
        [
            ["enable_shared_storage_snapshot_in_query", "1"],
            ["join_use_nulls", "1"],
            ["max_memory_usage", "536870912"],
            ["max_execution_time", "30"],
            ["use_query_cache", "0"],
        ]
    )


@pytest.mark.parametrize("damage", ["missing", "duplicate", "mismatch", "unexpected", "malformed"])
def test_bad_effective_settings_cannot_issue_a_success_receipt(damage: str) -> None:
    rows: list[list[object]] = [
        ["enable_shared_storage_snapshot_in_query", "1"],
        ["join_use_nulls", "1"],
        ["max_memory_usage", "536870912"],
        ["max_execution_time", "30"],
        ["use_query_cache", "0"],
    ]
    if damage == "missing":
        rows = []
    elif damage == "duplicate":
        rows.append(rows[0])
    elif damage == "mismatch":
        rows[0][1] = "0"
    elif damage == "unexpected":
        rows.append(["extra", "1"])
    else:
        rows[0] = ["enable_shared_storage_snapshot_in_query", 1]
    with pytest.raises(ValueError):
        verify_clickhouse_settings(rows)


@pytest.mark.parametrize("kind", ["bounded", "oversized", "compressed"])
def test_live_probe_bounds_wire_bytes_before_arrow_decoding(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / ".cache/marivo-multisource/secrets.env"
    secret.parent.mkdir(parents=True)
    secret.write_text("QUALIFICATION_PASSWORD=disposable-test-secret\n")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    reads: list[int | None] = []

    class Response(BytesIO):
        headers = {"Content-Encoding": "gzip" if kind == "compressed" else "identity"}

        def read(self, size: int | None = -1) -> bytes:
            reads.append(size)
            return super().read(size)

    def open_response(*args: object, **kwargs: object) -> Response:
        return Response(b"x" * (qualify.BUDGET + 2) if kind == "oversized" else b"ok")

    monkeypatch.setattr(qualify, "urlopen", open_response)
    probe = qualify.ClickHouseProbe()
    if kind == "bounded":
        assert probe.request("SELECT 1", role="fixture") == b"ok"
        assert probe.receipts[0]["wire_bytes"] == 2
    else:
        with pytest.raises(ValueError, match=r"Compressed|byte budget"):
            probe.request("SELECT 1", role="fixture")
        assert probe.receipts == []
    assert reads == ([] if kind == "compressed" else [qualify.BUDGET + 1])
