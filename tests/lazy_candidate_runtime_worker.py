"""Independent Candidate production, source-offline continuation and cold reuse."""

from __future__ import annotations

import argparse
import json
import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.candidate_codec import evidence_payload
from marivo.analysis.materialization.targets import LocalTarget
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.observation.predicates import gt
from marivo.analysis.operators.candidate_contracts import CandidateObjective
from marivo.analysis.operators.candidate_dataset import MaterializedCandidateDataset
from marivo.analysis.operators.delta import MaterializedDeltaDataset
from tests.lazy_candidate_fixtures import candidate_input, discover, setup_candidate
from tests.lazy_distinct_runtime_worker import frame_rows
from tests.lazy_materialization_crash_worker import record_evidence, snapshot, statistics, versions


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("Cold Candidate binding attempted a new execution")


def run(
    mode: str, objective: CandidateObjective, kind: str, project: Path, refs: dict[str, str]
) -> dict[str, object]:
    if mode == "produce":
        runtime, source, database = setup_candidate(project)
        runtime.target = LocalTarget()
        source_rows = candidate_input(source, objective, panel=True).execute()
        runtime.target = LocalTarget()
        result = discover(source_rows, objective).execute()
        database.rename(project / "origin.offline")
        refs = {
            "session": runtime.session_ref,
            "input": source_rows.state.artifact_ref.ref,
            "candidate": result.state.artifact_ref.ref,
        }
        before = snapshot(runtime)
    else:
        runtime = DatasetRuntime.open(project, refs["session"], target=LocalTarget())
        before = snapshot(runtime)
        recovered = runtime.artifact(refs["candidate"])
        assert isinstance(recovered, MaterializedCandidateDataset)
        result = recovered
    selected_rows: object = None
    selected_evidence: object = None
    if mode != "produce":
        recovered_source = runtime.artifact(refs["input"])
        assert isinstance(recovered_source, (MaterializedMetricDataset, MaterializedDeltaDataset))
        source_rows = recovered_source
        with ExitStack() as guards:
            if mode == "cold":
                for name in (
                    "place",
                    "compile_dataset",
                    "_build_backend_from_effective",
                    "execute_local",
                ):
                    guards.enter_context(patch.object(admission, name, forbidden))
            rebound = discover(source_rows, objective).execute()
            assert rebound.state.artifact_ref == result.state.artifact_ref
            selected = result.where(gt(result.fields.get("score"), 0))
            ranked = selected.rank(selected.fields.get("score"))
            retained = ranked.limit(2).execute()
        refs = {**refs, "selected": retained.state.artifact_ref.ref}
        frame = retained.to_pandas()
        assert all(type(value) is tuple for value in frame.reason_codes)
        selected_rows = frame_rows(frame)
        selected_record = runtime.store.artifact(refs["selected"])
        assert selected_record is not None
        selected_evidence = evidence_payload(selected_record.descriptor.candidate_evidence)
        selected_contract = retained.contract().render()
        assert refs["input"] in selected_contract and "threshold: 1.0" in selected_contract
        assert retained.findings().items == ()
        if mode == "cold":
            assert snapshot(runtime) == before
    record = runtime.store.artifact(refs["candidate"])
    assert record is not None
    frame = result.to_pandas()
    assert all(type(value) is tuple for value in frame.reason_codes)
    assert not frame.empty
    findings = result.findings()
    assert findings.items == ()
    contract = result.contract().render()
    assert refs["input"] in contract and "threshold: 1.0" in contract
    return {
        "pid": os.getpid(),
        "refs": refs,
        "rows": frame_rows(frame),
        "selected_rows": selected_rows,
        "artifact": record_evidence(record),
        "search_evidence": evidence_payload(record.descriptor.candidate_evidence),
        "selected_evidence": selected_evidence,
        "findings": [],
        "before": before,
        "after": snapshot(runtime),
        "statistics": statistics(runtime),
        "versions": versions(),
        "origin_removed": not (project / "warehouse.duckdb").exists(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "continue", "cold"))
    parser.add_argument(
        "objective", choices=("point_anomalies", "interesting_windows", "period_shifts")
    )
    parser.add_argument("kind", choices=("local", "engine"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--refs", default="{}")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.mode, args.objective, args.kind, args.project, json.loads(args.refs)),
            sort_keys=True,
            allow_nan=False,
        )
    )
