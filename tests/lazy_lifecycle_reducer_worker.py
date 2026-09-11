"""Independent production, source-offline continuation and exact cold binding."""

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
from marivo.analysis.domains.lifecycle import MaterializedLifecycleDataset
from marivo.analysis.domains.lifecycle_reducers import in_state
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.lifecycle_codec import evidence_payload
from marivo.analysis.materialization.targets import EngineTarget, LocalTarget
from marivo.analysis.observation.population import MaterializedPopulationDataset
from marivo.refs import ref
from marivo.semantic.state_model import ModelStateHandle
from tests.lazy_adapter_runtime_worker import forbidden, snapshot
from tests.lazy_event_runtime_worker import assert_identity_private
from tests.lazy_lifecycle_fixtures import (
    END,
    MODEL,
    START,
    history,
    lifecycle_registry,
    setup_lifecycle,
)
from tests.lazy_materialization_crash_worker import record_evidence, versions


def run(mode: str, project: Path, refs: dict[str, str], sink: str) -> dict[str, object]:
    if mode == "produce":
        runtime, sources, _ = setup_lifecycle(project, engine=True)
        metric = sources.observe(
            ref.metric("sales.revenue"),
            population=sources.population(ref.entity("sales.customers")),
        ).execute()
        produced = history(sources, population=metric).execute()
        return {
            "pid": os.getpid(),
            "refs": {"session": runtime.session_ref, "history": produced.state.artifact_ref.ref},
            "after": snapshot(runtime),
            "versions": versions(),
        }
    runtime = DatasetRuntime.open(
        project,
        refs["session"],
        target=EngineTarget("warehouse") if sink == "engine" else LocalTarget(),
    )
    before = snapshot(runtime)
    h = runtime.artifact(refs["history"])
    assert isinstance(h, MaterializedLifecycleDataset)
    database = project / "warehouse.duckdb"
    if mode == "continue":
        with duckdb.connect(str(database), config={"threads": 1}) as connection:
            connection.execute("DROP TABLE started_rows")
            connection.execute("DROP TABLE finished_rows")
    registry, sidecar = lifecycle_registry(database)
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    results: dict[str, MaterializedDataset] = {}
    result: MaterializedDataset
    with ExitStack() as stack:
        for name in (
            "marivo.analysis.compiler.lifecycle.compile_replay",
            "marivo.analysis.compiler.event_sources.lower_event_sources",
            "marivo.analysis.compiler.lowering.resolve_event_coverage",
            "marivo.analysis.compiler.lowering._Compiler._population",
        ):
            stack.enter_context(patch(name, forbidden))
        if mode == "cold":
            for name in ("place", "compile_dataset", "_build_backend_from_effective"):
                stack.enter_context(patch.object(admission, name, forbidden))
        for name, logical in (
            ("distribution", h.distribution(at=(START, END))),
            (
                "grouped",
                h.distribution(at=(START, END), axes=(ref.dimension("sales.customers.region"),)),
            ),
            ("transitions", h.transitions()),
            ("dwell", h.dwell()),
            ("violations", h.violations()),
            ("selection", h.select_subjects(in_state(ModelStateHandle(MODEL, "done"), at=END))),
        ):
            # Membership stays source-native even when terminal summaries use local storage.
            runtime.target = (
                EngineTarget("warehouse")
                if name == "selection"
                else EngineTarget("warehouse")
                if sink == "engine"
                else LocalTarget()
            )
            result = logical.execute()
            if mode == "cold":
                assert result.state.artifact_ref.ref == refs[name]
            results[name] = result
            refs = {**refs, name: result.state.artifact_ref.ref}
        selected = results["selection"]
        assert isinstance(selected, MaterializedPopulationDataset)
        result = sources.observe(ref.metric("sales.revenue"), population=selected).execute()
        if mode == "cold":
            assert result.state.artifact_ref.ref == refs["metric"]
        refs = {**refs, "metric": result.state.artifact_ref.ref}
        results["metric"] = result
    frames = {name: result.to_pandas() for name, result in results.items()}
    assert frames["selection"].entity_identity.tolist() == [(1,)]
    assert frames["metric"].entity_identity.tolist() == [(1,)]
    assert frames["distribution"].subject_count.tolist() == [1, 0, 1, 1]
    artifacts: dict[str, object] = {}
    for name, result in results.items():
        record = runtime.store.artifact(result.state.artifact_ref.ref)
        assert record is not None and record.evidence.finding_count == 0
        artifacts[name] = {
            "record": record_evidence(record),
            "lifecycle_evidence": evidence_payload(record.descriptor.lifecycle_evidence),
        }
    assert_identity_private(runtime)
    assert runtime.store.resources(runtime.session_ref) == ()
    if mode == "cold":
        assert snapshot(runtime) == before and runtime.statistics.statements == []
    return {
        "pid": os.getpid(),
        "refs": refs,
        "before": before,
        "after": snapshot(runtime),
        "artifacts": artifacts,
        "terminal_sha256": {
            name: hashlib.sha256(frame.to_json(date_format="iso").encode()).hexdigest()
            for name, frame in frames.items()
        },
        "trigger_readers_disabled": True,
        "population_enumeration_disabled": True,
        "identity_privacy_verified": True,
        "versions": versions(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "continue", "cold"))
    parser.add_argument("project", type=Path)
    parser.add_argument("sink", choices=("local", "engine"))
    parser.add_argument("--refs", default="{}")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.mode, args.project, json.loads(args.refs), args.sink),
            sort_keys=True,
            allow_nan=False,
        )
    )
