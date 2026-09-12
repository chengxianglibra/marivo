"""Atomic publication and storage guards exercised by real retained fold consumers."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.observation.predicates import gt
from marivo.refs import ref
from tests.lazy_adapter_runtime_worker import snapshot
from tests.lazy_local_fixtures import pandas_methods
from tests.lazy_retained_fixtures import setup_retained

pytestmark = pytest.mark.runtime

REVENUE = ref.metric("sales.revenue")
MEAN = ref.metric("sales.mean_amount")


@pytest.mark.parametrize("kind", ["local", "engine"])
@pytest.mark.parametrize(
    "point", ["after_rename", "insert_artifact", "insert_evidence", "before_commit"]
)
def test_retained_fold_failure_preserves_input_and_publishes_no_partial_bundle(
    tmp_path: Path, kind: Literal["local", "engine"], point: str
) -> None:
    fixture = setup_retained(tmp_path, kind)
    checkpoint = fixture.sources.observe([REVENUE, MEAN]).execute()
    original = fixture.runtime.store.artifact(checkpoint.state.artifact_ref.ref)
    before = snapshot(fixture.runtime)
    attempts: list[str] = []

    def fail(event: str) -> None:
        if event == point:
            attempts.append(event)
            raise RuntimeError("retained-fold-publication-fault")

    fixture.runtime._hook = fail
    with pytest.raises(MaterializationError):
        checkpoint.where(gt(REVENUE, 0)).aggregate().execute()
    assert attempts == [point]
    after = snapshot(fixture.runtime)
    assert after["dataset_artifacts"] == before["dataset_artifacts"]
    assert after["dataset_evidence"] == before["dataset_evidence"]
    assert after["analysis_action_runs"] == after["analysis_action_run_terminals"] == 2
    assert after["action_resource_journal"] == 0
    assert fixture.runtime.store.artifact(checkpoint.state.artifact_ref.ref) == original


def test_retained_fold_lost_commit_acknowledgement_recovers_same_binding(tmp_path: Path) -> None:
    fixture = setup_retained(tmp_path)
    checkpoint = fixture.sources.observe(MEAN).execute()

    def lost(event: str) -> None:
        if event == "after_commit":
            raise RuntimeError("lost-retained-commit-acknowledgement")

    fixture.runtime._hook = lost
    logical = checkpoint.aggregate()
    result = logical.execute()
    after = snapshot(fixture.runtime)
    assert result.to_pandas()["mean_amount"].tolist() == [29.4]
    assert logical.execute().state.artifact_ref == result.state.artifact_ref
    assert snapshot(fixture.runtime) == after
    assert after["dataset_artifacts"] == after["dataset_evidence"] == 2
    assert after["action_resource_journal"] == 0


def test_limit_cannot_hide_oversized_complete_retained_input(tmp_path: Path) -> None:
    with pandas_methods("metric.rank"):
        fixture = setup_retained(tmp_path)
        checkpoint = fixture.sources.observe([REVENUE, MEAN]).execute()
        fixture.runtime.local_policy = replace(fixture.runtime.local_policy, max_input_rows=5)
        logical = checkpoint.rank(checkpoint.fields.metric(REVENUE)).limit(1).aggregate()
        with pytest.raises(MaterializationError):
            logical.execute()
        after = snapshot(fixture.runtime)
        assert after["dataset_artifacts"] == after["dataset_evidence"] == 1
        assert after["action_resource_journal"] == 0
