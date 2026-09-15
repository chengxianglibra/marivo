"""Independent driver production, source-offline continuation and exact cold reuse."""

from __future__ import annotations

import argparse
import json
import os
from contextlib import ExitStack
from pathlib import Path
from typing import Literal
from unittest.mock import patch

from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.candidate_codec import evidence_payload
from marivo.analysis.materialization.targets import LocalTarget
from marivo.analysis.observation.predicates import eq
from marivo.analysis.operators.candidate_dataset import MaterializedCandidateDataset
from marivo.analysis.operators.delta import MaterializedDeltaDataset
from tests.lazy_distinct_runtime_worker import frame_rows
from tests.lazy_driver_runtime_fixtures import CHANNEL, driver_metric, setup_driver
from tests.lazy_materialization_crash_worker import record_evidence, snapshot, statistics, versions


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("Cold driver binding attempted new execution")


def run(
    mode: str, kind: Literal["local", "engine"], project: Path, refs: dict[str, str]
) -> dict[str, object]:
    if mode == "produce":
        fixture = setup_driver(project, kind)
        runtime = fixture.runtime
        delta = (
            driver_metric(fixture.sources, temporal=True, region=True)
            .compare(driver_metric(fixture.sources, baseline=True, temporal=True, region=True))
            .execute()
        )
        runtime.target = LocalTarget()
        result = delta.discover.driver_axes(search_space=[CHANNEL]).execute()
        fixture.database.rename(project / "origin.offline")
        refs = {
            "session": runtime.session_ref,
            "input": delta.state.artifact_ref.ref,
            "candidate": result.state.artifact_ref.ref,
        }
    else:
        runtime = DatasetRuntime.open(project, refs["session"], target=LocalTarget())
        loaded = runtime.artifact(refs["candidate"])
        assert isinstance(loaded, MaterializedCandidateDataset)
        result = loaded
    before = snapshot(runtime)
    selected_rows: list[list[object]] = []
    selected_evidence: object = None
    if mode != "produce":
        recovered_delta = runtime.artifact(refs["input"])
        assert isinstance(recovered_delta, MaterializedDeltaDataset)
        delta = recovered_delta
        with ExitStack() as guards:
            if mode == "cold":
                for name in (
                    "place",
                    "compile_dataset",
                    "_build_backend_from_effective",
                    "execute_local",
                ):
                    guards.enter_context(patch.object(admission, name, forbidden))
            assert (
                delta.discover.driver_axes(search_space=[CHANNEL]).execute().state.artifact_ref
                == result.state.artifact_ref
            )
            selected = result.where(eq(result.fields.get("comparison_ordinal"), 1))
            ranked = selected.rank(selected.fields.get("score"))
            retained = ranked.limit(1).execute()
        refs = {**refs, "selected": retained.state.artifact_ref.ref}
        selected_rows = frame_rows(retained.to_pandas())
        changed = runtime.store.artifact(refs["selected"])
        assert changed is not None
        selected_evidence = evidence_payload(changed.descriptor.candidate_evidence)
    record = runtime.store.artifact(refs["candidate"])
    assert record is not None
    frame = result.to_pandas()
    assert len(frame) == 4 and all(value == ("axis_concentration",) for value in frame.reason_codes)
    assert result.findings().items == () and result.evidence_digest.finding_count == 0
    if mode == "cold":
        assert snapshot(runtime) == before
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
    parser.add_argument("kind", choices=("local", "engine"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--refs", default="{}")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.mode, args.kind, args.project, json.loads(args.refs)),
            sort_keys=True,
            allow_nan=False,
        )
    )
