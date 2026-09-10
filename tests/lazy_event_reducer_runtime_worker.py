"""Three isolated processes prove native Event reductions and exact cold reuse."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import duckdb

from marivo.analysis.datasets.base import MaterializedDataset
from marivo.analysis.domains.contracts import EventJourneySemantics
from marivo.analysis.domains.event import MaterializedEventDataset
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.event_codec import evidence_payload
from marivo.analysis.materialization.event_reducer_codec import selection_evidence_payload
from marivo.analysis.materialization.targets import EngineTarget
from marivo.analysis.observation.population import MaterializedPopulationDataset
from marivo.analysis.observation.predicates import gt
from marivo.analysis.subject import dropped_before
from marivo.refs import ref
from tests.lazy_adapter_runtime_worker import forbidden, snapshot
from tests.lazy_event_fixtures import make_event_registry
from tests.lazy_event_runtime_fixtures import journey, setup_event
from tests.lazy_event_runtime_worker import assert_identity_private
from tests.lazy_materialization_crash_worker import record_evidence, statistics, versions


def run(mode: str, project: Path, refs: dict[str, str]) -> dict[str, object]:
    database = project / "warehouse.duckdb"
    if mode == "produce":
        runtime, sources, database = setup_event(project, engine=True)
        metric = sources.observe(
            ref.metric("sales.revenue"),
            population=sources.population(ref.entity("sales.customers")),
        )
        membership = metric.where(
            gt(metric.fields.metric(ref.metric("sales.revenue")), 10)
        ).execute()
        retained = journey(sources, population=membership).execute()
        assert runtime.statistics.transferred_rows == 0
        assert_identity_private(runtime)
        return {
            "pid": os.getpid(),
            "refs": {
                "session": runtime.session_ref,
                "membership": membership.state.artifact_ref.ref,
                "journey": retained.state.artifact_ref.ref,
            },
            "after": snapshot(runtime),
            "versions": versions(),
            "statistics": statistics(runtime),
            "identity_privacy_verified": True,
        }
    runtime = DatasetRuntime.open(project, refs["session"], target=EngineTarget("warehouse"))
    before = snapshot(runtime)
    receiver = runtime.artifact(refs["journey"])
    assert isinstance(receiver, MaterializedEventDataset)
    meaning = receiver.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    if mode == "continue":
        with duckdb.connect(str(database), config={"threads": 1}) as connection:
            connection.execute("DROP TABLE started_rows")
            connection.execute("DROP TABLE finished_rows")
    registry, sidecar = make_event_registry(database)
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    first, last = meaning.pattern.steps
    funnel = receiver.funnel()
    grouped = receiver.funnel(axes=(ref.dimension("sales.customers.region"),))
    durations = receiver.time_to_event(from_step=first, to_step=last)
    selected = receiver.select_subjects(dropped_before(step=last))
    results: dict[str, MaterializedDataset] = {}
    with ExitStack() as stack:
        for name in (
            "marivo.analysis.compiler.event.compile_event_match",
            "marivo.analysis.compiler.event_sources.lower_event_sources",
            "marivo.analysis.compiler.lowering.resolve_event_coverage",
            "marivo.analysis.materialization.reads.payload_batches",
        ):
            stack.enter_context(patch(name, forbidden))
        stack.enter_context(patch.object(admission, "supervise", forbidden))
        if mode == "cold":
            for name in ("place", "compile_dataset", "_build_backend_from_effective"):
                stack.enter_context(patch.object(admission, name, forbidden))
        for name, logical in (
            ("funnel", funnel),
            ("grouped", grouped),
            ("duration", durations),
            ("selection", selected),
        ):
            result = logical.execute()
            if mode == "cold":
                assert result.state.artifact_ref.ref == refs[name]
            results[name] = result
            refs = {**refs, name: result.state.artifact_ref.ref}
        selection_result = results["selection"]
        assert isinstance(selection_result, MaterializedPopulationDataset)
        metric = sources.observe(ref.metric("sales.revenue"), population=selection_result)
        final = metric.execute()
        if mode == "cold":
            assert final.state.artifact_ref.ref == refs["metric"]
        refs = {**refs, "metric": final.state.artifact_ref.ref}
        results["metric"] = final
    frames = {name: result.to_pandas() for name, result in results.items()}
    assert frames["funnel"].reached_count.tolist() == [2, 1]
    assert frames["funnel"].lost_count.tolist() == [0, 1]
    assert frames["duration"].completion_status.tolist() == ["complete", "incomplete"]
    assert frames["duration"].observed_duration.tolist() == [
        frames["duration"].duration.iloc[0],
        frames["duration"].followup_until.iloc[1] - frames["duration"].from_time.iloc[1],
    ]
    assert frames["selection"].entity_identity.tolist() == [(2,)]
    assert frames["metric"].entity_identity.tolist() == [(2,)]
    assert frames["metric"].revenue.tolist() == [100.0]
    assert frames["grouped"].region.isna().sum() == 2
    assert frames["grouped"].groupby("step_key")["lost_count"].sum().to_dict() == {
        "finish": 1,
        "start": 0,
    }
    artifacts: dict[str, object] = {}
    for name, materialized in results.items():
        record = runtime.store.artifact(materialized.state.artifact_ref.ref)
        assert record is not None and record.evidence.finding_count == 0
        if name != "metric":
            assert record.descriptor.retained_parts == ()
        artifacts[name] = {
            "record": record_evidence(record),
            "event_evidence": evidence_payload(record.descriptor.event_evidence),
            "selection_evidence": selection_evidence_payload(
                record.descriptor.subject_selection_evidence
            ),
        }
    assert runtime.statistics.transferred_rows == runtime.statistics.transferred_bytes == 0
    assert runtime.statistics.local_handoffs == ()
    assert runtime.store.resources(runtime.session_ref) == ()
    assert_identity_private(runtime)
    if mode == "cold":
        assert snapshot(runtime) == before
    return {
        "pid": os.getpid(),
        "refs": refs,
        "before": before,
        "after": snapshot(runtime),
        "terminal_sha256": {
            name: hashlib.sha256(frame.to_json(date_format="iso").encode()).hexdigest()
            for name, frame in frames.items()
        },
        "artifacts": artifacts,
        "statistics": statistics(runtime),
        "versions": versions(),
        "occurrence_origins_removed": True,
        "identity_privacy_verified": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "continue", "cold"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--refs", default="{}")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.mode, args.project, json.loads(args.refs)), sort_keys=True, allow_nan=False
        )
    )
