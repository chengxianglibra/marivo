"""Independent Entity Candidate production, retained membership and cold binding."""

from __future__ import annotations

import argparse
import json
import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import duckdb

from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.candidate_codec import evidence_payload
from marivo.analysis.materialization.targets import LocalTarget
from marivo.analysis.observation.predicates import gte
from marivo.analysis.operators.candidate_dataset import MaterializedCandidateDataset
from marivo.refs import ref
from tests.lazy_entity_candidate_fixtures import entity_metric, setup_entity_candidate
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_materialization_crash_worker import record_evidence, snapshot, statistics, versions


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("Entity Candidate attempted an unauthorized execution or identity read")


def run(mode: str, project: Path, refs: dict[str, str]) -> dict[str, object]:
    database = project / "warehouse.duckdb"
    if mode == "produce":
        runtime, sources, database = setup_entity_candidate(project)
        runtime.target = LocalTarget()
        logical = entity_metric(sources).discover.entity_outliers()
        selected = logical.where(gte(logical.fields.get("score"), 4.0))
        with (
            patch.object(admission, "execute_local", forbidden),
            patch("marivo.analysis.materialization.reads.payload_batches", forbidden),
        ):
            direct = sources.observe(ref.metric("sales.line_revenue"), population=selected)
            direct_result = direct.aggregate().execute()
            result = logical.execute()
        assert direct_result.to_pandas()["line_revenue"].tolist() == [40.0]
        refs = {"session": runtime.session_ref, "candidate": result.state.artifact_ref.ref}
        with duckdb.connect(str(database), config={"threads": 1}) as connection:
            connection.execute("DROP TABLE orders")
        before = snapshot(runtime)
    else:
        runtime = DatasetRuntime.open(project, refs["session"], target=LocalTarget())
        before = snapshot(runtime)
        recovered = runtime.artifact(refs["candidate"])
        assert isinstance(recovered, MaterializedCandidateDataset)
        result = recovered
        registry, sidecar = make_execution_registry(database)
        sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)

    selected_evidence: object = None
    total: float | None = None
    if mode != "produce":
        selected = result.where(gte(result.fields.get("score"), 4.0))
        ranked = selected.rank(selected.fields.get("score")).limit(1)
        with ExitStack() as guards:
            guards.enter_context(patch.object(admission, "execute_local", forbidden))
            guards.enter_context(
                patch("marivo.analysis.materialization.reads.payload_batches", forbidden)
            )
            if mode == "cold":
                for name in ("place", "compile_dataset", "_build_backend_from_effective"):
                    guards.enter_context(patch.object(admission, name, forbidden))
            retained = ranked.execute()
            observed = sources.observe(
                ref.metric("sales.line_revenue"), population=retained
            ).aggregate()
            observed_result = observed.execute()
        total = float(observed_result.to_pandas()["line_revenue"].iloc[0])
        assert total == 40.0
        refs = {
            **refs,
            "selected": retained.state.artifact_ref.ref,
            "observed": observed_result.state.artifact_ref.ref,
        }
        selected_record = runtime.store.artifact(refs["selected"])
        assert selected_record is not None
        selected_evidence = evidence_payload(selected_record.descriptor.candidate_evidence)
        assert retained.findings().items == ()
        if mode == "cold":
            assert snapshot(runtime) == before

    frame = result.to_pandas()
    assert frame.entity_identity.tolist() == [(4,)]
    assert frame.score.tolist() == [4.0]
    assert frame.scale_method.tolist() == ["mean_absolute_deviation"]
    assert frame.reason_codes.tolist() == [("entity_mad_threshold_met",)]
    record = runtime.store.artifact(refs["candidate"])
    assert record is not None and result.findings().items == ()
    assert runtime.statistics.events.get("local_execution_started", 0) == 0
    assert runtime.statistics.transferred_rows == (0 if mode == "cold" else 1)
    if mode != "produce":
        assert all('"orders"' not in sql for _, sql in runtime.statistics.statements)
    with duckdb.connect(str(database), read_only=True, config={"threads": 1}) as connection:
        assert connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'orders'"
        ).fetchone() == (0,)
    return {
        "pid": os.getpid(),
        "refs": refs,
        "identity_verified": True,
        "score": frame.score.tolist(),
        "total": total,
        "artifact": record_evidence(record),
        "search_evidence": evidence_payload(record.descriptor.candidate_evidence),
        "selected_evidence": selected_evidence,
        "before": before,
        "after": snapshot(runtime),
        "statistics": statistics(runtime),
        "versions": versions(),
        "origin_removed": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "continue", "cold"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--refs", default="{}")
    args = parser.parse_args()
    print(json.dumps(run(args.mode, args.project, json.loads(args.refs)), sort_keys=True))
