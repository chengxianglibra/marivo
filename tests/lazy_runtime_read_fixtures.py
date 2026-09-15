"""Selected real Store records for metadata-only runtime read tests."""

from dataclasses import replace

from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    LocalReceipt,
    RunDatasetInput,
    RunFailure,
    digest,
)
from marivo.analysis.materialization.store import SessionStore
from marivo.introspection.live.model import LiveHelpTarget
from tests.lazy_materialization_fixtures import descriptor


def input_value(value: ArtifactDescriptor | None = None) -> RunDatasetInput:
    value = descriptor() if value is None else value
    return RunDatasetInput(
        value.definition_fingerprint,
        value.row_contract.shape_id,
        value.row_contract_fingerprint,
        value.row_set_contract_fingerprint,
        ("session.population",),
        ("entity:sales.customers",),
    )


def failure() -> RunFailure:
    return RunFailure(
        phase="stage_execution",
        kind="execution_failed",
        safe_message="The action failed.",
        safe_location=None,
        expected={"count": 3},
        received=[1, 2],
        repair=AnalysisRepair(
            kind="retry",
            action="Retry the action.",
            help_target=LiveHelpTarget(surface="analysis", canonical_id="runtime.runs"),
        ),
    )


def publish(
    store: SessionStore,
    run_ref: str,
    artifact_ref: str,
    *,
    session_ref: str = "session",
    inputs: tuple[str, ...] = (),
) -> None:
    value = descriptor()
    receipt = value.storage_receipt
    assert isinstance(receipt, LocalReceipt)
    value = replace(
        value,
        storage_receipt=replace(
            receipt,
            project_relative_path=f".marivo/analysis/generations/v5/sessions/{session_ref}/artifacts/{artifact_ref}/primary",
        ),
    )
    store.admit(
        session_ref,
        digest(run_ref),
        input_value(value),
        run_ref=run_ref,
        input_artifact_refs=inputs,
    )
    store.publish(run_ref, artifact_ref, value)
