"""Three independent processes prove retained Runtime graphs need no source."""

import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from marivo.analysis.datasets.errors import DatasetFieldSelectionError
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.targets import ObjectTarget
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.observation.predicates import gt
from tests.lazy_adapter_runtime_worker import forbidden, snapshot
from tests.lazy_retained_fixtures import setup_retained
from tests.lazy_runtime_metric_fixtures import CUSTOMERS, expressions


def run(mode: str, project: Path, session: str, artifact: str) -> dict[str, object]:
    if mode == "produce":
        fixture = setup_retained(project)
        dataset = fixture.sources.observe(
            list(expressions()), population=fixture.sources.population(CUSTOMERS)
        ).execute()
        fixture.database.rename(project / "warehouse.offline")
        return {
            "pid": os.getpid(),
            "session": fixture.runtime.session_ref,
            "artifact": dataset.state.artifact_ref.ref,
            "after": snapshot(fixture.runtime),
        }
    runtime = DatasetRuntime.open(project, session)
    recovered = runtime.artifact(artifact)
    assert isinstance(recovered, MaterializedMetricDataset)
    dataset = recovered
    try:
        dataset.fields.metric(expressions()[0])
    except DatasetFieldSelectionError:
        pass
    else:
        raise AssertionError("Cold recovery invented a live expression binding")
    before = snapshot(runtime)
    logical = dataset.where(gt(dataset.fields.get("total"), 10)).aggregate()
    with ExitStack() as stack:
        for name in (
            "_build_backend_from_effective",
            "_effective_kwargs",
            "require_profile_for_backend_type",
            "compile_dataset",
        ):
            stack.enter_context(patch.object(admission, name, forbidden))
        if mode == "cold":
            runtime.target = ObjectTarget("unconfigured")
            for name in ("place", "execute_local"):
                stack.enter_context(patch.object(admission, name, forbidden))
        if mode == "cold":
            from tests.lazy_execution_fixtures import make_execution_registry

            registry, sidecar = make_execution_registry(project / "warehouse.duckdb")
            sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
            source_hit = sources.observe(
                list(expressions()), population=sources.population(CUSTOMERS)
            ).execute()
            assert source_hit.state.artifact_ref.ref == artifact
            assert runtime.statistics.primary_queries == 0
        output = logical.execute()
        projected = output.metric(output.fields.get("linear")).execute()
        delta = projected.compare(projected).execute()
    rows = output.to_pandas()
    return {
        "pid": os.getpid(),
        "artifact": output.state.artifact_ref.ref,
        "before": before,
        "after": snapshot(runtime),
        "total": rows["total"].tolist(),
        "average": rows["average"].tolist(),
        "weighted": rows["weighted"].tolist(),
        "linear": rows["linear"].tolist(),
        "projected_linear": projected.to_pandas()["linear"].tolist(),
        "projected_delta": delta.to_pandas()["delta"].tolist(),
        "queries": runtime.statistics.primary_queries,
        "source_fences": runtime.statistics.source_fences,
    }


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]), sort_keys=True))
