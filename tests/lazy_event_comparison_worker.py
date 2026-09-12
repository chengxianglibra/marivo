"""Separate producer, origin-offline continuation and cold binding acceptance."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import duckdb

from marivo.analysis.domains.contracts import EventJourneySemantics
from marivo.analysis.domains.event import MaterializedEventDataset
from marivo.analysis.funnel import funnel_loss_rate
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.targets import LocalTarget
from marivo.analysis.refs import ArtifactRef
from marivo.refs import ref
from tests.lazy_event_fixtures import make_event_registry
from tests.lazy_event_runtime_fixtures import journey, setup_event
from tests.lazy_event_runtime_worker import assert_identity_private


def run(project: Path, phase: str) -> None:
    manifest = project / "acceptance.json"
    if phase == "produce":
        runtime, sources, database = setup_event(project, engine=True)
        checkpoint = journey(sources).execute()
        with duckdb.connect(str(database), config={"threads": 1}) as connection:
            connection.execute("DROP TABLE started_rows")
            connection.execute("DROP TABLE finished_rows")
        facts = {"session": runtime.session_ref, "journey": checkpoint.state.artifact_ref.ref}
    else:
        raw: object = json.loads(manifest.read_text())
        assert isinstance(raw, dict) and all(
            isinstance(k, str) and isinstance(v, str) for k, v in raw.items()
        )
        facts = {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}
        runtime = DatasetRuntime.open(project, facts["session"], target=LocalTarget())
        registry, sidecar = make_event_registry(project / "warehouse.duckdb")
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        recovered = runtime.artifact(ArtifactRef(ref=facts["journey"]))
        assert isinstance(recovered, MaterializedEventDataset)
        checkpoint = recovered
        meaning = checkpoint.row_contract.family_semantics
        assert isinstance(meaning, EventJourneySemantics)
        delta = checkpoint.funnel().compare(checkpoint.funnel())
        attribution = delta.attribute(
            target=funnel_loss_rate(step=meaning.pattern.steps[-1]),
            axes=[ref.dimension("sales.customers.region")],
            mode="joint",
            top_k=1,
        )
        if phase == "recover":
            old = runtime.artifact(ArtifactRef(ref=facts["attribution"]))
            assert old.to_pandas().to_json() == facts["rows"]
            assert old.findings().items
        result = delta.execute()
        contributions = attribution.execute()
        if phase == "continue":
            facts.update(
                delta=result.state.artifact_ref.ref,
                attribution=contributions.state.artifact_ref.ref,
                delta_definition=delta.definition_fingerprint,
                attribution_definition=attribution.definition_fingerprint,
                rows=contributions.to_pandas().to_json(),
            )
        else:
            assert phase == "recover"
            assert facts["delta"] == result.state.artifact_ref.ref
            assert facts["attribution"] == contributions.state.artifact_ref.ref
            assert facts["delta_definition"] == delta.definition_fingerprint
            assert facts["attribution_definition"] == attribution.definition_fingerprint
            assert runtime.statistics.primary_queries == 0
            assert runtime.statistics.validation_queries == 0
            assert runtime.statistics.transferred_rows == 0
        if phase == "continue":
            assert runtime.statistics.transferred_rows == len(contributions.to_pandas())
    assert_identity_private(runtime)
    manifest.write_text(json.dumps(facts, sort_keys=True))
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    paths = sorted(
        {
            *root.joinpath("marivo").rglob("*.py"),
            *root.joinpath("tests").rglob("*.py"),
            *(root / name for name in ("pyproject.toml", "Makefile", ".importlinter")),
        }
    )
    for path in paths:
        digest.update(
            path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0"
        )
    print(
        json.dumps(
            {
                "phase": phase,
                "pid": os.getpid(),
                "candidate": digest.hexdigest(),
                "session": runtime.session_ref,
                "passed": True,
                "primary_queries": runtime.statistics.primary_queries,
                "validation_queries": runtime.statistics.validation_queries,
                "transferred_rows": runtime.statistics.transferred_rows,
            }
        )
    )


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2])
