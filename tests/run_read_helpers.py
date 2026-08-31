"""Small black-box helpers for assertions over public Run values."""

from __future__ import annotations

from typing import Any

from marivo.analysis import FailedRun, IncompleteRun, SucceededRun

PublicRun = IncompleteRun | SucceededRun | FailedRun


def run_arguments(run: PublicRun) -> dict[str, object]:
    """Return the public immutable argument tuple as an assertion-friendly mapping."""
    return {argument.name: argument.value for argument in run.arguments}


def capture_persisted_job_records(monkeypatch: Any) -> list[dict[str, Any]]:
    """Capture private intent commit payloads without changing production behavior."""
    from marivo.analysis.session import _runtime

    records: list[dict[str, Any]] = []
    original = _runtime._persist_run_success_from_legacy_record

    def capture(session: Any, record: dict[str, Any]) -> None:
        records.append(dict(record))
        original(session, record)

    monkeypatch.setattr(_runtime, "_persist_run_success_from_legacy_record", capture)
    return records


def persisted_queries(
    records: list[dict[str, Any]],
    *,
    output_ref: str,
) -> list[dict[str, object]]:
    """Return query audit payloads for one captured intent output."""
    for record in reversed(records):
        if (
            record.get("output_frame_ref") == output_ref
            or record.get("output_artifact_id") == output_ref
        ):
            queries = record.get("queries", [])
            assert isinstance(queries, list)
            return queries
    raise AssertionError(f"no captured intent record for output {output_ref!r}")
