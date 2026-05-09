from __future__ import annotations

from pathlib import Path

from marivo.contracts.ids import SessionId, StepId
from tests.contracts.contract_cases import ContractCase


def _run_insert_and_list(adapter, _: Path) -> None:
    """Insert a step and verify it appears in list_steps."""
    session_id = SessionId("sess-contract-1")
    step_id = StepId("step-contract-1")
    adapter.insert_step(
        step_id=step_id,
        session_id=session_id,
        step_type="observe",
        summary="Contract test step",
        result={"status": "ok"},
    )
    steps = adapter.list_steps(session_id)
    assert len(steps) >= 1
    matched = [s for s in steps if s.step_id == step_id]
    assert len(matched) == 1
    assert matched[0].step_type == "observe"
    assert matched[0].summary == "Contract test step"
    assert matched[0].result == {"status": "ok"}


def _run_insert_with_provenance(adapter, _: Path) -> None:
    """Insert a step with provenance metadata and verify round-trip."""
    session_id = SessionId("sess-contract-2")
    step_id = StepId("step-contract-2")
    adapter.insert_step(
        step_id=step_id,
        session_id=session_id,
        step_type="compare",
        summary="Step with provenance",
        result={"diff_count": 3},
        provenance={"source": "auto", "trigger": "observe"},
        semantic_metadata={"domain": "revenue"},
    )
    steps = adapter.list_steps(session_id)
    matched = [s for s in steps if s.step_id == step_id]
    assert len(matched) == 1
    step = matched[0]
    assert step.provenance is not None
    assert step.provenance["source"] == "auto"


def _run_list_empty_session(adapter, _: Path) -> None:
    """list_steps returns an empty list for a session with no steps."""
    steps = adapter.list_steps(SessionId("sess-no-steps"))
    assert steps == []


STEP_STORE_CASES = [
    ContractCase(name="insert_and_list", run=_run_insert_and_list),
    ContractCase(name="insert_with_provenance", run=_run_insert_with_provenance),
    ContractCase(name="list_empty_session", run=_run_list_empty_session),
]
