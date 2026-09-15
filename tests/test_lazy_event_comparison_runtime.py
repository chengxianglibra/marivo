"""Private Event funnel comparison and attribution Runtime acceptance."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest

from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.domains.contracts import EventJourneySemantics
from marivo.analysis.domains.event import LogicalEventDataset, MaterializedEventDataset
from marivo.analysis.funnel import funnel_loss_rate
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.observation.predicates import eq
from marivo.analysis.operators import registry
from marivo.analysis.operators.delta import MaterializedDeltaDataset
from marivo.refs import ref
from tests.lazy_adapter_runtime_worker import snapshot
from tests.lazy_event_fixtures import make_event_registry
from tests.lazy_event_runtime_fixtures import journey, setup_event

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("retained", [False, True])
def test_comparison_and_attribute(tmp_path: Path, local: bool, retained: bool) -> None:
    runtime, sources, database = setup_event(tmp_path, engine=True)
    logical = journey(sources)
    meaning = logical.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    current = logical
    receiver: LogicalEventDataset | MaterializedEventDataset
    if retained:
        committed = logical.execute()
        with duckdb.connect(str(database), config={"threads": 1}) as connection:
            connection.execute("DROP TABLE started_rows")
            connection.execute("DROP TABLE finished_rows")
        cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
        semantic_registry, sidecar = make_event_registry(database)
        cold.sources(semantic_registry=semantic_registry, sidecar=sidecar)
        recovered = cold.artifact(committed.state.artifact_ref)
        assert isinstance(recovered, MaterializedEventDataset)
        receiver = recovered
        runtime = cold
    else:
        receiver = current
    delta = receiver.funnel().compare(receiver.funnel())
    attribution = delta.attribute(
        target=funnel_loss_rate(step=meaning.pattern.steps[1]),
        axes=[ref.dimension("sales.customers.region")],
        top_k=1,
    )
    original = registry.implementation

    def implementation(dataset: LogicalDataset) -> registry.ImplementationRegistration:
        registered = original(dataset)
        return (
            replace(registered, backends=())
            if local and registered.operator_id in ("event.compare", "delta.funnel_attribute")
            else registered
        )

    if local:
        from marivo.analysis.materialization.targets import LocalTarget

        runtime.target = LocalTarget()
    with patch.object(registry, "implementation", implementation):
        result = delta.execute()
        first = delta.where(eq(delta.fields.get("step_key"), "start")).execute()
        attributed = attribution.execute()
    frame = result.to_pandas()
    assert frame.step_key.tolist() == ["start", "finish"]
    assert first.to_pandas().step_key.tolist() == ["start"]
    assert frame.loss_rate_delta.tolist()[1] == 0.0
    contributions = attributed.to_pandas()
    assert contributions.contribution.sum() == 0.0
    assert set(contributions.contribution_kind) == {"loss", "denominator_mix"}
    assert result.evidence_digest.finding_count == 1
    assert attributed.evidence_digest.finding_count == len(contributions)
    assert len(result.findings().items) == 1
    assert len(attributed.findings().items) == len(contributions)
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
    rebound = cold.artifact(attributed.state.artifact_ref)
    pd.testing.assert_frame_equal(rebound.to_pandas(), contributions)
    inspected = cold.revalidate(attributed.state.artifact_ref)
    assert (
        inspected.artifact_integrity,
        inspected.storage_authority,
        inspected.evidence_integrity,
    ) == ("valid", "readable", "valid")
    if not local:
        assert runtime.statistics.transferred_rows == len(contributions)
        assert runtime.statistics.events.get("local_execution_started", 0) == 0
    else:
        assert runtime.statistics.events.get("local_execution_started", 0) > 0
    checkpoint = cold.artifact(result.state.artifact_ref)
    assert isinstance(checkpoint, MaterializedDeltaDataset)
    with pytest.raises(Exception, match=r"checkpoint|journey"):
        checkpoint.attribute(
            target=funnel_loss_rate(step=meaning.pattern.steps[1]),
            axes=[ref.dimension("sales.customers.region")],
        )


@pytest.mark.parametrize("filter_censored", [False, True])
def test_unknown_comparison_fails_atomically(tmp_path: Path, filter_censored: bool) -> None:
    runtime, sources, _ = setup_event(tmp_path, engine=True)
    f = journey(sources, complete=False).funnel()
    selected = f.where(eq(f.fields.get("step_key"), "start")) if filter_censored else f
    before = snapshot(runtime)
    with pytest.raises(MaterializationError):
        selected.compare(selected).execute()
    after = snapshot(runtime)
    assert before["dataset_artifacts"] == after["dataset_artifacts"]
    assert before["dataset_evidence"] == after["dataset_evidence"]
    assert runtime.store.resources(runtime.session_ref) == ()


def test_target_filter_cannot_be_silently_undone(tmp_path: Path) -> None:
    runtime, sources, _ = setup_event(tmp_path, engine=True)
    j = journey(sources)
    meaning = j.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    delta = j.funnel().compare(j.funnel())
    selected = delta.where(eq(delta.fields.get("step_key"), "start"))
    before = snapshot(runtime)
    with pytest.raises(MaterializationError):
        selected.attribute(
            target=funnel_loss_rate(step=meaning.pattern.steps[-1]),
            axes=[ref.dimension("sales.customers.region")],
        ).execute()
    assert snapshot(runtime)["dataset_artifacts"] == before["dataset_artifacts"]


def test_three_process_journey_authority_and_exact_binding(tmp_path: Path) -> None:
    import json
    import os
    import subprocess
    import sys

    records = []
    for phase in ("produce", "continue", "recover"):
        completed = subprocess.run(
            [sys.executable, "-m", "tests.lazy_event_comparison_worker", str(tmp_path), phase],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert completed.returncode == 0, completed.stderr
        assert '"passed": true' in completed.stdout
        records.append(json.loads(completed.stdout.splitlines()[-1]))
    assert len({record["pid"] for record in records}) == 3
    assert len({record["candidate"] for record in records}) == 1
    evidence = os.environ.get("MARIVO_SLICE7C_EVIDENCE_DIR")
    if evidence:
        Path(evidence, "three-process.json").write_text(json.dumps(records, indent=2) + "\n")


@pytest.mark.parametrize("point", ["before_commit", "quality"])
def test_attribution_failure_or_cancellation_is_atomic(tmp_path: Path, point: str) -> None:
    armed = False

    def event(name: str) -> None:
        if armed and name == point:
            if point == "quality":
                raise KeyboardInterrupt("event-attribution-private-canary")
            raise OSError("event-attribution-private-canary")

    runtime, sources, _ = setup_event(tmp_path, engine=True, event=event)
    j = journey(sources).execute()
    meaning = j.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    output = (
        j.funnel()
        .compare(j.funnel())
        .attribute(
            target=funnel_loss_rate(step=meaning.pattern.steps[-1]),
            axes=[ref.dimension("sales.customers.region")],
        )
    )
    before = snapshot(runtime)
    armed = True
    expected_error = KeyboardInterrupt if point == "quality" else OSError
    with pytest.raises(expected_error) as caught:
        output.execute()
    assert "canary" in str(caught.value)
    assert snapshot(runtime)["dataset_artifacts"] == before["dataset_artifacts"]
    assert snapshot(runtime)["dataset_evidence"] == before["dataset_evidence"]
    assert runtime.store.resources(runtime.session_ref) == ()
    armed = False
    assert output.execute().to_pandas().contribution.sum() == 0


def test_aggregate_funnel_checkpoint_has_no_journey_authority(tmp_path: Path) -> None:
    runtime, sources, _ = setup_event(tmp_path)
    j = journey(sources)
    meaning = j.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    summary = j.funnel().execute()
    delta = summary.compare(summary)
    with pytest.raises(Exception, match=r"materialized|journey"):
        delta.attribute(
            target=funnel_loss_rate(step=meaning.pattern.steps[-1]),
            axes=[ref.dimension("sales.customers.region")],
        )
    assert "delta.attribute" not in delta.contract().render()
    assert delta.execute().to_pandas().loss_rate_delta.iloc[-1] == 0


@pytest.mark.parametrize("corruption", ["missing_components", "wrong_contract", "summary"])
def test_cold_attribution_authority_corruption_is_rejected(tmp_path: Path, corruption: str) -> None:
    import sqlite3

    from marivo.analysis.materialization.contracts import canonical_json, parse_json
    from marivo.analysis.materialization.errors import IntegrityError
    from tests.lazy_adapter_runtime_worker import forbidden

    runtime, sources, database = setup_event(tmp_path, engine=True)
    j = journey(sources)
    meaning = j.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    output = (
        j.funnel()
        .compare(j.funnel())
        .attribute(
            target=funnel_loss_rate(step=meaning.pattern.steps[-1]),
            axes=[ref.dimension("sales.customers.region")],
        )
        .execute()
    )
    artifact = output.state.artifact_ref.ref
    with sqlite3.connect(runtime.store.db_path) as connection:
        row = connection.execute(
            "SELECT descriptor_payload FROM dataset_artifacts WHERE artifact_ref=?", (artifact,)
        ).fetchone()
        assert row is not None and isinstance(row[0], str)
        payload = parse_json(row[0])
        assert isinstance(payload, dict)
        if corruption == "missing_components":
            payload["retained_parts"] = []
        elif corruption == "wrong_contract":
            parts = payload["retained_parts"]
            assert isinstance(parts, list) and isinstance(parts[0], dict)
            parts[0]["contract_version"] = 99
        else:
            summary = payload["funnel_evidence"]
            assert isinstance(summary, dict)
            summary["row_count"] = 999
        connection.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (canonical_json(payload), artifact),
        )
    database.unlink()
    before = snapshot(runtime)
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
    with (
        patch("marivo.analysis.materialization.admission.execute_local", forbidden),
        pytest.raises(IntegrityError),
    ):
        cold.artifact(output.state.artifact_ref)
    assert snapshot(cold) == before
    assert cold.store.resources(cold.session_ref) == ()


@pytest.mark.parametrize("axis_name", ["region", "positive", "negative"])
def test_nonzero_shifted_cohorts_have_full_runtime_source_local_parity(
    tmp_path: Path, axis_name: str
) -> None:
    from datetime import timedelta

    from marivo.analysis import time_scope
    from marivo.analysis.domains.completeness import BoundedCompletenessDeclarationV1
    from marivo.analysis.materialization.targets import LocalTarget
    from tests.lazy_event_runtime_fixtures import END, START, THROUGH

    frames: list[tuple[pd.DataFrame, pd.DataFrame]] = []
    for local in (False, True):
        project = tmp_path / ("local" if local else "native")
        project.mkdir()
        runtime, sources, database = setup_event(project, engine=True)
        axis = ref.dimension(f"sales.customers.{axis_name}")
        if axis_name != "region":
            semantic_registry, sidecar = make_event_registry(database)
            source = ref.dimension("sales.customers.region")
            dimension = replace(
                semantic_registry.dimensions[source.path],
                semantic_id=axis.path,
                name=axis_name,
                python_symbol=axis_name,
            )
            semantic_registry = replace(
                semantic_registry, dimensions={**semantic_registry.dimensions, axis.path: dimension}
            )
            semantic_registry.freeze()
            sidecar = replace(
                sidecar,
                bodies={**sidecar.bodies, axis: sidecar.bodies[source]},
                field_owners={**sidecar.field_owners, axis: sidecar.field_owners[source]},
                catalog_refs=frozenset((*sidecar.catalog_refs, axis)),
            )
            sources = runtime.sources(semantic_registry=semantic_registry, sidecar=sidecar)
        current = journey(sources)
        meaning = current.row_contract.family_semantics
        assert isinstance(meaning, EventJourneySemantics)
        baseline = sources.events.match(
            meaning.pattern,
            cohort_window=time_scope(start=START + timedelta(days=1), end=END + timedelta(days=1)),
            completion_through=THROUGH + timedelta(days=1),
            matching=meaning.matching,
            completeness=(
                BoundedCompletenessDeclarationV1(
                    inputs=(ref.event("sales.started"), ref.event("sales.finished")),
                    complete_from=START,
                    complete_through=THROUGH + timedelta(days=1),
                    rationale="Complete shifted-cohort fixture coverage.",
                ),
            ),
        )
        delta = current.funnel().compare(baseline.funnel())
        attribution = delta.attribute(
            target=funnel_loss_rate(step=meaning.pattern.steps[-1]),
            axes=(axis,),
            top_k=1,
        )
        original = registry.implementation

        def implementation(
            dataset: LogicalDataset,
            local: bool = local,
            original: Callable[[LogicalDataset], registry.ImplementationRegistration] = original,
        ) -> registry.ImplementationRegistration:
            registered = original(dataset)
            return (
                replace(registered, backends=())
                if local and registered.operator_id in ("event.compare", "delta.funnel_attribute")
                else registered
            )

        if local:
            runtime.target = LocalTarget()
        with patch.object(registry, "implementation", implementation):
            compared, attributed = delta.execute(), attribution.execute()
        frames.append((compared.to_pandas(), attributed.to_pandas()))
        assert frames[-1][0].loss_rate_delta.iloc[-1] != 0
        assert set(frames[-1][1].status) == {"ok"}
    for native, local_frame in zip(frames[0], frames[1], strict=True):
        pd.testing.assert_frame_equal(native, local_frame, atol=1e-12)


@pytest.mark.parametrize("engine", [False, True])
def test_filtered_funnel_checkpoint_cannot_hide_censoring(tmp_path: Path, engine: bool) -> None:
    runtime, sources, _ = setup_event(tmp_path, engine=engine)
    funnel = journey(sources, complete=False).funnel()
    checkpoint = funnel.where(eq(funnel.fields.get("step_key"), "start")).execute()
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
    recovered = cold.artifact(checkpoint.state.artifact_ref)
    assert isinstance(recovered, MaterializedEventDataset)
    before = snapshot(cold)
    with pytest.raises(MaterializationError, match="pre-filter completeness authority"):
        recovered.compare(recovered).execute()
    after = snapshot(cold)
    assert before["dataset_artifacts"] == after["dataset_artifacts"]
    assert before["dataset_evidence"] == after["dataset_evidence"]
    assert cold.store.resources(cold.session_ref) == ()


@pytest.mark.parametrize("fault", ["missing", "mutated"])
def test_funnel_component_inspection_is_independent_of_primary_preview(
    tmp_path: Path, fault: str
) -> None:
    runtime, sources, database = setup_event(tmp_path, engine=True)
    logical = journey(sources)
    meaning = logical.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    attributed = (
        logical.funnel()
        .compare(logical.funnel())
        .attribute(
            target=funnel_loss_rate(step=meaning.pattern.steps[1]),
            axes=[ref.dimension("sales.customers.region")],
            top_k=1,
        )
        .execute()
    )
    record = runtime.store.artifact(attributed.state.artifact_ref.ref)
    assert record is not None
    part = next(
        value
        for value in record.descriptor.retained_parts
        if value.contract_id == "event_funnel.additive_components"
    )
    receipt = part.storage_receipt
    assert isinstance(receipt, LocalReceipt)
    backing = tmp_path / receipt.project_relative_path / receipt.file_manifest[0].relative_path
    if fault == "missing":
        backing.rename(backing.with_suffix(".unavailable"))
    else:
        with backing.open("r+b") as stream:
            stream.write(b"FAIL")
    database.rename(database.with_suffix(".offline"))
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
    before = snapshot(cold)
    reopened = cold.artifact(attributed.state.artifact_ref)
    assert not reopened.to_pandas().empty
    assert reopened.findings().items
    inspected = cold.revalidate(attributed.state.artifact_ref)
    assert (
        inspected.artifact_integrity,
        inspected.storage_authority,
        inspected.evidence_integrity,
    ) == ("valid", fault, "valid")
    assert any(part.role in issue.safe_message for issue in inspected.issues)
    assert snapshot(cold) == before
