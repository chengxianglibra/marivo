"""Typed datasource repair construction shared by authoring operations."""

from __future__ import annotations

from marivo._authoring.model import AuthoringRepair
from marivo.datasource.errors import repair


def repair_for_authoring_code(code: str) -> AuthoringRepair:
    """Return the exact typed repair registered for one authoring blocker."""
    if code == "datasource_missing":
        return repair(
            kind="register",
            canonical_id="register",
            action="Register the datasource before inspecting this source.",
            preserves_evidence=False,
        )
    if code in {"source_mismatch", "transformed_partition_unsupported", "timeout_not_enforceable"}:
        return repair(
            kind="configure",
            canonical_id="inspect",
            action="Inspect the datasource configuration before retrying this operation.",
            preserves_evidence=False,
        )
    if code in {"selected_columns_required", "unknown_source_column"}:
        return repair(
            kind="inspect",
            canonical_id="inspect",
            action="Inspect the captured source schema before selecting columns.",
            preserves_evidence=True,
        )
    if code in {
        "partition_state_unknown",
        "incomplete_partition_fields",
        "partition_predicate_unsupported",
    }:
        return repair(
            kind="rescope",
            canonical_id="SourceInspection.partitions",
            action="Rescope using the captured partition evidence.",
            preserves_evidence=True,
        )
    if code in {"cache_stale", "schema_stale", "fingerprint_stale"}:
        return repair(
            kind="reacquire",
            canonical_id="SourceInspection.sample",
            action="Reacquire bounded evidence from the inspected source.",
            preserves_evidence=False,
        )
    if code == "acquisition_execution_failed":
        return repair(
            kind="reacquire",
            canonical_id="SourceInspection.sample",
            action=(
                "Only if the caller's remaining data-access budget permits, retry this exact "
                "bounded acquisition at most once. Caller-provided read, row, and timeout limits "
                "take precedence. If the same structured code and backend name recur, stop and "
                "report the datasource backend blocker."
            ),
            preserves_evidence=False,
        )
    if code == "acquisition_connection_failed":
        return repair(
            kind="reconnect",
            canonical_id="test",
            action="Validate the datasource connection before reacquiring bounded evidence.",
            preserves_evidence=True,
        )
    if code == "acquisition_source_failed":
        return repair(
            kind="inspect",
            canonical_id="inspect",
            action="Inspect the captured source identity before reacquiring bounded evidence.",
            preserves_evidence=True,
        )
    if code == "typed_schema_required":
        return repair(
            kind="configure",
            canonical_id="inspect",
            action="Configure a non-empty authored schema before inspection.",
            preserves_evidence=False,
        )
    raise ValueError(f"No typed authoring repair is registered for {code!r}.")
